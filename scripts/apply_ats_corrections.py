"""Apply an ats_corrections.json (from check_ats.py) to data/*.yaml — the
single serialized writer of a verification run. Applies
set_location/set_date/close/delete_non_us, stamps last_verified on every
row whose probe resolved, clears possible_duplicate_of pointers into
deleted rows, validates every touched row against ROW_SCHEMA before
anything is written (any failure aborts the whole apply), rewrites the
category YAML, and re-renders README.md. Never runs git.

Usage: python3 scripts/apply_ats_corrections.py [scratch/ats_corrections.json]
"""
import copy
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
    # deepcopy, not dict(): a shallow copy shares the nested `degree` and
    # `sources` lists with the caller, so the never-mutates guarantee would
    # hold only as long as no action touches a nested value.
    rows_by_category = copy.deepcopy(rows_by_category)
    index = {}
    for rows in rows_by_category.values():
        for row in rows:
            if row.get("id"):
                index[row["id"]] = row
    summary = {k: [] for k in (
        "confirmed", "location_fixed", "date_fixed", "closed", "deleted",
        "unresolved", "unknown", "skipped", "unrecognized_action")}
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
            # An action kind we don't implement, on a row that DOES exist —
            # a typo or a renamed action, not a stale id. Kept separate from
            # "skipped" so it can't be reported as a missing row.
            summary["unrecognized_action"].append(rid)
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

    # Corrections are matched to rows by id alone, so a duplicate id would
    # apply one row's correction to another row entirely: a delete would
    # remove every row sharing the id (reporting one), and a set_location
    # would land on whichever row loaded last. Duplicate ids are a known,
    # unfixed upstream bug in merge.py's id hash, and run_scrape_merge
    # deliberately writes them to disk anyway rather than lose a listing.
    # Refuse to touch a dataset in that state instead of guessing.
    seen, dupes = {}, set()
    for cat, rows in rows_by_category.items():
        for row in rows:
            rid = row.get("id")
            if rid is None:
                continue
            if rid in seen:
                dupes.add(rid)
            seen[rid] = cat
    colliding = sorted(dupes.intersection(
        {a.get("id") for a in doc["actions"]}))
    if colliding:
        for rid in colliding:
            print(f"DUPLICATE ID: {rid!r} matches more than one row")
        raise SystemExit(
            f"{len(colliding)} corrections id(s) match multiple rows; "
            f"nothing written. Resolve the duplicate ids first.")

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

    # Report deletes from the summary, not from the raw actions: an action
    # naming a row id that isn't in the data deletes nothing, and announcing
    # it would claim a destructive act that never happened.
    detail = {a.get("id"): a for a in doc["actions"]
              if a.get("action") == "delete_non_us"}
    for rid in summary["deleted"]:
        a = detail.get(rid, {})
        print(f"    DELETED (non-US): [{rid}] "
              f"api_locations={a.get('api_locations')} "
              f"country={a.get('country')}")
    for rid in summary["closed"]:
        print(f"    closed: [{rid}]")
    for rid in summary["skipped"]:
        print(f"    warn: skipped correction for unknown row id {rid!r}")
    for rid in summary["unrecognized_action"]:
        print(f"    warn: unrecognized action kind for existing row {rid!r}")
    print(", ".join(f"{k}={len(v)}" for k, v in summary.items()))
    return summary


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1
        else ROOT / "scratch" / "ats_corrections.json")
