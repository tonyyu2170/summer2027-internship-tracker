import urllib.error

import pytest

from probe_boards import (
    apply_results,
    board_key,
    discover,
    entry_line,
    identify_board,
    probe_board,
    sniff_html,
)


@pytest.mark.parametrize("url, ats, token", [
    ("https://boards.greenhouse.io/stripe/jobs/123", "greenhouse", "stripe"),
    ("https://job-boards.greenhouse.io/freeformfuturecorp/jobs/7872198003", "greenhouse", "freeformfuturecorp"),
    ("https://boards.greenhouse.io/embed/job_app?for=anduril&token=1", "greenhouse", "anduril"),
    ("https://jobs.lever.co/MachinaLabs/40bf906a", "lever", "MachinaLabs"),
    ("https://jobs.ashbyhq.com/rivianvw.tech/e93841e0", "ashby", "rivianvw.tech"),
    ("https://jobs.ashbyhq.com/notion/3fba/application?embed=true", "ashby", "notion"),
    ("https://jobs.smartrecruiters.com/BoschGroup/744000140089589-intern", "smartrecruiters", "BoschGroup"),
    ("https://careers-gdms.icims.com/jobs/74257/x/job", "icims", "careers-gdms.icims.com"),
    ("https://apply.workable.com/securityriskadvisors/j/3B23FB7BEB/", "workable", "securityriskadvisors"),
])
def test_identify_board_tokens(url, ats, token):
    found = identify_board(url)
    assert found["ats"] == ats
    assert found["token"] == token


def test_identify_workday_strips_locale_and_keeps_vanity_host():
    found = identify_board(
        "https://globalhr.wd5.myworkdayjobs.com/fr-CA/Private_Posting_No_TMP/job/US-IA/Software-Intern_01866136")
    assert found == {"ats": "workday", "token": "globalhr/Private_Posting_No_TMP",
                     "url": "https://globalhr.wd5.myworkdayjobs.com/Private_Posting_No_TMP",
                     "tenant": "globalhr", "site": "Private_Posting_No_TMP"}


@pytest.mark.parametrize("url", [
    "https://acme.com/careers",
    "https://www.quantbot.com/careers/4340833009?gh_jid=4340833009",
    "https://ngc.wd1.myworkdayjobs.com/job/x",          # site-less short link
    "https://ghr.wd1.myworkdayjobs.com/en-us",          # lowercase locale, no site
    "https://boards.greenhouse.io/embed/job_board",     # embed without for=
    "",
])
def test_identify_board_none_for_non_board_urls(url):
    assert identify_board(url) is None


def test_sniff_html_finds_greenhouse_embed_before_plain_links():
    html = ('<a href="https://www.linkedin.com/x">x</a>'
            '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acmeinc"></script>')
    assert sniff_html(html)["token"] == "acmeinc"


def test_sniff_html_finds_workday_board_link():
    html = '<a href="https://acme.wd5.myworkdayjobs.com/en-US/External_Careers/job/x">Openings</a>'
    found = sniff_html(html)
    assert found["ats"] == "workday"
    assert (found["tenant"], found["site"]) == ("acme", "External_Careers")


def test_sniff_html_skips_bare_api_paths_and_returns_none_when_empty():
    assert sniff_html('<script src="https://api.lever.co/v0/postings/"></script>') is None
    assert sniff_html("") is None


def test_board_key_dedupes_workday_by_tenant_site_and_tokens_case_insensitively():
    a = {"ats": "workday", "company": "RTX", "url": "https://globalhr.wd5.myworkdayjobs.com/rec_rtx_ext_gateway"}
    b = {"ats": "workday", "company": "Raytheon",
         "url": "https://globalhr.wd5.myworkdayjobs.com/en-US/REC_RTX_EXT_GATEWAY/job/x"}
    assert board_key(a) == board_key(b)
    pinned = {"ats": "workday", "company": "X", "url": "https://osv-cci.wd1.myworkdayjobs.com/ext", "tenant": "osv_cci"}
    derived = {"ats": "workday", "company": "X", "url": "https://osv-cci.wd1.myworkdayjobs.com/ext"}
    assert board_key(pinned) == board_key(derived)
    assert board_key({"ats": "lever", "url": "MachinaLabs"}) == board_key({"ats": "lever", "url": "machinalabs"})
    custom = {"ats": "custom", "url": "https://jobs.smartrecruiters.com/Codeage/743999669081604"}
    assert board_key(custom) == ("smartrecruiters", "codeage")


