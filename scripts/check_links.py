"""Network driver for link_check.py's pure classification logic. Untested,
like the rest of scraping (see docs/SCRAPING.md) — the classification logic
itself is tested in tests/test_link_check.py.

Probes every `open` row's link across data/*.yaml. Rows classified 'dead'
get written into a fetch-report JSON with closed_marker: true, so the
existing merge pipeline closes them the same way an explicit source-side
closed marker would (scripts/merge.py already has this path). This script
never writes data/*.yaml directly — run scripts/run_scrape_merge.py
afterward to apply."""
import json
import ssl
import certifi
import urllib.request
import urllib.error
import yaml
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from link_check import classify_link

ROOT = Path(__file__).resolve().parent.parent
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
# python.org macOS builds don't ship a CA bundle wired into the ssl module by
# default (unlike curl, which uses the system store) — use certifi's instead
# of disabling verification.
_SSL_CTX = ssl.create_default_context(cafile=certifi.where())


_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _probe(url: str, timeout: float = 12.0):
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            return resp.status, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl() or url
    except Exception:
        return 0, url  # network error/timeout -> classify_status_code -> "unknown"


def check_category(path: Path, workers: int = 15):
    rows = yaml.safe_load(path.read_text()) or []
    open_rows = [r for r in rows if r.get("status") == "open"]
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(classify_link, r["link"], _probe): r["id"] for r in open_rows}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    dead = [r for r in open_rows if results.get(r["id"]) == "dead"]
    return open_rows, results, dead


def run(data_dir=None, out_dir=None):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    out_dir = Path(out_dir) if out_dir else ROOT / "scratch" / "fetch_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    for path in sorted(data_dir.glob("*.yaml")):
        category = path.stem
        open_rows, results, dead = check_category(path)
        counts = {"alive": 0, "dead": 0, "unknown": 0}
        for status in results.values():
            counts[status] += 1
        print(f"[{category}] checked {len(open_rows)} open link(s): "
              f"{counts['alive']} alive, {counts['dead']} dead, {counts['unknown']} unknown")

        if not dead:
            continue
        postings = [
            {
                "company": r["company"], "role": r["role"], "location": r["location"],
                "link": r["link"], "term": r["term"], "degree": r["degree"],
                "source": "link_checker", "closed_marker": True,
            }
            for r in dead
        ]
        report = {"category": category, "source_entity": "link_checker", "postings": postings}
        out_path = out_dir / f"link_check_{category}.json"
        out_path.write_text(json.dumps(report, indent=2))
        for r in dead:
            print(f"    dead: [{r['id']}] {r['company']} - {r['role']} -> {r['link']}")


if __name__ == "__main__":
    run()
