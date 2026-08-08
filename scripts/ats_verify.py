"""Pure ATS-API verification logic. No I/O, no network.

Given a row's application link: derive the authoritative API URL for its
ATS family (`api_url`), parse a probe of that URL into a normalized
extract (`extract`), and decide what corrections the row needs (`decide`).
The network driver is check_ats.py; the write side is
apply_ats_corrections.py. Design:
docs/superpowers/specs/2026-08-08-ats-verification-design.md."""
import json
import re
from datetime import datetime, timedelta, timezone

from link_check import workday_cxs_url
from normalize import canonicalize_location, _NON_US_RE

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Regional boards (job-boards.eu.greenhouse.io) are served by the same
# boards-api host, so only the link pattern needs to know about them.
_GREENHOUSE_RE = re.compile(
    r"^https?://(?:job-boards|boards)(?:\.[a-z]{2})?\.greenhouse\.io"
    r"/([^/?#]+)/jobs/(\d+)", re.I)
_LEVER_RE = re.compile(
    rf"^https?://jobs\.lever\.co/([^/?#]+)/({_UUID})", re.I)
_ASHBY_RE = re.compile(
    rf"^https?://jobs\.ashbyhq\.com/([^/?#]+)/({_UUID})", re.I)
_SMARTRECRUITERS_RE = re.compile(
    r"^https?://jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.I)
_ICIMS_RE = re.compile(r"^https?://[^/]+\.icims\.com/jobs/\d+/", re.I)

_US_COUNTRY = {"us", "usa", "united states", "united states of america"}


def _is_us_country(country) -> bool:
    return (country or "").strip().lower() in _US_COUNTRY


_LOC_SPLIT_RE = re.compile(r"\s*[|;]\s*|\n+")
_PAREN_RE = re.compile(r"\s*\([^)]*\)")
_COUNTRY_TOKEN = r"(?:u\.?s\.?a?\.?|united states(?: of america)?)"
_COUNTRY_PREFIX_RE = re.compile(rf"^{_COUNTRY_TOKEN}\s*[-,]\s*", re.I)
_COUNTRY_SUFFIX_RE = re.compile(rf"[\s,-]+{_COUNTRY_TOKEN}\s*$", re.I)
_SITE_PREFIX_RE = re.compile(
    r"^(?:corporate|office|onsite|on-site|hybrid|remote)\s*-\s*", re.I)
_AREA_SUFFIX_RE = re.compile(r"\s+area$", re.I)
_JUNK_CITY_RE = re.compile(r"^\d|[|/]")
_CITY_ALIASES = {"new york city": "New York"}

# Earliest date that can plausibly be a Summer 2027 posting date. Bump this
# when the tracker moves to a new cycle.
_CYCLE_START = "2026-01-01"


def _location_candidates(raw):
    """Raw ATS location text -> candidate 'City, ST' strings.

    API location fields carry shapes canonicalize_location was never built
    for — it reads the last comma-part as the state and everything before
    it as the city, which is right for tracker table text and wrong here.
    Observed live: 'Denver, CO | Long Beach, CA' became 'Denver, CA'
    (wrong state), '150 North Riverside, Chicago, IL' became
    '150 North Riverside, IL' (street kept, city lost), and
    'North America/USA/Minnesota/Mankato, MN' passed through whole. So
    split the multi-location forms and strip the wrappers first, and let
    each candidate be canonicalized on its own."""
    out = []
    parts = _LOC_SPLIT_RE.split(raw or "")
    # 'Dallas, TX - Headquarters' also gets tried as 'Dallas, TX'. Only when
    # the head half carries a comma, so it looks like 'City, ST': without
    # that guard 'Remote - New York' yields the bare candidate 'Remote',
    # which canonicalizes to 'Remote (US)' and loses the city.
    parts += [p.split(" - ", 1)[0] for p in list(parts)
              if " - " in p and "," in p.split(" - ", 1)[0]]
    for part in parts:
        if "/" in part:
            part = part.rsplit("/", 1)[-1]       # org path -> its leaf
        part = _PAREN_RE.sub("", part)
        part = _COUNTRY_SUFFIX_RE.sub("", part)
        part = _COUNTRY_PREFIX_RE.sub("", part)
        part = _SITE_PREFIX_RE.sub("", part)
        segs = [s.strip() for s in part.split(",") if s.strip()]
        if len(segs) > 2:
            segs = segs[-2:]                     # drop street/country lead-ins
        if len(segs) == 2:
            city = _AREA_SUFFIX_RE.sub("", segs[0]).strip()
            city = _CITY_ALIASES.get(city.lower(), city)
            state = segs[1].replace(".", "")     # 'D.C.' -> 'DC'
            segs = [city.title() if city.islower() else city, state]
        cand = ", ".join(segs)
        if cand:
            out.append(cand)
    return out


def _plausible_city(canon) -> bool:
    """Reject a canonical result whose city half is a leftover fragment —
    a street number, a path remnant, or a bare country token."""
    city = canon.rsplit(",", 1)[0].strip()
    return bool(city) and not _JUNK_CITY_RE.search(city) and \
        city.lower() not in {"usa", "us", "united states"}


def _names_non_us_country(country) -> bool:
    """True only when the country field AFFIRMATIVELY names a non-US country.

    Deliberately not `not _is_us_country(...)`: negation-as-failure would
    read unrecognized US spellings ("U.S.", "America") as non-US evidence
    and delete live US rows. Under-matching is the safe direction here — a
    country this doesn't recognize (Germany, Japan) yields
    location_unresolved for manual review instead of a silent delete. Widen
    by adding an affirmative country list here, never by inverting the US
    check (spec decision 3; Tony, 2026-08-08)."""
    return bool(_NON_US_RE.search((country or "").strip().lower()))


def api_url(link: str):
    """('family', probe_url) for a link on a covered ATS, else None.

    For ashby the URL is the org's whole job board (one payload covers every
    row of that org; extract() picks out the row's own job by the link's
    trailing UUID). For icims it's the posting page itself (JSON-LD)."""
    cxs = workday_cxs_url(link)
    if cxs:
        return ("workday", cxs)
    m = _GREENHOUSE_RE.match(link)
    if m:
        return ("greenhouse",
                f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    m = _LEVER_RE.match(link)
    if m:
        return ("lever", f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    m = _ASHBY_RE.match(link)
    if m:
        return ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
    m = _SMARTRECRUITERS_RE.match(link)
    if m:
        return ("smartrecruiters",
                f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
    if _ICIMS_RE.match(link):
        return ("icims", link)
    return None


def extract(ats, status, body, link="", today=None):
    """Normalize one probe of an api_url into
    {"locations": [str], "country": str|None,
     "date_posted": "YYYY-MM-DD"|None, "closed": bool},
    or None for "couldn't tell" (ambiguous HTTP status, or a payload that
    doesn't parse as expected — format drift is never guessed at).

    `link` is the row's own application link — the ashby extractor needs it
    to find the row's job inside the org-wide board payload. `today` is a
    datetime.date anchoring workday's relative posted-on text."""
    if status in (404, 410):
        # Every family's probe URL is posting-scoped, so a 404 means the
        # posting is gone — except ashby, whose probe URL is the org's whole
        # BOARD: a 404 there means the org disabled or moved the board, not
        # that this posting closed. Treat that as unknown rather than
        # false-closing every row of the org.
        if ats == "ashby":
            return None
        return {"locations": [], "country": None, "date_posted": None,
                "closed": True}
    if not (200 <= status < 300) or not body:
        return None
    try:
        return _EXTRACTORS[ats](body, link, today)
    except Exception:
        return None


_POSTED_DAYS_RE = re.compile(r"(\d+)(\+?)\s*days?\s+ago", re.I)


def _workday_date(posted_on, today):
    """'Posted Today'/'Posted Yesterday'/'Posted N Days Ago' anchored to
    `today`. 'Posted 30+ Days Ago' is a lower bound, not a date — too
    coarse to be a correction, so None."""
    if today is None:
        return None
    low = posted_on.lower()
    if "today" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - timedelta(days=1)).isoformat()
    m = _POSTED_DAYS_RE.search(low)
    if m and not m.group(2):
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return None


def _extract_workday(body, link, today):
    info = json.loads(body)["jobPostingInfo"]
    locations = [info["location"]] + list(info.get("additionalLocations") or [])
    return {
        "locations": locations,
        "country": (info.get("country") or {}).get("descriptor"),
        "date_posted": _workday_date(info.get("postedOn") or "", today),
        "closed": False,
    }


def _extract_greenhouse(body, link, today):
    j = json.loads(body)
    j["id"]                       # shape guard: not a job payload -> drift
    name = (j.get("location") or {}).get("name") or ""
    return {
        "locations": [part.strip() for part in name.split(";") if part.strip()],
        "country": None,          # greenhouse carries country only in the text
        "date_posted": (j.get("first_published") or "")[:10] or None,
        "closed": False,
    }


def _extract_lever(body, link, today):
    j = json.loads(body)
    j["id"]                       # shape guard
    cats = j.get("categories") or {}
    locations = list(cats.get("allLocations") or [])
    if not locations and cats.get("location"):
        locations = [cats["location"]]
    created = j.get("createdAt")
    return {
        "locations": locations,
        "country": j.get("country"),
        "date_posted": (datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                        .date().isoformat() if created else None),
        "closed": False,
    }


def _extract_ashby(body, link, today):
    jobs = json.loads(body)["jobs"]
    # Take the uuid from _ASHBY_RE, not from the link's trailing path segment:
    # real links carry /application, /apply and ?utm_* suffixes, so a naive
    # rsplit yields "application" — which matches no job and would send the
    # row down the closed branch below. Manufacturing an authoritative "gone"
    # from a parse miss is exactly what this module must never do.
    m = _ASHBY_RE.match(link)
    if not m:
        return None
    uuid = m.group(2).lower()
    job = next(
        (jb for jb in jobs
         if (jb.get("id") or "").lower() == uuid
         or uuid in (jb.get("jobUrl") or "").lower()),
        None)
    if job is None:
        # the org's own board no longer serves this posting id — that is the
        # board's authoritative "gone", not scrape disappearance
        return {"locations": [], "country": None, "date_posted": None,
                "closed": True}
    address = ((job.get("address") or {}).get("postalAddress") or {})
    locations = []
    locality, region = address.get("addressLocality"), address.get("addressRegion")
    if locality and region:
        locations.append(f"{locality}, {region}")
    if job.get("location"):
        locations.append(job["location"])
    for sec in job.get("secondaryLocations") or []:
        if sec.get("location"):
            locations.append(sec["location"])
    return {
        "locations": locations,
        "country": address.get("addressCountry"),
        "date_posted": (job.get("publishedAt") or "")[:10] or None,
        "closed": job.get("isListed") is False,
    }


def _extract_smartrecruiters(body, link, today):
    j = json.loads(body)
    j["id"]                       # shape guard
    loc = j.get("location") or {}
    country = (loc.get("country") or "").upper() or None
    locations = []
    if loc.get("remote") and _is_us_country(country):
        # a bare "Remote" canonicalizes to "Remote (US)" — only emit it when
        # the API's own country field actually says US
        locations.append("Remote")
    if loc.get("city") and loc.get("region"):
        locations.append(f"{loc['city']}, {loc['region']}")
    elif loc.get("city"):
        locations.append(loc["city"])
    return {
        "locations": locations,
        "country": country,
        "date_posted": (j.get("releasedDate") or "")[:10] or None,
        "closed": False,
    }


_JSONLD_RE = re.compile(
    r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
    re.I | re.S)


def _extract_icims(body, link, today):
    for block in _JSONLD_RE.findall(body):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return _from_jsonld(item)
    return None


def _from_jsonld(item):
    places = item.get("jobLocation") or []
    if isinstance(places, dict):
        places = [places]
    locations, country = [], None
    for place in places:
        addr = (place or {}).get("address") or {}
        locality, region = addr.get("addressLocality"), addr.get("addressRegion")
        if locality and region:
            locations.append(f"{locality}, {region}")
        elif locality:
            locations.append(locality)
        country = country or addr.get("addressCountry")
    return {
        "locations": locations,
        "country": country,
        "date_posted": (item.get("datePosted") or "")[:10] or None,
        "closed": False,
    }


_EXTRACTORS = {
    "workday": _extract_workday,
    "greenhouse": _extract_greenhouse,
    "lever": _extract_lever,
    "ashby": _extract_ashby,
    "smartrecruiters": _extract_smartrecruiters,
    "icims": _extract_icims,
}


def decide(row, ext):
    """Row + extract -> list of action dicts (the spec's corrections
    contract, minus id/category/ats, which the driver adds). Pure; never
    mutates row. ext=None (ambiguous probe / unparseable payload) -> a
    single 'unknown' action and nothing else."""
    if ext is None:
        return [{"action": "unknown"}]
    if ext["closed"]:
        return [{"action": "close"}]
    actions = []
    us_locs = []
    for loc in ext["locations"]:
        for cand in _location_candidates(loc):
            canon = canonicalize_location(cand)
            if canon and _plausible_city(canon) and canon not in us_locs:
                us_locs.append(canon)
    country = ext.get("country")
    non_us_country = _names_non_us_country(country)
    if non_us_country:
        # An affirmative non-US country field beats any location parse. Two
        # ways a non-US row otherwise reads as US: a bare "Remote"
        # canonicalizes to "Remote (US)", and a foreign address can land on
        # a real US city name — Magna's 'Milton, Ontario, CA' (country
        # "Canada") canonicalizes to 'Ontario, CA', i.e. Ontario,
        # California. Clearing us_locs routes the row to delete_non_us
        # instead of relabelling it with a US location.
        us_locs = []
    if us_locs:
        stored = [p.strip() for p in row["location"].split(" / ")]
        if not any(p in us_locs for p in stored):
            actions.append({"action": "set_location",
                            "old": row["location"], "new": us_locs[0]})
    elif ext["locations"]:
        # Only the country field can authorize a delete. Location free text
        # never can: _NON_US_RE was built for strings already containing
        # "remote" (normalize.py), and against arbitrary employer text its
        # short tokens misfire — "\bon\b" matches the "on" in "on-site", so
        # "Chicago, IL (On-Site)" would read as Ontario. Anything that fails
        # to canonicalize without affirmative country evidence goes to
        # location_unresolved for manual review (Tony, 2026-08-08).
        if non_us_country:
            return [{"action": "delete_non_us",
                     "api_locations": ext["locations"], "country": country}]
        actions.append({"action": "location_unresolved",
                        "api_locations": ext["locations"]})
    api_date = ext["date_posted"]
    if api_date and api_date < _CYCLE_START:
        # Lever's createdAt and Greenhouse's first_published record when the
        # requisition was created, not when this posting went live. Evergreen
        # reqs carry dates years back — the live probe found a Summer 2027
        # Palantir role whose API date was 2016-10-06. Anything before the
        # cycle is not a posting date, so leave the row's own value alone.
        api_date = None
    if api_date and (api_date != row["date_posted"] or row.get("date_estimated")):
        actions.append({"action": "set_date",
                        "old": row["date_posted"], "new": api_date})
    return actions or [{"action": "confirm"}]
