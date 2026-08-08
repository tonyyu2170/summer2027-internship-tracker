# Scraping Runbook

The tested core (dedupe/merge/README) never touches the network. Scraping is a
per-source procedure that emits **fetch reports** — JSON files the tested
`run_scrape_merge.py` consumes. This file documents both.

## Trigger

- "scrape" -> the GitHub tracker repos only, all six supported categories.
  default source: cheap (raw markdown fetch, no per-company fan-out) and
  fast.
- "scrape <category>" -> only that category's rows from the GitHub tracker
  repos.
- Any other source (`sources/companies.yaml`-driven Greenhouse/Lever/Workday
  direct scraping, simplify.jobs, Indeed, LinkedIn/Handshake) only runs when
  Tony names it explicitly (e.g. "scrape companies.yaml" or "scrape
  simplify.jobs"). Full fan-out across every source got too slow and
  token-heavy, so as of 2026-07-24 those are opt-in, not part of a plain
  "scrape".
Scraping is never scheduled; it runs only on an explicit request.

Consulting and investment banking are intentionally out of scope. Matching
roles are counted as `category_drop` and never create a category file.

For the enabled actuarial company source, run
`python3 scripts/fetch_companies.py actuarial` before the serialized merge.
It emits the same fetch-report JSON contract as tracker sources and writes no
data files itself. Do not run it until the planned direct-link migration for
the historical Oliver Wyman row has been reviewed.

## Fetch-report contract

Write one JSON file per source entity into `scratch/fetch_reports/` (git-ignored):

```json
{
  "category": "quant",
  "source_entity": "greenhouse:citadelsecurities",
  "postings": [
    {
      "company": "Citadel Securities",
      "role": "Quantitative Trading Intern",
      "track": "Trading",
      "location": "New York, NY",
      "link": "https://boards.greenhouse.io/citadelsecurities/jobs/123",
      "date_posted": "2026-08-01",
      "term": "Summer 2027",
      "degree": ["BS", "MS"],
      "source": "greenhouse",
      "closed_marker": false
    }
  ]
}
```

Required per posting: `company, role, location, link, term, degree`.
Optional: `track` (quant only), `date_posted` (omit/null if the source has
none — merge fills today's date), `source` (defaults to `source_entity`),
`closed_marker` (true only when the source itself marks the role closed).
Locations that are not confidently US are dropped by the merge engine and
reported in the per-source drop tally — prefer emitting `City, ST`.

## Source -> tool

| Source | Tool | How |
|---|---|---|
| **The GitHub tracker repos in `sources/github_trackers.yaml` (default source, see Trigger)** | `python3 scripts/fetch_trackers.py` | Deterministic — no LLM. Skips any tracker whose commit SHA is unchanged since `sources/scrape_state.yaml`, then parses the file named by that tracker's `path` (five publish structured JSON/YAML exports; four are parsed from their README table). Writes fetch reports plus `scratch/fetch_reports/unclassified.json`. |
| *(opt-in only)* Company on Greenhouse | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | JSON per job; `link` = `absolute_url`; parse degree from the description text; `date_posted` from the best available field, else omit |
| *(opt-in only)* Company on Lever | `api.lever.co/v0/postings/<company>?mode=json` | JSON per posting; `link` = `hostedUrl`; parse degree from `description`/`lists` |
| *(opt-in only)* Company on Workday / custom site | Firecrawl `scrape`/`crawl` (primary), Playwright (local fallback) | Extract postings from rendered content |
| *(opt-in only)* simplify.jobs | Firecrawl `scrape`/`crawl` | Public, JS-rendered |
| *(opt-in only)* Indeed | Firecrawl first (`proxy: auto`); logged-in browser tool if blocked | — |
| *(opt-in only)* LinkedIn, Handshake | claude-in-chrome against Tony's logged-in session | Keep reads light and infrequent — automated activity can flag the account |

`sources/companies.yaml` is the watch-list of which company uses which ATS,
used only by the opt-in Greenhouse/Lever/Workday-direct rows above. As of
2026-07-24 it's dormant by default (kept for reference/future re-enable, not
deleted) since plain "scrape" no longer drives per-company ATS fan-out — see
Trigger. Only add to it or scrape from it when Tony explicitly asks for
direct-company scraping.

## Link liveness check

Added 2026-07-24 after Tony hit several dead links (Workday postings that
had closed, a Greenhouse job removed) that still showed `status: open`.
Nothing re-checks a link after it's first scraped, so closed postings
accumulate silently.

`python3 scripts/check_links.py` probes every `status: open` row's link
across `data/*.yaml` and writes `scratch/fetch_reports/link_check_<category>.json`
for any row it's confident is dead (`closed_marker: true`, `source:
"link_checker"`). It does **not** write `data/*.yaml` itself — run
`scripts/run_scrape_merge.py scratch/fetch_reports` afterward (same as any
other scrape) to actually close them.

Classification logic (`scripts/link_check.py`, unit-tested in
`tests/test_link_check.py`) only calls a link "dead" on an unambiguous
signal, never a plain non-200:
- Workday (`*.myworkdayjobs.com`): the HTML page is a client-rendered SPA
  that always returns 200, even for a gone posting — the check instead hits
  the underlying `wday/cxs/.../job/...` JSON API, which returns a real 404.
- Greenhouse: a dead job id redirects client-side from `/<token>/jobs/<id>`
  to the board root `/<token>?error=true` — that redirect is the signal.
- Everything else: only a real `404`/`410` counts as dead. Ambiguous codes
  (403, 406, 429, 5xx, timeouts) classify as "unknown" and are left alone —
  those are usually bot-blocking, not a closed posting, and this checker is
  not meant to be a source of false closures.

This is a separate concern from the "no disappearance-based auto-closing"
rule elsewhere in this doc: that rule is about *not* inferring closure just
because a role stopped appearing in a fresh scrape of a source listing. This
checker instead actively re-verifies a link *this repo already stores* —
closer in spirit to the existing `closed_marker` path than to guessing from
absence.

## Tracker parsing

As of 2026-07-25 the nine GitHub trackers are parsed deterministically by
`scripts/fetch_trackers.py` rather than by an LLM subagent per repo. See
`docs/superpowers/specs/2026-07-24-cheap-tracker-scraping-design.md` for the
rationale and the per-tracker configuration.

Three tiers, cheapest first:

1. **Skip.** `GET /repos/<repo>/commits?path=<path>` returns the newest SHA
   touching the parsed file. Equal to the SHA in `sources/scrape_state.yaml`
   means nothing changed — the tracker is skipped entirely.
2. **Parse.** `scripts/parse_tracker.py` handles four format families
   (cvrve JSON, zshah101 JSON, northwesternfintech YAML, Markdown pipe
   table). Costs no tokens.
3. **Classify.** Only links absent from `data/*.yaml` that no keyword rule
   matched land in `scratch/fetch_reports/unclassified.json`.
   `run_scrape_merge.py` refuses to merge while any entry there has a blank
   `category` — but note the caveat below.

**A link already in `data/*.yaml` keeps its current category, always.**
`merge_category` dedupes within one category file only, so a link that
changed category would exist in two files with nothing to catch it.

**A `closed_marker: true` posting whose link was never tracked before is
dropped, not imported.** Several sources (simplifyjobs in particular) export
years of inactive listings alongside active ones. Importing those would add
already-dead rows to `data/*.yaml`/`README.md` for postings this repo never
had a chance to show as open. `merge_category`'s own "no disappearance-based
auto-closing" rule only governs *existing* rows — this is a separate guard,
in the shim, for postings the merge engine has never seen.

**`scratch/fetch_reports/unclassified.json` has no automatic merge path
today.** Hand-filling each entry's `category` satisfies
`run_scrape_merge.py`'s guard, but there is no code that turns a classified
`unclassified.json` entry back into a fetch report — the file is only ever
read as a blocking check, then skipped. Its count is recorded in
`drop_counts.json` and then in `sources/scrape_state.yaml`; do not delete it
to hide unresolved rows. Building a real merge-back path is a follow-up, not
yet implemented.

**Two mutation caveats for `sources/scrape_state.yaml`:**
- Any real run of `fetch_trackers.py` — including a "dry run" pointed at a
  scratch output directory — advances and rewrites this file unconditionally
  (it's not gated by `out_dir`). If you're testing rather than doing a real
  scrape, `git checkout sources/scrape_state.yaml` afterward to discard it.
- `yaml.safe_dump` does not preserve comments. The header comment explaining
  this file's purpose will be silently dropped on its next real write; that
  is expected, not a bug to chase.

### Fallback

If a tracker's preferred source 404s, fails to parse, or yields fewer than
half its recorded `row_count`, `fetch_trackers.py` warns, writes no report,
and leaves its SHA unadvanced so the next run retries. To recover that
tracker in the meantime, dispatch an LLM subagent to read its README and
emit a fetch report by hand, per the *Fetch-report contract* above — that
path is retained precisely for this case.

Term filtering is load-bearing for `simplifyjobs`, `suryaharikrishnan` and
`zshah101`, whose exports carry several hiring cycles in one file.
`northwesternfintech` never emits `closed_marker`: it publishes no status
field, so a vanished role is disappearance, which this repo does not
auto-close on.

## Run procedure

1. Run `python3 scripts/fetch_trackers.py` for the GitHub trackers. It
   writes fetch reports itself. For any other (opt-in) source, dispatch
   scraping per source as before — those subagents **return parsed postings
   only** and never write data files.
2. The parent writes one fetch-report JSON per non-tracker source entity
   into `scratch/fetch_reports/`, and fills in any `category` left blank in
   `scratch/fetch_reports/unclassified.json`.
3. Run the single serialized writer:
   `python3 scripts/run_scrape_merge.py scratch/fetch_reports`
   It merges per category, rewrites `data/*.yaml`, regenerates `README.md`,
   and prints new/closed/possible-duplicate counts plus per-source drop tallies.
   - Also review any "warn: skipped ..." or "warn: dropped invalid row ..."
     lines — these mean a scraped posting or merged row failed validation
     and was not persisted; check the source data if a source is
     consistently producing these.
4. Review any "possible duplicate" lines; resolve by hand (delete the
   duplicate row, or clear its `possible_duplicate_of`).
5. Clear `scratch/fetch_reports/` and commit with a **targeted** `git add`
   (`data/*.yaml`, `sources/scrape_state.yaml` — not `-A`), so an unrelated
   in-progress edit elsewhere in the working tree doesn't get swept into a
   scrape commit: `git add data/ sources/scrape_state.yaml && git commit -m
   "scrape: update roles as of <date>"`. Run `git status --short` first to
   confirm nothing unexpected is staged.
6. **Pushing is Tony's action alone.** The assistant never runs `git push`.

## ATS verification pass (authoritative location/date)

Manual, explicit-request-only re-verification of open rows whose links sit
on an API-covered ATS (Workday CXS, Greenhouse boards-api, Lever v0
postings, Ashby posting-api, SmartRecruiters postings API, iCIMS JSON-LD).
Design: `docs/superpowers/specs/2026-08-08-ats-verification-design.md`.

1. `python3 scripts/check_ats.py [category ...]` — probes the APIs and
   writes `scratch/ats_corrections.json` (the audit record of every
   proposed change). Writes nothing else.
2. Review the printed summary — every proposed close and non-US delete is
   listed individually.
3. `python3 scripts/apply_ats_corrections.py scratch/ats_corrections.json`
   — the single serialized writer: applies corrections, stamps
   `last_verified`, rewrites `data/*.yaml`, re-renders `README.md`. Aborts
   without writing if any corrected row fails ROW_SCHEMA.
4. `python3 scripts/check_integrity.py`, then commit.

Never run concurrently with `run_scrape_merge.py` (single-writer
discipline). `unknown` results change nothing — no disappearance-based
closing.
