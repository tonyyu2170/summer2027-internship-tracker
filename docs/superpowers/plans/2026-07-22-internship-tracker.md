# Summer 2027 Internship Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic, tested core of a US-only Summer 2027 internship tracker — a per-category YAML data store, a link-normalized dedupe-and-merge engine, and a README generator — plus a scraping runbook, so that `scrape` runs feed parsed postings through tested code into a generated README.

**Architecture:** A tested core (pure Python: normalization, schema validation, merge engine, README generation) is cleanly separated from the fragile, source-specific network scraping. The two meet at a JSON "fetch report" contract: scraping subagents emit fetch reports; the parent session runs one serialized `run_scrape_merge` pass that merges them into `data/*.yaml` and regenerates `README.md`. All network code lives in a documented runbook, never in the tested core.

**Tech Stack:** Python 3.12, PyYAML 6.0.2 (data I/O), jsonschema 4.26.0 (row validation), pytest 8.3.4 (tests). Firecrawl MCP + Playwright + claude-in-chrome are scraping tools, exercised only via the runbook — no tests depend on them.

**Scope note:** Per the approved spec (`docs/superpowers/specs/2026-07-22-internship-tracker-design.md`), disappearance-based auto-closing (`miss_count`, completeness gate, row-count sanity) is **deferred**. Closed status is driven only by in-line source markers and manual edits. Do not build the deferred machinery.

---

## File structure

- `scripts/normalize.py` — pure helpers: `normalize_link`, `normalize_company`, `canonicalize_location`, `is_us_location`.
- `scripts/schema.py` — `ROW_SCHEMA` (jsonschema) + `validate_row`.
- `scripts/merge.py` — `merge_category(existing_rows, fetch_reports, today) -> (rows, summary)`; the dedupe/merge engine.
- `scripts/generate_readme.py` — `render(data_dir, readme_path)`; YAML → README.md.
- `scripts/run_scrape_merge.py` — parent orchestration entrypoint; the single serialized writer.
- `data/{swe,quant,data_science,ai_ml,hardware,actuarial,consulting,ib}.yaml` — per-category role stores.
- `sources/companies.yaml` — per-category watch-list.
- `docs/SCRAPING.md` — the scraping runbook + fetch-report contract.
- `conftest.py` — puts `scripts/` on `sys.path` for tests.
- `tests/test_{normalize,schema,merge,generate_readme,run_scrape_merge}.py`.
- `.gitignore`.

---

### Task 1: Scaffolding

**Files:**
- Create: `.gitignore`, `conftest.py`, `data/*.yaml` (8 empty), `sources/companies.yaml`, `scripts/` + `tests/` dirs

- [ ] **Step 1: Create `.gitignore`**

```
.env
.venv/
__pycache__/
*.pyc
.pytest_cache/
scratch/
.DS_Store
```

