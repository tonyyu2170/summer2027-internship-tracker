# Retro-Classification Sweep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a `categorize.py` rule change retro-apply to rows already in `data/*.yaml`, through a reviewed corrections file, so category drift stops accumulating silently.

**Architecture:** A new pure-plus-`main` module `scripts/check_categories.py` compares `classify_role(row["role"])` against the file each open row lives in and writes `scratch/category_corrections.json`. Tony edits one `action` field per entry; the existing single serialized writer `scripts/apply_ats_corrections.py` gains three action kinds (`recategorize`, `keep`, `drop`) to apply it. `auto_scrape.sh` reports drift to its own self-healing marker without writing the JSON.

**Tech Stack:** Python 3.12, PyYAML 6.0.2, pytest 8.3.4. No network. Run everything with `.venv/bin/python3` — bare `python3` may resolve to another project's venv.

**Spec:** `docs/superpowers/specs/2026-08-10-retro-classification-design.md`

**Standing repo rules that apply to every task here:**
- Run `.venv/bin/python3 scripts/check_integrity.py` before any commit touching `data/`.
- Never run `git push`. Local commits only.
- Never put "Claude", "Co-Authored-By: Claude", or a session URL in a commit message.

---

### Task 1: Narrow the over-matching `recruit` and `content` drop patterns

This is first because it is a live defect: those patterns run at classify time, so new
TikTok research postings are silently dropped on every scrape. **No `drop` action from
this feature may be applied until this task lands** — without it the sweep proposes
deleting nine legitimate rows.

**Files:**
- Modify: `scripts/categorize.py:38` (the `ai_ml` rule) and `scripts/categorize.py:47-48` (the final `DROP` rule)
- Test: `tests/test_categorize.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_categorize.py`:

```python
def test_drop_rules_do_not_match_program_or_team_names():
    # `recruit` used to fire on TikTok's program NAME ("Global Frontier Tech
    # Recruitment Program"), silently dropping 8 legitimate AI/ML roles at
    # classify time, and on XPENG's "2027 Campus Recruiting Robotics Center".
    # `content` used to fire on team names like "Data-Content Intelligence".
    assert classify_role(
        "Applied Scientist Intern - Business Integrity - Global Frontier Tech "
        "Recruitment Program - 2027 Start") == "ai_ml"
    assert classify_role(
        "Applied Scientist Intern - Trust and Safety - Multimodal Foundation "
        "Model - Global Frontier Tech Recruitment Program - 2027 Start") == "ai_ml"
    assert classify_role(
        "Research Scientist Intern (TikTok-Data-Content Intelligence) - 2027 Start"
    ) != "__drop__"
    assert classify_role("2027 Campus Recruiting Robotics Cente...") != "__drop__"


def test_drop_rules_still_catch_the_real_hr_and_content_functions():
    # Narrowing must not free the roles the patterns exist for.
    assert classify_role("Recruiting Intern") == "__drop__"
    assert classify_role("Recruiting Coordinator Intern") == "__drop__"
    assert classify_role("Talent Acquisition Intern") == "__drop__"
    assert classify_role("Content Strategy Intern") == "__drop__"
    assert classify_role("Content Marketing Intern") == "__drop__"
    assert classify_role("Content Moderation Intern") == "__drop__"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_categorize.py::test_drop_rules_do_not_match_program_or_team_names -v`
Expected: FAIL — `assert '__drop__' == 'ai_ml'`

- [ ] **Step 3: Narrow the two patterns and add the positive `applied scientist` rule**

In `scripts/categorize.py`, add the guarded `applied scientist` alternative to the
`ai_ml` rule:

```python
    ("ai_ml", r"machine learning|deep learning|\bml\b|\bai\b|\bnlp\b|computer vision"
              r"|applied scientist(?!.*\b(?:materials|chemist\w*|chemical|optics|polymer|metallurg\w*)\b)"),
```

