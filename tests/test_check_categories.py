"""Tests for the retro-classification sweep (scripts/check_categories.py)."""
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
