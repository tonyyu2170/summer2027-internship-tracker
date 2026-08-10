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
from categorize import DROP, classify_role
from normalize import normalize_link


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
