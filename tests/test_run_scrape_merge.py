import json
import yaml
from pathlib import Path
from run_scrape_merge import run


def test_run_merges_reports_and_regenerates_readme(tmp_path):
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir()
    for stem in ("swe", "quant", "data_science", "ai_ml", "hardware",
                 "actuarial", "consulting", "ib"):
        (data_dir / f"{stem}.yaml").write_text("[]\n")
    reports_dir = root / "reports"
    reports_dir.mkdir()
    (reports_dir / "r1.json").write_text(json.dumps({
        "category": "swe", "source_entity": "greenhouse:stripe",
        "postings": [{
            "company": "Stripe", "role": "SWE Intern", "location": "New York, NY",
            "link": "https://x.com/j", "term": "Summer 2027", "degree": ["BS"],
            "source": "greenhouse",
        }],
    }))
    readme = root / "README.md"

    summaries = run(reports_dir, data_dir, readme)

    swe = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert len(swe) == 1 and swe[0]["company"] == "Stripe"
    assert summaries["swe"]["new"] == [swe[0]["id"]]
    assert "Stripe" in readme.read_text()


def test_run_skips_posting_missing_required_field(tmp_path, capsys):
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir()
    reports_dir = root / "reports"
    reports_dir.mkdir()
    (reports_dir / "r1.json").write_text(json.dumps({
        "category": "swe", "source_entity": "greenhouse:acme",
        "postings": [
            {
                # missing link entirely -> must be skipped, not crash the run
                "company": "Acme", "role": "SWE Intern",
                "location": "New York, NY",
                "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
            },
            {
                "company": "Beta Corp", "role": "SWE Intern",
                "location": "Austin, TX", "link": "https://beta.example.com/jobs/1",
                "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
            },
        ],
    }))
    readme = root / "README.md"

    summaries = run(reports_dir, data_dir, readme)

    swe = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert len(swe) == 1
    assert swe[0]["company"] == "Beta Corp"
    assert summaries["swe"]["new"] == [swe[0]["id"]]

    out = capsys.readouterr().out
    assert "greenhouse:acme" in out
    assert "link" in out


def test_run_drops_row_that_fails_schema_validation(tmp_path, capsys):
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir()
    reports_dir = root / "reports"
    reports_dir.mkdir()
    (reports_dir / "r1.json").write_text(json.dumps({
        "category": "swe", "source_entity": "greenhouse:gamma",
        "postings": [{
            "company": "Gamma Inc", "role": "SWE Intern",
            "location": "New York, NY", "link": "https://gamma.example.com/jobs/1",
            "date_posted": "07/15/2026",  # malformed: not YYYY-MM-DD
            "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
        }],
    }))
    readme = root / "README.md"

    summaries = run(reports_dir, data_dir, readme)

    swe = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert swe == []
    assert summaries["swe"]["new"] == []

    out = capsys.readouterr().out
    assert "dropped invalid row" in out
    assert "date_posted" in out


def test_run_scrubs_closed_id_when_new_row_also_dropped(tmp_path, capsys):
    # A row can be created and re-found (and marked closed) within the same
    # run, by two postings sharing a link in the same fetch report. If that
    # row also fails schema validation (malformed date_posted here), its id
    # must not dangle in summary["closed"] once the row itself is dropped.
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir()
    reports_dir = root / "reports"
    reports_dir.mkdir()
    (reports_dir / "r1.json").write_text(json.dumps({
        "category": "swe", "source_entity": "greenhouse:delta",
        "postings": [
            {
                "company": "Delta Corp", "role": "SWE Intern",
                "location": "New York, NY",
                "link": "https://delta.example.com/jobs/1",
                "date_posted": "07/15/2026",  # malformed: not YYYY-MM-DD
                "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
            },
            {
                # Same link -> re-found within this run; closed_marker flips
                # the row to closed before the post-merge validation gate runs.
                "company": "Delta Corp", "role": "SWE Intern",
                "location": "New York, NY",
                "link": "https://delta.example.com/jobs/1",
                "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
                "closed_marker": True,
            },
        ],
    }))
    readme = root / "README.md"

    summaries = run(reports_dir, data_dir, readme)

    swe = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert swe == []
    assert summaries["swe"]["new"] == []
    assert summaries["swe"]["closed"] == []

    out = capsys.readouterr().out
    assert "dropped invalid row" in out
