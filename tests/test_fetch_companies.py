import json
from pathlib import Path

import yaml

from fetch_companies import run


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
