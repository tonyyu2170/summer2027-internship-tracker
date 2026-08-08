"""Pure ATS-API verification logic. No I/O, no network.

Given a row's application link: derive the authoritative API URL for its
ATS family (`api_url`), parse a probe of that URL into a normalized
extract (`extract`), and decide what corrections the row needs (`decide`).
The network driver is check_ats.py; the write side is
apply_ats_corrections.py. Design:
docs/superpowers/specs/2026-08-08-ats-verification-design.md."""
import json
import re
from datetime import datetime, timedelta, timezone

from link_check import workday_cxs_url
from normalize import canonicalize_location, _NON_US_RE

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_GREENHOUSE_RE = re.compile(
    r"^https?://(?:job-boards|boards)\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.I)
_LEVER_RE = re.compile(
    rf"^https?://jobs\.lever\.co/([^/?#]+)/({_UUID})", re.I)
_ASHBY_RE = re.compile(
    rf"^https?://jobs\.ashbyhq\.com/([^/?#]+)/({_UUID})", re.I)
_SMARTRECRUITERS_RE = re.compile(
    r"^https?://jobs\.smartrecruiters\.com/([^/?#]+)/(\d+)", re.I)
_ICIMS_RE = re.compile(r"^https?://[^/]+\.icims\.com/jobs/\d+/", re.I)


def api_url(link: str):
    """('family', probe_url) for a link on a covered ATS, else None.

    For ashby the URL is the org's whole job board (one payload covers every
    row of that org; extract() picks out the row's own job by the link's
    trailing UUID). For icims it's the posting page itself (JSON-LD)."""
    cxs = workday_cxs_url(link)
    if cxs:
        return ("workday", cxs)
    m = _GREENHOUSE_RE.match(link)
    if m:
        return ("greenhouse",
                f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
    m = _LEVER_RE.match(link)
    if m:
        return ("lever", f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
    m = _ASHBY_RE.match(link)
    if m:
        return ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
    m = _SMARTRECRUITERS_RE.match(link)
    if m:
        return ("smartrecruiters",
                f"https://api.smartrecruiters.com/v1/companies/{m.group(1)}/postings/{m.group(2)}")
    if _ICIMS_RE.match(link):
        return ("icims", link)
    return None


def extract(ats, status, body, link="", today=None):
    """Normalize one probe of an api_url into
    {"locations": [str], "country": str|None,
     "date_posted": "YYYY-MM-DD"|None, "closed": bool},
    or None for "couldn't tell" (ambiguous HTTP status, or a payload that
    doesn't parse as expected — format drift is never guessed at).

    `link` is the row's own application link — the ashby extractor needs it
    to find the row's job inside the org-wide board payload. `today` is a
    datetime.date anchoring workday's relative posted-on text."""
    if status in (404, 410):
        # Every family's probe URL is posting-scoped, so a 404 means the
        # posting is gone — except ashby, whose probe URL is the org's whole
        # BOARD: a 404 there means the org disabled or moved the board, not
        # that this posting closed. Treat that as unknown rather than
        # false-closing every row of the org.
        if ats == "ashby":
            return None
        return {"locations": [], "country": None, "date_posted": None,
                "closed": True}
    if not (200 <= status < 300) or not body:
        return None
    try:
        return _EXTRACTORS[ats](body, link, today)
    except Exception:
        return None


_POSTED_DAYS_RE = re.compile(r"(\d+)(\+?)\s*days?\s+ago", re.I)


def _workday_date(posted_on, today):
    """'Posted Today'/'Posted Yesterday'/'Posted N Days Ago' anchored to
    `today`. 'Posted 30+ Days Ago' is a lower bound, not a date — too
    coarse to be a correction, so None."""
    if today is None:
        return None
    low = posted_on.lower()
    if "today" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - timedelta(days=1)).isoformat()
    m = _POSTED_DAYS_RE.search(low)
    if m and not m.group(2):
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return None


def _extract_workday(body, link, today):
    info = json.loads(body)["jobPostingInfo"]
    locations = [info["location"]] + list(info.get("additionalLocations") or [])
    return {
        "locations": locations,
        "country": (info.get("country") or {}).get("descriptor"),
        "date_posted": _workday_date(info.get("postedOn") or "", today),
        "closed": False,
    }


def _extract_greenhouse(body, link, today):
    j = json.loads(body)
    j["id"]                       # shape guard: not a job payload -> drift
    name = (j.get("location") or {}).get("name") or ""
    return {
        "locations": [part.strip() for part in name.split(";") if part.strip()],
        "country": None,          # greenhouse carries country only in the text
        "date_posted": (j.get("first_published") or "")[:10] or None,
        "closed": False,
    }


def _extract_lever(body, link, today):
    j = json.loads(body)
    j["id"]                       # shape guard
    cats = j.get("categories") or {}
    locations = list(cats.get("allLocations") or [])
    if not locations and cats.get("location"):
        locations = [cats["location"]]
    created = j.get("createdAt")
    return {
        "locations": locations,
        "country": j.get("country"),
        "date_posted": (datetime.fromtimestamp(created / 1000, tz=timezone.utc)
                        .date().isoformat() if created else None),
        "closed": False,
    }


_EXTRACTORS = {
    "workday": _extract_workday,
    "greenhouse": _extract_greenhouse,
    "lever": _extract_lever,
}
