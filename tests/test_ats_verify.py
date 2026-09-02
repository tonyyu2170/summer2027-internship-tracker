"""Tests for the pure ATS-verification core (scripts/ats_verify.py)."""
import json
from datetime import date

import pytest

from ats_verify import api_url, extract, decide


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
                   "closed": True, "title": None}


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


# 23 of 71 live open Ashby rows carry a /application or ?utm_* suffix. Deriving
# the uuid from the link's last path segment yields "application" for those,
# matching no job and false-closing a live posting.
@pytest.mark.parametrize("suffix", [
    "/application",
    "/application?embed=true",
    "/apply",
    "?utm_source=Simplify&ref=Simplify",
    "/",
])
def test_extract_ashby_finds_job_despite_link_suffix(suffix):
    jobs = [{"id": "b333f0f7-0ca6-4509-8697-9303396b5364",
             "location": "San Francisco", "isListed": True}]
    ext = extract("ashby", 200, _ashby_board(jobs),
                  link=ASHBY_LINK + suffix, today=TODAY)
    assert ext["closed"] is False
    assert ext["locations"] == ["San Francisco"]


def test_extract_ashby_matches_by_joburl_despite_link_suffix():
    jobs = [{"jobUrl": ASHBY_LINK + "/application", "location": "Austin, TX",
             "isListed": True}]
    ext = extract("ashby", 200, _ashby_board(jobs),
                  link=ASHBY_LINK + "?utm_source=x", today=TODAY)
    assert ext["closed"] is False


def test_extract_ashby_unparseable_link_is_unknown_not_closed():
    # No uuid to match on means we cannot tell — and must not guess "gone".
    assert extract("ashby", 200, _ashby_board([{"id": "x"}]),
                   link="https://jobs.ashbyhq.com/bild-ai", today=TODAY) is None


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


def test_decide_deletes_on_non_us_country_field():
    ext = _ext(locations=["Toronto"], country="Canada")
    actions = decide(_row(), ext)
    assert actions[0]["action"] == "delete_non_us"
    assert actions[0]["api_locations"] == ["Toronto"]
    assert actions[0]["country"] == "Canada"


def test_decide_bare_remote_with_non_us_country_deletes():
    # "Remote" canonicalizes to "Remote (US)", but the API's own country
    # field wins when remote is the only US-looking signal
    ext = _ext(locations=["Remote"], country="Canada")
    assert decide(_row(), ext)[0]["action"] == "delete_non_us"


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


def test_decide_never_mutates_the_row():
    row = _row()
    before = dict(row)
    decide(row, _ext(locations=["Redmond, WA"], date_posted="2026-01-01"))
    assert row == before


def test_decide_non_us_country_beats_a_location_that_looks_us():
    # Magna's real row: 'Milton, Ontario, CA' with country 'Canada'
    # canonicalizes to 'Ontario, CA' — Ontario, California. Without the
    # country veto the row is relabelled as a US location instead of deleted.
    ext = _ext(locations=["Milton, Ontario, CA"], country="Canada")
    actions = decide(_row(location="Milton, CA"), ext)
    assert actions[0]["action"] == "delete_non_us"
    assert all(a["action"] != "set_location" for a in actions)


def test_decide_ignores_pre_cycle_requisition_dates():
    # Lever createdAt / Greenhouse first_published are requisition-creation
    # dates. The live probe found a Summer 2027 Palantir role reporting
    # 2016-10-06; writing that would date the posting ten years old.
    ext = _ext(locations=["New York, NY"], date_posted="2016-10-06")
    assert decide(_row(date_posted="2026-06-29"), ext) == [{"action": "confirm"}]


def test_decide_still_takes_in_cycle_dates():
    ext = _ext(locations=["New York, NY"], date_posted="2026-06-15")
    assert {"action": "set_date", "old": "2026-07-01",
            "new": "2026-06-15"} in decide(_row(), ext)


def test_api_url_greenhouse_regional_board():
    # job-boards.eu.greenhouse.io is served by the same boards-api host.
    assert api_url("https://job-boards.eu.greenhouse.io/imc/jobs/4823945101") == (
        "greenhouse",
        "https://boards-api.greenhouse.io/v1/boards/imc/jobs/4823945101",
    )


def test_api_url_workday_locale_prefixed_link_is_covered():
    ats, url = api_url(
        "https://nwis.wd12.myworkdayjobs.com/en-US/nw/job/Annapolis-Junction-MD/SWE_R-1")
    assert ats == "workday"
    assert "/wday/cxs/nwis/nw/job/" in url and "/en-US/" not in url


ICIMS_LINK = "https://careers-cadent.icims.com/jobs/1406/enterprise-ai-intern/job"


