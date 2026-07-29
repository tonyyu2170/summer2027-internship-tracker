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


def test_canonicalize_location_keeps_a_source_owned_multi_location_posting():
    assert canonicalize_location("New York, NY / Boston, Massachusetts") == \
        "New York, NY / Boston, MA"
    assert canonicalize_location("Remote - EMEA") is None
    assert canonicalize_location("Singapore") is None


def test_is_us_location():
    assert is_us_location("Chicago, IL") is True
    assert is_us_location("Toronto, ON") is False


def test_canonicalize_location_remote_us_city_substring_not_false_positive():
    assert canonicalize_location("Remote - Milwaukee") == "Remote (US)"
    assert canonicalize_location("Remote - Fremont") == "Remote (US)"
    assert canonicalize_location("Remote (US) - Dayton") == "Remote (US)"
    assert canonicalize_location("Remote - Canton, OH") == "Remote (US)"
    assert canonicalize_location("Remote - UK") is None
    assert canonicalize_location("Remote - ON") is None


# Identity params: distinguish genuinely different postings (different req id,
# different tenant, different job) and must never be stripped as "tracking".
IDENTITY_PARAMS = [
    ("gh_jid", "5987663004"),   # Greenhouse req id on a company's own careers page
    ("token", "8489233002"),    # Greenhouse job_app embed job id
    ("for", "aquaticcapitalmanagement"),
    ("jobCode", "R12345"), ("jobName", "swe-intern"), ("jobId", "12345"),
    ("req", "R99"), ("career_job_req_id", "3507"),
    ("company", "hcollp"),      # SAP SuccessFactors tenant; path is bare /career
    ("cid", "cf1a92f4"),        # ADP client id; generic app-shell path
]


def test_identity_params_are_never_stripped():
    for k, v in IDENTITY_PARAMS:
        assert normalize_link(f"https://x.com/careers?{k}={v}") \
            != normalize_link("https://x.com/careers"), f"{k} must stay distinguishing"


def test_distinct_jobs_on_one_page_stay_distinct():
    # regression: 11 Jump Trading roles must not collapse
    assert normalize_link("https://www.jumptrading.com/hr/job?gh_jid=111") \
        != normalize_link("https://www.jumptrading.com/hr/job?gh_jid=222")


def test_jr_id_is_stripped():
    # Simplify/vanshb03 referral token
    assert normalize_link("https://boards.greenhouse.io/fiserv/jobs/123?jr_id=69fa") \
        == normalize_link("https://boards.greenhouse.io/fiserv/jobs/123")


def test_embed_is_stripped():
    # Ashby iframe flag; only value seen in data is "true"
    assert normalize_link("https://jobs.ashbyhq.com/circleback/job/1?embed=true") \
        == normalize_link("https://jobs.ashbyhq.com/circleback/job/1")


def test_iis_lang_mode_are_stripped():
    # LinkedIn inbound-source tag, display language, and apply mode (job id is
    # in the path) — all load-bearing for Susquehanna
    assert normalize_link("https://careers.sig.com/job/1?iis=LinkedIn&lang=en&mode=apply") \
        == normalize_link("https://careers.sig.com/job/1")


def test_identity_survives_alongside_tracking():
    assert normalize_link("https://boards.greenhouse.io/x/jobs/1?gh_jid=598&jr_id=69fa") \
        == normalize_link("https://boards.greenhouse.io/x/jobs/1?gh_jid=598")
