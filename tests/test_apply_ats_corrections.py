"""Tests for the corrections applier (scripts/apply_ats_corrections.py)."""
import json
import yaml
import pytest
from apply_ats_corrections import apply_corrections, run

TODAY = "2026-08-08"


def _row(**kw):
    base = {
        "id": "r1", "company": "Acme", "role": "SWE Intern",
        "location": "New York, NY", "link": "https://x.com/1",
        "date_posted": "2026-07-01", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["s"], "date_added": "2026-07-01",
        "last_verified": "2026-07-01", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def _action(**kw):
    base = {"id": "r1", "category": "swe", "ats": "lever", "action": "confirm"}
    base.update(kw)
    return base


def test_confirm_stamps_last_verified():
    new, summary = apply_corrections({"swe": [_row()]}, [_action()], TODAY)
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["confirmed"] == ["r1"]


def test_unknown_does_not_stamp():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="unknown")], TODAY)
    assert new["swe"][0]["last_verified"] == "2026-07-01"
    assert summary["unknown"] == ["r1"]


def test_set_location():
    new, summary = apply_corrections(
        {"swe": [_row()]},
        [_action(action="set_location", old="New York, NY", new="Redmond, WA")],
        TODAY)
    assert new["swe"][0]["location"] == "Redmond, WA"
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["location_fixed"] == ["r1"]


def test_set_date_clears_estimated():
    new, summary = apply_corrections(
        {"swe": [_row(date_estimated=True)]},
        [_action(action="set_date", old="2026-07-01", new="2026-06-15")],
        TODAY)
    assert new["swe"][0]["date_posted"] == "2026-06-15"
    assert new["swe"][0]["date_estimated"] is False
    assert summary["date_fixed"] == ["r1"]


def test_close():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="close")], TODAY)
    assert new["swe"][0]["status"] == "closed"
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["closed"] == ["r1"]


def test_delete_removes_row_and_clears_dup_pointers_across_categories():
    data = {
        "swe": [_row(id="gone")],
        "quant": [_row(id="stays", link="https://x.com/2",
                       possible_duplicate_of="gone")],
    }
    new, summary = apply_corrections(
        data, [_action(id="gone", action="delete_non_us",
                       api_locations=["Toronto"], country="Canada")], TODAY)
    assert new["swe"] == []
    assert new["quant"][0]["possible_duplicate_of"] is None
    assert summary["deleted"] == ["gone"]


def test_location_unresolved_stamps_but_changes_nothing_else():
    new, summary = apply_corrections(
        {"swe": [_row()]},
        [_action(action="location_unresolved", api_locations=["New York"])],
        TODAY)
    assert new["swe"][0]["location"] == "New York, NY"
    assert new["swe"][0]["last_verified"] == TODAY
    assert summary["unresolved"] == ["r1"]


def test_correction_for_unknown_row_id_is_skipped():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(id="ghost")], TODAY)
    assert summary["skipped"] == ["ghost"]
    assert new["swe"][0]["last_verified"] == "2026-07-01"


def test_multiple_actions_for_one_row_all_apply():
    actions = [
        _action(action="set_location", old="New York, NY", new="Austin, TX"),
        _action(action="set_date", old="2026-07-01", new="2026-06-15"),
    ]
    new, _ = apply_corrections({"swe": [_row()]}, actions, TODAY)
    assert new["swe"][0]["location"] == "Austin, TX"
    assert new["swe"][0]["date_posted"] == "2026-06-15"


def test_apply_never_mutates_input():
    data = {"swe": [_row()]}
    snapshot = {"swe": [dict(data["swe"][0])]}
    apply_corrections(
        data,
        [_action(action="set_location", old="New York, NY", new="Austin, TX")],
        TODAY)
    assert data == snapshot


def _setup_tree(tmp_path, rows_swe):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    stems = ("swe", "quant", "data_science", "ai_ml", "hardware", "actuarial")
    for stem in stems:
        rows = rows_swe if stem == "swe" else []
        (data_dir / f"{stem}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    return data_dir


def _write_corrections(tmp_path, actions):
    p = tmp_path / "ats_corrections.json"
    p.write_text(json.dumps({"generated": TODAY, "actions": actions}))
    return p


def test_run_applies_writes_yaml_and_renders_readme(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    corrections = _write_corrections(tmp_path, [
        _action(action="set_location", old="New York, NY", new="Redmond, WA"),
    ])
    readme = tmp_path / "README.md"
    summary = run(corrections, data_dir=data_dir, readme_path=readme)
    assert summary["location_fixed"] == ["r1"]
    on_disk = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert on_disk[0]["location"] == "Redmond, WA"
    assert readme.exists()
    assert "Redmond, WA" in readme.read_text()


def test_run_aborts_on_schema_failure_writing_nothing(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    before = (data_dir / "swe.yaml").read_text()
    corrections = _write_corrections(tmp_path, [
        # empty location violates ROW_SCHEMA minLength — deterministic
        # corrections producing this means a bug, so the whole apply aborts
        _action(action="set_location", old="New York, NY", new=""),
    ])
    readme = tmp_path / "README.md"
    with pytest.raises(SystemExit):
        run(corrections, data_dir=data_dir, readme_path=readme)
    assert (data_dir / "swe.yaml").read_text() == before
    assert not readme.exists()
