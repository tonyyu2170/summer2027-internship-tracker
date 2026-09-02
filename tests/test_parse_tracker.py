import json
import pytest
from datetime import date
from pathlib import Path

from parse_tracker import (
    parse_cvrve_json,
    parse_zshah_json,
    parse_nufintech_yaml,
    parse_pipe_table,
    _resolve_nufintech_location,
    _resolve_us_location,
    _first_location,
    _is_off_cycle,
)

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed reference date for age-column derivation -- never date.today() in a
# parse function, so every test pins one explicitly (see parse_pipe_table).
REF = date(2026, 8, 8)


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
    # location is exempted from the blanket truthiness check: since
    # _resolve_us_location() now resolves non-US first-locations (e.g. this
    # fixture's Aquatic Capital Management entry, "London, UK") to None
    # rather than passing them through raw, a posting can legitimately have
    # location=None. When present, though, it must be a real US location.
    from normalize import canonicalize_location
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    for p in postings:
        for field in ("company", "role", "link", "term", "degree"):
            assert p.get(field), f"missing {field} in {p}"
        assert p["location"] is None or canonicalize_location(p["location"]), p
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


def test_resolve_us_location_maps_known_city_nicknames():
    assert _resolve_us_location("NYC") == "New York, NY"
    assert _resolve_us_location("New York") == "New York, NY"
    assert _resolve_us_location("New York City") == "New York, NY"
    assert _resolve_us_location("SF") == "San Francisco, CA"
    assert _resolve_us_location("Denver") == "Denver, CO"


def test_resolve_us_location_passes_through_already_valid_city_state():
    assert _resolve_us_location("Chicago, IL") == "Chicago, IL"


def test_resolve_us_location_drops_non_us_and_unrecognized():
    assert _resolve_us_location("London, UK") is None
    assert _resolve_us_location("Some Random Town") is None
    assert _resolve_us_location(None) is None


def test_parse_cvrve_json_resolves_nyc_alias():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NYC"],"active":true,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["location"] == "New York, NY"


def test_parse_zshah_json_resolves_nyc_alias():
    text = ('{"a": {"company":"A","title":"R","url":"https://e.com/1",'
            '"location":"NYC","is_open":true,"season":"Summer 2027",'
            '"category":"Software"}}')
    assert parse_zshah_json(text, season="Summer 2027")[0]["location"] == "New York, NY"


def test_parse_cvrve_json_real_fixture_nyc_entries_resolve_to_new_york():
    # Regression guard for the NYC-alias fix: these real fixture entries
    # have "NYC" as their first location, which canonicalize_location()
    # alone rejects (no state). Confirm _resolve_us_location's alias
    # fallback actually kicks in on real data, not just synthetic strings.
    #
    # Identifies the NYC-first-location entries by their url (a few other
    # entries, e.g. simplifyjobs' Barclays, already say "New York, NY"
    # verbatim and would also match a plain `location == "New York, NY"`
    # count, which is not what this test is checking) and asserts each one's
    # parsed posting resolves via the alias, not just that *some* posting
    # in the fixture happens to say "New York, NY".
    #
    # Counts verified directly against the fixture files: simplifyjobs.json
    # has 3 Summer-2027 entries with locations[0] == "NYC" (Ellipsis Labs,
    # Walleye Capital x2); suryaharikrishnan.json has 3 (Quadrillion,
    # Anthelion Capital, Virtu Financial).
    for fixture_name, expected_nyc_count in (
        ("simplifyjobs.json", 3),
        ("suryaharikrishnan.json", 3),
    ):
        text = _fixture(fixture_name)
        raw_entries = json.loads(text)
        nyc_links = {
            e.get("url")
            for e in raw_entries
            if "Summer 2027" in (e.get("terms") or [])
            and (e.get("locations") or [None])[0] == "NYC"
        }
        assert len(nyc_links) == expected_nyc_count, (
            f"{fixture_name}: expected {expected_nyc_count} raw entries with "
            f"locations[0] == 'NYC', found {len(nyc_links)}"
        )
        postings = parse_cvrve_json(
            text, term_field="terms", term_value="Summer 2027"
        )
        by_link = {p["link"]: p for p in postings}
        for link in nyc_links:
            assert by_link[link]["location"] == "New York, NY", by_link[link]


