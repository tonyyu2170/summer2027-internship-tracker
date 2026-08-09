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

from categorize import DROP, classify_role, known_link_categories
from normalize import normalize_link
from parse_company import (
    is_intern_title,
    parse_ashby_board,
    parse_greenhouse_board,
    parse_lever_postings,
    parse_phenom_job_page,
    parse_smartrecruiters_postings,
    parse_workday_cxs,
    parse_workday_search,
)

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = ["swe", "quant", "data_science", "ai_ml", "hardware", "actuarial"]
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
_HEADERS = {"User-Agent": "internship-tracker-scraper", "Accept": "text/html"}
_PARSERS = {
    "phenom_job_page": parse_phenom_job_page,
    "workday_cxs": parse_workday_cxs,
    "workday_search": parse_workday_search,
    "greenhouse_board": parse_greenhouse_board,
    "lever_api": parse_lever_postings,
    "ashby_api": parse_ashby_board,
    "smartrecruiters_api": parse_smartrecruiters_postings,
}

# Legacy watch-list entries ({company, ats, url}) map onto implicit API
# providers; ATS kinds without a wired API pull are skipped with a counter.
_ATS_PROVIDERS = {"greenhouse": "greenhouse_board",
                  "lever": "lever_api",
                  "ashby": "ashby_api",
                  "workday": "workday_search",
                  "smartrecruiters": "smartrecruiters_api"}

_SMARTRECRUITERS_HOST = "jobs.smartrecruiters.com"
_SMARTRECRUITERS_PAGE = 100

# Workday's search ranks rather than filters, so a bare "intern" matches most
# of a board. The full phrase is what actually narrows it (Capital One:
# 1775 postings -> 5), which keeps a board to one page in practice.
_WORKDAY_SEARCH_TEXT = "Summer 2027"
_WORKDAY_PAGE = 20
_WORKDAY_MAX_PAGES = 5


def _workday_site(url: str):
    """tenant/site for a `{tenant}.wd{N}.myworkdayjobs.com/{site}` career URL,
    or None when the watch-list URL isn't that shape."""
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if not parts.netloc.lower().endswith(".myworkdayjobs.com") or not segments:
        return None
    return {"tenant": parts.netloc.split(".")[0], "site": segments[0]}


def _smartrecruiters_board(url: str):
    """Company identifier for a `jobs.smartrecruiters.com/{id}` board URL, or
    None when the watch-list URL isn't that shape."""
    parts = urlsplit(url)
    segments = [segment for segment in parts.path.split("/") if segment]
    if parts.netloc.lower() != _SMARTRECRUITERS_HOST or not segments:
        return None
    return segments[0]


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
    if normalized["provider"] == "workday_search":
        # Derived here, outside run()'s per-source try, so an off-shape URL
        # has to degrade to "unwired" rather than abort the category.
        site = _workday_site(source["url"])
        if not site:
            normalized["provider"] = "_unwired"
            return normalized
        # A vanity host can differ from the real tenant id — Workday tenants
        # can't contain "-", so osv-cci.wd1... serves tenant `osv_cci`. An entry
        # pins tenant/site itself in that case; derivation stays the default,
        # since a blanket "-" -> "_" would break a genuinely hyphenated tenant.
        site.update({key: source[key] for key in ("tenant", "site") if key in source})
        normalized.update(site, search_text=_WORKDAY_SEARCH_TEXT)
    if normalized["provider"] == "smartrecruiters_api":
        # Same reason as the Workday derivation above: an off-shape watch-list
        # URL degrades to unwired here rather than aborting the category.
        board = _smartrecruiters_board(source["url"])
        if not board:
            normalized["provider"] = "_unwired"
            return normalized
        normalized["board"] = board
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


