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
}


def normalize_link(url: str) -> str:
    """Canonical application URL, used as the primary dedup key: lowercase
    scheme+host, drop fragment, strip tracking params, sort the rest, drop a
    trailing slash from the path."""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/")
    kept = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING_PARAMS
    )
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


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
