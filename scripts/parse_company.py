"""Pure parsers for direct company-career sources."""
import json
import re
from collections import Counter

from normalize import canonicalize_location

_JSON_LD = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _job_postings(value):
    if isinstance(value, list):
        for item in value:
            yield from _job_postings(item)
    elif isinstance(value, dict):
        if value.get("@type") == "JobPosting":
            yield value
        yield from _job_postings(value.get("@graph", []))


def _locations(job: dict) -> str | None:
    raw_locations = job.get("jobLocation") or []
    if isinstance(raw_locations, dict):
        raw_locations = [raw_locations]
    locations = []
    for raw in raw_locations:
        address = raw.get("address", {}) if isinstance(raw, dict) else {}
        city, region = address.get("addressLocality"), address.get("addressRegion")
        if not city or not region:
            return None
        location = canonicalize_location(f"{city}, {region}")
        if not location:
            return None
        if location not in locations:
            locations.append(location)
    return " / ".join(locations) if locations else None


def parse_phenom_job_page(html: str, source: dict) -> list[dict]:
    """Parse one configured Phenom JobPosting page into a fetch-report row.

    The configured page URL remains the application URL. A page with no
    matching JobPosting is a parse failure; a non-matching role is a valid,
    empty source result.
    """
    job = None
    for script in _JSON_LD.findall(html):
        try:
            values = json.loads(script.strip())
        except json.JSONDecodeError:
            continue
        job = next(_job_postings(values), None)
        if job:
            break
    if not job:
        raise ValueError("no JobPosting JSON-LD found")

    role = job.get("title")
    if not role:
        raise ValueError("JobPosting is missing title")
    if not re.search(source["role_pattern"], role):
        return []
    location = _locations(job)
    if not location:
        raise ValueError("JobPosting has no confidently US location")

    posting = {
        "company": source["company"],
        "role": role,
        "location": location,
        "link": source["url"],
        "term": source["term"],
        "degree": list(source["degree"]),
        "source": source["source_entity"],
    }
    if job.get("datePosted"):
        posting["date_posted"] = job["datePosted"]
    return [posting]


_INTERN = re.compile(r"(?i)\bintern(?:ship)?\b|\bco-?op\b")
_TERM27 = re.compile(r"(?i)summer\s*[-–—,]?\s*2027|2027\s*[-–—,]?\s*summer")
_TAGS = re.compile(r"<[^>]+>")


def _text(html: str) -> str:
    """Greenhouse board content arrives HTML-encoded; flatten it for search."""
    import html as _html
    return _TAGS.sub(" ", _html.unescape(html or ""))


def _degree_from(text: str) -> list:
    degrees = []
    if re.search(r"(?i)\bbs\b|\bba\b|bachelor|undergraduate", text):
        degrees.append("BS")
    if re.search(r"(?i)\bms\b|\bmsc\b|master", text):
        degrees.append("MS")
    if re.search(r"(?i)\bphd\b|doctora", text):
        degrees.append("PhD")
    return degrees or ["BS"]


def _board_posting(source, role, location, link, text, date_posted=None):
    posting = {
        "company": source["company"],
        "role": role,
        "location": location,
        "link": link,
        "term": "Summer 2027",
        "degree": _degree_from(text),
        "source": source["source_entity"],
    }
    if date_posted:
        posting["date_posted"] = date_posted[:10]
    return posting


def _filter_board_job(source, role, loc_text, link, body, drops):
    """Shared accept/reject logic for full-board pulls (greenhouse/lever/
    ashby): intern-titled, explicit Summer-2027 evidence, US location."""
    if not role or not link:
        drops["malformed_posting"] += 1
        return None
    if not _INTERN.search(role):
        drops["role_unmatched"] += 1
        return None
    if not (_TERM27.search(role) or _TERM27.search(body)):
        drops["term_unmatched"] += 1
        return None
    location = canonicalize_location(loc_text or "")
    if not location:
        drops["non_us_location"] += 1
        return None
    return location


def parse_greenhouse_board(payload: dict, source: dict) -> tuple[list[dict], Counter]:
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("greenhouse board response is missing jobs")
    postings, drops = [], Counter()
    for job in jobs:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        body = _text(job.get("content") or "")
        loc = ((job.get("location") or {}).get("name")
               if isinstance(job.get("location"), dict) else "")
        location = _filter_board_job(source, job.get("title"), loc,
                                     job.get("absolute_url"), body, drops)
        if not location:
            continue
        postings.append(_board_posting(
            source, job["title"], location, job["absolute_url"], body,
            job.get("first_published") or job.get("updated_at")))
    return postings, drops


