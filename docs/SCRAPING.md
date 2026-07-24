# Scraping Runbook

The tested core (dedupe/merge/README) never touches the network. Scraping is a
per-source procedure that emits **fetch reports** — JSON files the tested
`run_scrape_merge.py` consumes. This file documents both.

## Trigger

- "scrape" -> the GitHub tracker repos only, all categories. This is the
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
Locations that are not confidently US are dropped by the merge engine — no
need to pre-filter, but prefer emitting `City, ST`.

## Source -> tool

| Source | Tool | How |
|---|---|---|
| **The GitHub tracker repos in `sources/github_trackers.yaml` (default source, see Trigger)** | Raw markdown fetch | Fetch the raw README, parse the table rows; carry each row's own application href as `link`; if a row is marked closed in-line, set `closed_marker: true`. If the tracker has its own date/age column (e.g. "Date Posted", "Age"), convert it to an absolute `date_posted` (`YYYY-MM-DD`) relative to the scrape date — don't leave it blank when the tracker has one. |
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

## Run procedure

1. Dispatch scraping per source (parallel subagents where useful). Each
   subagent **returns parsed postings only** — it does not write data files.
2. The parent writes one fetch-report JSON per source entity into
   `scratch/fetch_reports/`.
3. Run the single serialized writer:
   `python3 scripts/run_scrape_merge.py scratch/fetch_reports`
   It merges per category, rewrites `data/*.yaml`, regenerates `README.md`,
   and prints new/closed/possible-duplicate counts.
   - Also review any "warn: skipped ..." or "warn: dropped invalid row ..."
     lines — these mean a scraped posting or merged row failed validation
     and was not persisted; check the source data if a source is
     consistently producing these.
4. Review any "possible duplicate" lines; resolve by hand (delete the
   duplicate row, or clear its `possible_duplicate_of`).
5. Clear `scratch/fetch_reports/` and commit:
   `git add -A && git commit -m "scrape: update roles as of <date>"`
6. **Pushing is Tony's action alone.** The assistant never runs `git push`.
