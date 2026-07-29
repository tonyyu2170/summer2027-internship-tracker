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
from datetime import date, datetime
from collections import defaultdict

from merge import merge_category
from schema import validate_row
from check_integrity import check_integrity
from generate_readme import render, ROOT, CATEGORIES

REQUIRED_POSTING_FIELDS = ["company", "role", "location", "link", "term", "degree"]


def _filter_postings(report: dict) -> dict:
    """Return a copy of report with postings missing a required field
    dropped. Prints a warning per skipped posting; never raises.

    Note: even a posting with every required field present can still
    produce a row that fails schema validation downstream (see
    _drop_invalid_rows) — this gate only catches missing/falsy fields,
    not malformed values."""
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
    """Validate only rows created new this run (summary['new'] — the ones
    built from untrusted incoming posting data). Rows loaded from existing
    data/*.yaml are left untouched even if malformed: they may be Tony's own
    hand edits, and silently deleting previously-tracked listings over a
    schema slip is worse than tolerating one until it's fixed by hand.

    Drops failing new rows (with a warning), scrubs their ids out of
    summary['new'] / summary['closed'] / summary['possible_duplicates'], and
    clears possible_duplicate_of on any surviving row that pointed at a
    dropped id, so nothing persisted ever references a row that wasn't.

    Existing rows that fail validation are kept as-is but get a warning
    printed, so a hand-edit typo is visible in run output instead of
    silently rendering wrong (e.g. via generate_readme.py)."""
    new_ids = set(summary["new"])
    kept, dropped = [], set()
    for row in rows:
        rid = row.get("id")
        errors = validate_row(row)
        if rid in new_ids:
            if errors:
                dropped.add(rid)
                print(f"    warn: dropped invalid row {rid!r}: {errors}")
                continue
        elif errors:
            print(f"    warn: existing row {rid!r} fails schema (kept as-is): {errors}")
        kept.append(row)

    if dropped:
        summary["new"] = [i for i in summary["new"] if i not in dropped]
        summary["closed"] = [i for i in summary["closed"] if i not in dropped]
        summary["possible_duplicates"] = [
            (new_id, dup_of) for new_id, dup_of in summary["possible_duplicates"]
            if new_id not in dropped and dup_of not in dropped
        ]
        for row in kept:
            if row.get("possible_duplicate_of") in dropped:
                row["possible_duplicate_of"] = None
    return kept


def _load_rows(path: Path) -> list:
    return (yaml.safe_load(path.read_text()) or []) if path.exists() else []


def run(reports_dir, data_dir=None, readme_path=None, state_path=None):
    reports_dir = Path(reports_dir)
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    state_path = (Path(state_path) if state_path else
                  data_dir.parent / "sources" / "scrape_state.yaml")
    today = date.today().isoformat()

    unclassified_path = reports_dir / "unclassified.json"
    if unclassified_path.exists():
        pending = json.loads(unclassified_path.read_text())
        pending = [p for p in pending if not p.get("category")]
        if pending:
            raise SystemExit(
                f"{len(pending)} unclassified posting(s) pending in "
                f"{unclassified_path}. Fill in each row's 'category' before "
                f"merging — defaulting a category silently creates "
                f"cross-category duplicates."
            )

    by_cat = defaultdict(list)
    for p in sorted(reports_dir.glob("*.json")):
        if p.name == "unclassified.json":
            continue
        report = json.loads(p.read_text())
        by_cat[report["category"]].append(_filter_postings(report))

    # Merge into a buffer first. The integrity check has to see the whole
    # post-merge picture before anything is persisted, so the write loop runs
    # only after check_integrity has had its say.
    merged, summaries = {}, {}
    for cat, reports in by_cat.items():
        path = data_dir / f"{cat}.yaml"
        existing = _load_rows(path)
        rows, summary = merge_category(existing, reports, today)
        merged[cat] = _drop_invalid_rows(rows, summary)
        summaries[cat] = summary
        print(f"[{cat}] +{len(summary['new'])} new, "
              f"{len(summary['closed'])} newly closed, "
              f"{len(summary['possible_duplicates'])} possible dup(s)")
        for new_id, dup_of in summary["possible_duplicates"]:
            print(f"    warn: {new_id} may duplicate {dup_of}")

    # Every category, not just the merged ones: id and link uniqueness are
    # cross-category invariants, and checking them over a subset is
    # meaningless. A swe-only run still has to see quant.yaml to notice that
    # an incoming swe link is already tracked there.
    all_rows = dict(merged)
    for stem, _title, _is_quant in CATEGORIES:
        if stem not in all_rows:
            all_rows[stem] = _load_rows(data_dir / f"{stem}.yaml")

    violations = check_integrity(all_rows)
    for v in violations:
        print(f"INTEGRITY: {v}")
    if violations:
        print(f"INTEGRITY: {len(violations)} violation(s). Rows are NOT deleted "
              f"— losing a tracked listing is worse than tolerating a flaw. "
              f"Fix by hand, then re-run.")

    for cat, rows in merged.items():
        (data_dir / f"{cat}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))

    state = yaml.safe_load(state_path.read_text()) if state_path.exists() else {}
    state = state or {}
    state["_last_run"] = {
        "new": sum(len(summary["new"]) for summary in summaries.values()),
        "closed": sum(len(summary["closed"]) for summary in summaries.values()),
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))

    render(data_dir, readme_path, state["_last_run"])
    summaries["_integrity"] = violations
    return summaries


if __name__ == "__main__":
    result = run(sys.argv[1] if len(sys.argv) > 1 else "scratch/fetch_reports")
    # Non-zero so a downstream commit step fails loudly rather than the run
    # appearing to succeed with a broken invariant already written to disk.
    if result.get("_integrity"):
        sys.exit(1)
