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
