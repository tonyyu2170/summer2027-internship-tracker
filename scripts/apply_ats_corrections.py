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
    raise NotImplementedError   # Task 7
