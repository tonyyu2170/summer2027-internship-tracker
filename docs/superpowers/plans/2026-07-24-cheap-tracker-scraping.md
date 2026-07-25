# Cheap GitHub-tracker Scraping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-tracker LLM README parsing with deterministic parsers plus a commit-SHA skip, so a plain `scrape` costs near-zero tokens.

**Architecture:** Two new pure, tested modules (`scripts/categorize.py`, `scripts/parse_tracker.py`) sit on the tested-core side of the boundary `docs/SCRAPING.md` draws. One new untested network shim (`scripts/fetch_trackers.py`) does SHA checks, fetches, and writes fetch reports — the same contract scraping subagents use today. Rows no deterministic rule can categorize are handed to the session via `scratch/fetch_reports/unclassified.json` rather than an in-script LLM call.

**Tech Stack:** Python 3.12, PyYAML 6.0.2, jsonschema 4.26.0, pytest 8.3.4, stdlib `urllib`/`json`/`re`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-24-cheap-tracker-scraping-design.md`

---

## File Structure

| File | Responsibility | Tested |
|---|---|---|
| `scripts/categorize.py` (new) | Role→category rules, upstream-category mapping, known-link category lookup | yes |
| `scripts/parse_tracker.py` (new) | Four format-family parsers, source text → postings | yes |
| `scripts/fetch_trackers.py` (new) | Network: SHA check, fetch, dispatch, write fetch reports | no |
| `sources/github_trackers.yaml` (modify) | Add `path`/`fmt` per tracker | — |
| `sources/scrape_state.yaml` (new) | Per-tracker last-parsed SHA, row_count | — |
| `scripts/run_scrape_merge.py` (modify) | Refuse to merge while unclassified rows are pending | yes |
| `tests/fixtures/*` (new) | Captured real output, one per tracker | — |
| `docs/SCRAPING.md` (modify) | Runbook rewrite | — |

Four format families (not nine parsers):
- **cvrve JSON** — `simplifyjobs`, `suryaharikrishnan`, `vanshb03`
- **zshah101 JSON** — `zshah101`
- **nufintech YAML** — `northwesternfintech`
- **pipe table** — `speedyapply`, `sndsh404`, `zapplyjobs`, `chieler`

---

### Task 1: Category rules

**Files:**
- Create: `scripts/categorize.py`
- Test: `tests/test_categorize.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_categorize.py
from categorize import classify_role, map_upstream_category, assign_category


def test_hardware_wins_over_quant_for_quant_firm_hardware_roles():
    # Regression guard for 0fdf5dd: Akuna Capital Hardware Engineer was
    # miscategorized as quant. Hardware must be checked before quant.
    assert classify_role("Hardware Engineer Intern") == "hardware"
    assert classify_role("Hardware Engineer (FPGA/ASIC) Intern") == "hardware"
    assert classify_role("Quantitative Hardware Engineer") == "hardware"


def test_classify_role_basic_categories():
    assert classify_role("Quantitative Trader Intern") == "quant"
    assert classify_role("Quantitative Research Intern (PHD)") == "quant"
    assert classify_role("Machine Learning Engineer Intern") == "ai_ml"
    assert classify_role("Data Scientist Intern") == "data_science"
    assert classify_role("Actuarial Intern") == "actuarial"
    assert classify_role("Investment Banking Summer Analyst") == "ib"
    assert classify_role("Consulting Intern") == "consulting"
    assert classify_role("Software Engineer Intern") == "swe"


def test_classify_role_returns_none_when_no_rule_matches():
    assert classify_role("Summer Intern") is None
    assert classify_role("Business Intern") is None


def test_map_upstream_category_handles_both_ai_data_spellings():
    # simplifyjobs/suryaharikrishnan spell it "AI/ML/Data"; zshah101 uses
    # "Data & ML/AI". Both split on role text.
    assert map_upstream_category("AI/ML/Data", "Data Scientist Intern") == "data_science"
    assert map_upstream_category("AI/ML/Data", "ML Engineer Intern") == "ai_ml"
    assert map_upstream_category("Data & ML/AI", "Data Analyst Intern") == "data_science"
    assert map_upstream_category("Data & ML/AI", "Deep Learning Intern") == "ai_ml"


def test_map_upstream_category_known_values():
    assert map_upstream_category("Software", "X") == "swe"
    assert map_upstream_category("Software Engineering", "X") == "swe"
    assert map_upstream_category("Quant", "X") == "quant"
    assert map_upstream_category("Quantitative Finance", "X") == "quant"
    assert map_upstream_category("Hardware", "X") == "hardware"


def test_map_upstream_category_drops_product():
    assert map_upstream_category("Product", "Product Manager Intern") == "__drop__"


def test_map_upstream_category_unknown_value_falls_through_to_classifier():
    # Upstream repos rename categories without notice. Unknown values must
    # never be dropped — they fall through to role-text classification.
    assert map_upstream_category("Cybersecurity", "Software Engineer Intern") == "swe"
    assert map_upstream_category("Brand New Bucket", "Summer Intern") is None


def test_assign_category_never_reclassifies_a_known_link():
    # merge_category dedupes within one category file only, so a link that
    # moves category would exist twice with nothing to catch it.
    known = {"https://example.com/jobs/1": "quant"}
    posting = {"link": "https://example.com/jobs/1?utm_source=x",
               "role": "Hardware Engineer Intern"}
    assert assign_category(posting, known) == "quant"


def test_assign_category_uses_rules_for_unknown_link():
    assert assign_category(
        {"link": "https://example.com/jobs/2", "role": "Software Engineer Intern"},
        {},
    ) == "swe"


def test_assign_category_returns_none_when_undecidable():
    assert assign_category(
        {"link": "https://example.com/jobs/3", "role": "Summer Intern"}, {}
    ) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_categorize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'categorize'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/categorize.py
"""Pure category assignment. No network, no LLM.

Category is assigned once, at first sight of a link, and is stable
thereafter: merge_category dedupes within a single category file only, so a
link that changed category would silently exist in two files. assign_category
therefore always prefers the category a link already has in data/*.yaml."""
import re
import yaml
from pathlib import Path

from normalize import normalize_link

ROOT = Path(__file__).resolve().parent.parent

# Sentinel: upstream category has no local equivalent; drop the posting.
DROP = "__drop__"

# Order matters. Hardware is checked before quant so hardware roles at quant
# firms (Jane Street, Akuna, IMC) route to hardware.yaml — the convention,
# and the bug fixed by hand in 0fdf5dd. data_science precedes ai_ml so
# "Data Scientist" wins over a bare AI match.
_RULES = [
    ("hardware", r"hardware|fpga|asic|firmware|silicon|verilog|\brtl\b|embedded|\bpcb\b"),
    ("actuarial", r"actuar"),
    ("ib", r"investment bank|\bibd\b"),
    ("consulting", r"consult"),
    ("quant", r"\bquant"),
    ("data_science", r"data scien|data analy|analytics"),
    ("ai_ml", r"machine learning|deep learning|\bml\b|\bai\b|\bnlp\b|computer vision"),
    ("swe", r"software|\bswe\b|engineer|developer|programmer|full.?stack|backend|frontend"),
]

_UPSTREAM = {
    "software": "swe",
    "software engineering": "swe",
    "quant": "quant",
    "quantitative finance": "quant",
    "hardware": "hardware",
    "product": DROP,
}

# Both spellings of the combined AI/data bucket seen in the wild.
_AI_DATA = {"ai/ml/data", "data & ml/ai"}


def classify_role(role: str) -> str | None:
    """Return a local category from role text, or None if no rule matches."""
    text = (role or "").lower()
    for category, pattern in _RULES:
        if re.search(pattern, text):
            return category
    return None


def map_upstream_category(value: str, role: str) -> str | None:
    """Map a tracker's own category string onto a local category.

    Returns DROP for upstream categories with no local equivalent. Unknown
    values fall through to classify_role rather than being dropped —
    upstream repos add and rename categories without notice."""
    key = (value or "").strip().lower()
    if key in _AI_DATA:
        return "data_science" if re.search(
            r"data scien|data analy|analytics", (role or "").lower()
        ) else "ai_ml"
    if key in _UPSTREAM:
        return _UPSTREAM[key]
    return classify_role(role)


def known_link_categories(data_dir=None) -> dict:
    """Map normalized link -> category, over every row in data/*.yaml."""
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    known = {}
    for path in sorted(data_dir.glob("*.yaml")):
        for row in (yaml.safe_load(path.read_text()) or []):
            link = row.get("link")
            if link:
                known[normalize_link(link)] = path.stem
    return known


def assign_category(posting: dict, known: dict) -> str | None:
    """Category for one posting: its existing one if the link is already
    tracked, else the tracker's own category, else role-text rules, else
    None (meaning: hand to the session to classify)."""
    link = posting.get("link")
    if link:
        existing = known.get(normalize_link(link))
        if existing:
            return existing
    if posting.get("upstream_category"):
        return map_upstream_category(posting["upstream_category"], posting.get("role", ""))
    return classify_role(posting.get("role", ""))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_categorize.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/categorize.py tests/test_categorize.py
git commit -m "feat: pure category assignment with link-stable categories"
```

---

### Task 2: Capture fixtures

Fixtures are captured before parsers so every parser test asserts against
real upstream output rather than an invented shape.

**Files:**
- Create: `tests/fixtures/` (9 files)

- [ ] **Step 1: Capture all nine fixtures**

```bash
cd "$(git rev-parse --show-toplevel)"
mkdir -p tests/fixtures

curl -s "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/.github/scripts/listings.json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps([e for e in d if e.get('active') and 'Summer 2027' in e.get('terms',[])][:20] + [e for e in d if 'Summer 2026' in e.get('terms',[])][:5], indent=1))" \
  > tests/fixtures/simplifyjobs.json

curl -s "https://raw.githubusercontent.com/SuryaHarikrishnan/internship-tracker/master/data/listings.json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps([e for e in d if e.get('active') and 'Summer 2027' in e.get('terms',[])][:20], indent=1))" \
  > tests/fixtures/suryaharikrishnan.json

curl -s "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps([e for e in d if e.get('season')=='Summer'][:20], indent=1))" \
  > tests/fixtures/vanshb03.json

curl -s "https://raw.githubusercontent.com/zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships/main/data/jobs.json" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); items=list(d.items()); print(json.dumps(dict(items[:25]), indent=1))" \
  > tests/fixtures/zshah101.json

curl -s "https://raw.githubusercontent.com/northwesternfintech/2027QuantInternships/main/data/akuna-capital.yaml" \
  > tests/fixtures/northwesternfintech.yaml

for spec in "speedyapply/2027-SWE-College-Jobs:main" "sndsh404/summer-2027-internships:main" \
            "zapplyjobs/Internships-2027:main" "Chieler/Summer-2027-SWE-Internships:main"; do
  repo="${spec%:*}"; br="${spec##*:}"
  name=$(echo "$repo" | cut -d/ -f1 | tr 'A-Z' 'a-z')
  curl -s "https://raw.githubusercontent.com/$repo/$br/README.md" > "tests/fixtures/$name.md"
done

ls -la tests/fixtures/
```

Expected: 9 files, none zero-length.

- [ ] **Step 2: Verify each fixture is non-empty and parseable**

```bash
python3 -c "
import json, yaml, pathlib
d = pathlib.Path('tests/fixtures')
for f in sorted(d.iterdir()):
    n = f.stat().st_size
    assert n > 0, f'{f} is empty'
    if f.suffix == '.json': json.loads(f.read_text())
    if f.suffix == '.yaml': yaml.safe_load(f.read_text())
    print(f.name, n, 'bytes OK')
"
```

Expected: nine `OK` lines, no assertion error.

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/
git commit -m "test: capture real tracker output as parser fixtures"
```

---

### Task 3: cvrve JSON parser

Covers `simplifyjobs`, `suryaharikrishnan`, `vanshb03` — one schema, differing
only in whether the cycle lives in `terms[]` or `season`.

**Files:**
- Create: `scripts/parse_tracker.py`
- Test: `tests/test_parse_tracker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_parse_tracker.py
import json
from pathlib import Path

from parse_tracker import parse_cvrve_json

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name):
    return (FIXTURES / name).read_text()


def test_parse_cvrve_json_filters_to_the_requested_term():
    # simplifyjobs' export is mostly Summer 2026; the fixture deliberately
    # includes Summer 2026 rows that must not survive.
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    assert postings, "expected some Summer 2027 postings"
    assert all(p["term"] == "Summer 2027" for p in postings)


def test_parse_cvrve_json_emits_required_fetch_report_fields():
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    for p in postings:
        for field in ("company", "role", "location", "link", "term", "degree"):
            assert p.get(field), f"missing {field} in {p}"
        assert isinstance(p["degree"], list) and p["degree"]
        assert set(p["degree"]) <= {"BS", "MS", "PhD"}


def test_parse_cvrve_json_takes_first_location():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"Software Engineer Intern",'
        '"url":"https://e.com/1","locations":["Atlanta, GA","Palm Beach, FL"],'
        '"active":true,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["location"] == "Atlanta, GA"


def test_parse_cvrve_json_maps_degrees():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":true,"terms":["Summer 2027"],'
        '"degrees":["Bachelor\'s","PhD"]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["degree"] == ["BS", "PhD"]


def test_parse_cvrve_json_defaults_degree_when_absent():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":true,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["degree"] == ["BS"]


def test_parse_cvrve_json_sets_closed_marker_from_active_false():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":false,"terms":["Summer 2027"],"degrees":[]}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["closed_marker"] is True


def test_parse_cvrve_json_season_variant_for_vanshb03():
    postings = parse_cvrve_json(
        _fixture("vanshb03.json"), term_field="season", term_value="Summer",
        term_out="Summer 2027",
    )
    assert postings
    assert all(p["term"] == "Summer 2027" for p in postings)


def test_parse_cvrve_json_carries_upstream_category_when_present():
    postings = parse_cvrve_json(
        _fixture("simplifyjobs.json"), term_field="terms", term_value="Summer 2027"
    )
    assert any(p.get("upstream_category") for p in postings)


def test_parse_cvrve_json_converts_unix_date_posted():
    postings = parse_cvrve_json(
        '[{"company_name":"C","title":"R","url":"https://e.com/1",'
        '"locations":["NY, NY"],"active":true,"terms":["Summer 2027"],'
        '"degrees":[],"date_posted":1764210912}]',
        term_field="terms", term_value="Summer 2027",
    )
    assert postings[0]["date_posted"] == "2025-11-27"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_parse_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'parse_tracker'`

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/parse_tracker.py
"""Deterministic parsers: tracker source text -> fetch-report postings.

Pure and network-free, so it lives on the tested side of the boundary
docs/SCRAPING.md draws. scripts/fetch_trackers.py does the fetching and
calls in here. Four format families cover all nine trackers."""
import json
from datetime import datetime, timezone

_DEGREE_MAP = {
    "bachelor's": "BS", "bachelors": "BS", "bs": "BS",
    "master's": "MS", "masters": "MS", "ms": "MS",
    "phd": "PhD", "ph.d.": "PhD", "doctorate": "PhD",
}


def _degrees(values) -> list:
    """Map a source's degree strings onto the schema's BS/MS/PhD enum.
    Defaults to ['BS'] — these lists target undergrads."""
    out = []
    for v in values or []:
        mapped = _DEGREE_MAP.get(str(v).strip().lower())
        if mapped and mapped not in out:
            out.append(mapped)
    return out or ["BS"]


def _from_unix(ts):
    if not ts:
        return None
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()


def parse_cvrve_json(text, term_field, term_value, term_out=None):
    """Parse the cvrve-family export shared by simplifyjobs,
    suryaharikrishnan and vanshb03.

    term_field is 'terms' (a list) or 'season' (a string); only entries
    matching term_value survive. term_out is the term written to the
    posting, defaulting to term_value — vanshb03 stores season 'Summer' in a
    2027-scoped repo, so it emits 'Summer 2027'."""
    entries = json.loads(text)
    term_out = term_out or term_value
    postings = []
    for e in entries:
        raw = e.get(term_field)
        matches = term_value in raw if isinstance(raw, list) else raw == term_value
        if not matches:
            continue
        locations = e.get("locations") or []
        posting = {
            "company": e.get("company_name"),
            "role": e.get("title"),
            "location": locations[0] if locations else None,
            "link": e.get("url"),
            "term": term_out,
            "degree": _degrees(e.get("degrees")),
            "closed_marker": not e.get("active", True),
        }
        date_posted = _from_unix(e.get("date_posted"))
        if date_posted:
            posting["date_posted"] = date_posted
        if e.get("category"):
            posting["upstream_category"] = e["category"]
        postings.append(posting)
    return postings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_parse_tracker.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/parse_tracker.py tests/test_parse_tracker.py
git commit -m "feat: cvrve-family JSON parser with term filtering"
```

---

### Task 4: zshah101 JSON parser

**Files:**
- Modify: `scripts/parse_tracker.py`
- Modify: `tests/test_parse_tracker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parse_tracker.py`, and add `parse_zshah_json` to the
import at the top of the file:

```python
def test_parse_zshah_json_filters_to_summer_2027_and_open():
    postings = parse_zshah_json(_fixture("zshah101.json"), season="Summer 2027")
    assert all(p["term"] == "Summer 2027" for p in postings)


def test_parse_zshah_json_reads_dict_keyed_by_id():
    text = ('{"amazon:amazon:1": {"company":"Amazon","title":"Software Dev Engineer Intern",'
            '"url":"https://e.com/1","location":"Seattle, WA","is_open":true,'
            '"season":"Summer 2027","category":"Software",'
            '"posted_at":"2026-03-25T00:00:00Z"}}')
    postings = parse_zshah_json(text, season="Summer 2027")
    assert len(postings) == 1
    p = postings[0]
    assert p["company"] == "Amazon"
    assert p["location"] == "Seattle, WA"
    assert p["upstream_category"] == "Software"
    assert p["date_posted"] == "2026-03-25"
    assert p["closed_marker"] is False


def test_parse_zshah_json_excludes_other_seasons():
    text = ('{"a": {"company":"A","title":"R","url":"https://e.com/1",'
            '"location":"NY, NY","is_open":true,"season":"Fall 2026","category":"Software"}}')
    assert parse_zshah_json(text, season="Summer 2027") == []


def test_parse_zshah_json_sets_closed_marker_from_is_open_false():
    text = ('{"a": {"company":"A","title":"R","url":"https://e.com/1",'
            '"location":"NY, NY","is_open":false,"season":"Summer 2027",'
            '"category":"Software"}}')
    assert parse_zshah_json(text, season="Summer 2027")[0]["closed_marker"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_parse_tracker.py -k zshah -v`
Expected: FAIL — `ImportError: cannot import name 'parse_zshah_json'`

- [ ] **Step 3: Write minimal implementation**

Append to `scripts/parse_tracker.py`:

```python
def _from_iso(value):
    if not value:
        return None
    return value.split("T")[0]


def parse_zshah_json(text, season):
    """Parse zshah101's data/jobs.json — a dict keyed by job id, with a
    singular `location` string and an explicit `season` per entry."""
    entries = json.loads(text)
    values = entries.values() if isinstance(entries, dict) else entries
    postings = []
    for e in values:
        if e.get("season") != season:
            continue
        posting = {
            "company": e.get("company"),
            "role": e.get("title"),
            "location": e.get("location"),
            "link": e.get("url"),
            "term": season,
            "degree": ["BS"],
            "closed_marker": not e.get("is_open", True),
        }
        date_posted = _from_iso(e.get("posted_at"))
        if date_posted:
            posting["date_posted"] = date_posted
        if e.get("category"):
            posting["upstream_category"] = e["category"]
        postings.append(posting)
    return postings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_parse_tracker.py -v`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/parse_tracker.py tests/test_parse_tracker.py
git commit -m "feat: zshah101 JSON parser"
```

---

### Task 5: northwesternfintech YAML parser

**Files:**
- Modify: `scripts/parse_tracker.py`
- Modify: `tests/test_parse_tracker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parse_tracker.py`, adding `parse_nufintech_yaml` to the import:

```python
def test_parse_nufintech_yaml_maps_role_codes_to_categories():
    postings = parse_nufintech_yaml(_fixture("northwesternfintech.yaml"))
    by_cat = {}
    for p in postings:
        by_cat.setdefault(p["category"], []).append(p)
    # Akuna's fixture has QD, QR, SWE and HW entries. HW must route to
    # hardware even though this is a quant-only repo (0fdf5dd).
    assert "hardware" in by_cat
    assert "quant" in by_cat
    assert "swe" in by_cat


def test_parse_nufintech_yaml_never_emits_a_closed_marker():
    # The repo publishes no status: its checkmark is decorative (66 checks,
    # 0 crosses) and closure is expressed by deleting the entry, which is
    # disappearance — this repo refuses to auto-close on that.
    postings = parse_nufintech_yaml(_fixture("northwesternfintech.yaml"))
    assert postings
    assert all(p.get("closed_marker") is False for p in postings)


def test_parse_nufintech_yaml_uses_label_in_role_when_present():
    text = """
name: "Test Capital"
website: "https://e.com"
locations: "Chicago"
notes: ""
roles:
  - role_type: "SWE"
    links:
      - url: "https://e.com/1"
        label: "C++"
      - url: "https://e.com/2"
"""
    postings = parse_nufintech_yaml(text)
    roles = {p["link"]: p["role"] for p in postings}
    assert roles["https://e.com/1"] == "Software Engineer Intern, C++"
    assert roles["https://e.com/2"] == "Software Engineer Intern"


def test_parse_nufintech_yaml_handles_company_with_no_roles():
    text = 'name: "Empty Co"\nwebsite: "https://e.com"\nlocations: "NYC"\nnotes: ""\nroles: []\n'
    assert parse_nufintech_yaml(text) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_parse_tracker.py -k nufintech -v`
Expected: FAIL — `ImportError: cannot import name 'parse_nufintech_yaml'`

- [ ] **Step 3: Write minimal implementation**

Add `import yaml` to the top of `scripts/parse_tracker.py`, then append:

```python
# HW routes to hardware even though this is a quant-only repo — the
# established convention, and the bug fixed by hand in 0fdf5dd.
_NUFINTECH_ROLES = {
    "QR": ("quant", "Quantitative Researcher Intern"),
    "QD": ("quant", "Quantitative Developer Intern"),
    "QT": ("quant", "Quantitative Trader Intern"),
    "SWE": ("swe", "Software Engineer Intern"),
    "HW": ("hardware", "Hardware Engineer Intern"),
}


def parse_nufintech_yaml(text):
    """Parse one northwesternfintech data/<company>.yaml file.

    Emits `category` directly from the role_type code rather than leaving it
    to the classifier. Never sets closed_marker: the source has no status
    field, so a vanished role is disappearance, which this repo does not
    auto-close on."""
    doc = yaml.safe_load(text) or {}
    company = doc.get("name")
    location = doc.get("locations")
    postings = []
    for role in doc.get("roles") or []:
        mapped = _NUFINTECH_ROLES.get(role.get("role_type"))
        if not mapped:
            continue
        category, base_role = mapped
        for link in role.get("links") or []:
            url = link.get("url")
            if not url:
                continue
            label = (link.get("label") or "").strip()
            postings.append({
                "company": company,
                "role": f"{base_role}, {label}" if label else base_role,
                "location": location,
                "link": url,
                "term": "Summer 2027",
                "degree": ["BS"],
                "closed_marker": False,
                "category": category,
            })
    return postings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_parse_tracker.py -v`
Expected: PASS — 17 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/parse_tracker.py tests/test_parse_tracker.py
git commit -m "feat: northwesternfintech YAML parser with role-code mapping"
```

---

### Task 6: Markdown pipe-table parser

Covers `speedyapply`, `sndsh404`, `zapplyjobs`, `chieler`. Column order
differs per tracker, so the parser reads the header row rather than assuming
positions.

**Files:**
- Modify: `scripts/parse_tracker.py`
- Modify: `tests/test_parse_tracker.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_parse_tracker.py`, adding `parse_pipe_table` to the import:

```python
def test_parse_pipe_table_reads_columns_by_header_name():
    text = """
| Company | Role | Location | Application/Link | Date Posted |
| --- | --- | --- | --- | --- |
| Acme | Software Engineer Intern | New York, NY | <a href="https://e.com/1">Apply</a> | Jul 24 |
"""
    postings = parse_pipe_table(text)
    assert len(postings) == 1
    p = postings[0]
    assert p["company"] == "Acme"
    assert p["role"] == "Software Engineer Intern"
    assert p["location"] == "New York, NY"
    assert p["link"] == "https://e.com/1"


def test_parse_pipe_table_handles_alternate_column_order():
    text = """
| Company | Role | Posted | Applied | Link |
| --- | --- | --- | --- | --- |
| Acme | SWE Intern | 2026-07-24 | — | [Apply](https://e.com/2) |
"""
    postings = parse_pipe_table(text)
    assert postings[0]["link"] == "https://e.com/2"
    assert postings[0]["company"] == "Acme"


def test_parse_pipe_table_resolves_carry_forward_arrow():
    # A leading ↳ means "same company as the row above".
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Jane Street | Software Engineer Intern | New York, NY | <a href="https://e.com/1">Apply</a> |
| ↳ | Hardware Engineer Intern | New York, NY | <a href="https://e.com/2">Apply</a> |
"""
    postings = parse_pipe_table(text)
    assert [p["company"] for p in postings] == ["Jane Street", "Jane Street"]


def test_parse_pipe_table_collapses_multi_location_to_first():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| HRT | SWE Intern | Austin, TX</br>Chicago, IL | <a href="https://e.com/1">Apply</a> |
"""
    assert parse_pipe_table(text)[0]["location"] == "Austin, TX"


def test_parse_pipe_table_collapses_details_block_to_first_location():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Google | SWE Intern | <details><summary>**30 locations**</summary>Mountain View, CA</br>Atlanta, GA</details> | <a href="https://e.com/1">Apply</a> |
"""
    assert parse_pipe_table(text)[0]["location"] == "Mountain View, CA"


def test_parse_pipe_table_sets_closed_marker_from_lock_emoji():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern 🔒 | NY, NY | <a href="https://e.com/1">Apply</a> |
"""
    p = parse_pipe_table(text)[0]
    assert p["closed_marker"] is True
    assert "🔒" not in p["role"]


def test_parse_pipe_table_skips_rows_without_a_link():
    text = """
| Company | Role | Location | Link |
| --- | --- | --- | --- |
| Acme | SWE Intern | NY, NY | Closed |
"""
    assert parse_pipe_table(text) == []


def test_parse_pipe_table_ignores_non_job_tables():
    # sndsh404's README carries resume/interview-prep tables after the list.
    text = """
| Resource | Link |
| --- | --- |
| Book | <a href="https://e.com/b">Buy</a> |
"""
    assert parse_pipe_table(text) == []


def test_parse_pipe_table_on_real_fixture_yields_postings():
    postings = parse_pipe_table(_fixture("speedyapply.md"))
    assert postings, "expected postings from the speedyapply fixture"
    for p in postings:
        assert p["company"] and p["role"] and p["link"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_parse_tracker.py -k pipe -v`
Expected: FAIL — `ImportError: cannot import name 'parse_pipe_table'`

- [ ] **Step 3: Write minimal implementation**

Add `import re` to the top of `scripts/parse_tracker.py`, then append:

```python
_COLUMN_ALIASES = {
    "company": "company", "company name": "company",
    "role": "role", "position": "role", "job": "role", "title": "role",
    "location": "location", "locations": "location",
    "link": "link", "application": "link", "application/link": "link",
    "apply": "link", "application link": "link",
}
_HREF = re.compile(r'href="([^"]+)"')
_MD_LINK = re.compile(r"\[[^\]]*\]\((<?)([^)>\s]+)")
_TAG = re.compile(r"<[^>]+>")


def _cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _first_location(cell):
    """Collapse a location cell to its first entry. Handles </br>-joined
    lists and <details> blocks wrapping many locations."""
    text = re.sub(r"<summary>.*?</summary>", "", cell, flags=re.DOTALL)
    text = re.split(r"</br>|<br\s*/?>", text)[0]
    return _TAG.sub("", text).strip()


