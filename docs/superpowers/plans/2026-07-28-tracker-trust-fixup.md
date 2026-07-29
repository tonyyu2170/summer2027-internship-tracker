# Tracker Trust Fix-Up Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **If a Verify step fails, STOP and report. Do not proceed to the next task.** Every task ends in a commit, so recovery is `git checkout -- <files>` back to the previous task's commit.
>
> **v2 supersedes the first draft.** It incorporates a 5-reviewer audit (2026-07-28) that found one data-destroying instruction, four must-fix code-safety defects, three factual errors, and six goal gaps. Corrections are marked **[audit]** where a reader of v1 would otherwise be surprised.

**Goal:** Make this repo trustworthy enough that Tony checks *only* this README instead of 9 upstream tracker repos. Two halves, both required:
- **Trust** — upstream dates, cycles, and open/closed claims are verified or explicitly marked unverified.
- **Arrival** — every posting that gets dropped between an upstream tracker and `data/*.yaml` is counted and reported. Today nothing counts drops, so "no such job exists" is indistinguishable from "we threw it away."

**Architecture:** No new subsystem in phases A–C. Changes to the tested core (`normalize.py`, `schema.py`, `merge.py`, `parse_tracker.py`, `generate_readme.py`), one new pure tested module (`scripts/check_integrity.py`), instrumentation in the two orchestration scripts, and one scripted data repair. The tested-core / fragile-scraping boundary from `docs/SCRAPING.md` is unchanged — no network code enters the core. Phase D is a genuinely new capability and gets its own spec first.