@pytest.mark.parametrize("final,expected", [
    (ICIMS_LINK, False),                                      # no redirect
    ("https://careers-cadent.icims.com/jobs/1406/enterprise-ai-intern/job?in_iframe=1",
     False),                                                  # same posting
    ("https://careers-cadent.icims.com/jobs/search?ss=1", True),   # listing page
    ("https://careers-cadent.icims.com/jobs/9999/other/job", True),  # other job
    ("https://careers-cadent.icims.com/", True),              # board root
    (None, False),                                            # no final url
])
def test_icims_redirected_away(final, expected):
    from ats_verify import icims_redirected_away
    assert icims_redirected_away(ICIMS_LINK, final) is expected


def test_icims_redirect_check_ignores_non_icims_links():
    from ats_verify import icims_redirected_away
    assert icims_redirected_away(
        "https://boards.greenhouse.io/acme/jobs/123", "https://elsewhere") is False


@pytest.mark.parametrize("locations,stored", [
    (["Redmond, WA"], "Washington, DC"),        # would once have been set_location
    (["New York"], "New York, NY"),             # would once have been unresolved
    (["Chicago, IL (On-Site)"], "Austin, TX"),
    (["150 North Riverside, Chicago, IL"], "San Francisco, CA"),
    (["Denver, CO | Long Beach, CA"], "Denver, CO"),
])
def test_decide_never_proposes_a_location_change(locations, stored):
    # Location is not a tracked field: the listing is US-only by
    # construction and individual locations aren't rendered or corrected.
    actions = decide(_row(location=stored), _ext(locations=locations))
    assert all(a["action"] not in ("set_location", "location_unresolved")
               for a in actions)


def test_decide_ignores_api_date_after_date_added():
    # 2026-09-01 audit: Workday's "Posted N Days Ago" follows the latest
    # re-post, so an API date after the row was first seen is not a posting
    # date and must not become a set_date.
    ext = _ext(locations=["New York, NY"], date_posted="2026-07-20")
    assert decide(_row(date_added="2026-07-05"), ext) == [{"action": "confirm"}]
    assert decide(_row(date_added="2026-07-20"), ext)[0]["action"] == "set_date"


# --- 2026-09-02 audit: restore titles a tracker truncated ---------------------

def test_extract_carries_the_posting_title():
    gh = json.dumps({"id": 1, "title": "Software Engineering Intern - Summer 2027",
                     "location": {"name": "Austin, TX"}, "first_published": "2026-08-01T00:00:00Z"})
    assert extract("greenhouse", 200, gh)["title"] == "Software Engineering Intern - Summer 2027"
    wd = json.dumps({"jobPostingInfo": {"title": "Intern - AI Systems", "location": "Boise, ID",
                                        "postedOn": "Posted Today"}})
    assert extract("workday", 200, wd, today=date(2026, 9, 2))["title"] == "Intern - AI Systems"
    lv = json.dumps({"id": "x", "text": "Data Intern", "categories": {"location": "NYC"}})
    assert extract("lever", 200, lv)["title"] == "Data Intern"
    sr = json.dumps({"id": "1", "name": "Calibration Intern", "location": {"city": "Plymouth", "region": "MI", "country": "us"}})
    assert extract("smartrecruiters", 200, sr)["title"] == "Calibration Intern"
    assert extract("greenhouse", 404, "")["title"] is None


def test_extract_ashby_carries_the_matched_jobs_title():
    link = "https://jobs.ashbyhq.com/acme/0d5b9e8b-2a2d-4f6b-9c1e-7f3a1b2c3d4e"
    body = json.dumps({"jobs": [{"id": "0d5b9e8b-2a2d-4f6b-9c1e-7f3a1b2c3d4e", "title": "Supply Chain Data & Analytics Intern (2027 Summer Internship)",
                                 "location": "Los Angeles, CA", "isListed": True}]})
    assert extract("ashby", 200, body, link=link)["title"].startswith("Supply Chain Data")


def test_decide_restores_a_truncated_title_from_the_api():
    row = _row(role="Supply Chain Data & Analytics Inte...")
    ext = _ext(title="Supply Chain Data & Analytics Intern (2027 Summer Internship)")
    assert {"action": "set_role", "old": row["role"],
            "new": "Supply Chain Data & Analytics Intern (2027 Summer Internship)"} in decide(row, ext)


def test_decide_leaves_a_full_or_unrelated_title_alone():
    assert decide(_row(role="SWE Intern"), _ext(title="SWE Intern - Summer 2027")) == [{"action": "confirm"}]
    assert decide(_row(role="SWE Inte..."), _ext(title="Data Intern")) == [{"action": "confirm"}]
    assert decide(_row(role="SWE Inte..."), _ext()) == [{"action": "confirm"}]
