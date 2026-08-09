# ATS-API verification pass + README column cleanup

**Date:** 2026-08-08
**Status:** Implemented.

## Motivation

Tracker-table text is unreliable for location and posting date: speedyapply
published "Washington, DC" for a Redmond, WA Microsoft role (hand-fixed in
c82a18c), and the 2026-08 date backfill showed age columns are coarse or
absent. Wherever a row's link points at a real ATS, that ATS's own API is
authoritative — stop trusting scraped table text for those rows.

Of 901 open rows, ~517 sit on an API-covered ATS: Workday (211, CXS JSON),
Greenhouse (131, boards-api), Ashby (71, embedded posting JSON),
Lever (51, v0 postings), iCIMS (30, JSON-LD in page), SmartRecruiters (23,
public postings API). All six endpoints were proven in the 2026-08-08
enrichment run. Custom career sites (~384 open rows) have no authoritative
API and are out of scope.

## Decisions (settled with Tony, 2026-08-08)

1. **Field scope:** the API may overwrite `location` and `date_posted`, and
   may close a row (`status: closed`, same semantics as a source-side
   `closed_marker`). `role`/`term`/`degree` are never touched.
2. **Location is not a tracked field.** *(Amended 2026-08-08: "i dont care
   much about location, we can get rid of location. as long as they are all
   US" — Tony.)* The API never rewrites `location`, and the README job
   tables do not render it. The field stays in `data/*.yaml` because
   `merge.py`'s fallback dedup key and the merge-time US-only filter both
   read it. The `set_location` and `location_unresolved` actions are gone.
3. **All-non-US:** a row is **deleted** when the API's **country field**
   affirmatively names a non-US country (`_NON_US_RE` matched against the
   country field alone). Location text never authorizes a delete, and no
   location parse can veto one. *(Amended three times on 2026-08-08 with
   Tony's approval. From "delete when nothing canonicalizes US", which
   would have false-deleted US rows with city-only locations. Then dropping
   "or a location matches `normalize._NON_US_RE`" — that pattern was built
   for strings already containing "remote", and `on` matched the "on"
   in "on-site", so "Chicago, IL (On-Site)" read as Ontario. The country
   check is affirmative rather than `not _is_us_country(...)`, so
   unrecognized US spellings like "U.S." are not non-US evidence. Finally,
   with location no longer tracked, the country field is the whole rule.
   Accepted consequence: a country the pattern does not recognize —
   "Germany", "Japan" — is not deleted. Under-matching is the safe
   direction; widen with an affirmative country list, never by inverting
   the US check.)*
4. **README (ask #2):** the *Status*, *Last Verified* and *Location*
   columns are dropped from the job tables, and only `status: open` rows
   are rendered. All three fields remain in `data/*.yaml`; nothing changes
   in the data model.
5. **Architecture:** corrections-report + dedicated applier (option B
   below), not an extension of `merge_category` and not a one-off repair
   script.

## Architecture

Mirrors the `link_check.py` / `check_links.py` split: pure tested core,
untested network driver, and a single serialized writer per run. `merge.py`,
the fetch-report contract, and `run_scrape_merge.py` are untouched.

### 1. `scripts/ats_verify.py` — pure core (tested, no I/O)

- `api_url(link) -> (ats, url) | None`. Recognizes the six families from
  the link and derives the authoritative URL. Workday reuses
  `link_check.workday_cxs_url`. Greenhouse derives the boards-api per-job
  URL; Lever the `api.lever.co/v0/postings/<company>/<id>` URL;
  SmartRecruiters its public postings-API URL. Ashby and iCIMS have no
  separate endpoint — their structured data is embedded in the posting page
  (Ashby embedded JSON, iCIMS JSON-LD), so `api_url` returns the page URL
  tagged with the family. Unrecognized links return `None` (out of scope).
- `extract(ats, status, body) -> {locations, date_posted, closed} | None`.
  Per-family payload parser; `date_posted` comes out normalized to
  `YYYY-MM-DD` or `None`. Returns `None` when the body doesn't parse as
  expected — format drift is treated as unknown, never guessed. Which
  payload field counts as the posting date is pinned per family in the
  implementation plan.
- `decide(row, extract) -> list[Action]`. Pure decision function; rules
  below.

### 2. `scripts/check_ats.py` — network driver (untested)

Loads open rows from `data/*.yaml`, keeps those with an `api_url`, probes
via the existing `check_links._probe(want_body=True)` (no second HTTP
client; modest thread pool), runs `extract` + `decide`, and writes one
corrections JSON to `scratch/ats_corrections.json`. It never writes
`data/*.yaml` or `README.md`. Per-row failures are isolated; per-family
`unknown` counts are printed so API drift is visible.

### 3. `scripts/apply_ats_corrections.py` — serialized writer (apply logic tested)

Reads the corrections file and applies it to `data/*.yaml` in one pass:

- applies `set_date` / `close` / `delete_non_us`;
- stamps `last_verified = today` on every row whose probe resolved
  (confirmed, corrected, or closed) — never on `unknown`;
- clears any `possible_duplicate_of` that pointed at a deleted row id;
- validates every modified row against `ROW_SCHEMA` **before** writing; any
  failure aborts the whole apply with nothing written (corrections are
  deterministic, so a schema failure is a bug, not bad input);
- warns and skips a correction whose row id no longer exists;
- rewrites the YAML and re-renders `README.md`;
- prints a summary with each close and each deletion listed individually.

### Corrections-file contract

At least one entry per probed row:

```json
{
  "generated": "2026-08-08",
  "actions": [
    {"id": "examplecorp-swe-intern-a1b2c3", "category": "swe", "ats": "workday",
     "action": "set_date", "old": "2026-08-08", "new": "2026-07-15"},
    {"id": "samplesoft-swe-intern-d4e5f6", "category": "swe", "ats": "greenhouse",
     "action": "confirm"},
    {"id": "acme-quant-intern-778899", "category": "quant", "ats": "lever",
     "action": "unknown"}
  ]
}
```

A row with several changes (location and date) gets several action entries
sharing its `id`. `confirm` and `unknown` carry no old/new.

## Decision rules

- API URL answers 404/410, or the payload marks the posting closed →
  `close`.
- The payload's country field is the only location-derived signal read: if
  it affirmatively names a non-US country -> `delete_non_us`. Otherwise the
  row's `location` is left exactly as-is (decision 2).
- Payload has a posting date differing from the row's → `set_date`, and
  `date_estimated` is cleared (an authoritative date beats both an estimate
  and a wrong "real" tracker date).
- Ambiguous probe (403/429/5xx/network error) or unparseable body →
  `unknown`: no changes, no `last_verified` bump. Unknown is never treated
  as closed — no disappearance-based closing, as ever.

## README change

`generate_readme._table` / `_row_cells`: drop the *Last Verified* and
*Status* columns; render only `status: open` rows. The `⚠️dup?(id)` marker
moves from the (removed) Status cell to the end of the *Role* cell. A
category whose rows are all closed renders "_No open roles._". The legend
drops its Status and Last-Verified sentences; the `~date` and dup-marker
notes stay. Same change shape as the Track-column removal in c82a18c.

## Testing

- `tests/test_ats_verify.py`: `api_url` accept/reject cases per family;
  `extract` against one small captured fixture payload per family; `decide`
  cases for every action, the member-confirm and primary-replacement
  location rules, and the date/estimated interplay.
- `tests/test_apply_ats_corrections.py`: each action type against temp YAML
  dirs; `last_verified` stamping; dup-pointer clearing on delete;
  schema-failure abort leaves files unwritten; unknown-id skip.
- `tests/test_generate_readme.py`: columns dropped, closed rows filtered,
  dup marker in the Role cell, all-closed category message.

## Run-book (explicit request only — never scheduled)

1. `python3 scripts/check_ats.py` → writes `scratch/ats_corrections.json`,
   prints the proposed summary (nothing else written — reviewable dry run).
2. Review, then
   `python3 scripts/apply_ats_corrections.py scratch/ats_corrections.json`.
3. `python3 scripts/check_integrity.py` → commit.

Never run concurrently with `run_scrape_merge.py` — same
single-writer-per-file discipline.

## Boundaries

- No schedule, no auto-run; invocation is an explicit request, like
  scraping.
- No disappearance-based closing; `unknown` changes nothing.
- Custom-site rows are untouched; `check_links.py` remains the liveness
  check for them.
- `merge.py` / fetch-report semantics unchanged; this pass is the only
  consumer of the corrections contract and `apply_ats_corrections.py` its
  only writer.
