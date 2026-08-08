# ATS-API Verification Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pull authoritative location / posting date / open-closed state from ATS APIs for the ~517 open rows whose links sit on a covered ATS, and drop the Status + Last Verified columns from the README job tables.

**Architecture:** Corrections-report + dedicated applier, mirroring the `link_check.py`/`check_links.py` split: a pure tested core (`scripts/ats_verify.py`), an untested network driver (`scripts/check_ats.py`) that emits one auditable corrections JSON, and a tested serialized writer (`scripts/apply_ats_corrections.py`). `merge.py`, the fetch-report contract, and `run_scrape_merge.py` are untouched. Spec (read it first): `docs/superpowers/specs/2026-08-08-ats-verification-design.md`.

**Tech Stack:** Python 3.12, PyYAML, jsonschema, pytest. No new dependencies. Network I/O only in `check_ats.py`, via the existing `check_links._probe`.

**Conventions that bind every task:**
- Run tests with `python3 -m pytest tests/test_<name>.py -v` from the repo root (`conftest.py` puts `scripts/` on `sys.path`, so tests import `from ats_verify import ...` directly).
- Commit after every task. NEVER `git push` — that is exclusively Tony's action.
- `python3 scripts/check_integrity.py` before any commit that touches `data/` (only Task 8's README regeneration reads `data/`; it does not modify it, but run the check there anyway).
- This plan creates code but performs **no verification run against the live APIs** beyond Task 5's small smoke probe. The first full run is a separate, explicit operational step for Tony.

---

### Task 1: `ats_verify.api_url` — link → (family, API URL)

**Files:**
- Create: `tests/test_ats_verify.py`
- Create: `scripts/ats_verify.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ats_verify.py`:

```python
"""Tests for the pure ATS-verification core (scripts/ats_verify.py)."""
from ats_verify import api_url


def test_api_url_greenhouse_job_boards():
    assert api_url("https://job-boards.greenhouse.io/scaleai/jobs/4703343005") == (
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/scaleai/jobs/4703343005",
    )


def test_api_url_greenhouse_boards_subdomain():
    assert api_url("https://boards.greenhouse.io/acme/jobs/123") == (
        "greenhouse", "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123",
    )


def test_api_url_lever():
    assert api_url("https://jobs.lever.co/acds/01fdf41b-a835-4e00-8d01-0275677a8f08") == (
        "lever",
        "https://api.lever.co/v0/postings/acds/01fdf41b-a835-4e00-8d01-0275677a8f08",
    )


def test_api_url_lever_non_uuid_path_is_not_covered():
    assert api_url("https://jobs.lever.co/acds") is None


def test_api_url_ashby_is_the_org_board():
    # One board fetch covers every row of the org; extract() finds the row's
    # own job in it by the link's trailing UUID.
    assert api_url("https://jobs.ashbyhq.com/bild-ai/b333f0f7-0ca6-4509-8697-9303396b5364") == (
        "ashby", "https://api.ashbyhq.com/posting-api/job-board/bild-ai",
    )


def test_api_url_workday_reuses_cxs_derivation():
    ats, url = api_url(
        "https://cigna.wd5.myworkdayjobs.com/cignacareers/job/Bloomfield-CT/"
        "Actuarial-Internship---Summer-2027_26006087"
    )
    assert ats == "workday"
    assert url == (
        "https://cigna.wd5.myworkdayjobs.com/wday/cxs/cigna/cignacareers/job/"
        "Bloomfield-CT/Actuarial-Internship---Summer-2027_26006087"
    )


def test_api_url_smartrecruiters():
    assert api_url("https://jobs.smartrecruiters.com/Intuitive/744000133458290") == (
        "smartrecruiters",
        "https://api.smartrecruiters.com/v1/companies/Intuitive/postings/744000133458290",
    )


def test_api_url_icims_is_the_page_itself():
    # iCIMS serves JobPosting JSON-LD in the posting page; there is no
    # separate API URL to derive.
    link = "https://careers-cadent.icims.com/jobs/1406/enterprise-ai-intern/job"
    assert api_url(link) == ("icims", link)


def test_api_url_custom_site_is_none():
    assert api_url("https://www.janestreet.com/join-jane-street/position/123") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ats_verify'`

- [ ] **Step 3: Write the implementation**

Create `scripts/ats_verify.py`:

```python
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
_GREENHOUSE_RE = re.compile(
    r"^https?://(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.I)
_LEVER_RE = re.compile(
    rf"^https?://jobs\.lever\.co/([^/?#]+)/({_UUID})", re.I)
_ASHBY_RE = re.compile(
    rf"^https?://jobs\.ashbyhq\.com/([^/?#]+)/({_UUID})", re.I)
_SMARTRECRUITERS_RE = re.compile(
    r"^https?://jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.I)
_ICIMS_RE = re.compile(r"^https?://[^/]+\.icims\.com/jobs/\d+/", re.I)


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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_ats_verify.py scripts/ats_verify.py
git commit -m "feat: derive authoritative ATS API URLs from application links"
```

---

### Task 2: `ats_verify.extract` — Workday, Greenhouse, Lever payloads

**Files:**
- Modify: `tests/test_ats_verify.py` (append)
- Modify: `scripts/ats_verify.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ats_verify.py` (and add the imports at the top of the file: `import json`, `from datetime import date`, and extend the existing import to `from ats_verify import api_url, extract`):

```python
TODAY = date(2026, 8, 8)


def _workday_body(**info):
    base = {
        "location": "Bloomfield, CT", "additionalLocations": [],
        "postedOn": "Posted 3 Days Ago",
        "country": {"descriptor": "United States of America"},
    }
    base.update(info)
    return json.dumps({"jobPostingInfo": base})


def test_extract_404_means_closed_for_posting_scoped_families():
    ext = extract("workday", 404, None, today=TODAY)
    assert ext == {"locations": [], "country": None, "date_posted": None,
                   "closed": True}


def test_extract_ambiguous_status_is_none():
    assert extract("workday", 429, "", today=TODAY) is None
    assert extract("greenhouse", 500, "{}", today=TODAY) is None
    assert extract("lever", 0, None, today=TODAY) is None


def test_extract_workday_locations_and_relative_date():
    body = _workday_body(additionalLocations=["Austin, TX"])
    ext = extract("workday", 200, body, today=TODAY)
    assert ext["locations"] == ["Bloomfield, CT", "Austin, TX"]
    assert ext["date_posted"] == "2026-08-05"
    assert ext["country"] == "United States of America"
    assert ext["closed"] is False


def test_extract_workday_posted_today_and_yesterday():
    assert extract("workday", 200, _workday_body(postedOn="Posted Today"),
                   today=TODAY)["date_posted"] == "2026-08-08"
    assert extract("workday", 200, _workday_body(postedOn="Posted Yesterday"),
                   today=TODAY)["date_posted"] == "2026-08-07"


def test_extract_workday_30_plus_days_is_too_coarse():
    assert extract("workday", 200, _workday_body(postedOn="Posted 30+ Days Ago"),
                   today=TODAY)["date_posted"] is None


def test_extract_workday_malformed_body_is_none():
    assert extract("workday", 200, "<html>Not JSON</html>", today=TODAY) is None


def test_extract_greenhouse_multi_location_and_first_published():
    body = json.dumps({
        "id": 4703343005,
        "location": {"name": "San Francisco, CA; New York, NY"},
        "first_published": "2026-07-15T10:23:00-04:00",
    })
    ext = extract("greenhouse", 200, body, today=TODAY)
    assert ext["locations"] == ["San Francisco, CA", "New York, NY"]
    assert ext["date_posted"] == "2026-07-15"
    assert ext["closed"] is False


def test_extract_greenhouse_payload_without_id_is_drift():
    assert extract("greenhouse", 200,
                   json.dumps({"location": {"name": "NYC"}}), today=TODAY) is None


def test_extract_lever_all_locations_country_and_created_at():
    body = json.dumps({
        "id": "01fdf41b-a835-4e00-8d01-0275677a8f08", "country": "US",
        "categories": {"location": "New York",
                       "allLocations": ["New York", "Austin"]},
        "createdAt": 1784073600000,   # 2026-07-15T00:00:00Z in epoch millis
    })
    ext = extract("lever", 200, body, today=TODAY)
    assert ext["locations"] == ["New York", "Austin"]
    assert ext["country"] == "US"
    assert ext["date_posted"] == "2026-07-15"


def test_extract_lever_falls_back_to_single_location():
    body = json.dumps({"id": "x", "categories": {"location": "Austin, TX"}})
    assert extract("lever", 200, body, today=TODAY)["locations"] == ["Austin, TX"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: the new tests FAIL — `ImportError: cannot import name 'extract'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/ats_verify.py`:

```python
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


_EXTRACTORS = {
    "workday": _extract_workday,
    "greenhouse": _extract_greenhouse,
    "lever": _extract_lever,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: 19 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_ats_verify.py scripts/ats_verify.py
git commit -m "feat: extract authoritative location/date from Workday, Greenhouse, Lever payloads"
```

---

### Task 3: `ats_verify.extract` — Ashby, SmartRecruiters, iCIMS payloads

**Files:**
- Modify: `tests/test_ats_verify.py` (append)
- Modify: `scripts/ats_verify.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ats_verify.py`:

```python
ASHBY_LINK = "https://jobs.ashbyhq.com/bild-ai/b333f0f7-0ca6-4509-8697-9303396b5364"


def _ashby_board(jobs):
    return json.dumps({"jobs": jobs})


def test_extract_ashby_finds_job_by_link_uuid():
    jobs = [
        {"id": "aaaaaaaa-0000-0000-0000-000000000000",
         "jobUrl": "https://jobs.ashbyhq.com/bild-ai/aaaaaaaa-0000-0000-0000-000000000000",
         "location": "Remote"},
        {"id": "b333f0f7-0ca6-4509-8697-9303396b5364",
         "location": "San Francisco",
         "secondaryLocations": [{"location": "New York"}],
         "publishedAt": "2026-07-01T00:00:00.000Z", "isListed": True,
         "address": {"postalAddress": {
             "addressLocality": "San Francisco",
             "addressRegion": "California",
             "addressCountry": "United States"}}},
    ]
    ext = extract("ashby", 200, _ashby_board(jobs), link=ASHBY_LINK, today=TODAY)
    # the address-derived "Locality, Region" comes first: it's the only form
    # canonicalize_location can resolve when the display location is city-only
    assert ext["locations"][0] == "San Francisco, California"
    assert "New York" in ext["locations"]
    assert ext["date_posted"] == "2026-07-01"
    assert ext["country"] == "United States"
    assert ext["closed"] is False


def test_extract_ashby_job_absent_from_own_board_is_closed():
    # The org's own board API no longer serving the posting id is that
    # board's authoritative "gone" — not scrape disappearance.
    jobs = [{"id": "aaaaaaaa-0000-0000-0000-000000000000"}]
    ext = extract("ashby", 200, _ashby_board(jobs), link=ASHBY_LINK, today=TODAY)
    assert ext["closed"] is True


def test_extract_ashby_unlisted_job_is_closed():
    jobs = [{"id": "b333f0f7-0ca6-4509-8697-9303396b5364",
             "isListed": False, "location": "SF"}]
    ext = extract("ashby", 200, _ashby_board(jobs), link=ASHBY_LINK, today=TODAY)
    assert ext["closed"] is True


def test_extract_ashby_board_404_is_unknown_not_closed():
    # covered in extract()'s status handling; pinned here as a regression test
    assert extract("ashby", 404, None, link=ASHBY_LINK, today=TODAY) is None


def test_extract_smartrecruiters_city_region_country_and_date():
    body = json.dumps({
        "id": "744000133458290", "releasedDate": "2026-06-20T08:00:00.000Z",
        "location": {"city": "Sunnyvale", "region": "CA", "country": "us",
                     "remote": False},
    })
    ext = extract("smartrecruiters", 200, body, today=TODAY)
    assert ext["locations"] == ["Sunnyvale, CA"]
    assert ext["country"] == "US"
    assert ext["date_posted"] == "2026-06-20"


def test_extract_smartrecruiters_remote_us():
    body = json.dumps({"id": "1", "location": {"country": "us", "remote": True}})
    assert extract("smartrecruiters", 200, body, today=TODAY)["locations"] == ["Remote"]


def test_extract_smartrecruiters_remote_non_us_is_not_emitted_as_remote():
    # a bare "Remote" would canonicalize to "Remote (US)" downstream — only
    # emit it when the API's own country field says US
    body = json.dumps({"id": "1", "location": {"country": "gb", "remote": True}})
    ext = extract("smartrecruiters", 200, body, today=TODAY)
    assert "Remote" not in ext["locations"]
    assert ext["country"] == "GB"


def test_extract_icims_jsonld():
    page = ('<html><head><script type="application/ld+json">'
            + json.dumps({"@type": "JobPosting", "datePosted": "2026-05-10",
                          "jobLocation": {"@type": "Place", "address": {
                              "addressLocality": "Philadelphia",
                              "addressRegion": "PA",
                              "addressCountry": "US"}}})
            + "</script></head></html>")
    ext = extract("icims", 200, page, today=TODAY)
    assert ext["locations"] == ["Philadelphia, PA"]
    assert ext["date_posted"] == "2026-05-10"
    assert ext["country"] == "US"


def test_extract_icims_multiple_job_locations():
    page = ('<script type="application/ld+json">'
            + json.dumps({"@type": "JobPosting", "jobLocation": [
                {"address": {"addressLocality": "Atlanta", "addressRegion": "GA"}},
                {"address": {"addressLocality": "Dallas", "addressRegion": "TX"}}]})
            + "</script>")
    ext = extract("icims", 200, page, today=TODAY)
    assert ext["locations"] == ["Atlanta, GA", "Dallas, TX"]


def test_extract_icims_page_without_jsonld_is_none():
    assert extract("icims", 200, "<html>SPA shell, no JSON-LD</html>",
                   today=TODAY) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: the new tests FAIL (missing `_EXTRACTORS` entries make `extract` raise `KeyError` internally and return None; assertions on the None result fail)

- [ ] **Step 3: Write the implementation**

In `scripts/ats_verify.py`, add these definitions above `_EXTRACTORS` (put `_US_COUNTRY`/`_is_us_country` right after the module-level regexes — Task 4's `decide` uses them too):

```python
_US_COUNTRY = {"us", "usa", "united states", "united states of america"}


def _is_us_country(country) -> bool:
    return (country or "").strip().lower() in _US_COUNTRY


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
```

Then extend `_EXTRACTORS` to:

```python
_EXTRACTORS = {
    "workday": _extract_workday,
    "greenhouse": _extract_greenhouse,
    "lever": _extract_lever,
    "ashby": _extract_ashby,
    "smartrecruiters": _extract_smartrecruiters,
    "icims": _extract_icims,
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: 29 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_ats_verify.py scripts/ats_verify.py
git commit -m "feat: extract authoritative data from Ashby, SmartRecruiters, iCIMS payloads"
```

---

### Task 4: `ats_verify.decide` — extract → correction actions

**Files:**
- Modify: `tests/test_ats_verify.py` (append)
- Modify: `scripts/ats_verify.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ats_verify.py` (extend the top import to `from ats_verify import api_url, extract, decide`):

```python
def _row(**kw):
    base = {
        "id": "r1", "company": "Acme", "role": "SWE Intern",
        "location": "New York, NY",
        "link": "https://jobs.lever.co/acme/01fdf41b-a835-4e00-8d01-0275677a8f08",
        "date_posted": "2026-07-01", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["s"], "date_added": "2026-07-01",
        "last_verified": "2026-07-01", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def _ext(**kw):
    base = {"locations": [], "country": None, "date_posted": None, "closed": False}
    base.update(kw)
    return base


def test_decide_none_ext_is_unknown():
    assert decide(_row(), None) == [{"action": "unknown"}]


def test_decide_closed_wins_over_everything():
    ext = _ext(closed=True, locations=["London"], date_posted="2026-01-01")
    assert decide(_row(), ext) == [{"action": "close"}]


def test_decide_confirms_when_stored_location_matches_any_api_us_location():
    ext = _ext(locations=["Austin, TX", "New York, NY"], date_posted="2026-07-01")
    assert decide(_row(), ext) == [{"action": "confirm"}]


def test_decide_sets_location_to_primary_us_location_on_mismatch():
    ext = _ext(locations=["Redmond, WA", "Austin, TX"])
    actions = decide(_row(location="Washington, DC"), ext)
    assert {"action": "set_location", "old": "Washington, DC",
            "new": "Redmond, WA"} in actions


def test_decide_multi_part_stored_location_confirms_on_any_member():
    ext = _ext(locations=["Austin, TX"])
    assert decide(_row(location="New York, NY / Austin, TX"), ext) == [
        {"action": "confirm"}]


def test_decide_deletes_on_non_us_country_field():
    ext = _ext(locations=["Toronto"], country="Canada")
    actions = decide(_row(), ext)
    assert actions[0]["action"] == "delete_non_us"
    assert actions[0]["api_locations"] == ["Toronto"]
    assert actions[0]["country"] == "Canada"


def test_decide_non_us_location_text_alone_never_deletes():
    # Only the country field authorizes a delete. Non-US-looking location
    # text with no country evidence is unresolved, not deleted.
    actions = decide(_row(), _ext(locations=["London, UK"]))
    assert actions[0]["action"] == "location_unresolved"
    assert all(a["action"] != "delete_non_us" for a in actions)


@pytest.mark.parametrize("location", [
    "Chicago, IL (On-Site)",          # \bon\b matched "on" in "on-site"
    "Remote / On-site",
    "San Francisco, CA (Hybrid - 3 days on-site)",
])
def test_decide_on_site_free_text_is_not_read_as_ontario(location):
    actions = decide(_row(), _ext(locations=[location]))
    assert all(a["action"] != "delete_non_us" for a in actions)


@pytest.mark.parametrize("country", ["U.S.", "U.S.A.", "America",
                                     "United States (USA)"])
def test_decide_unrecognized_us_spelling_never_deletes(country):
    # Not being in the US allowlist is not affirmative non-US evidence.
    actions = decide(_row(), _ext(locations=["Somewhereville"], country=country))
    assert all(a["action"] != "delete_non_us" for a in actions)


def test_decide_unrecognized_country_is_unresolved_not_deleted():
    # Under-matching is the safe direction: a country the pattern doesn't
    # know yields manual review rather than a silent delete.
    actions = decide(_row(), _ext(locations=["Munich"], country="Germany"))
    assert actions[0]["action"] == "location_unresolved"


def test_decide_ambiguous_city_only_is_unresolved_never_deleted():
    # "New York" without a state canonicalizes to None — not confidently US,
    # which is NOT the same as non-US. Spec decision 3 (amended).
    actions = decide(_row(), _ext(locations=["New York"]))
    assert actions[0]["action"] == "location_unresolved"
    assert actions[0]["api_locations"] == ["New York"]
    assert all(a["action"] != "delete_non_us" for a in actions)


def test_decide_bare_remote_with_non_us_country_deletes():
    # "Remote" canonicalizes to "Remote (US)", but the API's own country
    # field wins when remote is the only US-looking signal
    ext = _ext(locations=["Remote"], country="Canada")
    assert decide(_row(), ext)[0]["action"] == "delete_non_us"


def test_decide_remote_us_row_confirms_against_bare_remote():
    ext = _ext(locations=["Remote"], country="US")
    assert decide(_row(location="Remote (US)"), ext) == [{"action": "confirm"}]


def test_decide_sets_differing_date():
    ext = _ext(locations=["New York, NY"], date_posted="2026-06-15")
    assert {"action": "set_date", "old": "2026-07-01",
            "new": "2026-06-15"} in decide(_row(), ext)


def test_decide_confirms_estimated_date_by_reissuing_it():
    # equal date but row is flagged estimated: emit set_date so the applier
    # clears date_estimated — the date is now confirmed, not guessed
    ext = _ext(locations=["New York, NY"], date_posted="2026-07-01")
    actions = decide(_row(date_estimated=True), ext)
    assert {"action": "set_date", "old": "2026-07-01",
            "new": "2026-07-01"} in actions


def test_decide_no_locations_in_payload_leaves_location_alone():
    assert decide(_row(), _ext(date_posted="2026-07-01")) == [{"action": "confirm"}]


def test_decide_never_mutates_the_row():
    row = _row()
    before = dict(row)
    decide(row, _ext(locations=["Redmond, WA"], date_posted="2026-01-01"))
    assert row == before
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: the new tests FAIL — `ImportError: cannot import name 'decide'`

- [ ] **Step 3: Write the implementation**

Append to `scripts/ats_verify.py`:

```python
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
        canon = canonicalize_location(loc or "")
        if canon and canon not in us_locs:
            us_locs.append(canon)
    country = ext.get("country")
    non_us_country = _names_non_us_country(country)
    if us_locs == ["Remote (US)"] and non_us_country:
        # a bare "Remote" canonicalizes US, but the API's own country field
        # says otherwise — the country wins when remote is the only signal
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
    if api_date and (api_date != row["date_posted"] or row.get("date_estimated")):
        actions.append({"action": "set_date",
                        "old": row["date_posted"], "new": api_date})
    return actions or [{"action": "confirm"}]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_ats_verify.py -v`
Expected: 43 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_ats_verify.py scripts/ats_verify.py
git commit -m "feat: decide corrections from authoritative ATS extracts"
```

---

### Task 5: `check_ats.py` — network driver

**Files:**
- Create: `scripts/check_ats.py`

Untested by the suite, like `check_links.py` — network drivers stay outside the tested core (docs/SCRAPING.md discipline). Verified by a small live smoke probe instead.

- [ ] **Step 1: Write the driver**

Create `scripts/check_ats.py`:

```python
"""Network driver for ats_verify.py's pure verification logic. Untested,
like check_links.py (see docs/SCRAPING.md).

Probes every open row whose link sits on an API-covered ATS and writes ONE
corrections JSON to scratch/ats_corrections.json — the audit record of
every proposed change. Never writes data/*.yaml or README.md; run
scripts/apply_ats_corrections.py on the file afterward to apply. Never run
concurrently with run_scrape_merge.py (single-writer discipline).

Rows of the same Ashby org each re-fetch the same board URL; at the
current row counts that redundancy is cheaper than caching across threads.

Usage: python3 scripts/check_ats.py [category ...]   # default: all"""
import json
import sys
import yaml
from pathlib import Path
from datetime import date
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from check_links import _probe
from ats_verify import api_url, extract, decide

ROOT = Path(__file__).resolve().parent.parent


def _verify_row(category, row, today):
    ats, url = api_url(row["link"])
    status, _final, body = _probe(url, want_body=True)
    ext = extract(ats, status, body, link=row["link"], today=today)
    return [
        {"id": row["id"], "category": category, "ats": ats, **action}
        for action in decide(row, ext)
    ]


def run(data_dir=None, out_path=None, workers=10, categories=None):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    out_path = (Path(out_path) if out_path
                else ROOT / "scratch" / "ats_corrections.json")
    today = date.today()

    targets = []
    for path in sorted(data_dir.glob("*.yaml")):
        if categories and path.stem not in categories:
            continue
        for row in yaml.safe_load(path.read_text()) or []:
            if row.get("status") == "open" and api_url(row.get("link") or ""):
                targets.append((path.stem, row))

    actions = []
    counts = Counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_verify_row, cat, row, today): (cat, row)
                   for cat, row in targets}
        for fut in as_completed(futures):
            cat, row = futures[fut]
            try:
                row_actions = fut.result()
            except Exception as e:      # one bad row must not kill the run
                print(f"    warn: [{row.get('id')}] probe failed: {e}")
                row_actions = [{"id": row["id"], "category": cat,
                                "ats": api_url(row["link"])[0],
                                "action": "unknown"}]
            actions.extend(row_actions)
            for a in row_actions:
                counts[(a["ats"], a["action"])] += 1

    actions.sort(key=lambda a: (a["category"], a["id"], a["action"]))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated": today.isoformat(), "actions": actions}, indent=2))

    for ats in sorted({ats for ats, _ in counts}):
        parts = ", ".join(f"{act}={n}"
                          for (a, act), n in sorted(counts.items()) if a == ats)
        print(f"[{ats}] {parts}")
    for a in actions:
        if a["action"] == "delete_non_us":
            print(f"    would DELETE (non-US): [{a['id']}] "
                  f"api_locations={a.get('api_locations')} "
                  f"country={a.get('country')}")
        elif a["action"] == "close":
            print(f"    would close: [{a['id']}]")
    print(f"{len(targets)} row(s) probed -> {out_path}")


if __name__ == "__main__":
    run(categories=set(sys.argv[1:]) or None)
```

- [ ] **Step 2: Live smoke probe on the smallest category**

Run: `python3 scripts/check_ats.py actuarial`
Expected: a per-family summary line (actuarial's API rows are Workday), e.g. `[workday] confirm=N, set_date=M`, then `N row(s) probed -> .../scratch/ats_corrections.json`. Inspect: `python3 -m json.tool scratch/ats_corrections.json | head -40` — every entry has `id`, `category`, `ats`, `action`. If every action is `unknown`, STOP and debug (probe headers or a payload-shape assumption) before continuing.

- [ ] **Step 3: Remove the smoke output**

Run: `rm scratch/ats_corrections.json`
(It came from a partial-category run; the real run is Tony's explicit operational step later.)

- [ ] **Step 4: Verify the full suite still passes**

Run: `python3 -m pytest tests/ -v`
Expected: all pass (the driver imports cleanly; nothing else changed)

- [ ] **Step 5: Commit**

```bash
git add scripts/check_ats.py
git commit -m "feat: ATS verification network driver emitting corrections JSON"
```

---

### Task 6: `apply_ats_corrections.apply_corrections` — pure apply

**Files:**
- Create: `tests/test_apply_ats_corrections.py`
- Create: `scripts/apply_ats_corrections.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_apply_ats_corrections.py`:

```python
"""Tests for the corrections applier (scripts/apply_ats_corrections.py)."""
import json
import yaml
import pytest
from apply_ats_corrections import apply_corrections, run

TODAY = "2026-08-08"


def _row(**kw):
    base = {
        "id": "r1", "company": "Acme", "role": "SWE Intern",
        "location": "New York, NY", "link": "https://x.com/1",
        "date_posted": "2026-07-01", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["s"], "date_added": "2026-07-01",
        "last_verified": "2026-07-01", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def _action(**kw):
    base = {"id": "r1", "category": "swe", "ats": "lever", "action": "confirm"}
    base.update(kw)
    return base


def test_confirm_stamps_last_verified():
    new, summary = apply_corrections({"swe": [_row()]}, [_action()], TODAY)
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["confirmed"] == ["r1"]


def test_unknown_does_not_stamp():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="unknown")], TODAY)
    assert new["swe"][0]["last_verified"] == "2026-07-01"
    assert summary["unknown"] == ["r1"]


def test_set_location():
    new, summary = apply_corrections(
        {"swe": [_row()]},
        [_action(action="set_location", old="New York, NY", new="Redmond, WA")],
        TODAY)
    assert new["swe"][0]["location"] == "Redmond, WA"
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["location_fixed"] == ["r1"]


def test_set_date_clears_estimated():
    new, summary = apply_corrections(
        {"swe": [_row(date_estimated=True)]},
        [_action(action="set_date", old="2026-07-01", new="2026-06-15")],
        TODAY)
    assert new["swe"][0]["date_posted"] == "2026-06-15"
    assert new["swe"][0]["date_estimated"] is False
    assert summary["date_fixed"] == ["r1"]


def test_close():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="close")], TODAY)
    assert new["swe"][0]["status"] == "closed"
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["closed"] == ["r1"]


def test_delete_removes_row_and_clears_dup_pointers_across_categories():
    data = {
        "swe": [_row(id="gone")],
        "quant": [_row(id="stays", link="https://x.com/2",
                       possible_duplicate_of="gone")],
    }
    new, summary = apply_corrections(
        data, [_action(id="gone", action="delete_non_us",
                       api_locations=["Toronto"], country="Canada")], TODAY)
    assert new["swe"] == []
    assert new["quant"][0]["possible_duplicate_of"] is None
    assert summary["deleted"] == ["gone"]


def test_location_unresolved_stamps_but_changes_nothing_else():
    new, summary = apply_corrections(
        {"swe": [_row()]},
        [_action(action="location_unresolved", api_locations=["New York"])],
        TODAY)
    assert new["swe"][0]["location"] == "New York, NY"
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["unresolved"] == ["r1"]


def test_correction_for_unknown_row_id_is_skipped():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(id="ghost")], TODAY)
    assert summary["skipped"] == ["ghost"]
    assert new["swe"][0]["last_verified"] == "2026-07-01"


def test_multiple_actions_for_one_row_all_apply():
    actions = [
        _action(action="set_location", old="New York, NY", new="Austin, TX"),
        _action(action="set_date", old="2026-07-01", new="2026-06-15"),
    ]
    new, _ = apply_corrections({"swe": [_row()]}, actions, TODAY)
    assert new["swe"][0]["location"] == "Austin, TX"
    assert new["swe"][0]["date_posted"] == "2026-06-15"


def test_apply_never_mutates_input():
    data = {"swe": [_row()]}
    snapshot = {"swe": [dict(data["swe"][0])]}
    apply_corrections(
        data,
        [_action(action="set_location", old="New York, NY", new="Austin, TX")],
        TODAY)
    assert data == snapshot
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_apply_ats_corrections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'apply_ats_corrections'`

- [ ] **Step 3: Write the implementation**

Create `scripts/apply_ats_corrections.py` (the `run` entrypoint the test file already imports is implemented in Task 7 — a stub keeps the import satisfied here):

```python
"""Apply an ats_corrections.json (from check_ats.py) to data/*.yaml — the
single serialized writer of a verification run. Applies
set_location/set_date/close/delete_non_us, stamps last_verified on every
row whose probe resolved, clears possible_duplicate_of pointers into
deleted rows, validates every touched row against ROW_SCHEMA before
anything is written (any failure aborts the whole apply), rewrites the
category YAML, and re-renders README.md. Never runs git.

Usage: python3 scripts/apply_ats_corrections.py [scratch/ats_corrections.json]
"""
import copy
import json
import sys
import yaml
from pathlib import Path
from datetime import date

from schema import validate_row
from generate_readme import render, ROOT, CATEGORIES

# actions that prove the posting was authoritatively seen this run
_RESOLVED = {"confirm", "set_location", "set_date", "close",
             "location_unresolved"}


def apply_corrections(rows_by_category, actions, today):
    """Pure. Returns (new_rows_by_category, summary); never mutates input.
    summary maps outcome kinds to sorted row-id lists; 'skipped' holds ids
    from the corrections file that no longer exist in the data."""
    # deepcopy, not dict(): a shallow copy shares the nested `degree` and
    # `sources` lists with the caller, so the never-mutates guarantee would
    # hold only as long as no action touches a nested value.
    rows_by_category = copy.deepcopy(rows_by_category)
    index = {}
    for rows in rows_by_category.values():
        for row in rows:
            if row.get("id"):
                index[row["id"]] = row
    summary = {k: [] for k in (
        "confirmed", "location_fixed", "date_fixed", "closed", "deleted",
        "unresolved", "unknown", "skipped", "unrecognized_action")}
    deleted, verified = set(), set()
    for a in actions:
        rid, act = a.get("id"), a.get("action")
        row = index.get(rid)
        if row is None:
            summary["skipped"].append(rid)
            continue
        if act in _RESOLVED:
            verified.add(rid)
        if act == "confirm":
            summary["confirmed"].append(rid)
        elif act == "set_location":
            row["location"] = a["new"]
            summary["location_fixed"].append(rid)
        elif act == "set_date":
            row["date_posted"] = a["new"]
            row["date_estimated"] = False
            summary["date_fixed"].append(rid)
        elif act == "close":
            row["status"] = "closed"
            summary["closed"].append(rid)
        elif act == "delete_non_us":
            deleted.add(rid)
            summary["deleted"].append(rid)
        elif act == "location_unresolved":
            summary["unresolved"].append(rid)
        elif act == "unknown":
            summary["unknown"].append(rid)
        else:
            # An action kind we don't implement, on a row that DOES exist —
            # a typo or a renamed action, not a stale id. Kept separate from
            # "skipped" so it can't be reported as a missing row.
            summary["unrecognized_action"].append(rid)
    new = {}
    for cat, rows in rows_by_category.items():
        kept = []
        for row in rows:
            if row.get("id") in deleted:
                continue
            if row.get("id") in verified:
                row["last_verified"] = today
            if row.get("possible_duplicate_of") in deleted:
                row["possible_duplicate_of"] = None
            kept.append(row)
        new[cat] = kept
    for ids in summary.values():
        ids.sort(key=str)
    return new, summary


def run(corrections_path, data_dir=None, readme_path=None):
    raise NotImplementedError   # Task 7
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_apply_ats_corrections.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_apply_ats_corrections.py scripts/apply_ats_corrections.py
git commit -m "feat: pure corrections applier with last_verified stamping and safe deletes"
```

---

### Task 7: `apply_ats_corrections.run` — validate, write, render

**Files:**
- Modify: `tests/test_apply_ats_corrections.py` (append)
- Modify: `scripts/apply_ats_corrections.py` (replace the `run` stub)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_apply_ats_corrections.py`:

```python
def _setup_tree(tmp_path, rows_swe):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stems = ("swe", "quant", "data_science", "ai_ml", "hardware", "actuarial")
    for stem in stems:
        rows = rows_swe if stem == "swe" else []
        (data_dir / f"{stem}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    return data_dir


def _write_corrections(tmp_path, actions):
    p = tmp_path / "ats_corrections.json"
    p.write_text(json.dumps({"generated": TODAY, "actions": actions}))
    return p


def test_run_applies_writes_yaml_and_renders_readme(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    corrections = _write_corrections(tmp_path, [
        _action(action="set_location", old="New York, NY", new="Redmond, WA"),
    ])
    readme = tmp_path / "README.md"
    summary = run(corrections, data_dir=data_dir, readme_path=readme)
    assert summary["location_fixed"] == ["r1"]
    on_disk = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert on_disk[0]["location"] == "Redmond, WA"
    assert readme.exists()
    assert "Redmond, WA" in readme.read_text()


def test_run_aborts_on_schema_failure_writing_nothing(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    before = (data_dir / "swe.yaml").read_text()
    corrections = _write_corrections(tmp_path, [
        # empty location violates ROW_SCHEMA minLength — deterministic
        # corrections producing this means a bug, so the whole apply aborts
        _action(action="set_location", old="New York, NY", new=""),
    ])
    readme = tmp_path / "README.md"
    with pytest.raises(SystemExit):
        run(corrections, data_dir=data_dir, readme_path=readme)
    assert (data_dir / "swe.yaml").read_text() == before
    assert not readme.exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_apply_ats_corrections.py -v`
Expected: the two new tests FAIL with `NotImplementedError`

- [ ] **Step 3: Write the implementation**

In `scripts/apply_ats_corrections.py`, replace the `run` stub with:

```python
def run(corrections_path, data_dir=None, readme_path=None):
    corrections_path = Path(corrections_path)
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    doc = json.loads(corrections_path.read_text())
    rows_by_category = {}
    for stem, _title, _is_quant in CATEGORIES:
        path = data_dir / f"{stem}.yaml"
        rows_by_category[stem] = (
            (yaml.safe_load(path.read_text()) or []) if path.exists() else [])

    # Corrections are matched to rows by id alone, so a duplicate id would
    # apply one row's correction to another row entirely: a delete would
    # remove every row sharing the id (reporting one), and a set_location
    # would land on whichever row loaded last. Duplicate ids are a known,
    # unfixed upstream bug in merge.py's id hash, and run_scrape_merge
    # deliberately writes them to disk anyway rather than lose a listing.
    # Refuse to touch a dataset in that state instead of guessing.
    seen, dupes = {}, set()
    for cat, rows in rows_by_category.items():
        for row in rows:
            rid = row.get("id")
            if rid is None:
                continue
            if rid in seen:
                dupes.add(rid)
            seen[rid] = cat
    colliding = sorted(dupes.intersection(
        {a.get("id") for a in doc["actions"]}))
    if colliding:
        for rid in colliding:
            print(f"DUPLICATE ID: {rid!r} matches more than one row")
        raise SystemExit(
            f"{len(colliding)} corrections id(s) match multiple rows; "
            f"nothing written. Resolve the duplicate ids first.")

    today = date.today().isoformat()
    new_rows, summary = apply_corrections(rows_by_category, doc["actions"], today)

    # Validate only the rows this run touched: pre-existing malformed
    # hand-edits are tolerated exactly as run_scrape_merge does.
    touched = set()
    for kind in ("confirmed", "location_fixed", "date_fixed", "closed",
                 "unresolved"):
        touched.update(summary[kind])
    errors = []
    for cat, rows in new_rows.items():
        for row in rows:
            if row.get("id") in touched:
                for err in validate_row(row):
                    errors.append(f"[{cat}] {row['id']}: {err}")
    if errors:
        for e in errors:
            print(f"SCHEMA: {e}")
        raise SystemExit(
            f"{len(errors)} schema error(s) on corrected rows; nothing written.")

    for cat, rows in new_rows.items():
        (data_dir / f"{cat}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    render(data_dir, readme_path)

    # Report deletes from the summary, not from the raw actions: an action
    # naming a row id that isn't in the data deletes nothing, and announcing
    # it would claim a destructive act that never happened.
    detail = {a.get("id"): a for a in doc["actions"]
              if a.get("action") == "delete_non_us"}
    for rid in summary["deleted"]:
        a = detail.get(rid, {})
        print(f"    DELETED (non-US): [{rid}] "
              f"api_locations={a.get('api_locations')} "
              f"country={a.get('country')}")
    for rid in summary["closed"]:
        print(f"    closed: [{rid}]")
    for rid in summary["skipped"]:
        print(f"    warn: skipped correction for unknown row id {rid!r}")
    for rid in summary["unrecognized_action"]:
        print(f"    warn: unrecognized action kind for existing row {rid!r}")
    print(", ".join(f"{k}={len(v)}" for k, v in summary.items()))
    return summary


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1
        else ROOT / "scratch" / "ats_corrections.json")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_apply_ats_corrections.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_apply_ats_corrections.py scripts/apply_ats_corrections.py
git commit -m "feat: serialized applier entrypoint with schema abort gate"
```

---

### Task 8: README — drop Status + Last Verified columns, render open rows only

**Files:**
- Modify: `scripts/generate_readme.py`
- Modify: `tests/test_generate_readme.py`
- Regenerate: `README.md`

- [ ] **Step 1: Update the tests**

In `tests/test_generate_readme.py`:

**(a)** Replace `test_closed_row_renders_lock` (lines 75–79) entirely with:

```python
def test_closed_rows_are_not_rendered(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(status="closed")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "Jane Street" not in text
    swe_section = text.split("## Software Engineering")[1].split("## ")[0]
    assert "_No open roles._" in swe_section


def test_mixed_category_renders_only_open_rows(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(id="a", company="OpenCo"),
        _row(id="b", company="ClosedCo", link="https://x.com/k", status="closed"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "OpenCo" in text
    assert "ClosedCo" not in text


def test_status_and_last_verified_columns_dropped(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row()])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "| Company | Role | Location | Link | Date Posted | Term | Degree |" in text
    assert "Status |" not in text
    assert "Last Verified" not in text


def test_dup_marker_renders_in_role_cell(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(possible_duplicate_of="other-row-id")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "Quant Trading Intern ⚠️dup?(other-row-id)" in text
```

**(b)** In `test_estimated_date_is_marked_without_changing_sort_order`, change the final assertion `assert "Last Verified" in text` to `assert "Last Verified" not in text`.

**(c)** In `test_pipe_newline_and_paren_in_free_text_dont_corrupt_table`, change `assert len(cells) == 9` to `assert len(cells) == 7`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_generate_readme.py -v`
Expected: the new/changed tests FAIL (Status column still rendered, closed row still present, 9 cells)

- [ ] **Step 3: Update `scripts/generate_readme.py`**

**(a)** Delete the `_status_cell` function (lines 30–34) entirely.

**(b)** Replace `_row_cells` with:

```python
def _row_cells(row: dict) -> str:
    # `track` stays in the data model; it's just not rendered — the role
    # title already carries it (Tony, 2026-08-08).
    role = _escape_cell(row["role"])
    if row.get("possible_duplicate_of"):
        role += f" ⚠️dup?({row['possible_duplicate_of']})"
    cells = [_escape_cell(row["company"]), role]
    cells += [
        _escape_cell(row["location"]),
        f"[Apply](<{row['link']}>)",
        _escape_cell(("~" if row.get("date_estimated") else "") + row["date_posted"]),
        _escape_cell(row["term"]),
        _escape_cell("/".join(row["degree"])),
    ]
    return "| " + " | ".join(cells) + " |"
```

**(c)** In `_table`, replace the `header` list with:

```python
    header = ["Company", "Role", "Location", "Link", "Date Posted", "Term",
              "Degree"]
```

**(d)** In `render`, replace the job-category loop

```python
    for stem, title, _is_quant in CATEGORIES:
        rows = rows_by_category[stem]
        out += [f"## {title}", ""]
        out += [_table(rows), ""] if rows else ["_No roles tracked yet._", ""]
```

with:

```python
    for stem, title, _is_quant in CATEGORIES:
        rows = rows_by_category[stem]
        open_rows = [r for r in rows if r.get("status") != "closed"]
        out += [f"## {title}", ""]
        if open_rows:
            out += [_table(open_rows), ""]
        elif rows:
            out += ["_No open roles._", ""]
        else:
            out += ["_No roles tracked yet._", ""]
```

(`!= "closed"` rather than `== "open"` so a hand-corrupted row with a missing status stays visible instead of silently vanishing — the same tolerance run_scrape_merge extends to existing rows.)

**(e)** Replace the legend string with:

```python
        "**Legend** — Degree = BS/MS/PhD eligibility. ~Date Posted is "
        "estimated from when we first recorded the role. ⚠️dup? marks a "
        "possible duplicate pending manual review. Closed roles are kept in "
        "the data but not rendered. Programs/Research/Competitions status: "
        "🟢 **Open** · ⏳ `opens <date>` (or ⏳ Upcoming if unannounced) · "
        "🔒 Closed · ⚪ Unknown.",
```

- [ ] **Step 4: Run the full test suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass. (`run_scrape_merge` tests render READMEs too and must survive the column change; if any test asserts on the removed columns or a closed row's visibility, update that assertion the same way as steps 1b/1c.)

- [ ] **Step 5: Regenerate the real README and check integrity**

Run: `python3 scripts/generate_readme.py`
Expected: `Wrote /Users/turdy/unemploy/summer2027/internship-tracker/README.md`

Run: `python3 scripts/check_integrity.py`
Expected: exit 0, `No blocking violations.`

Spot-check: `head -40 README.md` — job tables end at the Degree column, no Status/Last Verified, closed rows absent.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_readme.py tests/test_generate_readme.py README.md
git commit -m "feat: drop Status and Last Verified README columns; render open rows only"
```

---

### Task 9: Docs, spec status, final verification

**Files:**
- Modify: `docs/SCRAPING.md` (append)
- Modify: `docs/superpowers/specs/2026-08-08-ats-verification-design.md` (status line)
- Modify: `CLAUDE.md` (local only — gitignored, do NOT `git add` it)

- [ ] **Step 1: Append the run-book to `docs/SCRAPING.md`**

```markdown

## ATS verification pass (authoritative location/date)

Manual, explicit-request-only re-verification of open rows whose links sit
on an API-covered ATS (Workday CXS, Greenhouse boards-api, Lever v0
postings, Ashby posting-api, SmartRecruiters postings API, iCIMS JSON-LD).
Design: `docs/superpowers/specs/2026-08-08-ats-verification-design.md`.

1. `python3 scripts/check_ats.py [category ...]` — probes the APIs and
   writes `scratch/ats_corrections.json` (the audit record of every
   proposed change). Writes nothing else.
2. Review the printed summary — every proposed close and non-US delete is
   listed individually.
3. `python3 scripts/apply_ats_corrections.py scratch/ats_corrections.json`
   — the single serialized writer: applies corrections, stamps
   `last_verified`, rewrites `data/*.yaml`, re-renders `README.md`. Aborts
   without writing if any corrected row fails ROW_SCHEMA.
4. `python3 scripts/check_integrity.py`, then commit.

Never run concurrently with `run_scrape_merge.py` (single-writer
discipline). `unknown` results change nothing — no disappearance-based
closing.
```

- [ ] **Step 2: Flip the spec status**

In `docs/superpowers/specs/2026-08-08-ats-verification-design.md`, change
`**Status:** Approved design, not yet implemented.` to
`**Status:** Implemented.`

- [ ] **Step 3: Update the local `CLAUDE.md` (gitignored — do not commit)**

In the `## Tech stack & commands` code block, add after the `check_programs.py` line:

```bash
python3 scripts/check_ats.py [category ...]                 # ATS-API verification probe (explicit request only) -> scratch/ats_corrections.json
python3 scripts/apply_ats_corrections.py scratch/ats_corrections.json   # apply corrections; then check_integrity + commit
```

And in the `## Current state` first paragraph, extend the script list with `scripts/ats_verify.py`, `scripts/check_ats.py`, `scripts/apply_ats_corrections.py`.

- [ ] **Step 4: Full verification**

Run: `python3 -m pytest tests/ -v`
Expected: all pass (254 pre-existing plus this plan's new tests)

Run: `python3 scripts/check_integrity.py`
Expected: exit 0, `No blocking violations.`

- [ ] **Step 5: Commit**

```bash
git add docs/SCRAPING.md docs/superpowers/specs/2026-08-08-ats-verification-design.md
git commit -m "docs: ATS verification run-book; mark spec implemented"
```

---

## After the plan: first live run (Tony-triggered, NOT part of this plan)

The implementation ships with zero data rows changed. The first full
verification run is a separate operational step on Tony's explicit request,
following the docs/SCRAPING.md run-book: `check_ats.py` (all categories) →
review `scratch/ats_corrections.json`, especially every `delete_non_us` and
`close` → `apply_ats_corrections.py` → `check_integrity.py` → commit.
Expect a large `location_unresolved`/`set_date` volume on the first pass;
that's the point.

## Known risks (accepted in design)

- Workday CXS may serve HTML to some tenants despite the JSON API path —
  those rows come out `unknown` (visible in per-family counts), never
  wrong.
- Ashby orgs with the posting-api disabled 404 at board level → `unknown`
  for all their rows (deliberate: a board-404 must not close postings).
- `check_links.py`'s 404-based closing overlaps this pass for API-covered
  rows; harmless duplication, and check_links remains the only liveness
  check for custom-site rows.