def _extract_link(cell):
    m = _HREF.search(cell)
    if m:
        return m.group(1)
    m = _MD_LINK.search(cell)
    if m:
        return m.group(2)
    return None


def parse_pipe_table(text):
    """Parse every Markdown pipe table in a README that looks like a job
    table, i.e. whose header maps to at least company, role and link.

    Column order differs across trackers, so columns are located by header
    name. Tables that don't match (resource lists, prep links) are skipped."""
    postings = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip().startswith("|"):
            i += 1
            continue
        header = {}
        for idx, name in enumerate(_cells(line)):
            key = _COLUMN_ALIASES.get(_TAG.sub("", name).strip().lower())
            if key and key not in header:
                header[key] = idx
        if not {"company", "role", "link"} <= set(header):
            i += 1
            continue
        i += 1
        if i < len(lines) and re.match(r"^\|[\s\-:|]+\|?$", lines[i].strip()):
            i += 1
        last_company = None
        while i < len(lines) and lines[i].strip().startswith("|"):
            cells = _cells(lines[i])
            i += 1
            if max(header.values()) >= len(cells):
                continue
            link = _extract_link(cells[header["link"]])
            if not link:
                continue
            company = _TAG.sub("", cells[header["company"]]).strip().strip("*")
            if company in ("↳", "|↳", ""):
                company = last_company
            else:
                last_company = company
            role = _TAG.sub("", cells[header["role"]]).strip()
            closed = "🔒" in role
            role = role.replace("🔒", "").replace("🛂", "").replace("🇺🇸", "")
            role = role.replace("🔥", "").replace("🎓", "").strip()
            location = (
                _first_location(cells[header["location"]])
                if "location" in header else None
            )
            if not (company and role):
                continue
            postings.append({
                "company": company,
                "role": role,
                "location": location,
                "link": link,
                "term": "Summer 2027",
                "degree": ["BS"],
                "closed_marker": closed,
            })
    return postings
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_parse_tracker.py -v`
Expected: PASS — 26 passed

- [ ] **Step 5: Verify the other three real fixtures parse**

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from parse_tracker import parse_pipe_table
for name in ['speedyapply', 'sndsh404', 'zapplyjobs', 'chieler']:
    text = Path(f'tests/fixtures/{name}.md').read_text()
    got = parse_pipe_table(text)
    print(f'{name}: {len(got)} postings')
    assert got, f'{name} produced nothing'
"
```

