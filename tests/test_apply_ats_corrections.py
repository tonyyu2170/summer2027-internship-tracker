"""Tests for the corrections applier (scripts/apply_ats_corrections.py)."""
import copy
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


def test_correction_for_unknown_row_id_is_skipped():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(id="ghost")], TODAY)
    assert summary["skipped"] == ["ghost"]
    assert new["swe"][0]["last_verified"] == "2026-07-01"


def test_multiple_actions_for_one_row_all_apply():
    actions = [
        _action(action="set_date", old="2026-07-01", new="2026-06-15"),
        _action(action="confirm"),
    ]
    new, summary = apply_corrections({"swe": [_row()]}, actions, TODAY)
    assert new["swe"][0]["date_posted"] == "2026-06-15"
    assert summary["confirmed"] == ["r1"] and summary["date_fixed"] == ["r1"]


def test_apply_never_mutates_input():
    # deepcopy, not dict(): a shallow snapshot shares the nested `degree` and
    # `sources` lists with the input, so `data == snapshot` would hold even if
    # apply_corrections mutated one of them in place.
    data = {"swe": [_row()]}
    snapshot = copy.deepcopy(data)
    apply_corrections(
        data,
        [_action(action="set_date", old="2026-07-01", new="2026-06-15")],
        TODAY)
    assert data == snapshot


def test_unrecognized_action_on_existing_row_is_not_reported_as_unknown_id():
    # A typo'd or renamed action kind must not be reported as a stale row id,
    # and must change nothing.
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="clsoe")], TODAY)
    assert summary["unrecognized_action"] == ["r1"]
    assert summary["skipped"] == []
    assert new["swe"][0]["status"] == "open"
    assert new["swe"][0]["last_verified"] == "2026-07-01"


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
        _action(action="set_date", old="2026-07-01", new="2026-06-15"),
    ])
    readme = tmp_path / "README.md"
    summary = run(corrections, data_dir=data_dir, readme_path=readme)
    assert summary["date_fixed"] == ["r1"]
    on_disk = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert on_disk[0]["date_posted"] == "2026-06-15"
    assert readme.exists()
    assert "2026-06-15" in readme.read_text()


