import yaml
import check_programs
from check_programs import (
    _slug, _match_signal, _is_future, derive_status, build_row, check_kind, run,
)


# ---------------------------------------------------------------------------
# _slug
# ---------------------------------------------------------------------------

def test_slug_joins_org_and_name():
    assert _slug("NVIDIA", "NVIDIA Ignite") == "nvidia-nvidia-ignite"


def test_slug_lowercases_and_strips_punctuation():
    assert _slug("Jane Street", "FOCUS") == "jane-street-focus"


# ---------------------------------------------------------------------------
# _match_signal
# ---------------------------------------------------------------------------

def test_match_signal_literal_substring():
    assert _match_signal("Applications are now closed", "text: Applications are now closed.")


def test_match_signal_regex():
    assert _match_signal(r"clos(ed|ing)", "Registration is currently closing soon")


def test_match_signal_no_match():
    assert not _match_signal("Applications are now closed", "Apply today!")


def test_match_signal_case_sensitive():
    assert not _match_signal("Applications Closed", "applications closed")


def test_match_signal_falls_back_to_substring_on_bad_regex():
    # unbalanced paren is not valid regex; must not raise, must fall back
    # to plain substring containment.
    bad_pattern = "applications (are closed"
    assert _match_signal(bad_pattern, "text with applications (are closed inside")
    assert not _match_signal(bad_pattern, "no match here")


# ---------------------------------------------------------------------------
# _is_future
# ---------------------------------------------------------------------------

def test_is_future_full_date():
    assert _is_future("2026-11-01", "2026-08-07")
    assert not _is_future("2026-07-01", "2026-08-07")


def test_is_future_year_month():
    assert _is_future("2026-11", "2026-08-07")
    assert not _is_future("2026-08", "2026-08-07")


def test_is_future_none_is_false():
    assert not _is_future(None, "2026-08-07")


# ---------------------------------------------------------------------------
# derive_status — the four required derivations, plus preserve-on-failure
# ---------------------------------------------------------------------------

TODAY = "2026-08-07"


def test_open_signal_matches_closed_does_not():
    status = derive_status(
        "now open", "now closed", "Applications are now open for 2027!",
        None, TODAY, "unknown",
    )
    assert status == "open"


def test_closed_signal_matches_open_does_not():
    status = derive_status(
        "now open", "now closed", "Applications are now closed.",
        None, TODAY, "open",
    )
    assert status == "closed"


def test_both_signals_match_falls_back_to_preserve():
    body = "Applications are now open. Applications are now closed."
    status = derive_status("now open", "now closed", body, None, TODAY, "open")
    assert status == "open"  # preserved, not overwritten


def test_neither_signal_matches_falls_back_to_preserve():
    body = "Nothing relevant here."
    status = derive_status("now open", "now closed", body, None, TODAY, "closed")
    assert status == "closed"  # preserved


def test_neither_signal_matches_with_no_prior_status_is_unknown():
    body = "Nothing relevant here."
    status = derive_status(None, None, body, None, TODAY, None)
    assert status == "unknown"


def test_future_opens_with_no_open_signal_is_upcoming():
    status = derive_status(None, None, "irrelevant body", "2026-11", TODAY, "unknown")
    assert status == "upcoming"


def test_closed_signal_match_beats_future_opens_heuristic():
    # Real shape from the watch-list: closed_signal describes "not open yet"
    # phrasing, with a genuinely future opens date. An explicit signal match
    # must win over the date heuristic.
    body = "Applications for the next competition are not open yet."
    status = derive_status(
        None, "Applications for the next competition are not open yet.",
        body, "2026-11", TODAY, "unknown",
    )
    assert status == "closed"


def test_fetch_failed_preserves_prior_status_never_flips_to_closed():
    # A transient 403/network failure is modeled as body=None. Even though
    # the prior status is 'open' and closed_signal is set, a failed fetch
    # must never derive 'closed'.
    status = derive_status("now open", "now closed", None, None, TODAY, "open")
    assert status == "open"


def test_fetch_failed_with_no_prior_status_is_unknown():
    status = derive_status("now open", "now closed", None, None, TODAY, None)
    assert status == "unknown"


def test_fetch_failed_does_not_apply_future_opens_heuristic():
    # Per design: a failed fetch preserves immediately and does not fall
    # through to the future-opens check (that check only applies when the
    # fetch succeeded but signals were ambiguous).
    status = derive_status(None, None, None, "2026-11", TODAY, "closed")
    assert status == "closed"


# ---------------------------------------------------------------------------
# _fetch_body — real network-adjacent layer; the non-2xx-body invariant
# ---------------------------------------------------------------------------

def test_fetch_body_never_returns_body_for_non_2xx(monkeypatch):
    def fake_link_probe(url, timeout=12.0, want_body=False):
        return 403, url, "<html>applications are now closed</html>"

    monkeypatch.setattr(check_programs, "_link_probe", fake_link_probe)
    assert check_programs._fetch_body("https://example.com") is None