Expected: four lines, each with a non-zero count. If any is zero, inspect
that fixture's header row and add its column spelling to `_COLUMN_ALIASES`.

- [ ] **Step 6: Commit**

```bash
git add scripts/parse_tracker.py tests/test_parse_tracker.py
git commit -m "feat: header-driven markdown pipe-table parser"
```

---

### Task 7: Tracker config and scrape state

**Files:**
- Modify: `sources/github_trackers.yaml`
- Create: `sources/scrape_state.yaml`

- [ ] **Step 1: Rewrite the tracker config with per-tracker parse settings**

Replace the contents of `sources/github_trackers.yaml`:

```yaml
# GitHub tracker repos scraped by a plain "scrape" / "scrape <category>"
# (see docs/SCRAPING.md). `handle` is the short name that shows up in
# data/*.yaml's `sources` field as `github_tracker:<handle>`.
#
# `path` is the file (or directory) actually parsed — five trackers publish
# structured exports, which are both cheaper and more reliable than their
# rendered README. `fmt` selects the parser in scripts/parse_tracker.py.
# See docs/superpowers/specs/2026-07-24-cheap-tracker-scraping-design.md.
- handle: simplifyjobs
  repo: SimplifyJobs/Summer2026-Internships
  branch: dev
  path: .github/scripts/listings.json
  fmt: cvrve_json
  term_field: terms
  term_value: Summer 2027
  # The rendered README is the Summer 2026 list — only this JSON carries 2027.
- handle: suryaharikrishnan
  repo: SuryaHarikrishnan/internship-tracker
  branch: master
  path: data/listings.json
  fmt: cvrve_json
  term_field: terms
  term_value: Summer 2027
- handle: vanshb03
  repo: vanshb03/Summer2027-Internships
  branch: dev
  path: .github/scripts/listings.json
  fmt: cvrve_json
  term_field: season
  term_value: Summer
  term_out: Summer 2027
- handle: zshah101
  repo: zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships
  branch: main
  path: data/jobs.json
  fmt: zshah_json
  term_value: Summer 2027
- handle: northwesternfintech
  repo: northwesternfintech/2027QuantInternships
  branch: main
  path: data
  fmt: nufintech_yaml
- handle: speedyapply
  repo: speedyapply/2027-SWE-College-Jobs
  branch: main
  path: README.md
  fmt: pipe_table
- handle: sndsh404
  repo: sndsh404/summer-2027-internships
  branch: main
  path: README.md
  fmt: pipe_table
- handle: zapplyjobs
  repo: zapplyjobs/Internships-2027
  branch: main
  path: README.md
  fmt: pipe_table
- handle: chieler
  repo: Chieler/Summer-2027-SWE-Internships
  branch: main
  path: README.md
  fmt: pipe_table
```

