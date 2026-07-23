from schema import validate_row

VALID = {
    "id": "jane-street-quant-trading-intern-a1b2c3",
    "company": "Jane Street",
    "role": "Quantitative Trading Intern",
    "track": "Trading",
    "location": "New York, NY",
    "link": "https://boards.greenhouse.io/janestreet/jobs/123",
    "date_posted": "2026-07-15",
    "term": "Summer 2027",
    "degree": ["BS", "MS"],
    "status": "open",
    "sources": ["greenhouse"],
    "date_added": "2026-07-22",
    "last_verified": "2026-07-22",
    "possible_duplicate_of": None,
}


def test_valid_row_has_no_errors():
    assert validate_row(VALID) == []


def test_missing_required_field_is_error():
    row = {k: v for k, v in VALID.items() if k != "company"}
    assert any("company" in e for e in validate_row(row))


def test_bad_status_enum_is_error():
    assert validate_row({**VALID, "status": "maybe"})


def test_bad_date_format_is_error():
    assert validate_row({**VALID, "date_posted": "07/15/2026"})


def test_track_is_optional():
    row = {k: v for k, v in VALID.items() if k != "track"}
    assert validate_row(row) == []
