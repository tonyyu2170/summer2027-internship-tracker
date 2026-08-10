import json

from repost_verify import find_reposts, listing_url, parse_listing


def _row(rid, role, link, date="2026-05-29"):
    return {"id": rid, "company": "InfiniteQuant", "role": role,
            "link": link, "date_posted": date, "status": "open"}


def test_listing_url_smartrecruiters():
    ats, url = listing_url(
        "https://jobs.smartrecruiters.com/InfiniteQuant/744000129235439-quant-researcher")
    assert ats == "smartrecruiters"
    assert url == ("https://api.smartrecruiters.com/v1/companies/"
                   "InfiniteQuant/postings?limit=100")


def test_listing_url_greenhouse_and_lever():
    ats, url = listing_url("https://job-boards.greenhouse.io/anthropic/jobs/4020305008")
    assert ats == "greenhouse"
    assert url == "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs"

    ats, url = listing_url(
        "https://jobs.lever.co/field-ai/8a3b5d4b-f88f-4704-bfdd-74e8dcd30704")
    assert ats == "lever"
    assert url == "https://api.lever.co/v0/postings/field-ai?mode=json"


def test_listing_url_skips_workday_and_unknown():
    # Workday is deliberately out of scope: normalize_link doesn't collapse
    # its -N instance suffixes or board aliases, so every such row would
    # look absent from the listing and fake a repost.
    assert listing_url(
        "https://hntb.wd5.myworkdayjobs.com/hntb_careers/job/X/Y_R-31092-1") is None
    assert listing_url("https://www.google.com/about/careers/jobs/results/123") is None


def test_parse_listing_smartrecruiters():
    body = json.dumps({"content": [
        {"id": "744000142560129", "name": "Quantitative Researcher - Internship",
         "releasedDate": "2026-08-10T05:51:41.257Z",
         "company": {"identifier": "InfiniteQuant"}}]})
    entries = parse_listing("smartrecruiters", body)
    assert entries == [{
        "link": "https://jobs.smartrecruiters.com/InfiniteQuant/744000142560129",
        "title": "Quantitative Researcher - Internship",
        "date_posted": "2026-08-10"}]


def test_parse_listing_greenhouse_and_lever():
    gh = json.dumps({"jobs": [{"id": 7, "title": "SWE Intern",
                               "absolute_url": "https://job-boards.greenhouse.io/x/jobs/7",
                               "updated_at": "2026-08-01T00:00:00-04:00"}]})
    assert parse_listing("greenhouse", gh) == [
        {"link": "https://job-boards.greenhouse.io/x/jobs/7",
         "title": "SWE Intern", "date_posted": "2026-08-01"}]

    lv = json.dumps([{"text": "Robotics Intern",
                      "hostedUrl": "https://jobs.lever.co/x/abc",
                      "createdAt": 1786665600000}])
    entries = parse_listing("lever", lv)
    assert entries[0]["link"] == "https://jobs.lever.co/x/abc"
    assert entries[0]["title"] == "Robotics Intern"
    assert entries[0]["date_posted"].startswith("2026-")


def test_lever_pre_cycle_date_is_dropped():
    # Lever's createdAt records requisition creation, not this posting --
    # evergreen reqs carry dates years back (same guard as ats_verify).
    lv = json.dumps([{"text": "Intern", "hostedUrl": "https://jobs.lever.co/x/a",
                      "createdAt": 1475712000000}])  # 2016
    assert parse_listing("lever", lv)[0]["date_posted"] is None


def test_find_reposts_matches_a_unique_missing_row_to_a_unique_new_posting():
    rows = [_row("old-34910f", "Quantitative Researcher - Internship - Summer 2027",
                 "https://jobs.smartrecruiters.com/InfiniteQuant/744000129235439-quant")]
    entries = [{"link": "https://jobs.smartrecruiters.com/InfiniteQuant/744000142560129",
                "title": "Quantitative Researcher - Internship - Summer 2027",
                "date_posted": "2026-08-10"}]
    assert find_reposts(rows, entries) == [{
        "action": "repost", "id": "old-34910f",
        "old_link": rows[0]["link"],
        "new_link": entries[0]["link"], "new_date": "2026-08-10"}]


def test_greenhouse_two_hostname_forms_are_the_same_posting():
    # Greenhouse serves one board as both boards.greenhouse.io and
    # job-boards.greenhouse.io, and normalize_link collapses neither. Live
    # run: Neuralink job 6594422003 was tracked under job-boards and listed
    # under boards, and naive comparison called it a repost of itself.
    rows = [_row("nl", "Software Engineer Intern",
                 "https://job-boards.greenhouse.io/neuralink/jobs/6594422003")]
    entries = [{"link": "https://boards.greenhouse.io/neuralink/jobs/6594422003"
                        "?gh_jid=6594422003",
                "title": "Software Engineer Intern", "date_posted": "2026-06-22"}]
    assert find_reposts(rows, entries) == []


def test_find_reposts_ignores_rows_still_live():
    link = "https://jobs.smartrecruiters.com/InfiniteQuant/744000142560129-quant"
    rows = [_row("live", "QR Internship", link)]
    entries = [{"link": "https://jobs.smartrecruiters.com/InfiniteQuant/744000142560129",
                "title": "QR Internship", "date_posted": "2026-08-10"}]
    assert find_reposts(rows, entries) == []


def test_find_reposts_refuses_to_guess_when_titles_fan_out():
    # Copart had 9 identical 'Software Engineering Intern' rows; guessing
    # which absent row maps to which new posting would be garbage.
    rows = [_row("a", "SWE Intern", "https://jobs.lever.co/x/1"),
            _row("b", "SWE Intern", "https://jobs.lever.co/x/2")]
    entries = [{"link": "https://jobs.lever.co/x/9", "title": "SWE Intern",
                "date_posted": "2026-08-10"}]
    actions = find_reposts(rows, entries)
    assert [a["action"] for a in actions] == ["ambiguous"]
    assert sorted(actions[0]["ids"]) == ["a", "b"]


def test_find_reposts_is_silent_when_a_missing_row_has_no_title_match():
    # Absent with no replacement is the closed case, and closed-role
    # accuracy is explicitly out of scope (Tony, 2026-08-10).
    rows = [_row("gone", "Retired Role", "https://jobs.lever.co/x/1")]
    entries = [{"link": "https://jobs.lever.co/x/9", "title": "Something Else",
                "date_posted": "2026-08-10"}]
    assert find_reposts(rows, entries) == []


def test_find_reposts_ignores_truncated_titles():
    # Upstream-truncated roles ('Raytheon Electrical Engineering Inter...')
    # can't be matched safely, so they simply produce nothing.
    rows = [_row("t", "Quantitative Researcher - Inter...",
                 "https://jobs.lever.co/x/1")]
    entries = [{"link": "https://jobs.lever.co/x/9",
                "title": "Quantitative Researcher - Internship",
                "date_posted": "2026-08-10"}]
    assert find_reposts(rows, entries) == []
