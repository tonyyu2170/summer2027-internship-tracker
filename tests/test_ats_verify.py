"""Tests for the pure ATS-verification core (scripts/ats_verify.py)."""
import json
from datetime import date

from ats_verify import api_url, extract


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
