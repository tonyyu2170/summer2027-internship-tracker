# Cheap GitHub-tracker scraping

**Status:** design, approved 2026-07-24
**Problem owner:** Tony

## Problem

A single plain `scrape` drains a session's token budget. The cause is not
network volume — it is that scraping dispatches one LLM subagent per tracker
repo, and each subagent *reads the entire rendered README table* to emit
structured postings. Across the 9 repos in `sources/github_trackers.yaml`
that is roughly 6,000 lines of Markdown/HTML through an LLM on every run,
almost all of it unchanged since the previous run.

The fix is to move parsing out of the LLM entirely, and to skip repos that
have not changed at all.

## Cost model

Three tiers, cheapest first. Each row that reaches a more expensive tier
should have earned it.

| Tier | Mechanism | Cost |
|---|---|---|
| 0 | Repo unchanged since last scrape → skip entirely | one API call |
| 1 | Deterministic parse (JSON/YAML export, or regex over a pipe table) | ~0 tokens |
| 2 | LLM category classification, new links only | a few rows/run |

Today every row lands in a tier far above 2. After this change the common
case is tier 0 or 1, and tier 2 sees only genuinely-new postings from the
four trackers that publish no category field.

## What was considered and cut

**Diff-based parsing (parse only the lines a commit changed).** Cut. It was
attractive while parsing was the expensive step, but once parsing is
deterministic Python a full-file parse costs microseconds — diffing would
only save network bytes and CPU, both already free. Measured against the
real repos it also does not pay: SimplifyJobs' README changed 369 lines
across ~31h of commits (its `Age` column holds rolling `1d`/`4d` values that
a bot rewrites), so the "diff looks like a rewrite" fallback would fire
constantly. The added machinery — SHA-anchored `compare` calls, patch
parsing, hunk-to-row mapping, a rewrite-detection heuristic — buys nothing
that tier 0 and tier 1 do not already deliver.

**Dropping redundant trackers.** Cut. `chieler` re-publishes
Simplify/vanshb03/sndsh404/zshah101, and `suryaharikrishnan` re-publishes
Simplify's dataset outright (`"source":"Simplify"`,
`_sources:["simplify-2026"]`). Dropping them would have been a real saving
under the old cost model. Under the new one they are nearly free: their
parse is deterministic, and their rows are overwhelmingly links already in
`data/*.yaml`, so the link-hash gate stops them before any LLM call. Keep
all 9 — redundancy costs approximately nothing and adds resilience when one
upstream tracker stalls.

## Mechanisms

### 1. Skip unchanged repos (tier 0)

`sources/scrape_state.yaml` (committed) records, per tracker handle, the
commit SHA of the file that was last parsed:

```yaml
simplifyjobs:
  path: .github/scripts/listings.json
  sha: 74d97e97721b05966a1b3b748c40f601e9f9ad90
  scraped_at: 2026-07-24
  row_count: 107
```

Before fetching, `GET /repos/{repo}/commits?path={path}&per_page=1`. If the
returned SHA equals the stored SHA, skip the tracker completely — no fetch,
no parse, no fetch report. SHA is used rather than a timestamp because it is
exact and immune to clock skew.

State is committed rather than gitignored so it survives across machines; a
missing entry simply means "never scraped", which falls through to a normal
full parse.

`row_count` is stored for the sanity check in *Failure modes* below.

### 2. Prefer structured exports over rendered tables

Five of nine trackers publish machine-readable data. Parsing those is both
cheaper and materially more reliable than scraping the rendered README,
which carries `↳` same-company carry-forward markers, `<details>` blocks
wrapping 30-location postings, and a mix of HTML `<table>` and Markdown pipe
syntax.

### 3. Deterministic parsing (tier 1)

`scripts/parse_tracker.py` — pure, network-free, unit-tested against saved
fixtures of each tracker's real output. One adapter function per format
family; each takes source text and returns fetch-report postings. This sits
on the tested-core side of the boundary `docs/SCRAPING.md` already draws.

