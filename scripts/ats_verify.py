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
