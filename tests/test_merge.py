from merge import merge_category

TODAY = "2026-07-22"


def _posting(**kw):
    base = {
        "company": "Jane Street", "role": "Quant Trading Intern",
        "location": "New York, NY",
        "link": "https://boards.greenhouse.io/js/jobs/1",
        "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
    }
    base.update(kw)
    return base


def _report(postings, category="quant", entity="greenhouse:js"):
    return {"category": category, "source_entity": entity, "postings": postings}


def test_new_posting_becomes_open_row():
    rows, summary = merge_category([], [_report([_posting()])], TODAY)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["date_added"] == TODAY
    assert rows[0]["date_estimated"] is True
    assert rows[0]["sources"] == ["greenhouse"]
    assert summary["new"] == [rows[0]["id"]]


def test_missing_date_posted_falls_back_to_today():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    assert rows[0]["date_posted"] == TODAY
    assert rows[0]["date_estimated"] is True


def test_explicit_date_posted_is_not_estimated_and_validates():
    from schema import validate_row

    rows, _ = merge_category(
        [], [_report([_posting(date_posted="2026-07-15")])], TODAY)
    assert rows[0]["date_posted"] == "2026-07-15"
    assert rows[0]["date_estimated"] is False
    assert validate_row(rows[0]) == []


def test_posting_level_date_estimated_flows_into_new_row():
    # A month-granularity pipe-table age ("2mo") still derives a real
    # date_posted, but parse_tracker.py marks the posting itself
    # date_estimated=True -- that flag must survive onto the row, not get
    # silently overwritten to False just because date_posted is present.
    rows, _ = merge_category(
        [], [_report([_posting(date_posted="2026-06-01", date_estimated=True)])], TODAY)
    assert rows[0]["date_posted"] == "2026-06-01"
    assert rows[0]["date_estimated"] is True


def test_missing_date_posted_is_estimated_regardless_of_posting_flag():
    # Defensive: a posting with no date_posted at all must never end up
    # date_estimated=False, even if it explicitly (incorrectly) claims
    # date_estimated=False -- the today-fallback is never a real date.
    rows, _ = merge_category([], [_report([_posting(date_estimated=False)])], TODAY)
    assert rows[0]["date_posted"] == TODAY
    assert rows[0]["date_estimated"] is True


def test_non_us_posting_is_dropped_and_reported(capsys):
    drops = []
    rows, summary = merge_category(
        [], [_report([_posting(location="London, UK")])], TODAY,
        lambda source, stage: drops.append((source, stage)))
    assert rows == [] and summary["new"] == []
    assert drops == [("greenhouse", "non_us_location")]
    assert "skipped non-US location" in capsys.readouterr().out


def test_real_incoming_date_upgrades_estimated_existing_row():
    # First run: no date_posted anywhere, so the row falls back to today
    # with date_estimated: true (e.g. a source that had no age column yet).
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    assert rows[0]["date_estimated"] is True

    # Second run: same link re-found, now carrying a real derived date (e.g.
    # the pipe-table parser's new age-column support). The stale estimate
    # must be replaced, not kept alongside a real date sitting unused.
    rows2, _ = merge_category(
        rows, [_report([_posting(date_posted="2026-06-01")])], "2026-07-25")
    assert len(rows2) == 1
    assert rows2[0]["date_posted"] == "2026-06-01"
    assert rows2[0]["date_estimated"] is False


def test_incoming_date_never_overwrites_a_real_existing_date():
    rows, _ = merge_category(
        [], [_report([_posting(date_posted="2026-05-01")])], TODAY)
    assert rows[0]["date_estimated"] is False

    rows2, _ = merge_category(
        rows, [_report([_posting(date_posted="2026-06-15")])], "2026-07-25")
    assert rows2[0]["date_posted"] == "2026-05-01"
    assert rows2[0]["date_estimated"] is False


