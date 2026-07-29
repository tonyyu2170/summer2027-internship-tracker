# Phase D — Refreshable actuarial sourcing

**Date:** 2026-07-29  
**Status:** In progress — Marsh/Oliver Wyman and Genworth are implemented;
The Hartford remains manual discovery.

## Scope decision

The tracker has six categories: SWE, Quantitative Finance, Data Science,
AI/ML, Hardware Engineering, and Actuarial. Consulting and Investment Banking
are deliberately out of scope: their historical rows are removed and future
matches are counted as `category_drop`, not placed in a replacement category.

Phase D focuses only on expanding actuarial coverage through direct,
first-party sources. It does not introduce a scheduled scrape, a generic web
crawler, disappearance-based closing, or another merge path.

## Current state

Three actuarial roles remain, all last verified on 2026-07-23 and carrying
only the non-refreshable `aggregator` source:

- Genworth Financial — Actuarial Development Program
- Marsh McLennan / Oliver Wyman — Actuarial Internship
- The Hartford — Actuarial Student Program

The watch-list already identifies the three employer-owned boards, but no
code consumes it. The Oliver Wyman role is independently confirmed on
[Marsh McLennan's official careers site](https://careers.marsh.com/global/en/job/R_356561/Oliver-Wyman-Actuarial-Internship-Summer-2027), which serves a
server-rendered JobPosting JSON-LD block.

## Source strategy

Use employer-owned posting pages as the source of record. Society boards,
university lists, aggregators, and deadline articles may identify employers,
but never become a `sources` value on a role: a landed row must use the
employer's own application URL.

| Employer | Known source | Provider | Phase-D use |
|---|---|---|---|
| Marsh McLennan / Oliver Wyman | [Careers posting](https://careers.marsh.com/global/en/job/R_356561/Oliver-Wyman-Actuarial-Internship-Summer-2027) | Phenom JobPosting page | First adapter target |
| Genworth Financial | `gnw.wd1.myworkdayjobs.com/Genworth_Confidential` | Workday CXS | Enabled: POST `/wday/cxs/gnw/Genworth_Confidential/jobs` with `searchText: actuarial`; results must match Summer 2027 |
| The Hartford | `thehartford.wd5.myworkdayjobs.com/Careers_Restricted` | Workday | Reconnoitre its CXS endpoint before enabling |
| Travelers, Nationwide, Aon, WTW, Munich Re | First-party career site to be verified | Unknown | Add one at a time after source verification |

## Chosen mechanism

Build a manually invoked `scripts/fetch_companies.py` network shim that reads
only `sources/companies.yaml` entries for the requested category and emits
the existing fetch-report JSON contract into `scratch/fetch_reports/`.
`run_scrape_merge.py` remains the sole writer of `data/` and `README.md`.
Company-source state lives under a `company_sources` key in
`sources/scrape_state.yaml`; a failed source emits no report and does not
modify its entry.

The initial adapter set is deliberately small:

- `phenom_job_page` for a configured, server-rendered JobPosting page;
- `workday_cxs` for a documented CXS search endpoint; and
- `manual_discovery` for a first-party landing page that should be watched but
  cannot yet enumerate canonical job postings.

There is no `custom` escape hatch. An unsupported career site must report a
visible `unsupported_source` count, not silently scrape or manufacture roles.

An actuarial source entry must contain `company`, `provider`, `url`,
`source_entity`, `term`, `degree`, and `role_pattern`. Provider-specific
fields are allowed only when required to call its documented endpoint. The
pre-existing `ats`/`url` entries in other categories remain untouched until
they receive their own consumer.

Each enabled adapter must have a saved/mocked fixture proving pagination,
canonical employer-owned application links, 2027 filtering, explicit-only
closed signals, and safe failure (no report and no state advancement).
For direct postings, `term` is an explicit source-config value and `degree`
is a conservative configured eligibility list; neither is inferred from a
role title. Missing or malformed source data is allowed to reach the normal
drop tally, never filled with a guess.

## Data and validation rules

- A qualifying role contains `actuar` or a named actuarial-development
  program title, has a US location, and targets Summer 2027.
- Existing historical rows remain until a direct source verifies or explicitly
  closes them; a source's failure or absence never closes a role.
- Before a direct source can refresh a historical aggregator row whose link
  differs, make a separate, reviewed canonical-link migration. Match only a
  configured employer/requisition pair; never use a role/location triple to
  rewrite a link. The Oliver Wyman Workday `R_356561` row is the first such
  migration.
- New sources extend the existing per-source drop tally; invalid, non-US, or
  unmatched postings remain visible in the run summary.
- Each employer moves from discovery to a posting-producing adapter in its own
  reviewable implementation slice.

## Implementation sequence after approval

1. Add the source contract and collector plumbing, with tests that it writes
   fetch reports only.
2. Implement and test the Marsh/Oliver Wyman JobPosting-page adapter.
3. Reconnoitre and then implement Genworth or The Hartford's Workday CXS
   adapter, whichever exposes a stable documented result endpoint.
4. Add further actuarial employers one at a time once their first-party source
   and test fixture are confirmed.

Each slice must pass the full suite and `python3 scripts/check_integrity.py`.