def test_fetch_body_returns_body_for_200(monkeypatch):
    def fake_link_probe(url, timeout=12.0, want_body=False):
        return 200, url, "<html>hello</html>"

    monkeypatch.setattr(check_programs, "_link_probe", fake_link_probe)
    assert check_programs._fetch_body("https://example.com") == "<html>hello</html>"


# ---------------------------------------------------------------------------
# build_row
# ---------------------------------------------------------------------------

ENTRY = {
    "name": "NVIDIA Ignite", "org": "NVIDIA", "kind": "program",
    "category": "ai_ml", "url": "https://example.com/ignite",
    "apply_url": None, "status": "unknown", "opens": None, "closes": None,
    "eligibility": "Freshmen and sophomores", "location": "Santa Clara, CA",
    "cycle": "Summer 2027", "check_url": "https://example.com/ignite",
    "open_signal": None, "closed_signal": None, "notes": None,
}


def test_build_row_first_creation_sets_sources_and_date_added():
    row = build_row(ENTRY, "nvidia-nvidia-ignite", "unknown", TODAY, None)
    assert row["sources"] == ["llm_discovery"]
    assert row["date_added"] == TODAY
    assert row["last_checked"] == TODAY
    assert row["status"] == "unknown"
    assert row["id"] == "nvidia-nvidia-ignite"


def test_build_row_update_preserves_date_added_and_sources():
    existing = {
        "id": "nvidia-nvidia-ignite", "date_added": "2026-01-01",
        "sources": ["llm_discovery", "manual_review"], "status": "unknown",
    }
    row = build_row(ENTRY, "nvidia-nvidia-ignite", "open", "2026-08-07", existing)
    assert row["date_added"] == "2026-01-01"
    assert row["sources"] == ["llm_discovery", "manual_review"]
    assert row["last_checked"] == "2026-08-07"
    assert row["status"] == "open"


# ---------------------------------------------------------------------------
# check_kind — orchestration with a stubbed fetch
# ---------------------------------------------------------------------------

def test_check_kind_new_entry_open_signal_matches():
    entries = [{**ENTRY, "open_signal": "now open", "closed_signal": None}]

    def fetch(url):
        return "Applications are now open!"

    rows, summary = check_kind(entries, [], TODAY, fetch)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert summary["open"] == 1
    assert summary["fetch_failed"] == 0


def test_check_kind_fetch_failure_preserves_existing_row_status():
    existing = [{
        "id": "nvidia-nvidia-ignite", "name": "NVIDIA Ignite", "org": "NVIDIA",
        "kind": "program", "category": "ai_ml", "url": "https://example.com/ignite",
        "apply_url": None, "status": "open", "opens": None, "closes": None,
        "eligibility": "Freshmen and sophomores", "location": "Santa Clara, CA",
        "cycle": "Summer 2027", "sources": ["llm_discovery"],
        "date_added": "2026-01-01", "last_checked": "2026-01-01", "notes": None,
    }]
    entries = [{**ENTRY, "open_signal": None, "closed_signal": "closed"}]

    def fetch(url):
        return None  # simulate a transient failure

    rows, summary = check_kind(entries, existing, TODAY, fetch)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"  # preserved, not flipped to unknown/closed
    assert rows[0]["last_checked"] == TODAY
    assert summary["fetch_failed"] == 1
    assert summary["transitioned"] == []


def test_check_kind_reports_transition():
    existing = [{
        "id": "nvidia-nvidia-ignite", "name": "NVIDIA Ignite", "org": "NVIDIA",
        "kind": "program", "category": "ai_ml", "url": "https://example.com/ignite",
        "apply_url": None, "status": "unknown", "opens": None, "closes": None,
        "eligibility": "Freshmen and sophomores", "location": "Santa Clara, CA",
        "cycle": "Summer 2027", "sources": ["llm_discovery"],
        "date_added": "2026-01-01", "last_checked": "2026-01-01", "notes": None,
    }]
    entries = [{**ENTRY, "open_signal": "now open", "closed_signal": None}]

    def fetch(url):
        return "Applications are now open!"

    rows, summary = check_kind(entries, existing, TODAY, fetch)
    assert summary["transitioned"] == [("nvidia-nvidia-ignite", "unknown", "open")]