def test_parse_pipe_table_reads_columns_by_header_name():
    text = """
| Company | Role | Location | Application/Link | Date Posted |
| --- | --- | --- | --- | --- |
| Acme | Software Engineer Intern | New York, NY | <a href="https://e.com/1">Apply</a> | Jul 24 |
"""
    postings = parse_pipe_table(text, REF)
    assert len(postings) == 1
    p = postings[0]
    assert p["company"] == "Acme"
    assert p["role"] == "Software Engineer Intern"
    assert p["location"] == "New York, NY"
    assert p["link"] == "https://e.com/1"


def test_parse_pipe_table_handles_alternate_column_order():
    text = """
| Company | Role | Posted | Applied | Link |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | 2026-07-24 | — | [Apply](https://e.com/2) |
"""
    postings = parse_pipe_table(text, REF)
    assert postings[0]["link"] == "https://e.com/2"
    assert postings[0]["company"] == "Acme"


def test_parse_pipe_table_resolves_carry_forward_arrow():
    # A leading ↳ means "same company as the row above".
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Jane Street | Software Engineer Intern | New York, NY | <a href="https://e.com/1">Apply</a> |
| ↳ | Hardware Engineer Intern | New York, NY | <a href="https://e.com/2">Apply</a> |
"""
    postings = parse_pipe_table(text, REF)
    assert [p["company"] for p in postings] == ["Jane Street", "Jane Street"]


def test_parse_pipe_table_collapses_multi_location_to_first():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| HRT | SWE Intern | Austin, TX</br>Chicago, IL | <a href="https://e.com/1">Apply</a> |
"""
    assert parse_pipe_table(text, REF)[0]["location"] == "Austin, TX"


def test_parse_pipe_table_collapses_details_block_to_first_location():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Google | SWE Intern | <details><summary>**30 locations**</summary>Mountain View, CA</br>Atlanta, GA</details> | <a href="https://e.com/1">Apply</a> |
"""
    assert parse_pipe_table(text, REF)[0]["location"] == "Mountain View, CA"


def test_parse_pipe_table_sets_closed_marker_from_lock_emoji():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern 🔒 | NY, NY | <a href="https://e.com/1">Apply</a> |
"""
    p = parse_pipe_table(text, REF)[0]
    assert p["closed_marker"] is True
    assert "🔒" not in p["role"]


def test_parse_pipe_table_skips_rows_without_a_link():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | Closed |
"""
    assert parse_pipe_table(text, REF) == []


def test_parse_pipe_table_ignores_non_job_tables():
    # sndsh404's README carries resource/interview-prep tables after the list.
    text = """
| Resource | Link |
| --- | --- |
| Book | <a href="https://e.com/b">Buy</a> |
"""
    assert parse_pipe_table(text, REF) == []


def test_parse_pipe_table_on_real_fixture_yields_postings():
    postings = parse_pipe_table(_fixture("speedyapply.md"), REF)
    assert postings, "expected postings from the speedyapply fixture"
    for p in postings:
        assert p["company"] and p["role"] and p["link"]


def test_parse_pipe_table_drops_explicit_off_cycle_rows():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern (Fall 2026) | NY, NY | <a href="https://e.com/1">Apply</a> |
| Acme | SWE Intern - Winter 2027 | NY, NY | <a href="https://e.com/2">Apply</a> |
| Acme | Summer 2027 SWE Intern | NY, NY | <a href="https://e.com/3">Apply</a> |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/4">Apply</a> |
"""
    postings = parse_pipe_table(text, REF)
    links = {p["link"] for p in postings}
    assert links == {"https://e.com/3", "https://e.com/4"}


