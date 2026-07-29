"""Explicit first-party company-source collector for configured categories.

This network shim emits fetch reports only. run_scrape_merge.py remains the
single writer for data and README files.
"""
import json
import re
import ssl
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import certifi
import yaml

from parse_company import parse_phenom_job_page

ROOT = Path(__file__).resolve().parent.parent
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "internship-tracker-scraper", "Accept": "text/html"}
_PARSERS = {"phenom_job_page": parse_phenom_job_page}


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=30, context=_SSL_CTX) as response:
        return response.read().decode("utf-8")


def _load_drop_counts(path: Path):
    if not path.exists():
        return defaultdict(Counter)
    raw = json.loads(path.read_text())
    return defaultdict(Counter, {source: Counter(counts) for source, counts in raw.items()})


def _report_name(source_entity: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", source_entity.lower()).strip("_")


def run(category: str, out_dir=None, config_path=None, state_path=None, fetch=None):
    """Fetch configured sources for one category and write fetch reports.

    `fetch` is injectable for tests. A source advances its state only after a
    non-empty, successfully parsed report is written.
    """
    out_dir = Path(out_dir) if out_dir else ROOT / "scratch" / "fetch_reports"
    config_path = Path(config_path) if config_path else ROOT / "sources" / "companies.yaml"
    state_path = Path(state_path) if state_path else ROOT / "sources" / "scrape_state.yaml"
    sources = (yaml.safe_load(config_path.read_text()) or {}).get(category)
    if sources is None:
        raise SystemExit(f"unknown company-source category {category!r}")

    out_dir.mkdir(parents=True, exist_ok=True)
    fetch = fetch or _get
    drop_counts = _load_drop_counts(out_dir / "drop_counts.json")
    state = yaml.safe_load(state_path.read_text()) if state_path.exists() else {}
    state = state or {}
    company_state = dict(state.get("company_sources") or {})
    state_changed = False

    for source in sources:
        entity = source["source_entity"]
        provider = source["provider"]
        if provider == "manual_discovery":
            drop_counts[entity]["manual_discovery"] += 1
            print(f"[{entity}] manual discovery only; no report written")
            continue
        parser = _PARSERS.get(provider)
        if not parser:
            drop_counts[entity]["unsupported_source"] += 1
            print(f"[{entity}] unsupported provider {provider!r}; no report written")
            continue
        try:
            postings = parser(fetch(source["url"]), source)
        except Exception as exc:
            drop_counts[entity]["source_parse_failed"] += 1
            print(f"[{entity}] warn: source failed ({exc}); no report written")
            continue
        if not postings:
            drop_counts[entity]["role_unmatched"] += 1
            print(f"[{entity}] no matching roles; no report written")
            continue

        report = {"category": category, "source_entity": entity, "postings": postings}
        (out_dir / f"{_report_name(entity)}_{category}.json").write_text(
            json.dumps(report, indent=1))
        company_state[entity] = {
            "provider": provider,
            "last_success": date.today().isoformat(),
            "row_count": len(postings),
        }
        state_changed = True
        print(f"[{entity}] {len(postings)} posting(s)")

    (out_dir / "drop_counts.json").write_text(json.dumps(
        {source: dict(counts) for source, counts in sorted(drop_counts.items())}, indent=1))
    if state_changed:
        state["company_sources"] = company_state
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))
    return {source: dict(counts) for source, counts in drop_counts.items()}


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 scripts/fetch_companies.py <category>")
    run(sys.argv[1])
