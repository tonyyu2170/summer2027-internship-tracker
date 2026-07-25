"""Deterministic parsers: tracker source text -> fetch-report postings.

Pure and network-free, so it lives on the tested side of the boundary
docs/SCRAPING.md draws. scripts/fetch_trackers.py does the fetching and
calls in here. Four format families cover all nine trackers."""
import json
from datetime import datetime, timezone

_DEGREE_MAP = {
    "bachelor's": "BS", "bachelors": "BS", "bs": "BS",
    "master's": "MS", "masters": "MS", "ms": "MS",
    "phd": "PhD", "ph.d.": "PhD", "doctorate": "PhD",
}


def _degrees(values) -> list:
    """Map a source's degree strings onto the schema's BS/MS/PhD enum.
    Defaults to ['BS'] — these lists target undergrads."""
    out = []
    for v in values or []:
        mapped = _DEGREE_MAP.get(str(v).strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    return out or ["BS"]


def _from_unix(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def parse_cvrve_json(text, term_field, term_value, term_out=None):
    """Parse the cvrve-family export shared by simplifyjobs,
    suryaharikrishnan and vanshb03.

    term_field is 'terms' (a list) or 'season' (a string); only entries
    matching term_value survive. term_out is the term written to the
    posting, defaulting to term_value — vanshb03 stores season 'Summer' in a
    2027-scoped repo, so it emits 'Summer 2027'."""
    entries = json.loads(text)
    term_out = term_out or term_value
    postings = []
    for e in entries:
        raw = e.get(term_field)
        matches = term_value in raw if isinstance(raw, list) else raw == term_value
        if not matches:
            continue
        locations = e.get("locations") or []
        posting = {
            "company": e.get("company_name"),
            "role": e.get("title"),
            "location": locations[0] if locations else None,
            "link": e.get("url"),
            "term": term_out,
            "degree": _degrees(e.get("degrees")),
            "closed_marker": not e.get("active", True),
        }
        date_posted = _from_unix(e.get("date_posted"))
        if date_posted:
            posting["date_posted"] = date_posted
        if e.get("category"):
            posting["upstream_category"] = e["category"]
        postings.append(posting)
    return postings
