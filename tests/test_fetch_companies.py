import json
from pathlib import Path

import yaml

from fetch_companies import (
    _fetch_smartrecruiters,
    _fetch_workday_cxs,
    _fetch_workday_search,
    _normalize_source,
    run,
)


SOURCE = {
    "actuarial": [{
        "company": "Oliver Wyman",
        "provider": "phenom_job_page",
        "url": "https://careers.example.com/job/R_356561/actuarial-intern",
        "source_entity": "company:marsh-oliver-wyman",
        "term": "Summer 2027",
        "degree": ["BS"],
        "role_pattern": "(?i)actuar",
    }]
}

HTML = '''<script type="application/ld+json">{
  "@type": "JobPosting", "title": "Actuarial Internship",
  "datePosted": "2026-07-26",
  "jobLocation": {"address": {
    "addressLocality": "New York", "addressRegion": "NY"}}
}</script>'''


def _write_config(path: Path, config=SOURCE):
    path.write_text(yaml.safe_dump(config, sort_keys=False))


def test_run_writes_fetch_report_and_preserves_existing_state(tmp_path):
    config_path = tmp_path / "companies.yaml"
    state_path = tmp_path / "scrape_state.yaml"
    out_dir = tmp_path / "reports"
    _write_config(config_path)
    state_path.write_text(yaml.safe_dump({"github_tracker": {"row_count": 42}}))

    drops = run("actuarial", out_dir, config_path, state_path, fetch=lambda _: HTML)

    report = json.loads((out_dir / "company_marsh_oliver_wyman_actuarial.json").read_text())
    assert report["category"] == "actuarial"
    assert report["postings"][0]["location"] == "New York, NY"
    assert drops == {}
    state = yaml.safe_load(state_path.read_text())
    assert state["github_tracker"] == {"row_count": 42}
    assert state["company_sources"]["company:marsh-oliver-wyman"]["row_count"] == 1


def test_run_records_source_failure_without_advancing_its_state(tmp_path):
    config_path = tmp_path / "companies.yaml"
    state_path = tmp_path / "scrape_state.yaml"
    out_dir = tmp_path / "reports"
    _write_config(config_path)
    prior = {"company_sources": {"company:marsh-oliver-wyman": {"last_success": "2026-07-01"}}}
    state_path.write_text(yaml.safe_dump(prior))
    out_dir.mkdir()
    (out_dir / "company_marsh_oliver_wyman_actuarial.json").write_text("{}")

    def failing_fetch(_):
        raise OSError("offline")

    drops = run("actuarial", out_dir, config_path, state_path, fetch=failing_fetch)

    assert not list(out_dir.glob("*_actuarial.json"))
    assert drops["company:marsh-oliver-wyman"]["source_parse_failed"] == 1
    assert yaml.safe_load(state_path.read_text()) == prior


def test_run_counts_manual_discovery_without_writing_a_report(tmp_path):
    config_path = tmp_path / "companies.yaml"
    out_dir = tmp_path / "reports"
    config = {"actuarial": [{
        "company": "Genworth Financial", "provider": "manual_discovery",
        "url": "https://careers.example.com", "source_entity": "company:genworth",
        "term": "Summer 2027", "degree": ["BS"], "role_pattern": "(?i)actuar",
    }]}
    _write_config(config_path, config)

    drops = run("actuarial", out_dir, config_path, tmp_path / "state.yaml")

    assert drops["company:genworth"]["manual_discovery"] == 1
    assert not list(out_dir.glob("*_actuarial.json"))


