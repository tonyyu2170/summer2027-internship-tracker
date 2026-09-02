import re
import datetime as dt
import yaml
from pathlib import Path
from generate_readme import render, _format_opens, CATEGORIES, OPPORTUNITY_KINDS


def _write(data_dir: Path, stem: str, rows: list):
    (data_dir / f"{stem}.yaml").write_text(yaml.safe_dump(rows, sort_keys=False))


def _row(**kw):
    base = {
        "id": "x", "company": "Jane Street", "role": "Quant Trading Intern",
        "location": "New York, NY", "link": "https://x.com/j",
        "date_posted": "2026-07-15", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["greenhouse"], "date_added": "2026-07-15",
        "last_verified": "2026-07-15", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def _empty_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    for stem in ("swe", "quant", "data_science", "ai_ml", "hardware",
                 "actuarial"):
        _write(d, stem, [])
    return d


def _write_opp(data_dir: Path, stem: str, rows: list):
    opp_dir = data_dir / "opportunities"
    opp_dir.mkdir(exist_ok=True)
    (opp_dir / f"{stem}.yaml").write_text(yaml.safe_dump(rows, sort_keys=False))


def _opp_row(**kw):
    base = {
        "id": "nvidia-ignite", "name": "NVIDIA Ignite", "org": "NVIDIA",
        "kind": "program", "category": "ai_ml",
        "url": "https://example.com/ignite", "apply_url": None,
        "status": "open", "opens": None, "closes": None,
        "eligibility": "Sophomores and juniors", "location": "Santa Clara, CA",
        "cycle": "Summer 2027", "sources": ["llm_discovery"],
        "date_added": "2026-07-28", "last_checked": "2026-07-28", "notes": None,
    }
    base.update(kw)
    return base


def test_render_writes_toc_and_all_headings(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "## Software Engineering" in text
    assert "## Actuarial" in text
    assert "## Consulting" not in text
    assert "## Investment Banking" not in text
    assert "[Quantitative Finance](#quantitative-finance)" in text


def test_every_section_has_a_back_to_top_link(tmp_path):
    # One per H2 except "## Contents", which sits a few lines from the top.
    # Covers the populated and both empty-section branches: swe has a row,
    # quant renders "_No open roles._", the rest "_No roles tracked yet._",
    # and the three opportunity kinds render their own empty branch.
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row()])
    _write(data_dir, "quant", [_row(id="c", link="https://x.com/c",
                                    status="closed")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    link = "[⬆ Back to top](#summer-2027-internship-tracker)"
    sections = text.split("\n## ")[1:]
    assert sections[0].split("\n", 1)[0] == "Contents"
    assert link not in sections[0]
    for section in sections[1:]:
        assert link in section, f"missing back-to-top in: {section[:40]!r}"
    assert text.count(link) == len(sections) - 1 == len(CATEGORIES) + len(OPPORTUNITY_KINDS)


def test_back_to_top_target_matches_the_rendered_h1(tmp_path):
    # The link is only "automatic" if its target is the anchor GitHub
    # generates for the H1 — guard the two against drifting apart.
    data_dir = _empty_data_dir(tmp_path)
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    h1 = text.split("\n", 1)[0].removeprefix("# ")
    target = h1.lower().replace(" ", "-").replace("/", "")
    assert f"[⬆ Back to top](#{target})" in text


def test_quant_table_has_track_column_and_row(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "quant", [_row(track="Trading")])
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "| Track |" not in text     # track stays in data, not rendered
    assert "[Apply](<https://x.com/j>)" in text


def test_closed_rows_are_not_rendered(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(status="closed")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "Jane Street" not in text
    swe_section = text.split("## Software Engineering")[1].split("## ")[0]
    assert "_No open roles._" in swe_section


def test_mixed_category_renders_only_open_rows(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(id="a", company="OpenCo"),
        _row(id="b", company="ClosedCo", link="https://x.com/k", status="closed"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "OpenCo" in text
    assert "ClosedCo" not in text


def test_status_and_last_verified_columns_dropped(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row()])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "| Company | Role | Link | Date Posted | Term | Degree |" in text
    assert "Status |" not in text
    assert "Last Verified" not in text


def test_dup_marker_renders_in_role_cell(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(possible_duplicate_of="other-row-id")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "Quant Trading Intern ⚠️dup?(other-row-id)" in text


def test_rows_sorted_newest_first(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(company="Older", date_posted="2026-01-01"),
        _row(company="Newer", date_posted="2026-09-01"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert text.index("Newer") < text.index("Older")


def test_same_date_ties_break_newest_scraped_first(tmp_path):
    # merge.py always appends new rows to the end of a category's list, so
    # list position is a proxy for scrape recency when date_posted ties.
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(id="a", company="ScrapedMorning", link="https://x.com/a",
             date_posted="2026-08-10"),
        _row(id="b", company="ScrapedEvening", link="https://x.com/b",
             date_posted="2026-08-10"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert text.index("ScrapedEvening") < text.index("ScrapedMorning")


def test_estimated_date_is_marked_without_changing_sort_order(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(company="Older estimate", date_posted="2026-01-01", date_estimated=True),
        _row(company="Newer actual", date_posted="2026-09-01"),
    ])
    out = tmp_path / "README.md"
    render(data_dir, out, {"new": 2, "closed": 1})
    text = out.read_text()
    assert "~2026-01-01" in text
    assert text.index("Newer actual") < text.index("Older estimate")
    assert "Last scrape: +2 new, 1 closed." in text
    assert "Last Verified" not in text


def test_pipe_newline_and_paren_in_free_text_dont_corrupt_table(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(
        role="Software Engineer | Backend",
        company="Weird\nCo",
        link="https://example.com/apply?ref=(promo)",
    )])
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    row_lines = [l for l in text.splitlines() if l.startswith("| Weird")]
    assert len(row_lines) == 1
    row_line = row_lines[0]
    assert "\n" not in row_line
    assert "Software Engineer \\| Backend" in row_line
    # split on unescaped pipes only: an escaped "\|" must not create a new column
    cells = [c for c in re.split(r"(?<!\\)\|", row_line) if c.strip()]
    assert len(cells) == 6
    assert "[Apply](<https://example.com/apply?ref=(promo)>)" in text


# ---------------------------------------------------------------------------
# Programs / Research / Competitions sections
# ---------------------------------------------------------------------------

# _format_opens — direct unit tests for the defensive coercion seams a
# hand-edited sources/programs.yaml can trigger (PyYAML parses an unquoted
# date/int scalar into a non-str object).

def test_format_opens_year_month_string():
    assert _format_opens("2026-09") == "Sep 2026"


def test_format_opens_full_date_string():
    assert _format_opens("2026-09-15") == "Sep 15, 2026"


def test_format_opens_none_is_none():
    assert _format_opens(None) is None


def test_format_opens_date_object():
    assert _format_opens(dt.date(2026, 9, 15)) == "Sep 15, 2026"


def test_format_opens_datetime_object_truncates_time_of_day():
    assert _format_opens(dt.datetime(2026, 9, 15, 13, 30, 0)) == "Sep 15, 2026"


def test_format_opens_bare_int_does_not_raise():
    # An unquoted 'opens: 2026' YAML scalar parses as an int, not a str or
    # date — must fall back to the raw value rather than crashing the
    # whole render on .isoformat().
    assert _format_opens(2026) == "2026"


def test_opportunity_sections_render_headings_and_toc(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "## Programs" in text
    assert "## Research" in text
    assert "## Competitions" in text
    assert "[Programs](#programs)" in text
    assert "[Research](#research)" in text
    assert "[Competitions](#competitions)" in text


def test_empty_opportunity_sections_render_without_broken_table(tmp_path):
    # _empty_data_dir doesn't create a data/opportunities/ dir at all —
    # covers both "no rows" and "dir doesn't exist" in one pass.
    data_dir = _empty_data_dir(tmp_path)
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert text.count("_No opportunities tracked yet._") == 3
    assert "| Program |" not in text


def test_opp_status_badge_open(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(status="open")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "🟢 **Open**" in text


def test_opp_status_badge_upcoming_with_opens_date(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(status="upcoming", opens="2026-09")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "⏳ `opens Sep 2026`" in text


def test_opp_status_badge_upcoming_full_date_is_human_formatted(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(status="upcoming", opens="2026-09-15")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "⏳ `opens Sep 15, 2026`" in text


def test_opp_status_badge_upcoming_null_opens_is_bare(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(status="upcoming", opens=None)])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    row_line = next(l for l in text.splitlines() if l.startswith("| NVIDIA Ignite"))
    assert row_line.endswith("⏳ Upcoming |")
    assert "`opens" not in row_line


def test_opp_status_badge_closed(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(status="closed")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "🔒 Closed" in text


def test_opp_status_badge_unknown(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(status="unknown")])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "⚪ Unknown" in text


def test_opp_rows_sorted_open_upcoming_unknown_closed(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [
        _opp_row(id="c", name="Closed Program", status="closed"),
        _opp_row(id="u2", name="Upcoming No Date", status="upcoming", opens=None),
        _opp_row(id="k", name="Unknown Program", status="unknown"),
        _opp_row(id="u1", name="Upcoming Sept", status="upcoming", opens="2026-09"),
        _opp_row(id="o", name="Open Program", status="open"),
        _opp_row(id="u0", name="Upcoming August", status="upcoming", opens="2026-08"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    order = [text.index(name) for name in (
        "Open Program", "Upcoming August", "Upcoming Sept",
        "Upcoming No Date", "Unknown Program", "Closed Program",
    )]
    assert order == sorted(order)


def test_opp_category_null_renders_em_dash_not_none(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(category=None)])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "None" not in text
    row_line = next(l for l in text.splitlines() if l.startswith("| NVIDIA Ignite"))
    assert "| — |" in row_line


def test_opp_link_prefers_apply_url_over_url(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(
        url="https://example.com/page", apply_url="https://example.com/apply",
    )])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "[Apply](<https://example.com/apply>)" in text
    assert "[Apply](<https://example.com/page>)" not in text


def test_opp_link_falls_back_to_url_when_no_apply_url(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "programs", [_opp_row(
        url="https://example.com/page", apply_url=None,
    )])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "[Apply](<https://example.com/page>)" in text


def test_opp_pipe_and_paren_in_free_text_dont_corrupt_table(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write_opp(data_dir, "research", [_opp_row(
        name="Summer Program | Track B",
        url="https://example.com/apply?ref=(promo)",
    )])
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    row_lines = [l for l in text.splitlines() if l.startswith("| Summer")]
    assert len(row_lines) == 1
    row_line = row_lines[0]
    assert "Summer Program \\| Track B" in row_line
    cells = [c for c in re.split(r"(?<!\\)\|", row_line) if c.strip()]
    assert len(cells) == 7          # opportunity tables keep their own columns
    assert "[Apply](<https://example.com/apply?ref=(promo)>)" in text


def test_opp_row_missing_both_urls_renders_em_dash_not_dead_link(tmp_path):
    # Mirrors a hand-corrupted row check_programs.py tolerates keeping as-is
    # (test_check_kind_existing_row_missing_id_is_not_deleted): no 'url' key
    # at all. render() reads data/opportunities/*.yaml directly and must not
    # print Python's None into a public README link, nor render a
    # live-looking [Apply](<#>) link to nowhere.
    data_dir = _empty_data_dir(tmp_path)
    row = _opp_row()
    del row["url"]
    row["apply_url"] = None
    _write_opp(data_dir, "programs", [row])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert "(<None>)" not in text
    assert "[Apply](<#>)" not in text
    row_line = next(l for l in text.splitlines() if l.startswith("| NVIDIA Ignite"))
    cells = [c.strip() for c in re.split(r"(?<!\\)\|", row_line) if c.strip()]
    assert cells[5] == "—"   # Link column: Program/Org/Category/Eligibility/Opens/Link/Status


def test_legend_mentions_opportunity_status_badges(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    legend = next(l for l in text.splitlines() if l.startswith("**Legend**"))
    assert "⏳" in legend
    assert "⚪ Unknown" in legend


def test_contents_lists_each_category_with_its_open_count(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    rows = [_row(id="a", link="https://x.com/a", status="open"),
            _row(id="b", link="https://x.com/b", status="closed"),
            _row(id="c", link="https://x.com/c", status="open")]
    for stem in ("swe", "quant", "data_science", "ai_ml", "hardware", "actuarial"):
        (data_dir / f"{stem}.yaml").write_text(yaml.safe_dump(rows if stem == "swe" else []))
    readme = tmp_path / "README.md"
    render(data_dir, readme)
    text = readme.read_text()
    assert "- [Software Engineering](#software-engineering) (2 open)" in text
    assert "- [Actuarial](#actuarial) (0 open)" in text