def parse_lever_postings(payload: list, source: dict) -> tuple[list[dict], Counter]:
    if not isinstance(payload, list):
        raise ValueError("lever postings response is not a list")
    postings, drops = [], Counter()
    for job in payload:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        body = (job.get("descriptionPlain") or "") + " " + (job.get("text") or "")
        loc = (job.get("categories") or {}).get("location") or ""
        location = _filter_board_job(source, job.get("text"), loc,
                                     job.get("hostedUrl"), body, drops)
        if not location:
            continue
        created = job.get("createdAt")
        date_posted = None
        if isinstance(created, (int, float)) and created > 0:
            from datetime import datetime, timezone
            date_posted = datetime.fromtimestamp(
                created / 1000, tz=timezone.utc).date().isoformat()
        postings.append(_board_posting(
            source, job["text"], location, job["hostedUrl"], body, date_posted))
    return postings, drops


def parse_ashby_board(payload: dict, source: dict) -> tuple[list[dict], Counter]:
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("ashby job-board response is missing jobs")
    postings, drops = [], Counter()
    for job in jobs:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        if job.get("isListed") is False:
            drops["unlisted"] += 1
            continue
        role = job.get("title")
        # employmentType Intern counts as the intern signal even when the
        # title omits the word.
        role_ok = bool(role) and (bool(_INTERN.search(role))
                                  or job.get("employmentType") == "Intern")
        if not role_ok:
            drops["role_unmatched"] += 1
            continue
        body = _text(job.get("descriptionHtml") or "") or (job.get("descriptionPlain") or "")
        if not (_TERM27.search(role) or _TERM27.search(body)):
            drops["term_unmatched"] += 1
            continue
        locs = [job.get("location") or ""] + [
            (s.get("location") or "") for s in (job.get("secondaryLocations") or [])
            if isinstance(s, dict)]
        location = next((canonicalize_location(l) for l in locs
                         if canonicalize_location(l)), None)
        link = job.get("jobUrl") or job.get("applyUrl")
        if not link:
            drops["malformed_posting"] += 1
            continue
        if not location:
            drops["non_us_location"] += 1
            continue
        postings.append(_board_posting(
            source, role, location, link, body, job.get("publishedAt")))
    return postings, drops


def parse_smartrecruiters_postings(payload: dict, source: dict) -> tuple[list[dict], Counter]:
    """Parse SmartRecruiters job details into fetch-report postings.

    The board list response carries no description, and SmartRecruiters ignores
    the filter params that would narrow it server-side (`experienceLevel=
    internship` returns the whole board), so Summer-2027 evidence exists only
    in the detail payload. The fetch layer pairs each title/US pre-filtered hit
    with its detail, and this applies the same intern/2027/US rules as the
    other board parsers.
    """
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("SmartRecruiters response is missing jobs")

    postings, drops = [], Counter()
    for job in jobs:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        sections = ((job.get("jobAd") or {}).get("sections") or {}).values()
        body = _text(" ".join(section.get("text") or "" for section in sections
                              if isinstance(section, dict)))
        place = job.get("location") or {}
        loc_text = ", ".join(part for part in (place.get("city"), place.get("region"))
                             if part)
        link = job.get("postingUrl") or job.get("applyUrl")
        location = _filter_board_job(source, job.get("name"), loc_text, link, body, drops)
        if not location:
            continue
        postings.append(_board_posting(
            source, job["name"], location, link, body, job.get("releasedDate")))
    return postings, drops


# Workday location text is tenant-authored, so it arrives in several shapes
# canonicalize_location can't read. These reshape a string into something it
# *can* judge — they never decide US-ness themselves, so a bad reshape still
# has to survive the canonicalizer to become a location.
_WD_SITE_CODE = re.compile(r"^US-([A-Z]{2})-(.+?)(?:\s*~.*)?$")
_WD_COUNTRY_STATE_CITY = re.compile(r"^United States-([^-]+)-(.+)$")
_WD_TRAILING_COUNTRY = re.compile(r",\s*United States(?: of America)?$", re.IGNORECASE)
_WD_TRAILING_SITE = re.compile(r"\s*\([^()]*\)$")


def parse_workable_jobs(payload: dict, source: dict) -> tuple[list[dict], Counter]:
    """Parse Workable job details (v2 `jobs/{shortcode}`) into fetch-report
    postings. The fetch layer pre-filters the v3 list by intern title and US
    country code, attaches each job's apply `link`, and pulls the detail,
    which is where the description (and so the Summer-2027 evidence) lives."""
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("Workable response is missing jobs")
    postings, drops = [], Counter()
    for job in jobs:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        role = job.get("title")
        body = _text((job.get("description") or "") + " " + (job.get("requirements") or ""))
        location = _filter_board_job(source, role, _workable_place(job), job.get("link"), body, drops)
        if not location:
            continue
        postings.append(_board_posting(source, role, location, job["link"], body, job.get("published")))
    return postings, drops


