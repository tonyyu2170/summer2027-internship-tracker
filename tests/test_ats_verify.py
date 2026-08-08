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


# --- location pre-cleaning -------------------------------------------------
# canonicalize_location reads the last comma-part as the state, which is right
# for tracker table text and wrong for raw ATS location fields. Every case here
# is a real string observed in the 2026-08-08 live probe of 439 rows.

@pytest.mark.parametrize("raw,expected", [
    # multi-location in one string: the killer — pairing Denver with CA
    ("Denver, CO | Long Beach, CA", ["Denver, CO", "Long Beach, CA"]),
    # street address ahead of the city
    ("150 North Riverside, Chicago, IL", ["Chicago, IL"]),
    # org path
    ("North America/USA/Minnesota/Mankato, MN", ["Mankato, MN"]),
    # country prefix / suffix, comma- and space- and dash-separated
    ("USA, LaFayette, GA", ["LaFayette, GA"]),
    ("US - Lincoln, NE", ["Lincoln, NE"]),
    ("Newark, NJ, USA", ["Newark, NJ"]),
    ("Cambridge, MA USA", ["Cambridge, MA"]),
    ("North Billerica, MA - USA", ["North Billerica, MA"]),
    ("Santa Clara, California - United States of America", ["Santa Clara, CA"]),
    # site qualifiers and parentheticals
    ("Corporate - Baton Rouge, LA", ["Baton Rouge, LA"]),
    ("Dallas, TX - Headquarters", ["Dallas, TX"]),
    ("Bellevue, WA (Seattle)", ["Bellevue, WA"]),
    ("Bala Cynwyd (Philadelphia Area), PA", ["Bala Cynwyd, PA"]),
    ("Detroit Area, MI", ["Detroit, MI"]),
    # casing and the New York City / New York churn
    ("new york, NY", ["New York, NY"]),
    ("New York City, NY", ["New York, NY"]),
    ("Washington, D.C.", ["Washington, DC"]),
])
def test_location_candidates_resolve_real_ats_formats(raw, expected):
    from ats_verify import _location_candidates, _plausible_city
    from normalize import canonicalize_location
    got = []
    for cand in _location_candidates(raw):
        canon = canonicalize_location(cand)
        if canon and _plausible_city(canon) and canon not in got:
            got.append(canon)
    assert got == expected


@pytest.mark.parametrize("raw", [
    "Atlanta",                          # city-only: ambiguous, not non-US
    "London",
    "Chicago, New York City",           # two cities, no state
    "In-Office",
    "Flexible - Any SpaceX Site",
    "MI - Detroit Sales Office",        # state-first: deliberately not inferred
    "US-California-Palo Alto",
    "DE-CELLE-BAKER-HUGHES-STRASSE 1",
])
def test_ambiguous_location_text_still_resolves_to_nothing(raw):
    # Under-matching is the safe direction: these become location_unresolved,
    # never a wrong set_location.
    from ats_verify import _location_candidates, _plausible_city
    from normalize import canonicalize_location
    assert not [c for c in (canonicalize_location(x)
                            for x in _location_candidates(raw))
                if c and _plausible_city(c)]


def test_decide_confirms_multi_location_string_instead_of_writing_wrong_state():
    # The regression that halted the first live run: True Anomaly's stored
    # 'Denver, CO' against API 'Denver, CO | Long Beach, CA' produced a
    # set_location to 'Denver, CA'.
    ext = _ext(locations=["Denver, CO | Long Beach, CA"])
    assert decide(_row(location="Denver, CO"), ext) == [{"action": "confirm"}]


def test_decide_ignores_street_address_when_a_real_city_is_present():
    ext = _ext(locations=["150 North Riverside, Chicago, IL"])
    actions = decide(_row(location="San Francisco, CA"), ext)
    assert {"action": "set_location", "old": "San Francisco, CA",
            "new": "Chicago, IL"} in actions
