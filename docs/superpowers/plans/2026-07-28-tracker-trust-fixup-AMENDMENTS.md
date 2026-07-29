# Amendments to the Tracker Trust Fix-Up plan (2026-07-28, during execution)

Findings from verifying the plan's claims against live `data/*.yaml` before
executing Task A3. **Read this before starting A3** — the plan's A3 scope
instruction, taken literally, destroys real job postings.

Phases A1 and A2 are complete and committed. A3 has NOT started; `data/` is untouched.

---

## The core defect: "resolve all 34 triple-key groups / 46 redundant rows" is wrong

A triple key is `(normalized_company, lowercased_role, canonical_location)`.
Grouping the live 770 rows by it yields 34 groups. Breaking those down by
whether the rows actually share a **normalized link** (the repo's primary
dedup key):

| Kind | Groups | Redundant rows |
|---|---|---|
| All rows share one normalized link → **true duplicate** | 5 | 5 |
| Every row has a **distinct** link → **distinct requisitions** | 28 | 39 |
| Mixed (Voloridge) | 1 | — |

**Merging all 34 groups would delete ~39 genuine, separately-applyable postings.**
That is the exact opposite of the plan's stated "Arrival" goal.

Verified examples of same-company/role/location rows that are genuinely different jobs:

- **Copart** ×7 — seven distinct Workday requisition IDs, Dallas TX.
- **Lazard** ×4 — distinct `opp/4154`, `opp/4117`, `opp/4126`… IDs.
- **Altom Transport** ×3 — Workable `/j/9FC654F05E/`, `/j/8536165C7B/`, `/j/1E3C4A9408/`.
- **PlusAI** ×3 — three distinct Lever UUIDs.
- **Evercore** ×2 — `opp/2912` and `opp/2911`.

This is precisely the false-merge risk that `CLAUDE.md`'s dedup rule already
warns about ("a bare triple carries real false-merge risk... never auto-merged").
The plan's Findings table (F8) counted the groups but did not check whether
their members share a link.

### Corrected A3 scope

1. **Merge the 9 duplicate-normalized-link groups.** These are real duplicates
   by the primary key. → 770 - 9 = **761 rows**.
2. **Merge only these locale/case URL variants** (same requisition served under
   two URL forms — verified by requisition ID, not inferred):
   - **Astreya** 4 rows → 2. These are 2 requisitions (`R0015746`, `R0015747`),
     each duplicated across `ai_ml` and `hardware` by a `/en-US/` path segment.
     Tony's decision (2026-07-28): **keep the two `hardware` rows**, delete the
     two `ai_ml` ones. Hardware rows are older (`date_added: 2026-07-23`) and
     carry real `date_posted` values where the ai_ml rows have scrape-date fallbacks.
   - **Copart** 7 rows → 6 (one pair differs only by `/en-US/`).
   - **InterDigital** 2 rows → 1 (differs by `/en-US/` + tenant-name case).
   → **-4 more rows ≈ 757 total.** Re-verify this count; do not hard-code it.
3. **Leave every other triple group intact** and report it as advisory.

---

## Groups the plan names that must NOT be merged

### Hudson River Trading and Quadrillion — not duplicates

The plan's A3 Step 3 table lists these as status conflicts to "probe, then resolve."
They are two distinct requisitions each:

| Group | Row A | Row B |
|---|---|---|
| Hudson River Trading | `gh_jid=7964062` (closed) | `gh_jid=8059837` (open) |
| Quadrillion | Ashby `601e105d-2f0f-4482-9bae-3a825a1b97fd` (closed) | Ashby `a4acc44c-31ce-41a0-ab44-2500487b4d05` (open) |

One open + one closed is honest data. **Do not probe them; do not merge them.**
A probe answers "is this URL alive," but the question is "is this one posting or
two" — and the links already answer it. Merging them would contradict Task A1,
which exists specifically to protect `gh_jid` as an identity param.

### Voloridge is two groups, not one 3-row merge

- `…-8db21e` — Greenhouse `job-boards.greenhouse.io/voloridgeinvestmentmanagement/jobs/4224862009`, **open**. A separate live requisition on a different ATS. **Leave it.**
- `…-9d8be0` ×2 — both `voloridge-investment-management.hiringthing.com/job/1013126/…`. Same normalized link → merge, resolve to **closed** per the plan's recorded 404 probe.

A naive 3→1 merge destroys the live Greenhouse posting.

---

## Consequence: the integrity checker was rescoped (already implemented)

With HRT and Quadrillion correctly left alone, a **blocking** triple-scoped
status-agreement invariant would report them forever, so `check_integrity`
would exit 1 permanently and Task A4's merge gate would be born dead.

Implemented in commit `efb02ee`:

- Triple-scoped status agreement → **advisory** (`triple_status_disagreements()`).
  Rationale is invariant 6's rationale: a bare triple is not reliable identity.
- **New blocking invariant: status agreement within a normalized-LINK group.**
  Same link *is* the primary key, so disagreement there is a defect by definition.

The plan rejected link-scoping as "permanently vacuous." That reasoning is wrong:
vacuous-after-repair is what a healthy invariant looks like — it guards against
regressions in fresh scrape output, which is Task A4's actual job.

**This rescope caught 2 previously-invisible true defects:** both Fiserv pairs
share a normalized link and disagree on status, but their roles differ by a
trailing 🛂 marker, so their triples never matched and the old check never saw
them. Blocking count held at 22: −2 false positives (HRT, Quadrillion),
+2 true positives (Fiserv ×2).

---

## Smaller corrections

**The role-merge rule inverts on its own example.** The plan says "the longer
string if one is truncated (ends `...`)" and then names `'Software Engineer
Intern'` (24 chars) as the answer over `'Software Engineering Intern (Summer
2...'` (39 chars). Read literally it picks the truncated one.

> Corrected rule: **prefer the non-truncated string. A role ending in `...` is
> truncated regardless of length.**

**`date_posted` fallback cases confirmed in the live data** (the rule "take the
earlier, unless one equals its own row's `date_added`" is load-bearing here):

- Susquehanna `…-c9b7fe` (quant): `date_posted == date_added == 2026-07-23` → fallback. Take the swe row's real `2026-05-22`.
- Circleback `…-26e7bd`: `date_posted == date_added == 2026-07-23` → fallback. Take the other row's real `2026-07-15`.

**All 3 self-referential `possible_duplicate_of` sit on rows A3 deletes**
(ICE `f935cf`, Voloridge `9d8be0`, Avanade `ef8ce4`), so the plan's Step 5 is
correctly a no-op — assert it, don't edit pointers.

**Baseline confirmed accurate:** 770 rows (swe 238, quant 202, ai_ml 136,
hardware 96, data_science 48, ib 36, consulting 11, actuarial 3), 703 open /
67 closed, 0 schema failures. The plan's stated baseline was right.

---

## Still-unresolved, needs Tony

The **Anduril** two-hostname residual the plan already defers is the *same class*
as the Astreya/Copart/InterDigital locale variants resolved above — Greenhouse
serving one requisition under two hostnames (4 rows in `swe.yaml`). It was left
out of scope deliberately; revisit once A3 lands, with the caution the plan
gives: stripping `gh_jid` "fixes" it while collapsing 11 Jump Trading roles into one.
