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
Scraping is never scheduled; it runs only on an explicit request. (A
launchd every-6h schedule was tried and reverted the same day, 2026-08-09 —
Tony prefers saying "scrape".) The standard way to execute a plain "scrape"
is `bash scripts/auto_scrape.sh` — one shot: trackers → merge → README → a
targeted local commit, never `git push`. It skips itself while
`scratch/ats_corrections.json` exists, another writer process runs, or
`data/`/`README.md` have uncommitted changes; on anything needing judgment
(unclassified postings, integrity violations) it stops without committing
and appends to `scratch/auto_scrape/NEEDS_ATTENTION` (log:
`scratch/auto_scrape/auto_scrape.log`). Resolve unclassified rows via
per-link entries in `sources/manual_categories.yaml` (or a `categorize.py`
rule for a recurring family), then re-run.

**Every scrape auto-verifies its new rows** (added 2026-08-09): after a
successful merge the runner calls `scripts/verify_links.py`, which probes
each row added today (ByteDance/TikTok SSR titles, Workable JSON API,
generic HTML elsewhere) and applies only unambiguous outcomes — explicit
non-2027 term ⇒ delete + suppress in `manual_categories.yaml`; hard
404/410 ⇒ delete; authoritative ByteDance/TikTok title ⇒ restore role text.
Ambiguous results (bot-blocks, missing SSR titles, metadata-only years) are
never acted on. A flood of wrong-term flags aborts with no changes (format
break, not data). Judgment rules live in `scripts/link_verify.py`
(unit-tested); audit trail: `scratch/verify_links_audit.json`.
`python3 scripts/verify_links.py --all` re-verifies every open row —
explicit request only, like all full passes.

Consulting and investment banking are intentionally out of scope. Matching
roles are counted as `category_drop` and never create a category file.