- [ ] **Step 2: Create the empty scrape-state file**

```bash
cat > sources/scrape_state.yaml <<'EOF'
# Per-tracker record of the commit SHA last parsed, written by
# scripts/fetch_trackers.py. A tracker whose SHA is unchanged is skipped
# entirely — no fetch, no parse. A missing entry means "never scraped" and
# falls through to a full parse.
#
# Committed (not gitignored) so the skip survives across machines.
# row_count is the last successful posting count, used as the sanity
# baseline: a run yielding under half of it is treated as an upstream
# format change rather than as truth.
{}
EOF
python3 -c "import yaml; print(yaml.safe_load(open('sources/scrape_state.yaml')))"
```

Expected: `{}`

- [ ] **Step 3: Verify both files parse**

```bash
python3 -c "
import yaml
t = yaml.safe_load(open('sources/github_trackers.yaml'))
assert len(t) == 9, f'expected 9 trackers, got {len(t)}'
for e in t:
    assert {'handle','repo','branch','path','fmt'} <= set(e), e
print('9 trackers OK')
"
```

Expected: `9 trackers OK`

- [ ] **Step 4: Commit**

```bash
git add sources/github_trackers.yaml sources/scrape_state.yaml
git commit -m "feat: per-tracker parse config and scrape-state file"
```

