import json
from pathlib import Path

from parse_tracker import parse_cvrve_json

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return (FIXTURES / name).read_text()


def test_parse_cvrve_json_filters_to_the_requested_term():
    # simplifyjobs' export is mostly Summer 2026; the fixture deliberately
    # includes Summer 2026 rows that must not survive.
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    assert postings, "expected some Summer 2027 postings"
    assert all(p["term"] == "Summer 2027" for p in postings)


def test_parse_cvrve_json_emits_required_fetch_report_fields():
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    for p in postings:
        for field in ("company", "role", "location", "link", "term", "degree"):
            assert p.get(field), f"missing {field} in {p}"
        assert isinstance(p["degree"], list) and p["degree"]
        assert set(p["degree"]) <= {"BS", "MS", "PhD"}


def test_parse_cvrve_json_takes_first_location():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"Software Engineer Intern",'
        '"url":"https://e.com/1","locations":["Atlanta, GA","Palm Beach, FL"],'
        '"active":true,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["location"] == "Atlanta, GA"


def test_parse_cvrve_json_maps_degrees():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":true,"terms":["Summer 2027"],'
        '"degrees":["Bachelor\'s","PhD"]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["degree"] == ["BS", "PhD"]


def test_parse_cvrve_json_defaults_degree_when_absent():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":true,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["degree"] == ["BS"]


def test_parse_cvrve_json_sets_closed_marker_from_active_false():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":false,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["closed_marker"] is True


def test_parse_cvrve_json_season_variant_for_vanshb03():
    postings = parse_cvrve_json(
        _fixture("vanshb03.json"), term_field="season", term_value="Summer",
        term_out="Summer 2027",
    )
    assert postings
    assert all(p["term"] == "Summer 2027" for p in postings)


def test_parse_cvrve_json_carries_upstream_category_when_present():
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    assert any(p.get("upstream_category") for p in postings)


def test_parse_cvrve_json_converts_unix_date_posted():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":true,"terms":["Summer 2027"],'
        '"degrees":[],"date_posted":1764210912}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["date_posted"] == "2025-11-27"
