"""Pure parsers for direct company-career sources."""
import json
import re

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
