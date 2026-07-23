# Summer 2027 Internship Tracker — Design Spec

**Date:** 2026-07-22
**Status:** Approved, pending implementation plan

## Purpose

A comprehensive, US-only listing of Summer 2027 internships across eight role categories, modeled on repos like [SimplifyJobs/Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships) but broader in scope (more categories, more sources). This is a market listing of what's out there — it does not track Tony's own application status. That remains the separate job of `../summer2027_internship_tracker.xlsx`; there is zero overlap between the two systems.

## Non-goals

- Not a personal application-status tracker (no "Applied" column, no CRM-style fields).
- Not guaranteed exhaustive on day one. "Comprehensive" is a direction to grow into per category, not a day-one claim — see "Realistic Coverage Expectations" below.
- Not scraping anything automatically on a schedule. Scraping only happens when explicitly requested ("scrape" or "scrape \<category>").

## Repo layout

```
internship-tracker/                 (own git repo, private GitHub remote: tonyyu2170/internship-tracker)
├── README.md                       # Generated — TOC + 8 category tables + legend + "last updated" stamp
├── data/
│   ├── swe.yaml
│   ├── quant.yaml                  # includes `track` field: Trading | Research | Development
│   ├── data_science.yaml
│   ├── ai_ml.yaml
│   ├── hardware.yaml
│   ├── actuarial.yaml
│   ├── consulting.yaml
│   └── ib.yaml
├── sources/
│   └── companies.yaml              # Per-category watch-list: target companies + career-page URL + ATS type
├── scripts/
│   └── generate_readme.py          # Renders README.md from data/*.yaml
└── docs/superpowers/specs/         # This spec and future ones
```

One data file per category (not one giant file) — cleaner git diffs, no write contention across categories during a multi-source scrape.

## Data schema

Each entry in a category's yaml file (illustrative example, not real data):

```yaml
- id: jane-street-quant-trading-nyc     # slug: company + role + sorted location set
  company: Jane Street
  role: Quantitative Trading Intern
  track: Trading                        # quant.yaml only; omitted elsewhere
  location: New York, NY / Chicago, IL  # one row per posting, multi-location comma/slash-listed
  link: https://...
  date_posted: 2026-07-15
  term: Summer 2027                     # Summer 2027 | Fall 2026 | Off-Cycle | etc.
  degree: [BS, MS]                      # subset of [BS, MS, PhD]
  status: open                          # open | closed — closed rows are kept, never deleted
  source: company_career_page           # audit trail: where first discovered
  date_added: 2026-07-22
  last_verified: 2026-07-22
```

### Dedup key

Normalized `(company, role title, location-set)` triple. Location comparison is **set-equality on the split location list**, not raw string match — `"New York, NY / Chicago, IL"` and `"Chicago, IL / New York, NY"` are the same posting. On a re-find, update `last_verified` (and `status` if it changed) in place; never create a duplicate row.

## Categories & tables

Eight tables, each its own `##` anchor, linked from a table of contents at the top of `README.md`:

1. Software Engineering
2. Quantitative Finance (single table; `Track` column distinguishes Trading / Research / Development)
3. Data Science
4. AI/ML
5. Hardware Engineering
6. Actuarial
7. Consulting
8. Investment Banking

Columns: `Company | Role | Track* | Location | Link | Date Posted | Term | Degree | Status` (*Quantitative Finance table only). Rows sorted by `date_posted` descending (newest first). Closed roles render with a 🔒 marker and stay in the table rather than being deleted, preserving history of what existed.

`generate_readme.py` also renders a short legend (what 🔒 / Term values / degree abbreviations mean) and a "Last updated: \<date>" stamp at the top of the generated `README.md`.

## Scraping — source-to-tool mapping

| Source | Tool | Notes |
|---|---|---|
| The 4 linked GitHub repos (northwesternfintech/2027QuantInternships, sndsh404/summer-2027-internships, speedyapply/2027-SWE-College-Jobs, Chieler/Summer-2027-SWE-Internships) + any others found | Direct fetch of raw markdown | No Firecrawl needed — plain public markdown |
| Company career pages on Greenhouse or Lever | Their public no-auth JSON API directly (`boards-api.greenhouse.io/v1/boards/{token}/jobs`, `api.lever.co/v0/postings/{company}`) | Fast, free, zero Firecrawl credits |
| Company career pages elsewhere (Workday, custom sites) | Firecrawl `scrape`/`crawl` | Handles JS rendering natively |
| simplify.jobs (the website) | Firecrawl `scrape`/`crawl` | Public/browsable without login, JS-rendered |
| Indeed | Firecrawl first (`proxy: auto`) | Falls back to the logged-in browser tool only if Firecrawl gets blocked |
| LinkedIn, Handshake | claude-in-chrome browser tool against Tony's already-logged-in session | Never routed through Firecrawl; no credentials handled by the assistant |

Firecrawl runs as an MCP server (`firecrawl-mcp`), authenticated via `FIRECRAWL_API_KEY` (Tony is signing up for a key; server config falls back to the keyless free tier — scrape/search only, rate-limited — until the key is supplied).

`sources/companies.yaml` seeds with a reasonable default company list per category (ATS type noted where known) and grows over time as scrapes surface new companies worth watching directly.

## Realistic coverage expectations

The 4 linked repos and most other findable Summer 2027 trackers are SWE/quant-heavy. They give strong bootstrap coverage for Software Engineering and Quantitative Finance, but essentially nothing for Data Science, AI/ML, Hardware Engineering, Actuarial, Consulting, or Investment Banking — those six categories have no comparable aggregator and depend on company-by-company scraping and job-board search from day one. Expect the first scrape to leave SWE/quant well-populated and the other six thin; they fill in incrementally as `companies.yaml` grows across subsequent scrape sessions, not all at once.

## Per-scrape mechanics

Trigger: user says "scrape" (all sources) or "scrape \<category>" (targeted to one category's sources).

1. Fetch/scrape each in-scope source per the tool mapping above (dispatched as parallel subagents per source where useful).
2. Parse results into the schema.
3. Dedupe against the relevant `data/<category>.yaml` using the dedup key.
4. Merge: add genuinely new rows; for existing rows, refresh `last_verified` and update `status` — a role is marked `closed` when it disappears from **the source that listed it** (re-checked at scrape time), not by pinging the link's raw HTTP status (many ATS platforms return 200 even for closed postings).
5. Regenerate `README.md` via `generate_readme.py`.
6. Commit locally: `git commit -m "scrape: update roles as of <date>"`.

**Push policy:** commits happen automatically each scrape; pushing to the GitHub remote does not. Push is a separate, explicit action Tony requests when he wants it, since it touches shared/remote state.

## GitHub remote

Private repo at `tonyyu2170/internship-tracker`. Created during initial setup; not pushed to automatically per scrape (see push policy above).

## Initial population plan

1. Scaffold the empty framework: schema, `generate_readme.py`, empty `data/*.yaml` files, seeded `sources/companies.yaml`.
2. Run one seed scrape against the 4 linked repos, plus a search for any other public Summer 2027 tracker repos.
3. Generate the first `README.md` from whatever that seed scrape yields — expected to be SWE/quant-heavy per the coverage expectations above.

## Open items for the implementation plan

- Exact starting company list per category in `sources/companies.yaml` (to be drafted during implementation, editable afterward).
- Firecrawl API key handoff mechanics (env var vs. MCP config file) — deferred to implementation since it depends on how Tony delivers the key.