- [ ] **Step 2: Create `conftest.py` at repo root**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
```

- [ ] **Step 3: Create the 8 empty data files**

Each of `data/swe.yaml`, `data/quant.yaml`, `data/data_science.yaml`, `data/ai_ml.yaml`, `data/hardware.yaml`, `data/actuarial.yaml`, `data/consulting.yaml`, `data/ib.yaml` contains exactly:

```yaml
[]
```

- [ ] **Step 4: Create `sources/companies.yaml` with a modest starter watch-list**

```yaml
# Per-category watch-list. `ats` is one of: greenhouse | lever | workday | custom.
# `url` is the board/careers URL or ATS board token. Grows over time.
quant:
  - {company: Jane Street, ats: custom, url: https://www.janestreet.com/join-jane-street/open-roles/}
  - {company: Citadel Securities, ats: greenhouse, url: citadelsecurities}
  - {company: Optiver, ats: custom, url: https://optiver.com/working-at-optiver/career-opportunities/}
  - {company: DRW, ats: greenhouse, url: drwholdingsllc}
  - {company: IMC Trading, ats: custom, url: https://careers.imc.com/us/en}
swe:
  - {company: Stripe, ats: greenhouse, url: stripe}
  - {company: Databricks, ats: greenhouse, url: databricks}
data_science: []
ai_ml: []
hardware: []
actuarial: []
consulting: []
ib: []
```

- [ ] **Step 5: Verify every data + sources file is valid YAML**

Run: `python -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('data/*.yaml')+['sources/companies.yaml']]; print('all valid')"`
Expected: `all valid`

- [ ] **Step 6: Commit**

```bash
git add .gitignore conftest.py data/ sources/
git commit -m "chore: scaffold data store, watch-list, and test bootstrap"
```

---

### Task 2: `normalize.py`

**Files:**
- Create: `scripts/normalize.py`
- Test: `tests/test_normalize.py`

- [ ] **Step 1: Write the failing tests**

```python
from normalize import normalize_link, normalize_company, canonicalize_location, is_us_location


def test_normalize_link_strips_tracking_and_trailing_slash():
    a = normalize_link("HTTPS://Boards.Greenhouse.io/janestreet/jobs/123/?utm_source=x&gh_src=y")
    b = normalize_link("https://boards.greenhouse.io/janestreet/jobs/123")
    assert a == b == "https://boards.greenhouse.io/janestreet/jobs/123"


def test_normalize_link_keeps_meaningful_query_sorted():
    assert normalize_link("https://x.com/j?b=2&a=1&utm_term=z") == "https://x.com/j?a=1&b=2"


def test_normalize_company_strips_legal_suffix():
    assert normalize_company("Jane Street Group, LLC") == "jane street"
    assert normalize_company("Stripe, Inc.") == "stripe"
    assert normalize_company("  Optiver ") == "optiver"


def test_canonicalize_location_us_forms():
    assert canonicalize_location("New York, NY") == "New York, NY"
    assert canonicalize_location("Austin, Texas") == "Austin, TX"
    assert canonicalize_location("Remote") == "Remote (US)"


def test_canonicalize_location_rejects_non_us():
    assert canonicalize_location("London, UK") is None
    assert canonicalize_location("Remote - EMEA") is None
    assert canonicalize_location("Singapore") is None


def test_is_us_location():
    assert is_us_location("Chicago, IL") is True
    assert is_us_location("Toronto, ON") is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'normalize'`

- [ ] **Step 3: Write `scripts/normalize.py`**

Note: `normalize_company` strips stacked trailing suffixes, so `"Jane Street Group, LLC"` → `"jane street"` (both `LLC` and `Group` removed). Keep the `group` token in the suffix list to match the test.

```python
"""Pure normalization helpers used by the merge engine. No I/O, no network."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "gh_src", "lever-source", "lever-origin",
}


def normalize_link(url: str) -> str:
    """Canonical application URL, used as the primary dedup key: lowercase
    scheme+host, drop fragment, strip tracking params, sort the rest, drop a
    trailing slash from the path."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    kept = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    )
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(inc|llc|corp|corporation|ltd|co|group)\.?$", re.IGNORECASE
)


def normalize_company(name: str) -> str:
    """Lowercase, collapse whitespace, strip trailing legal suffixes."""
    n = re.sub(r"\s+", " ", name.strip()).lower()
    prev = None
    while prev != n:            # strip stacked suffixes, e.g. "Group, LLC"
        prev = n
        n = _LEGAL_SUFFIX.sub("", n).strip().rstrip(",").strip()
    return n


_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_ABBREVS = set(_US_STATES.values())
_NON_US = ("emea", "apac", "uk", "europe", "canada", "india", "london",
           "singapore", "toronto", "ontario", "on")


def canonicalize_location(loc: str) -> str | None:
    """Return 'City, ST' or 'Remote (US)' when confidently US, else None.
    None means 'not confidently US' and is dropped by the US-only filter."""
    s = re.sub(r"\s+", " ", (loc or "").strip())
    if not s:
        return None
    low = s.lower()
    if "remote" in low:
        return None if any(t in low for t in _NON_US) else "Remote (US)"
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 2:
        return None
    city, tail = parts[0], parts[-1]
    if tail.upper() in _STATE_ABBREVS:
        return f"{city}, {tail.upper()}"
    if tail.lower() in _US_STATES:
        return f"{city}, {_US_STATES[tail.lower()]}"
    return None


def is_us_location(loc: str) -> bool:
    return canonicalize_location(loc) is not None
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_normalize.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/normalize.py tests/test_normalize.py
git commit -m "feat: link/company/location normalization helpers"
```

---

### Task 3: `schema.py`

**Files:**
- Create: `scripts/schema.py`
- Test: `tests/test_schema.py`

- [ ] **Step 1: Write the failing tests**

```python
from schema import validate_row

VALID = {
    "id": "jane-street-quant-trading-intern-a1b2c3",
    "company": "Jane Street",
    "role": "Quantitative Trading Intern",
    "track": "Trading",
    "location": "New York, NY",
    "link": "https://boards.greenhouse.io/janestreet/jobs/123",
    "date_posted": "2026-07-15",
    "term": "Summer 2027",
    "degree": ["BS", "MS"],
    "status": "open",
    "sources": ["greenhouse"],
    "date_added": "2026-07-22",
    "last_verified": "2026-07-22",
    "possible_duplicate_of": None,
}


def test_valid_row_has_no_errors():
    assert validate_row(VALID) == []


def test_missing_required_field_is_error():
    row = {k: v for k, v in VALID.items() if k != "company"}
    assert any("company" in e for e in validate_row(row))


def test_bad_status_enum_is_error():
    assert validate_row({**VALID, "status": "maybe"})


def test_bad_date_format_is_error():
    assert validate_row({**VALID, "date_posted": "07/15/2026"})


def test_track_is_optional():
    row = {k: v for k, v in VALID.items() if k != "track"}
    assert validate_row(row) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'schema'`

- [ ] **Step 3: Write `scripts/schema.py`**

```python
"""JSON-schema for one role row, plus a validator returning readable errors."""
from jsonschema import Draft202012Validator

_DATE = r"^\d{4}-\d{2}-\d{2}$"

ROW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "id", "company", "role", "location", "link", "date_posted", "term",
        "degree", "status", "sources", "date_added", "last_verified",
        "possible_duplicate_of",
    ],
    "properties": {
        "id": {"type": "string", "minLength": 1},
        "company": {"type": "string", "minLength": 1},
        "role": {"type": "string", "minLength": 1},
        "track": {"enum": ["Trading", "Research", "Development"]},
        "location": {"type": "string", "minLength": 1},
        "link": {"type": "string", "minLength": 1},
        "date_posted": {"type": "string", "pattern": _DATE},
        "term": {"type": "string", "minLength": 1},
        "degree": {
            "type": "array", "minItems": 1,
            "items": {"enum": ["BS", "MS", "PhD"]},
        },
        "status": {"enum": ["open", "closed"]},
        "sources": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "date_added": {"type": "string", "pattern": _DATE},
        "last_verified": {"type": "string", "pattern": _DATE},
        "possible_duplicate_of": {"type": ["string", "null"]},
    },
}

_validator = Draft202012Validator(ROW_SCHEMA)


def validate_row(row: dict) -> list[str]:
    """Return a list of 'path: message' errors ([] if the row is valid)."""
    return [
        f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
        for e in _validator.iter_errors(row)
    ]
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_schema.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/schema.py tests/test_schema.py
git commit -m "feat: row schema and validator"
```

---

### Task 4: `merge.py`

**Files:**
- Create: `scripts/merge.py`
- Test: `tests/test_merge.py`

Fetch-report shape (input): `{"category": str, "source_entity": str, "postings": [posting, ...]}`. A posting has: `company, role, location, link, term, degree` (required), and `track, date_posted, source, closed_marker` (optional). `merge_category` returns `(rows, summary)` where `summary = {"new": [...ids], "closed": [...ids], "possible_duplicates": [(new_id, existing_id), ...]}`.

- [ ] **Step 1: Write the failing tests**

```python
from merge import merge_category

TODAY = "2026-07-22"


def _posting(**kw):
    base = {
        "company": "Jane Street", "role": "Quant Trading Intern",
        "location": "New York, NY",
        "link": "https://boards.greenhouse.io/js/jobs/1",
        "term": "Summer 2027", "degree": ["BS"], "source": "greenhouse",
    }
    base.update(kw)
    return base


def _report(postings, category="quant", entity="greenhouse:js"):
    return {"category": category, "source_entity": entity, "postings": postings}


def test_new_posting_becomes_open_row():
    rows, summary = merge_category([], [_report([_posting()])], TODAY)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["date_added"] == TODAY
    assert rows[0]["sources"] == ["greenhouse"]
    assert summary["new"] == [rows[0]["id"]]


def test_missing_date_posted_falls_back_to_today():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    assert rows[0]["date_posted"] == TODAY


def test_non_us_posting_is_dropped():
    rows, summary = merge_category(
        [], [_report([_posting(location="London, UK")])], TODAY)
    assert rows == [] and summary["new"] == []


def test_same_link_across_sources_merges_and_accumulates_sources():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    # Second run: same job, different source, tracking param on the link.
    rows2, summary = merge_category(
        rows,
        [_report([_posting(source="github_tracker",
                           link="https://boards.greenhouse.io/js/jobs/1?utm_source=x")])],
        "2026-07-25")
    assert len(rows2) == 1
    assert set(rows2[0]["sources"]) == {"greenhouse", "github_tracker"}
    assert rows2[0]["last_verified"] == "2026-07-25"
    assert summary["new"] == []


def test_inline_closed_marker_sets_closed():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    rows2, summary = merge_category(
        rows, [_report([_posting(closed_marker=True)])], "2026-07-25")
    assert rows2[0]["status"] == "closed"
    assert summary["closed"] == [rows2[0]["id"]]


def test_same_triple_different_link_is_flagged_not_merged():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    rows2, summary = merge_category(
        rows,
        [_report([_posting(link="https://jobs.lever.co/js/other", source="lever")])],
        "2026-07-25")
    assert len(rows2) == 2
    new_id = summary["new"][0]
    assert (new_id, rows[0]["id"]) in summary["possible_duplicates"]
    assert next(r for r in rows2 if r["id"] == new_id)["possible_duplicate_of"] == rows[0]["id"]


def test_input_rows_not_mutated():
    rows, _ = merge_category([], [_report([_posting()])], TODAY)
    snapshot = [dict(r) for r in rows]
    merge_category(rows, [_report([_posting(closed_marker=True)])], "2026-07-25")
    assert rows == snapshot
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'merge'`

- [ ] **Step 3: Write `scripts/merge.py`**

```python
"""Deterministic dedupe-and-merge engine. Pure: no I/O, no network.

Consumes fetch reports (see docs/SCRAPING.md) plus the existing rows for one
category and returns the merged rows and a run summary. The single serialized
writer in run_scrape_merge.py calls this once per category file."""
import re
import hashlib
from normalize import normalize_link, normalize_company, canonicalize_location


def _slug(company: str, role: str, key: str) -> str:
    base = "-".join(
        re.sub(r"[^a-z0-9]+", "-", x.lower()).strip("-")
        for x in (company, role)
    )
    return f"{base}-{hashlib.sha1(key.encode()).hexdigest()[:6]}"


def _triple(item: dict):
    """Low-confidence fallback identity: (company, role, canonical location)."""
    return (
        normalize_company(item["company"]),
        item["role"].strip().lower(),
        canonicalize_location(item["location"]),
    )


def merge_category(existing_rows, fetch_reports, today):
    """existing_rows: list[dict]; fetch_reports: list[fetch-report dict];
    today: 'YYYY-MM-DD'. Returns (rows, summary)."""
    rows = [dict(r) for r in existing_rows]          # copy; never mutate input
    for r in rows:
        r["sources"] = list(r["sources"])
    by_link = {normalize_link(r["link"]): r for r in rows}
    by_triple = {_triple(r): r for r in rows}
    summary = {"new": [], "closed": [], "possible_duplicates": []}

    for report in fetch_reports:
        for p in report["postings"]:
            canon_loc = canonicalize_location(p["location"])
            if canon_loc is None:                    # US-only filter
                continue
            nlink = normalize_link(p["link"])
            src = p.get("source", report.get("source_entity", "unknown"))

            if nlink in by_link:                      # same posting, re-found
                row = by_link[nlink]
                row["last_verified"] = today
                if src not in row["sources"]:
                    row["sources"].append(src)
                if p.get("closed_marker") and row["status"] != "closed":
                    row["status"] = "closed"
                    summary["closed"].append(row["id"])
                continue

            trip = _triple({**p, "location": canon_loc})
            dup_of = by_triple[trip]["id"] if trip in by_triple else None
            row = {
                "id": _slug(p["company"], p["role"], nlink),
                "company": p["company"],
                "role": p["role"],
                "location": canon_loc,
                "link": p["link"],
                "date_posted": p.get("date_posted") or today,
                "term": p["term"],
                "degree": p["degree"],
                "status": "closed" if p.get("closed_marker") else "open",
                "sources": [src],
                "date_added": today,
                "last_verified": today,
                "possible_duplicate_of": dup_of,
            }
            if p.get("track"):
                row["track"] = p["track"]
            rows.append(row)
            by_link[nlink] = row
            by_triple.setdefault(trip, row)
            summary["new"].append(row["id"])
            if dup_of:
                summary["possible_duplicates"].append((row["id"], dup_of))

    return rows, summary
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_merge.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/merge.py tests/test_merge.py
git commit -m "feat: link-normalized dedupe-and-merge engine"
```

---

### Task 5: `generate_readme.py`

**Files:**
- Create: `scripts/generate_readme.py`
- Test: `tests/test_generate_readme.py`

- [ ] **Step 1: Write the failing tests**

```python
import yaml
from pathlib import Path
from generate_readme import render


def _write(data_dir: Path, stem: str, rows: list):
    (data_dir / f"{stem}.yaml").write_text(yaml.safe_dump(rows, sort_keys=False))


def _row(**kw):
    base = {
        "id": "x", "company": "Jane Street", "role": "Quant Trading Intern",
        "location": "New York, NY", "link": "https://x.com/j",
        "date_posted": "2026-07-15", "term": "Summer 2027", "degree": ["BS"],
        "status": "open", "sources": ["greenhouse"], "date_added": "2026-07-15",
        "last_verified": "2026-07-15", "possible_duplicate_of": None,
    }
    base.update(kw)
    return base


def _empty_data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    for stem in ("swe", "quant", "data_science", "ai_ml", "hardware",
                 "actuarial", "consulting", "ib"):
        _write(d, stem, [])
    return d


def test_render_writes_toc_and_all_headings(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "## Software Engineering" in text
    assert "## Investment Banking" in text
    assert "[Quantitative Finance](#quantitative-finance)" in text


def test_quant_table_has_track_column_and_row(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "quant", [_row(track="Trading")])
    out = tmp_path / "README.md"
    render(data_dir, out)
    text = out.read_text()
    assert "| Track |" in text
    assert "Trading" in text
    assert "[Apply](https://x.com/j)" in text


def test_closed_row_renders_lock(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [_row(status="closed")])
    render(data_dir, tmp_path / "README.md")
    assert "🔒" in (tmp_path / "README.md").read_text()


def test_rows_sorted_newest_first(tmp_path):
    data_dir = _empty_data_dir(tmp_path)
    _write(data_dir, "swe", [
        _row(company="Older", date_posted="2026-01-01"),
        _row(company="Newer", date_posted="2026-09-01"),
    ])
    render(data_dir, tmp_path / "README.md")
    text = (tmp_path / "README.md").read_text()
    assert text.index("Newer") < text.index("Older")
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_generate_readme.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'generate_readme'`

- [ ] **Step 3: Write `scripts/generate_readme.py`**

```python
"""Render README.md from data/*.yaml. Pure transform; writes one file."""
import yaml
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent

CATEGORIES = [                       # (yaml stem, display title, is_quant)
    ("swe", "Software Engineering", False),
    ("quant", "Quantitative Finance", True),
    ("data_science", "Data Science", False),
    ("ai_ml", "AI/ML", False),
    ("hardware", "Hardware Engineering", False),
    ("actuarial", "Actuarial", False),
    ("consulting", "Consulting", False),
    ("ib", "Investment Banking", False),
]


def _anchor(title: str) -> str:
    return title.lower().replace(" ", "-").replace("/", "")


def _status_cell(row: dict) -> str:
    cell = "🔒 Closed" if row.get("status") == "closed" else "🟢 Open"
    if row.get("possible_duplicate_of"):
        cell += f" ⚠️dup?({row['possible_duplicate_of']})"
    return cell


def _row_cells(row: dict, is_quant: bool) -> str:
    cells = [row["company"], row["role"]]
    if is_quant:
        cells.append(row.get("track", ""))
    cells += [
        row["location"],
        f"[Apply]({row['link']})",
        row["date_posted"],
        row["term"],
        "/".join(row["degree"]),
        _status_cell(row),
    ]
    return "| " + " | ".join(cells) + " |"


def _table(rows: list, is_quant: bool) -> str:
    header = ["Company", "Role"] + (["Track"] if is_quant else []) + \
        ["Location", "Link", "Date Posted", "Term", "Degree", "Status"]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for r in sorted(rows, key=lambda r: r["date_posted"], reverse=True):
        lines.append(_row_cells(r, is_quant))
    return "\n".join(lines)


def _load(data_dir: Path, stem: str) -> list:
    p = data_dir / f"{stem}.yaml"
    return (yaml.safe_load(p.read_text()) or []) if p.exists() else []


def render(data_dir=None, readme_path=None) -> Path:
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    out = [
        "# Summer 2027 Internship Tracker",
        "",
        f"_Last updated: {date.today().isoformat()}_",
        "",
        "US-based Summer 2027 internships across eight role categories. "
        "A market listing — not a personal application tracker.",
        "",
        "## Contents",
        "",
    ]
    out += [f"- [{title}](#{_anchor(title)})" for _, title, _ in CATEGORIES]
    out += [
        "",
        "**Legend** — Status: 🟢 Open · 🔒 Closed. Degree = BS/MS/PhD "
        "eligibility. Date Posted is the source's date where available, else "
        "the date we first recorded the role. ⚠️dup? marks a possible "
        "duplicate pending manual review.",
        "",
    ]
    for stem, title, is_quant in CATEGORIES:
        rows = _load(data_dir, stem)
        out += [f"## {title}", ""]
        out += [_table(rows, is_quant), ""] if rows else ["_No roles tracked yet._", ""]
    readme_path.write_text("\n".join(out) + "\n")
    return readme_path


if __name__ == "__main__":
    print(f"Wrote {render()}")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_generate_readme.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Generate the initial (empty) README and commit**

Run: `python scripts/generate_readme.py`
Expected: `Wrote .../README.md`

```bash
git add scripts/generate_readme.py tests/test_generate_readme.py README.md
git commit -m "feat: README generator + initial empty README"
```

---

### Task 6: `run_scrape_merge.py` orchestration

**Files:**
- Create: `scripts/run_scrape_merge.py`
- Test: `tests/test_run_scrape_merge.py`

- [ ] **Step 1: Write the failing test**

```python
import json
import yaml
from pathlib import Path
from run_scrape_merge import run


def test_run_merges_reports_and_regenerates_readme(tmp_path):
    root = tmp_path
    data_dir = root / "data"
    data_dir.mkdir()
    for stem in ("swe", "quant", "data_science", "ai_ml", "hardware",
                 "actuarial", "consulting", "ib"):
        (data_dir / f"{stem}.yaml").write_text("[]\n")
    reports_dir = root / "reports"
    reports_dir.mkdir()
    (reports_dir / "r1.json").write_text(json.dumps({
        "category": "swe", "source_entity": "greenhouse:stripe",
        "postings": [{
            "company": "Stripe", "role": "SWE Intern", "location": "New York, NY",
            "link": "https://x.com/j", "term": "Summer 2027", "degree": ["BS"],
            "source": "greenhouse",
        }],
    }))
    readme = root / "README.md"

    summaries = run(reports_dir, data_dir, readme)

    swe = yaml.safe_load((data_dir / "swe.yaml").read_text())
    assert len(swe) == 1 and swe[0]["company"] == "Stripe"
    assert summaries["swe"]["new"] == [swe[0]["id"]]
    assert "Stripe" in readme.read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_scrape_merge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'run_scrape_merge'`

- [ ] **Step 3: Write `scripts/run_scrape_merge.py`**

```python
"""Parent-session entrypoint: the single serialized writer for a scrape run.

Loads every fetch-report JSON in a directory (see docs/SCRAPING.md), groups
them by category, merges each category exactly once, rewrites that category's
YAML, then regenerates README.md. Prints a per-category summary. Never runs
git — committing (and pushing, which is Tony's alone) happens outside."""
import json
import sys
import yaml
from pathlib import Path
from datetime import date
from collections import defaultdict

from merge import merge_category
from generate_readme import render, ROOT


def run(reports_dir, data_dir=None, readme_path=None):
    reports_dir = Path(reports_dir)
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    today = date.today().isoformat()

    by_cat = defaultdict(list)
    for p in sorted(reports_dir.glob("*.json")):
        report = json.loads(p.read_text())
        by_cat[report["category"]].append(report)

    summaries = {}
    for cat, reports in by_cat.items():
        path = data_dir / f"{cat}.yaml"
        existing = (yaml.safe_load(path.read_text()) or []) if path.exists() else []
        rows, summary = merge_category(existing, reports, today)
        path.write_text(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
        summaries[cat] = summary
        print(f"[{cat}] +{len(summary['new'])} new, "
              f"{len(summary['closed'])} newly closed, "
              f"{len(summary['possible_duplicates'])} possible dup(s)")
        for new_id, dup_of in summary["possible_duplicates"]:
            print(f"    warn: {new_id} may duplicate {dup_of}")

    render(data_dir, readme_path)
    return summaries


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "scratch/fetch_reports")
```

- [ ] **Step 4: Run to verify pass, and the full suite**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests across the 5 files)

- [ ] **Step 5: Commit**

```bash
git add scripts/run_scrape_merge.py tests/test_run_scrape_merge.py
git commit -m "feat: serialized scrape-merge orchestration entrypoint"
```

---

### Task 7: Scraping runbook

**Files:**
- Create: `docs/SCRAPING.md`

This task is documentation — no tests. It records the procedure the assistant follows on a `scrape` request and the fetch-report contract that Task 6 consumes.

- [ ] **Step 1: Write `docs/SCRAPING.md`**

````markdown
# Scraping Runbook

The tested core (dedupe/merge/README) never touches the network. Scraping is a
per-source procedure that emits **fetch reports** — JSON files the tested
`run_scrape_merge.py` consumes. This file documents both.

## Trigger

- "scrape" -> all sources, all categories.
- "scrape <category>" -> only that category's sources.
Scraping is never scheduled; it runs only on an explicit request.

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
Locations that are not confidently US are dropped by the merge engine — no
need to pre-filter, but prefer emitting `City, ST`.

## Source -> tool

| Source | Tool | How |
|---|---|---|
| The 4 GitHub tracker repos + any others found | Raw markdown fetch | Fetch the raw README, parse the table rows; carry each row's own application href as `link`; if a row is marked closed in-line, set `closed_marker: true` |
| Company on Greenhouse | `boards-api.greenhouse.io/v1/boards/<token>/jobs?content=true` | JSON per job; `link` = `absolute_url`; parse degree from the description text; `date_posted` from the best available field, else omit |
| Company on Lever | `api.lever.co/v0/postings/<company>?mode=json` | JSON per posting; `link` = `hostedUrl`; parse degree from `description`/`lists` |
| Company on Workday / custom site | Firecrawl `scrape`/`crawl` (primary), Playwright (local fallback) | Extract postings from rendered content |
| simplify.jobs | Firecrawl `scrape`/`crawl` | Public, JS-rendered |
| Indeed | Firecrawl first (`proxy: auto`); logged-in browser tool if blocked | — |
| LinkedIn, Handshake | claude-in-chrome against Tony's logged-in session | Keep reads light and infrequent — automated activity can flag the account |

`sources/companies.yaml` is the watch-list of which company uses which ATS.
Add newly-discovered companies there (serialized through the parent, like the
data files).

## Run procedure

1. Dispatch scraping per source (parallel subagents where useful). Each
   subagent **returns parsed postings only** — it does not write data files.
2. The parent writes one fetch-report JSON per source entity into
   `scratch/fetch_reports/`.
3. Run the single serialized writer:
   `python scripts/run_scrape_merge.py scratch/fetch_reports`
   It merges per category, rewrites `data/*.yaml`, regenerates `README.md`,
   and prints new/closed/possible-duplicate counts.
4. Review any "possible duplicate" lines; resolve by hand (delete the
   duplicate row, or clear its `possible_duplicate_of`).
5. Clear `scratch/fetch_reports/` and commit:
   `git add -A && git commit -m "scrape: update roles as of <date>"`
6. **Pushing is Tony's action alone.** The assistant never runs `git push`.
````

- [ ] **Step 2: Verify the file exists and is non-empty**

Run: `test -s docs/SCRAPING.md && echo ok`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add docs/SCRAPING.md
git commit -m "docs: scraping runbook and fetch-report contract"
```

---

## Optional follow-up (not a build task)

- **Create the private GitHub remote** (authorized in the spec; the assistant creates it but never pushes):
  `gh repo create tonyyu2170/internship-tracker --private --source=. --remote=origin`
  This sets `origin` without pushing. Tony pushes when he chooses:
  `git push -u origin main`. If you'd rather create the repo through the GitHub
  web UI, skip the `gh` command and just add the remote by hand.

---

## Self-review checklist (verified while writing this plan)

- **Spec coverage:** 8 category tables (Task 5 `CATEGORIES`); required columns incl. degree/date/link (Task 5 `_row_cells`); TOC (Task 5); subdirectory repo (already scaffolded); all sources (Task 7 table); US-only filter (Task 4 drop + Task 2 `canonicalize_location`); in-line closed markers (Task 4 `closed_marker`); possible-duplicate flagging (Task 4 `possible_duplicate_of`); serialized single writer (Task 6). ✔
- **Deferred, correctly absent:** no `miss_count`, no completeness gate, no row-count sanity check anywhere in Tasks 2–7. ✔
- **Type consistency:** `merge_category(existing_rows, fetch_reports, today) -> (rows, summary)` identical in Task 4 and Task 6; `render(data_dir, readme_path)` identical in Task 5 and Task 6; row keys match `ROW_SCHEMA` (Task 3) across Tasks 4–5; `_posting` test helper includes all required posting fields. ✔
- **Push policy:** no task runs `git push`; the only push reference is Tony-run (Task 7 Step 1 procedure + optional follow-up). ✔
- **No placeholders:** every code step contains complete, runnable code; every run step has an exact command and expected output. ✔
