# Scraping Runbook

The tested core (dedupe/merge/README) never touches the network. Scraping is a
per-source procedure that emits **fetch reports** — JSON files the tested
`run_scrape_merge.py` consumes. This file documents both.

## Trigger

- "scrape" -> all sources, all categories.
- "scrape <category>" -> only that category's sources.
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
| The 4 GitHub tracker repos + any others found | Raw markdown fetch | Fetch the raw README, parse the table rows; carry each row's own application href as `link`; if a row is marked closed in-line, set `closed_marker: true` |
| Company on Greenhouse | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | JSON per job; `link` = `absolute_url`; parse degree from the description text; `date_posted` from the best available field, else omit |
| Company on Lever | `api.lever.co/v0/postings/<company>?mode=json` | JSON per posting; `link` = `hostedUrl`; parse degree from `description`/`lists` |
| Company on Workday / custom site | Firecrawl `scrape`/`crawl` (primary), Playwright (local fallback) | Extract postings from rendered content |
| simplify.jobs | Firecrawl `scrape`/`crawl` | Public, JS-rendered |
| Indeed | Firecrawl first (`proxy: auto`); logged-in browser tool if blocked | — |
| LinkedIn, Handshake | claude-in-chrome against Tony's logged-in session | Keep reads light and infrequent — automated activity can flag the account |

`sources/companies.yaml` is the watch-list of which company uses which ATS.
Add newly-discovered companies there (serialized through the parent, like the
data files).

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
