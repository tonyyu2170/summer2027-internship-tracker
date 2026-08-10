"""Detect roles reposted under a new requisition id. Pure; no network.

A company that reposts a role gets a new req id, so the tracked link keeps
pointing at the superseded posting and the row keeps its original
`date_posted`. Because the README sorts newest-first, a role that just went
live sinks to the bottom of its section and is effectively invisible --
InfiniteQuant's Summer 2027 QR internship sat at row 158 of 187 that way.

The signal is the company's own posting list: a tracked link that no longer
appears in it, next to an untracked listing entry with the same title, is a
repost. `check_reposts.py` does the fetching; this module only decides.

**Workday is deliberately out of scope.** `normalize_link` does not collapse
its `-N` requisition instance suffixes or its board aliases (see
docs/superpowers/plans/2026-08-09-full-link-verification.md), so Workday rows
routinely fail an exact link comparison and would fake a repost apiece.
SmartRecruiters, Greenhouse and Lever all expose an authoritative company-level
listing with clean ids.
"""
import json
import re
from datetime import datetime, timezone

from normalize import normalize_link

# Lever's createdAt is when the requisition was created, not when this
# posting went live; evergreen reqs carry dates years back. Same guard as
# ats_verify._CYCLE_START.
_CYCLE_START = "2026-01-01"

_SMARTRECRUITERS_RE = re.compile(
    r"^https?://jobs\.smartrecruiters\.com/([^/]+)/", re.I)
_GREENHOUSE_RE = re.compile(
    r"^https?://(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/", re.I)
_LEVER_RE = re.compile(r"^https?://jobs\.lever\.co/([^/]+)/", re.I)


def listing_url(link: str):
    """(ats, company-listing API url) for a supported board, else None."""
    m = _SMARTRECRUITERS_RE.match(link or "")
    if m:
        return ("smartrecruiters",
                f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}"
                f"/postings?limit=100")
    m = _GREENHOUSE_RE.match(link or "")
    if m:
        return ("greenhouse",
                f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs")
    m = _LEVER_RE.match(link or "")
    if m:
        return ("lever",
                f"https://api.lever.co/v0/postings/{m.group(1)}?mode=json")
    return None


def _iso(value):
    """ISO date string from an ATS date, or None. Pre-cycle dates are
    dropped rather than trusted -- see _CYCLE_START."""
    if value is None:
        return None
    if isinstance(value, (int, float)):          # Lever: epoch milliseconds
        day = datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    else:
        text = str(value)[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return None
        day = text
    return day if day >= _CYCLE_START else None


def parse_listing(ats: str, body: str) -> list:
    """Listing payload -> [{link, title, date_posted}]. Unparseable
    payloads raise; the driver decides what a failed fetch means."""
    doc = json.loads(body)
    if ats == "smartrecruiters":
        out = []
        for p in doc.get("content", []):
            company = (p.get("company") or {}).get("identifier")
            out.append({
                "link": f"https://jobs.smartrecruiters.com/{company}/{p['id']}",
                "title": p.get("name"),
                "date_posted": _iso(p.get("releasedDate"))})
        return out
    if ats == "greenhouse":
        return [{"link": j.get("absolute_url"), "title": j.get("title"),
                 "date_posted": _iso(j.get("updated_at"))}
                for j in doc.get("jobs", [])]
    if ats == "lever":
        return [{"link": p.get("hostedUrl"), "title": p.get("text"),
                 "date_posted": _iso(p.get("createdAt"))}
                for p in doc]
    raise ValueError(f"unsupported ats {ats!r}")


def _link_key(link):
    """Identity of a posting for comparison only — never written back.

    `normalize_link` is the repo-wide canonical form and stays authoritative
    for stored links; it just doesn't collapse Greenhouse's two hostnames
    (`boards.` vs `job-boards.greenhouse.io`) or its `gh_jid` param, so the
    same job read from a board listing and from a tracker looks like two
    postings. Widening normalize_link itself would rewrite ids across all of
    data/ (the deferred board-alias work), so the fix lives here."""
    key = normalize_link(link or "")
    key = re.sub(r"^(https?://)job-boards\.greenhouse\.io/", r"\1boards.greenhouse.io/",
                 key, flags=re.I)
    if "greenhouse.io/" in key.lower():
        key = key.split("?", 1)[0]
    return key


def _title_key(text):
    """Normalized title, or None when the text can't be matched safely.

    Upstream sources truncate long roles ('Raytheon Electrical Engineering
    Inter...'); a truncated title has no exact counterpart in the listing,
    and prefix-matching it would be a guess."""
    text = (text or "").strip()
    if not text or text.endswith("..."):
        return None
    return re.sub(r"\s+", " ", text).lower()


def find_reposts(rows: list, entries: list) -> list:
    """Rows for one board + that board's live listing -> action dicts.

    Emits `repost` only when exactly one tracked row and exactly one
    untracked listing entry share a title: companies post the same title
    many times over (nine identical Copart 'Software Engineering Intern'
    rows), so any fan-out is reported as `ambiguous` for review instead of
    guessed at. A missing row with no title match produces nothing at all --
    that is the closed case, which is out of scope."""
    live = {_link_key(e["link"]) for e in entries if e.get("link")}
    tracked = {_link_key(r["link"]) for r in rows if r.get("link")}

    missing_by_title = {}
    for row in rows:
        if not row.get("link") or _link_key(row["link"]) in live:
            continue
        key = _title_key(row.get("role"))
        if key:
            missing_by_title.setdefault(key, []).append(row)

    new_by_title = {}
    for entry in entries:
        if not entry.get("link") or _link_key(entry["link"]) in tracked:
            continue
        key = _title_key(entry.get("title"))
        if key:
            new_by_title.setdefault(key, []).append(entry)

    actions = []
    for key, group in sorted(missing_by_title.items()):
        candidates = new_by_title.get(key) or []
        if not candidates:
            continue
        if len(group) == 1 and len(candidates) == 1:
            actions.append({
                "action": "repost",
                "id": group[0]["id"],
                "old_link": group[0]["link"],
                "new_link": candidates[0]["link"],
                "new_date": candidates[0]["date_posted"]})
        else:
            actions.append({
                "action": "ambiguous",
                "ids": [r["id"] for r in group],
                "title": key,
                "candidates": [c["link"] for c in candidates]})
    return actions
