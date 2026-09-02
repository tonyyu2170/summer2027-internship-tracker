from normalize import normalize_link, normalize_company, canonicalize_location, is_us_location


def test_normalize_link_strips_tracking_and_trailing_slash():
    a = normalize_link("HTTPS://Boards.Greenhouse.io/janestreet/jobs/123/?utm_source=x&gh_src=y")
    b = normalize_link("https://boards.greenhouse.io/janestreet/jobs/123")
    assert a == b == "https://job-boards.greenhouse.io/janestreet/jobs/123"


def test_normalize_link_keeps_meaningful_query_sorted():
    assert normalize_link("https://x.com/j?b=2&a=1&utm_term=z") == "https://x.com/j?a=1&b=2"


def test_normalize_link_collapses_bytedance_tiktok_search_variants():
    # 2026-08-09: trackers emit the same ByteDance/TikTok requisition as both
    # a /search/<id> link and a detail-page link; 14 duplicate pairs found.
    assert normalize_link("https://joinbytedance.com/search/7671147251943213317") == \
        normalize_link("https://jobs.bytedance.com/en/position/7671147251943213317/detail")
    assert normalize_link("https://lifeattiktok.com/search/7633668456744503557") == \
        normalize_link("https://lifeattiktok.com/position/7633668456744503557")
    # non-numeric or other-host /search/ paths are untouched
    assert normalize_link("https://example.com/search/123") == "https://example.com/search/123"
    assert normalize_link("https://joinbytedance.com/search/abc") == \
        "https://joinbytedance.com/search/abc"


def test_normalize_link_workday_locale_segment_is_stripped():
    a = normalize_link("https://astreya.wd5.myworkdayjobs.com/en-US/life-at-astreya-opportunities/job/Remote-CA/AI-Infrastructure-DC-Design-Intern_R0015746")
    b = normalize_link("https://astreya.wd5.myworkdayjobs.com/life-at-astreya-opportunities/job/Remote-CA/AI-Infrastructure-DC-Design-Intern_R0015746")
    assert a == b


def test_normalize_link_workday_site_segment_case_folds():
    a = normalize_link("https://interdigital.wd1.myworkdayjobs.com/InterDigital/job/PA/Intern_REQ26-1093")
    b = normalize_link("https://interdigital.wd1.myworkdayjobs.com/interdigital/job/PA/Intern_REQ26-1093")
    assert a == b
    # requisition id and job-title segments keep their case
    assert a.endswith("/job/PA/Intern_REQ26-1093")


def test_normalize_link_locale_segment_untouched_on_non_workday_hosts():
    assert normalize_link("https://example.com/en-US/jobs/1") == \
        "https://example.com/en-US/jobs/1"


def test_normalize_link_lever_apply_suffix_is_stripped():
    a = normalize_link("https://jobs.lever.co/plusai/8b1f-uuid/apply")
    b = normalize_link("https://jobs.lever.co/plusai/8b1f-uuid")
    assert a == b
    # '/apply' elsewhere in the path or on other hosts is untouched
    assert normalize_link("https://x.com/careers/apply") == "https://x.com/careers/apply"


def test_normalize_link_ashby_application_suffix_is_stripped():
    a = normalize_link("https://jobs.ashbyhq.com/pika/e135-uuid/application?embed=true")
    b = normalize_link("https://jobs.ashbyhq.com/pika/e135-uuid")
    assert a == b


def test_normalize_link_smartrecruiters_title_slug_is_stripped():
    # The API returns the slugged form, trackers emit the bare id.
    a = normalize_link("https://jobs.smartrecruiters.com/WesternDigital/"
                       "744000138727213-summer-2027-software-engineering-internship")
    b = normalize_link("https://jobs.smartrecruiters.com/WesternDigital/744000138727213")
    assert a == b == "https://jobs.smartrecruiters.com/WesternDigital/744000138727213"
    # A different numeric id is a different posting, slug or not.
    assert normalize_link(
        "https://jobs.smartrecruiters.com/WesternDigital/744000140949875") != b
    # Non-numeric ids and other hosts are untouched.
    assert normalize_link("https://jobs.smartrecruiters.com/Acme/oneclick-ui-x9") == \
        "https://jobs.smartrecruiters.com/Acme/oneclick-ui-x9"


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


def test_canonicalize_location_country_code_ca_is_not_california():
    assert canonicalize_location("Milton, Ontario, CA") is None
    assert canonicalize_location("Vancouver, BC, CA") is None
    assert canonicalize_location("Montreal, Quebec, CA") is None
    # Middle parts only: US shapes with an extra region segment still resolve.
    assert canonicalize_location("Irvine, Orange County, CA") == "Irvine, CA"
    assert canonicalize_location("Ontario, CA") == "Ontario, CA"


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


def test_normalize_link_collapses_greenhouse_host_embed_and_redundant_gh_jid():
    # 2026-09-01 data audit: same-req rows split across the legacy
    # boards. host, the embed/job_app form, a redundant gh_jid and www.
    canon = normalize_link("https://job-boards.greenhouse.io/point72/jobs/7297613002")
    assert normalize_link(
        "https://boards.greenhouse.io/point72/jobs/7297613002?gh_jid=7297613002") == canon
    assert normalize_link(
        "https://boards.greenhouse.io/embed/job_app?for=point72&jr_id=6a07&token=7297613002") == canon
    # an embed with no board token can't be mapped and stays distinct
    assert normalize_link("https://boards.greenhouse.io/embed/job_app?token=8049938") != canon
    # a gh_jid that is NOT the path's own id still distinguishes reqs
    assert normalize_link("https://boards.greenhouse.io/x/jobs/1?gh_jid=7964062") \
        != normalize_link("https://boards.greenhouse.io/x/jobs/1?gh_jid=8059837")
    assert normalize_link("https://akunacapital.com/careers/job/8021481/?gh_jid=8021481") \
        == normalize_link("https://www.akunacapital.com/careers/job/8021481/")
    assert normalize_link("https://x.com/careers/job/1234/?gh_jid=123") \
        != normalize_link("https://x.com/careers/job/1234")


def test_normalize_link_strips_smartrecruiters_oga_and_microsoft_search_form():
    assert normalize_link(
        "https://jobs.smartrecruiters.com/BoschGroup/744000140317669-adas-intern?oga=true") \
        == normalize_link("https://jobs.smartrecruiters.com/BoschGroup/744000140317669")
    assert normalize_link(
        "https://apply.careers.microsoft.com/careers?query=intern&start=0&pid=1970393556922929") \
        == normalize_link("https://apply.careers.microsoft.com/careers/job/1970393556922929")