def test_estimated_incoming_date_does_not_upgrade_estimated_existing_row():
    # An incoming posting whose own date_posted is itself flagged estimated
    # (e.g. a month-granularity pipe-table age) is not "a real date_posted"
    # -- it must not overwrite an existing estimate either, since that
    # would just swap one guess for another without the honesty gained.
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    assert rows[0]["date_estimated"] is True
    stale_date = rows[0]["date_posted"]

    rows2, _ = merge_category(
        rows,
        [_report([_posting(date_posted="2026-06-01", date_estimated=True)])],
        "2026-07-25")
    assert rows2[0]["date_posted"] == stale_date
    assert rows2[0]["date_estimated"] is True


def test_incoming_posting_without_date_posted_does_not_touch_existing_row():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    stale_date = rows[0]["date_posted"]

    rows2, _ = merge_category(rows, [_report([_posting()])], "2026-07-25")
    assert rows2[0]["date_posted"] == stale_date
    assert rows2[0]["date_estimated"] is True


def test_same_link_across_sources_merges_and_accumulates_sources():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    # Second run: same job, different source, tracking param on the link.
    rows2, summary = merge_category(
        rows,
        [_report([_posting(source="github_tracker",
                           link="https://boards.greenhouse.io/js/jobs/1?utm_source=x")])],
        "2026-07-25")
    assert len(rows2) == 1
    assert set(rows2[0]["sources"]) == {"greenhouse", "github_tracker"}
    assert rows2[0]["last_verified"] == "2026-07-25"
    assert summary["new"] == []


def test_inline_closed_marker_sets_closed():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    rows2, summary = merge_category(
        rows, [_report([_posting(closed_marker=True)])], "2026-07-25")
    assert rows2[0]["status"] == "closed"
    assert summary["closed"] == [rows2[0]["id"]]


def test_same_triple_different_link_is_flagged_not_merged():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    rows2, summary = merge_category(
        rows,
        [_report([_posting(link="https://jobs.lever.co/js/other", source="lever")])],
        "2026-07-25")
    assert len(rows2) == 2
    new_id = summary["new"][0]
    assert (new_id, rows[0]["id"]) in summary["possible_duplicates"]
    assert next(r for r in rows2 if r["id"] == new_id)["possible_duplicate_of"] == rows[0]["id"]


def test_input_rows_not_mutated():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    snapshot = [dict(r) for r in rows]
    merge_category(rows, [_report([_posting(closed_marker=True)])], "2026-07-25")
    assert rows == snapshot


def _existing_row_missing_id(**kw):
    row = {
        "company": "Jane Street", "role": "Quant Trading Intern",
        "location": "New York, NY",
        "link": "https://boards.greenhouse.io/js/jobs/1",
        "term": "Summer 2027", "degree": ["BS"], "status": "open",
        "sources": ["greenhouse"], "date_added": TODAY, "last_verified": TODAY,
        "possible_duplicate_of": None,
        # no "id" key: simulates manual YAML corruption
    }
    row.update(kw)
    return row


def test_missing_id_on_existing_row_found_by_triple_does_not_crash():
    existing = [_existing_row_missing_id()]
    rows, summary = merge_category(
        existing,
        [_report([_posting(link="https://jobs.lever.co/js/other", source="lever")])],
        "2026-07-25")
    assert len(rows) == 2
    new_row = next(r for r in rows if r["link"] != existing[0]["link"])
    assert new_row["possible_duplicate_of"] is None
    assert summary["possible_duplicates"] == []


def test_missing_id_on_existing_row_found_by_link_does_not_crash():
    existing = [_existing_row_missing_id()]
    rows, summary = merge_category(
        existing, [_report([_posting(closed_marker=True)])], "2026-07-25")
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert summary["closed"] == []


def test_incoming_date_after_date_added_does_not_upgrade_estimate():
    # 2026-09-01 audit: 30 rows carried date_posted later than date_added
    # because a tracker's own add-date was taken as the posting date.
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    assert rows[0]["date_added"] == TODAY and rows[0]["date_estimated"] is True
    rows2, _ = merge_category(
        rows, [_report([_posting(date_posted="2026-07-30")])], "2026-08-01")
    assert rows2[0]["date_posted"] == TODAY
    assert rows2[0]["date_estimated"] is True
    rows3, _ = merge_category(
        rows, [_report([_posting(date_posted="2026-07-10")])], "2026-08-01")
    assert rows3[0]["date_posted"] == "2026-07-10"
    assert rows3[0]["date_estimated"] is False
