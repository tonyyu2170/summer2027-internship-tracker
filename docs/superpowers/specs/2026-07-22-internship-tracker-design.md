# Summer 2027 Internship Tracker — Design Spec

**Date:** 2026-07-22
**Status:** Revised after multi-agent review, pending implementation plan

## Purpose

A comprehensive, US-only listing of Summer 2027 internships across eight role categories, modeled on repos like [SimplifyJobs/Summer2026-Internships](https://github.com/SimplifyJobs/Summer2026-Internships) but broader in scope (more categories, more sources). This is a market listing of what's out there — it does not track Tony's own application status. That remains the separate job of `../summer2027_internship_tracker.xlsx`; there is zero overlap between the two systems.

## Non-goals

- Not a personal application-status tracker (no "Applied" column, no CRM-style fields).
- Not guaranteed exhaustive on day one. "Comprehensive" is a direction to grow into per category, not a day-one claim — see "Realistic Coverage Expectations" below.
- Not scraping anything automatically on a schedule. Scraping only happens when explicitly requested ("scrape" or "scrape \<category>").
- Not auto-pushed, and not assistant-pushed at all. Committing locally after a scrape is automatic; pushing to GitHub is exclusively Tony's action, run by Tony himself — the assistant never runs `git push` in this repo, under any circumstance.

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
  role: Quantitative Trading Intern
  track: Trading                        # quant.yaml only; omitted elsewhere
  location: New York, NY                # single location, UNLESS the source itself presents one posting spanning multiple offices (see below)
  link: https://boards.greenhouse.io/janestreet/jobs/1234567   # normalized application URL — this IS the primary dedup identity, see Dedup key
  date_posted: 2026-07-15               # true posting date if the source exposes one; otherwise equals date_added, and the README legend notes this may be a discovery-date proxy
  term: Summer 2027                     # Summer 2027 | Fall 2026 | Off-Cycle | etc.
  degree: [BS, MS]                      # subset of [BS, MS, PhD]
  status: open                          # open | closed — closed rows are kept, never deleted
  sources: [github_tracker, greenhouse] # every source that has independently confirmed this exact posting; grows over time, never overwritten
  date_added: 2026-07-22
  last_verified: 2026-07-22
  possible_duplicate_of: null           # set to another row's `id` only when matched via the low-confidence fallback key — see Dedup key
```

### Multi-location postings

A single row lists multiple locations (e.g. `New York, NY / Chicago, IL`) **only when the source itself represents that as one posting with one link/ID** (e.g. a job board entry whose own location field is a multi-city string, or a GitHub-repo table row that lists multiple cities for one link). When a company instead posts the *same title* as **separate job IDs per office** — which is how Greenhouse and Lever typically structure multi-office roles — those stay as **separate rows**, each with its own single location and its own distinct link. We never synthesize a combined-location row by merging two separately-linked postings ourselves: doing so would silently drop one of the two application links, and would risk merging two genuinely different reqs that happen to share a generic title (e.g. two distinct "Software Engineer Intern" openings at the same company).

### Dedup key

**Primary: the normalized application link.** This is deliberately source-agnostic, because it's what actually reconciles the same real posting when it's found through two different sources — e.g. one of the 4 GitHub tracker repos and a direct Greenhouse fetch both pointing at the same job. Per-source identifiers (a GitHub tracker row, a Greenhouse job ID) are NOT used as the primary key on their own, because a key scoped to "whichever source we happened to see it through first" never reconciles across sources — the same job could otherwise sit in the data as two rows with two different `status` values (open per one source, closed per another), which is a worse outcome than a plain duplicate. Normalization: strip tracking/query parameters (`utm_*`, `ref`, `gh_src`, `lever-source`, etc.), lowercase scheme+host, and resolve known redirect/tracking wrappers (e.g. a simplify.jobs outbound link) to their final destination where feasible. Two results whose normalized links match are the same posting — merge them, and add whichever source found it this time to that row's `sources` list (which only grows, never gets overwritten) rather than replacing it.

For GitHub-tracker sources specifically, the link used for matching is **the row's own embedded application URL**, never the row's position or line number in the table — these tables get resorted/reordered on nearly every update, so position is never a valid identity, only the href is.

**Fallback**, only for the rare source with no extractable application link at all (e.g. a listing described purely in text with no href): normalized `(company, role title, location)` triple. Normalization: lowercase, strip whitespace, strip common legal suffixes from company names (Inc., LLC, Corp., Corporation, Ltd.), canonicalize location to `City, ST` using two-letter state abbreviations. This fallback deliberately does **not** attempt cross-location merging — see "Multi-location postings" above. Because a bare triple carries real false-merge risk (two genuinely different reqs can share a generic title, company, and city — the same hazard flagged for multi-location handling), a fallback-triple match is **never auto-merged** into an existing row. Instead it's added as its own new row with `possible_duplicate_of` set to the closest-matching existing row's `id`, and listed in the scrape's end-of-run summary for Tony to manually confirm or reject.

`id` is a slug generated from `company + role + a short hash of the normalized link (or the fallback triple)`, used only for human-readable file/anchor purposes — it is never itself the dedup key.

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

- **US-only filter (enforced pipeline step).** After parsing and before dedup/merge, any posting whose location doesn't *confidently* resolve to a US city/state or "Remote (US)" is dropped. Ambiguous or un-mappable locations are dropped rather than kept — a US-only tracker errs toward exclusion.
- **In-line closed markers are the closed signal.** For a source whose own listing marks a role closed in place (e.g. a 🔒 emoji in a SimplifyJobs-style repo, confirmed by inspecting the actual format), that marker sets `status: closed` directly — an explicit statement from the source, not an inference. A role carrying no such marker keeps whatever status it already has; nothing is auto-closed merely because it stopped appearing in a scrape. Tony can also set `status: closed` by hand.
- **Fallback-triple matches are flagged, never auto-merged** (see Dedup key): added as their own row with `possible_duplicate_of` set, and surfaced in the end-of-run summary for Tony to confirm or reject.

**Deferred — intentionally not built in the initial version.** Earlier drafts of this spec specified a disappearance-based auto-closing state machine: a `miss_count` field, a 2-consecutive-miss threshold before closing, a fetch-completeness gate, and a per-entity row-count sanity check. These are cut on purpose. (1) Per Tony's standing simplicity/YAGNI guidance, a wrong `status` on a personal browse-list is low-stakes — a stale "open" row costs one dead-link click. (2) The completeness gate the whole edifice depends on is unknowable for exactly the fragile sources it would protect (Firecrawl-rendered JS pages, browser-read LinkedIn/Handshake), where you cannot confirm you saw every posting — so the most complex piece would be least reliable where it matters most. If disappearance-detection later proves genuinely necessary, it can be reintroduced scoped to only the sources where absence is a clean, complete signal (Greenhouse/Lever JSON responses, the single-file GitHub markdown fetches), not applied blanket. Until then, closing is driven by in-line markers plus Tony's manual edits.

## Per-scrape mechanics

Trigger: user says "scrape" (all sources) or "scrape \<category>" (targeted to one category's sources).

1. Fetch/scrape each in-scope source per the tool mapping above (dispatched as parallel subagents per source where useful).
2. Parse results into the schema; apply the US-only filter.
3. Each subagent returns its parsed results to the parent session — it does **not** write to `data/*.yaml` or `sources/companies.yaml` itself.
4. The parent session performs one serialized dedupe-and-merge pass per category file: match against the normalized application link (or the fallback triple, flagged via `possible_duplicate_of` rather than merged — see Dedup key), add genuinely new rows, and apply the "Data integrity safeguards" rules above to update `status`/`last_verified` on existing rows. This single-writer-per-file step is what actually prevents write races — splitting data by category only helps if writes are also serialized; concurrent subagents writing the same file directly would not be safe on their own. The same serialization applies to any append to the shared `sources/companies.yaml`.
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
