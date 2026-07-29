from check_integrity import check_integrity, triple_groups


def _row(**kw):
    base = {
        "id": "jane-street-quant-trading-intern-a1b2c3",
        "company": "Jane Street",
        "role": "Quantitative Trading Intern",
        "location": "New York, NY",
        "link": "https://boards.greenhouse.io/janestreet/jobs/123",
        "date_posted": "2026-07-15",
        "term": "Summer 2027",
        "degree": ["BS", "MS"],
        "status": "open",
        "sources": ["greenhouse"],
        "date_added": "2026-07-22",
        "last_verified": "2026-07-22",
        "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def test_clean_data_across_categories_has_no_violations():
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://boards.greenhouse.io/x/jobs/1")],
        "quant": [_row(
            id="b-1", company="Citadel", role="Trading Intern",
            location="Chicago, IL", link="https://boards.greenhouse.io/x/jobs/2",
        )],
    }
    assert check_integrity(rows_by_category) == []
    assert triple_groups(rows_by_category) == []


# --- invariant 1: id uniqueness -------------------------------------------

def test_duplicate_id_across_categories_is_flagged():
    rows_by_category = {
        "swe": [_row(id="dup-1", link="https://a.com/1")],
        "quant": [_row(
            id="dup-1", company="Citadel", role="Other Role",
            location="Chicago, IL", link="https://a.com/2",
        )],
    }
    violations = check_integrity(rows_by_category)
    hit = [v for v in violations if "dup-1" in v]
    assert len(hit) == 1
    assert "swe" in hit[0] and "quant" in hit[0]


def test_duplicate_id_message_includes_both_links_to_disambiguate():
    # Two rows sharing an id also share company/role (the id is a slug of
    # those) — the link is the only thing that tells them apart, so it must
    # be in the message, not just in _describe()'s company/role.
    rows_by_category = {
        "swe": [
            _row(id="dup-1", link="https://a.com/1"),
            _row(id="dup-1", link="https://a.com/2"),
        ],
    }
    violations = check_integrity(rows_by_category)
    hit = [v for v in violations if "duplicate id" in v]
    assert len(hit) == 1
    assert "https://a.com/1" in hit[0] and "https://a.com/2" in hit[0]


def test_no_id_duplicate_violation_when_ids_differ():
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1")],
        "quant": [_row(
            id="a-2", company="Citadel", location="Chicago, IL",
            link="https://a.com/2",
        )],
    }
    assert not any("duplicate id" in v for v in check_integrity(rows_by_category))


# --- invariant 2: normalize_link uniqueness -------------------------------

def test_duplicate_link_across_categories_is_flagged():
    # Same job, tracking param on one copy — normalize_link should collapse
    # both to the same canonical link.
    rows_by_category = {
        "swe": [_row(
            id="a-1", link="https://boards.greenhouse.io/x/jobs/1?utm_source=li",
        )],
        "quant": [_row(
            id="b-1", company="Citadel", role="Other Role",
            location="Chicago, IL", link="https://boards.greenhouse.io/x/jobs/1",
        )],
    }
    violations = check_integrity(rows_by_category)
    hit = [v for v in violations if "duplicate link" in v]
    assert len(hit) == 1
    assert "a-1" in hit[0] and "b-1" in hit[0]


def test_different_links_do_not_trip_link_uniqueness():
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1")],
        "quant": [_row(
            id="b-1", company="Citadel", location="Chicago, IL",
            link="https://a.com/2",
        )],
    }
    assert not any("duplicate link" in v for v in check_integrity(rows_by_category))


# --- invariant 3: schema validity ------------------------------------------

def test_schema_violation_is_reported():
    rows_by_category = {"swe": [_row(status="maybe")]}
    violations = check_integrity(rows_by_category)
    assert any("fails schema" in v and "swe" in v for v in violations)


def test_row_missing_id_does_not_crash_and_is_reported():
    row = _row()
    del row["id"]
    rows_by_category = {"swe": [row]}
    violations = check_integrity(rows_by_category)
    assert any("fails schema" in v for v in violations)
    assert triple_groups(rows_by_category) == []


def test_row_missing_link_does_not_crash_and_is_reported():
    row = _row()
    del row["link"]
    rows_by_category = {"swe": [row]}
    violations = check_integrity(rows_by_category)
    assert any("fails schema" in v for v in violations)


# --- invariant 4: possible_duplicate_of referential integrity -------------

