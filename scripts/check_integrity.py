"""Integrity invariants for the merged tracker data, checked across every
category at once. Pure: no I/O, no network — loading data/*.yaml from disk
belongs in the __main__ block only.

merge_category() (see merge.py) dedupes strictly *within* one category, so
it structurally cannot catch a posting that's been tracked under two
different category files. This module is the cross-category check that
fills that gap.

Invariants 1-5 are blocking: check_integrity() covers exactly these (plus a
malformed-link guard invariant 3's schema check can't catch — see
_classify_link) and its return value drives the process exit code.
Invariant 6 is advisory only:
triple_groups() covers it separately and never affects the exit code — a
bare (company, role, location) triple carries real false-merge risk, so
matches are surfaced for manual review, never auto-merged, and a group
still showing up after a repair pass (because it was reviewed and kept) is
an expected outcome, not an error to gate on."""
import sys
from collections import defaultdict

from normalize import normalize_link
from merge import _triple
from schema import validate_row


def _all_rows(rows_by_category: dict) -> list:
    """Flatten to [(category, row), ...] across every category, in a stable
    (category name, then original file order) sequence."""
    return [
        (category, row)
        for category in sorted(rows_by_category)
        for row in rows_by_category[category]
    ]


def _describe(category: str, row: dict) -> str:
    """Short, actionable pointer to a row: category, id, company, role —
    enough to find it in data/<category>.yaml without grepping blind.
    company/role use !r rather than raw interpolation: both are free text
    from scraped sources and a literal pipe or newline in either would
    otherwise split one violation across multiple lines, breaking the
    one-violation-per-line contract __main__ (and the merge-pipeline gate)
    depend on — generate_readme.py hit this same bug class with unescaped
    company/role text and needed _escape_cell to fix it."""
    rid = row.get("id") or "<no id>"
    company = row.get("company") or "<no company>"
    role = row.get("role") or "<no role>"
    return f"{category}/{rid} ({company!r} - {role!r})"


def _sorted_group(group: list) -> list:
    """Deterministic order for a group of (category, row) pairs, independent
    of dict-insertion order, so the resulting violation string is stable."""
    return sorted(group, key=lambda cr: (cr[0], str(cr[1].get("id"))))


def _classify_link(row: dict):
    """Classify row['link'] for link-related checks. Returns (kind, value):

      ("missing", None) — link is absent (None) or not a string, or is the
        empty string "". ROW_SCHEMA already requires link (required +
        type: string + minLength: 1), so invariant 3 fully covers this
        case; there's nothing new to say here.

      ("malformed", reason) — link IS a non-empty string, but is blank
        after stripping whitespace, raises inside normalize_link(), or
        normalizes to an empty value. None of these are schema errors:
        minLength:1 only checks the raw JSON string's length, so e.g. a
        three-space link "   " is schema-valid. Without this case such a
        row would silently vanish from invariant 2 with no trace anywhere
        — a real gap, not a duplicate of invariant 3.

      ("ok", normalized) — link normalized successfully; usable for
        invariant 2's uniqueness check."""
    link = row.get("link")
    if not isinstance(link, str) or link == "":
        return ("missing", None)
    if not link.strip():
        return ("malformed", "blank (whitespace-only) link")
    try:
        normalized = normalize_link(link)
    except Exception as e:
        return ("malformed", f"failed to parse: {e}")
    if not normalized:
        return ("malformed", "normalized to an empty value")
    return ("ok", normalized)


def _safe_triple(row: dict):
    """merge._triple(row), or None if the row is too degraded to group
    (company/role/location missing or non-string) or if its location
    doesn't confidently resolve to a US city/state (canonicalize_location
    returns None, so _triple's third element is None). Grouping every
    None-location row into one shared bucket would false-merge unrelated
    postings that merely have an unparseable location in common, which is
    worse than missing the group — so those rows are excluded from
    triple-based grouping entirely (both invariant 5 and invariant 6)."""
    if not all(isinstance(row.get(f), str) for f in ("company", "role", "location")):
        return None
    try:
        triple = _triple(row)
    except Exception:
        return None
    return triple if triple[2] is not None else None


def _group_by_triple(pairs: list) -> dict:
    groups = defaultdict(list)
    for category, row in pairs:
        triple = _safe_triple(row)
        if triple is not None:
            groups[triple].append((category, row))
    return groups


def _check_id_uniqueness(pairs: list) -> list:
    by_id = defaultdict(list)
    for category, row in pairs:
        rid = row.get("id")
        if isinstance(rid, str) and rid:
            by_id[rid].append((category, row))
    violations = []
    for rid, group in by_id.items():
        if len(group) > 1:
            # Two rows sharing an id almost always share company/role too
            # (the id is a slug of exactly those) — _describe() alone would
            # print two identical descriptors. The link is what actually
            # distinguishes them, so include it.
            entries = ", ".join(
                f"{_describe(c, r)} link={r.get('link')!r}"
                for c, r in _sorted_group(group)
            )
            violations.append(f"duplicate id {rid!r} in {len(group)} rows: {entries}")
    return violations


