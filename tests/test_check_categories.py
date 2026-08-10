"""Tests for the retro-classification sweep (scripts/check_categories.py)."""
import yaml

from categorize import manual_link_categories
from check_categories import find_disagreements


def _row(**kw):
    base = {
        "id": "r1", "company": "Acme", "role": "Software Engineer Intern",
        "location": "New York, NY", "link": "https://x.com/1",
        "date_posted": "2026-07-01", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["s"], "date_added": "2026-07-01",
        "last_verified": "2026-07-01", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def test_agreement_produces_no_action():
    rows = {"swe": [_row()], "quant": [], "hardware": []}
    assert find_disagreements(rows, {}) == []


def test_unclassifiable_role_produces_no_action():
    # classify_role returning None means the rules have no opinion; a row must
    # never move on no opinion.
    rows = {"swe": [_row(role="Summer Intern, Early Interest")], "quant": []}
    assert find_disagreements(rows, {}) == []


def test_closed_row_is_skipped_even_when_it_disagrees():
    rows = {"quant": [_row(role="Software Engineer Intern", status="closed")],
            "swe": []}
    assert find_disagreements(rows, {}) == []


def test_adjudicated_link_is_skipped_even_when_it_disagrees():
    rows = {"quant": [_row(role="Software Engineer Intern")], "swe": []}
    overrides = {"https://x.com/1": "quant"}
    assert find_disagreements(rows, overrides) == []


def test_cross_category_disagreement_proposes_recategorize():
    rows = {"quant": [_row(role="FPGA Engineer Intern")], "hardware": []}
    actions = find_disagreements(rows, {})
    assert len(actions) == 1
    assert actions[0]["action"] == "recategorize"
    assert actions[0]["from"] == "quant"
    assert actions[0]["to"] == "hardware"
    assert actions[0]["id"] == "r1"
    assert actions[0]["link"] == "https://x.com/1"
    assert actions[0]["role"] == "FPGA Engineer Intern"


def test_out_of_scope_role_proposes_drop():
    rows = {"quant": [_row(role="Venture Capital Analyst Intern")], "swe": []}
    actions = find_disagreements(rows, {})
    assert len(actions) == 1
    assert actions[0]["action"] == "drop"
    assert actions[0]["to"] == "__drop__"


def test_load_rows_reads_every_category_file(tmp_path):
    from check_categories import load_rows
    (tmp_path / "swe.yaml").write_text(yaml.safe_dump([_row()]))
    rows = load_rows(tmp_path)
    assert rows["swe"] == [_row()]
    # Categories with no file on disk must still be present, so a
    # recategorize target never KeyErrors.
    assert rows["quant"] == []


def test_sweep_apply_keep_sweep_is_idempotent(tmp_path):
    # The property this whole feature exists for. It is also the only test
    # that catches a mismatch between the link form `keep` WRITES to
    # manual_categories.yaml and the form find_disagreements READS back:
    # the applier appends the raw link, manual_link_categories normalizes
    # keys on read, and find_disagreements normalizes before comparing.
    rows = {"quant": [_row(role="Software Engineer Intern",
                           link="https://x.com/1?utm_source=board")],
            "swe": []}

    first = find_disagreements(rows, {})
    assert len(first) == 1
    assert first[0]["action"] == "recategorize"

    # Simulate adjudicating it as `keep`, byte-for-byte how run() writes it.
    overrides_path = tmp_path / "manual_categories.yaml"
    overrides_path.write_text(
        yaml.safe_dump({first[0]["link"]: first[0]["from"]}, sort_keys=True))

    second = find_disagreements(
        rows, manual_link_categories(path=overrides_path))
    assert second == []


def test_drift_marker_is_written_when_drift_exists_and_removed_when_clean(tmp_path):
    from check_categories import write_drift_marker
    marker = tmp_path / "CATEGORY_DRIFT"

    write_drift_marker(marker, 3, "2026-08-10")
    assert marker.exists()
    assert "3" in marker.read_text()
    assert "check_categories.py" in marker.read_text()

    # Self-healing: a clean run removes the marker rather than leaving a
    # stale one, so the file's existence is itself the signal.
    write_drift_marker(marker, 0, "2026-08-11")
    assert not marker.exists()