---

### Task 8: Unclassified-rows guard in the merge entrypoint

Built before the network shim so the shim has a guard to write into.

**Files:**
- Modify: `scripts/run_scrape_merge.py`
- Modify: `tests/test_run_scrape_merge.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_scrape_merge.py`:

```python
import json
import pytest
from run_scrape_merge import run


def test_run_refuses_to_merge_while_unclassified_rows_are_pending(tmp_path):
    # An unclassified row silently defaulting to a category is how
    # cross-category duplicates get created, so merge must stop instead.
    reports = tmp_path / "fetch_reports"
    reports.mkdir()
    unclassified = reports / "unclassified.json"
    unclassified.write_text(json.dumps([
        {"link": "https://e.com/1", "role": "Summer Intern", "category": None}
    ]))
    with pytest.raises(SystemExit) as exc:
        run(reports, data_dir=tmp_path / "data", readme_path=tmp_path / "README.md")
    assert "unclassified" in str(exc.value)


def test_run_proceeds_when_unclassified_file_is_empty(tmp_path):
    reports = tmp_path / "fetch_reports"
    reports.mkdir()
    (reports / "unclassified.json").write_text("[]")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    run(reports, data_dir=data_dir, readme_path=tmp_path / "README.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_run_scrape_merge.py -k unclassified -v`
