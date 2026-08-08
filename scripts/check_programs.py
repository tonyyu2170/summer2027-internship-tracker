"""Network driver for the Phase-E watch-list re-checker. Untested at the
network edge, like the rest of scraping (see docs/SCRAPING.md) — the
status-derivation and row-building logic it calls stays pure and is tested
in tests/test_check_programs.py against a stubbed fetch function.

For each sources/programs.yaml entry, fetches check_url and matches
open_signal / closed_signal against the page body to derive a status. A
transient fetch failure (or an ambiguous match) never overwrites a known
status — see derive_status. Writes results into data/opportunities/*.yaml.
Never runs on a schedule — explicit request only, and never called from
run_scrape_merge.py's scrape path."""
import re
import yaml
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from check_links import _probe as _link_probe
from opportunity_schema import validate_opportunity

ROOT = Path(__file__).resolve().parent.parent

KIND_TO_FILE = {
    "programs": "programs.yaml",
    "research": "research.yaml",
    "competitions": "competitions.yaml",
}


def _fetch_body(url: str, timeout: float = 12.0):
    """Fetch a URL's body text via check_links._probe (same browser UA,
    same certifi SSL context — no second HTTP client). Returns None on any
    failure, including a non-2xx status: a 403 bot-block or a dead-link
    shell page must never hand its body to signal matching."""
    status, _final_url, body = _link_probe(url, timeout=timeout, want_body=True)
    if body is None or not (200 <= status < 300):
        return None
    return body


def _slug(org: str, name: str) -> str:
    return "-".join(
        re.sub(r"[^a-z0-9]+", "-", x.lower()).strip("-")
        for x in (org, name)
    )


def _match_signal(signal: str, text: str) -> bool:
    """Case-sensitive. Substring containment is checked first: most signals
    are literal page-text snippets that can contain regex metacharacters
    ("Apply by January 15 (11:59pm)" would misread the parens as a capture
    group under regex-first matching and fail to find its own literal
    text). A signal not found literally is then tried as a regex; an
    invalid pattern (re.error) is simply not a match."""
    if signal in text:
        return True
    try:
        return re.search(signal, text) is not None
    except re.error:
        return False


def _is_future(date_str, today: str) -> bool:
    if not date_str:
        return False
    if not isinstance(date_str, str):
        # A hand-edited sources/programs.yaml can leave an unquoted date
        # scalar, which PyYAML parses into a date/datetime object rather
        # than a string.
        date_str = date_str.isoformat()
    if len(date_str) == 7:          # 'YYYY-MM' -> compare as the 1st
        date_str = f"{date_str}-01"
    return date_str > today


def derive_status(open_signal, closed_signal, body, opens, today, prev_status):
    """body: fetched page text, or None if the fetch failed outright.

    - open_signal matches, closed_signal doesn't -> 'open'
    - closed_signal matches, open_signal doesn't -> 'closed'
    - fetch failed -> preserve prev_status (never let a transient failure
      flip a program to closed)
    - otherwise (both or neither signal matched) -> a future 'opens' date
      is 'upcoming'; else preserve prev_status
    - preserving with no prior status falls back to 'unknown'
    """
    if body is None:
        return prev_status or "unknown"

    open_match = bool(open_signal) and _match_signal(open_signal, body)
    closed_match = bool(closed_signal) and _match_signal(closed_signal, body)
    if open_match and not closed_match:
        return "open"
    if closed_match and not open_match:
        return "closed"

    if _is_future(opens, today):
        return "upcoming"
    return prev_status or "unknown"


def _date_str(value):
    """Normalize an opens/closes value to an ISO string. A hand-edited
    sources/programs.yaml can leave an unquoted date scalar, which PyYAML
    parses into a date/datetime object rather than a string — the schema
    requires a string, so an unnormalized value would fail validation
    (and thus never get written) on every run."""
    if value is None or isinstance(value, str):
        return value
    if hasattr(value, "hour"):        # datetime -> truncate to its date part
        value = value.date()
    return value.isoformat()


def build_row(entry: dict, id_: str, status: str, today: str, existing_row=None) -> dict:
    if existing_row:
        sources = list(existing_row.get("sources") or ["llm_discovery"])
        date_added = existing_row.get("date_added") or today
    else:
        sources = ["llm_discovery"]
        date_added = today
    return {
        "id": id_,
        "name": entry["name"],
        "org": entry["org"],
        "kind": entry["kind"],
        "category": entry.get("category"),
        "url": entry["url"],
        "apply_url": entry.get("apply_url"),
        "status": status,
        "opens": _date_str(entry.get("opens")),
        "closes": _date_str(entry.get("closes")),
        "eligibility": entry["eligibility"],
        "location": entry.get("location"),
        "cycle": entry.get("cycle"),
        "sources": sources,
        "date_added": date_added,
        "last_checked": today,
        "notes": entry.get("notes"),
    }


