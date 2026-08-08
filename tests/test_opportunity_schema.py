from opportunity_schema import OPPORTUNITY_SCHEMA, validate_opportunity

VALID = {
    "id": "nvidia-ignite",
    "name": "NVIDIA Ignite",
    "org": "NVIDIA",
    "kind": "program",
    "category": "ai_ml",
    "url": "https://example.com/nvidia-ignite",
    "apply_url": "https://example.com/nvidia-ignite/apply",
    "status": "open",
    "opens": "2026-09",
    "closes": None,
    "eligibility": "Sophomores and juniors, US-based",
    "location": "Santa Clara, CA",
    "cycle": "Summer 2027",
    "sources": ["llm_discovery"],
    "date_added": "2026-07-28",
    "last_checked": "2026-07-28",
    "notes": None,
}


def test_valid_row_has_no_errors():
    assert validate_opportunity(VALID) == []


def test_missing_required_field_is_error():
    row = {k: v for k, v in VALID.items() if k != "org"}
    assert any("org" in e for e in validate_opportunity(row))


def test_bad_kind_enum_is_error():
    assert validate_opportunity({**VALID, "kind": "internship"})


def test_bad_category_enum_is_error():
    errors = validate_opportunity({**VALID, "category": "consulting"})
    assert errors
    assert any("category" in e for e in errors)


def test_category_null_is_valid():
    assert validate_opportunity({**VALID, "category": None}) == []


def test_category_enum_matches_tracker_job_categories():
    from generate_readme import CATEGORIES
    job_categories = {stem for stem, _title, _is_quant in CATEGORIES}
    schema_categories = set(OPPORTUNITY_SCHEMA["properties"]["category"]["enum"]) - {None}
    assert schema_categories == job_categories


def test_bad_status_enum_is_error():
    assert validate_opportunity({**VALID, "status": "maybe"})


def test_status_upcoming_and_unknown_are_valid():
    assert validate_opportunity({**VALID, "status": "upcoming"}) == []
    assert validate_opportunity({**VALID, "status": "unknown"}) == []


def test_bad_opens_format_is_error():
    assert validate_opportunity({**VALID, "opens": "09/2026"})


def test_opens_year_month_is_valid():
    assert validate_opportunity({**VALID, "opens": "2026-09"}) == []


def test_opens_full_date_is_valid():
    assert validate_opportunity({**VALID, "opens": "2026-09-15"}) == []


def test_opens_null_is_valid():
    assert validate_opportunity({**VALID, "opens": None}) == []


def test_closes_null_is_valid():
    assert validate_opportunity({**VALID, "closes": None}) == []


def test_nullable_fields_null_is_valid():
    row = {**VALID, "apply_url": None, "location": None, "cycle": None, "notes": None}
    assert validate_opportunity(row) == []


def test_eligibility_is_required_and_not_nullable():
    assert validate_opportunity({**VALID, "eligibility": None})


def test_sources_empty_list_is_error():
    assert validate_opportunity({**VALID, "sources": []})


def test_bad_sources_item_error_has_readable_path():
    errors = validate_opportunity({**VALID, "sources": [123]})
    assert any("sources/0" in e for e in errors)


def test_bad_date_added_format_is_error():
    assert validate_opportunity({**VALID, "date_added": "07/28/2026"})


def test_extra_property_is_error():
    assert validate_opportunity({**VALID, "unexpected_field": "x"})
