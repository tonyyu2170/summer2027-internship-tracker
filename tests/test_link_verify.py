from link_verify import (
    family,
    probe_url,
    page_title,
    clean_role,
    title_term_year,
    bad_term_markers,
    evaluate,
    suppression_links,
    over_cap,
)


def test_family_and_probe_url_bytedance_tiktok_map_to_search_form():
    assert probe_url("https://jobs.bytedance.com/en/position/7671147251943213317/detail") == \
        "https://joinbytedance.com/search/7671147251943213317"
    assert probe_url("https://lifeattiktok.com/position/7633668456744503557") == \
        "https://lifeattiktok.com/search/7633668456744503557"
    assert probe_url("https://apply.workable.com/acme/j/AB12CD34EF/") == \
        "https://apply.workable.com/api/v2/accounts/acme/jobs/AB12CD34EF"
    assert family("https://job-boards.greenhouse.io/x/jobs/1") == "generic"
    assert probe_url("https://job-boards.greenhouse.io/x/jobs/1") == \
        "https://job-boards.greenhouse.io/x/jobs/1"


def test_bad_term_markers_flags_explicit_seasons_only():
    bad, has27 = bad_term_markers("Join us for Fall 2026! Apply now.")
    assert bad and not has27
    # JSON metadata must NOT match — live false-positive class (Meta/Corpay).
    bad, _ = bad_term_markers('{"start_time":"2025-04-01","x":1}')
    assert bad == []
    bad, _ = bad_term_markers('StartTimestampUTC":"2026-01-02')
    assert bad == []
    # 2027 presence anywhere is surfaced so callers can decline to flag.
    _, has27 = bad_term_markers("Fall 2026 cohort or Summer 2027 cohort")
    assert has27


def test_evaluate_wrong_term_requires_no_2027_anywhere():
    assert evaluate("https://x.com/j", 200, "Starts Fall 2026 only", "SWE Intern")["verdict"] == "wrong_term"
    assert evaluate("https://x.com/j", 200, "Fall 2026 and Summer 2027 tracks", "SWE Intern")["verdict"] == "ok"
    # stored role naming 2027 also blocks the flag
    assert evaluate("https://x.com/j", 200, "Starts Fall 2026", "SWE Intern - Summer 2027")["verdict"] == "ok"


def test_evaluate_bytedance_title_rules():
    link = "https://joinbytedance.com/search/7665037087551129909"
    html = "<title>Developer Advocacy Project Intern (PICO) - 2026 Start | Jobs</title>"
    assert evaluate(link, 200, html, "Developer Advocacy Project Intern (PI...")["verdict"] == "wrong_term"
    html27 = "<title>SWE Intern (Networks) - 2027 Summer (BS/MS)</title>"
    out = evaluate(link, 200, html27, "SWE Intern (Netw...")
    assert out["verdict"] == "ok"
    assert out["new_role"] == "SWE Intern (Networks) - 2027 Summer"
    # missing SSR title is ambiguous, never dead
    assert evaluate(link, 200, "<html></html>", "x")["verdict"] == "ambiguous"


def test_evaluate_dead_only_on_hard_404_410():
    assert evaluate("https://x.com/j", 404, "", "r")["verdict"] == "dead"
    assert evaluate("https://x.com/j", 410, "", "r")["verdict"] == "dead"
    assert evaluate("https://x.com/j", 403, "", "r")["verdict"] == "ok"


def test_suppression_links_cover_both_forms_for_bytedance_tiktok():
    assert suppression_links("https://lifeattiktok.com/position/763366845674450001") == [
        "https://joinbytedance.com/search/763366845674450001",
        "https://lifeattiktok.com/search/763366845674450001",
    ]
    assert suppression_links("https://job-boards.greenhouse.io/x/jobs/1") == [
        "https://job-boards.greenhouse.io/x/jobs/1"
    ]


def test_title_helpers():
    assert page_title("<title>  A  B | Careers at C</title>") == "A B"
    assert page_title("<title>Ads &amp; Signal Intern | TikTok</title>") == "Ads & Signal Intern"
    assert clean_role("ML Intern - 2027 Summer (BS/MS)") == "ML Intern - 2027 Summer"
    assert title_term_year("SWE Intern - 2026 Start") == "2026"
    assert title_term_year("SWE Intern - 2027 Summer") == "2027"
    assert title_term_year("SWE Intern") is None


def test_over_cap_threshold():
    assert not over_cap(5, 10)
    assert over_cap(6, 10)
    assert not over_cap(20, 200)
    assert over_cap(41, 200)
