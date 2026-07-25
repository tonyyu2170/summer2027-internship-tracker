"""Deterministic parsers: tracker source text -> fetch-report postings.

Pure and network-free, so it lives on the tested side of the boundary
docs/SCRAPING.md draws. scripts/fetch_trackers.py does the fetching and
calls in here. Four format families cover all nine trackers."""
import json
import re
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


_COLUMN_ALIASES = {
    "company": "company", "company name": "company",
    "role": "role", "position": "role", "job": "role", "title": "role",
    "location": "location", "locations": "location",
    "link": "link", "application": "link", "application/link": "link",
    "apply": "link", "application link": "link", "posting": "link",
}
_HREF = re.compile(r'href="([^"]+)"')
_MD_LINK = re.compile(r"\[[^\]]*\]\((<?)([^)>\s]+)")
_TAG = re.compile(r"<[^>]+>")
_OFF_CYCLE = re.compile(r"\b(summer|fall|winter|spring)\s*20\d\d\b", re.I)


def _is_off_cycle(role):
    """True if role text carries an explicit season+year marker that isn't
    Summer 2027. This repo only tracks Summer 2027 (see CLAUDE.md); an
    explicit off-cycle marker means the row must be dropped, not relabeled,
    since parse_pipe_table stamps every row's term as Summer 2027."""
    m = _OFF_CYCLE.search(role)
    return bool(m) and m.group(0).lower().replace(" ", "") != "summer2027"


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _first_location(cell):
    """Collapse a location cell to its first entry. Handles </br>-joined
    lists, <details> blocks wrapping many locations, and trailing "+N"/
    "(multiple US)" decoration some trackers append to indicate more
    locations were collapsed."""
    text = re.sub(r"<summary>.*?</summary>", "", cell, flags=re.DOTALL)
    text = re.split(r"</br>|<br\s*/?>", text)[0]
    text = _TAG.sub("", text).strip()
    text = re.sub(r"\s*\+\d+\s*$", "", text)
    text = re.sub(r"\s*\(multiple US\)\s*$", "", text, flags=re.I)
    return text.strip()


def _extract_link(cell):
    m = _HREF.search(cell)
    if m:
        return m.group(1)
    m = _MD_LINK.search(cell)
    if m:
        return m.group(2)
    return None


def parse_pipe_table(text):
    """Parse every Markdown pipe table in a README that looks like a job
    table, i.e. whose header maps to at least company, role and link.

    Column order differs across trackers, so columns are located by header
    name. Tables that don't match (resource lists, prep links) are skipped."""
    postings = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip().startswith("|"):
            i += 1
            continue
        header = {}
        for idx, name in enumerate(_cells(line)):
            key = _COLUMN_ALIASES.get(_TAG.sub("", name).strip("* ").lower())
            if key and key not in header:
                header[key] = idx
        if not {"company", "role", "link"} <= set(header):
            i += 1
            continue
        i += 1
        if i < len(lines) and re.match(r"^\|[\s\-:|]+\|?$", lines[i].strip()):
            i += 1
        last_company = None
        while i < len(lines) and lines[i].strip().startswith("|"):
            cells = _cells(lines[i])
            i += 1
            if max(header.values()) >= len(cells):
                continue
            link = _extract_link(cells[header["link"]])
            if not link:
                continue
            company = _TAG.sub("", cells[header["company"]]).strip().strip("*")
            if company in ("↳", "|↳", ""):
                company = last_company
            else:
                last_company = company
            role = _TAG.sub("", cells[header["role"]]).strip()
            closed = "🔒" in role
            role = role.replace("🔒", "").replace("🛂", "").replace("🇺🇸", "")
            role = role.replace("🔥", "").replace("🎓", "").strip()
            if _is_off_cycle(role):
                continue
            location = (
                _first_location(cells[header["location"]])
                if "location" in header else None
            )
            if location:
                location = _resolve_us_location(location) or location
            if not (company and role):
                continue
            postings.append({
                "company": company,
                "role": role,
                "location": location,
                "link": link,
                "term": "Summer 2027",
                "degree": ["BS"],
                "closed_marker": closed,
            })
    return postings