def _get_json_api(url: str):
    """Workday's CXS endpoints 406 on the shared text/html Accept header."""
    request = urllib.request.Request(
        url, headers={**_HEADERS, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30, context=_SSL_CTX) as response:
        return json.loads(response.read().decode("utf-8"))


def _cxs_base(source: dict) -> str:
    parts = urlsplit(source["url"])
    return (f"{parts.scheme}://{parts.netloc}/wday/cxs/"
            f"{source['tenant']}/{source['site']}")


def _fetch_workday_search(source: dict, post=None, get=None) -> dict:
    """Search one Workday board, then pull the job detail behind each
    intern-titled hit.

    The search response carries no description and shows a multi-site posting
    only as "3 Locations", so Summer-2027 evidence and a US location can be
    judged solely from the detail payload. The title pre-filter and the page
    cap bound what an unexpectedly broad board costs.
    """
    post, get = post or _post_json, get or _get_json_api
    base = _cxs_base(source)
    rows, offset, total, truncated = [], 0, None, False
    while total is None or offset < total:
        payload = post(f"{base}/jobs", {
            "appliedFacets": {},
            "limit": _WORKDAY_PAGE,
            "offset": offset,
            "searchText": source["search_text"],
        })
        page = payload.get("jobPostings")
        if not isinstance(page, list) or not isinstance(payload.get("total"), int):
            raise ValueError("invalid Workday CXS search response")
        rows.extend(page)
        total = payload["total"]
        if len(page) == 0 and offset < total:
            raise ValueError("Workday CXS returned an empty page before total")
        offset += len(page)
        if len(rows) >= _WORKDAY_PAGE * _WORKDAY_MAX_PAGES:
            truncated = offset < total
            break

    jobs = []
    for row in rows:
        path = row.get("externalPath") or ""
        if not is_intern_title(row.get("title") or "") or not path.startswith("/job/"):
            continue
        jobs.append(get(base + path)["jobPostingInfo"])
    return {"jobs": jobs, "truncated": truncated}


def _fetch_smartrecruiters(source: dict, get=None) -> dict:
    """Page one SmartRecruiters board, then pull the detail behind each US
    intern-titled hit.

    SmartRecruiters ignores the params that would narrow the board server-side
    (`q=` ranks rather than filters, `experienceLevel=internship` is dropped),
    so the whole board is paged and filtered here. The list rows carry no
    description but do carry a structured location, so title plus
    `country == "us"` is enough to bound the detail fetches — 4762 Bosch
    postings cost 31 of them.
    """
    get = get or _get_json_api
    base = (f"https://api.smartrecruiters.com/v1/companies/{source['board']}/postings")
    rows, offset, total = [], 0, None
    while total is None or offset < total:
        payload = get(f"{base}?limit={_SMARTRECRUITERS_PAGE}&offset={offset}")
        page = payload.get("content")
        if not isinstance(page, list) or not isinstance(payload.get("totalFound"), int):
            raise ValueError("invalid SmartRecruiters postings response")
        rows.extend(page)
        total = payload["totalFound"]
        if len(page) == 0 and offset < total:
            raise ValueError("SmartRecruiters returned an empty page before total")
        offset += len(page)

    jobs = []
    for row in rows:
        if not is_intern_title(row.get("name") or ""):
            continue
        if (row.get("location") or {}).get("country") != "us":
            continue
        jobs.append(get(f"{base}/{row['id']}"))
    return {"jobs": jobs}


def _fetch_source(source: dict):
    provider = source["provider"]
    if provider == "phenom_job_page":
        return _get(source["url"])
    if provider == "workday_cxs":
        return _fetch_workday_cxs(source)
    if provider == "workday_search":
        return _fetch_workday_search(source)
    if provider == "greenhouse_board":
        return _get_json("https://boards-api.greenhouse.io/v1/boards/"
                         f"{source['url']}/jobs?content=true")
    if provider == "lever_api":
        return _get_json(f"https://api.lever.co/v0/postings/{source['url']}?mode=json")
    if provider == "ashby_api":
        return _get_json("https://api.ashbyhq.com/posting-api/job-board/"
                         f"{source['url']}?includeCompensation=false")
    if provider == "smartrecruiters_api":
        return _fetch_smartrecruiters(source)
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
        def clear_reports():
            # A posting can be filed outside its watch-list category, so a
            # source owns one report per category, not just its own.
            for known_category in CATEGORIES:
                (out_dir / f"{_report_name(entity)}_{known_category}.json").unlink(
                    missing_ok=True)

        if provider in ("_unwired", "_unverified"):
            clear_reports()
            drop_counts.pop(entity, None)
            drop_counts[entity][provider.lstrip("_") + "_source"] += 1
            continue
        # A requested collection owns its source's reports and counters for
        # this run. Otherwise a failed source would leave a stale report for
        # the merge and repeated runs would inflate its drop tally.
        clear_reports()
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
        # A watch-list board is the company's whole intern programme, so most
        # of what it returns is off-scope for this tracker (supply chain, HR,
        # sales). The watch-list category says where a company's rows *live*,
        # not what any one role is — so categorize.py decides, exactly as it
        # does for the tracker path, and only falls back to the watch-list
        # category when it can't tell.
        by_category = defaultdict(list)
        for posting in postings:
            resolved = classify_role(posting["role"])
            if resolved == DROP:
                drop_counts[entity]["category_drop"] += 1
                continue
            target = resolved or category
            # Compared against `target`, not the watch-list category:
            # classify_role can move a posting out of the category its company
            # is watched under, and a guard that checked the watch-list
            # category would wave through exactly that case. Castleton is
            # watched under quant, so its two already-tracked quant rows were
            # refiled as fresh data_science/swe rows — duplicate links the
            # merge can't see, since categories dedupe independently.
            existing_cat = known.get(normalize_link(posting["link"]))
            if existing_cat and existing_cat != target:
                drop_counts[entity]["tracked_elsewhere"] += 1
                continue
            by_category[target].append(posting)
        if not by_category:
            if not parser_drops:
                drop_counts[entity]["role_unmatched"] += 1
            print(f"[{entity}] no matching roles; no report written")
            continue

        for target, rows in sorted(by_category.items()):
            (out_dir / f"{_report_name(entity)}_{target}.json").write_text(json.dumps(
                {"category": target, "source_entity": entity, "postings": rows}, indent=1))
        total = sum(len(rows) for rows in by_category.values())
        company_state[entity] = {
            "provider": provider,
            "last_success": date.today().isoformat(),
            "row_count": total,
        }
        state_changed = True
        print(f"[{entity}] {total} posting(s) -> {', '.join(sorted(by_category))}")

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
    categories = sys.argv[1:] or CATEGORIES
    fetch = _prefetched()
    for cat in categories:
        print(f"--- {cat} ---")
        run(cat, fetch=fetch)
