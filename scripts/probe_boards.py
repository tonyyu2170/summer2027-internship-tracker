"""Verify and discover company ATS boards for sources/companies.yaml.

Network shim (like fetch_companies.py): it never writes data/ or README.
The pure parts — identify_board / sniff_html / board_key / entry_line /
apply_results — are unit-tested; the network parts take injectable fetchers.

  python3 scripts/probe_boards.py sniff URL...            # ATS identity of posting/careers URLs
  python3 scripts/probe_boards.py verify                  # re-probe every wired watch-list board
  python3 scripts/probe_boards.py discover                # find the real ATS behind custom / unverified entries
  python3 scripts/probe_boards.py mine                    # candidates from tracker exports + data/ links -> scratch/candidates_mined.json
  python3 scripts/probe_boards.py candidates FILE.json    # probe {company, category, url|ats+token} candidates
  python3 scripts/probe_boards.py apply RESULTS.json      # write confirmed boards into sources/companies.yaml

Every probe writes scratch/probe_<command>.json (input entry + outcome);
`apply` takes such a file and only touches entries whose outcome is `ok`.
"""
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import certifi
import yaml

from categorize import DROP, map_upstream_category
from normalize import canonicalize_location
from parse_company import is_intern_title

ROOT = Path(__file__).resolve().parent.parent
COMPANIES = ROOT / "sources" / "companies.yaml"
CANDIDATES = ROOT / "scratch" / "candidates_mined.json"
CATEGORIES = ["swe", "quant", "data_science", "ai_ml", "hardware", "actuarial"]
WIRED = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable"}
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())
# Discovery GETs a company's own careers page, which routinely bot-blocks the
# scraper UA; this is a one-off identity sniff, not a scrape.
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
_TIMEOUT = 20
_WORKERS = 12
_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.I)
_TOKEN = r"([A-Za-z0-9][A-Za-z0-9_.-]*)"

# Tracker exports mined for boards: last cycle's lists name the boards that
# will carry next summer's postings, and every link is posting-evidenced.
_EXPORTS = [
    ("SimplifyJobs/Summer2026-Internships", "dev", ".github/scripts/listings.json"),
    ("SimplifyJobs/Summer2025-Internships", "dev", ".github/scripts/listings.json"),
    ("vanshb03/Summer2026-Internships", "dev", ".github/scripts/listings.json"),
    ("vanshb03/Summer2027-Internships", "dev", ".github/scripts/listings.json"),
    ("SuryaHarikrishnan/internship-tracker", "master", "data/listings.json"),
]

# Hosts that are their own (unwired) ATS: recorded as-is so a later wiring
# pass can find them instead of re-sniffing the careers page.
_KNOWN_HOSTS = {
    "apply.workable.com": "workable",
    "jobs.jobvite.com": "jobvite",
}


def identify_board(url: str):
    """ATS identity of a posting or board URL, or None when the URL doesn't
    name a board (a plain careers page, a gh_jid page whose board token is
    only in the HTML, ...)."""
    parts = urlsplit(url or "")
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io",
                "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"):
        if segments and segments[0] == "embed":
            token = (parse_qs(parts.query).get("for") or [None])[0]
        else:
            token = segments[0] if segments else None
        return _entry("greenhouse", token) if token else None
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and segments:
        return _entry("lever", segments[0])
    if host == "jobs.ashbyhq.com" and segments:
        return _entry("ashby", segments[0])
    if host.endswith(".myworkdayjobs.com"):
        return _workday(host, segments)
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com") and segments:
        if segments[0] not in ("oneclick-ui", "api"):
            return _entry("smartrecruiters", segments[0],
                          url=f"https://jobs.smartrecruiters.com/{segments[0]}")
    if host.endswith(".icims.com") and host.startswith("careers-"):
        return _entry("icims", host, url=f"https://{host}")
    if host in _KNOWN_HOSTS and segments:
        return _entry(_KNOWN_HOSTS[host], segments[0],
                      url=f"https://{host}/{segments[0]}")
    return None


def _workday(host, segments):
    tenant = host.split(".")[0]
    rest = [s for s in segments if not _LOCALE.match(s)]
    if not rest or rest[0] in ("job", "wday"):
        return None
    site = rest[0]
    return _entry("workday", f"{tenant}/{site}", url=f"https://{host}/{site}",
                  tenant=tenant, site=site)


def _entry(ats, token, url=None, **extra):
    return {"ats": ats, "token": token, "url": url or token, **extra}


