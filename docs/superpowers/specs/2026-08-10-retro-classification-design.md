# Retro-Classification Sweep — Design Spec

**Date:** 2026-08-10
**Status:** Approved, pending implementation plan

## Purpose

`classify_role` runs only on incoming postings. Nothing re-classifies rows already
in `data/*.yaml`, so every rule added to `categorize.py` improves only future
scrapes and leaves the existing corpus stale.

A sweep of all 817 open rows on 2026-08-10 found **127 disagreements** between
`classify_role(row["role"])` and the file the row lives in. Some are deliberate
(`Citadel Securities | Software Engineer` in `quant.yaml` — a SWE role at a quant
firm). Some are real errors: `Optiver | FPGA Engineer Intern` ×2 sits in
`quant.yaml` against the standing convention that quant-firm hardware/FPGA roles
belong in `hardware.yaml`. This is the same class of error found by eye twice in
one session on 2026-08-09 ("the first row of swe is literally titled electrical
engineering intern"), and it recurs on every rule change.

This spec adds a re-runnable sweep that reports disagreements, a review file to
adjudicate them, and a way to record a decision so it never reports again.

## Relationship to the 2026-07-24 category-stability decision

`2026-07-24-cheap-tracker-scraping-design.md` § "Link-hash gate and category
stability" states: *"A link already in `data/*.yaml` is never classified and never
reclassified. It keeps whatever category file it currently lives in,"* and calls
this *"not an optimization, it is a correctness requirement"* — because
`merge_category` dedupes within one category file only, so a link classified
`hardware` this run and `quant` last run would exist in two files with nothing to
catch it. `categorize.py`'s `manual_link_categories` docstring says the same:
*"a manual entry can never recategorize a tracked link."*

**That decision stands. This spec amends its scope, not its reasoning.** What the
correctness requirement forbids is *automatic, per-run* reclassification, where the
same link can land in different files on successive scrapes. This sweep is a
one-shot, human-reviewed move executed by the single serialized writer
(`apply_ats_corrections.py`). After a move the link exists in exactly one file, and
the link-hash gate loads links from *all* of `data/*.yaml`, so the next scrape still
skips the row entirely. No run-to-run churn is introduced.

Two consequences follow, and the implementation must preserve both:

- The classify-time path is untouched. `fetch_trackers.py` keeps winning over
  `manual_categories.yaml` for tracked links.
- A `keep` entry written to `manual_categories.yaml` is therefore inert for the
  merge path — its only consumer is `check_categories.py`'s "already adjudicated"
  check. It becomes live again only if the row is later deleted and the posting
  rediscovered, at which point it reproduces the same decision. That is a bonus,
  not a conflict.

## Non-goals

- **Not automatic reclassification.** `classify_role` is not authoritative over a
  human placement; nothing moves without review. See the section above.
- **Not a change to `classify_role` itself.** The sweep reports what the current
  rules say. Fixing a rule that says the wrong thing is separate work.
- **Not closed rows.** Closed listings are out of scope by standing preference —
  only currently-open roles are swept.
- **Not a fix for the row id/link drift bug.** `recategorize` moves a row between
  files without touching `id` or `link`, so it neither triggers nor fixes it.

## Architecture

```
scripts/check_categories.py                 (new)
    reads data/*.yaml + sources/manual_categories.yaml
    -> scratch/category_corrections.json    {"generated": "...", "actions": [...]}
              |
        Tony edits the "action" field per entry
              |
              v
scripts/apply_ats_corrections.py            (existing single serialized writer)
    -> moves / deletes rows, appends to sources/manual_categories.yaml,
       rewrites every changed data/*.yaml, re-renders README.md
```

`check_categories.py` is one module holding a pure `find_disagreements()` plus a
`main()` that does file I/O. It deliberately does **not** follow the
`ats_verify.py`/`check_ats.py` and `repost_verify.py`/`check_reposts.py`
pure/driver split: that split exists to quarantine fragile, source-specific
network code (`docs/SCRAPING.md`), and this sweep touches no network. A second
file would be symmetry without a reason.

`check_integrity.py` was considered as a host — it already carries the advisory
`sweep_off_cycle` and `triple_groups` sweeps, and `auto_scrape.sh` already calls
it. Rejected: its value is that it only ever reports, never writes. Emitting a
corrections file would break that property.

## Component: `find_disagreements`

```python
find_disagreements(rows_by_category: dict, overrides: dict) -> list[dict]
```

Pure; returns proposed actions. For every row, in order:

1. Skip if `row.get("status") == "closed"`.
2. Skip if `normalize_link(row["link"])` is a key in `overrides`
   (`sources/manual_categories.yaml`) — already adjudicated, and authoritative.
3. `got = classify_role(row["role"])`.
4. Skip if `got is None` — the rules have no opinion (109 rows on 2026-08-10).
5. Skip if `got == cat` — agreement.
6. Otherwise emit one action.

Each action carries `id`, `action`, `from`, `to`, `company`, `role`, `link` — enough
to judge an entry without opening the YAML.

## Component: three action kinds in `apply_ats_corrections.py`

| Action | Proposed when | Effect |
| --- | --- | --- |
| `recategorize` | `got` is another category | Move the row dict from `rows_by_category[from]` to `[to]`. |
| `drop` | `got == "__drop__"` | Delete the row; append `link: __drop__` to `manual_categories.yaml`. |
| `keep` | never proposed | Leave the row; append `link: <from>` to `manual_categories.yaml`. |

**`recategorize` does not rehash the id.** A row's `id` is
`_slug(company, role, normalize_link(link))` and does not embed the category —
`check_integrity` prints `cat/id` for display only. Moving a row between files
changes neither `id` nor `link`, so no id churn and no drift.

**`keep` is the load-bearing action.** Roughly 110 of the 127 disagreements are
deliberate placements, so recording "leave it alone" must cost one word. Tony
changes an entry's `"action"` to `"keep"` and that row never reports again. Without
this, run two is ~110 rows of noise around a handful of real errors.

**`drop` reuses the existing suppression path** — the same `manual_categories.yaml`
append that `apply_ats_corrections.py` already performs for superseded repost links
and that `verify_links.py` performs for non-2027 rows. Deletion (not closing)
matches the existing `delete_non_us` precedent.

### Error handling

- A `to` value outside `CATEGORIES` goes to the existing `unrecognized_action`
  summary bucket — a typo must not crash a delete-capable run mid-loop.
- A `recategorize` or `keep` missing its `from`/`to` key follows the same
  `"new" not in a` degradation the existing `set_date` and `repost` actions use:
  reported as `unrecognized_action`, not applied.
- An `id` no longer present in the data goes to `skipped`, unchanged from today.
- The existing pre-write `ROW_SCHEMA` gate still runs. A moved row is byte-identical
  to what it was, so `recategorize` cannot introduce a schema error.

## `auto_scrape.sh` integration

Two changes:

1. **In-flight guard (line 30).** Add `scratch/category_corrections.json` beside
   `scratch/ats_corrections.json`: never scrape while a category review is in
   flight, per the single-writer discipline in `docs/SCRAPING.md`.
2. **Report after `verify_links.py`.** Call `check_categories.py --report-only`,
   which appends `N category disagreement(s) — run check_categories.py` to
   `scratch/auto_scrape/NEEDS_ATTENTION` and returns 0 so the run still commits.

`--report-only` writes no JSON, and that is the point. If the scrape wrote the
corrections file itself, the first run would find 127 disagreements and then block
every subsequent scrape via the new guard until the review was finished.
Report-only keeps the guard meaning exactly what it means for ATS: the file exists
only while Tony is mid-review. A stale `NEEDS_ATTENTION` does not block a later run
(only `ats_corrections.json` and the lock directory do), so the append is safe.

Bare `python3 scripts/check_categories.py` writes the JSON, matching its
`check_ats.py` / `check_reposts.py` / `check_programs.py` siblings.

## Testing

New `tests/test_check_categories.py`, covering `find_disagreements`:

- a row whose role classifies to its own category → no action
- `classify_role` returning `None` → no action
- a closed row → skipped even when it disagrees
- a row whose link is in `overrides` → skipped even when it disagrees
- a cross-category disagreement → one `recategorize` with correct `from`/`to`
- a role classifying to `__drop__` → one `drop`

Additions to `tests/test_apply_ats_corrections.py`:

- `recategorize` removes the row from `from` and appends it to `to`, with `id` and
  `link` unchanged
- `keep` leaves every category file untouched and appends `link: <from>` to
  `manual_categories.yaml`
- `drop` deletes the row and appends `link: __drop__`
- an unknown `to` lands in `unrecognized_action` and writes nothing

Verification gate, unchanged from repo standing rules: `python3 -m pytest tests/ -v`
green, then `python3 scripts/check_integrity.py` clean before any commit touching
`data/`.

## Run-book

```bash
python3 scripts/check_categories.py                                   # -> scratch/category_corrections.json
#   review the file; set "action" to "keep" on every deliberate placement
python3 scripts/apply_ats_corrections.py scratch/category_corrections.json
python3 scripts/check_integrity.py                                    # then commit
```