def test_possible_duplicate_of_self_reference_is_flagged():
    rows_by_category = {"swe": [_row(id="self-1", possible_duplicate_of="self-1")]}
    violations = check_integrity(rows_by_category)
    assert any("self-1" in v and "itself" in v for v in violations)


def test_possible_duplicate_of_unknown_id_is_flagged():
    rows_by_category = {
        "swe": [_row(id="a-1", possible_duplicate_of="does-not-exist")],
    }
    violations = check_integrity(rows_by_category)
    assert any("does-not-exist" in v for v in violations)


def test_possible_duplicate_of_valid_reference_is_clean():
    rows_by_category = {
        "swe": [
            _row(id="a-1", link="https://a.com/1"),
            _row(
                id="a-2", link="https://a.com/2", role="Different Role",
                possible_duplicate_of="a-1",
            ),
        ],
    }
    assert check_integrity(rows_by_category) == []


def test_possible_duplicate_of_none_is_never_flagged():
    rows_by_category = {"swe": [_row(id="a-1", possible_duplicate_of=None)]}
    assert check_integrity(rows_by_category) == []


# --- invariant 5: status agreement within a _triple group -----------------

def test_status_disagreement_within_triple_group_is_flagged():
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1", status="open")],
        "data_science": [_row(id="a-2", link="https://a.com/2", status="closed")],
    }
    violations = check_integrity(rows_by_category)
    hit = [v for v in violations if "status disagreement" in v]
    assert len(hit) == 1
    assert "a-1" in hit[0] and "a-2" in hit[0]


def test_status_agreement_within_triple_group_is_clean_for_check_integrity():
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1", status="open")],
        "data_science": [_row(id="a-2", link="https://a.com/2", status="open")],
    }
    # Same triple, same status: not a *blocking* violation (invariant 5),
    # even though the pair is still surfaced by the advisory invariant 6.
    assert not any("status disagreement" in v for v in check_integrity(rows_by_category))


def test_single_row_triple_is_never_a_status_violation():
    rows_by_category = {"swe": [_row(id="a-1")]}
    assert check_integrity(rows_by_category) == []


# --- invariant 6: triple_groups (advisory, report-only) --------------------

def test_triple_group_of_two_is_reported_by_advisory_function():
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1")],
        "data_science": [_row(id="a-2", link="https://a.com/2")],
    }
    groups = triple_groups(rows_by_category)
    assert len(groups) == 1
    assert "a-1" in groups[0] and "a-2" in groups[0]


def test_triple_groups_empty_for_singletons():
    rows_by_category = {"swe": [_row(id="a-1")]}
    assert triple_groups(rows_by_category) == []


def test_triple_groups_never_affects_check_integrity_exit_value():
    # A triple group that would show up in the advisory report (same
    # company/role/location, agreeing status) must not itself appear in
    # check_integrity()'s blocking output.
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1")],
        "data_science": [_row(id="a-2", link="https://a.com/2")],
    }
    assert triple_groups(rows_by_category) != []
    assert check_integrity(rows_by_category) == []


def test_unparseable_location_rows_are_not_grouped_by_triple():
    # canonicalize_location can't resolve "somewhere vague" to a US city/
    # state, so _triple's location element is None for both rows. Grouping
    # them together under a shared None key would false-merge two unrelated
    # postings that merely share a garbage location string.
    rows_by_category = {
        "swe": [_row(id="a-1", link="https://a.com/1", location="somewhere vague")],
        "quant": [_row(id="a-2", link="https://a.com/2", location="somewhere vague")],
    }
    assert triple_groups(rows_by_category) == []
    assert check_integrity(rows_by_category) == []


# --- purity / determinism ---------------------------------------------------

def test_check_integrity_does_not_mutate_input():
    rows_by_category = {"swe": [_row(id="a-1")]}
    snapshot = {"swe": [dict(rows_by_category["swe"][0])]}
    check_integrity(rows_by_category)
    triple_groups(rows_by_category)
    assert rows_by_category == snapshot


def test_check_integrity_output_is_sorted():
    rows_by_category = {
        "swe": [_row(id="z-1", link="https://a.com/1", status="maybe")],
        "quant": [_row(
            id="a-1", company="Citadel", location="Chicago, IL",
            link="https://a.com/2", status="also-bad",
        )],
    }
    violations = check_integrity(rows_by_category)
    assert len(violations) == 2
    assert violations == sorted(violations)