Expected: FAIL — `DID NOT RAISE <class 'SystemExit'>`

- [ ] **Step 3: Write minimal implementation**

In `scripts/run_scrape_merge.py`, inside `run()`, immediately after
`today = date.today().isoformat()`:

```python
    unclassified_path = reports_dir / "unclassified.json"
    if unclassified_path.exists():
        pending = json.loads(unclassified_path.read_text())
        pending = [p for p in pending if not p.get("category")]
        if pending:
            raise SystemExit(
                f"{len(pending)} unclassified posting(s) pending in "
                f"{unclassified_path}. Fill in each row's 'category' before "
                f"merging — defaulting a category silently creates "
                f"cross-category duplicates."
            )
```

Then, so the file is not re-read as a fetch report, change the report glob loop:

```python
    for p in sorted(reports_dir.glob("*.json")):
        if p.name == "unclassified.json":
            continue
        report = json.loads(p.read_text())
        by_cat[report["category"]].append(_filter_postings(report))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_run_scrape_merge.py -v`
Expected: PASS — all tests in the file, including the two new ones

- [ ] **Step 5: Commit**

```bash
git add scripts/run_scrape_merge.py tests/test_run_scrape_merge.py
git commit -m "feat: block merge while unclassified postings are pending"
```

---

### Task 9: Network shim

