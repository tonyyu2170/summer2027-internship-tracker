"""Deterministic parsers: tracker source text -> fetch-report postings.

Pure and network-free, so it lives on the tested side of the boundary
docs/SCRAPING.md draws. scripts/fetch_trackers.py does the fetching and
calls in here. Four format families cover all nine trackers."""
import json
import yaml
from datetime import datetime, timezone

from normalize import canonicalize_location

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


# canonicalize_location() only accepts "City, ST"/"City, State" — it
# correctly drops non-US locations, but also drops legitimate US postings
# spelled as a bare city nickname (e.g. "NYC"). This is a small, deliberately
# non-exhaustive list of unambiguous major-hub aliases, not an attempt at
# full city coverage.
_KNOWN_CITY_ALIASES = {
    "nyc": "New York, NY",
    "new york": "New York, NY",
    "new york city": "New York, NY",
    "sf": "San Francisco, CA",
    "bay area": "San Francisco, CA",
    "denver": "Denver, CO",
}


def _resolve_us_location(raw):
    """Resolve a source's location string to 'City, ST' if possible, trying
    the strict canonicalize_location() shape first, then a small alias map
    for well-known US city nicknames it doesn't recognize. Returns None
    when neither resolves — the caller (merge.py's US-only filter) treats
    that as non-US or unplaceable."""
    canon = canonicalize_location(raw)
    if canon:
        return canon
    return _KNOWN_CITY_ALIASES.get((raw or "").strip().lower())


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
            "location": _resolve_us_location(locations[0]) if locations else None,
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


def _from_iso(value):
    if not value:
        return None
    return value.split("T")[0]


def parse_zshah_json(text, season):
    """Parse zshah101's data/jobs.json — a dict keyed by job id, with a
    singular `location` string and an explicit `season` per entry."""
    entries = json.loads(text)
    values = entries.values() if isinstance(entries, dict) else entries
    postings = []
    for e in values:
        if e.get("season") != season:
            continue
        posting = {
            "company": e.get("company"),
            "role": e.get("title"),
            "location": _resolve_us_location(e.get("location")),
            "link": e.get("url"),
            "term": season,
            "degree": ["BS"],
            "closed_marker": not e.get("is_open", True),
        }
        date_posted = _from_iso(e.get("posted_at"))
        if date_posted:
            posting["date_posted"] = date_posted
        if e.get("category"):
            posting["upstream_category"] = e["category"]
        postings.append(posting)
    return postings


# HW routes to hardware even though this is a quant-only repo — the
# established convention, and the bug fixed by hand in 0fdf5dd.
_NUFINTECH_ROLES = {
    "QR": ("quant", "Quantitative Researcher Intern"),
    "QD": ("quant", "Quantitative Developer Intern"),
    "QT": ("quant", "Quantitative Trader Intern"),
    "SWE": ("swe", "Software Engineer Intern"),
    "HW": ("hardware", "Hardware Engineer Intern"),
}


# Companies publish `locations` inconsistently — bare city names with no
# state, non-US cities, and multi-location strings joined by "," or ";"
# with no clear separator between the location list and a trailing state.
# canonicalize_location() only accepts "City, ST"/"City, State"; this maps
# the city spellings seen across the tracker's ~58 companies onto that
# shape, or None for a known non-US city, so the shared US-only filter in
# merge.py can do its job. Unrecognized cities fall through to None
# (dropped) rather than guessing.
_NUFINTECH_CITY_MAP = {
    "chicago": "Chicago, IL",
    "nyc": "New York, NY",
    "new york city": "New York, NY",
    "boston": "Boston, MA",
    "houston": "Houston, TX",
    "miami": "Miami, FL",
    "irvine": "Irvine, CA",
    "berkeley": "Berkeley, CA",
    "bala cynwyd": "Bala Cynwyd, PA",
    "setauket": "Setauket, NY",
    "samford": "Stamford, CT",  # source typo for Stamford, CT (Trexquant HQ)
    "london": None,  # non-US
}


def _resolve_nufintech_location(raw):
    """Resolve one northwesternfintech `locations` string to 'City, ST',
    or None if it can't be confidently placed. Multiple locations are
    joined by ';' (already-valid 'City, ST' segments) or ',' (bare city
    names with no per-city state); only the first is used, matching the
    "emit the first" convention used elsewhere in this project."""
    if not raw:
        return None
    first = raw.split(";")[0].strip()
    canon = canonicalize_location(first)
    if canon:
        return canon
    token = first.split(",")[0].split(" - ")[0].strip().lower()
    return _NUFINTECH_CITY_MAP.get(token)


def parse_nufintech_yaml(text):
    """Parse one northwesternfintech data/<company>.yaml file.

    Emits `category` directly from the role_type code rather than leaving it
    to the classifier. Never sets closed_marker: the source has no status
    field, so a vanished role is disappearance, which this repo does not
    auto-close on."""
    doc = yaml.safe_load(text) or {}
    company = doc.get("name")
    location = _resolve_nufintech_location(doc.get("locations"))
    postings = []
    for role in doc.get("roles") or []:
        mapped = _NUFINTECH_ROLES.get(role.get("role_type"))
        if not mapped:
            continue
        category, base_role = mapped
        for link in role.get("links") or []:
            url = link.get("url")
            if not url:
                continue
            label = (link.get("label") or "").strip()
            postings.append({
                "company": company,
                "role": f"{base_role}, {label}" if label else base_role,
                "location": location,
                "link": url,
                "term": "Summer 2027",
                "degree": ["BS"],
                "closed_marker": False,
                "category": category,
            })
    return postings
