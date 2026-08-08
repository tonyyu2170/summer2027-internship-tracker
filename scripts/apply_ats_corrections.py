"""Apply an ats_corrections.json (from check_ats.py) to data/*.yaml — the
single serialized writer of a verification run. Applies
set_location/set_date/close/delete_non_us, stamps last_verified on every
row whose probe resolved, clears possible_duplicate_of pointers into
deleted rows, validates every touched row against ROW_SCHEMA before
anything is written (any failure aborts the whole apply), rewrites the
category YAML, and re-renders README.md. Never runs git.

Usage: python3 scripts/apply_ats_corrections.py [scratch/ats_corrections.json]
"""
import json
import sys
import yaml
from pathlib import Path
from datetime import date

from schema import validate_row
from generate_readme import render, ROOT, CATEGORIES

# actions that prove the posting was authoritatively seen this run
_RESOLVED = {"confirm", "set_location", "set_date", "close",
             "location_unresolved"}


def apply_corrections(rows_by_category, actions, today):
    """Pure. Returns (new_rows_by_category, summary); never mutates input.
    summary maps outcome kinds to sorted row-id lists; 'skipped' holds ids
    from the corrections file that no longer exist in the data."""
    rows_by_category = {
        cat: [dict(r) for r in rows] for cat, rows in rows_by_category.items()
    }
    index = {}
    for rows in rows_by_category.values():
        for row in rows:
            if row.get("id"):
                index[row["id"]] = row
    summary = {k: [] for k in (
        "confirmed", "location_fixed", "date_fixed", "closed", "deleted",
        "unresolved", "unknown", "skipped")}
    deleted, verified = set(), set()
    for a in actions:
        rid, act = a.get("id"), a.get("action")
        row = index.get(rid)
        if row is None:
            summary["skipped"].append(rid)
            continue
        if act in _RESOLVED:
            verified.add(rid)
        if act == "confirm":
            summary["confirmed"].append(rid)
        elif act == "set_location":
            row["location"] = a["new"]
            summary["location_fixed"].append(rid)
        elif act == "set_date":
            row["date_posted"] = a["new"]
            row["date_estimated"] = False
            summary["date_fixed"].append(rid)
        elif act == "close":
            row["status"] = "closed"
            summary["closed"].append(rid)
        elif act == "delete_non_us":
            deleted.add(rid)
            summary["deleted"].append(rid)
        elif act == "location_unresolved":
            summary["unresolved"].append(rid)
        elif act == "unknown":
            summary["unknown"].append(rid)
        else:
            summary["skipped"].append(rid)
    new = {}
    for cat, rows in rows_by_category.items():
        kept = []
        for row in rows:
            if row.get("id") in deleted:
                continue
            if row.get("id") in verified:
                row["last_verified"] = today
            if row.get("possible_duplicate_of") in deleted:
                row["possible_duplicate_of"] = None
            kept.append(row)
        new[cat] = kept
    for ids in summary.values():
        ids.sort(key=str)
    return new, summary


def run(corrections_path, data_dir=None, readme_path=None):
    corrections_path = Path(corrections_path)
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    doc = json.loads(corrections_path.read_text())
    rows_by_category = {}
    for stem, _title, _is_quant in CATEGORIES:
        path = data_dir / f"{stem}.yaml"
        rows_by_category[stem] = (
            (yaml.safe_load(path.read_text()) or []) if path.exists() else [])

    today = date.today().isoformat()
    new_rows, summary = apply_corrections(rows_by_category, doc["actions"], today)

    # Validate only the rows this run touched: pre-existing malformed
    # hand-edits are tolerated exactly as run_scrape_merge does.
    touched = set()
    for kind in ("confirmed", "location_fixed", "date_fixed", "closed",
                 "unresolved"):
        touched.update(summary[kind])
    errors = []
    for cat, rows in new_rows.items():
        for row in rows:
            if row.get("id") in touched:
                for err in validate_row(row):
                    errors.append(f"[{cat}] {row['id']}: {err}")
    if errors:
        for e in errors:
            print(f"SCHEMA: {e}")
        raise SystemExit(
            f"{len(errors)} schema error(s) on corrected rows; nothing written.")

    for cat, rows in new_rows.items():
        (data_dir / f"{cat}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    render(data_dir, readme_path)

    for a in doc["actions"]:
        if a.get("action") == "delete_non_us":
            print(f"    DELETED (non-US): [{a.get('id')}] "
                  f"api_locations={a.get('api_locations')} "
                  f"country={a.get('country')}")
    for rid in summary["closed"]:
        print(f"    closed: [{rid}]")
    for rid in summary["skipped"]:
        print(f"    warn: skipped correction for unknown row id {rid!r}")
    print(", ".join(f"{k}={len(v)}" for k, v in summary.items()))
    return summary


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1
        else ROOT / "scratch" / "ats_corrections.json")
