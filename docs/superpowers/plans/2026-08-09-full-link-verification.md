# Full link verification pass — every open row, every host

Trigger (Tony, 2026-08-09): "The first link says 2026 start. Im sure a great
number of these kinds of issues exist throughout the repo. Go through every
single link and see what kinds of issues can occur, and fix these issues.
when api not available use playwright"

## Issue taxonomy (established by the 2026-08-09 ByteDance/TikTok pilot)

Verified against live pages, 136 rows probed:

1. **Wrong term** — tracker said Summer 2027, the live posting's own title
   says `2026 Start` / `2026 Summer` / `2026 Fall`. 18 of 136 (13%!).
   Fix: delete row AND add both link forms to `sources/manual_categories.yaml`
   as `__drop__` — deletion alone lets the next scrape re-import it.
2. **Truncated/stale titles** — upstream trackers truncate with literal
   `...`; live title is authoritative. 108 of 136 retitled (degree suffixes
   like `(BS/MS)` stripped — Degree is its own column).
3. **Cross-domain duplicate links** — one req served under two URL forms
   (fixed for ByteDance/TikTok in `normalize_link`; watch for the same
   disease elsewhere, e.g. vanity redirect domains).
4. **Dead links / closed postings** — only close on an unambiguous signal
   (existing `check_links.py` / `check_ats.py` rules; a missing SSR title on
   an HTTP-200 page is ambiguous → no action).
5. **Non-US / stale dates** — already covered for API hosts by `check_ats.py`.

## Coverage map (844 open rows before the pilot, 194 hosts)

- **Done (pilot, commits 2593112/2d704d9/50296f0):** lifeattiktok.com 102,
  jobs.bytedance.com 20, joinbytedance.com 14 — SSR `<title>` probe via the
  `/search/<id>` form (the `jobs.bytedance.com` detail page is a JS stub;
  `joinbytedance.com/search/<id>` serves full SSR HTML. Dead id ⇒ page with
  no job `<title>`).
- **Bucket A — API-covered (~300 rows):** greenhouse (113), ashby (60),
  lever (40), smartrecruiters (23), workday tenants, icims. Run the existing
  run-book: `check_ats.py` → review (board-rename check!) →
  `apply_ats_corrections.py`. THEN extend: `ats_verify.decide` does not yet
  check TERM — add a term check on the API title/description (flag non-2027
  markers for review, don't auto-delete).
- **Bucket B — custom hosts, SSR-first (~290 rows, 169 hosts):** for each
  host with >3 rows (janestreet 17, optiver 16, sig 16+14, microsoft 13,
  jumptrading 11, deshaw 11, drw 9, akuna 9, citadel 8+7, imc 7, jpmc
  oracle 7, workable 14...), curl one sample page: if title/term is in the
  HTML (like ByteDance was), write a per-host extractor into a probe config;
  **only if the page is a true JS shell, use Playwright** (Tony's
  instruction). Long-tail hosts (1-2 rows) can be batch-probed generically:
  fetch, look for the stored role text and a `20XX` term marker in the HTML.
- **Discipline:** every bucket writes an audit JSON to `scratch/` first
  (probe → review printed evidence → apply), same as the ATS pass. Never
  auto-close/delete on ambiguity. After each apply: `check_integrity.py`,
  re-render README, targeted commit. Deleted-for-term links ALWAYS go into
  `manual_categories.yaml` as `__drop__` or they resurrect.

## Status: EXECUTED 2026-08-09 (same session, commits a9f05af..1ef4c62)

All 844 open links were probed by at least one method: ATS APIs (467),
API-host SSR term-scan (472), ByteDance/TikTok SSR titles (136), generic
custom-host scan (235), Workable JSON API (14), Playwright (10 JS shells).

Results: **64 non-2027 rows deleted** (7.6% of the repo — 18 ByteDance/
TikTok, 11+1 custom-host, 34 API-host "Fall 2026" wave), 1 non-US delete,
9 dead rows closed (hard 404s + Point72 explicit page-not-found), 108
titles restored, 7 dates fixed. 844 → 770 open. Every deleted link is
suppressed in `sources/manual_categories.yaml` (deletion alone re-imports).

## Residual (probed, deliberately no action — ambiguous evidence)

- 47 ATS unknowns (38 Workday-HTML tenants, 9 iCIMS) — known steady-state.
- 17 HTTP 403 bot-blocked custom rows; 1 × 406; ~5 transient read errors.
- 6 SPA-redirects to generic careers pages (WSP ×2, Google ×2, DTCC, h-co)
  — may be dead, but redirect-to-search is not an unambiguous signal.
- 4 weak term suspects left open (Meta RS ×3 `start_time:2025` metadata,
  Corpay posting-timestamp); Eluvio "Last Minute Summer" (no year).

## Recurring exposure

Trackers keep mislabeling non-2027 postings as Summer 2027, so NEW rows
arrive unverified. The scans are repeatable ~5-minute procedures (threaded
scripts in the 2026-08-09 session; audit JSONs in scratch/). Options if it
recurs: re-run the scans after big scrape waves, or fold a term check into
`ats_verify.decide` (original Task 2 — still unbuilt, now optional).