`scripts/fetch_trackers.py` — the thin, untested network shim: reads
`scrape_state.yaml`, does the SHA check, fetches, calls the parser, writes
fetch reports to `scratch/fetch_reports/`. No parsing logic lives here.

### 4. Link-hash gate and category stability (tier 2 entry)

Before any classification, load every `link` already present across
`data/*.yaml` into a normalized-link set (`normalize_link`, the existing
function). Then:

- **A link already in `data/*.yaml` is never classified and never
  reclassified.** It keeps whatever category file it currently lives in.
- Only links absent from that set reach the classifier.

The second rule is not an optimization, it is a correctness requirement.
`merge_category` dedupes *within* one category file only. If a link were
classified `hardware` this run and `quant` last run, the same posting would
exist in two files and nothing would catch it. Category is therefore
assigned once, at first sight, and is stable thereafter.

## Per-tracker configuration

| # | handle | Source to parse | Fmt | Category | Term filter | Closure |
|---|---|---|---|---|---|---|
| 1 | `simplifyjobs` | `.github/scripts/listings.json` | JSON | `category` field | **required:** `terms[]` ∋ `Summer 2027` | `active:false` |
| 2 | `suryaharikrishnan` | `data/listings.json` | JSON | `category` field | **required:** `terms[]` ∋ `Summer 2027` | `active:false` |
| 3 | `zshah101` | `data/jobs.json` | JSON | `category` field | **required:** `season == "Summer 2027"` | `is_open:false` |
| 4 | `vanshb03` | `.github/scripts/listings.json` | JSON | classifier | `season == "Summer"` (repo is 2027-scoped) | `active:false` |
| 5 | `northwesternfintech` | `data/*.yaml` | YAML | `role_type` map | repo scope | none — see below |
| 6 | `speedyapply` | README table | MD pipe | `### Quant` section, else classifier | repo scope | 🔒 |
| 7 | `sndsh404` | README table (scope to `## the list`) | MD pipe | classifier | repo scope | 🔒 |
| 8 | `zapplyjobs` | README table | MD pipe | classifier | repo scope | 🔒 |
| 9 | `chieler` | README table | MD pipe | classifier | repo scope | 🔒 |

### Term filtering is load-bearing

Trackers 1–3 carry multiple hiring cycles in one file and **must** be
filtered, or hundreds of wrong-cycle rows enter the dataset. Concretely,
`simplifyjobs`' export holds 9,986 `Summer 2026` entries against 327
`Summer 2027`. The current dataset is clean (all 697 rows are `Summer 2027`)
and must stay that way.

Trackers 4–9 are single-cycle repos where the term is implied by repo scope;
emit `term: "Summer 2027"` directly.

### SimplifyJobs: the README is the wrong cycle

`SimplifyJobs/Summer2026-Internships`' rendered README lists **Summer 2026**
roles. This is why the tracker contributes **0 rows** today despite being in
the source list — the existing scrape reads the README and correctly finds
nothing for 2027. Its `listings.json`, however, holds **107 active
Summer 2027 postings** (53 Quant, 25 Software, 20 AI/ML/Data, 4 Hardware).

Switching this tracker to the JSON export therefore reduces cost *and*
recovers ~107 roles currently missed entirely. Those land through the normal
merge path on the first run after this change.

### Category assignment

Three sources of truth, in precedence order:

1. **Explicit field** (trackers 1–3). Map upstream category strings onto the
   eight local categories: `Software`/`Software Engineering` → `swe`,
   `Quant`/`Quantitative Finance` → `quant`, `AI/ML/Data` → split by role
   text into `ai_ml` or `data_science`, `Hardware` → `hardware`,
   `Product` → dropped (no local category).
2. **Static map** (tracker 5). `role_type` codes: `QR`/`QD` → `quant`,
   `SWE` → `swe`, `HW` → `hardware`. Note that `HW` routes to `hardware`
   even though this is a quant-only repo — this is the established
   convention, and getting it wrong is exactly the bug fixed by hand in
   `0fdf5dd` (Akuna Capital Hardware Engineer miscategorized as quant).
