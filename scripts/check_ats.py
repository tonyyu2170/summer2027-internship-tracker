"""Network driver for ats_verify.py's pure verification logic. Untested,
like check_links.py (see docs/SCRAPING.md).

Probes every open row whose link sits on an API-covered ATS and writes ONE
corrections JSON to scratch/ats_corrections.json — the audit record of
every proposed change. Never writes data/*.yaml or README.md; run
scripts/apply_ats_corrections.py on the file afterward to apply. Never run
concurrently with run_scrape_merge.py (single-writer discipline).

Rows of the same Ashby org each re-fetch the same board URL; at the
current row counts that redundancy is cheaper than caching across threads.

Usage: python3 scripts/check_ats.py [category ...]   # default: all"""
import json
import sys
import yaml
from pathlib import Path
from datetime import date
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from check_links import _probe
from ats_verify import api_url, extract, decide, icims_redirected_away

ROOT = Path(__file__).resolve().parent.parent


def _verify_row(category, row, today):
    ats, url = api_url(row["link"])
    status, final, body = _probe(url, want_body=True)
    if ats == "icims" and icims_redirected_away(row["link"], final):
        # The page we landed on is not this posting; its JSON-LD would
        # describe someone else's job. Unknown, not a correction.
        ext = None
    else:
        ext = extract(ats, status, body, link=row["link"], today=today)
    return [
        {"id": row["id"], "category": category, "ats": ats, **action}
        for action in decide(row, ext)
    ]


def run(data_dir=None, out_path=None, workers=10, categories=None):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    out_path = (Path(out_path) if out_path
                else ROOT / "scratch" / "ats_corrections.json")
    today = date.today()

    targets = []
    for path in sorted(data_dir.glob("*.yaml")):
        if categories and path.stem not in categories:
            continue
        for row in yaml.safe_load(path.read_text()) or []:
            if row.get("status") == "open" and api_url(row.get("link") or ""):
                targets.append((path.stem, row))

    actions = []
    counts = Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_verify_row, cat, row, today): (cat, row)
                   for cat, row in targets}
        for fut in as_completed(futures):
            cat, row = futures[fut]
            try:
                row_actions = fut.result()
            except Exception as e:      # one bad row must not kill the run
                print(f"    warn: [{row.get('id')}] probe failed: {e}")
                row_actions = [{"id": row["id"], "category": cat,
                                "ats": api_url(row["link"])[0],
                                "action": "unknown"}]
            actions.extend(row_actions)
            for a in row_actions:
                counts[(a["ats"], a["action"])] += 1

    actions.sort(key=lambda a: (a["category"], a["id"], a["action"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated": today.isoformat(), "actions": actions}, indent=2))

    for ats in sorted({ats for ats, _ in counts}):
        parts = ", ".join(f"{act}={n}"
                          for (a, act), n in sorted(counts.items()) if a == ats)
        print(f"[{ats}] {parts}")
    for a in actions:
        if a["action"] == "delete_non_us":
            print(f"    would DELETE (non-US): [{a['id']}] "
                  f"api_locations={a.get('api_locations')} "
                  f"country={a.get('country')}")
        elif a["action"] == "close":
            print(f"    would close: [{a['id']}]")
    print(f"{len(targets)} row(s) probed -> {out_path}")


if __name__ == "__main__":
    run(categories=set(sys.argv[1:]) or None)