def test_workday_cxs_paginates_and_uses_configured_search_contract():
    source = {
        "provider": "workday_cxs",
        "url": "https://gnw.wd1.myworkdayjobs.com/Genworth_Confidential",
        "tenant": "gnw", "site": "Genworth_Confidential", "search_text": "actuarial",
    }
    calls = []

    def post(url, body):
        calls.append((url, body))
        if body["offset"] == 0:
            return {"total": 2, "jobPostings": [{"title": "first"}]}
        return {"total": 2, "jobPostings": [{"title": "second"}]}

    assert _fetch_workday_cxs(source, post) == {
        "jobPostings": [{"title": "first"}, {"title": "second"}]}
    assert calls == [
        ("https://gnw.wd1.myworkdayjobs.com/wday/cxs/gnw/Genworth_Confidential/jobs", {
            "appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "actuarial"}),
        ("https://gnw.wd1.myworkdayjobs.com/wday/cxs/gnw/Genworth_Confidential/jobs", {
            "appliedFacets": {}, "limit": 20, "offset": 1, "searchText": "actuarial"}),
    ]


def test_run_merges_workday_parser_drops_with_a_matching_report(tmp_path):
    config_path = tmp_path / "companies.yaml"
    state_path = tmp_path / "scrape_state.yaml"
    out_dir = tmp_path / "reports"
    config = {"actuarial": [{
        "company": "Genworth Financial", "provider": "workday_cxs",
        "url": "https://gnw.wd1.myworkdayjobs.com/Genworth_Confidential",
        "source_entity": "company:genworth", "term": "Summer 2027", "degree": ["BS"],
        "role_pattern": "(?i)actuar", "term_pattern": "(?i)summer\\s+2027",
        "tenant": "gnw", "site": "Genworth_Confidential", "search_text": "actuarial",
    }]}
    _write_config(config_path, config)
    out_dir.mkdir()
    (out_dir / "drop_counts.json").write_text(json.dumps({
        "company:genworth": {"manual_discovery": 1},
        "github_tracker:example": {"unclassified": 2},
    }))
    payload = {"jobPostings": [
        {"title": "Actuarial Analyst – 2027", "externalPath": "/job/analyst", "locationsText": "Richmond, Virginia"},
        {"title": "Actuarial Intern – Summer 2027", "externalPath": "/job/intern", "locationsText": "Richmond, Virginia"},
    ]}

    drops = run("actuarial", out_dir, config_path, state_path, fetch=lambda _: payload)

    report = json.loads((out_dir / "company_genworth_actuarial.json").read_text())
    assert report["postings"][0]["link"].endswith("Genworth_Confidential/job/intern")
    assert drops == {
        "company:genworth": {"term_unmatched": 1},
        "github_tracker:example": {"unclassified": 2},
    }
    state = yaml.safe_load(state_path.read_text())
    assert state["company_sources"]["company:genworth"]["row_count"] == 1


def _greenhouse_board(*titles):
    return {"jobs": [
        {"title": t, "absolute_url": f"https://boards.greenhouse.io/acme/jobs/{i}",
         "location": {"name": "Austin, TX"}, "content": "Summer 2027. BS students."}
        for i, t in enumerate(titles)]}


def test_run_drops_company_roles_categorize_rejects(tmp_path):
    config_path = tmp_path / "companies.yaml"
    out_dir = tmp_path / "reports"
    _write_config(config_path, {"swe": [
        {"company": "Acme", "ats": "greenhouse", "url": "acme"}]})
    payload = _greenhouse_board("Supply Chain Intern", "Outside Sales Internship")

    drops = run("swe", out_dir, config_path, tmp_path / "state.yaml",
                fetch=lambda _: payload)

    assert drops["company:acme"]["category_drop"] == 2
    assert not list(out_dir.glob("*.json")) or not list(out_dir.glob("company_acme_*.json"))


def test_run_files_company_postings_under_their_classified_category(tmp_path):
    config_path = tmp_path / "companies.yaml"
    out_dir = tmp_path / "reports"
    _write_config(config_path, {"swe": [
        {"company": "Acme", "ats": "greenhouse", "url": "acme"}]})
    # A swe-watch-list board carrying a data-science role and an unclassifiable
    # one: the first is filed where it belongs, the second falls back.
    payload = _greenhouse_board("Data Science Intern - Summer 2027",
                                "Software Engineer Intern",
                                "Rotational Program Intern")

    run("swe", out_dir, config_path, tmp_path / "state.yaml", fetch=lambda _: payload)

    ds = json.loads((out_dir / "company_acme_data_science.json").read_text())
    assert ds["category"] == "data_science"
    assert [p["role"] for p in ds["postings"]] == ["Data Science Intern - Summer 2027"]
    swe = json.loads((out_dir / "company_acme_swe.json").read_text())
    assert sorted(p["role"] for p in swe["postings"]) == [
        "Rotational Program Intern", "Software Engineer Intern"]


def test_normalize_source_derives_a_workday_search_endpoint():
    source = _normalize_source({
        "company": "Cadence (University)", "ats": "workday",
        "url": "https://cadence.wd1.myworkdayjobs.com/Univ_Careers"})

    assert source["provider"] == "workday_search"
    assert source["source_entity"] == "company:cadence-university"
    assert source["tenant"] == "cadence"
    assert source["site"] == "Univ_Careers"
    assert source["search_text"] == "Summer 2027"


def test_normalize_source_lets_an_entry_pin_a_workday_tenant():
    # Castleton's vanity host is osv-cci but its tenant is osv_cci; deriving
    # the tenant from the host label 422s.
    source = _normalize_source({
        "company": "Castleton Commodities International", "ats": "workday",
        "tenant": "osv_cci",
        "url": "https://osv-cci.wd1.myworkdayjobs.com/CCICareers"})

    assert source["tenant"] == "osv_cci"
    assert source["site"] == "CCICareers"


def test_fetch_smartrecruiters_pages_the_board_then_details_us_interns():
    source = _normalize_source({
        "company": "Acme", "ats": "smartrecruiters",
        "url": "https://jobs.smartrecruiters.com/AcmeGroup"})
    assert source["provider"] == "smartrecruiters_api"
    assert source["board"] == "AcmeGroup"

    board = "https://api.smartrecruiters.com/v1/companies/AcmeGroup/postings"
    pages = {
        f"{board}?limit=100&offset=0": {"totalFound": 3, "content": [
            {"id": "1", "name": "Software Engineer Intern",
             "location": {"country": "us"}},
            {"id": "2", "name": "Software Engineer Intern",
             "location": {"country": "de"}}]},   # non-US: no detail fetch
        f"{board}?limit=100&offset=2": {"totalFound": 3, "content": [
            {"id": "3", "name": "Staff Engineer",
             "location": {"country": "us"}}]},   # not intern-titled
    }
    requested = []

    def get(url):
        requested.append(url)
        return pages.get(url, {"id": "1", "name": "Software Engineer Intern"})

    payload = _fetch_smartrecruiters(source, get)

    assert payload == {"jobs": [{"id": "1", "name": "Software Engineer Intern"}]}
    assert requested[-1] == f"{board}/1"
    assert len(requested) == 3


def _search_source():
    return _normalize_source({
        "company": "Acme", "ats": "workday",
        "url": "https://acme.wd1.myworkdayjobs.com/External"})


def test_fetch_workday_search_pulls_details_only_for_intern_titles():
    gets = []

    def post(url, body):
        assert url == "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/jobs"
        assert body["searchText"] == "Summer 2027"
        return {"total": 2, "jobPostings": [
            {"title": "SWE Intern - Summer 2027", "externalPath": "/job/Austin-TX/a_R1"},
            {"title": "Director, Summer 2027 Planning", "externalPath": "/job/Austin-TX/b_R2"},
        ]}

    def get(url):
        gets.append(url)
        return {"jobPostingInfo": {"title": "SWE Intern - Summer 2027"}}

    payload = _fetch_workday_search(_search_source(), post, get)

    # Only the intern-titled row costs a detail request.
    assert gets == [
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/External/job/Austin-TX/a_R1"]
    assert payload == {"jobs": [{"title": "SWE Intern - Summer 2027"}],
                       "truncated": False}


def test_fetch_workday_search_caps_a_runaway_search():
    def post(url, body):
        offset = body["offset"]
        return {"total": 10_000, "jobPostings": [
            {"title": f"Intern {offset + i}", "externalPath": f"/job/Austin-TX/j{offset + i}"}
            for i in range(20)]}

    def get(url):
        return {"jobPostingInfo": {"title": "Intern"}}

    payload = _fetch_workday_search(_search_source(), post, get)

    assert payload["truncated"] is True
    assert len(payload["jobs"]) == 100