Note the `\w*` on `chemist` and `metallurg`. The group carries a trailing `\b`, which
cannot fire before the `y` in "chemistry" or "metallurgy" — bare `chemistr` and
`metallurg` alternatives silently never match those words.

**The lookahead is required, not optional.** `_RULES` is evaluated top-down and
`ai_ml` sits ahead of the final `DROP` rule that owns `\bmaterials\b` and `chemist`,
so a bare `applied scientist` silently outranks them: `Materials Science Intern`
classifies `__drop__` but `Applied Scientist - Materials Science` would classify
`ai_ml`, contradicting the locked-in test at `tests/test_categorize.py:43`. Worse,
the sweep built in Tasks 2–7 is structurally blind to it — the rule would say
`ai_ml` and the file would say `ai_ml`, so it never reports as a disagreement.
Keep the exclusion list narrow and physical-science only; **do not add `biolog`**,
because `Applied Scientist Intern - Computational Biology` is correctly `ai_ml`.

In the final `DROP` rule, replace the `recruit` alternative:

```python
           r"|human resources|\bhr\b|recruiting intern|recruiting coordinator|\brecruiter\b|\btalent\b"
```

and replace the `\bcontent\b` alternative:

```python
           r"|newsgathering|content (?:strateg|marketing|writ|produc|moderat|design|creat)|community engagement|sponsorship|\bsports\b"
```

`creat` is in the list because `Content Creator Intern` is a common real
out-of-scope title. It cannot reintroduce the original bug: the AI research titles
that mention "Content Creation" match `\bai\b` and are claimed by the `ai_ml` rule
before the `DROP` rule is ever reached.

Leave `media` and `manufacturing` alone. `Tencent | Cloud Media Services Intern` and
`Neuralink | Manufacturing Intern, Surgery & Robot...` are judgment calls, not bugs;
they surface as `drop` proposals in Task 7 and get adjudicated with `keep`.

- [ ] **Step 4: Run the new tests and the full suite**

Run: `.venv/bin/python3 -m pytest tests/test_categorize.py -v`
Expected: PASS, including both new tests.

Run: `.venv/bin/python3 -m pytest tests/ -v`
Expected: PASS, no regressions.

- [ ] **Step 5: Verify the blast radius against real data**

Run:

```bash
.venv/bin/python3 - <<'PY'
import sys, yaml, glob, os
sys.path.insert(0, 'scripts')
from categorize import classify_role
from collections import Counter
c = Counter()
for f in sorted(glob.glob('data/*.yaml')):
    cat = os.path.basename(f)[:-5]
    for r in yaml.safe_load(open(f)) or []:
        if r.get('status') != 'closed':
            c[classify_role(r.get('role', ''))] += 1
print(dict(c))
PY
```

Expected: `__drop__` is **14** (was 23 before this task). The nine freed roles are
five TikTok `Applied Scientist` titles now classifying `ai_ml`, and four titles now
classifying `None` (three TikTok `Research Scientist` and the XPENG robotics row),
which keep their current correct placement.

- [ ] **Step 6: Commit**

```bash
git add scripts/categorize.py tests/test_categorize.py
git commit -m "fix: stop drop rules matching program and team names

recruit fired on TikTok's 'Global Frontier Tech Recruitment Program' and
XPENG's 'Campus Recruiting Robotics Center'; content fired on team names
like 'Data-Content Intelligence'. Nine legitimate AI/ML and robotics
roles were classifying __drop__ at scrape time. Narrows both to the job
function and routes applied-scientist titles to ai_ml."
```

---

### Task 2: `find_disagreements` — the pure sweep

**Files:**
- Create: `scripts/check_categories.py`
- Test: `tests/test_check_categories.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_check_categories.py`:

