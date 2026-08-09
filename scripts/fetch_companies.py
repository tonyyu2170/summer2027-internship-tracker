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
from urllib.parse import urlsplit

import certifi
import yaml

from categorize import known_link_categories
from normalize import normalize_link
from parse_company import (
    parse_ashby_board,
    parse_greenhouse_board,
    parse_lever_postings,
    parse_phenom_job_page,
    parse_workday_cxs,
)

ROOT = Path(__file__).resolve().parent.parent
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "internship-tracker-scraper", "Accept": "text/html"}
_PARSERS = {
    "phenom_job_page": parse_phenom_job_page,
    "workday_cxs": parse_workday_cxs,
    "greenhouse_board": parse_greenhouse_board,
    "lever_api": parse_lever_postings,
    "ashby_api": parse_ashby_board,
}

# Legacy watch-list entries ({company, ats, url}) map onto implicit API
# providers; ATS kinds without a wired API pull are skipped with a counter.
_ATS_PROVIDERS = {"greenhouse": "greenhouse_board",
                  "lever": "lever_api",
                  "ashby": "ashby_api"}


def _normalize_source(source: dict):
    """Return a provider-shaped source for a legacy watch-list entry, the
    entry itself when already rich, or None when it isn't scrapeable."""
    if "provider" in source:
        return source
    slug = re.sub(r"[^a-z0-9]+", "-", source["company"].lower()).strip("-")
    normalized = {**source, "source_entity": f"company:{slug}"}
    if source.get("verified") is False:
        normalized["provider"] = "_unverified"
        return normalized
    normalized["provider"] = _ATS_PROVIDERS.get(source.get("ats"), "_unwired")
    return normalized


def _get(url: str) -> str:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=30, context=_SSL_CTX) as response:
        return response.read().decode("utf-8")


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**_HEADERS, "Accept": "application/json", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30, context=_SSL_CTX) as response:
        return json.loads(response.read().decode("utf-8"))


def _workday_cxs_url(source: dict) -> str:
    parts = urlsplit(source["url"])
    if not parts.scheme or not parts.netloc:
        raise ValueError("Workday source url must be absolute")
    return (f"{parts.scheme}://{parts.netloc}/wday/cxs/"
            f"{source['tenant']}/{source['site']}/jobs")


def _fetch_workday_cxs(source: dict, post=None) -> dict:
    """Fetch every page from one configured Workday CXS search endpoint."""
    post = post or _post_json
    endpoint = _workday_cxs_url(source)
    postings, offset, total = [], 0, None
    while total is None or offset < total:
        payload = post(endpoint, {
            "appliedFacets": {},
            "limit": 20,
            "offset": offset,
            "searchText": source["search_text"],
        })
        page = payload.get("jobPostings")
        if not isinstance(page, list) or not isinstance(payload.get("total"), int):
            raise ValueError("invalid Workday CXS search response")
        postings.extend(page)
        total = payload["total"]
        if len(page) == 0 and offset < total:
            raise ValueError("Workday CXS returned an empty page before total")
        offset += len(page)
    return {"jobPostings": postings}


def _get_json(url: str):
    return json.loads(_get(url))


def _fetch_source(source: dict):
    provider = source["provider"]
    if provider == "phenom_job_page":
        return _get(source["url"])
    if provider == "workday_cxs":
        return _fetch_workday_cxs(source)
    if provider == "greenhouse_board":
        return _get_json("https://boards-api.greenhouse.io/v1/boards/"
                         f"{source['url']}/jobs?content=true")
    if provider == "lever_api":
        return _get_json(f"https://api.lever.co/v0/postings/{source['url']}?mode=json")
    if provider == "ashby_api":
        return _get_json("https://api.ashbyhq.com/posting-api/job-board/"
                         f"{source['url']}?includeCompensation=false")
    raise ValueError(f"unsupported provider {source['provider']!r}")


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
    known = known_link_categories()
    drop_counts = _load_drop_counts(out_dir / "drop_counts.json")
    state = yaml.safe_load(state_path.read_text()) if state_path.exists() else {}
    state = state or {}
    company_state = dict(state.get("company_sources") or {})
    state_changed = False

    for source in sources:
        source = _normalize_source(source)
        entity = source["source_entity"]
        provider = source["provider"]
        report_path = out_dir / f"{_report_name(entity)}_{category}.json"
        if provider in ("_unwired", "_unverified"):
            report_path.unlink(missing_ok=True)
            drop_counts.pop(entity, None)
            drop_counts[entity][provider.lstrip("_") + "_source"] += 1
            continue
        # A requested collection owns its source's report and counters for
        # this run. Otherwise a failed source would leave a stale report for
        # the merge and repeated runs would inflate its drop tally.
        report_path.unlink(missing_ok=True)
        drop_counts.pop(entity, None)
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
            result = parser(fetch(source) if fetch else _fetch_source(source), source)
        except Exception as exc:
            drop_counts[entity]["source_parse_failed"] += 1
            print(f"[{entity}] warn: source failed ({exc}); no report written")
            continue
        if isinstance(result, tuple):
            postings, parser_drops = result
            drop_counts[entity].update(parser_drops)
        else:
            postings = result
            parser_drops = Counter()
        # A link the tracker pipeline already placed in another category must
        # not be re-imported under this company's watch-list category — that
        # creates a cross-category duplicate the merge can't see (categories
        # dedupe independently). Same-category known links pass through: the
        # merge refreshes the existing row.
        kept = []
        for posting in postings:
            existing_cat = known.get(normalize_link(posting["link"]))
            if existing_cat and existing_cat != category:
                drop_counts[entity]["tracked_elsewhere"] += 1
                continue
            kept.append(posting)
        postings = kept
        if not postings:
            if not parser_drops:
                drop_counts[entity]["role_unmatched"] += 1
            print(f"[{entity}] no matching roles; no report written")
            continue

        report = {"category": category, "source_entity": entity, "postings": postings}
        report_path.write_text(json.dumps(report, indent=1))
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


def _prefetched(config_path=None):
    """Concurrently fetch every scrapeable source up front; run() then
    consumes results via its injectable `fetch` parameter. Exceptions are
    stored and re-raised inside run()'s per-source try."""
    from concurrent.futures import ThreadPoolExecutor
    config_path = Path(config_path) if config_path else ROOT / "sources" / "companies.yaml"
    config = yaml.safe_load(config_path.read_text()) or {}
    sources = [_normalize_source(s) for entries in config.values() for s in entries or []]
    sources = [s for s in sources if s["provider"] in _PARSERS]
    results = {}

    def one(s):
        try:
            results[s["source_entity"]] = ("ok", _fetch_source(s))
        except Exception as exc:
            results[s["source_entity"]] = ("err", exc)

    with ThreadPoolExecutor(16) as ex:
        list(ex.map(one, sources))

    def fetch(source):
        kind, value = results[source["source_entity"]]
        if kind == "err":
            raise value
        return value
    return fetch


if __name__ == "__main__":
    categories = sys.argv[1:] or ["swe", "quant", "data_science", "ai_ml",
                                  "hardware", "actuarial"]
    fetch = _prefetched()
    for cat in categories:
        print(f"--- {cat} ---")
        run(cat, fetch=fetch)