def test_entry_line_matches_file_style():
    assert entry_line({"ats": "greenhouse", "company": "Stripe", "url": "stripe"}) == \
        "  - {ats: greenhouse, company: Stripe, url: stripe}"
    assert entry_line({"ats": "workday", "company": "Ameren",
                       "url": "https://ameren.wd1.myworkdayjobs.com/External"}) == \
        "  - {ats: workday, company: Ameren, url: 'https://ameren.wd1.myworkdayjobs.com/External'}"


def test_probe_board_counts_intern_titles_and_reports_failures():
    def get(url):
        if url.endswith("/jobs"):
            return {"jobs": [{"title": "Software Engineer Intern"}, {"title": "Staff Engineer"}]}
        return {"name": "Acme Inc"}
    out = probe_board({"ats": "greenhouse", "token": "acme", "url": "acme"}, get=get)
    assert out == {"status": "ok", "jobs": 2, "intern_jobs": 1, "name": "Acme Inc"}

    def missing(url):
        raise urllib.error.HTTPError(url, 404, "nf", {}, None)
    assert probe_board({"ats": "lever", "token": "nope", "url": "nope"}, get=missing) == \
        {"status": "fail", "error": "HTTP 404"}


def test_probe_workday_retries_hyphenated_tenant_as_underscored():
    calls = []

    def post(url, payload):
        calls.append(url)
        if "/wday/cxs/osv-cci/" in url:
            raise urllib.error.HTTPError(url, 422, "bad tenant", {}, None)
        return {"total": 3}
    found = identify_board("https://osv-cci.wd1.myworkdayjobs.com/External")
    out = probe_board(found, post=post)
    assert out["status"] == "ok" and out["pinned_tenant"] == "osv_cci"
    assert calls == ["https://osv-cci.wd1.myworkdayjobs.com/wday/cxs/osv-cci/External/jobs",
                     "https://osv-cci.wd1.myworkdayjobs.com/wday/cxs/osv_cci/External/jobs"]


def test_discover_uses_url_then_redirect_then_html():
    def never(u):
        raise AssertionError("fetch must not run for a board URL")
    direct, _ = discover("https://jobs.lever.co/acme/1", fetch=never)
    assert direct["ats"] == "lever"
    redirected, _ = discover("https://acme.com/careers",
                             fetch=lambda u: ("https://acme.wd5.myworkdayjobs.com/en-US/Ext", ""))
    assert redirected["ats"] == "workday" and redirected["site"] == "Ext"
    sniffed, _ = discover("https://acme.com/careers",
                          fetch=lambda u: (u, '<iframe src="https://jobs.ashbyhq.com/acme?embed=js">'))
    assert sniffed == {"ats": "ashby", "token": "acme", "url": "acme"}
    nothing, err = discover("https://acme.com/careers", fetch=lambda u: (u, "<p>we are hiring</p>"))
    assert nothing is None and err is None

    def blocked(u):
        raise urllib.error.HTTPError(u, 403, "blocked", {}, None)
    assert discover("https://acme.com/careers", fetch=blocked) == (None, "HTTP 403")


WATCHLIST = """# header comment
# second line

swe:
  - {ats: greenhouse, company: Stripe, url: stripe}
  - {ats: custom, company: Acme, url: 'https://acme.com/careers', verified: false}
  - {ats: custom, company: Beta, url: 'https://beta.com/jobs'}
quant:
  - {ats: custom, company: Gamma, url: 'https://gamma.com/careers'}
actuarial:
  - company: Rich Co
    provider: workday_cxs
    url: https://rich.wd1.myworkdayjobs.com/Ext
  - {ats: custom, company: Delta, url: 'https://delta.com/careers'}
"""


def _ok(kind, category, entry, found, **extra):
    return {"kind": kind, "category": category, "entry": entry, "found": found,
            "outcome": {"status": "ok", "jobs": 1, "intern_jobs": 0, "name": None, **extra}}


