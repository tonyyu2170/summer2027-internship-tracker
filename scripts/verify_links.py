"""Post-scrape link verification driver. Network code — untested by design
(the judgment rules live in link_verify.py, which is unit-tested).

Default: probe open rows added today (the ones a scrape just imported with
unverified tracker labels). --all: probe every open row. Applies only
unambiguous outcomes, then re-checks integrity and re-renders README:

- wrong_term  -> delete row + suppress its link form(s) in
                 sources/manual_categories.yaml (deletion alone re-imports)
- dead (404)  -> delete row (no suppression — a revived link may re-import)
- ok+new_role -> restore authoritative title (ByteDance/TikTok family only)
- ambiguous / errors -> no action

Safety: aborts with exit 2 and NO changes if wrong-term flags exceed
link_verify.over_cap (a flood means a format break, not real data). Never
touches git — the caller (auto_scrape.sh or the session) commits.
"""
import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import certifi
import yaml

from link_verify import evaluate, probe_url, suppression_links, over_cap
from check_integrity import check_integrity
from generate_readme import render, ROOT, CATEGORIES

_SSL = ssl.create_default_context(cafile=certifi.where())
_HDR = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        "Accept-Language": "en-US"}


def _probe(job):
    cat, row = job
    url = probe_url(row["link"])
    if url is None:
        return cat, row, {"verdict": "ambiguous", "evidence": "unprobeable link"}
    try:
        req = urllib.request.Request(url, headers=_HDR)
        with urllib.request.urlopen(req, timeout=12, context=_SSL) as resp:
            body = resp.read(500000).decode("utf-8", "replace")
            status = resp.status
    except urllib.error.HTTPError as e:
        body, status = "", e.code
    except Exception as e:
        return cat, row, {"verdict": "error", "evidence": str(e)[:60]}
    return cat, row, evaluate(row["link"], status, body, row.get("role") or "")


def run(scope="new"):
    today = date.today().isoformat()
    data = {}
    for stem, _t, _q in CATEGORIES:
        p = ROOT / "data" / f"{stem}.yaml"
        data[stem] = (yaml.safe_load(p.read_text()) or []) if p.exists() else []

    jobs = [(cat, r) for cat, rows in data.items() for r in rows
            if r.get("status") == "open" and r.get("link")
            and (scope == "all" or r.get("date_added") == today)]
    if not jobs:
        print("verify_links: nothing to probe")
        return 0

    print(f"verify_links: probing {len(jobs)} row(s) (scope={scope})")
    with ThreadPoolExecutor(12) as ex:
        results = list(ex.map(_probe, jobs))

    audit = [{"category": c, "id": r.get("id"), "link": r["link"], **v}
             for c, r, v in results]
    (ROOT / "scratch").mkdir(exist_ok=True)
    (ROOT / "scratch" / "verify_links_audit.json").write_text(
        json.dumps(audit, indent=1))

    wrong = [(c, r, v) for c, r, v in results if v["verdict"] == "wrong_term"]
    dead = [(c, r, v) for c, r, v in results if v["verdict"] == "dead"]
    retitle = [(c, r, v) for c, r, v in results
               if v["verdict"] == "ok" and v.get("new_role")]
    if over_cap(len(wrong), len(results)):
        print(f"ABORT: {len(wrong)} wrong-term flags out of {len(results)} "
              f"probed exceeds the safety cap — review "
              f"scratch/verify_links_audit.json by hand. No changes made.")
        return 2

    for c, r, v in wrong:
        print(f"  delete [{c}] {r.get('id')}: {v['evidence'][:70]}")
    for c, r, v in dead:
        print(f"  delete-dead [{c}] {r.get('id')} ({v['evidence']})")
    if not (wrong or dead or retitle):
        print(f"verify_links: all {len(results)} clean "
              f"(errors/ambiguous: {sum(1 for _, _, v in results if v['verdict'] in ('error', 'ambiguous'))})")
        return 0

    drop_ids = {r.get("id") for _, r, _ in wrong} | {r.get("id") for _, r, _ in dead}
    for _, r, v in retitle:
        r["role"] = v["new_role"]
    merged = {cat: [r for r in rows if r.get("id") not in drop_ids]
              for cat, rows in data.items()}

    violations = check_integrity(merged)
    if violations:
        for x in violations:
            print(f"INTEGRITY: {x}")
        print("verify_links: integrity violations — nothing written.")
        return 2

    for cat, rows in merged.items():
        (ROOT / "data" / f"{cat}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    if wrong:
        with open(ROOT / "sources" / "manual_categories.yaml", "a") as f:
            f.write("# auto verify_links: live page says a non-2027 term.\n")
            f.write(yaml.safe_dump(
                {l: "__drop__" for _, r, _ in wrong
                 for l in suppression_links(r["link"])}, sort_keys=True))

    state = yaml.safe_load((ROOT / "sources" / "scrape_state.yaml").read_text()) or {}
    render(ROOT / "data", ROOT / "README.md", state.get("_last_run"))
    print(f"verify_links: deleted {len(wrong)} wrong-term (suppressed), "
          f"{len(dead)} dead; retitled {len(retitle)}")
    return 0


if __name__ == "__main__":
    sys.exit(run("all" if "--all" in sys.argv else "new"))