def test_check_kind_invalid_row_is_not_written_existing_kept():
    existing = [{
        "id": "nvidia-nvidia-ignite", "name": "NVIDIA Ignite", "org": "NVIDIA",
        "kind": "program", "category": "ai_ml", "url": "https://example.com/ignite",
        "apply_url": None, "status": "open", "opens": None, "closes": None,
        "eligibility": "Freshmen and sophomores", "location": "Santa Clara, CA",
        "cycle": "Summer 2027", "sources": ["llm_discovery"],
        "date_added": "2026-01-01", "last_checked": "2026-01-01", "notes": None,
    }]
    bad_entry = {**ENTRY, "eligibility": None}  # required, non-nullable -> invalid

    def fetch(url):
        return None

    rows, summary = check_kind([bad_entry], existing, TODAY, fetch)
    assert rows == existing  # untouched, bad row never written
    assert len(summary["invalid"]) == 1
    assert summary["invalid"][0][0] == "nvidia-nvidia-ignite"


def test_check_kind_existing_row_missing_id_is_not_deleted():
    # A hand-corrupted or otherwise id-less existing row must never be
    # silently dropped from the rewritten file (mirrors merge.py's .get("id")
    # graceful-degradation guard).
    unkeyed_row = {
        "name": "Mystery Program", "org": "Unknown", "status": "open",
        "kind": "program",
    }
    existing = [unkeyed_row]

    def fetch(url):
        return None

    rows, summary = check_kind([], existing, TODAY, fetch)
    assert unkeyed_row in rows


def test_check_kind_orphaned_row_carried_through_untouched():
    existing = [{
        "id": "orphan-co-orphan-program", "name": "Orphan Program", "org": "Orphan Co",
        "kind": "program", "category": None, "url": "https://example.com/orphan",
        "apply_url": None, "status": "open", "opens": None, "closes": None,
        "eligibility": "Anyone", "location": None, "cycle": None,
        "sources": ["llm_discovery"], "date_added": "2026-01-01",
        "last_checked": "2026-01-01", "notes": None,
    }]

    def fetch(url):
        return None

    rows, summary = check_kind([], existing, TODAY, fetch)
    assert rows == existing
    assert summary["open"] == 0  # not counted — not checked this run


# ---------------------------------------------------------------------------
# run — full I/O wrapper against tmp_path only
# ---------------------------------------------------------------------------

def _write_watchlist(path, programs=None, research=None, competitions=None):
    path.write_text(yaml.safe_dump({
        "programs": programs or [],
        "research": research or [],
        "competitions": competitions or [],
    }))


def test_run_writes_all_three_kind_files(tmp_path):
    watchlist_path = tmp_path / "programs.yaml"
    _write_watchlist(
        watchlist_path,
        programs=[{**ENTRY, "open_signal": "now open"}],
    )
    data_dir = tmp_path / "data"
    state_path = tmp_path / "scrape_state.yaml"

    def probe(url):
        return "Applications are now open!"

    run(watchlist_path, data_dir, state_path, probe)

    assert (data_dir / "opportunities" / "programs.yaml").exists()
    assert (data_dir / "opportunities" / "research.yaml").exists()
    assert (data_dir / "opportunities" / "competitions.yaml").exists()
    rows = yaml.safe_load((data_dir / "opportunities" / "programs.yaml").read_text())
    assert rows[0]["status"] == "open"


def test_run_read_modify_write_preserves_existing_scrape_state(tmp_path):
    watchlist_path = tmp_path / "programs.yaml"
    _write_watchlist(watchlist_path)
    data_dir = tmp_path / "data"
    state_path = tmp_path / "scrape_state.yaml"
    state_path.write_text(yaml.safe_dump({
        "some_tracker": {"row_count": 42, "sha": "abc123"},
        "_last_run": {"new": 3, "closed": 1},
    }))

    def probe(url):
        return None

    run(watchlist_path, data_dir, state_path, probe)

    state = yaml.safe_load(state_path.read_text())
    assert state["some_tracker"] == {"row_count": 42, "sha": "abc123"}
    assert state["_last_run"] == {"new": 3, "closed": 1}
    assert "_last_program_check" in state
    assert "ran_at" in state["_last_program_check"]


def test_run_writes_no_tracked_repo_file(tmp_path):
    # Sanity check: nothing under the real repo's data/ or sources/ is
    # touched when explicit paths are passed — including scrape_state.yaml,
    # the file the controller specifically forbade clobbering.
    import check_programs as cp
    real_data_dir = cp.ROOT / "data"
    real_state_path = cp.ROOT / "sources" / "scrape_state.yaml"
    watchlist_path = tmp_path / "programs.yaml"
    _write_watchlist(watchlist_path, programs=[ENTRY])
    data_dir = tmp_path / "data"
    state_path = tmp_path / "scrape_state.yaml"
    guarded = {
        "programs.yaml", "research.yaml", "competitions.yaml",
    }
    before = {
        name: (real_data_dir / "opportunities" / name).read_text()
        for name in guarded
    }
    before_state = real_state_path.read_text()

    run(watchlist_path, data_dir, state_path, lambda url: None)

    for name in guarded:
        assert (real_data_dir / "opportunities" / name).read_text() == before[name]
    assert real_state_path.read_text() == before_state
