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
