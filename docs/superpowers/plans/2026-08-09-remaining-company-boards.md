# Remaining company-board work (post-Workday)

Handoff from the 2026-08-09 Workday session (commits `aa60082`, `736bf71`,
`ad31b10`). Tony asked to tackle these in a fresh session. Everything below is
grounded in probes already run — don't re-derive it, verify and proceed.

**Read first:** `docs/SCRAPING.md` (company-board section), `CLAUDE.md` hard
rules. Standing rules that still apply: scraping runs on explicit request
only, the assistant never runs `git push`, and `check_integrity.py` runs
before any commit touching `data/`.

## State at handoff

`sources/companies.yaml` holds 1000 companies. Wired: greenhouse (187),
ashby (131), lever (20), workday (114), plus 3 rich `provider:` entries.
Unwired: **custom 524, icims 12, smartrecruiters 9** — 545 total.

Company-board postings now route through `categorize.classify_role`
(`ad31b10`): a DROP verdict drops the row, a confident verdict files it under
that category (one source can write several reports), and only an
unclassifiable role falls back to the watch-list category.

Test suite: 393 passing (`.venv/bin/python3 -m pytest tests/ -v`). Prefer
`.venv/bin/python3` over bare `python3`.

---

## Task 1 — three failing Workday boards

3 of 114 boards error and degrade to `source_parse_failed` (harmless, but
they're silent gaps):

| Company | Category | URL | Symptom |
|---|---|---|---|
| IDEXX | swe | `https://idexx.wd1.myworkdayjobs.com/IDEXX` | HTTP 422 |
| Penn State University | swe | `https://psu.wd1.myworkdayjobs.com/Student` | HTTP 404 |
| Castleton Commodities Intl | quant | `https://osv-cci.wd1.myworkdayjobs.com/CCICareers` | HTTP 422 |

The derivation `_workday_site()` assumes `{tenant}.wd{N}.myworkdayjobs.com/{site}`
→ tenant = first host label, site = first path segment. That holds for 111 of
114. Hypotheses worth checking, cheapest first:

1. **Tenant ≠ host label.** `osv-cci` is suspicious — Workday tenants
   sometimes differ from the vanity host. Load the careers page in the
   browser, watch the network tab for the real `/wday/cxs/<tenant>/<site>/jobs`
   POST, and compare.
2. **Site id renamed/retired** (likely for the Penn State 404 — `Student` may
   have been folded into another site).
3. **422 = malformed request body for that tenant**, not a bad URL. Some
   tenants reject `appliedFacets: {}` or require a `locations` facet.

Fix shape: if the URL is simply wrong, correct it in `sources/companies.yaml`
(the watch-list is data, not code). Only touch `_workday_site()` if a *shape*
is genuinely different — and add a test if so. Verify each with a real fetch
before committing.

## Task 2 — cosmetic: `Mckinney, TX` should be `McKinney, TX`

`_workday_place()` in `scripts/parse_company.py` upper-cases RTX-style
site-code locations with `.title()`, which mangles intercaps city names
(`MCKINNEY` → `Mckinney`, and any `McX`/`O'X`/`LaX` name).

Low value — per `[[ats_verification_shipped]]` location is no longer
displayed; the field survives for dedup and the US filter, and the value
still canonicalizes correctly. Two honest options:

- **Leave it and delete this task.** Defensible.
- Fix by preferring a correctly-cased city from elsewhere in the payload
  rather than by hand-maintaining an intercaps list — hardcoding `Mc`/`Mac`
  rules will be wrong somewhere else.

Test to add either way: `_workday_place("US-TX-MCKINNEY-513WZ ~ ...", True)`.
Existing tests in `tests/test_parse_company.py` pin `Mckinney, TX` today, so
they must be updated together with any fix.

## Task 3 — the 524 custom entries (the real work)

**Correction to carry forward:** all 99 `verified: false` entries are
`custom` — a subset of the 524, not a separate group. Hand-check those links
before wiring anything that depends on them.

**The hard finding: there is no head to attack.** Host concentration across
the 524 is almost flat —

```
9  apply.workable.com        2  www.citadelsecurities.com
7  www.workatastartup.com    2  www.drw.com
3  careers.sig.com           2  www.deshaw.com
2  www.janestreet.com        1  ...~500 hosts with a single entry
```

So "wire custom" is ~500 bespoke scrapers, not a handful. Do **not** plan a
generic custom-host scraper — that's the trap. Suggested order:

1. **Wire icims (12) and smartrecruiters (9) first.** Both have documented
   public JSON APIs, both are `_ATS_PROVIDERS` one-liners plus a parser, and
   both reuse the existing `_filter_board_job` / `_board_posting` helpers.
   This is the same shape as the greenhouse/lever/ashby work — a known-good
   pattern, ~21 companies, an afternoon.
2. **Then `apply.workable.com` (9) and `workatastartup.com` (7)** — real
   ATSes with stable APIs, so they behave like step 1 rather than like
   bespoke scraping.
3. **Then stop and measure before going further.** Early August yield is
   structurally low (most boards have no Summer-2027 posting yet); the
   Workday pull found only 188 intern-titled hits across 114 boards, and
   categorization cut that to ~65 on-scope. Re-measure yield-per-board in
   the fall before spending effort on a ~500-host long tail that may return
   a handful of rows.
4. **Only then** consider Firecrawl per-site for the highest-value remaining
   names, driven by which companies Tony actually cares about — not by
   working down the list.

Architecture constraint (from `CLAUDE.md`): fragile source-specific scraping
stays out of the tested core. New parsers go in `parse_company.py` as pure
functions over a fetched payload; network code stays in `fetch_companies.py`.

## Open decision not in scope here

`categorize.py` precision, surfaced by the Workday pull and deliberately left
alone: `internship program` is a DROP alternative, so Capital One's
"Technology Internship Program" — a real SWE internship — is dropped, while a
bare `engineer` match files civil/mechanical interns under swe. Tuning those
rules affects the tracker path too, so it needs Tony's call.