**Files:**
- Create: `scripts/fetch_trackers.py`

Untested by design, matching `scripts/check_links.py` — all logic it depends
on is already covered by Tasks 1 and 3–6.

- [ ] **Step 1: Write the shim**

```python
# scripts/fetch_trackers.py
"""Network driver for parse_tracker.py. Untested, like the rest of scraping
(see docs/SCRAPING.md) — the parsers and category rules it calls are tested
in tests/test_parse_tracker.py and tests/test_categorize.py.

Three cost tiers: skip a tracker whose commit SHA is unchanged; parse the
rest deterministically; hand only rows no rule could categorize to the
session via scratch/fetch_reports/unclassified.json. Writes fetch reports
only — never data/*.yaml. Run scripts/run_scrape_merge.py afterward."""
import json
import ssl
import sys
import urllib.request
import urllib.error
import certifi
import yaml
from collections import defaultdict
from datetime import date
from pathlib import Path

from categorize import assign_category, known_link_categories, DROP
from parse_tracker import (
    parse_cvrve_json,
    parse_zshah_json,
    parse_nufintech_yaml,
    parse_pipe_table,
)

ROOT = Path(__file__).resolve().parent.parent
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "internship-tracker-scraper", "Accept": "*/*"}


def _get(url, as_json=False):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if as_json else body


def _latest_sha(repo, path, branch):
    url = (f"https://api.github.com/repos/{repo}/commits"
           f"?path={path}&sha={branch}&per_page=1")
    data = _get(url, as_json=True)
    return data[0]["sha"] if data else None


def _raw(repo, branch, path):
    return _get(f"https://raw.githubusercontent.com/{repo}/{branch}/{path}")


def _parse(cfg):
    """Fetch and parse one tracker. Returns a list of postings."""
    fmt = cfg["fmt"]
    if fmt == "cvrve_json":
        return parse_cvrve_json(
            _raw(cfg["repo"], cfg["branch"], cfg["path"]),
            term_field=cfg["term_field"],
            term_value=cfg["term_value"],
            term_out=cfg.get("term_out"),
        )
    if fmt == "zshah_json":
        return parse_zshah_json(
            _raw(cfg["repo"], cfg["branch"], cfg["path"]),
            season=cfg["term_value"],
        )
    if fmt == "pipe_table":
        return parse_pipe_table(_raw(cfg["repo"], cfg["branch"], cfg["path"]))
    if fmt == "nufintech_yaml":
        # One recursive listing, not 59 separate content fetches.
        tree = _get(
            f"https://api.github.com/repos/{cfg['repo']}/git/trees/"
            f"{cfg['branch']}?recursive=1", as_json=True
        )
        postings = []
        for node in tree.get("tree", []):
            p = node["path"]
            if p.startswith(f"{cfg['path']}/") and p.endswith((".yaml", ".yml")):
                postings.extend(
                    parse_nufintech_yaml(_raw(cfg["repo"], cfg["branch"], p))
                )
        return postings
    raise ValueError(f"unknown fmt {fmt!r}")


def run(out_dir=None):
    out_dir = Path(out_dir) if out_dir else ROOT / "scratch" / "fetch_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    trackers = yaml.safe_load((ROOT / "sources" / "github_trackers.yaml").read_text())
    state_path = ROOT / "sources" / "scrape_state.yaml"
    state = yaml.safe_load(state_path.read_text()) or {}
    known = known_link_categories()
    unclassified = []

    for cfg in trackers:
        handle = cfg["handle"]
        prior = state.get(handle) or {}
        try:
            sha = _latest_sha(cfg["repo"], cfg["path"], cfg["branch"])
        except Exception as e:
            print(f"[{handle}] warn: SHA check failed ({e}); parsing anyway")
            sha = None

        if sha and sha == prior.get("sha"):
            print(f"[{handle}] unchanged, skipped")
            continue

        try:
            postings = _parse(cfg)
        except Exception as e:
            print(f"[{handle}] warn: parse failed ({e}). Falling back to the "
                  f"LLM subagent README parse for this tracker — see "
                  f"docs/SCRAPING.md. SHA not advanced.")
            continue

        baseline = prior.get("row_count") or 0
        if not postings or (baseline and len(postings) < baseline / 2):
            print(f"[{handle}] warn: yielded {len(postings)} postings vs "
                  f"baseline {baseline} — treating as an upstream format "
                  f"change. No report written, SHA not advanced.")
            continue

        by_cat = defaultdict(list)
        for p in postings:
            category = p.pop("category", None) or assign_category(p, known)
            if category == DROP:
                continue
            if not category:
                unclassified.append({**p, "handle": handle, "category": None})
                continue
            p["source"] = f"github_tracker:{handle}"
            by_cat[category].append(p)

        for category, rows in by_cat.items():
            report = {
                "category": category,
                "source_entity": f"github_tracker:{handle}",
                "postings": rows,
            }
            (out_dir / f"{handle}_{category}.json").write_text(
                json.dumps(report, indent=1)
            )

        state[handle] = {
            "path": cfg["path"],
            "sha": sha,
            "scraped_at": date.today().isoformat(),
            "row_count": len(postings),
        }
        print(f"[{handle}] {len(postings)} postings across "
              f"{len(by_cat)} categor(ies)")

    (out_dir / "unclassified.json").write_text(json.dumps(unclassified, indent=1))
    state_path.write_text(yaml.safe_dump(state, sort_keys=True))
    if unclassified:
        print(f"\n{len(unclassified)} posting(s) need a category. Fill in "
              f"'category' for each in {out_dir / 'unclassified.json'}, then "
              f"run scripts/run_scrape_merge.py.")
    return unclassified


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
```

- [ ] **Step 2: Verify the full suite still passes**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — 33 original tests plus the ones added in Tasks 1, 3–6, 8

- [ ] **Step 3: Dry-run against the live trackers**

```bash
mkdir -p scratch/dryrun && rm -f scratch/dryrun/*.json
python3 scripts/fetch_trackers.py scratch/dryrun
```

Expected: a line per tracker with a posting count; no traceback. On the
first run nothing is skipped (state is empty). Confirm `simplifyjobs`
reports roughly 107 postings — the roles currently missed entirely.

- [ ] **Step 4: Confirm the SHA skip works on a second run**