```python
"""Tests for the retro-classification sweep (scripts/check_categories.py)."""
import yaml

from check_categories import find_disagreements
from categorize import manual_link_categories


def _row(**kw):
    base = {
        "id": "r1", "company": "Acme", "role": "Software Engineer Intern",
        "location": "New York, NY", "link": "https://x.com/1",
        "date_posted": "2026-07-01", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["s"], "date_added": "2026-07-01",
        "last_verified": "2026-07-01", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def test_agreement_produces_no_action():
    rows = {"swe": [_row()], "quant": [], "hardware": []}
    assert find_disagreements(rows, {}) == []


def test_unclassifiable_role_produces_no_action():
    # classify_role returning None means the rules have no opinion; a row must
    # never move on no opinion.
    rows = {"swe": [_row(role="Summer Intern, Early Interest")], "quant": []}
    assert find_disagreements(rows, {}) == []


def test_closed_row_is_skipped_even_when_it_disagrees():
    rows = {"quant": [_row(role="Software Engineer Intern", status="closed")],
            "swe": []}
    assert find_disagreements(rows, {}) == []


def test_adjudicated_link_is_skipped_even_when_it_disagrees():
    rows = {"quant": [_row(role="Software Engineer Intern")], "swe": []}
    overrides = {"https://x.com/1": "quant"}
    assert find_disagreements(rows, overrides) == []


def test_cross_category_disagreement_proposes_recategorize():
    rows = {"quant": [_row(role="FPGA Engineer Intern")], "hardware": []}
    actions = find_disagreements(rows, {})
    assert len(actions) == 1
    assert actions[0]["action"] == "recategorize"
    assert actions[0]["from"] == "quant"
    assert actions[0]["to"] == "hardware"
    assert actions[0]["id"] == "r1"
    assert actions[0]["link"] == "https://x.com/1"
    assert actions[0]["role"] == "FPGA Engineer Intern"


def test_out_of_scope_role_proposes_drop():
    rows = {"quant": [_row(role="Venture Capital Analyst Intern")], "swe": []}
    actions = find_disagreements(rows, {})
    assert len(actions) == 1
    assert actions[0]["action"] == "drop"
    assert actions[0]["to"] == "__drop__"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_check_categories.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'check_categories'`

- [ ] **Step 3: Write the module with only `find_disagreements`**

Create `scripts/check_categories.py`:

```python
"""Report rows whose role no longer classifies to the category file they live
in, so a categorize.py rule change can retro-apply to already-tracked rows.

classify_role runs only on incoming postings (fetch_trackers.py), and a row
already in data/*.yaml always wins over sources/manual_categories.yaml — so
every rule added to categorize.py improves only future scrapes and leaves the
existing corpus stale. This module finds that drift.

It never writes data/*.yaml. It writes one corrections JSON — the audit record
— which Tony reviews and apply_ats_corrections.py applies, exactly like
check_ats.py and check_reposts.py. Unlike those two it needs no network, so it
carries its own pure function rather than splitting into a verify/driver pair.

Usage: python3 scripts/check_categories.py [--report-only]
"""
import json
import sys
from datetime import date
from pathlib import Path

import yaml

from categorize import DROP, classify_role, manual_link_categories
from generate_readme import CATEGORIES
from normalize import normalize_link

ROOT = Path(__file__).resolve().parent.parent


def find_disagreements(rows_by_category, overrides):
    """Pure. Returns one proposed action per open row whose role classifies to
    a category other than the file it lives in.

    `overrides` is normalized-link -> category, from manual_link_categories();
    a row whose link appears there was already adjudicated by hand and is left
    alone rather than re-litigated. A None classification means the rules have
    no opinion, which is never grounds to move a row.
    """
    actions = []
    for cat in sorted(rows_by_category):
        for row in rows_by_category[cat]:
            if row.get("status") == "closed":
                continue
            link = row.get("link") or ""
            if normalize_link(link) in overrides:
                continue
            got = classify_role(row.get("role") or "")
            if got is None or got == cat:
                continue
            actions.append({
                "id": row.get("id"),
                "action": "drop" if got == DROP else "recategorize",
                "from": cat,
                "to": got,
                "company": row.get("company"),
                "role": row.get("role"),
                "link": link,
            })
    return actions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_check_categories.py -v`
