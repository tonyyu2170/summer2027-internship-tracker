from datetime import date

import yaml

from generate_dashboard import render_dashboard, summarize

ROWS = [
    {"company": "Acme", "role": "SWE Intern", "link": "https://x/1", "date_posted": "2026-08-31",
     "status": "open", "degree": ["BS", "MS"], "sources": ["github_tracker", "github_tracker:simplifyjobs"],
     "category": "swe"},
    {"company": "Acme", "role": "Quant Intern", "link": "https://x/2", "date_posted": "2026-08-20",
     "status": "open", "degree": ["BS"], "sources": ["company:acme"], "category": "quant",
     "date_estimated": True},
    {"company": "Beta", "role": "Old Intern", "link": "https://x/3", "date_posted": "2026-05-01",
     "status": "closed", "degree": ["PhD"], "sources": ["github_tracker:chieler"], "category": "swe"},
]
BOARDS = {"swe": [{"ats": "greenhouse", "company": "Acme", "url": "acme"},
                  {"ats": "ashby", "company": "Dead", "url": "dead", "verified": False}],
          "quant": [{"ats": "custom", "company": "Gamma", "url": "https://gamma.com/careers"}]}


def test_summarize_counts_open_rows_weeks_and_boards():
    s = summarize(ROWS, BOARDS, today=date(2026, 9, 2))
    assert (s["open"], s["closed"], s["companies"]) == (2, 1, 1)
    assert (s["last7"], s["last30"]) == (1, 2)
    assert s["by_cat"][:2] == [("swe", 1), ("quant", 1)]
    assert s["boards"] == 2 and s["boards_unverified"] == 1
    assert s["ats"] == [("greenhouse", 1), ("custom", 1)]
    assert s["sources"] == [("simplifyjobs", 1), ("company:acme", 1)]
    assert s["degrees"] == [("BS", 2), ("MS", 1), ("PhD", 0)]
    assert [r["link"] for r in s["newest"]] == ["https://x/1", "https://x/2"]
    weeks = dict(s["weeks"])
    assert weeks[date(2026, 8, 31)]["swe"] == 1 and weeks[date(2026, 8, 17)]["quant"] == 1
    assert s["estimated_share"] == 33


def test_render_dashboard_writes_a_self_contained_page(tmp_path):
    data, sources = tmp_path / "data", tmp_path / "sources"
    data.mkdir(), sources.mkdir()
    for cat in ("swe", "quant"):
        (data / f"{cat}.yaml").write_text(yaml.safe_dump(
            [{k: v for k, v in r.items() if k != "category"} for r in ROWS if r["category"] == cat]))
    (sources / "companies.yaml").write_text(yaml.safe_dump(BOARDS))
    (sources / "scrape_state.yaml").write_text(yaml.safe_dump({"_last_run": {"new": 3, "closed": 1}}))
    page = render_dashboard(data, sources, today=date(2026, 9, 2))
    assert page.startswith("<title>Summer 2027 Internship Radar</title>")
    assert "Last scrape: +3 new, 1 closed." in page
    assert 'href="https://x/1"' in page and "https://x/3" not in page   # closed row not listed
    assert "<script src=" not in page                                     # no external libraries
    assert page.count("<svg") == 6 and "data-tip=" in page
    assert 'data-cat="swe"' in page and 'class="chip" data-cat="quant"' in page   # filterable
    assert 'id="q"' in page and "prefers-reduced-motion" in page