def test_parse_pipe_table_real_fixtures_have_no_off_cycle_rows():
    import re
    pat = re.compile(r"\b(summer|fall|winter|spring)\s*20\d\d\b", re.I)
    for name in ["speedyapply", "sndsh404", "zapplyjobs", "chieler"]:
        postings = parse_pipe_table(_fixture(f"{name}.md"), REF)
        for p in postings:
            matches = [m.group(0).lower().replace(" ", "") for m in pat.finditer(p["role"])]
            assert not matches or "summer2027" in matches, (name, p)


def test_parse_pipe_table_keeps_row_naming_both_summer_2027_and_another_cycle():
    # A role can legitimately list multiple eligible cycles. The verdict
    # must not depend on which cycle name appears first in the string.
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern - Fall 2026/Summer 2027 | NY, NY | <a href="https://e.com/1">Apply</a> |
| Acme | SWE Intern - Summer 2027/Fall 2026 | NY, NY | <a href="https://e.com/2">Apply</a> |
"""
    links = {p["link"] for p in parse_pipe_table(text, REF)}
    assert links == {"https://e.com/1", "https://e.com/2"}


def test_first_location_strips_plus_n_suffix():
    assert _first_location("Mountain View, CA +29") == "Mountain View, CA"


def test_first_location_strips_multiple_us_suffix():
    assert _first_location("Austin, TX (multiple US)") == "Austin, TX"


def test_parse_pipe_table_resolves_bare_city_via_alias():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern | NYC | <a href="https://e.com/1">Apply</a> |
"""
    assert parse_pipe_table(text, REF)[0]["location"] == "New York, NY"


def test_parse_pipe_table_unresolvable_location_becomes_none_not_raw_text():
    # A truthy-but-unplaceable location ("USA") must become None, not pass
    # through raw — None is what makes run_scrape_merge.py's pre-merge gate
    # print a visible warning, instead of merge.py's US-only filter dropping
    # the row later with zero trace.
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern | USA | <a href="https://e.com/1">Apply</a> |
"""
    assert parse_pipe_table(text, REF)[0]["location"] is None


@pytest.mark.parametrize("role", [
    "(FALL) Data Analyst Intern",
    "AI software Engineer Project Intern - Transaction Platform - 2026 Start - BS/MS",
    "2026 Internship, Fall - Data Science",
    "Software Engineering Intern (Winter)",
    "Spring 2026 Software Engineer Co-op",
])
def test_off_cycle_variants_are_detected(role):
    assert _is_off_cycle(role)


@pytest.mark.parametrize("role", [
    # A bare "Summer" says nothing about the cycle and must stay eligible.
    "Summer Analyst",
    "2027 Strategic Advisory: Mergers & Acquisitions Summer Analyst Program",
    # A bare non-2027 year with no season word is not a cycle marker.
    "Software Engineer Intern (apps reviewed from Aug 2026)",
    "Intern - Mechanical Engineer - 2026",
    # Names Summer 2027 alongside another cycle -- still eligible.
    "Fall 2026/Summer 2027 SWE Intern",
    "Software Engineering- Internship (Fall 2026/Summer 2027)",
    "Summer 2027 Systems Engineering Intern",
    "Software Engineer Intern",
])
def test_eligible_roles_are_not_flagged_off_cycle(role):
    assert not _is_off_cycle(role)


def test_parse_pipe_table_requires_reference_date():
    # Purity guard: parse_pipe_table must never fall back to date.today()
    # internally -- the reference date has to come from the caller (real
    # runs) or the test, every time.
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | 4d |
"""
    with pytest.raises(TypeError):
        parse_pipe_table(text)


def test_parse_pipe_table_derives_date_posted_from_day_age():
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | 4d |
"""
    p = parse_pipe_table(text, REF)[0]
    assert p["date_posted"] == "2026-08-04"
    assert "date_estimated" not in p


def test_parse_pipe_table_derives_date_posted_from_week_age():
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | 3w |
"""
    p = parse_pipe_table(text, REF)[0]
    assert p["date_posted"] == "2026-07-18"
    assert "date_estimated" not in p