Expected: PASS — 6 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/check_categories.py tests/test_check_categories.py
git commit -m "feat: add the retro-classification sweep's pure core

find_disagreements reports open rows whose role classifies to a category
other than the file they live in, skipping closed rows, links already
adjudicated in manual_categories.yaml, and roles the rules have no
opinion on."
```

---

### Task 3: `main()`, the corrections file, and `--report-only`

**Files:**
- Modify: `scripts/check_categories.py` (append to the module from Task 2)
- Test: `tests/test_check_categories.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_check_categories.py`:

```python
def test_load_rows_reads_every_category_file(tmp_path):
    from check_categories import load_rows
    (tmp_path / "swe.yaml").write_text(yaml.safe_dump([_row()]))
    rows = load_rows(tmp_path)
    assert rows["swe"] == [_row()]
    # Categories with no file on disk must still be present, so a
    # recategorize target never KeyErrors.
    assert rows["quant"] == []


def test_drift_marker_is_written_when_drift_exists_and_removed_when_clean(tmp_path):
    from check_categories import write_drift_marker
    marker = tmp_path / "CATEGORY_DRIFT"

    write_drift_marker(marker, 3, "2026-08-10")
    assert marker.exists()
    assert "3" in marker.read_text()
    assert "check_categories.py" in marker.read_text()

    # Self-healing: a clean run removes the marker rather than leaving a
    # stale one, so the file's existence is itself the signal.
    write_drift_marker(marker, 0, "2026-08-11")
    assert not marker.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_check_categories.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_rows' from 'check_categories'`

- [ ] **Step 3: Append `load_rows`, `write_drift_marker`, and `run`**

Append to `scripts/check_categories.py`:

```python
def load_rows(data_dir):
    """category stem -> rows. Every stem in CATEGORIES is present even when its
    file is missing, so a recategorize target is always a valid key."""
    rows_by_category = {}
    for stem, _title, _is_quant in CATEGORIES:
        path = Path(data_dir) / f"{stem}.yaml"
        rows_by_category[stem] = (
            (yaml.safe_load(path.read_text()) or []) if path.exists() else [])
    return rows_by_category


def write_drift_marker(marker_path, count, today):
    """Overwrite the advisory marker, or remove it when there is no drift.

    Deliberately NOT scratch/auto_scrape/NEEDS_ATTENTION: auto_scrape.sh does
    `rm -f "$MARKER"` on both success paths, so an advisory written there is
    wiped in the same run, and that file means "the scrape stopped". Overwrite
    (not append) keeps this from growing a line per scrape while a backlog sits
    unadjudicated; removal at zero makes the file self-healing.
    """
    marker_path = Path(marker_path)
    if count:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            f"{today} {count} row(s) sit in a category their role no longer "
            f"classifies to.\nRun scripts/check_categories.py, review "
            f"scratch/category_corrections.json, then apply it.\n")
    elif marker_path.exists():
        marker_path.unlink()


def run(data_dir=None, out_path=None, marker_path=None, report_only=False):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    out_path = Path(out_path) if out_path else ROOT / "scratch" / "category_corrections.json"
    marker_path = (Path(marker_path) if marker_path
                   else ROOT / "scratch" / "auto_scrape" / "CATEGORY_DRIFT")

    rows_by_category = load_rows(data_dir)
    actions = find_disagreements(rows_by_category, manual_link_categories())
    today = date.today().isoformat()

    write_drift_marker(marker_path, len(actions), today)

    for a in actions:
        print(f"    {a['from']} -> {a['to']}: [{a['id']}] "
              f"{a['company']} | {a['role']}")
    print(f"{len(actions)} disagreement(s)")

    if report_only:
        # Writing the JSON here would trip auto_scrape.sh's own in-flight
        # guard and block every later scrape until the review finished.
        return actions

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(
        {"generated": today, "actions": actions}, indent=2))
    print(f"-> {out_path}")
    return actions


if __name__ == "__main__":
    run(report_only="--report-only" in sys.argv[1:])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_check_categories.py -v`
