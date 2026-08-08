"""Deterministic dedupe-and-merge engine. Pure: no I/O, no network.

Consumes fetch reports (see docs/SCRAPING.md) plus the existing rows for one
category and returns the merged rows and a run summary. The single serialized
writer in run_scrape_merge.py calls this once per category file."""
import re
import hashlib
from normalize import normalize_link, normalize_company, canonicalize_location


def _slug(company: str, role: str, key: str) -> str:
    base = "-".join(
        re.sub(r"[^a-z0-9]+", "-", x.lower()).strip("-")
        for x in (company, role)
    )
    return f"{base}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def _triple(item: dict):
    """Low-confidence fallback identity: (company, role, canonical location)."""
    return (
        normalize_company(item["company"]),
        item["role"].strip().lower(),
        canonicalize_location(item["location"]),
    )


def merge_category(existing_rows, fetch_reports, today, on_drop=None):
    """existing_rows: list[dict]; fetch_reports: list[fetch-report dict];
    today: 'YYYY-MM-DD'. Returns (rows, summary)."""
    rows = [dict(r) for r in existing_rows]          # copy; never mutate input
    for r in rows:
        r["sources"] = list(r["sources"])
    by_link = {normalize_link(r["link"]): r for r in rows}
    by_triple = {_triple(r): r for r in rows}
    summary = {"new": [], "closed": [], "possible_duplicates": []}

    for report in fetch_reports:
        for p in report["postings"]:
            canon_loc = canonicalize_location(p["location"])
            if canon_loc is None:                    # US-only filter
                src = p.get("source", report.get("source_entity", "unknown"))
                print(f"    warn: [{src}] skipped non-US location: {p['location']!r}")
                if on_drop:
                    on_drop(src, "non_us_location")
                continue
            nlink = normalize_link(p["link"])
            src = p.get("source", report.get("source_entity", "unknown"))

            if nlink in by_link:                      # same posting, re-found
                row = by_link[nlink]
                row["last_verified"] = today
                if src not in row["sources"]:
                    row["sources"].append(src)
                if p.get("closed_marker") and row["status"] != "closed":
                    row["status"] = "closed"
                    if row.get("id"):
                        summary["closed"].append(row["id"])
                continue

            trip = _triple({**p, "location": canon_loc})
            dup_of = by_triple[trip].get("id") if trip in by_triple else None
            row = {
                "id": _slug(p["company"], p["role"], nlink),
                "company": p["company"],
                "role": p["role"],
                "location": canon_loc,
                "link": p["link"],
                "date_posted": p.get("date_posted") or today,
                # A posting can flag its own derived date_posted as coarse
                # (e.g. a "2mo"-granularity pipe-table age -- see
                # parse_tracker._derive_date_posted); that must survive onto
                # the row. `or` rather than a bare lookup: a posting with no
                # date_posted at all is always estimated (today-fallback),
                # regardless of what it claims.
                "date_estimated": bool(p.get("date_estimated")) or p.get("date_posted") is None,
                "term": p["term"],
                "degree": p["degree"],
                "status": "closed" if p.get("closed_marker") else "open",
                "sources": [src],
                "date_added": today,
                "last_verified": today,
                "possible_duplicate_of": dup_of,
            }
            if p.get("track"):
                row["track"] = p["track"]
            rows.append(row)
            by_link[nlink] = row
            by_triple.setdefault(trip, row)
            summary["new"].append(row["id"])
            if dup_of:
                summary["possible_duplicates"].append((row["id"], dup_of))

    return rows, summary
