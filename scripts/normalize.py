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
# `{tenant}.wdN.myworkdayjobs.com` or the path-tenant form `wdN.myworkdaysite.com`
_WORKDAY_HOST = re.compile(r"^(?:([a-z0-9_-]+)\.)?(wd\d+)\.myworkday(?:jobs|site)\.com$")
# Paths on these hosts are a board slug plus an opaque id; the slug is
# case-insensitive and trackers/boards disagree on its case (NorthwoodSpace vs
# northwoodspace: 4 duplicate groups, 2026-09-02).
_SLUG_ID_HOSTS = {"jobs.ashbyhq.com", "jobs.lever.co", "job-boards.greenhouse.io",
                  "apply.workable.com"}
_ICIMS_JOB = re.compile(r"^/jobs/(\d+)(?:/|$)")
_SIG_JOB = re.compile(r"/jobs/(\d+)$")


def _workday_key(netloc: str, path: str):
    """(netloc, path) collapsing every URL form of one Workday requisition
    onto ``{tenant}.wdN.myworkdayjobs.com/job/{REQ}``, or None when the path
    carries no ``<slug>_<REQ>`` token.

    One requisition is served under every career site of the tenant
    (Careers / University_Careers / Redeployment...), with or without a
    single-digit ``-N`` posting-instance suffix, as ``/job/`` or ``/details/``,
    behind an optional locale segment, and sometimes with a different
    location segment per site. The 2026-09-02 merge landed 28 such duplicate
    groups, so the requisition id alone is the identity. A two-digit suffix
    is left alone: Microchip's ids end in a year (``R3077-26``)."""
    m = _WORKDAY_HOST.match(netloc)
    if not m:
        return None
    tenant, wd = m.group(1), m.group(2)
    segs = [s for s in path.split("/") if s]
    if segs and _WORKDAY_LOCALE.match(segs[0]):
        segs = segs[1:]
    if not tenant:                    # wdN.myworkdaysite.com/recruiting/{tenant}/{site}/...
        if len(segs) < 2 or segs[0].lower() != "recruiting":
            return None
        tenant, segs = segs[1], segs[2:]
    if len(segs) < 3 or segs[1].lower() not in ("job", "details") or "_" not in segs[-1]:
        return None
    req = re.sub(r"-\d$", "", segs[-1].rsplit("_", 1)[1]).upper()
    if not re.search(r"\d", req):
        return None
    return f"{tenant.lower()}.{wd}.myworkdayjobs.com", f"/job/{req}"


def _canonical_path(netloc: str, path: str) -> str:
    """Host-specific path canonicalization for ATS URL variants that serve one
    requisition under several paths (verified in the 2026-08-08 and
    2026-09-02 duplicate reviews). Workday is handled by `_workday_key`
    before this runs."""
    if netloc in _SLUG_ID_HOSTS:
        path = path.lower()
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
    if netloc == "apply.workable.com" and path.endswith("/apply"):
        return path[: -len("/apply")]
    if netloc.endswith(".icims.com"):
        # /jobs/<id>/<title-slug>/job and /jobs/<id>/job are one posting; the
        # slug is decorative (Daktronics, SIG: 2 duplicate groups, 2026-09-02).
        m = _ICIMS_JOB.match(path)
        if m:
            return f"/jobs/{m.group(1)}/job"
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
    # SIG's careers.sig.com is a Phenom front for its iCIMS board: the same
    # job id appears as /jobs/<id>, /<category>/jobs/<id> and
    # careers-sig.icims.com/jobs/<id>/... (5 rows over 2 ids, 2026-09-02).
    sig = _SIG_JOB.search(path) if netloc == "careers.sig.com" else None
    if sig:
        netloc, path = "careers-sig.icims.com", f"/jobs/{sig.group(1)}"
    workday = _workday_key(netloc, path)
    if workday:
        netloc, path = workday
    path = _canonical_path(netloc, path)
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    ]
    if workday or netloc.endswith(".icims.com"):
        kept = []      # the requisition id in the path is the whole identity
    q = dict(kept)
    # One requisition, several link shapes (verified 2026-09-01: 40+
    # same-req duplicate pairs). Collapse each onto the board's own page.
    if netloc == "job-boards.greenhouse.io" and path == "/embed/job_app" \
            and q.get("for") and q.get("token"):
        path = f"/{q['for'].lower()}/jobs/{q['token']}"
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

# Earliest date that can plausibly be a Summer 2027 posting date; anything
# earlier is an evergreen requisition's creation date. Bump per cycle.
CYCLE_START = "2026-01-01"

_TRUNCATED = re.compile(r"(?:\.\.\.|…)\s*$")


def extends_truncated(old, new) -> bool:
    """True when `old` is a truncated title ("Foo Inte...") and `new` is a
    longer, untruncated title that starts with the same text. Trackers cut
    long titles (zapplyjobs at ~37 chars); a fuller title from another
    source or the ATS API restores them."""
    if not isinstance(old, str) or not isinstance(new, str) or not _TRUNCATED.search(old):
        return False
    stem = _TRUNCATED.sub("", old).strip().lower()
    new = new.strip()
    return bool(stem) and new.lower().startswith(stem) and len(new) > len(stem) \
        and not _TRUNCATED.search(new)


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
_NON_US = ("emea", "apac", "uk", "england", "europe", "canada", "india", "london",
           "singapore", "toronto", "ontario", "on", "quebec", "qc",
           "british columbia", "bc", "alberta", "manitoba", "saskatchewan",
           "nova scotia", "new brunswick", "newfoundland")
_NON_US_RE = re.compile(r"\b(?:" + "|".join(_NON_US) + r")\b")
_COUNTRY = re.compile(r"^(?:us|usa|u\.s\.a?\.?|united states(?: of america)?)$", re.IGNORECASE)
# "US - NY, New York" (Parsons, 2026-09-02): country and state first, city last.
_COUNTRY_STATE = re.compile(r"^(?:usa?|united states)\s*-\s*([A-Za-z]{2})$", re.IGNORECASE)


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
    while len(parts) > 2 and _COUNTRY.match(parts[0]):
        parts.pop(0)              # "USA, Louisville, KY" (GE Appliances, 2026-09-02)
    if len(parts) < 2:
        return None
    state_first = _COUNTRY_STATE.match(parts[0])
    if state_first and len(parts) == 2 and state_first.group(1).upper() in _STATE_ABBREVS:
        parts = [parts[1], state_first.group(1)]
    # "Milton, Ontario, CA": the country code would otherwise read as
    # California. Only the middle parts are checked, so a US city that
    # happens to share a name with a non-US place still resolves.
    if _NON_US_RE.search(" ".join(parts[1:-1]).lower()):
        return None
    city, tail = parts[0], parts[-1]
    if _COUNTRY.match(city):
        return None               # a country is not a city ("USA, KY")
    if city.isupper() and len(city) > 3:
        city = city.title()       # "GREENVILLE" -> "Greenville" (tenant shouting)
    if tail.upper() in _STATE_ABBREVS:
        return f"{city}, {tail.upper()}"
    if tail.lower() in _US_STATES:
        return f"{city}, {_US_STATES[tail.lower()]}"
    return None


def is_us_location(loc: str) -> bool:
    return canonicalize_location(loc) is not None
