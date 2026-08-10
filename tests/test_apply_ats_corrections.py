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


def test_repost_moves_link_date_and_recomputes_the_id():
    row = _row(link="https://jobs.smartrecruiters.com/Acme/111",
               date_posted="2026-05-29", date_estimated=True)
    new, summary = apply_corrections(
        {"swe": [row]},
        [_action(action="repost", old_link="https://jobs.smartrecruiters.com/Acme/111",
                 new_link="https://jobs.smartrecruiters.com/Acme/222",
                 new_date="2026-08-10")],
        TODAY)
    moved = new["swe"][0]
    assert moved["link"] == "https://jobs.smartrecruiters.com/Acme/222"
    assert moved["date_posted"] == "2026-08-10"
    assert moved["date_estimated"] is False
    assert moved["last_verified"] == TODAY
    # The id is a hash of the link; leaving it stale is the known drift bug.
    assert moved["id"] != "r1"
    assert summary["reposted"] == [moved["id"]]


def test_repost_without_a_new_link_is_not_applied():
    new, summary = apply_corrections(
        {"swe": [_row()]}, [_action(action="repost")], TODAY)
    assert new["swe"][0]["link"] == "https://x.com/1"
    assert summary["reposted"] == []
    assert summary["unrecognized_action"] == ["r1"]


def test_ambiguous_is_report_only_and_never_counts_as_a_missing_row():
    new, summary = apply_corrections(
        {"swe": [_row()]},
        [{"action": "ambiguous", "ids": ["r1", "r2"], "category": "swe",
          "ats": "lever", "title": "swe intern", "candidates": []}],
        TODAY)
    assert new["swe"][0] == _row()
    assert summary["skipped"] == []
    assert summary["unrecognized_action"] == []


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


def test_recategorize_moves_the_row_without_touching_id_or_link():
    new, summary = apply_corrections(
        {"quant": [_row(role="FPGA Engineer Intern")], "hardware": []},
        [_action(action="recategorize", **{"from": "quant", "to": "hardware"})],
        TODAY)
    assert new["quant"] == []
    assert len(new["hardware"]) == 1
    # The id is a hash of company/role/link and does not embed the category,
    # so a move must not rehash it — that would be the id/link drift bug.
    assert new["hardware"][0]["id"] == "r1"
    assert new["hardware"][0]["link"] == "https://x.com/1"
    assert summary["recategorized"] == ["r1"]


def test_recategorize_to_an_unknown_category_is_rejected():
    new, summary = apply_corrections(
        {"quant": [_row()], "hardware": []},
        [_action(action="recategorize", **{"from": "quant", "to": "nonsense"})],
        TODAY)
    assert new["quant"] == [_row()]
    assert summary["unrecognized_action"] == ["r1"]
    assert summary["recategorized"] == []


def test_keep_leaves_every_category_file_untouched():
    new, summary = apply_corrections(
        {"quant": [_row()], "hardware": []},
        [_action(action="keep", **{"from": "quant", "to": "swe"})],
        TODAY)
    assert new["quant"] == [_row()]
    assert new["hardware"] == []
    assert summary["kept"] == ["r1"]


def test_keep_without_a_from_is_rejected():
    new, summary = apply_corrections(
        {"quant": [_row()]}, [_action(action="keep", to="swe")], TODAY)
    assert new["quant"] == [_row()]
    assert summary["unrecognized_action"] == ["r1"]


def test_keep_with_a_falsy_from_is_rejected():
    # Not merely "key absent" — `from` is written verbatim into
    # sources/manual_categories.yaml by run(), with no downstream gate to
    # catch it, so an empty or null value must be refused here. This is a
    # deliberate deviation from set_date's `"new" not in a` check, which can
    # afford to let an empty value through to the schema gate.
    for bad in ("", None):
        new, summary = apply_corrections(
            {"quant": [_row()]},
            [_action(action="keep", **{"from": bad, "to": "swe"})], TODAY)
        assert new["quant"] == [_row()]
        assert summary["unrecognized_action"] == ["r1"]
        assert summary["kept"] == []


