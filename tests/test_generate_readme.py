import re
import yaml
from pathlib import Path
from generate_readme import render


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


def test_quant_table_has_track_column_and_row(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "quant", [_row(track="Trading")])
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "| Track |" in text
    assert "Trading" in text
    assert "[Apply](<https://x.com/j>)" in text


def test_closed_row_renders_lock(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(status="closed")])
    render(data_dir, tmp_path / "README.md")
    assert "🔒" in (tmp_path / "README.md").read_text()


def test_rows_sorted_newest_first(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(company="Older", date_posted="2026-01-01"),
        _row(company="Newer", date_posted="2026-09-01"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert text.index("Newer") < text.index("Older")


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
    assert "Last Verified" in text


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
    assert len(cells) == 9
    assert "[Apply](<https://example.com/apply?ref=(promo)>)" in text
