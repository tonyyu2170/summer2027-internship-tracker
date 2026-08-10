"""Network driver for repost_verify.py's pure logic. Untested, like
check_ats.py (see docs/SCRAPING.md).

Fetches each company's live posting list ONCE (not once per row) and asks
repost_verify which tracked rows point at a superseded requisition. Writes
one corrections JSON to scratch/repost_corrections.json — the audit record —
and never touches data/*.yaml or README.md; run
scripts/apply_repost_corrections.py on the file afterward. Never run
concurrently with run_scrape_merge.py (single-writer discipline).

Only SmartRecruiters, Greenhouse and Lever are covered; see repost_verify's
docstring for why Workday is excluded.

Usage: python3 scripts/check_reposts.py [category ...]   # default: all"""
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import yaml

from check_links import _probe
from repost_verify import find_reposts, listing_url, parse_listing

ROOT = Path(__file__).resolve().parent.parent


def _check_board(ats, url, rows):
    status, _final, body = _probe(url, want_body=True)
    if status != 200 or not body:
        raise RuntimeError(f"listing fetch returned {status}")
    entries = parse_listing(ats, body)
    if not entries:
        # An empty listing would mark every row on the board a repost
        # candidate; treat it as a failed fetch instead.
        raise RuntimeError("listing was empty")
    return find_reposts([r for _cat, r in rows], entries)


def run(data_dir=None, out_path=None, workers=8, categories=None):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    out_path = (Path(out_path) if out_path
                else ROOT / "scratch" / "repost_corrections.json")
    today = date.today()

    # Group open rows by board so each listing is fetched once.
    boards = defaultdict(list)
    category_of = {}
    for path in sorted(data_dir.glob("*.yaml")):
        if categories and path.stem not in categories:
            continue
        for row in yaml.safe_load(path.read_text()) or []:
            if row.get("status") != "open":
                continue
            target = listing_url(row.get("link") or "")
            if target:
                boards[target].append((path.stem, row))
                category_of[row["id"]] = path.stem

    actions, counts = [], Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_check_board, ats, url, rows): (ats, url)
                   for (ats, url), rows in boards.items()}
        for fut in as_completed(futures):
            ats, url = futures[fut]
            try:
                board_actions = fut.result()
            except Exception as e:   # one bad board must not kill the run
                print(f"    warn: [{ats}] {url}: {e}")
                counts[(ats, "fetch_failed")] += 1
                continue
            for a in board_actions:
                rid = a.get("id") or a["ids"][0]
                actions.append({"category": category_of[rid], "ats": ats, **a})
                counts[(ats, a["action"])] += 1

    actions.sort(key=lambda a: (a["category"], a.get("id") or a["ids"][0]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated": today.isoformat(), "actions": actions}, indent=2))

    for ats in sorted({a for a, _ in counts}):
        parts = ", ".join(f"{act}={n}"
                          for (a, act), n in sorted(counts.items()) if a == ats)
        print(f"[{ats}] {parts}")
    for a in actions:
        if a["action"] == "repost":
            print(f"    would REPOST: [{a['id']}] {a['new_date']} {a['new_link']}")
        else:
            print(f"    ambiguous: {a['ids']} {a['title']!r} -> {a['candidates']}")
    print(f"{len(boards)} board(s) checked, "
          f"{sum(len(r) for r in boards.values())} open row(s) -> {out_path}")


if __name__ == "__main__":
    run(categories=set(sys.argv[1:]) or None)
