# Summer 2027 Internship Tracker — Design Spec

**Date:** 2026-07-22
**Status:** Revised after multi-agent review, pending implementation plan

## Purpose

A comprehensive, US-only listing of Summer 2027 internships across eight role categories, modeled on repos like [SimplifyJobs/Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships) but broader in scope (more categories, more sources). This is a market listing of what's out there — it does not track Tony's own application status. That remains the separate job of `../summer2027_internship_tracker.xlsx`; there is zero overlap between the two systems.

## Non-goals

- Not a personal application-status tracker (no "Applied" column, no CRM-style fields).
- Not guaranteed exhaustive on day one. "Comprehensive" is a direction to grow into per category, not a day-one claim — see "Realistic Coverage Expectations" below.
- Not scraping anything automatically on a schedule. Scraping only happens when explicitly requested ("scrape" or "scrape \<category>").
- Not auto-pushed. Committing locally after a scrape is automatic; pushing to GitHub is exclusively Tony's action — the assistant never runs `git push`, on any cadence, for any reason, without an explicit in-the-moment request.

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
├── .gitignore                      # Backstop: excludes any local .env / credential file, even though none should be repo-tracked
└── docs/superpowers/specs/         # This spec and future ones
```

One data file per category (not one giant file) — cleaner git diffs, and the "Data integrity safeguards" section below defines how writes to these files stay race-free even under parallel scraping.

## Data schema

Each entry in a category's yaml file (illustrative example, not real data):

```yaml
- id: jane-street-quant-trading-nyc-a1b2c3    # slug: company + role + first 6 chars of a stable hash (see Dedup key)
  company: Jane Street
  source_ref: https://boards.greenhouse.io/janestreet/jobs/1234567  # the source's own canonical link/ID for this exact posting — primary identity
  role: Quantitative Trading Intern
  track: Trading                        # quant.yaml only; omitted elsewhere
  location: New York, NY                # single location, UNLESS the source itself presents one posting spanning multiple offices (see below)
  link: https://boards.greenhouse.io/janestreet/jobs/1234567
  date_posted: 2026-07-15               # true posting date if the source exposes one; otherwise equals date_added, and the README legend notes this may be a discovery-date proxy
  term: Summer 2027                     # Summer 2027 | Fall 2026 | Off-Cycle | etc.
  degree: [BS, MS]                      # subset of [BS, MS, PhD]
  status: open                          # open | closed — closed rows are kept, never deleted
  source: greenhouse                    # audit trail: where first discovered
  date_added: 2026-07-22
  last_verified: 2026-07-22
  miss_count: 0                         # consecutive scrapes where this posting was checked and NOT found; see closed-detection below