def check_kind(entries: list, existing_rows: list, today: str, fetch):
    """fetch: callable(url) -> body text, or None on failure. Pure aside
    from that one injected call — no direct network or file I/O.

    Returns (rows, summary). summary counts only cover entries actually
    present in the watch-list this run; a row whose watch-list entry has
    since disappeared is carried through untouched and left out of the
    tallies, matching this repo's refusal to auto-close on disappearance."""
    existing_by_id = {r["id"]: r for r in existing_rows if r.get("id")}
    unkeyed = [r for r in existing_rows if not r.get("id")]
    seen_ids = set()
    rows = []
    summary = {
        "open": 0, "upcoming": 0, "closed": 0, "unknown": 0,
        "fetch_failed": 0, "transitioned": [], "invalid": [],
    }

    for entry in entries:
        id_ = _slug(entry["org"], entry["name"])
        seen_ids.add(id_)
        existing = existing_by_id.get(id_)
        # A hand-corrupted existing row missing 'status' degrades to the
        # same fallback as no existing row at all, rather than KeyError-ing
        # the whole run.
        prev_status = (existing.get("status") if existing else None) or \
            entry.get("status") or "unknown"

        check_url = entry.get("check_url")
        body = fetch(check_url) if check_url else None
        if check_url and body is None:
            summary["fetch_failed"] += 1

        status = derive_status(
            entry.get("open_signal"), entry.get("closed_signal"),
            body, entry.get("opens"), today, prev_status,
        )
        row = build_row(entry, id_, status, today, existing)
        errors = validate_opportunity(row)
        if errors:
            summary["invalid"].append((id_, errors))
            if existing:
                rows.append(existing)   # keep the last known-good row as-is
            continue

        if existing and existing.get("status") != status:
            summary["transitioned"].append((id_, existing.get("status"), status))
        rows.append(row)
        summary[status] += 1

    for id_, row in existing_by_id.items():
        if id_ not in seen_ids:
            rows.append(row)            # watch-list entry removed; keep row
    rows.extend(unkeyed)                # malformed existing row; keep as-is

    return rows, summary


def run(watchlist_path=None, data_dir=None, state_path=None, fetch=None):
    watchlist_path = Path(watchlist_path) if watchlist_path else ROOT / "sources" / "programs.yaml"
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    state_path = Path(state_path) if state_path else ROOT / "sources" / "scrape_state.yaml"
    fetch = fetch or _fetch_body
    today = date.today().isoformat()

    watchlist = yaml.safe_load(watchlist_path.read_text()) or {}
    opp_dir = data_dir / "opportunities"
    opp_dir.mkdir(parents=True, exist_ok=True)

    totals = Counter()
    transitioned, invalid = [], []
    for kind, filename in KIND_TO_FILE.items():
        entries = watchlist.get(kind) or []
        out_path = opp_dir / filename
        existing_rows = (yaml.safe_load(out_path.read_text()) or []) if out_path.exists() else []

        rows, summary = check_kind(entries, existing_rows, today, fetch)
        out_path.write_text(yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))

        for status in ("open", "upcoming", "closed", "unknown"):
            totals[status] += summary[status]
        totals["fetch_failed"] += summary["fetch_failed"]
        transitioned += [(kind, *t) for t in summary["transitioned"]]
        invalid += [(kind, *inv) for inv in summary["invalid"]]

        print(f"[{kind}] {summary['open']} open, {summary['upcoming']} upcoming, "
              f"{summary['closed']} closed, {summary['unknown']} unknown "
              f"({summary['fetch_failed']} fetch failure(s))")
        for id_, old, new in summary["transitioned"]:
            print(f"    transitioned: [{id_}] {old} -> {new}")
        for id_, errors in summary["invalid"]:
            print(f"    warn: [{kind}] {id_} failed schema, not written: {errors}")

    state = yaml.safe_load(state_path.read_text()) if state_path.exists() else {}
    state = state or {}
    state["_last_program_check"] = {
        "ran_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "open": totals["open"],
        "upcoming": totals["upcoming"],
        "closed": totals["closed"],
        "unknown": totals["unknown"],
        "fetch_failed": totals["fetch_failed"],
        "transitioned": [f"{kind}:{id_} {old}->{new}" for kind, id_, old, new in transitioned],
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(yaml.safe_dump(state, sort_keys=False, allow_unicode=True))

    if invalid:
        print(f"\n{len(invalid)} row(s) failed schema validation and were not written.")

    return {"totals": dict(totals), "transitioned": transitioned, "invalid": invalid}


if __name__ == "__main__":
    run()