def _workable_place(job: dict) -> str:
    """'City, Region' for the first US location, 'Remote' for a US remote
    role, else '' (which canonicalize_location rejects)."""
    places = [job.get("location") or {}] + [l for l in job.get("locations") or [] if isinstance(l, dict)]
    for place in places:
        if place.get("countryCode") != "US":
            continue
        if place.get("city") and place.get("region"):
            return f"{place['city']}, {place['region']}"
        if job.get("remote") or job.get("workplace") == "remote":
            return "Remote"
    return ""


def _workday_place(raw: str, us: bool) -> str | None:
    """Canonicalize one Workday location string, reshaping the tenant-specific
    formats first. `us` (the posting's own country field) gates the shape
    parses so a Canadian `CA-ON-TORONTO` can never be read as a US state."""
    place = re.sub(r"\s+", " ", (raw or "").strip())
    if not place:
        return None
    if us:
        site_code = _WD_SITE_CODE.match(place)
        if site_code:
            state, rest = site_code.groups()
            # Drop only the trailing building code ("TEWKSBURY-TB2"), so a
            # hyphenated city ("WINSTON-SALEM-123") keeps its hyphen.
            city = rest.rsplit("-", 1)[0] if "-" in rest else rest
            place = f"{city.title()}, {state}"
        country_first = _WD_COUNTRY_STATE_CITY.match(place)
        if country_first:
            state, city = country_first.groups()
            place = f"{city}, {state}"
    place = _WD_TRAILING_COUNTRY.sub("", place)
    place = _WD_TRAILING_SITE.sub("", place)
    return canonicalize_location(place)


def is_intern_title(role: str) -> bool:
    """The title pre-filter the Workday fetch uses to bound how many job-detail
    requests a board costs. `parse_workday_search` re-checks it, so this is an
    optimization only — never the accept decision."""
    return bool(role) and bool(_INTERN.search(role))


def parse_workday_search(payload: dict, source: dict) -> tuple[list[dict], Counter]:
    """Parse enriched Workday job details into fetch-report postings.

    Workday's CXS *search* response carries no description and collapses a
    multi-site posting to "3 Locations", so neither Summer-2027 evidence nor a
    US location can be judged from it. The fetch layer therefore pairs each
    candidate with its job-detail payload, and this parses those fields with
    the same intern/2027/US rules the other board parsers apply.
    """
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("Workday search response is missing jobs")

    postings, drops = [], Counter()
    if payload.get("truncated"):
        drops["search_truncated"] += 1
    for job in jobs:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        role, link = job.get("title"), job.get("externalUrl")
        if not role or not link:
            drops["malformed_posting"] += 1
            continue
        if not _INTERN.search(role):
            drops["role_unmatched"] += 1
            continue
        body = _text(job.get("jobDescription") or "")
        if not (_TERM27.search(role) or _TERM27.search(body)):
            drops["term_unmatched"] += 1
            continue
        places = [job.get("location") or ""] + list(job.get("additionalLocations") or [])
        us = (job.get("country") or {}).get("descriptor") == "United States of America"
        located = [loc for loc in (_workday_place(p, us) for p in places) if loc]
        if not located:
            drops["non_us_location"] += 1
            continue
        # startDate is Workday's absolute posting date; postedOn is only a
        # relative string. A posting without one leaves date_posted unset and
        # the merge marks the new row's date estimated.
        postings.append(_board_posting(
            source, role, " / ".join(dict.fromkeys(located)), link, body,
            job.get("startDate")))
    return postings, drops


def parse_workday_cxs(payload: dict, source: dict) -> tuple[list[dict], Counter]:
    """Parse a Workday CXS search response into fetch-report postings.

    Workday exposes only a relative posting time in this response, so the
    normal merge path marks a new row's date as estimated instead of guessing
    an absolute date.
    """
    jobs = payload.get("jobPostings")
    if not isinstance(jobs, list):
        raise ValueError("Workday CXS response is missing jobPostings")

    postings, drops = [], Counter()
    for job in jobs:
        if not isinstance(job, dict):
            drops["malformed_posting"] += 1
            continue
        role = job.get("title")
        path = job.get("externalPath")
        if not isinstance(role, str) or not isinstance(path, str):
            drops["malformed_posting"] += 1
            continue
        if not re.search(source["role_pattern"], role):
            drops["role_unmatched"] += 1
            continue
        if not re.search(source["term_pattern"], role):
            drops["term_unmatched"] += 1
            continue
        location = canonicalize_location(job.get("locationsText", ""))
        if not location:
            drops["non_us_location"] += 1
            continue
        if not path.startswith("/job/"):
            drops["malformed_posting"] += 1
            continue
        postings.append({
            "company": source["company"],
            "role": role,
            "location": location,
            "link": source["url"].rstrip("/") + path,
            "term": source["term"],
            "degree": list(source["degree"]),
            "source": source["source_entity"],
        })
    return postings, drops
