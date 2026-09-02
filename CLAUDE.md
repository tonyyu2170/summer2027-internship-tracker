# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository, including the scheduled cloud routines described under "Hard rules" below.

## Purpose

A US-only **market listing** of Summer 2027 internships across six categories (SWE, Quant, Data Science, AI/ML, Hardware, Actuarial), modeled on repos like SimplifyJobs/Summer2026-Internships. It tracks what's out there in the market — it is not anyone's personal application-status tracker.

Remote: `origin` → `git@github.com:tonyyu2170/summer2027-internship-tracker.git`.

## Tech stack & commands

Python 3.12, PyYAML, jsonschema, pytest. Firecrawl MCP + Playwright + claude-in-chrome are scraping-only tools — no test depends on them. The repo has its own `.venv` locally — prefer `.venv/bin/python3` over bare `python3`, which may resolve to an unrelated project's venv.

```bash
python3 -m pytest tests/ -v                        # full suite
python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('data/*.yaml')+['sources/companies.yaml']]; print('all valid')"  # validate YAML data files
python3 scripts/run_scrape_merge.py scratch/fetch_reports   # the single serialized merge+README-regen entrypoint
python3 scripts/check_integrity.py                          # data invariants; run before any commit touching data/
python3 scripts/check_programs.py                           # program/research/competition watch-list re-check (explicit request only)
python3 scripts/check_ats.py [category ...]                 # ATS-API verification probe (explicit request only) -> scratch/ats_corrections.json
python3 scripts/apply_ats_corrections.py scratch/ats_corrections.json   # apply corrections; then check_integrity + commit
python3 scripts/check_reposts.py [category ...]             # find roles re-listed under a new req id -> scratch/repost_corrections.json
python3 scripts/check_categories.py                         # rows sitting in a category their role no longer classifies to -> scratch/category_corrections.json
python3 scripts/probe_boards.py discover|mine|candidates|verify|apply   # watch-list board discovery/verification (see docs/SCRAPING.md "Growing the watch-list")
python3 scripts/generate_dashboard.py                       # docs/dashboard.html — one-page market view of data/ (pure; publish it as an artifact to share)
```
(`apply_ats_corrections.py` is the shared applier for all three `check_*.py` correction files — pass whichever `scratch/*_corrections.json` was produced.)

`conftest.py` at the repo root puts `scripts/` on `sys.path` so tests import modules like `from normalize import ...` directly.

## Architecture

Two sides meeting at one JSON contract:

- **Tested core (pure Python, network-free):** `scripts/normalize.py` (link/company/location normalization + US-location check), `scripts/schema.py` (`ROW_SCHEMA` + `validate_row`), `scripts/merge.py` (`merge_category(existing_rows, fetch_reports, today) -> (rows, summary)` — the dedupe/merge engine), `scripts/generate_readme.py` (`render(data_dir, readme_path)` — YAML → `README.md`).
- **Orchestration:** `scripts/run_scrape_merge.py` is the single serialized writer. It loads fetch-report JSON files, groups by category, calls `merge_category` once per category, validates and filters new rows, rewrites that category's `data/*.yaml`, then calls `render(...)` to regenerate `README.md`. It never touches git.
- **Fragile, source-specific scraping** lives entirely outside the tested core, in the procedure documented at `docs/SCRAPING.md` — never mix network code into `normalize.py`, `merge.py`, etc.
- **Fetch-report contract:** scraping subagents return parsed postings only; they never write `data/*.yaml` or `sources/companies.yaml` directly. One fetch-report JSON per source entity goes to `scratch/fetch_reports/` (git-ignored), then `run_scrape_merge.py` runs as the one serialized pass — this single-writer-per-file step is what prevents write races.
- **Data layout:** one YAML file per category under `data/` (`swe.yaml`, `quant.yaml`, `data_science.yaml`, `ai_ml.yaml`, `hardware.yaml`, `actuarial.yaml`); `sources/companies.yaml` is the per-category company watch-list (`ats`: greenhouse | lever | ashby | workday | smartrecruiters | workable | icims | custom; the first six are pulled by `fetch_companies.py`).

### Dedup key

