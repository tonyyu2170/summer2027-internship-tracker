"""Integrity invariants for the merged tracker data, checked across every
category at once. Pure: no I/O, no network — loading data/*.yaml from disk
belongs in the __main__ block only.

merge_category() (see merge.py) dedupes strictly *within* one category, so
it structurally cannot catch a posting that's been tracked under two
different category files. This module is the cross-category check that
fills that gap.

The blocking/advisory split follows identity STRENGTH, not a fixed
invariant count:

  BLOCKING (check_integrity(), drives the process exit code): everything
  scoped to the normalized LINK — the repo's PRIMARY dedup key (see
  normalize.normalize_link / merge.py's by_link). Two rows sharing a
  normalized link are the same posting by definition, so a duplicate link,
  or a status disagreement between them, is a defect, full stop. Also
  blocking for the same "this is unambiguously wrong" reason: id
  uniqueness, malformed (present-but-unusable) links, ROW_SCHEMA validity,
  and possible_duplicate_of referential integrity.

  ADVISORY (triple_groups() and triple_status_disagreements(), neither
  affects the exit code): everything scoped to a bare (company, role,
  location) TRIPLE — the repo's low-confidence FALLBACK identity, used
  only when a source has no extractable link (see merge._triple). A
  shared triple does NOT mean "same posting": two different requisitions
  for the same role at the same company/location are common, and they can
  legitimately have different links and different statuses (one filled,
  one still open). Live examples in this data: Hudson River Trading (two
  distinct Greenhouse gh_jid requisitions — one closed, one open) and
  Quadrillion (two distinct Ashby posting UUIDs, same pattern). Neither is
  a defect; the data-repair task leaves both as two separate rows on
  purpose. Do NOT re-promote triple-level status agreement to the blocking
  set — that would make check_integrity() exit non-zero forever on exactly
  this legitimate case."""
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


def _group_by_link(pairs: list) -> dict:
    """Group rows by normalized link — the repo's PRIMARY dedup key. Only
    rows classified "ok" by _classify_link participate; "missing" and
    "malformed" links are handled by their own dedicated checks."""
    groups = defaultdict(list)
    for category, row in pairs:
        kind, nlink = _classify_link(row)
        if kind == "ok":
            groups[nlink].append((category, row))
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
    violations = []
    for nlink, group in _group_by_link(pairs).items():
        if len(group) > 1:
            # status is included so a reader can tell at a glance whether a
            # duplicate-link pair also disagrees on status (see
            # _check_link_status_agreement, which flags that specifically).
            entries = ", ".join(
                f"{_describe(c, r)} link={r.get('link')!r} status={r.get('status')!r}"
                for c, r in _sorted_group(group)
            )
            violations.append(f"duplicate link {nlink!r} in {len(group)} rows: {entries}")
    return violations


def _check_link_status_agreement(pairs: list) -> list:
    """BLOCKING: status agreement within a normalized-LINK group. Same
    normalized link is the repo's PRIMARY dedup key, so two rows sharing
    one are the same posting by definition — a status disagreement here is
    a defect, not a judgment call. (Contrast triple_status_disagreements,
    which checks the same thing at the low-confidence TRIPLE key and is
    advisory only, precisely because a shared triple is not reliable
    identity.)"""
    violations = []
    for nlink, group in _group_by_link(pairs).items():
        if len(group) < 2:
            continue
        statuses = {row.get("status") for _, row in group}
        if len(statuses) > 1:
            entries = ", ".join(
                f"{_describe(c, r)} status={r.get('status')!r}"
                for c, r in _sorted_group(group)
            )
            violations.append(f"status disagreement for link {nlink!r}: {entries}")
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