def _check_link_uniqueness(pairs: list) -> list:
    by_link = defaultdict(list)
    for category, row in pairs:
        kind, nlink = _classify_link(row)
        if kind == "ok":
            by_link[nlink].append((category, row))
    violations = []
    for nlink, group in by_link.items():
        if len(group) > 1:
            # status is included so a reader can tell at a glance whether a
            # duplicate-link pair also disagrees on status — invariant 5 is
            # deliberately scoped to _triple groups only, so it won't catch
            # that here if the two rows' company/role text differs (e.g. by
            # a trailing emoji marker) even though the link is identical.
            entries = ", ".join(
                f"{_describe(c, r)} link={r.get('link')!r} status={r.get('status')!r}"
                for c, r in _sorted_group(group)
            )
            violations.append(f"duplicate link {nlink!r} in {len(group)} rows: {entries}")
    return violations


def _check_malformed_links(pairs: list) -> list:
    """Blocking: a row whose link is present but unusable (see
    _classify_link's "malformed" case) gets its own violation instead of
    silently dropping out of _check_link_uniqueness with no trace."""
    violations = []
    for category, row in pairs:
        kind, reason = _classify_link(row)
        if kind == "malformed":
            rid = row.get("id") or "<no id>"
            violations.append(
                f"[{category}] row {rid!r} link {row.get('link')!r} is "
                f"malformed ({reason}); excluded from duplicate-link detection"
            )
    return violations


def _check_schema(pairs: list) -> list:
    violations = []
    for category, row in pairs:
        errors = validate_row(row)
        if errors:
            rid = row.get("id") or "<no id>"
            violations.append(f"[{category}] row {rid!r} fails schema: {errors}")
    return violations


def _check_duplicate_refs(pairs: list) -> list:
    known_ids = {
        row.get("id") for _, row in pairs
        if isinstance(row.get("id"), str) and row.get("id")
    }
    violations = []
    for category, row in pairs:
        dup = row.get("possible_duplicate_of")
        if dup is None:
            continue
        rid = row.get("id") or "<no id>"
        if not isinstance(dup, str):
            # Malformed value (e.g. a hand-edited list/dict) — schema check
            # already flags the row; just don't crash comparing it here.
            continue
        if dup == rid:
            violations.append(
                f"[{category}] row {rid!r} possible_duplicate_of points at itself"
            )
        elif dup not in known_ids:
            violations.append(
                f"[{category}] row {rid!r} possible_duplicate_of={dup!r} "
                f"references an id that doesn't exist"
            )
    return violations


def _check_status_agreement(pairs: list) -> list:
    violations = []
    for triple, group in _group_by_triple(pairs).items():
        if len(group) < 2:
            continue
        statuses = {row.get("status") for _, row in group}
        if len(statuses) > 1:
            entries = ", ".join(
                f"{_describe(c, r)} status={r.get('status')!r}"
                for c, r in _sorted_group(group)
            )
            violations.append(f"status disagreement for triple {triple!r}: {entries}")
    return violations


def check_integrity(rows_by_category: dict) -> list:
    """Blocking invariants only (1-5): id uniqueness, normalize_link
    uniqueness, schema validity, possible_duplicate_of referential
    integrity, and status agreement within a _triple group — all checked
    across every category at once. Also blocking: a row whose link is
    present but too malformed to normalize (see _classify_link) — this
    isn't one of the plan's original 5 invariants by number, but it's a
    real gap ROW_SCHEMA doesn't cover (minLength:1 passes a whitespace-only
    or unparseable link) and belongs in the same blocking set rather than
    silently opting a row out of invariant 2. Returns [] when clean; never
    mutates input. The returned list is always sorted, so two runs over the
    same data diff cleanly."""
    pairs = _all_rows(rows_by_category)
    violations = (
        _check_id_uniqueness(pairs)
        + _check_link_uniqueness(pairs)
        + _check_malformed_links(pairs)
        + _check_schema(pairs)
        + _check_duplicate_refs(pairs)
        + _check_status_agreement(pairs)
    )
    return sorted(violations)


def triple_groups(rows_by_category: dict) -> list:
    """Advisory only (invariant 6): every group of >=2 rows sharing a
    _triple across categories, regardless of whether their status agrees.
    Report only, never auto-merge — a bare (company, role, location) triple
    carries real false-merge risk. Does NOT affect check_integrity()'s
    return value or the process exit code; a group that's been reviewed and
    intentionally kept as two rows will still show up here forever, and
    that's expected, not a bug."""
    pairs = _all_rows(rows_by_category)
    violations = []
    for triple, group in _group_by_triple(pairs).items():
        if len(group) >= 2:
            entries = ", ".join(_describe(c, r) for c, r in _sorted_group(group))
            violations.append(f"possible duplicate group {triple!r}: {entries}")
    return sorted(violations)


if __name__ == "__main__":
    import yaml
    from generate_readme import CATEGORIES, ROOT

    data_dir = ROOT / "data"
    rows_by_category = {}
    for stem, _title, _is_quant in CATEGORIES:
        path = data_dir / f"{stem}.yaml"
        rows_by_category[stem] = (
            (yaml.safe_load(path.read_text()) or []) if path.exists() else []
        )

    violations = check_integrity(rows_by_category)
    for v in violations:
        print(v)

    groups = triple_groups(rows_by_category)
    if groups:
        print(f"\nADVISORY: {len(groups)} possible-duplicate triple group(s) "
              f"(report only, not auto-merged):")
        for g in groups:
            print(g)

    if violations:
        print(f"\n{len(violations)} blocking violation(s).")
        sys.exit(1)
    print("\nNo blocking violations.")
