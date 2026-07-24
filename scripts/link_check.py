"""Pure link-liveness classification. No I/O, no network.

Some ATSes serve a client-rendered SPA shell that returns HTTP 200 even for
an expired job posting (the "not found" state only appears after JS runs),
so a plain status-code check on the visible URL is not reliable everywhere:

- Workday (`*.myworkdayjobs.com`): the HTML page always 200s. The
  underlying `wday/cxs/...` JSON API it calls returns a real 404 for a
  gone posting — `workday_cxs_url` derives that API URL.
- Greenhouse (`greenhouse.io`): a dead job id redirects client-side from
  `/<token>/jobs/<id>` back to the board root `/<token>?error=true` —
  `is_greenhouse_dead_redirect` detects that.
- Everything else: a plain HTTP status is trusted directly.

The network probe itself is injected (`probe(url) -> (status, final_url)`)
so `classify_link` stays pure and testable; the real probe implementation
lives in the untested driver script."""
import re
from urllib.parse import urlsplit

_WORKDAY_HOST_RE = re.compile(r"^([^.]+)\.wd\d+\.myworkdayjobs\.com$", re.IGNORECASE)
_WORKDAY_PATH_RE = re.compile(r"^/([^/]+)/job/(.+)$")


def workday_cxs_url(link: str) -> str | None:
    """The Workday job-detail JSON API URL for a job page URL, or None if
    `link` isn't a Workday job-posting URL."""
    parts = urlsplit(link)
    host_match = _WORKDAY_HOST_RE.match(parts.netloc)
    path_match = _WORKDAY_PATH_RE.match(parts.path)
    if not host_match or not path_match:
        return None
    tenant = host_match.group(1)
    site, rest = path_match.group(1), path_match.group(2)
    return f"{parts.scheme}://{parts.netloc}/wday/cxs/{tenant}/{site}/job/{rest}"


def is_greenhouse_dead_redirect(original_link: str, final_url: str) -> bool:
    """True if following redirects from a Greenhouse job link landed
    somewhere that lost the `/jobs/<id>` path on the same host — Greenhouse's
    signal for "this posting id no longer exists"."""
    orig = urlsplit(original_link)
    final = urlsplit(final_url)
    if orig.netloc.lower() != final.netloc.lower():
        return False
    return "/jobs/" in orig.path and "/jobs/" not in final.path


def classify_status_code(status: int) -> str:
    """'alive' | 'dead' | 'unknown'. Only unambiguous codes count as dead —
    403/406/429/5xx etc. are usually bot-blocking, not a gone posting."""
    if 200 <= status < 300:
        return "alive"
    if status in (404, 410):
        return "dead"
    return "unknown"


def classify_link(link: str, probe) -> str:
    """probe: callable(url) -> (status_code: int, final_url: str).
    Returns 'alive' | 'dead' | 'unknown'."""
    cxs = workday_cxs_url(link)
    if cxs:
        status, _ = probe(cxs)
        return classify_status_code(status)

    status, final_url = probe(link)
    if "greenhouse.io" in link.lower() and is_greenhouse_dead_redirect(link, final_url):
        return "dead"
    return classify_status_code(status)