def test_drop_deletes_the_row():
    new, summary = apply_corrections(
        {"quant": [_row(role="Venture Capital Analyst Intern")]},
        [_action(action="drop", **{"from": "quant", "to": "__drop__"})],
        TODAY)
    assert new["quant"] == []
    assert summary["dropped"] == ["r1"]


def test_recategorize_appends_without_clobbering_the_destination():
    new, summary = apply_corrections(
        {"quant": [_row(id="mover", role="FPGA Engineer Intern")],
         "hardware": [_row(id="sitting", link="https://x.com/2")]},
        [_action(id="mover", action="recategorize",
                 **{"from": "quant", "to": "hardware"})],
        TODAY)
    assert new["quant"] == []
    assert [r["id"] for r in new["hardware"]] == ["sitting", "mover"]


def test_run_moves_a_row_between_category_files_on_disk(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row(role="FPGA Engineer Intern")])
    (data_dir / "hardware.yaml").write_text(yaml.safe_dump([]))
    corrections = _write_corrections(tmp_path, [
        _action(action="recategorize", **{"from": "swe", "to": "hardware"})])
    run(corrections, data_dir=data_dir, readme_path=tmp_path / "README.md",
        overrides_path=tmp_path / "manual_categories.yaml")
    assert yaml.safe_load((data_dir / "swe.yaml").read_text()) == []
    moved = yaml.safe_load((data_dir / "hardware.yaml").read_text())
    assert [r["id"] for r in moved] == ["r1"]


def test_run_records_a_keep_in_the_overrides_file(tmp_path):
    data_dir = _setup_tree(tmp_path, [_row()])
    overrides = tmp_path / "manual_categories.yaml"
    corrections = _write_corrections(tmp_path, [
        _action(action="keep", link="https://x.com/1",
                **{"from": "swe", "to": "quant"})])
    run(corrections, data_dir=data_dir, readme_path=tmp_path / "README.md",
        overrides_path=overrides)
    assert yaml.safe_load(overrides.read_text()) == {"https://x.com/1": "swe"}
    # The row itself must not have moved.
    assert len(yaml.safe_load((data_dir / "swe.yaml").read_text())) == 1


def test_run_survives_a_malformed_duplicate_keep_action(tmp_path):
    # A hand-edited corrections file can carry two keep actions for one id,
    # one well-formed and one missing `from`. The malformed one is rejected
    # by apply_corrections, but its id still appears in summary["kept"]
    # thanks to the valid one — so run() must re-check `from` rather than
    # bracket-index it and crash a delete-capable run mid-write.
    data_dir = _setup_tree(tmp_path, [_row()])
    overrides = tmp_path / "manual_categories.yaml"
    corrections = _write_corrections(tmp_path, [
        _action(action="keep", link="https://x.com/1",
                **{"from": "swe", "to": "quant"}),
        _action(action="keep", link="https://x.com/1", to="quant"),
    ])
    run(corrections, data_dir=data_dir, readme_path=tmp_path / "README.md",
        overrides_path=overrides)
    assert yaml.safe_load(overrides.read_text()) == {"https://x.com/1": "swe"}


def test_possible_duplicate_of_survives_a_recategorize():
    # Only pointers into DELETED rows are cleared. recategorize never
    # rehashes the id, so a pointer into a moved row stays resolvable
    # dataset-wide and must be left alone.
    new, _summary = apply_corrections(
        {"quant": [_row(id="mover", role="FPGA Engineer Intern")],
         "hardware": [],
         "swe": [_row(id="pointer", link="https://x.com/2",
                      possible_duplicate_of="mover")]},
        [_action(id="mover", action="recategorize",
                 **{"from": "quant", "to": "hardware"})],
        TODAY)
    assert new["swe"][0]["possible_duplicate_of"] == "mover"
    assert new["hardware"][0]["id"] == "mover"