def test_run_aborts_on_schema_failure_writing_nothing(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    before = (data_dir / "swe.yaml").read_text()
    corrections = _write_corrections(tmp_path, [
        # empty date violates ROW_SCHEMA's date pattern — deterministic
        # corrections producing this means a bug, so the whole apply aborts
        _action(action="set_date", old="2026-07-01", new=""),
    ])
    readme = tmp_path / "README.md"
    with pytest.raises(SystemExit):
        run(corrections, data_dir=data_dir, readme_path=readme)
    assert (data_dir / "swe.yaml").read_text() == before
    assert not readme.exists()


def test_run_aborts_when_a_corrections_id_matches_two_rows(tmp_path):
    # Rows are matched by id alone, so a duplicate id would delete every row
    # sharing it (reporting one) and land a set_location on whichever row
    # loaded last. merge.py can still emit duplicate ids, so refuse to run.
    data_dir = _setup_tree(tmp_path, [_row(id="dup", company="AlphaCo")])
    (data_dir / "quant.yaml").write_text(yaml.safe_dump(
        [_row(id="dup", company="BetaCo", link="https://x.com/2")],
        sort_keys=False, allow_unicode=True))
    before = (data_dir / "swe.yaml").read_text()
    corrections = _write_corrections(tmp_path, [
        _action(id="dup", action="delete_non_us",
                api_locations=["Toronto"], country="Canada"),
    ])
    readme = tmp_path / "README.md"
    with pytest.raises(SystemExit):
        run(corrections, data_dir=data_dir, readme_path=readme)
    assert (data_dir / "swe.yaml").read_text() == before
    assert not readme.exists()


def test_run_does_not_announce_a_delete_that_did_not_happen(tmp_path, capsys):
    # An action naming a row id that isn't in the data deletes nothing; the
    # output must not claim otherwise.
    data_dir = _setup_tree(tmp_path, [_row()])
    corrections = _write_corrections(tmp_path, [
        _action(id="ghost", action="delete_non_us",
                api_locations=["Toronto"], country="Canada"),
    ])
    summary = run(corrections, data_dir=data_dir,
                  readme_path=tmp_path / "README.md")
    assert summary["deleted"] == []
    assert summary["skipped"] == ["ghost"]
    assert "DELETED" not in capsys.readouterr().out
    assert len(yaml.safe_load((data_dir / "swe.yaml").read_text())) == 1


def test_run_tolerates_a_pre_existing_schema_error_on_a_confirmed_row(tmp_path):
    # confirm is the modal outcome and changes nothing but last_verified, so
    # a row that already failed schema must not block the whole apply — the
    # same tolerance run_scrape_merge extends to rows loaded from disk.
    bad = _row(id="handedit", notes="hand-added field ROW_SCHEMA forbids")
    data_dir = _setup_tree(tmp_path, [bad, _row(id="good", link="https://x.com/2")])
    corrections = _write_corrections(tmp_path, [
        _action(id="handedit", action="confirm"),
        _action(id="good", action="set_date",
                old="2026-07-01", new="2026-06-15"),
    ])
    summary = run(corrections, data_dir=data_dir,
                  readme_path=tmp_path / "README.md")
    on_disk = {r["id"]: r for r in
               yaml.safe_load((data_dir / "swe.yaml").read_text())}
    assert summary["date_fixed"] == ["good"]               # the good fix landed
    assert on_disk["good"]["date_posted"] == "2026-06-15"
    assert on_disk["handedit"]["notes"] == "hand-added field ROW_SCHEMA forbids"


def test_run_still_aborts_when_this_apply_introduces_a_schema_error(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    before = (data_dir / "swe.yaml").read_text()
    corrections = _write_corrections(tmp_path, [
        _action(action="set_date", old="2026-07-01", new=""),
    ])
    with pytest.raises(SystemExit):
        run(corrections, data_dir=data_dir, readme_path=tmp_path / "README.md")
    assert (data_dir / "swe.yaml").read_text() == before


def test_run_leaves_unchanged_category_files_untouched(tmp_path):
    # safe_dump re-wraps lines, so rewriting every category turns a one-row
    # correction into a diff across all six files.
    data_dir = _setup_tree(tmp_path, [_row()])
    (data_dir / "quant.yaml").write_text(yaml.safe_dump(
        [_row(id="q1", link="https://x.com/9")], sort_keys=False,
        allow_unicode=True))
    quant_before = (data_dir / "quant.yaml").read_text()
    corrections = _write_corrections(tmp_path, [
        _action(action="set_date", old="2026-07-01", new="2026-06-15")])
    run(corrections, data_dir=data_dir, readme_path=tmp_path / "README.md")
    assert (data_dir / "quant.yaml").read_text() == quant_before


def test_run_rejects_a_malformed_corrections_file_cleanly(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    before = (data_dir / "swe.yaml").read_text()
    bad = tmp_path / "bad.json"
    bad.write_text('{"generated": "2026-08-08"')          # truncated
    with pytest.raises(SystemExit):
        run(bad, data_dir=data_dir, readme_path=tmp_path / "README.md")
    no_actions = tmp_path / "noactions.json"
    no_actions.write_text('{"generated": "2026-08-08"}')
    with pytest.raises(SystemExit):
        run(no_actions, data_dir=data_dir, readme_path=tmp_path / "README.md")
    assert (data_dir / "swe.yaml").read_text() == before


def test_action_missing_its_new_value_is_skipped_not_a_crash():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="set_date", old="2026-07-01")],
        TODAY)
    assert summary["unrecognized_action"] == ["r1"]
    assert new["swe"][0]["date_posted"] == "2026-07-01"
