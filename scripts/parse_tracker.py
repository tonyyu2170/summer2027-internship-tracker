"""Deterministic parsers: tracker source text -> fetch-report postings.

Pure and network-free, so it lives on the tested side of the boundary
docs/SCRAPING.md draws. scripts/fetch_trackers.py does the fetching and
calls in here. Four format families cover all nine trackers."""
import json
import re
import yaml
from datetime import datetime, timedelta, timezone

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
# The age/date column's header name across the four pipe-table trackers:
# speedyapply "Age", chieler/zapplyjobs "Posted", sndsh404 "Added". Kept out
# of _COLUMN_ALIASES/`header` on purpose -- see parse_pipe_table's date_idx
# handling, which must not tighten the cells-vs-header bound check that
# company/role/location/link rely on.
_DATE_COLUMN_LABELS = {"age", "posted", "added"}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# <N><unit>: m(inute)/h(our)/d(ay)/w(eek)/mo(nth), as seen live on
# zapplyjobs (e.g. "35m", "18h", "4d", "3w", "2mo") and speedyapply (days
# only, e.g. "4d"). Alternation tries "mo" before the single-char classes,
# so "1mo" doesn't get mis-split as "1m" + stray "o".
_AGE = re.compile(r"^(\d+)\s*(mo|[mhdw])$", re.IGNORECASE)
# Observed "no date on file" placeholders (chieler, sndsh404) -- distinct
# from unrecognized-but-present text like "Recently" or "Date unknown",
# which _derive_date_posted also returns (None, None) for, just via falling
# through every pattern instead of an explicit placeholder match.
_DASH_VALUES = {"-", "--", "---", "—"}
_HREF = re.compile(r'href="([^"]+)"')
_MD_LINK = re.compile(r"\[[^\]]*\]\((<?)([^)>\s]+)")
_TAG = re.compile(r"<[^>]+>")
_SEASON = r"(?:summer|fall|winter|spring)"
_OFF_CYCLE = re.compile(rf"\b{_SEASON}\s*20\d\d\b", re.I)
_YEAR = re.compile(r"\b20\d\d\b")
# 'summer' is deliberately absent here: a bare "Summer Analyst" carries no
# cycle information, so it cannot establish that a role is off-cycle.
_BARE_NON_SUMMER = re.compile(r"\b(?:fall|winter|spring)\b", re.I)
_SEASON_YEAR_NEAR = re.compile(
    rf"\b{_SEASON}\b.{{0,20}}?\b20\d\d\b|\b20\d\d\b.{{0,20}}?\b{_SEASON}\b", re.I)
_YEAR_START = re.compile(r"\b(20\d\d)\s+start\b", re.I)


def _is_off_cycle(role):
    """True if role text carries a cycle marker that isn't Summer 2027. This
    repo only tracks Summer 2027 (see CLAUDE.md); an off-cycle marker means
    the row must be dropped, not relabeled, since parse_pipe_table stamps
    every row's term as Summer 2027.

    Deliberately NOT off-cycle:
      - a bare season word "Summer" with no year ("Summer Analyst")
      - a bare non-2027 year with no season word ("Intern - Mechanical
        Engineer - 2026", "apps reviewed from Aug 2026") -- roles routinely
        carry a year for reasons unrelated to the cycle
    """
    adjacent = [m.group(0).lower().replace(" ", "") for m in _OFF_CYCLE.finditer(role)]
    if "summer2027" in adjacent:
        # Names the cycle we track, possibly alongside others
        # ("Fall 2026/Summer 2027"). Eligible either way. Checks every match,
        # not just the first, so the verdict doesn't depend on ordering.
        return False
    if adjacent:
        return True

    # No adjacent season+year pair. Widen, carefully.
    if not _YEAR.search(role) and _BARE_NON_SUMMER.search(role):
        return True                                  # "(FALL) Data Analyst Intern"
    near = _SEASON_YEAR_NEAR.search(role)
    if near and "2027" not in near.group(0):
        return True                                  # "2026 Internship, Fall - ..."
    start = _YEAR_START.search(role)
    if start and start.group(1) != "2027":
        return True                                  # "... - 2026 Start - BS/MS"
    return False


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


def _derive_date_posted(raw, reference_date):
    """Resolve one age/date cell to (date_posted, estimated), or (None,
    None) when the cell carries no derivable date. reference_date is the
    caller-supplied fetch date (a datetime.date) -- never date.today(), per
    docs/SCRAPING.md's rule that parse functions stay pure.

    Precision rule: an explicit date, or a day/week-granularity age, is
    trusted exactly (estimated=False). A month-granularity age is still
    derived -- better than the scrape-date fallback -- but flagged
    estimated=True since "2mo" only pins the day to +/-2 weeks or so.
    Anything unrecognized (dash placeholders, "Recently", "Date unknown",
    ...) returns (None, None) rather than guessing; the caller (merge.py)
    already falls back to today's date with date_estimated=True for a
    posting with no date_posted at all."""
    text = (raw or "").strip()
    if not text or text in _DASH_VALUES:
        return None, None
    if _ISO_DATE.match(text):
        return text, False
    if text.lower() in ("today", "new"):
        return reference_date.isoformat(), False
    m = _AGE.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "mo":
            return (reference_date - timedelta(days=30 * n)).isoformat(), True
        if unit == "w":
            return (reference_date - timedelta(weeks=n)).isoformat(), False
        if unit == "d":
            return (reference_date - timedelta(days=n)).isoformat(), False
        return reference_date.isoformat(), False    # 'h'/'m': same calendar day
    return None, None


def parse_pipe_table(text, reference_date):
    """Parse every Markdown pipe table in a README that looks like a job
    table, i.e. whose header maps to at least company, role and link.

    reference_date (a datetime.date) is the fetch date used to derive
    date_posted from an age column ("4d", "2mo", ...) -- required, not
    defaulted, so this stays a pure function; see _derive_date_posted.

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
        date_idx = None
        for idx, name in enumerate(_cells(line)):
            label = _TAG.sub("", name).strip("* ").lower()
            key = _COLUMN_ALIASES.get(label)
            if key and key not in header:
                header[key] = idx
            elif date_idx is None and label in _DATE_COLUMN_LABELS:
                date_idx = idx
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
                # Let unresolved garbage ("USA", "multiple US") become None
                # rather than passing the raw text through — a None here is
                # what makes run_scrape_merge.py's pre-merge gate print a
                # visible warning instead of merge.py's US-only filter
                # dropping it later with no trace at all.
                location = _resolve_us_location(location)
            if not (company and role):
                continue
            posting = {
                "company": company,
                "role": role,
                "location": location,
                "link": link,
                "term": "Summer 2027",
                "degree": ["BS"],
                "closed_marker": closed,
            }
            if date_idx is not None and date_idx < len(cells):
                raw_date = _TAG.sub("", cells[date_idx]).strip()
                date_posted, date_estimated = _derive_date_posted(raw_date, reference_date)
                if date_posted:
                    posting["date_posted"] = date_posted
                    if date_estimated:
                        posting["date_estimated"] = True
            postings.append(posting)
    return postings