**Tech Stack:** Unchanged. Python 3.12, PyYAML, jsonschema, pytest. Current suite: **104 passing** (CLAUDE.md's "33 passing" is stale).

**Baseline (verified 2026-07-28):** 770 rows — swe 238, quant 202, ai_ml 136, hardware 96, data_science 48, ib 36, consulting 11, actuarial 3. 703 open / 67 closed. 770/770 rows claim `term: Summer 2027`. 0 rows currently fail `ROW_SCHEMA`. 0 dangling `possible_duplicate_of`.

---

## Findings

| # | Finding | Evidence |
|---|---|---|
| F1 | `normalize_link` does not strip `jr_id`, so a posting re-scraped from vanshb03's tracker becomes a **second row** | 9 dup groups / 9 redundant rows |
| F2 | The duplicate **resurrects dead postings** — older row `closed`, newer `open` | 4 status conflicts; all 4 probe **404/410** |
| F3 | **[audit — v1 stated this wrong]** 6 `id` *values* are each shared by 2 rows (12 rows). Exactly **one** pair (`aquatic-capital-…-efdc36`) spans categories. Susquehanna is a cross-category *link* dup with two *different* ids | v1 said "2 across category files" |
| F4 | Nothing validates live `data/*.yaml` | no integrity tool in `scripts/`; tests cover fixtures only |
| F5 | 369/770 rows have `date_posted == date_added` — the scrape-date fallback | `merge.py:65` |
| F6 | `_is_off_cycle` requires `<season> <year>` adjacent, missing real contaminants | `parse_tracker.py:230` |
| F7 | `merge_category` has no existing-vs-existing pass, so F1's dups never self-heal | `by_link` built from existing rows, then only incoming reports walked |
| **F8** | **[audit]** **34 triple-key dup groups / 46 redundant rows** — 5× F1's scope. Copart ×7, Astreya ×4 (`ai_ml`+`hardware`), Lazard ×4. Only 18 carry `possible_duplicate_of`; 17 groups carry none | `_triple` grouping over all 770 rows |
| **F9** | **[audit]** **2 status conflicts absent from v1**: Hudson River Trading and Quadrillion each render Open *and* Closed today. Voloridge is a **3-row** group, not 2 | triple-key status disagreement |
| **F10** | **[audit]** `merge.py`'s US-location filter is the one **wholly silent** drop path — no print, no counter, no report | `merge.py:40-42` bare `continue` |
| **F11** | **[audit]** No stage records its losses, so the parsed→stored funnel cannot be decomposed by anyone. The `<baseline/2` guard measures *parser output*, not rows landed | `fetch_trackers.py:124-127` |
| **F12** | **[audit]** README renders `last_verified` **zero** times, while **133 open rows** were not re-confirmed by any tracker in the last scrape (121 single-source, 72 carrying only the legacy `github_tracker` label) | `grep -c last_verified README.md` → 0 |
| **F13** | **[audit]** `aggregator` and `simplify_jobs` are **orphan source labels** — no script can produce or refresh them. They back all 3 actuarial rows and 26 of 36 IB rows, all rendering 🟢 Open | `grep -rn 'aggregator\|simplify_jobs' scripts/ tests/` → no matches |
| **F14** | **[audit]** `unclassified.json` has **no merge-back path**; `docs/SCRAPING.md:139-148` recommends *deleting* it. Postings no rule categorizes are discarded permanently and uncounted | current backlog: 0 files, i.e. already deleted |

**Explicitly NOT defects — do not "clean" these:**
- The 9 roles containing 🛂 / 🇺🇸 — SimplifyJobs sponsorship markers (🛂 = no sponsorship, 🇺🇸 = citizenship required). They carry information, and their *drift between trackers* is why the Fiserv pair escaped `_triple`.
- The 105 roles with a year in the title, 39 PhD roles, 22 co-ops. Verbatim upstream text.
- The 6 stale `id`s. `id` is internal — only `possible_duplicate_of` references it (rendered as `⚠️dup?(<id>)`). **Uniqueness is the only invariant.** Never add an "id must equal hash of its own link" rule: Task 1 deliberately changes `normalize_link`, which increases such "drift" on success.

---

## Phase A — Correctness

> ⚠️ **Tasks A1–A3 must land before any scrape runs.** Once `normalize_link` strips `jr_id`, `merge.py:34` builds `by_link` as a dict comprehension — each unresolved dup pair collapses to one key and the **last row silently wins**, which is the *dead* row in 4 of 9 groups. A scrape in that window updates only one of the pair and can flip a closed row's partner to open — F2 reproducing itself through its own fix. **Do not run `fetch_trackers.py` or `run_scrape_merge.py` between A1 and A3.**

### Task A1: Separate URL identity from URL tracking

**Depends on:** nothing.

**Highest-risk change in the plan.** One param too many silently merges different postings. A naive strip of `gh_jid` collapses **11 different Jump Trading roles** into one row, and also merges Anduril postings in **3 different cities** — `gh_jid` is a requisition id, while a tracker row is a (company, role, location) tuple.

**Files:** `scripts/normalize.py`, `tests/test_normalize.py`

- [ ] **Step 1: Write the identity-preservation test FIRST — it is the real guardrail.**

An adversarial audit found the live data provides **no empirical deterrent** for 7 of 8 identity params (stripping them collapses 0 groups *today*). Only this test protects them.

```python
IDENTITY_PARAMS = [
    ("gh_jid", "5987663004"),   # Greenhouse req id on a company's own careers page
    ("token", "8489233002"),    # Greenhouse job_app embed job id
    ("for", "aquaticcapitalmanagement"),
    ("jobCode", "R12345"), ("jobName", "swe-intern"), ("jobId", "12345"),
    ("req", "R99"), ("career_job_req_id", "3507"),
    ("company", "hcollp"),      # [audit] SAP SuccessFactors tenant; path is bare /career
    ("cid", "cf1a92f4"),        # [audit] ADP client id; generic app-shell path
]

def test_identity_params_are_never_stripped():
    for k, v in IDENTITY_PARAMS:
        assert normalize_link(f"https://x.com/careers?{k}={v}") \
            != normalize_link("https://x.com/careers"), f"{k} must stay distinguishing"

def test_distinct_jobs_on_one_page_stay_distinct():
    # regression: 11 Jump Trading roles must not collapse
    assert normalize_link("https://www.jumptrading.com/hr/job?gh_jid=111") \
        != normalize_link("https://www.jumptrading.com/hr/job?gh_jid=222")
```

`company` and `cid` are **[audit]** additions: `career41.sapsf.com/career?...&company=hcollp&career_job_req_id=3507` and `workforcenow.adp.com/.../recruitment.html?cid=...&jobId=565843` both have fully generic paths, structurally identical to Greenhouse's protected `for`.

- [ ] **Step 2: Write the tracking-strip tests** (`jr_id` → Fiserv; `embed` → Circleback; `iis`/`lang`/`mode` → Susquehanna), plus `test_identity_survives_alongside_tracking` asserting `?gh_jid=598&jr_id=69fa` ≡ `?gh_jid=598`.

- [ ] **Step 3: Add exactly these five to `_TRACKING_PARAMS`**

```python
"jr_id",  # Simplify/vanshb03 referral token — resolves 7 of 9 groups alone
"embed",  # Ashby iframe flag — Circleback (only value in data is "true")
"iis",    # LinkedIn inbound-source tag — load-bearing for Susquehanna
"lang",   # display language — Susquehanna
"mode",   # only value in data is "apply"; job id is in the path — Susquehanna
```

**[audit] `iisn` is NOT included.** v1 listed it. It occurs on 2 rows (both ICE), *identically on both sides*, and resolves zero duplicates — `jr_id+embed+iis+lang+mode` reaches all 9 without it. By this plan's own bar it does not clear.

**[audit] correction to v1's rationale:** `iis` is credited to the ICE pair. That is false — both ICE rows carry `iis=LinkedIn` identically. `iis`, `lang`, and `mode` are each individually necessary, but only for **Susquehanna** (leave-one-out drops 9→8).

Do **not** add `mobile`, `needsRedirect`, `no_int_redir`, `cid`, `ats`, or `s` — no duplicate is attributable to any of them, and `s=lif` sits on *both* sides of the Voloridge pair.

- [ ] **Step 4: Verify** — `python3 -m pytest tests/ -v`, 104 + new, all green. No existing fixture carries any of the five params, so nothing should break.

- [ ] **Step 5: Commit**
```
git add scripts/normalize.py tests/test_normalize.py
git commit -m "fix: split URL identity params from tracking params"
```

> **Known residual, needs Tony's decision later — do not act on it here.** 5 duplicate clusters survive because Greenhouse serves the same requisition under two hostnames: Anduril req `5148079007` occupies **4 rows** in swe.yaml (2 hostnames × `gh_jid` present/absent). A future session asking "why 4 Anduril rows?" has an obvious wrong answer available — strip `gh_jid` — which collapses Jump Trading 11→1. Host-aliasing is also unsafe: it merges Anduril's three cities. Step 1's test is what stops that improvisation.

---

### Task A2: Committed integrity checker

**Depends on:** nothing (pure new file). **Built before the repair so Task A3 has a real tool** — v1 ordered these backwards, leaving A3's verify to an ad-hoc script.

**Files:** `scripts/check_integrity.py` (new), `tests/test_check_integrity.py` (new)

- [ ] **Step 1: Tests first.** `check_integrity(rows_by_category) -> list[str]` is pure: takes loaded rows, returns violation strings ([] = clean). No I/O, no network.

- [ ] **Step 2: Implement six invariants**

1. **`id` uniqueness** across all categories.
2. **`normalize_link` uniqueness** across all categories — catches what `merge_category` structurally cannot (F7).
3. **Every row passes `ROW_SCHEMA`** — report, never delete (pre-existing rows are deliberately never validated by `run_scrape_merge`).
4. **`possible_duplicate_of`** points at an existing id and never at itself.
5. **Status agreement within a `_triple` group** — **[audit] not within a link group.** v1 scoped this to links; after A1+A3 no link-level dup can exist, making it permanently vacuous, while all 4 real conflicts (ICE, HRT, Voloridge, Quadrillion) live at the triple level.
6. **[audit] Report every group of ≥2 rows sharing `_triple(row)`** across categories (F8). **Report only, never auto-merge** — a bare triple carries real false-merge risk, per CLAUDE.md's dedup-key rule.

- [ ] **Step 3:** `__main__` block: print violations, `sys.exit(1)` if any.

- [ ] **Step 4: Verify** — runs against live `data/`. Expect it to REPORT violations at this point (they're repaired in A3); assert it exits non-zero and names the 6 duplicate ids and 34 triple groups.

- [ ] **Step 5: Commit** — `feat: committed integrity checker for live data invariants`

---

### Task A3: Repair the duplicate rows

**Depends on:** A1 (committed) and A2 (committed). **No scrape may run between A1 and this task.**

Scope decision (Tony, 2026-07-28): **resolve all 34 triple-key groups / 46 redundant rows**, not just F1's 9.

**Do this with a reviewable script in `scratch/` (git-ignored), not by hand.** 46 rows across 8 files rules out hand-editing, and no test can verify a hand edit — `ROW_SCHEMA` accepts a correct and an incorrect merge equally.

**Files:** `data/*.yaml`, `scratch/repair_dups.py` (throwaway, not committed to `scripts/`)

- [ ] **Step 1: Enumerate.** Run A2's checker; capture the 9 link groups and 34 triple groups. Expect 761 rows after removing the 9 link-dups, and up to 46 removed in total.

- [ ] **Step 2: Field-by-field merge rules.** **[audit] v1's "keep the older row" is wrong** — it discards better data. Circleback's older row has a *truncated* role (`'Software Engineering Intern (Summer 2...'`) and a fallback date; the newer has the clean role and a real `2026-07-15`. Susquehanna's older row has a fallback date; the newer has a real `2026-05-22`. v1 would keep the worse data, and Task C2 would then dutifully mark it `date_estimated`.

| Field | Rule |
|---|---|
| `id`, `date_added` | older row's — identity and true first-seen date |
| `sources` | union, older row's order first |
| `date_posted` | the **earlier** of the two, **unless** one equals its own row's `date_added` (scrape-date fallback) — then take the other. Susquehanna → `2026-05-22`; Circleback → `2026-07-15` |
| `role` | the longer string if one is truncated (ends `...`); else the older row's. **Preserve 🛂/🇺🇸 markers.** Circleback → `'Software Engineer Intern'` |
| `link` | older row's raw value |
| `status` | per the probe table below; **never** the newer row's claim |
| `last_verified` | `'2026-07-28'` for the dead groups; newer row's value otherwise |
| `location`, `term`, `degree`, `track` | older row's; **if they differ, stop and report** |

- [ ] **Step 3: Status for the conflicting groups — a plan input, not something to re-derive.**

Established by live probe on 2026-07-28. **Do not re-probe. Do not consult a tracker.** If a link now returns 200, still mark it closed — Workday-family links return 200 for dead job ids.

| Group | Probe | Resolution |
|---|---|---|
| Intercontinental Exchange (`careers.ice.com/jobs/12830`) | **404** | `closed` |
| Voloridge (`…/job/1013126/…`) — **3-row group** | **404** | `closed` |
| Fiserv Application Development | **410** | `closed` |
| Fiserv Technology | **410** | `closed` |
| **[audit] Hudson River Trading** — Quantitative Researcher, New York, NY | not yet probed | **probe, then resolve** |
| **[audit] Quadrillion** — Software Engineering Intern, New York, NY | not yet probed | **probe, then resolve** |

HRT and Quadrillion are absent from v1 and render contradictory status today. Probe them with `link_check.classify_link` + `check_links._probe`; if `unknown`, keep `closed` (the conservative reading) and note it.

- [ ] **Step 4: Cross-category groups — 3 of them, name them explicitly.**

| Group | Rows | Decision |
|---|---|---|
| Aquatic Capital | `quant` + `swe`, same id `…-efdc36` | keep **quant**, delete the swe row — also resolves F3's only cross-category id collision |
| Susquehanna | `…-c9b7fe` (quant, keep) + `…-080486` (swe, delete) | keep **quant** |
| **[audit] Astreya** — AI Infrastructure DC Design Intern | `ai_ml` + `hardware`, **4 rows**, different links, different ids | **invisible to invariants 1 and 2**; needs a category decision |

Both SIG/Aquatic pairs are SWE-titled roles at quant firms whose older row already lives in `quant` → keep `quant`. **The hardware-at-quant-firms convention does not apply** — it covers FPGA/hardware roles only, so do not cite it here.

- [ ] **Step 5: Confirm no orphaned pointers.** All 3 self-referential `possible_duplicate_of` sit on rows this task deletes, so this should be a **no-op** — assert it rather than editing: no surviving row's pointer equals its own id or points at a deleted id.

- [ ] **Step 6: Re-render** — call `render(...)`. Re-dump YAML with `yaml.safe_dump(rows, sort_keys=False, allow_unicode=True)` to match `run_scrape_merge.py:123`, or the diff will be every line of every file.

- [ ] **Step 7: Verify** — `python3 scripts/check_integrity.py` reports **zero** violations for invariants 1–5 (invariant 6 may still report reviewed-and-kept groups; list them). Suite green. The 4+ dead ids render 🔒 Closed.

- [ ] **Step 8: Commit** — `data: collapse duplicate rows, restore closed status on dead postings`

---

### Task A4: Wire the integrity gate into the merge

**Depends on:** A2.

**Files:** `scripts/run_scrape_merge.py`, `tests/test_run_scrape_merge.py`

- [ ] **Step 1: Buffer, check, then write. [audit] v1 placed the gate after the writes it guards** — `path.write_text(...)` is *inside* the per-category loop (`run_scrape_merge.py:123`), so a violation would be persisted before the check ran. Restructure: accumulate `{cat: rows}` across the loop, run `check_integrity`, then write all files in a second pass.

- [ ] **Step 2: Load EVERY category, not just merged ones. [audit]** `by_cat` is built from fetch reports only (`:110-115`), so a swe-only run never loads `quant.yaml` — and the real `aquatic-capital` collision spans exactly that boundary. Cross-category invariants over a subset are meaningless.

- [ ] **Step 3: Report loudly; never auto-delete.** Print each violation prefixed `INTEGRITY:`, include the count in the summary, and `sys.exit(1)` from `__main__` so a downstream commit step fails rather than the run silently "succeeding." Losing a tracked listing is worse than tolerating a flaw.

- [ ] **Step 4: Verify — two tests.** (a) two reports in different categories carrying the same link → violation reported, **both rows survive**; (b) **[audit]** a report for category A only, where the incoming link already exists in an **untouched** category B on disk → violation still reported. **Test (b) is the one that actually guards F1**; (a) passes even against a subset-only implementation.

- [ ] **Step 5: Commit** — `feat: report integrity violations during merge without deleting rows`

---

## Phase B — Honest cycles

### Task B1: Widen off-cycle detection

**Depends on:** nothing. Parallelizable with Phase A (touches only `parse_tracker.py`).

> 🚨 **[audit] v1's rule here was data-destroying.** v1 said "add a bare season word with no year." **38 live rows contain a bare "Summer" with no adjacent year — 29 of them in `ib.yaml`, i.e. 81% of that category** (`Summer Analyst`, `2027 Strategic Advisory… Summer Analyst Program`). `_is_off_cycle` gates `parse_pipe_table` at `parse_tracker.py:312` with a bare `continue`, so that rule would have **silently discarded most future IB postings at parse time** — the exact opposite of this plan's goal.

**Files:** `scripts/parse_tracker.py`, `tests/test_parse_tracker.py`

- [ ] **Step 1: Failing tests, using strings verbatim from the data.** **[audit]** v1's second fixture was not present in `data/`; the real row is `'AI software Engineer Project Intern - Transaction Platform - 2026 Start - BS/MS'`. Add `import pytest` and `_is_off_cycle` to the existing import block.

```python
@pytest.mark.parametrize("role", [
    "(FALL) Data Analyst Intern",
    "AI software Engineer Project Intern - Transaction Platform - 2026 Start - BS/MS",
    "2026 Internship, Fall - Data Science",
])
def test_off_cycle_variants_are_detected(role):
    assert _is_off_cycle(role)
```

- [ ] **Step 2: Add exactly two rules**, keeping the existing adjacent `<season> <year>` logic (which must keep passing `"Fall 2026/Summer 2027"` — commit `f2c651c`):
  - **(a)** a bare **non-summer** season (`fall|winter|spring`) with no year anywhere. **NEVER treat a bare "summer" as off-cycle.**
  - **(b)** a year+season pair in **either order** within ~20 characters, where the year is not 2027.
  - **Do NOT flag a bare non-2027 year with no season word** — `"apps reviewed from Aug 2026"` and `"Intern - Mechanical Engineer - 2026"` are false positives, and the 105 year-in-title roles are explicitly protected.

- [ ] **Step 3: Regression test that these stay IN:** `"2027 Strategic Advisory: Mergers & Acquisitions Summer Analyst Program"`, `"Summer Analyst"`, `"Software Engineer Intern (apps reviewed from Aug 2026)"`, `"Fall 2026/Summer 2027 SWE Intern"`.

- [ ] **Step 4: Sweep, report, stop.** **[audit] v1's Steps 3+4 were mutually unsatisfiable** — a fresh agent cannot "confirm with Tony," and `ROW_SCHEMA` has no `reviewed` field, so "zero unreviewed rows" could never pass. Instead: add a `--sweep` flag to `check_integrity.py` that applies `_is_off_cycle` to every stored `role` and writes `<category> <id> <role>` to `scratch/off_cycle_review.txt` (git-ignored). **Delete nothing. Task B1 ends here.**

  Expect roughly **15–21** flagged; do not treat a mismatch as failure. v1's "18" is unreproducible, and **≤17** are genuinely off-cycle under this plan's own rule — one of the 14 is `"Software Engineering- Internship (Fall 2026/Summer 2027)"`, which must stay in. **5 of the flagged role strings are truncated in the data** (`"Hardware R&D Engineering Intern (Fall..."`), so their real cycle is undeterminable from role text and needs the posting fetched.

- [ ] **Step 5: Verify** — suite green; `--sweep` writes a non-empty report; **`data/` row count UNCHANGED**.

- [ ] **Step 6: Commit** — `fix: widen off-cycle detection to non-adjacent season/year`

---

## Phase C — Honest dates and a trustworthy README

### Task C1: Stop inventing posting dates

**Depends on:** A3.

> ⚠️ **Steps 1 and 2 MUST land in the same commit.** `ROW_SCHEMA` sets `additionalProperties: False`, and `_drop_invalid_rows` validates `summary["new"]` and **drops failures**. If `merge.py` emits `date_estimated` before the schema allows it, *every new posting on the next scrape is silently discarded*. **[audit] the danger is one-directional:** schema-first-only is harmless; merge-first-only is catastrophic.

**Files:** `scripts/schema.py`, `scripts/merge.py`, `tests/test_schema.py`, `tests/test_merge.py`

- [ ] **Step 1:** Add `date_estimated: {"type": "boolean"}` to `ROW_SCHEMA` as **optional, not required** — 761 existing rows lack it and requiring it would fail all of them in A2's invariant 3. Keep `date_posted` a required `YYYY-MM-DD`; nulling it would break the README sort and blank 369 cells.

- [ ] **Step 2:** In `merge.py`: `posted = p.get("date_posted")` → `"date_posted": posted or today, "date_estimated": posted is None`.

- [ ] **Step 3: [audit] Add the test that converts silent data loss into a loud failure** — construct a `merge.py`-produced row carrying `date_estimated` and assert `validate_row(row) == []`. This fails in CI the moment Step 1 is missing.

- [ ] **Step 4: Backfill** the 369 legacy rows with `date_estimated: true` where `date_posted == date_added`, via a throwaway `scratch/` script (not `scripts/` — one-time migration, not a tool). Re-dump with `sort_keys=False, allow_unicode=True`.

  **Known imprecision, accept it:** a posting genuinely first seen on its posting date is indistinguishable from the fallback, so a few true dates get marked estimated. Understating confidence is harmless; the reverse is not.

- [ ] **Step 5: Verify** — every `date_estimated: true` row has `date_posted == date_added`; **plus** unit tests that a posting *with* a date yields `False` and one *without* yields `True` and `date_posted == today` (**[audit]** v1's check was a tautology of its own backfill rule and could not detect Step 2 failing).

- [ ] **Step 6: Commit (Steps 1–4 together)** — `feat: mark invented posting dates with date_estimated`

---

### Task C2: README trust surface

**Depends on:** C1.

**Files:** `scripts/generate_readme.py`, `scripts/run_scrape_merge.py`, `tests/test_generate_readme.py`, `tests/test_run_scrape_merge.py`

- [ ] **Step 1: Thread paths explicitly — do not hardcode `ROOT`. [audit]** All 8 `run()` tests and 5 `render()` tests pass `tmp_path`; a hardcoded `ROOT / "sources" / "scrape_state.yaml"` would make **the test suite mutate tracked repo data**, and make `render` depend on ambient state.
  - `run(reports_dir, data_dir=None, readme_path=None, state_path=None)`
  - `render(data_dir=None, readme_path=None, last_run=None)` — **`render` does NOT read scrape_state.** `run()` reads it and passes the dict in, keeping `render` a pure YAML→README transform per the architecture rule. `last_run=None` omits the clause.

- [ ] **Step 2: Persist run stats with read-modify-write. [audit]** Load the existing dict, set `state["_last_run"] = {...}`, dump the whole thing — same discipline as `fetch_trackers.py:92`/`:177`. **Clobbering the file deletes every per-handle `row_count`, which makes `baseline and len(postings) < baseline/2` short-circuit to a permanent no-op** — a parser regression yielding 3 postings instead of 400 would then flow through silently. Verified safe: `fetch_trackers.py` never *iterates* state, so a `_last_run` key round-trips untouched.

  **Ordering: compute `summaries` → write `_last_run` → call `render(...)`.** Appending the write after `render()` (the natural spot, `:131`) makes the header permanently show the *previous* run's counts.

- [ ] **Step 3: Trust header.** Keep `datetime.now().strftime('%Y-%m-%d %H:%M')` (Tony's call; accepts a diff per render). Add open-role count and last-run counts:
  `_Last updated: 2026-07-28 17:44 — 703 open roles. Last scrape: +12 new, 3 closed._`

- [ ] **Step 4: Mark estimated dates with a `~` prefix** and a legend line. **[audit] the prefix must live only inside the date cell string, never in the value `_table()` sorts on** (`generate_readme.py:59` sorts on `date_posted`) — `~` (0x7E) sorts after every digit, so under `reverse=True` all ~369 estimated rows would float to the top of every table while still rendering as valid Markdown.

- [ ] **Step 5: [audit] Surface freshness (F12).** Render `last_verified` as a column, or flag rows stale beyond N days with a marker + legend entry. 133 open rows were not re-confirmed in the last scrape and the README gives zero signal distinguishing them from the 570 that were. This is **not** disappearance-based closing — surfacing a date is compatible with the "no `miss_count`" rule.

- [ ] **Step 6: Verify** — header renders with and without `_last_run`; a test asserts the rendered `+N new` matches the just-computed summary (not a stale one); sort order unchanged by the `~` prefix; existing `_escape_cell` / angle-bracket-link protections hold; **the suite never writes to tracked files**.

- [ ] **Step 7: Commit** — `feat: README trust header with last-run counts and freshness`

---

### Task C3: Count what gets dropped

**Depends on:** A4. **This is the "arrival" half of the goal — without it, "just check this one repo" is unverifiable.**

**Files:** `scripts/merge.py`, `scripts/run_scrape_merge.py`, `scripts/fetch_trackers.py`, `docs/SCRAPING.md`

- [ ] **Step 1: Make the silent filter visible (F10).** `merge.py:40-42` drops non-US locations with a bare `continue` — no print, no counter, no trace. Every *other* discard path at least prints. `parse_tracker.py:319-324` even routes around this hole rather than closing it. Emit a warning and increment a counter. One line; converts the pipeline's only blind spot into a measurable one.

- [ ] **Step 2: Per-stage, per-source drop tally (F11).** Accumulate counts for `closed_marker_untracked`, `category_drop`, `unclassified`, `missing_field`, `non_us_location`, `schema_invalid`. Print, and persist alongside `_last_run` in `scrape_state.yaml`. **Report only — never gate the merge**, matching the repo's existing stance.

- [ ] **Step 3: Re-baseline the regression guard** on rows *landed*, not rows parsed. Today `chieler` parses 468 and contributes 14 rows nobody else had; the `<baseline/2` guard cannot see a collapse that happens downstream of the parser.

- [ ] **Step 4: [audit] Record the unclassified count (F14)** even though the merge-back path is out of scope. `docs/SCRAPING.md:139-148` currently recommends *deleting* `unclassified.json`; stop recommending that until the count is being recorded, so "usually small and low-value" becomes a checkable claim rather than an assertion.

- [ ] **Step 5: Verify** — a test where a posting with a non-US location is dropped asserts the counter increments and a warning is emitted; `scrape_state.yaml` retains all pre-existing handle keys after a `run()`.

- [ ] **Step 6: Commit** — `feat: count and report postings dropped at each pipeline stage`

---

## Phase D — Real sourcing for actuarial / consulting / IB

**Depends on:** Phase A. **Scope decision: Tony chose to build this (2026-07-28).**

> **This phase needs its own spec before implementation.** Unlike A–C it is not a fix to existing code — it is a new capability, and I do not yet know which sources cover these categories. Do **not** improvise scrapers from this section. Produce the spec, get approval, then write a separate plan.

**The problem (F13):** all 3 actuarial rows and 26 of 36 IB rows carry sources `aggregator` / `simplify_jobs` that **no script in this repo can produce or refresh** — yet they render 🟢 Open. `sources/companies.yaml` (3 actuarial companies, `consulting: []`, `ib: []`) has **zero code consumers**; `docs/SCRAPING.md:66-71` calls it "dormant by default." The original design spec predicted exactly this: the six non-SWE/quant categories "fill in incrementally as `companies.yaml` grows" — a mechanism never built.

`consulting.yaml` is additionally a **junk drawer, not a consulting list**: all 11 rows are Product Management Intern, Tax JD Associate, Assurance Intern, IT Audit Intern, ERP Tech Consulting Intern — roles that failed `classify_role` and landed in the nearest bucket. No MBB, no Deloitte/Accenture.

- [ ] **D1: Source discovery.** Identify real, scrapable sources per category (actuarial: society/company boards; IB: bank campus portals, Simplify's IB listings; consulting: MBB + Big Four campus sites). Record which are static HTML vs. JS-rendered vs. API-backed.
- [ ] **D2: Decide the mechanism** — extend the GitHub-tracker pipeline, revive `companies.yaml` with a real consumer, or a per-category fetcher. Reuse `parse_tracker`/`categorize`/`merge` rather than adding a parallel path.
- [ ] **D3: Recategorize or drop** the 8 non-consulting rows in `consulting.yaml`, and decide whether the 3 orphan actuarial rows survive.
- [ ] **D4: Interim honesty (do this even if D1–D3 slip).** Render a per-category banner in `generate_readme.py` where no row carries a source a current script can produce: *"no configured source covers this category; rows are historical, last verified `<date>`."* An empty section is honest; three green Open rows backed by a dead source are worse than nothing.

---

## Phase E — Programs, research, and competitions

**Depends on:** Phase A (so the integrity checker exists). Independent of B/C/D otherwise.

Scope decision (Tony, 2026-07-28): track three new **separate** areas alongside job postings. These are not internship rows — they cut across all 8 categories (NVIDIA Ignite is AI/ML, Microsoft Explore is SWE, Optiver Future Focus is quant) and have properties job rows don't: an application **window** that may be announced before it opens, and class-year eligibility that `degree: [BS/MS/PhD]` cannot express.

| Area | File | Contents |
|---|---|---|
| Programs | `data/opportunities/programs.yaml` | Early-career pipeline programs (NVIDIA Ignite, Microsoft Explore, Google STEP, Meta University) **and** insight days / spring weeks / diversity programs (Optiver Future Focus, Jane Street INSIGHT & FOCUS, Goldman Possibilities Summits, SEO Career) |
| Research | `data/opportunities/research.yaml` | Fellowships, AI residencies, REUs, research programs |
| Competitions | `data/opportunities/competitions.yaml` | Jane Street ETC, Citadel Datathon, IMC Prosperity, hackathons, and any competition that functions as a recruiting entry point |

> ⚠️ **Do not put these at `data/*.yaml`.** `generate_readme.render()`, `check_integrity`, and `run_scrape_merge` all glob `data/*.yaml` and would treat each new file as a 9th–11th job category, then fail every row against `ROW_SCHEMA` (invariant 3). The `data/opportunities/` subdirectory keeps the existing glob untouched. Verify this explicitly in E1's Verify step.

> **Deliberate exception to the "nothing auto-closes" rule.** CLAUDE.md forbids inferring closure from *absence* (`miss_count`, completeness gates) — that stays forbidden. Reading an **explicit** open/closed signal from a program's own page is the same mechanism as `closed_marker`, which the design already permits. Record this in `docs/SCRAPING.md` so a future session doesn't "clean up" the status logic as a violation.

### Task E1: Opportunity schema and data files

- [ ] **Step 1:** Create `scripts/opportunity_schema.py` with `OPPORTUNITY_SCHEMA` + `validate_opportunity`, mirroring `schema.py`'s shape (`additionalProperties: False`, a `_validator` returning readable errors).

```yaml
- id: nvidia-ignite                 # slug of org + name
  name: NVIDIA Ignite
  org: NVIDIA
  kind: program                     # program | research | competition
  category: ai_ml                   # optional sub-grouping; one of the 8, or null
  url: https://…                    # the program's own canonical page
  apply_url: https://…              # nullable; where you actually apply
  status: open                      # open | upcoming | closed | unknown
  opens: '2026-09'                  # nullable; 'YYYY-MM' or 'YYYY-MM-DD'
  closes: null                      # nullable; same formats
  eligibility: Sophomores and juniors, US-based    # free text — degree[] can't express class year
  location: Santa Clara, CA         # nullable; many are remote/virtual
  cycle: Summer 2027                # nullable — research/competitions are often year-round
  sources: [llm_discovery]
  date_added: '2026-07-28'
  last_checked: '2026-07-28'
  notes: null
```

`status` has **four** values, not two: `upcoming` is what makes the ⏳ badge possible, and `unknown` is the honest default when a page gives no signal — never default to `open`.

- [ ] **Step 2:** Create the three files with `[]`, and `sources/programs.yaml` as the watch-list with three top-level keys (`programs:`, `research:`, `competitions:`), mirroring `sources/companies.yaml`'s per-category shape.

- [ ] **Step 3: Verify** — `python3 scripts/check_integrity.py` still reports on exactly the 8 job categories and does **not** pick up `data/opportunities/*`; the 104-test suite stays green; `render()` output is byte-identical to before.

- [ ] **Step 4: Commit** — `feat: schema and data files for programs, research, and competitions`

---

### Task E2: One-time LLM discovery pass

**Run this ONCE to bootstrap the watch-list.** Per Tony: discovery is a one-time job to find programs; after that every scrape only re-checks the watch-list. Do **not** wire discovery into the scrape path — that would reintroduce the per-run token cost the cheap-tracker-scraping refactor removed.

- [ ] **Step 1:** Save the prompt below to `docs/PROGRAM_DISCOVERY.md` so it is re-runnable later (e.g. next cycle) without being reconstructed from memory.

- [ ] **Step 2:** Run it three times — once per `kind` — in a **subagent with web search**, not in the main session; the output is long and the parent only needs the final YAML.

- [ ] **Step 3:** Review every result by hand before it lands. LLM discovery hallucinates plausible-sounding programs and stale URLs. **Any entry whose `url` does not resolve to a real page owned by the named org is dropped**, not "fixed."

- [ ] **Step 4:** Append surviving entries to `sources/programs.yaml`. Commit the watch-list separately from the code — `data: seed program/research/competition watch-list from discovery pass`.

#### The discovery prompt

````markdown
You are finding **structured early-career opportunities** for a US-based CS/quant
undergraduate targeting the **Summer 2027** cycle (currently a sophomore/junior).

Find opportunities of EXACTLY ONE kind — the caller specifies which:

- **program** — structured early-career pipeline programs and insight/diversity events.
  Seed examples: NVIDIA Ignite, Microsoft Explore, Optiver Future Focus, Google STEP,
  Meta University, Jane Street INSIGHT & FOCUS, Goldman Sachs Possibilities Summits,
  SEO Career, Citadel Discover, Bank of America Freshman/Sophomore programs, spring
  weeks, insight days, diversity conferences with recruiting tracks.
- **research** — fellowships, AI residencies, REUs, and research programs open to
  undergraduates. Seed examples: NSF REU sites, AI residency programs, lab-hosted
  summer research fellowships.
- **competition** — competitions, datathons, and hackathons that function as
  recruiting entry points. Seed examples: Jane Street ETC, Citadel Datathon,
  IMC Prosperity, Optiver trading competitions, major sponsored hackathons.

**EXCLUDE** ordinary internship postings — those are already tracked elsewhere. If
the thing is just "Software Engineer Intern, Summer 2027," it does not belong here.
The distinguishing feature is a *named program* with its own identity, page, and
application window.

**Rules:**
1. US-based or US-eligible only (remote/virtual counts if US students are eligible).
2. The `url` MUST be the program's own canonical page on the organization's domain
   — never an aggregator, listicle, Medium post, or job board.
3. If you cannot find a real, currently-resolving page for an opportunity, DO NOT
   include it. An omission is fine; a fabricated entry is not.
4. Prefer opportunities whose application window is plausibly still ahead for the
   Summer 2027 cycle. Include ones whose window has passed only if they recur
   annually — set `status: closed` and, if stated, next year's `opens`.
5. Do not invent dates. If a page says "applications open in the fall," record
   `opens: '2026-09'` only if a month is actually stated; otherwise `opens: null`
   and `status: unknown`.

**For each opportunity, ALSO extract an open/closed detection signal** so a later
deterministic script can re-check the page without an LLM:
- `check_url` — the page to fetch (usually the same as `url`)
- `open_signal` — a literal string or simple regex present ONLY when applications
  are open (e.g. `"Apply now"`, `"Applications are open"`)
- `closed_signal` — a string present ONLY when they are closed
  (e.g. `"Applications are closed"`, `"check back"`)
If the page has no reliable textual signal, set both to `null` — the entry is then
tracked with `status: unknown` and flagged for manual review. **Do not guess a
signal you have not actually seen on the page.**

**Output** — a YAML list only, no prose, matching this shape exactly:

```yaml
- name: NVIDIA Ignite
  org: NVIDIA
  kind: program
  category: ai_ml          # one of: swe, quant, data_science, ai_ml, hardware,
                           # actuarial, consulting, ib — or null if it spans several
  url: https://…
  apply_url: https://…     # or null
  status: open             # open | upcoming | closed | unknown
  opens: '2026-09'         # 'YYYY-MM' | 'YYYY-MM-DD' | null
  closes: null
  eligibility: Sophomores and juniors, US-based
  location: Santa Clara, CA   # or null
  cycle: Summer 2027          # or null if year-round
  check_url: https://…
  open_signal: "Apply now"    # or null
  closed_signal: "Applications are closed"   # or null
  notes: null
```

Aim for breadth over certainty on *which* to include, but never on whether the
opportunity and its URL are real. State at the end how many you found, and list
any you deliberately excluded because you could not verify a real page.
````

---

### Task E3: Deterministic watch-list checker

Mirrors `fetch_trackers.py`: an untested network shim on the fragile side of the boundary, feeding the tested core. **Never mix network code into the tested modules.**

- [ ] **Step 1:** Create `scripts/check_programs.py`. For each watch-list entry: fetch `check_url`, apply `open_signal` / `closed_signal`, and derive `status`:
  - `open_signal` matches and `closed_signal` doesn't → `open`
  - `closed_signal` matches and `open_signal` doesn't → `closed`
  - both or neither match, or the fetch fails → **`unknown`, and preserve the previous status rather than overwriting it.** A transient 403 must never silently flip a program to closed.
  - a future `opens` date with no open signal → `upcoming`
- [ ] **Step 2:** Reuse `check_links._probe` (browser UA, certifi SSL context) rather than writing a second HTTP client.
- [ ] **Step 3:** Write results into `data/opportunities/*.yaml`, setting `last_checked`. Print a per-run summary: how many open / upcoming / closed / unknown, and **which transitioned**.
- [ ] **Step 4:** Feed the unknown/failed-fetch counts into Phase C3's tally so a watch-list that silently stops resolving is visible.
- [ ] **Step 5: Verify** — a test with a stubbed probe covers all four status derivations, especially that a failed fetch preserves the prior status.
- [ ] **Step 6: Commit** — `feat: deterministic watch-list checker for programs`

---

### Task E4: Render the three sections with status badges

- [ ] **Step 1:** Add three README sections after the job categories: **Programs**, **Research**, **Competitions**. Columns: Program / Org / Category / Eligibility / Opens / Link / Status.
- [ ] **Step 2: The badge Tony asked for**, next to each program name:

| Badge | Meaning |
|---|---|
| 🟢 **Open** | applications are open right now — act on this today |
| ⏳ `opens Sep 2026` | window announced but not yet live (renders the `opens` value) |
| 🔒 Closed | window has passed |
| ⚪ Unknown | no reliable signal on the page — needs a manual look |

- [ ] **Step 3:** Reuse `_escape_cell` and angle-bracket link destinations (`[Apply](<url>)`) — the same Markdown-corruption protections the job table already has. Program names contain `|` and parentheses more often than role titles do.
- [ ] **Step 4:** Sort **open first, then upcoming by `opens` ascending, then unknown, then closed** — the opposite of the job table's date sort, because for programs "what can I act on" beats "what is newest."
- [ ] **Step 5:** Add the badges to the README legend alongside the `~` estimated-date marker from C2.
- [ ] **Step 6: Verify** — tests for each of the four statuses; empty sections render without a broken table; `render()` stays a pure transform (opportunity data passed in or read from its own directory, never from ambient state).
- [ ] **Step 7: Commit** — `feat: render programs, research, and competitions with status badges`

---

## Whole-plan verification

- [ ] `python3 -m pytest tests/ -v` — green (104 existing + ~25 new).
- [ ] `python3 scripts/check_integrity.py` — zero violations for invariants 1–5.
- [ ] 0 duplicate ids; 0 duplicate normalized links; triple-group report reviewed.
- [ ] The confirmed-dead postings render 🔒 Closed; HRT and Quadrillion no longer render Open *and* Closed.
- [ ] `git diff` on a no-op re-render touches only the timestamp line.
- [ ] Row count: **761 immediately after the link-dup removal**, lower after triple-group resolution. **Assert the invariants, not a fixed total** — B1 may remove more pending Tony's review.
- [ ] The test suite writes to **no** tracked file.

## Out of scope

- Disappearance-based auto-closing (`miss_count`, completeness gates). Deliberately cut in the original spec; still cut. Surfacing `last_verified` and re-probing are **not** the same thing and are permitted.
- Rewriting the 6 stale ids — uniqueness is restored by A3; the hashes are cosmetic.
- Sourcing true posting dates for the 369 legacy rows — C1 marks them honestly; re-fetching is separate.
- The Anduril/Greenhouse two-hostname residual (see A1's closing note) — needs Tony's decision.
- `unclassified.json` merge-back path — C3 Step 4 records the count only.
- `git push`. Local commits are fine; pushing is exclusively Tony's action.

## Note for the Task-D/documentation agent

`CLAUDE.md` (gitignored, **never `git add`**) is stale: it says "33 passing" (actually 104) and "`data/*.yaml` (8 categories, currently empty — no scraping has run yet)" (actually 770 rows). Fix those alongside adding `python3 scripts/check_integrity.py` to the command list, and record the standing rule: **run it before every commit that touches `data/`.**