Expected: PASS — 8 passed.

- [ ] **Step 5: Verify `--report-only` writes no JSON**

Run:

```bash
rm -f scratch/category_corrections.json
.venv/bin/python3 scripts/check_categories.py --report-only | tail -3
ls scratch/category_corrections.json 2>&1 | tail -1
```

Expected: a disagreement count is printed, and the `ls` reports
`No such file or directory`.

- [ ] **Step 6: Commit**

```bash
git add scripts/check_categories.py tests/test_check_categories.py
git commit -m "feat: add check_categories CLI, corrections file and drift marker

--report-only prints and maintains scratch/auto_scrape/CATEGORY_DRIFT
without writing the corrections JSON, so a scrape-time report cannot trip
the in-flight guard and block later scrapes."
```

---

### Task 4: `recategorize`, `keep` and `drop` in the applier

**Files:**
- Modify: `scripts/apply_ats_corrections.py:43-45` (summary buckets), `:96-105` (action dispatch), `:106-120` (rebuild loop)
- Test: `tests/test_apply_ats_corrections.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_apply_ats_corrections.py`:

```python
def test_recategorize_moves_the_row_without_touching_id_or_link():
    new, summary = apply_corrections(
        {"quant": [_row(role="FPGA Engineer Intern")], "hardware": []},
        [_action(action="recategorize", **{"from": "quant", "to": "hardware"})],
        TODAY)
    assert new["quant"] == []
    assert len(new["hardware"]) == 1
    # The id is a hash of company/role/link and does not embed the category,
    # so a move must not rehash it — that would be the id/link drift bug.
    assert new["hardware"][0]["id"] == "r1"
    assert new["hardware"][0]["link"] == "https://x.com/1"
    assert summary["recategorized"] == ["r1"]


def test_recategorize_to_an_unknown_category_is_rejected():
    new, summary = apply_corrections(
        {"quant": [_row()], "hardware": []},
        [_action(action="recategorize", **{"from": "quant", "to": "nonsense"})],
        TODAY)
    assert new["quant"] == [_row()]
    assert summary["unrecognized_action"] == ["r1"]
    assert summary["recategorized"] == []


def test_keep_leaves_every_category_file_untouched():
    new, summary = apply_corrections(
        {"quant": [_row()], "hardware": []},
        [_action(action="keep", **{"from": "quant", "to": "swe"})],
        TODAY)
    assert new["quant"] == [_row()]
    assert new["hardware"] == []
    assert summary["kept"] == ["r1"]


def test_keep_without_a_from_is_rejected():
    new, summary = apply_corrections(
        {"quant": [_row()]}, [_action(action="keep", to="swe")], TODAY)
    assert new["quant"] == [_row()]
    assert summary["unrecognized_action"] == ["r1"]


def test_drop_deletes_the_row():
    new, summary = apply_corrections(
        {"quant": [_row(role="Venture Capital Analyst Intern")]},
        [_action(action="drop", **{"from": "quant", "to": "__drop__"})],
        TODAY)
    assert new["quant"] == []
    assert summary["dropped"] == ["r1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python3 -m pytest tests/test_apply_ats_corrections.py -k "recategorize or keep or drop" -v`
Expected: FAIL — `KeyError: 'recategorized'`

- [ ] **Step 3: Add the three actions**

In `scripts/apply_ats_corrections.py`, extend the summary buckets:

```python
    summary = {k: [] for k in (
        "confirmed", "date_fixed", "closed", "deleted", "reposted",
        "recategorized", "kept", "dropped",
        "unknown", "skipped", "unrecognized_action")}
    deleted, verified, moved = set(), set(), {}
```

Add three branches immediately before the final `else:` in the action loop:

```python
        elif act == "recategorize":
            # A typo in `to` must not silently vanish a row: reject it the way
            # a renamed action kind is rejected, and leave the row alone.
            if a.get("to") not in rows_by_category:
                summary["unrecognized_action"].append(rid)
                continue
            moved[rid] = a["to"]
            summary["recategorized"].append(rid)
        elif act == "keep":
            # The row stays put; run() records the decision in
            # manual_categories.yaml so the sweep stops re-reporting it.
            if not a.get("from"):
                summary["unrecognized_action"].append(rid)
                continue
            summary["kept"].append(rid)
        elif act == "drop":
            deleted.add(rid)
            summary["dropped"].append(rid)
```

Replace the rebuild loop with one that relocates moved rows:

```python
    new, relocated = {}, []
    for cat, rows in rows_by_category.items():
        kept = []
        for row in rows:
            rid = row.get("id")
            if rid in deleted:
                continue
            if rid in verified:
                row["last_verified"] = today
            if row.get("possible_duplicate_of") in deleted:
                row["possible_duplicate_of"] = None
            if rid in moved:
                relocated.append((moved[rid], row))
                continue
            kept.append(row)
        new[cat] = kept
    for target, row in relocated:
        new[target].append(row)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python3 -m pytest tests/test_apply_ats_corrections.py -v`
Expected: PASS, including the five new tests and every pre-existing one.

- [ ] **Step 5: Commit**

```bash
git add scripts/apply_ats_corrections.py tests/test_apply_ats_corrections.py
git commit -m "feat: apply recategorize, keep and drop corrections

recategorize moves a row between category files without rehashing its id
(the id does not embed the category); an unknown target is rejected
rather than vanishing the row."
```

---

### Task 5: Record adjudications in `manual_categories.yaml`, plus the idempotence test

Without this the sweep re-reports every deliberate placement forever — roughly 110 of
the first run's rows — and the tool is unusable on its second run.

**Files:**
- Modify: `scripts/apply_ats_corrections.py` (the reporting section of `run`, beside the existing `superseded` append at `:236`)
- Test: `tests/test_check_categories.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_check_categories.py`:

```python
def test_sweep_apply_keep_sweep_is_idempotent(tmp_path):
    # The property this whole feature exists for. It is also the only test
    # that catches a mismatch between the link form `keep` WRITES to
    # manual_categories.yaml and the form find_disagreements READS back:
    # the applier appends the raw link, manual_link_categories normalizes
    # keys on read, and find_disagreements normalizes before comparing.
    rows = {"quant": [_row(role="Software Engineer Intern",
                           link="https://x.com/1?utm_source=board")],
            "swe": []}

    first = find_disagreements(rows, {})
    assert len(first) == 1
    assert first[0]["action"] == "recategorize"

    # Simulate adjudicating it as `keep`, byte-for-byte how run() writes it.
    overrides_path = tmp_path / "manual_categories.yaml"
    overrides_path.write_text(
        yaml.safe_dump({first[0]["link"]: first[0]["from"]}, sort_keys=True))

    second = find_disagreements(
        rows, manual_link_categories(path=overrides_path))
    assert second == []
```

- [ ] **Step 2: Run the test — this one is expected to PASS**

Run: `.venv/bin/python3 -m pytest tests/test_check_categories.py::test_sweep_apply_keep_sweep_is_idempotent -v`
Expected: PASS. This is a characterization test, not a red-first one: both sides
already normalize (the applier appends the raw link, `manual_link_categories`
normalizes keys on read, `find_disagreements` normalizes before comparing), so the
round-trip holds as soon as Task 2's module exists. Its job is to **stay** green —
it fails the day someone makes one side normalize and the other not. If it fails
now, stop: the read/write asymmetry must be fixed before Step 3.

- [ ] **Step 3: Append adjudications in `run()`**

In `scripts/apply_ats_corrections.py`, directly after the existing `superseded` block
(the one writing `"# auto apply_ats_corrections: superseded by a repost.\n"`), add:

```python
    # Record every category adjudication so check_categories stops re-reporting
    # it. Keyed off the summary, not the raw actions, so an action whose row no
    # longer exists never writes a decision about a row that is not there.
    applied_keep, applied_drop = set(summary["kept"]), set(summary["dropped"])
    adjudicated = {}
    for a in actions:
        rid, link = a.get("id"), a.get("link")
        if not link:
            continue
        if a.get("action") == "keep" and rid in applied_keep:
            adjudicated[link] = a["from"]
        elif a.get("action") == "drop" and rid in applied_drop:
            adjudicated[link] = "__drop__"
    if adjudicated:
        with open(ROOT / "sources" / "manual_categories.yaml", "a") as f:
            f.write("# auto apply_ats_corrections: category adjudication.\n")
            f.write(yaml.safe_dump(adjudicated, sort_keys=True))
    for rid in summary["recategorized"]:
        print(f"    recategorized: [{rid}]")
    for rid in summary["kept"]:
        print(f"    kept: [{rid}]")
    for rid in summary["dropped"]:
        print(f"    dropped: [{rid}]")
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python3 -m pytest tests/ -v`
Expected: PASS, all tests including the idempotence test.

- [ ] **Step 5: Commit**

```bash
git add scripts/apply_ats_corrections.py tests/test_check_categories.py
git commit -m "feat: record category adjudications so the sweep stops re-reporting

A keep writes the row's current category to manual_categories.yaml and a
drop writes __drop__, both keyed off the summary so a decision is never
recorded for a row that no longer exists. Adds the sweep/keep/sweep
idempotence test that guards the raw-vs-normalized link round trip."
```

---

### Task 6: Wire the advisory report into `auto_scrape.sh`

**Files:**
- Modify: `scripts/auto_scrape.sh:30-33` (in-flight guard) and `:88-94` (after `verify_links.py`)

- [ ] **Step 1: Extend the in-flight guard**

Replace the guard at `scripts/auto_scrape.sh:30-33` with:

```bash
if [ -f "$REPO/scratch/ats_corrections.json" ]; then
    log "skip: scratch/ats_corrections.json exists (ATS review in flight)"
    exit 0
fi
if [ -f "$REPO/scratch/category_corrections.json" ]; then
    log "skip: scratch/category_corrections.json exists (category review in flight)"
    exit 0
fi
```

- [ ] **Step 2: Report drift after `verify_links.py`**

Immediately after the `verify_links.py` block that ends at `scripts/auto_scrape.sh:94`
(the `fi` closing `if [ $rc -ne 0 ]`), insert:

```bash
# Advisory only: never writes the corrections JSON (that would trip the
# in-flight guard above and block every later scrape) and never fails the
# run. Maintains its own CATEGORY_DRIFT marker because $MARKER is cleared
# on both success paths below.
log "run: check_categories.py --report-only"
"$PY" scripts/check_categories.py --report-only >> "$LOG" 2>&1 || \
    log "warn: check_categories.py --report-only failed — see log"
```

- [ ] **Step 3: Verify the script still parses and the guard works**

Run: `bash -n scripts/auto_scrape.sh`
Expected: no output (syntax OK).

Run:

```bash
touch scratch/category_corrections.json
bash scripts/auto_scrape.sh
tail -2 scratch/auto_scrape/auto_scrape.log
rm -f scratch/category_corrections.json
```

Expected: the log's last line reads
`skip: scratch/category_corrections.json exists (category review in flight)`,
and `git status --short` shows no changes to `data/` or `README.md`.

- [ ] **Step 4: Commit**

```bash
git add scripts/auto_scrape.sh
git commit -m "feat: report category drift on every scrape

Advisory only — never writes the corrections JSON and never fails the
run. Uses its own CATEGORY_DRIFT marker because NEEDS_ATTENTION is
cleared on both success paths. Adds the corrections file to the
in-flight guard."
```

---

### Task 7: First live run — generate, adjudicate, apply

