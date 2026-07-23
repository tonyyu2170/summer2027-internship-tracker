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
                 "actuarial", "consulting", "ib"):
        _write(d, stem, [])
    return d


def test_render_writes_toc_and_all_headings(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "## Software Engineering" in text
    assert "## Investment Banking" in text
    assert "[Quantitative Finance](#quantitative-finance)" in text


def test_quant_table_has_track_column_and_row(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "quant", [_row(track="Trading")])
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "| Track |" in text
    assert "Trading" in text
    assert "[Apply](https://x.com/j)" in text


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