Primary: the **normalized application link** (`normalize_link` — strips tracking params, lowercases scheme+host, drops trailing slash; collapses one requisition's link shapes per ATS — a Workday link keys on tenant + requisition id, so site aliases and `-N` instance suffixes are one posting; Ashby/Lever/Greenhouse/Workable slugs case-fold; iCIMS keys on `/jobs/<id>`). Fallback, only when a source has no extractable link: a normalized `(company, role, location)` triple — this is **never auto-merged**; it's added as a new row with `possible_duplicate_of` set and surfaced in the run summary for manual review, since a bare triple carries real false-merge risk. (In practice, most triple-flagged pairs turn out to be genuinely distinct postings, not duplicates — don't delete on a flag alone.)

### Status / closing

`status: closed` is set **only** by an explicit in-line marker from the source itself (`closed_marker: true`) or by a manual edit. Nothing is auto-closed just because a role stops appearing in a scrape. **Do not build disappearance-based auto-closing** (`miss_count`, a completeness gate, row-count sanity checks) — deliberately cut from the design, not an oversight.

### US-only filter

Applied via `canonicalize_location`/`is_us_location` (word-boundary matching, not substring — a plain substring check once falsely flagged cities containing "on"/"uk", e.g. Milwaukee, Dayton): any location that doesn't confidently resolve to a US city/state or "Remote (US)" is dropped rather than kept.

### A few non-obvious behaviors worth knowing

- `merge_category` is where the policy gates live for every source: an off-cycle title (`parse_tracker._is_off_cycle`), a non-US location, and a `date_posted` outside `[CYCLE_START, today]` (turned into an estimate). A re-found link also gets its title restored when the stored one is truncated (`extends_truncated`). Company boards never file a role under the watch-list category — a title no rule or `manual_categories.yaml` entry places is dropped as `unclassified_role`.
- `run_scrape_merge.py` validates and can drop only rows *newly created that run* against `ROW_SCHEMA`. Existing rows loaded from disk are never auto-deleted for failing schema — a malformed hand-edit is kept as-is (with a warning) rather than silently removing a previously-tracked listing.
- `merge.py` looks up existing rows via `.get("id")`, not bracket-indexing, so a hand-corrupted row missing `id` degrades gracefully instead of crashing the category's run.
- `run_scrape_merge.py` keeps a *new* link that two reports file under different categories in one run in exactly one of them (`classify_role`'s verdict, else the first report in sorted order; counted as `cross_category_duplicate`). Without this a full tracker re-parse landed 46 same-id twins (2026-09-02). A link already on disk in another category is still only reported by `check_integrity`, never moved.
- `fetch_companies.py` caps concurrent Workday pulls at 4 and retries HTTP 429 after 15/30/60s: at ~950 Workday boards the 16-thread prefetch rate-limited 113 tenants in one run.
- `generate_readme.py` escapes `|`/newlines in scraped text and uses angle-bracket link destinations (`[Apply](<url>)`) so scraped company/role/location text can't corrupt the Markdown table. Same-date rows render newest-scraped-first (tie-broken by list position, since `merge.py` always appends new rows to the end).

## Hard rules

- **Local/interactive scraping only runs on explicit request** — "scrape" (GitHub trackers), "scrape companies" (adds the wired greenhouse/lever/ashby/workday watch-list), or "scrape \<category>" — never on a schedule for a local session. A plain "scrape" runs via `bash scripts/auto_scrape.sh` (trackers → merge → auto-verify new rows → README → targeted local commit; stops and appends to `scratch/auto_scrape/NEEDS_ATTENTION` when judgment is needed).
- **Three scheduled cloud routines** ("Internship Scrape — Morning/Midday/Evening", 7:30am/12pm/8pm ET) run trackers-only 3x/day (company-ATS boards are skipped there — that cloud environment's egress policy blocks those hosts outright) and propose changes via a PR on the `claude/auto-scrape` branch. They never push to `main`, force-push, or merge/close the PR themselves — merging into `main` stays a manual decision. This is the one exception to the rule below.
- **The assistant never runs `git push` to `main` in this repo, under any circumstance, in an interactive session.** Pushing to `main` is reserved for manual action by the repo owner.
- **Firecrawl's `FIRECRAWL_API_KEY` lives outside this repo** (shell env var or a global, out-of-repo Claude Code MCP config) — never in a project-level `.mcp.json` or any file tracked here.
