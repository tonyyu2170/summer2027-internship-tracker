import json
from pathlib import Path

from parse_tracker import (
    parse_cvrve_json,
    parse_zshah_json,
    parse_nufintech_yaml,
    _resolve_nufintech_location,
)

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


def test_parse_zshah_json_filters_to_summer_2027_and_open():
    postings = parse_zshah_json(_fixture("zshah101.json"), season="Summer 2027")
    assert all(p["term"] == "Summer 2027" for p in postings)


def test_parse_zshah_json_reads_dict_keyed_by_id():
    text = ('{"amazon:amazon:1": {"company":"Amazon","title":"Software Dev Engineer Intern",'
            '"url":"https://e.com/1","location":"Seattle, WA","is_open":true,'
            '"season":"Summer 2027","category":"Software",'
            '"posted_at":"2026-03-25T00:00:00Z"}}')
    postings = parse_zshah_json(text, season="Summer 2027")
    assert len(postings) == 1
    p = postings[0]
    assert p["company"] == "Amazon"
    assert p["location"] == "Seattle, WA"
    assert p["upstream_category"] == "Software"
    assert p["date_posted"] == "2026-03-25"
    assert p["closed_marker"] is False


def test_parse_zshah_json_excludes_other_seasons():
    text = ('{"a": {"company":"A","title":"R","url":"https://e.com/1",'
            '"location":"NY, NY","is_open":true,"season":"Fall 2026","category":"Software"}}')
    assert parse_zshah_json(text, season="Summer 2027") == []


def test_parse_zshah_json_sets_closed_marker_from_is_open_false():
    text = ('{"a": {"company":"A","title":"R","url":"https://e.com/1",'
            '"location":"NY, NY","is_open":false,"season":"Summer 2027",'
            '"category":"Software"}}')
    assert parse_zshah_json(text, season="Summer 2027")[0]["closed_marker"] is True


def test_parse_nufintech_yaml_maps_role_codes_to_categories():
    postings = parse_nufintech_yaml(_fixture("northwesternfintech.yaml"))
    by_cat = {}
    for p in postings:
        by_cat.setdefault(p["category"], []).append(p)
    # Akuna's fixture has QD, QR, SWE and HW entries. HW must route to
    # hardware even though this is a quant-only repo (0fdf5dd).
    assert "hardware" in by_cat
    assert "quant" in by_cat
    assert "swe" in by_cat


def test_parse_nufintech_yaml_never_emits_a_closed_marker():
    # The repo publishes no status: its checkmark is decorative (66 checks,
    # 0 crosses) and closure is expressed by deleting the entry, which is
    # disappearance — this repo refuses to auto-close on that.
    postings = parse_nufintech_yaml(_fixture("northwesternfintech.yaml"))
    assert postings
    assert all(p.get("closed_marker") is False for p in postings)


def test_parse_nufintech_yaml_uses_label_in_role_when_present():
    text = """
name: "Test Capital"
website: "https://e.com"
locations: "Chicago"
notes: ""
roles:
  - role_type: "SWE"
    links:
      - url: "https://e.com/1"
        label: "C++"
      - url: "https://e.com/2"
"""
    postings = parse_nufintech_yaml(text)
    roles = {p["link"]: p["role"] for p in postings}
    assert roles["https://e.com/1"] == "Software Engineer Intern, C++"
    assert roles["https://e.com/2"] == "Software Engineer Intern"


def test_parse_nufintech_yaml_handles_company_with_no_roles():
    text = 'name: "Empty Co"\nwebsite: "https://e.com"\nlocations: "NYC"\nnotes: ""\nroles: []\n'
    assert parse_nufintech_yaml(text) == []


def test_resolve_nufintech_location_maps_bare_city_names():
    assert _resolve_nufintech_location("Chicago") == "Chicago, IL"
    assert _resolve_nufintech_location("NYC") == "New York, NY"
    assert _resolve_nufintech_location("Boston") == "Boston, MA"


def test_resolve_nufintech_location_passes_through_already_valid_city_state():
    assert _resolve_nufintech_location("Greenwich, CT") == "Greenwich, CT"
    assert _resolve_nufintech_location("Jupiter, Florida") == "Jupiter, FL"


def test_resolve_nufintech_location_drops_non_us_city():
    assert _resolve_nufintech_location("London") is None


def test_resolve_nufintech_location_takes_first_of_semicolon_separated_list():
    assert _resolve_nufintech_location("New York, NY; Boston, MA; Miami, FL") == "New York, NY"


def test_resolve_nufintech_location_takes_first_of_ambiguous_comma_list():
    # "Chicago, NYC" is two bare cities joined by comma, not "City, ST" —
    # the second token isn't a valid state, so this must not be
    # misinterpreted as state="NYC". First city wins.
    assert _resolve_nufintech_location("Chicago, NYC") == "Chicago, IL"


def test_resolve_nufintech_location_returns_none_for_unrecognized_city():
    assert _resolve_nufintech_location("Some Unmapped Town") is None


def test_parse_nufintech_yaml_emits_a_location_that_survives_canonicalize_location():
    # Regression guard: the real Akuna fixture's `locations: "Chicago"`
    # must resolve to something canonicalize_location() accepts, or every
    # posting from this tracker is silently dropped at merge time.
    from normalize import canonicalize_location
    postings = parse_nufintech_yaml(_fixture("northwesternfintech.yaml"))
    assert postings
    for p in postings:
        assert canonicalize_location(p["location"]) is not None, p