3. **Classifier** (trackers 4, 6–9, and category-less rows elsewhere).
   Keyword rules first — a role title containing `Quantitative`/`Quant
   Trader`/`Quant Researcher` → `quant`, `Hardware`/`FPGA`/`ASIC`/
   `Firmware`/`Silicon` → `hardware`, `Machine Learning`/`ML`/`AI` →
   `ai_ml`, `Data Scien`/`Data Analyst` → `data_science`, `Actuarial` →
   `actuarial`, `Investment Banking` → `ib`, `Consultant`/`Consulting` →
   `consulting`, and `Software`/`SWE`/`Engineer` → `swe` as the last rule.
   Anything still unmatched goes to a single small LLM call, batched across
   all unclassified new rows in the run.

The hardware-at-quant-firm rule must be checked *before* the quant rule so
"Hardware Engineer Intern" at Jane Street/Akuna/IMC routes to `hardware`.

### Closure

`northwesternfintech` publishes no status. Its README renders `✅` for every
link unconditionally (verified: 66 `✅`, **0** `❌`) and its documented schema
has no status field — when a role closes, the entry is simply deleted. That
is disappearance, which this repo explicitly refuses to auto-close on. So:
**never emit `closed_marker` from tracker 5.** Dead links from that source
are caught by the existing `scripts/check_links.py` path instead.

All other trackers emit `closed_marker: true` only on their own explicit
signal, per the existing rule.

### Field defaults

`degree` is required by `ROW_SCHEMA` and constrained to `["BS","MS","PhD"]`.
Most trackers do not publish it. Default to `["BS"]`; where a source does
publish it (`simplifyjobs`/`suryaharikrishnan` `degrees[]`), map
`Bachelor's`→`BS`, `Master's`→`MS`, `PhD`→`PhD`, falling back to `["BS"]`
when the array is empty.

`location`: where a source gives multiple locations, emit the first, matching
the existing convention in `data/*.yaml` (Google's 30-location posting is
stored as `Mountain View, CA`). Non-US locations are dropped downstream by
`canonicalize_location`; no pre-filtering needed.

`date_posted`: use the source's own value where present. Absent, omit it and
let merge fill the scrape date — unchanged behavior.

## Failure modes

Every failure warns loudly and falls back; nothing is dropped silently.

- **Preferred source 404s or fails to parse** → fall back to the existing
  LLM-subagent full-README parse for that tracker only, and warn. Cost for
  that one tracker returns to today's level; correctness is preserved.
- **Parser yields 0 rows, or fewer than half the `row_count` recorded in
  `scrape_state.yaml`** → treat as a probable upstream format change. Warn,
  do not write the fetch report, and do not advance the stored SHA, so the
  next run retries rather than treating the empty result as truth. Silent
  zero-extraction is the failure mode that quietly loses data.
- **Upstream changes schema** → caught by the row-count check above and by
  fixture tests drifting from live output.

Advancing the stored SHA happens only after a tracker parses successfully
and its fetch report is written.

## Testing

`tests/test_parse_tracker.py` — fixtures captured from each of the 9
trackers' real output, one per format family, asserting parsed postings
match expected structure. Fixtures are the only practical way to hold 9
differing formats stable.

Specific cases worth pinning: multi-cycle filtering (a `Summer 2026` row is
excluded), `HW` → `hardware` routing, `↳` carry-forward resolution in pipe
tables, `<details>` multi-location collapse, and the classifier's
hardware-before-quant ordering.

The network shim is not unit-tested, consistent with the existing boundary.

## Out of scope

- Per-tracker scrape cadence / frequency tuning — deliberately excluded.
- Disappearance-based auto-closing — still forbidden.
- The opt-in non-GitHub sources (`companies.yaml`, simplify.jobs, Indeed,
  LinkedIn). Unchanged.
- `git push` — remains Tony's action alone.