def test_parse_pipe_table_flags_month_age_as_estimated():
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | 2mo |
"""
    p = parse_pipe_table(text, REF)[0]
    assert p["date_posted"] == "2026-06-09"
    assert p["date_estimated"] is True


def test_parse_pipe_table_collapses_hour_and_minute_age_to_reference_date():
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | Hour Intern | NY, NY | <a href="https://e.com/1">Apply</a> | 18h |
| Acme | Minute Intern | NY, NY | <a href="https://e.com/2">Apply</a> | 35m |
"""
    postings = parse_pipe_table(text, REF)
    for p in postings:
        assert p["date_posted"] == "2026-08-08"
        assert "date_estimated" not in p


def test_parse_pipe_table_reads_explicit_iso_date_in_age_column_as_real():
    text = """
| Company | Role | Location | Link | Added |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | 2026-07-21 |
"""
    p = parse_pipe_table(text, REF)[0]
    assert p["date_posted"] == "2026-07-21"
    assert "date_estimated" not in p


def test_parse_pipe_table_recognizes_today_and_new():
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | Today Intern | NY, NY | <a href="https://e.com/1">Apply</a> | today |
| Acme | New Intern | NY, NY | <a href="https://e.com/2">Apply</a> | New |
"""
    for p in parse_pipe_table(text, REF):
        assert p["date_posted"] == "2026-08-08"
        assert "date_estimated" not in p


@pytest.mark.parametrize("raw", ["-", "---", "—", ""])
def test_parse_pipe_table_leaves_date_posted_unset_for_dash_placeholder(raw):
    text = f"""
| Company | Role | Location | Link | Added |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | {raw} |
"""
    p = parse_pipe_table(text, REF)[0]
    assert "date_posted" not in p
    assert "date_estimated" not in p


@pytest.mark.parametrize("raw", ["Recently", "Date unknown"])
def test_parse_pipe_table_leaves_date_posted_unset_for_unrecognized_value(raw):
    # "Recently" is observed live on zapplyjobs.md, but a cross-check against
    # a fresh live fetch (2026-08-08) showed rows marked "Recently" in an
    # older snapshot were actually 1-4 months old by the time real ages
    # appeared -- it carries no reliable elapsed-time information. Treating
    # it as "today" would fabricate precision, so it's left unrecognized
    # like any other unparseable cell (falls back to merge.py's existing
    # scrape-date + date_estimated=True behavior).
    text = f"""
| Company | Role | Location | Link | Posted |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> | {raw} |
"""
    p = parse_pipe_table(text, REF)[0]
    assert "date_posted" not in p
    assert "date_estimated" not in p


def test_parse_pipe_table_missing_trailing_age_cell_does_not_drop_row():
    # Regression guard: the date column sits last in speedyapply's and
    # sndsh404's real headers. It must not be folded into the same
    # cells-vs-header bound check used for company/role/location/link, or a
    # row that's merely missing its trailing (optional, best-effort) age
    # cell would be dropped wholesale instead of just losing its date.
    text = """