def check_integrity(rows_by_category: dict) -> list:
    """BLOCKING checks only, all scoped to strong identity (id or
    normalized link — see module docstring): id uniqueness, normalize_link
    uniqueness, status agreement within a link group, malformed
    (present-but-unusable) links, ROW_SCHEMA validity, and
    possible_duplicate_of referential integrity — all checked across every
    category at once. Returns [] when clean; never mutates input. The
    returned list is always sorted, so two runs over the same data diff
    cleanly.

    Deliberately does NOT include status agreement within a _triple group
    — see triple_status_disagreements(), which reports that as advisory
    only, and the module docstring for why (Hudson River Trading,
    Quadrillion)."""
    pairs = _all_rows(rows_by_category)
    violations = (
        _check_id_uniqueness(pairs)
        + _check_link_uniqueness(pairs)
        + _check_link_status_agreement(pairs)
        + _check_malformed_links(pairs)
        + _check_schema(pairs)
        + _check_duplicate_refs(pairs)
    )
    return sorted(violations)


def sweep_off_cycle(rows_by_category: dict) -> list:
    """ADVISORY only: stored rows whose role text reads as a cycle other than
    Summer 2027, per parse_tracker._is_off_cycle. These predate the widened
    detection, so the parser would reject them today but they are already on
    disk. Report only -- some role strings are truncated by their upstream
    tracker ("Hardware R&D Engineering Intern (Fall..."), which makes the real
    cycle undeterminable from the text alone and needs the posting fetched.
    Deleting on this signal alone would drop live rows."""
    from parse_tracker import _is_off_cycle
    return sorted(
        f"{category} {row.get('id')} {row.get('role')!r}"
        for category, row in _all_rows(rows_by_category)
        if isinstance(row.get("role"), str) and _is_off_cycle(row["role"])
    )


def triple_groups(rows_by_category: dict) -> list:
    """ADVISORY only: every group of >=2 rows sharing a _triple across
    categories, regardless of whether their status agrees. Report only,
    never auto-merge — a bare (company, role, location) triple carries
    real false-merge risk. Does NOT affect check_integrity()'s return
    value or the process exit code; a group that's been reviewed and
    intentionally kept as two rows will still show up here forever, and
    that's expected, not a bug."""
    pairs = _all_rows(rows_by_category)
    violations = []
    for triple, group in _group_by_triple(pairs).items():
        if len(group) >= 2:
            entries = ", ".join(_describe(c, r) for c, r in _sorted_group(group))
            violations.append(f"possible duplicate group {triple!r}: {entries}")
    return sorted(violations)


def triple_status_disagreements(rows_by_category: dict) -> list:
    """ADVISORY only: status disagreement within a _triple group. Distinct
    from triple_groups() — this reports only the subset of triple groups
    that disagree on status, so a reader can tell "these might be the same
    posting" (triple_groups) apart from "these disagree on open/closed"
    (this function); a group can appear in one, the other, both, or
    neither.

    A bare (company, role, location) triple is NOT reliable identity (see
    module docstring), so a status disagreement here is a SIGNAL, not
    necessarily a defect: two different requisitions for the same role at
    the same company/location can legitimately have different links and
    different statuses (one filled, one still open). Live examples: Hudson
    River Trading (two distinct Greenhouse gh_jid requisitions) and
    Quadrillion (two distinct Ashby posting UUIDs) both trip this and are
    NOT defects. Never affects check_integrity()'s return value or the
    process exit code — do not promote this to blocking."""
    pairs = _all_rows(rows_by_category)
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

    if "--sweep" in sys.argv:
        lines = sweep_off_cycle(rows_by_category)
        out = ROOT / "scratch" / "off_cycle_review.txt"
        out.parent.mkdir(exist_ok=True)
        out.write_text("\n".join(lines) + ("\n" if lines else ""))
        print(f"{len(lines)} row(s) flagged off-cycle -> {out}")
        print("Nothing was deleted. Review by hand before acting.")
        sys.exit(0)

    violations = check_integrity(rows_by_category)
    for v in violations:
        print(v)

    status_disagreements = triple_status_disagreements(rows_by_category)
    if status_disagreements:
        print(f"\nADVISORY: {len(status_disagreements)} triple-level status "
              f"disagreement(s) (NOT necessarily defects — a bare triple is "
              f"not reliable identity; see module docstring):")
        for s in status_disagreements:
            print(s)

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