Direct company boards (wired 2026-08-09): `python3 scripts/fetch_companies.py
[category ...]` (default: all six) pulls every watch-list entry with a wired
provider — the rich `provider:` entries plus, implicitly, legacy entries
whose `ats` is greenhouse/lever/ashby/smartrecruiters (public JSON APIs) or
workday (all wired 2026-08-09; `custom`, `icims`, and `verified: false`
entries are still skipped with counters — see "iCIMS is deliberately
unwired" below). Board pulls keep only intern-titled roles with explicit
Summer-2027 evidence and a US location, skip links already tracked in a
different category (`tracked_elsewhere` — categories dedupe independently),
and emit the same fetch-report contract. Shortcut for the whole cycle:
`bash scripts/auto_scrape.sh --companies` (trackers + boards + merge +
auto-verify + local commit). This runs only on explicit request — "scrape
companies" — like every non-tracker source.

A watch-list board is a company's *whole* intern programme, so most of what
it returns is off-scope (supply chain, HR, outside sales). The watch-list
category therefore says only where a company's rows tend to live, not what
any one role is: every posting goes through `categorize.classify_role`, the
same rules the tracker path uses. A `DROP` verdict drops the posting
(`category_drop`); a confident verdict files it under *that* category, so one
source can write several reports (`company_acme_data_science.json` alongside
`company_acme_swe.json`) and a source clears its report in every category
before a run; only an unclassifiable role falls back to the watch-list
category. Measured on the first full Workday pass this cut 145 hits to ~65.
Residual imprecision is categorize.py's, not the board path's — `internship
program` is a DROP alternative, so "Technology Internship Program" drops,
while a bare `engineer` match sends civil/mechanical interns to swe.

Workday is a two-stage pull, because its search response has no description
and shows a multi-site posting only as "3 Locations". Stage 1 searches the
board's CXS endpoint for the phrase `Summer 2027` — Workday *ranks* rather
than filters, so a bare "intern" matches most of a board while the full
phrase narrows it (Capital One: 1775 postings -> 5). Stage 2 pulls the job
detail behind each intern-titled hit, which is where the description,
`additionalLocations`, and the absolute `startDate` live. `tenant`/`site`
come from the watch-list URL (`{tenant}.wd{N}.myworkdayjobs.com/{site}`), a
title pre-filter bounds how many detail requests a board costs, and a 5-page
cap (`search_truncated`) stops a runaway board. CXS 406s on the shared
`text/html` Accept header, so those requests send `application/json`.

Workday's error codes tell you which half is wrong, which is worth knowing
before hand-editing a board URL: **422 means a bad tenant, 404 a bad site.**
The tenant is normally the first host label, but Workday tenant ids can't
contain `-`, so a hyphenated vanity host fronts an underscored tenant
(`osv-cci.wd1...` serves tenant `osv_cci`). Those entries pin `tenant:` (and
may pin `site:`) in `sources/companies.yaml` rather than relying on
derivation — a blanket `-` -> `_` rule would break a genuinely hyphenated
tenant. A site can also serve search but 404 every job detail (Penn State's
`Student`), in which case find the sibling site that serves both.

SmartRecruiters is the same two-stage shape for a different reason: its list
response carries no description, and it *ignores* the params that would
narrow the board server-side — `q=intern` ranks (AbbVie: still 724 of 1712,
MSL roles on top) and `experienceLevel=internship` is dropped entirely. So
the whole board is paged at 100/request. Unlike Workday, list rows carry a
structured location, so the intern-title pre-filter also checks
`location.country == "us"`, which is what keeps the detail leg cheap: 307
intern-titled hits across the nine boards reduce to 54 detail fetches.

**iCIMS is deliberately unwired.** Probed 2026-08-09: `careers-{slug}.icims.com`
serves an Angular-rendered `iCIMS_JobsTable` page, the `searchRss=1` parameter
returns HTML rather than a feed, and job-detail pages carry no JSON-LD. There
is no public JSON to parse, so the only route is bespoke HTML scraping — which
this repo deliberately doesn't build. The 12 entries stay `icims` and are
counted as `unwired_source`. Revisit only if iCIMS exposes a real API.

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
direct-company scraping. **2026-08-09: expanded to exactly 1000 companies**
(361 mined from data/ posting links — posting-evidenced; the rest curated
and live-probed; `verified: false` marks links a probe could not confirm).
Still a watch-list, but no longer only for the rich `provider:` entries:
greenhouse/lever/ashby/workday/smartrecruiters are all wired into
`fetch_companies.py` as of 2026-08-09. What remains unwired is `custom` (524
entries, a near-flat tail of ~500 distinct hosts) and `icims` (12, no public
JSON — see above).

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

## ATS verification pass (authoritative posting date / open state)

Manual, explicit-request-only re-verification of open rows whose links sit
on an API-covered ATS. Corrects `date_posted`, closes dead postings, and
deletes rows the API's country field says are non-US. It never touches
`location` — that field is kept for merge-time dedup and the US filter, but
is not tracked or rendered (Workday CXS, Greenhouse boards-api, Lever v0
postings, Ashby posting-api, SmartRecruiters postings API, iCIMS JSON-LD).
Design: `docs/superpowers/specs/2026-08-08-ats-verification-design.md`.

1. `python3 scripts/check_ats.py [category ...]` — probes the APIs and
   writes `scratch/ats_corrections.json` (the audit record of every
   proposed change). Writes nothing else.
2. Review the printed summary — every proposed close and non-US delete is
   listed individually.
3. **Before applying, group the proposed closes by org and look for any org
   where *every* row closes.** That is the shape of a board rename, not of
   real closures: `api_url` maps all of an org's rows to one board token
   (Ashby) or one company slug (Greenhouse/Lever/SmartRecruiters), so a
   renamed board 404s — or serves an empty `jobs` array — for every row at
   once. This is the one outcome the pipeline cannot self-correct: nothing
   ever re-opens a closed row, and `check_ats.py` skips closed rows on every
   future pass, so a false close leaves the README permanently and is
   repairable only by hand or `git revert`. Deletes are gated on affirmative
   country evidence and are individually printed; **closes are the riskier
   action here despite looking reversible.**

   ```bash
   python3 -c "import json,collections; \
   a=json.load(open('scratch/ats_corrections.json'))['actions']; \
   print(collections.Counter(x['ats'] for x in a if x['action']=='close'))"
   ```
4. `python3 scripts/apply_ats_corrections.py scratch/ats_corrections.json`
   — the single serialized writer: applies corrections, stamps
   `last_verified`, rewrites `data/*.yaml`, re-renders `README.md`. Aborts
   without writing if any corrected row fails ROW_SCHEMA, or if a
   corrections id matches more than one row (duplicate ids would delete
   every row sharing the id and edit the wrong one).
5. `python3 scripts/check_integrity.py`, then commit.

Never run concurrently with `run_scrape_merge.py` (single-writer
discipline), and **do not scrape between steps 1 and 4** — the corrections
file has no freshness check, so a stale `set_date` would clobber a fresher
merge result and a stale `close` would close a re-listed row.
`unknown` results change nothing — no disappearance-based closing.

## Repost check (explicit request only)

A company that re-lists a role gets a **new requisition id**, so the tracked
link keeps pointing at the superseded posting and the row keeps its original
`date_posted`. Since the README sorts newest-first, a role that just went live
sinks: InfiniteQuant's Summer 2027 QR internship sat at row 158 of 187 that
way, which is invisible if you only read the top of a section.

1. `python3 scripts/check_reposts.py [category ...]` — fetches each company's
   posting list **once per board** (not per row) and writes
   `scratch/repost_corrections.json`.
2. Review it. `repost` carries `old_link`/`new_link`/`new_date`; `ambiguous`
   is report-only, emitted whenever a title fans out (several tracked rows or
   several new postings share it) rather than guessing a pairing.
3. `python3 scripts/apply_ats_corrections.py scratch/repost_corrections.json`
   — the same applier consumes it. A `repost` rewrites `link` and
   `date_posted`, **recomputes `id`** (it hashes the link — leaving it stale
   is the known id/link drift bug), and appends the superseded link to
   `sources/manual_categories.yaml` as `__drop__` so the next scrape can't
   re-import the old posting as a second row.
4. `python3 scripts/check_integrity.py`, then commit.

Covers SmartRecruiters, Greenhouse and Lever only. **Workday is excluded on
purpose**: `normalize_link` collapses neither its `-N` requisition instance
suffixes nor its board aliases, so every such row would fail an exact link
comparison and fake a repost. For the same reason `repost_verify._link_key`
folds Greenhouse's two hostnames (`boards.` / `job-boards.greenhouse.io`) and
its `gh_jid` param for comparison only — a live run called Neuralink job
`6594422003` a repost of itself before that was added. Nothing here ever
closes a row: a tracked link absent from the listing with no title match
produces no action at all.