```bash
python3 scripts/fetch_trackers.py scratch/dryrun
```

Expected: every tracker prints `unchanged, skipped`.

- [ ] **Step 5: Commit**

```bash
git checkout sources/scrape_state.yaml   # discard dry-run state
rm -rf scratch/dryrun
git add scripts/fetch_trackers.py
git commit -m "feat: tracker fetch shim with SHA skip and format dispatch"
```

---

### Task 10: Update the runbook

**Files:**
- Modify: `docs/SCRAPING.md`

- [ ] **Step 1: Replace the "Source -> tool" table's first row**

Replace the GitHub-trackers row (the one beginning
`| **The GitHub tracker repos in \`sources/github_trackers.yaml\`...`) with:

```markdown
| **The GitHub tracker repos in `sources/github_trackers.yaml` (default source, see Trigger)** | `python3 scripts/fetch_trackers.py` | Deterministic — no LLM. Skips any tracker whose commit SHA is unchanged since `sources/scrape_state.yaml`, then parses the file named by that tracker's `path` (five publish structured JSON/YAML exports; four are parsed from their README table). Writes fetch reports plus `scratch/fetch_reports/unclassified.json`. |
```

- [ ] **Step 2: Add a section after "Link liveness check"**

```markdown
## Tracker parsing

As of 2026-07-24 the nine GitHub trackers are parsed deterministically by
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
   matched land in `scratch/fetch_reports/unclassified.json`. Fill in each
   row's `category`, then run `run_scrape_merge.py` — it refuses to merge
   while any remain blank.

**A link already in `data/*.yaml` keeps its current category, always.**
`merge_category` dedupes within one category file only, so a link that
changed category would exist in two files with nothing to catch it.

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
```

- [ ] **Step 3: Update the "Run procedure" first two steps**

Replace steps 1 and 2 with:

```markdown
1. Run `python3 scripts/fetch_trackers.py` for the GitHub trackers. It
   writes fetch reports itself. For any other (opt-in) source, dispatch
   scraping per source as before — those subagents **return parsed postings
   only** and never write data files.
2. The parent writes one fetch-report JSON per non-tracker source entity
   into `scratch/fetch_reports/`, and fills in any `category` left blank in
   `scratch/fetch_reports/unclassified.json`.
```

- [ ] **Step 4: Verify the doc has no stale instruction**

```bash
grep -n "Fetch the raw README, parse the table rows" docs/SCRAPING.md || echo "stale line gone"
```

Expected: `stale line gone`

- [ ] **Step 5: Commit**

```bash
git add docs/SCRAPING.md
git commit -m "docs: runbook for deterministic tracker parsing"
```

---

### Task 11: First real run

- [ ] **Step 1: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: PASS, no failures

- [ ] **Step 2: Fetch**

```bash
rm -f scratch/fetch_reports/*.json
python3 scripts/fetch_trackers.py
```

Expected: per-tracker counts. Note how many postings need classification.

- [ ] **Step 3: Classify any pending rows**

Open `scratch/fetch_reports/unclassified.json`, set each entry's `category`
to one of `swe, quant, data_science, ai_ml, hardware, actuarial, consulting,
ib`, and save. If the file is `[]`, skip this step.

- [ ] **Step 4: Merge**

```bash
python3 scripts/run_scrape_merge.py scratch/fetch_reports
```

Expected: per-category `+N new` lines. `simplifyjobs`' ~107 Summer 2027
postings land here, most of them new.

- [ ] **Step 5: Verify no cross-category duplicate links**

```bash
python3 -c "
import yaml, glob, sys
sys.path.insert(0, 'scripts')
from normalize import normalize_link
seen = {}
dupes = []
for f in glob.glob('data/*.yaml'):
    for r in (yaml.safe_load(open(f)) or []):
        k = normalize_link(r['link'])
        if k in seen and seen[k] != f:
            dupes.append((k, seen[k], f))
        seen[k] = f
print('cross-category duplicate links:', len(dupes))
for d in dupes[:10]: print(' ', d)
assert not dupes, 'category instability introduced duplicates'
"
```

Expected: `cross-category duplicate links: 0`

- [ ] **Step 6: Confirm no wrong-cycle rows entered**

```bash
python3 -c "
import yaml, glob
from collections import Counter
terms = Counter()
for f in glob.glob('data/*.yaml'):
    for r in (yaml.safe_load(open(f)) or []):
        terms[r.get('term')] += 1
print(terms.most_common())
assert set(terms) == {'Summer 2027'}, 'wrong-cycle rows leaked in'
"
```

Expected: `[('Summer 2027', N)]` with N noticeably above 697.

- [ ] **Step 7: Commit**

```bash
rm -f scratch/fetch_reports/*.json
git add -A
git commit -m "scrape: deterministic tracker parse, recover SimplifyJobs Summer 2027 rows"
```

**Do not push.** Pushing is Tony's action alone.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Tier 0 skip / `scrape_state.yaml` | 7, 9 |
| Tier 1 deterministic parsing | 3, 4, 5, 6 |
| Tier 2 link gate + category stability | 1, 9 |
| Per-tracker config table | 7 |
| Term filtering | 3, 4, 7, 11 |
| SimplifyJobs backfill | 7, 11 |
| Category assignment (3 precedence levels) | 1, 5 |
| Unmapped upstream category → classifier | 1 |
| northwesternfintech no closure | 5 |
| Field defaults (degree/location/date) | 3, 4, 5, 6 |
| Failure modes (404, row-count, SHA hold) | 9 |
| Who runs the classifier | 8, 9 |
| Testing / fixtures | 2, 3–6 |
| `docs/SCRAPING.md` update | 10 |

**Type consistency:** `parse_cvrve_json(text, term_field, term_value, term_out)`,
`parse_zshah_json(text, season)`, `parse_nufintech_yaml(text)`,
`parse_pipe_table(text)` — call sites in Task 9 match Tasks 3–6.
`assign_category(posting, known)`, `known_link_categories(data_dir=None)`,
`classify_role(role)`, `map_upstream_category(value, role)`, `DROP` — all
defined in Task 1 and used consistently in Task 9.

**Known follow-up:** `merge.py` silently substitutes the scrape date when a
source has no real `date_posted`. This plan does not change that behavior;
it remains open for Tony's call.
