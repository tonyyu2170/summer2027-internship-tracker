"""Parent-session entrypoint: the single serialized writer for a scrape run.

Loads every fetch-report JSON in a directory (see docs/SCRAPING.md), groups
them by category, merges each category exactly once, rewrites that category's
YAML, then regenerates README.md. Prints a per-category summary. Never runs
git — committing (and pushing, which is Tony's alone) happens outside.

Fetch-report JSON comes from fragile, source-specific scraping and is not
trusted to be well-formed, so two validation gates sit around merge_category:
before it, postings missing a required field are dropped; after it, rows that
fail the row schema are dropped before they ever reach data/*.yaml."""
import json
import sys
import yaml
from pathlib import Path
from datetime import date
from collections import defaultdict

from merge import merge_category
from schema import validate_row
from generate_readme import render, ROOT

REQUIRED_POSTING_FIELDS = ["company", "role", "location", "link", "term", "degree"]


def _filter_postings(report: dict) -> dict:
    """Return a copy of report with postings missing a required field
    dropped. Prints a warning per skipped posting; never raises."""
    entity = report.get("source_entity", "unknown")
    kept = []
    for p in report.get("postings", []):
        if not isinstance(p, dict):
            print(f"    warn: [{entity}] skipped non-object posting: {p!r}")
            continue
        missing = [f for f in REQUIRED_POSTING_FIELDS if not p.get(f)]
        if missing:
            label = p.get("company") or p.get("link") or "<unidentified posting>"
            print(f"    warn: [{entity}] skipped {label!r}: "
                  f"missing required field(s) {missing}")
            continue
        kept.append(p)
    return {**report, "postings": kept}


def _drop_invalid_rows(rows: list, summary: dict) -> list:
    """Run validate_row over rows; drop failures (with a warning), and scrub
    dropped ids out of summary['new'] / summary['possible_duplicates']."""
    kept, dropped = [], set()
    for row in rows:
        errors = validate_row(row)
        if errors:
            dropped.add(row["id"])
            print(f"    warn: dropped invalid row {row['id']!r}: {errors}")
        else:
            kept.append(row)

    if dropped:
        summary["new"] = [i for i in summary["new"] if i not in dropped]
        summary["possible_duplicates"] = [
            (new_id, dup_of) for new_id, dup_of in summary["possible_duplicates"]
            if new_id not in dropped and dup_of not in dropped
        ]
    return kept


def run(reports_dir, data_dir=None, readme_path=None):
    reports_dir = Path(reports_dir)
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    today = date.today().isoformat()

    by_cat = defaultdict(list)
    for p in sorted(reports_dir.glob("*.json")):
        report = json.loads(p.read_text())
        by_cat[report["category"]].append(_filter_postings(report))

    summaries = {}
    for cat, reports in by_cat.items():
        path = data_dir / f"{cat}.yaml"
        existing = (yaml.safe_load(path.read_text()) or []) if path.exists() else []
        rows, summary = merge_category(existing, reports, today)
        rows = _drop_invalid_rows(rows, summary)
        path.write_text(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
        summaries[cat] = summary
        print(f"[{cat}] +{len(summary['new'])} new, "
              f"{len(summary['closed'])} newly closed, "
              f"{len(summary['possible_duplicates'])} possible dup(s)")
        for new_id, dup_of in summary["possible_duplicates"]:
            print(f"    warn: {new_id} may duplicate {dup_of}")

    render(data_dir, readme_path)
    return summaries


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "scratch/fetch_reports")
