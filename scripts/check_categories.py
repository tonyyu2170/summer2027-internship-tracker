"""Report rows whose role no longer classifies to the category file they live
in, so a categorize.py rule change can retro-apply to already-tracked rows.

classify_role runs only on incoming postings (fetch_trackers.py), and a row
already in data/*.yaml always wins over sources/manual_categories.yaml — so
every rule added to categorize.py improves only future scrapes and leaves the
existing corpus stale. This module finds that drift.

It never writes data/*.yaml. It writes one corrections JSON — the audit record
— which Tony reviews and apply_ats_corrections.py applies, exactly like
check_ats.py and check_reposts.py. Unlike those two it needs no network, so it
carries its own pure function rather than splitting into a verify/driver pair.

Usage: python3 scripts/check_categories.py [--report-only]
"""
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from categorize import DROP, classify_role, manual_link_categories
from generate_readme import CATEGORIES
from normalize import normalize_link

ROOT = Path(__file__).resolve().parent.parent


def find_disagreements(rows_by_category, overrides):
    """Pure. Returns one proposed action per open row whose role classifies to
    a category other than the file it lives in.

    `overrides` is normalized-link -> category, from manual_link_categories();
    a row whose link appears there was already adjudicated by hand and is left
    alone rather than re-litigated. A None classification means the rules have
    no opinion, which is never grounds to move a row.
    """
    actions = []
    for cat in sorted(rows_by_category):
        for row in rows_by_category[cat]:
            if row.get("status") == "closed":
                continue
            link = row.get("link") or ""
            if normalize_link(link) in overrides:
                continue
            got = classify_role(row.get("role") or "")
            if got is None or got == cat:
                continue
            actions.append({
                "id": row.get("id"),
                "action": "drop" if got == DROP else "recategorize",
                "from": cat,
                "to": got,
                "company": row.get("company"),
                "role": row.get("role"),
                "link": link,
            })
    return actions


def load_rows(data_dir):
    """category stem -> rows. Every stem in CATEGORIES is present even when its
    file is missing, so a recategorize target is always a valid key."""
    rows_by_category = {}
    for stem, _title, _is_quant in CATEGORIES:
        path = Path(data_dir) / f"{stem}.yaml"
        rows_by_category[stem] = (
            (yaml.safe_load(path.read_text()) or []) if path.exists() else [])
    return rows_by_category


def write_drift_marker(marker_path, count, today):
    """Overwrite the advisory marker, or remove it when there is no drift.

    Deliberately NOT scratch/auto_scrape/NEEDS_ATTENTION: auto_scrape.sh does
    `rm -f "$MARKER"` on both success paths, so an advisory written there is
    wiped in the same run, and that file means "the scrape stopped". Overwrite
    (not append) keeps this from growing a line per scrape while a backlog sits
    unadjudicated; removal at zero makes the file self-healing.
    """
    marker_path = Path(marker_path)
    if count:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            f"{today} {count} row(s) sit in a category their role no longer "
            f"classifies to.\nRun scripts/check_categories.py, review "
            f"scratch/category_corrections.json, then apply it.\n")
    elif marker_path.exists():
        marker_path.unlink()


def run(data_dir=None, out_path=None, marker_path=None, report_only=False):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    out_path = Path(out_path) if out_path else ROOT / "scratch" / "category_corrections.json"
    marker_path = (Path(marker_path) if marker_path
                   else ROOT / "scratch" / "auto_scrape" / "CATEGORY_DRIFT")

    rows_by_category = load_rows(data_dir)
    actions = find_disagreements(rows_by_category, manual_link_categories())
    today = date.today().isoformat()

    write_drift_marker(marker_path, len(actions), today)

    for a in actions:
        print(f"    {a['from']} -> {a['to']}: [{a['id']}] "
              f"{a['company']} | {a['role']}")
    print(f"{len(actions)} disagreement(s)")

    if report_only:
        # Writing the JSON here would trip auto_scrape.sh's own in-flight
        # guard and block every later scrape until the review finished.
        return actions

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated": today, "actions": actions}, indent=2))
    print(f"-> {out_path}")
    return actions


if __name__ == "__main__":
    run(report_only="--report-only" in sys.argv[1:])
