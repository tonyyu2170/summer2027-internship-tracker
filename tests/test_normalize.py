from normalize import normalize_link, normalize_company, canonicalize_location, is_us_location


def test_normalize_link_strips_tracking_and_trailing_slash():
    a = normalize_link("HTTPS://Boards.Greenhouse.io/janestreet/jobs/123/?utm_source=x&gh_src=y")
    b = normalize_link("https://boards.greenhouse.io/janestreet/jobs/123")
    assert a == b == "https://boards.greenhouse.io/janestreet/jobs/123"


def test_normalize_link_keeps_meaningful_query_sorted():
    assert normalize_link("https://x.com/j?b=2&a=1&utm_term=z") == "https://x.com/j?a=1&b=2"


def test_normalize_company_strips_legal_suffix():
    assert normalize_company("Jane Street Group, LLC") == "jane street"
    assert normalize_company("Stripe, Inc.") == "stripe"
    assert normalize_company("  Optiver ") == "optiver"


def test_canonicalize_location_us_forms():
    assert canonicalize_location("New York, NY") == "New York, NY"
    assert canonicalize_location("Austin, Texas") == "Austin, TX"
    assert canonicalize_location("Remote") == "Remote (US)"


def test_canonicalize_location_rejects_non_us():
    assert canonicalize_location("London, UK") is None
    assert canonicalize_location("Remote - EMEA") is None
    assert canonicalize_location("Singapore") is None


def test_is_us_location():
    assert is_us_location("Chicago, IL") is True
    assert is_us_location("Toronto, ON") is False
