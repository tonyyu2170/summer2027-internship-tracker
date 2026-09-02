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
from normalize import _NON_US_RE, extends_truncated

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


# Earliest date that can plausibly be a Summer 2027 posting date. Bump this
# when the tracker moves to a new cycle.
_CYCLE_START = "2026-01-01"


_ICIMS_JOB_PATH_RE = re.compile(r"(/jobs/\d+)(?:/|$)", re.I)


def icims_redirected_away(link, final_url) -> bool:
    """True when an iCIMS probe ended up somewhere that lost the posting's
    own /jobs/<id>/ path.

    Every other family probes a JSON API keyed to one posting; iCIMS probes
    the posting HTML page, and urllib follows redirects silently. An expired
    posting that redirects to a search or listing page still carries
    JobPosting JSON-LD — for a *different* job — and `_extract_icims` takes
    the first block it finds. Without this check that becomes an
    authoritative set_location/set_date for the wrong role: the one place in
    this pipeline that writes wrong data instead of degrading to unknown."""
    if not _ICIMS_RE.match(link or ""):
        return False        # only iCIMS probes fetch a posting HTML page
    m = _ICIMS_JOB_PATH_RE.search(link)
    if not m or not final_url:
        return False
    return m.group(1).lower() not in final_url.lower()


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
     "date_posted": "YYYY-MM-DD"|None, "closed": bool, "title": str|None},
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
                "closed": True, "title": None}
    if not (200 <= status < 300) or not body:
        return None
    try:
        ext = _EXTRACTORS[ats](body, link, today)
    except Exception:
        return None
    if ext is not None:
        ext.setdefault("title", None)
    return ext


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
        "title": info.get("title"),
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
        "title": j.get("title"),
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
        "title": j.get("text"),
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
                "closed": True, "title": None}
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
        "title": job.get("title"),
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
        "title": j.get("name"),
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
        "title": item.get("title"),
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
    # Location is no longer tracked as a corrected field (Tony, 2026-08-08:
    # "as long as they are all US"), so the only thing the payload's
    # locations are still read for is keeping the listing US-only: an
    # affirmative non-US country field deletes the row. Nothing here ever
    # rewrites row["location"].
    if _names_non_us_country(ext.get("country")):
        return [{"action": "delete_non_us",
                 "api_locations": ext["locations"],
                 "country": ext.get("country")}]
    api_date = ext["date_posted"]
    if api_date and api_date < _CYCLE_START:
        # Lever's createdAt and Greenhouse's first_published record when the
        # requisition was created, not when this posting went live. Evergreen
        # reqs carry dates years back — the live probe found a Summer 2027
        # Palantir role whose API date was 2016-10-06. Anything before the
        # cycle is not a posting date, so leave the row's own value alone.
        api_date = None
    if api_date and api_date > (row.get("date_added") or api_date):
        # Workday's "Posted N Days Ago" tracks the latest re-post or edit, and
        # a date after this repo first saw the row cannot be its posting
        # date (2026-09-01 audit: 27 rows carried one). Leave the row alone.
        api_date = None
    if api_date and (api_date != row["date_posted"] or row.get("date_estimated")):
        actions.append({"action": "set_date",
                        "old": row["date_posted"], "new": api_date})
    title = (ext.get("title") or "").strip()
    if extends_truncated(row.get("role"), title):
        # A tracker cut the title ("Supply Chain Data & Analytics Inte...");
        # the board's own title is authoritative for the same posting.
        actions.append({"action": "set_role", "old": row["role"], "new": title})
    return actions or [{"action": "confirm"}]