```

### Multi-location postings

A single row lists multiple locations (e.g. `New York, NY / Chicago, IL`) **only when the source itself represents that as one posting with one link/ID** (e.g. a job board entry whose own location field is a multi-city string, or a GitHub-repo table row that lists multiple cities for one link). When a company instead posts the *same title* as **separate job IDs per office** — which is how Greenhouse and Lever typically structure multi-office roles — those stay as **separate rows**, each with its own single location and its own distinct link. We never synthesize a combined-location row by merging two separately-linked postings ourselves: doing so would silently drop one of the two application links, and would risk merging two genuinely different reqs that happen to share a generic title (e.g. two distinct "Software Engineer Intern" openings at the same company).

### Dedup key

**Primary:** `source_ref` — the source's own stable identifier for a posting (a Greenhouse/Lever job ID or URL, a specific line in a GitHub tracker table, etc.). Two scrape results with the same `source_ref` are the same posting, full stop; a re-find updates `last_verified` in place.

**Fallback**, only for sources with no stable per-posting identifier (e.g. a markdown table row with nothing but visible text): normalized `(company, role title, location)` triple — exact single-location match, not a set. Normalization: lowercase, strip whitespace, strip common legal suffixes from company names (Inc., LLC, Corp., Corporation, Ltd.), canonicalize location to `City, ST` using two-letter state abbreviations. This fallback deliberately does **not** attempt cross-location merging — see "Multi-location postings" above.

`id` is a slug generated from `company + role + a short hash of source_ref (or the fallback triple)`, used only for human-readable file/anchor purposes — it is never itself the dedup key.

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

`generate_readme.py` also renders a short legend (what 🔒 / Term values / degree abbreviations mean, and the "Date Posted may be a discovery-date proxy" caveat) and a "Last updated: \<date>" stamp at the top of the generated `README.md`.

## Scraping — source-to-tool mapping

| Source | Tool | Notes |
|---|---|---|
| The 4 linked GitHub repos (northwesternfintech/2027QuantInternships, sndsh404/summer-2027-internships, speedyapply/2027-SWE-College-Jobs, Chieler/Summer-2027-SWE-Internships) + any others found | Direct fetch of raw markdown | No Firecrawl needed — plain public markdown. Also the reference model for in-line closed-markers — see closed-detection below |
| Company career pages on Greenhouse or Lever | Their public no-auth JSON API directly (`boards-api.greenhouse.io/v1/boards/{token}/jobs`, `api.lever.co/v0/postings/{company}`) | Fast, free, zero Firecrawl credits. Degree eligibility is **not** a structured field on either API — parsed from the free-text description via keyword matching (Bachelor's/Master's/PhD/"currently pursuing"). Posting date is similarly unreliable (Greenhouse's `updated_at` changes on any edit, not just the original post) — use the best available "first seen" field per API response; fall back to `date_added` when nothing better exists |
| Company career pages elsewhere (Workday, custom sites) | Firecrawl `scrape`/`crawl` primary; Playwright (installed locally — Python 1.58.0 + `@playwright/cli`) as a free, local, scriptable fallback | Firecrawl handles JS rendering with no script to maintain; Playwright is worth writing a small script for a specific site scraped repeatedly, or when a page needs custom pagination/interaction Firecrawl's `actions` list can't cleanly express, or simply to avoid spending Firecrawl credits on a high-frequency target |
| simplify.jobs (the website) | Firecrawl `scrape`/`crawl` | Public/browsable without login, JS-rendered |
| Indeed | Firecrawl first (`proxy: auto`) | Falls back to the logged-in browser tool only if Firecrawl gets blocked |
| LinkedIn, Handshake | claude-in-chrome browser tool against Tony's already-logged-in session | Never routed through Firecrawl or Playwright; no credentials handled by the assistant. This does carry a different risk than credential exposure: automated-looking activity on Tony's own account could get it flagged/rate-limited by LinkedIn/Handshake. Mitigate by keeping these reads light and infrequent (targeted searches, not exhaustive crawling), not by treating the approach as risk-free |

Firecrawl runs as an MCP server (`firecrawl-mcp`), authenticated via `FIRECRAWL_API_KEY`. **Key storage is decided now, not deferred:** the key lives in a shell environment variable or Tony's global (out-of-repo) Claude Code MCP config — never in a project-level `.mcp.json` or any file tracked inside `internship-tracker/`. The repo's `.gitignore` excludes `.env` and similar files as a backstop even though none should exist in-tree. Server config falls back to the keyless free tier (scrape/search only, rate-limited) until the key is supplied; note that `crawl` (needed for Workday/custom pages and simplify.jobs) is **not** available keyless, so those sources — and by extension most of the six thin categories — stay effectively blocked until the key is added.

`sources/companies.yaml` seeds with a reasonable default company list per category (ATS type noted where known) and grows over time as scrapes surface new companies worth watching directly.

## Realistic coverage expectations

The 4 linked repos and most other findable Summer 2027 trackers are SWE/quant-heavy. They give strong bootstrap coverage for Software Engineering and Quantitative Finance, but essentially nothing for Data Science, AI/ML, Hardware Engineering, Actuarial, Consulting, or Investment Banking — those six categories have no comparable aggregator and depend on company-by-company scraping and job-board search from day one. Expect the first scrape to leave SWE/quant well-populated and the other six thin; they fill in incrementally as `companies.yaml` grows across subsequent scrape sessions, not all at once.

## Data integrity safeguards

These rules exist because a scrape run is not guaranteed to succeed cleanly on every source every time, and the cost of trusting bad data silently (auto-committed, no review gate) is higher than the cost of being conservative about what counts as "closed."

- **Fetch-failed vs. confirmed-absent.** A source that times out, rate-limits, or errors during a scrape is left untouched for this run — it does *not* count as evidence the role is gone. Only a source that was **successfully fetched and parsed**, and in which a previously-open role is genuinely not present, counts as a "miss."
- **N-consecutive-misses threshold.** A role is only marked `closed` after **2 consecutive misses** (i.e., the source was successfully checked twice in a row with the role absent both times) — not on a single miss. This absorbs one-off flakiness (a Firecrawl timeout, a thin browser read, pagination that didn't fully load) without wrongly closing roles that are actually still open. `miss_count` in the schema tracks this per role; it resets to 0 the moment the role is found again.
- **In-line closed markers take priority.** For GitHub-style sources (including SimplifyJobs-style repos, confirmed by inspecting their actual format), roles are often kept in the table but marked closed in place (e.g. a 🔒 emoji) rather than removed outright, sometimes only later archived to a separate file. When a source's own listing marks a role closed in-line, treat that as an immediate, confident `closed` signal — it does not need to go through the miss-count threshold, since it's an explicit statement from the source rather than an inference from absence.
- **Row-count sanity check.** If a source that previously yielded N roles returns dramatically fewer in one run (e.g., under 20% of the prior count), treat that as a likely fetch/parse failure for this run — surface it to Tony rather than silently applying it as "most of these roles closed."
- **US-only filter is an enforced pipeline step, not just a stated goal.** After parsing and before dedup/merge, any posting whose location doesn't resolve to a US city/state or "Remote (US)" is dropped.

## Per-scrape mechanics

Trigger: user says "scrape" (all sources) or "scrape \<category>" (targeted to one category's sources).

1. Fetch/scrape each in-scope source per the tool mapping above (dispatched as parallel subagents per source where useful).
2. Parse results into the schema; apply the US-only filter.
3. Each subagent returns its parsed results to the parent session — it does **not** write to `data/*.yaml` or `sources/companies.yaml` itself.
4. The parent session performs one serialized dedupe-and-merge pass per category file: match against `source_ref` (or the fallback triple), add genuinely new rows, and apply the "Data integrity safeguards" rules above to update `status`/`last_verified`/`miss_count` on existing rows. This single-writer-per-file step is what actually prevents write races — splitting data by category only helps if writes are also serialized; concurrent subagents writing the same file directly would not be safe on their own. The same serialization applies to any append to the shared `sources/companies.yaml`.
5. Regenerate `README.md` via `generate_readme.py`.
6. Commit locally: `git commit -m "scrape: update roles as of <date>"`.

**Push policy:** commits happen automatically each scrape; pushing to the GitHub remote never happens automatically, on any cadence. Push is exclusively Tony's action, run by him whenever he chooses.

## GitHub remote

Private repo at `tonyyu2170/internship-tracker`. Created during initial setup; never pushed to by the assistant (see push policy above and in Non-goals).

## Initial population plan

1. Scaffold the empty framework: schema, `generate_readme.py`, empty `data/*.yaml` files, seeded `sources/companies.yaml`, `.gitignore`.
2. Run one seed scrape against the 4 linked repos, plus a search for any other public Summer 2027 tracker repos.
3. Generate the first `README.md` from whatever that seed scrape yields — expected to be SWE/quant-heavy per the coverage expectations above.

## Open items for the implementation plan

- Exact starting company list per category in `sources/companies.yaml` (to be drafted during implementation, editable afterward), including which ATS (Greenhouse/Lever/Workday/custom) each target company uses, since that determines which tool handles it.
- Whether any specific high-frequency career-page target is worth a dedicated Playwright script versus relying on ad-hoc Firecrawl calls — a call to make per-company as watch-list entries are added, not up front.