**This task is not autonomous.** It deletes and moves real rows. Stop at Step 2 and
hand the file to Tony; do not apply anything he has not reviewed.

**Files:**
- Modify: `data/*.yaml`, `sources/manual_categories.yaml`, `README.md` (all via the applier)

- [ ] **Step 1: Generate the corrections file**

Run: `.venv/bin/python3 scripts/check_categories.py`
Expected: roughly 118 disagreements (127 measured on 2026-08-10, minus the nine Task 1
frees), written to `scratch/category_corrections.json`.

- [ ] **Step 2: Hand the file to Tony for adjudication**

Present the actions grouped by `from -> to` with counts, and state plainly that every
entry left as `recategorize` or `drop` will move or delete that row. He sets
`"action": "keep"` on each deliberate placement. Known ones to expect from the
2026-08-10 sweep: ~40 `quant -> swe` (SWE roles at quant firms), ~26 `hardware -> swe`,
and the two judgment-call drops (`Tencent | Cloud Media Services Intern`,
`Neuralink | Manufacturing Intern, Surgery & Robot...`). Known real errors to expect:
`Optiver | FPGA Engineer Intern` ×2 under `quant -> hardware`.

- [ ] **Step 3: Apply the reviewed file**

Run: `.venv/bin/python3 scripts/apply_ats_corrections.py scratch/category_corrections.json`
Expected: per-row `recategorized:` / `kept:` / `dropped:` lines, then the rewritten
category files and a re-rendered `README.md`.

- [ ] **Step 4: Verify integrity and idempotence on real data**

Run: `.venv/bin/python3 scripts/check_integrity.py`
Expected: `No blocking violations.`

Run: `.venv/bin/python3 scripts/check_categories.py --report-only`
Expected: `0 disagreement(s)`, and `scratch/auto_scrape/CATEGORY_DRIFT` does not exist.

- [ ] **Step 5: Commit**

```bash
rm -f scratch/category_corrections.json
git add data/ sources/manual_categories.yaml README.md
git commit -m "fix: apply the first retro-classification pass

Moves rows whose role no longer classifies to the file they live in,
drops out-of-scope roles, and records every deliberate placement in
manual_categories.yaml so the sweep stays quiet."
```

---

### Task 8: Document the run-book

**Files:**
- Modify: `internship-tracker/CLAUDE.md` (the command list under "Tech stack & commands")
- Modify: `docs/SCRAPING.md`

- [ ] **Step 1: Add the commands to CLAUDE.md**

In the fenced command block under "Tech stack & commands", after the `check_reposts.py`
lines, add:

```bash
python3 scripts/check_categories.py                        # retro-classification sweep -> scratch/category_corrections.json
python3 scripts/apply_ats_corrections.py scratch/category_corrections.json  # same applier (recategorize/keep/drop)
```

- [ ] **Step 2: Note the advisory in docs/SCRAPING.md**

Add to the section describing `auto_scrape.sh`:

> Every run also calls `check_categories.py --report-only`. It is advisory: it never
> writes the corrections JSON and never fails the run. When rows have drifted out of
> category it writes `scratch/auto_scrape/CATEGORY_DRIFT` (overwritten each run, and
> removed once the count reaches zero). `NEEDS_ATTENTION` still means only "the scrape
> stopped".

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md docs/SCRAPING.md
git commit -m "docs: record the retro-classification run-book"
```

---

## Verification summary

| Gate | Command | Expected |
| --- | --- | --- |
| Unit tests | `.venv/bin/python3 -m pytest tests/ -v` | all pass |
| Rule blast radius (Task 1) | the inline `Counter` script | `__drop__` count 23 → 14 |
| Data invariants | `.venv/bin/python3 scripts/check_integrity.py` | `No blocking violations.` |
| Idempotence on real data | `.venv/bin/python3 scripts/check_categories.py --report-only` | `0 disagreement(s)` |
| Shell syntax | `bash -n scripts/auto_scrape.sh` | no output |
