"""Pure normalization helpers used by the merge engine. No I/O, no network."""
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "source", "gh_src", "lever-source", "lever-origin",
    "jr_id",  # Simplify/vanshb03 referral token — Fiserv
    "embed",  # Ashby iframe flag — Circleback (only value in data is "true")
    "iis",    # LinkedIn inbound-source tag — safe to strip; Susquehanna
    "lang",   # display language — Susquehanna
    "mode",   # only value in data is "apply"; job id is in the path — Susquehanna
    "oga",    # SmartRecruiters apply-flow flag; job id is in the path — Bosch
}


_WORKDAY_LOCALE = re.compile(r"^[a-z]{2}-[a-z]{2}$", re.IGNORECASE)
_SR_POSTING = re.compile(r"^(/[^/]+/\d+)(?:-.*)?$")


def _canonical_path(netloc: str, path: str) -> str:
    """Host-specific path canonicalization for ATS URL variants that serve one
    requisition under several paths (verified in the 2026-08-08 duplicate
    review). Board aliases (e.g. a Workday redeployment tenant) and req-id
    ``-N`` instance suffixes are deliberately NOT collapsed — those need
    per-company evidence."""
    if netloc.endswith(".myworkdayjobs.com"):
        segs = [s for s in path.split("/") if s]
        if segs and _WORKDAY_LOCALE.match(segs[0]):
            segs = segs[1:]           # locale prefix: /en-US/, /fr-CA/, ...
        if segs:
            segs[0] = segs[0].lower()  # site/board segment case varies by source
        return "/" + "/".join(segs) if segs else ""
    if netloc == "jobs.smartrecruiters.com":
        # Posting URLs are /{Company}/{numeric id} with an optional
        # title-derived slug appended; the id alone is the identity. Verified
        # 2026-08-09: 3 duplicate pairs, every one same company and same id,
        # differing only by the slug — the API returns the slugged form while
        # trackers emit the bare one.
        slugged = _SR_POSTING.match(path)
        if slugged:
            return slugged.group(1)
    if netloc == "jobs.lever.co" and path.endswith("/apply"):
        return path[: -len("/apply")]
    if netloc == "jobs.ashbyhq.com" and path.endswith("/application"):
        return path[: -len("/application")]
    return path


_SEARCH_ID = re.compile(r"^/search/(\d+)$")


def normalize_link(url: str) -> str:
    """Canonical application URL, used as the primary dedup key: lowercase
    scheme+host, drop fragment, strip tracking params, sort the rest, drop a
    trailing slash from the path, collapse known ATS path variants."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]           # www. and bare host serve the same page
    if netloc == "boards.greenhouse.io":
        netloc = "job-boards.greenhouse.io"   # legacy host; Greenhouse redirects
    path = parts.path.rstrip("/")
    # ByteDance/TikTok serve one requisition under two link forms; trackers
    # emit both (verified 2026-08-09: 14 same-req-id duplicate pairs).
    # Collapse onto the detail-page form the careers sites themselves use.
    m = _SEARCH_ID.match(path)
    if m and netloc == "joinbytedance.com":
        netloc, path = "jobs.bytedance.com", f"/en/position/{m.group(1)}/detail"
    elif m and netloc == "lifeattiktok.com":
        path = f"/position/{m.group(1)}"
    path = _canonical_path(netloc, path)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    q = dict(kept)
    # One requisition, several link shapes (verified 2026-09-01: 40+
    # same-req duplicate pairs). Collapse each onto the board's own page.
    if netloc == "job-boards.greenhouse.io" and path == "/embed/job_app" \
            and q.get("for") and q.get("token"):
        path = f"/{q['for']}/jobs/{q['token']}"
        kept = [(k, v) for k, v in kept if k not in ("for", "token")]
    elif netloc == "apply.careers.microsoft.com" and path == "/careers" and q.get("pid"):
        path, kept = f"/careers/job/{q['pid']}", []
    # A gh_jid that just repeats the req id already in the path adds nothing;
    # one that differs (a company page listing many reqs) is the identity.
    kept = [(k, v) for k, v in kept
            if not (k == "gh_jid" and v
                    and re.search(rf"(?<![0-9]){re.escape(v)}(?![0-9])", path))]
    return urlunsplit((scheme, netloc, path, urlencode(sorted(kept)), ""))


_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(inc|llc|corp|corporation|ltd|co|group)\.?$", re.IGNORECASE
)


def normalize_company(name: str) -> str:
    """Lowercase, collapse whitespace, strip trailing legal suffixes."""
    n = re.sub(r"\s+", " ", name.strip()).lower()
    prev = None
    while prev != n:            # strip stacked suffixes, e.g. "Group, LLC"
        prev = n
        n = _LEGAL_SUFFIX.sub("", n).strip().rstrip(",").strip()
    return n


_US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ",
    "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
_STATE_ABBREVS = set(_US_STATES.values())
_NON_US = ("emea", "apac", "uk", "europe", "canada", "india", "london",
           "singapore", "toronto", "ontario", "on")
_NON_US_RE = re.compile(r"\b(?:" + "|".join(_NON_US) + r")\b")


def canonicalize_location(loc: str) -> str | None:
    """Return 'City, ST' or 'Remote (US)' when confidently US, else None.
    None means 'not confidently US' and is dropped by the US-only filter."""
    s = re.sub(r"\s+", " ", (loc or "").strip())
    if not s:
        return None
    if " / " in s:
        locations = [canonicalize_location(part) for part in s.split(" / ")]
        return " / ".join(locations) if all(locations) else None
    low = s.lower()
    if "remote" in low:
        return None if _NON_US_RE.search(low) else "Remote (US)"
    parts = [p.strip() for p in s.split(",")]
    if len(parts) < 2:
        return None
    city, tail = parts[0], parts[-1]
    if tail.upper() in _STATE_ABBREVS:
        return f"{city}, {tail.upper()}"
    if tail.lower() in _US_STATES:
        return f"{city}, {_US_STATES[tail.lower()]}"
    return None


def is_us_location(loc: str) -> bool:
    return canonicalize_location(loc) is not None