_SNIFF = [
    ("greenhouse", re.compile(r"greenhouse\.io/(?:v1/boards/|embed/job_(?:board|app)(?:/js)?\?(?:[^\"'\s<>]*?&)?for=)" + _TOKEN)),
    ("greenhouse", re.compile(r"(?:job-)?boards\.(?:eu\.)?greenhouse\.io/(?!embed\b)" + _TOKEN)),
    ("lever", re.compile(r"(?:jobs|api)\.(?:eu\.)?lever\.co/(?:v0/postings/)?" + _TOKEN)),
    ("ashby", re.compile(r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/" + _TOKEN)),
    ("workday", re.compile(r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/((?:[a-z]{2}-[a-z]{2}/)?[A-Za-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/" + _TOKEN)),
]
_SNIFF_SKIP = {"embed", "js", "api", "v0", "v1", "postings", "static", "assets", "job", "wday"}


def sniff_html(html: str):
    """First ATS board referenced from a careers page (embed script, iframe,
    'see all openings' link). Returns the same shape as identify_board."""
    for ats, pattern in _SNIFF:
        for match in pattern.finditer(html or ""):
            if ats == "workday":
                found = identify_board(f"https://{match.group(1)}/{match.group(2)}")
            else:
                token = match.group(1).rstrip(".")
                if token.lower() in _SNIFF_SKIP:
                    continue
                found = identify_board(_board_url(ats, token))
            if found:
                return found
    return None


def _board_url(ats, token):
    return {"greenhouse": f"https://boards.greenhouse.io/{token}",
            "lever": f"https://jobs.lever.co/{token}",
            "ashby": f"https://jobs.ashbyhq.com/{token}",
            "smartrecruiters": f"https://jobs.smartrecruiters.com/{token}",
            "workable": f"https://apply.workable.com/{token}"}[ats]


def board_key(entry: dict):
    """Identity a board is deduped on: two watch-list entries with the same
    key scrape the same thing regardless of the company name they carry."""
    ats = entry.get("ats")
    if ats == "workday":
        found = identify_board(entry.get("url") or "")
        tenant = entry.get("tenant") or (found or {}).get("tenant")
        site = entry.get("site") or (found or {}).get("site")
        return ("workday", (tenant or "").lower().replace("-", "_"), (site or "").lower())
    if ats in ("greenhouse", "lever", "ashby"):
        return (ats, (entry.get("url") or "").lower())
    found = identify_board(entry.get("url") or "")
    if found:
        return (found["ats"], found["token"].lower())
    return (ats, (entry.get("url") or "").lower().rstrip("/"))


def entry_line(entry: dict) -> str:
    """One watch-list line, in the file's own `  - {ats: .., company: .., url: ..}` style."""
    return "  - " + yaml.dump(entry, default_flow_style=True, width=10000,
                              allow_unicode=True).strip()


def watchlist_entry(company: str, found: dict) -> dict:
    entry = {"ats": found["ats"], "company": company, "url": found["url"]}
    if found["ats"] == "workday" and found.get("pinned_tenant"):
        # Workday tenants can't contain "-": the vanity host fronts an
        # underscored tenant, so the entry pins it (see docs/SCRAPING.md).
        entry["tenant"] = found["pinned_tenant"]
    return entry


# ---- network -------------------------------------------------------------

def _request(url, payload=None, accept="application/json"):
    headers = {"User-Agent": _UA, "Accept": accept}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=_TIMEOUT, context=_SSL_CTX) as response:
        return response.geturl(), response.read().decode("utf-8", "replace")


def _json(url, payload=None):
    return json.loads(_request(url, payload)[1])


def probe_board(found: dict, get=None, post=None) -> dict:
    """Confirm a board identity against its public API. Returns
    {status: ok|fail, jobs, intern_jobs, name, error, tenant?}."""
    get, post = get or _json, post or _json
    ats, token = found["ats"], found["token"]
    try:
        if ats == "greenhouse":
            jobs = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")["jobs"]
            name = get(f"https://boards-api.greenhouse.io/v1/boards/{token}").get("name")
            return _ok(jobs, name, key="title")
        if ats == "lever":
            jobs = get(f"https://api.lever.co/v0/postings/{token}?mode=json")
            if not isinstance(jobs, list):
                raise ValueError("lever response is not a list")
            return _ok(jobs, None, key="text")
        if ats == "ashby":
            jobs = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")["jobs"]
            return _ok(jobs, None, key="title")
        if ats == "smartrecruiters":
            payload = get(f"https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=1")
            return {"status": "ok", "jobs": payload["totalFound"], "intern_jobs": None, "name": None}
        if ats == "workday":
            return _probe_workday(found, post)
        if ats == "workable":
            payload = post(f"https://apply.workable.com/api/v3/accounts/{token}/jobs",
                           {"query": "", "location": [], "department": [], "worktype": [], "remote": []})
            return _ok(payload["results"], None, key="title")
    except (urllib.error.URLError, ValueError, KeyError, TypeError) as exc:
        return {"status": "fail", "error": _err(exc)}
    return {"status": "fail", "error": f"no probe for ats {ats!r}"}


def _ok(jobs, name, key):
    titles = [j.get(key) or "" for j in jobs]
    return {"status": "ok", "jobs": len(jobs), "name": name,
            "intern_jobs": sum(1 for t in titles if is_intern_title(t))}


def _probe_workday(found, post):
    """CXS search; a hyphenated vanity host is retried as the underscored
    tenant it fronts (422 = bad tenant, 404 = bad site)."""
    host = urlsplit(found["url"]).netloc
    tenants = [found.get("tenant")]
    if "-" in tenants[0]:
        tenants.append(tenants[0].replace("-", "_"))
    last = None
    for tenant in tenants:
        url = f"https://{host}/wday/cxs/{tenant}/{found['site']}/jobs"
        try:
            payload = post(url, {"appliedFacets": {}, "limit": 1, "offset": 0,
                                 "searchText": "Summer 2027"})
            outcome = {"status": "ok", "jobs": payload["total"], "intern_jobs": payload["total"],
                       "name": None}
            if tenant != found.get("tenant"):
                outcome["pinned_tenant"] = tenant
            return outcome
        except (urllib.error.URLError, ValueError, KeyError, TypeError) as exc:
            last = exc
    return {"status": "fail", "error": _err(last)}


def _err(exc):
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return f"{type(exc).__name__}: {exc}"[:200]


def discover(url: str, fetch=None):
    """Find the ATS board behind a careers URL: from the URL itself, else
    from the page it redirects to, else from the page's HTML.
    Returns (found_or_None, error_or_None)."""
    found = identify_board(url)
    if found:
        return found, None
    fetch = fetch or (lambda u: _request(u, accept="text/html,*/*"))
    try:
        final, html = fetch(url)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        return None, _err(exc)
    return identify_board(final) or sniff_html(html), None


# ---- CLI -----------------------------------------------------------------

def _load_watchlist():
    return yaml.safe_load(COMPANIES.read_text())


def _flat(data):
    for category in CATEGORIES:
        for entry in data.get(category) or []:
            yield category, entry


def _known_keys(data):
    return {board_key(e) for _, e in _flat(data) if "provider" not in e}


def _judge(company, found, error, known):
    """Outcome for a discovered/candidate board: probe it unless it is
    unusable or already on the watch-list."""
    if not found:
        return {"status": "unknown", "error": error or "no ATS reference found"}
    if found["ats"] not in WIRED:
        return {"status": "unwired", "ats": found["ats"], "url": found["url"]}
    if board_key(watchlist_entry(company, found)) in known:
        return {"status": "duplicate"}
    return probe_board(found)


def _run(items, work, name):
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(work, items))
    out = ROOT / "scratch" / f"probe_{name}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1))
    counts = {}
    for r in results:
        counts[r["outcome"]["status"]] = counts.get(r["outcome"]["status"], 0) + 1
    print(f"{len(results)} probed: {counts} -> {out.relative_to(ROOT)}")
    return results


def cmd_verify(argv):
    data = _load_watchlist()
    items = [(c, e) for c, e in _flat(data)
             if "provider" not in e and e.get("ats") in WIRED and e.get("verified") is not False]

    def work(item):
        category, entry = item
        if entry["ats"] in ("workday", "smartrecruiters", "workable"):
            found = identify_board(entry["url"])
            if found and entry["ats"] == "workday":
                found.update({k: entry[k] for k in ("tenant", "site") if k in entry})
        else:
            found = _entry(entry["ats"], entry["url"])
        outcome = probe_board(found) if found else {"status": "fail", "error": "off-shape url"}
        return {"kind": "verify", "category": category, "entry": entry, "found": found, "outcome": outcome}
    _run(items, work, "verify")


def cmd_discover(argv):
    data = _load_watchlist()
    known = _known_keys(data)
    items = [(c, e) for c, e in _flat(data)
             if "provider" not in e and (e.get("ats") not in WIRED or e.get("verified") is False)]

    def work(item):
        category, entry = item
        found, error = discover(entry["url"])
        # A custom entry whose URL already names a board must not count as
        # a duplicate of itself.
        outcome = _judge(entry["company"], found, error, known - {board_key(entry)})
        return {"kind": "discover", "category": category, "entry": entry, "found": found, "outcome": outcome}
    _run(items, work, "discover")


def cmd_candidates(argv):
    """Candidates file: [{company, category, url}] or [{company, category, ats, token}]."""
    known = _known_keys(_load_watchlist())
    items = json.loads(Path(argv[0]).read_text())

    def work(cand):
        if cand.get("ats") and cand.get("token"):
            found, error = identify_board(_board_url(cand["ats"], cand["token"])), None
        else:
            found, error = discover(cand.get("url") or "")
        outcome = _judge(cand["company"], found, error, known)
        return {"kind": "candidate", "category": cand.get("category"), "entry": cand,
                "found": found, "outcome": outcome}
    _run(items, work, "candidates")


def mine_candidates(exports, data_rows, known):
    """Boards behind posting links that aren't on the watch-list yet.
    exports: iterable of cvrve-shaped entries; data_rows: (category, row)
    pairs from data/. Pure — returns candidates sorted by evidence."""
    boards = {}
    for company, category, url in _evidence(exports, data_rows):
        found = identify_board(url)
        if not found or found["ats"] not in WIRED:
            continue
        key = board_key(watchlist_entry(company, found))
        if key in known:
            continue
        board = boards.setdefault(key, {"found": found, "company": Counter(), "category": Counter()})
        board["company"][company] += 1
        board["category"][category] += 1
    out = []
    for board in boards.values():
        found = board["found"]
        url = found["url"] if found["ats"] == "workday" else _board_url(found["ats"], found["token"])
        out.append({"company": board["company"].most_common(1)[0][0],
                    "category": board["category"].most_common(1)[0][0],
                    "url": url, "postings": sum(board["category"].values())})
    return sorted(out, key=lambda c: (-c["postings"], c["company"]))


def _evidence(exports, data_rows):
    for e in exports:
        if not any(canonicalize_location(loc) for loc in e.get("locations") or []):
            continue
        category = map_upstream_category(e.get("category"), e.get("title"))
        if category in (None, DROP):
            continue
        yield (e.get("company_name") or "").strip(), category, e.get("url") or ""
    for category, row in data_rows:
        yield row.get("company") or "", category, row.get("link") or ""


def cmd_mine(argv):
    known = _known_keys(_load_watchlist())
    exports = []
    for repo, branch, path in _EXPORTS:
        try:
            exports.extend(_json(f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"))
        except (urllib.error.URLError, ValueError) as exc:
            print(f"skip {repo}: {_err(exc)}")
    data_rows = [(f.stem, row) for f in (ROOT / "data").glob("*.yaml")
                 for row in yaml.safe_load(f.read_text()) or []]
    cands = mine_candidates(exports, data_rows, known)
    CANDIDATES.write_text(json.dumps(cands, indent=1))
    print(f"{len(cands)} candidate boards from {len(exports)} export rows + {len(data_rows)} data rows "
          f"-> {CANDIDATES.relative_to(ROOT)}")


def cmd_sniff(argv):
    for url in argv:
        found, error = discover(url)
        print(json.dumps({"url": url, "found": found, "error": error}))


def apply_results(results, text: str):
    """Rewrite the watch-list text: confirmed discoveries replace their own
    line in place, confirmed candidates append to their category block.
    Pure — returns (new_text, summary)."""
    lines = text.split("\n")
    summary = {"replaced": 0, "added": 0, "skipped": 0}
    seen = _known_keys(yaml.safe_load(text))
    for r in results:
        if r["outcome"].get("status") != "ok" or not r.get("found"):
            continue
        found = dict(r["found"], **{k: v for k, v in r["outcome"].items() if k == "pinned_tenant"})
        new = watchlist_entry(r["entry"]["company"], found)
        key = board_key(new)
        if r["kind"] == "discover":
            old = entry_line(r["entry"])
            if key in seen or old not in lines:
                summary["skipped"] += 1
                continue
            lines[lines.index(old)] = entry_line(new)
        elif r["kind"] == "candidate" and r.get("category") in CATEGORIES:
            if key in seen:
                summary["skipped"] += 1
                continue
            lines.insert(_category_end(lines, r["category"]), entry_line(new))
        else:
            continue
        summary["replaced" if r["kind"] == "discover" else "added"] += 1
        seen.add(key)
    return "\n".join(lines), summary


def _category_end(lines, category):
    """Index just past the last flow entry of a category block."""
    start = lines.index(f"{category}:")
    end = start + 1
    for i in range(start + 1, len(lines)):
        if lines[i] and not lines[i].startswith(" "):
            break
        if lines[i].startswith("  - {"):
            end = i + 1
    return end


def cmd_apply(argv):
    results = json.loads(Path(argv[0]).read_text())
    text, summary = apply_results(results, COMPANIES.read_text())
    COMPANIES.write_text(text)
    print(summary)


def main(argv):
    commands = {"verify": cmd_verify, "discover": cmd_discover, "candidates": cmd_candidates,
                "mine": cmd_mine, "sniff": cmd_sniff, "apply": cmd_apply}
    if not argv or argv[0] not in commands:
        print(__doc__)
        return 2
    commands[argv[0]](argv[1:])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