| Company | Role | Location | Link | Age |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | <a href="https://e.com/1">Apply</a> |
"""
    postings = parse_pipe_table(text, REF)
    assert len(postings) == 1
    assert postings[0]["company"] == "Acme"
    assert "date_posted" not in postings[0]


def test_parse_pipe_table_row_counts_unchanged_across_real_fixtures():
    # Wiring up date derivation must not change which rows survive parsing
    # -- only whether they carry date_posted/date_estimated.
    expected = {
        "speedyapply": 108, "sndsh404": 114, "zapplyjobs": 429, "chieler": 471,
    }
    for name, count in expected.items():
        assert len(parse_pipe_table(_fixture(f"{name}.md"), REF)) == count, name


def test_parse_pipe_table_real_fixtures_derive_dates_from_age_columns():
    # speedyapply: Anthelion Capital's Age is "0d" -> posted on REF itself.
    speedyapply = parse_pipe_table(_fixture("speedyapply.md"), REF)
    by_link = {p["link"]: p for p in speedyapply}
    anthelion = by_link["https://jobs.ashbyhq.com/anthelioncap/5e2ea37b-2369-474e-b717-c24c60976e96"]
    assert anthelion["date_posted"] == "2026-08-08"
    assert "date_estimated" not in anthelion

    # chieler: Quadrillion's Posted column is the explicit date 2026-07-24.
    chieler = parse_pipe_table(_fixture("chieler.md"), REF)
    by_link = {p["link"]: p for p in chieler}
    quadrillion = by_link[
        "https://jobs.ashbyhq.com/quadrillion-labs/a4acc44c-31ce-41a0-ab44-2500487b4d05/application?embed=true"
    ]
    assert quadrillion["date_posted"] == "2026-07-24"
    assert "date_estimated" not in quadrillion

    # sndsh404: Susquehanna's Added column is the explicit date 2026-07-21.
    sndsh404 = parse_pipe_table(_fixture("sndsh404.md"), REF)
    by_link = {p["link"]: p for p in sndsh404}
    susquehanna = by_link["https://careers.sig.com/jobs/10822"]
    assert susquehanna["date_posted"] == "2026-07-21"
    assert "date_estimated" not in susquehanna


def test_parse_pipe_table_zapplyjobs_real_fixture_covers_every_age_format():
    # The 7 rows below were hand-edited from their original "Recently"
    # placeholder (see test_..._unrecognized_value's docstring) to the
    # distinct age-column formats actually observed on a live fetch of
    # zapplyjobs/Internships-2027's README on 2026-08-08.
    postings = parse_pipe_table(_fixture("zapplyjobs.md"), REF)
    by_link = {p["link"]: p for p in postings}

    minutes = by_link["https://joinbytedance.com/search/7533045355162044690"]
    assert minutes["date_posted"] == "2026-08-08"
    assert "date_estimated" not in minutes

    hours = by_link["https://joinbytedance.com/search/7625759034518128901"]
    assert hours["date_posted"] == "2026-08-08"
    assert "date_estimated" not in hours

    one_day = by_link["https://joinbytedance.com/search/7600174040255007029"]
    assert one_day["date_posted"] == "2026-08-07"
    assert "date_estimated" not in one_day

    three_weeks = by_link[
        "https://www.google.com/about/careers/applications/jobs/results/95141459539174086"
    ]
    assert three_weeks["date_posted"] == "2026-07-18"
    assert "date_estimated" not in three_weeks

    two_months = by_link[
        "https://www.google.com/about/careers/applications/jobs/results/85564713261245126"
    ]
    assert two_months["date_posted"] == "2026-06-09"
    assert two_months["date_estimated"] is True

    seventy_one_months = by_link["https://job-boards.greenhouse.io/pdtpartners/jobs/8083292"]
    assert seventy_one_months["date_posted"] == "2020-10-08"
    assert seventy_one_months["date_estimated"] is True

    unknown = by_link["https://job-boards.greenhouse.io/pdtpartners/jobs/8077685"]
    assert "date_posted" not in unknown
    assert "date_estimated" not in unknown


def test_parse_pipe_table_decodes_entities_and_strips_zero_width():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Tencent\u200b | \u200bAlgorithm Intern - Ads &amp; Signal  (Omni)\u200b | Palo Alto, CA | <a href="https://e.com/1">Apply</a> |
"""
    p = parse_pipe_table(text, REF)[0]
    assert p["company"] == "Tencent"
    assert p["role"] == "Algorithm Intern - Ads & Signal (Omni)"


def test_parse_cvrve_json_decodes_entities_in_title():
    raw = json.dumps([{"company_name": "TikTok", "title": "ML Intern (Ads &amp; Measurement)",
                       "locations": ["San Jose, CA"], "url": "https://e.com/2",
                       "terms": ["Summer 2027"], "active": True}])
    p = parse_cvrve_json(raw, term_field="terms", term_value="Summer 2027")[0]
    assert p["role"] == "ML Intern (Ads & Measurement)"