def test_apply_results_replaces_in_place_appends_and_dedupes():
    results = [
        _ok("discover", "swe", {"ats": "custom", "company": "Acme", "url": "https://acme.com/careers", "verified": False},
            {"ats": "lever", "token": "acme", "url": "acme"}),
        # Beta turns out to front Stripe's board: already tracked, so left alone.
        _ok("discover", "swe", {"ats": "custom", "company": "Beta", "url": "https://beta.com/jobs"},
            {"ats": "greenhouse", "token": "stripe", "url": "stripe"}),
        _ok("candidate", "quant", {"company": "Epsilon", "category": "quant", "url": "https://epsilon.com"},
            {"ats": "workday", "token": "eps-co/Ext", "url": "https://eps-co.wd1.myworkdayjobs.com/Ext",
             "tenant": "eps-co", "site": "Ext"}, pinned_tenant="eps_co"),
        _ok("candidate", "actuarial", {"company": "Zeta", "category": "actuarial", "url": "https://zeta.com"},
            {"ats": "ashby", "token": "zeta", "url": "zeta"}),
        # Same board proposed twice: second one skipped.
        _ok("candidate", "swe", {"company": "Zeta Labs", "category": "swe", "url": "https://zeta.com"},
            {"ats": "ashby", "token": "zeta", "url": "zeta"}),
        {"kind": "candidate", "category": "swe", "entry": {"company": "Dead"}, "found": None,
         "outcome": {"status": "unknown"}},
    ]
    text, summary = apply_results(results, WATCHLIST)
    assert summary == {"replaced": 1, "added": 2, "skipped": 2}
    assert text == """# header comment
# second line

swe:
  - {ats: greenhouse, company: Stripe, url: stripe}
  - {ats: lever, company: Acme, url: acme}
  - {ats: custom, company: Beta, url: 'https://beta.com/jobs'}
quant:
  - {ats: custom, company: Gamma, url: 'https://gamma.com/careers'}
  - {ats: workday, company: Epsilon, tenant: eps_co, url: 'https://eps-co.wd1.myworkdayjobs.com/Ext'}
actuarial:
  - company: Rich Co
    provider: workday_cxs
    url: https://rich.wd1.myworkdayjobs.com/Ext
  - {ats: custom, company: Delta, url: 'https://delta.com/careers'}
  - {ats: ashby, company: Zeta, url: zeta}
"""


def test_mine_candidates_keeps_us_posting_evidenced_boards_not_yet_tracked():
    from probe_boards import mine_candidates
    exports = [
        {"company_name": "Acme", "category": "Software", "title": "SWE Intern",
         "locations": ["Austin, TX"], "url": "https://job-boards.greenhouse.io/acme/jobs/1"},
        {"company_name": "Acme Inc", "category": "AI/ML/Data", "title": "ML Intern",
         "locations": ["Austin, TX"], "url": "https://boards.greenhouse.io/acme/jobs/2"},
        {"company_name": "Maple", "category": "Software", "title": "SWE Intern",
         "locations": ["Toronto, ON, Canada"], "url": "https://jobs.lever.co/maple/1"},   # non-US
        {"company_name": "Stripe", "category": "Software", "title": "SWE Intern",
         "locations": ["Seattle, WA"], "url": "https://boards.greenhouse.io/stripe/jobs/3"},  # already tracked
        {"company_name": "Consultco", "category": "Consulting", "title": "Analyst Intern",
         "locations": ["New York, NY"], "url": "https://jobs.lever.co/consultco/1"},   # DROP category
        {"company_name": "Tesla", "category": "Hardware", "title": "EE Intern",
         "locations": ["Fremont, CA"], "url": "https://www.tesla.com/careers/search/job/1"},  # no board
    ]
    data_rows = [("quant", {"company": "Epsilon", "link": "https://eps.wd5.myworkdayjobs.com/en-US/Ext/job/x/y_R1"})]
    known = {("greenhouse", "stripe")}
    assert mine_candidates(exports, data_rows, known) == [
        {"company": "Acme", "category": "swe", "url": "https://boards.greenhouse.io/acme", "postings": 2},
        {"company": "Epsilon", "category": "quant", "url": "https://eps.wd5.myworkdayjobs.com/Ext", "postings": 1},
    ]
