# scripts/fetch_trackers.py
"""Network driver for parse_tracker.py. Untested, like the rest of scraping
(see docs/SCRAPING.md) — the parsers and category rules it calls are tested
in tests/test_parse_tracker.py and tests/test_categorize.py.

Three cost tiers: skip a tracker whose commit SHA is unchanged; parse the
rest deterministically; hand only rows no rule could categorize to the
session via scratch/fetch_reports/unclassified.json. Writes fetch reports
only — never data/*.yaml. Run scripts/run_scrape_merge.py afterward."""
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
import certifi
import yaml
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from categorize import (
    assign_category,
    known_link_categories,
    known_link_locations,
    manual_link_categories,
    DROP,
)
from normalize import normalize_link
from parse_tracker import (
    parse_cvrve_json,
    parse_zshah_json,
    parse_nufintech_yaml,
    parse_pipe_table,
)

ROOT = Path(__file__).resolve().parent.parent
# An egress proxy that re-terminates TLS (the scheduled cloud runs go through
# one) signs with a CA that certifi doesn't carry; it exports SSL_CERT_FILE.
# Honor that when it's set, else fall back to certifi as before.
_SSL_CTX = ssl.create_default_context(
    cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
_HEADERS = {"User-Agent": "internship-tracker-scraper", "Accept": "*/*"}


def _get(url, as_json=False):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if as_json else body


def _latest_sha(repo, path, branch):
    url = (f"https://api.github.com/repos/{repo}/commits"
           f"?path={path}&sha={branch}&per_page=1")
    data = _get(url, as_json=True)
    return data[0]["sha"] if data else None


def _raw(repo, branch, path):
    return _get(f"https://raw.githubusercontent.com/{repo}/{branch}/{path}")


def _parse(cfg):
    """Fetch and parse one tracker. Returns a list of postings."""
    fmt = cfg["fmt"]
    if fmt == "cvrve_json":
        return parse_cvrve_json(
            _raw(cfg["repo"], cfg["branch"], cfg["path"]),
            term_field=cfg["term_field"],
            term_value=cfg["term_value"],
            term_out=cfg.get("term_out"),
        )
    if fmt == "zshah_json":
        return parse_zshah_json(
            _raw(cfg["repo"], cfg["branch"], cfg["path"]),
            season=cfg["term_value"],
        )
    if fmt == "pipe_table":
        return parse_pipe_table(
            _raw(cfg["repo"], cfg["branch"], cfg["path"]), reference_date=date.today())
    if fmt == "nufintech_yaml":
        # One recursive listing, not 59 separate content fetches.
        tree = _get(
            f"https://api.github.com/repos/{cfg['repo']}/git/trees/"
            f"{cfg['branch']}?recursive=1", as_json=True
        )
        postings = []
        for node in tree.get("tree", []):
            p = node["path"]
            if p.startswith(f"{cfg['path']}/") and p.endswith((".yaml", ".yml")):
                postings.extend(
                    parse_nufintech_yaml(_raw(cfg["repo"], cfg["branch"], p))
                )
        return postings
    raise ValueError(f"unknown fmt {fmt!r}")


def run(out_dir=None):
    out_dir = Path(out_dir) if out_dir else ROOT / "scratch" / "fetch_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    trackers = yaml.safe_load((ROOT / "sources" / "github_trackers.yaml").read_text())
    state_path = ROOT / "sources" / "scrape_state.yaml"
    state = yaml.safe_load(state_path.read_text()) or {}
    # Data rows win over manual judgments: a tracked link's category is
    # stable, and manual_categories.yaml only speaks for links no rule or
    # data row covers.
    known = {**manual_link_categories(), **known_link_categories()}
    known_locations = known_link_locations()
    unclassified = []
    drop_counts_path = out_dir / "drop_counts.json"
    prior_drops = json.loads(drop_counts_path.read_text()) if drop_counts_path.exists() else {}
    drop_counts = defaultdict(
        Counter, {source: Counter(counts) for source, counts in prior_drops.items()})

    for cfg in trackers:
        handle = cfg["handle"]
        prior = state.get(handle) or {}
        try:
            sha = _latest_sha(cfg["repo"], cfg["path"], cfg["branch"])
        except Exception as e:
            print(f"[{handle}] warn: SHA check failed ({e}); parsing anyway")
            sha = None

        if sha and sha == prior.get("sha"):
            print(f"[{handle}] unchanged, skipped")
            continue

        try:
            postings = _parse(cfg)
        except Exception as e:
            print(f"[{handle}] warn: parse failed ({e}). Falling back to the "
                  f"LLM subagent README parse for this tracker — see "
                  f"docs/SCRAPING.md. SHA not advanced.")
            continue

        for p in postings:
            if not p.get("location") and p.get("link"):
                loc = known_locations.get(normalize_link(p["link"]))
                if loc:
                    p["location"] = loc

        baseline = prior.get("row_count") or 0
        if not postings or (baseline and len(postings) < baseline / 2):
            print(f"[{handle}] warn: yielded {len(postings)} postings vs "
                  f"baseline {baseline} — treating as an upstream format "
                  f"change. No report written, SHA not advanced.")
            continue

        by_cat = defaultdict(list)
        for p in postings:
            link = p.get("link")
            if p.get("closed_marker") and link and normalize_link(link) not in known:
                # Already dead and never tracked before: nothing to close,
                # and adding it now would only import a corpse. Sources like
                # simplifyjobs carry years of inactive listings alongside
                # active ones — most closed_marker rows are exactly this.
                drop_counts[f"github_tracker:{handle}"]["closed_marker_untracked"] += 1
                continue
            # An already-tracked link keeps its existing category, always —
            # even when the parser itself sets one (northwesternfintech's
            # role-type map always does). Checking the known-link map first
            # is what makes category assignment stable across sources; a
            # parser's own opinion only matters for a link seen for the
            # first time.
            explicit_category = p.pop("category", None)
            existing_category = known.get(normalize_link(link)) if link else None
            category = existing_category or explicit_category or assign_category(p, known)
            if category == DROP:
                drop_counts[f"github_tracker:{handle}"]["category_drop"] += 1
                continue
            if not category:
                unclassified.append({**p, "handle": handle, "category": None})
                drop_counts[f"github_tracker:{handle}"]["unclassified"] += 1
                continue
            p["source"] = f"github_tracker:{handle}"
            by_cat[category].append(p)

        for category, rows in by_cat.items():
            report = {
                "category": category,
                "source_entity": f"github_tracker:{handle}",
                "postings": rows,
            }
            (out_dir / f"{handle}_{category}.json").write_text(
                json.dumps(report, indent=1)
            )

        state[handle] = {
            "path": cfg["path"],
            "sha": sha,
            "scraped_at": date.today().isoformat(),
            "row_count": len(postings),
        }
        print(f"[{handle}] {len(postings)} postings across "
              f"{len(by_cat)} categor(ies)")

    (out_dir / "unclassified.json").write_text(json.dumps(unclassified, indent=1))
    drop_counts_path.write_text(json.dumps(
        {source: dict(counts) for source, counts in drop_counts.items()}, indent=1))
    state["_last_fetch"] = {"drops": {
        source: dict(counts) for source, counts in sorted(drop_counts.items())}}
    state_path.write_text(yaml.safe_dump(state, sort_keys=True))
    if unclassified:
        print(f"\n{len(unclassified)} posting(s) need a category. Fill in "
              f"'category' for each in {out_dir / 'unclassified.json'}, then "
              f"run scripts/run_scrape_merge.py.")
    return unclassified


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
