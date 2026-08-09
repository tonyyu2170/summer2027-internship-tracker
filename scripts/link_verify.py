"""Pure decision logic for post-scrape link verification. No network.

The network driver is scripts/verify_links.py (untested, like all network
code — see docs/SCRAPING.md). This module owns the judgment rules proven in
the 2026-08-09 full-repo verification pass:

- A posting is flagged wrong-term only on an explicit season+year marker in
  page text AND no "2027" anywhere — metadata like `start_time":"2025` or
  `StartTimestampUTC` must NOT match (that was a live false-positive class).
- ByteDance/TikTok titles are authoritative (their /search/<id> pages are
  SSR); a missing title there is AMBIGUOUS, never evidence of death.
- Only hard HTTP 404/410 counts as dead. 403/406/timeouts are bot-blocking.
"""
import re
from urllib.parse import urlsplit

# Season word (or "start", but not the "start_" of JSON metadata keys)
# within a short span of a 2025-2028 year. The span excludes quotes and
# underscores so JSON blobs like start_time":"2025 can't bridge the gap.
_TERM = re.compile(
    r"(?:\b(?:summer|fall|winter|spring)\b|\bstart(?:ing)?\b(?!_))"
    r"[^<>{}\"_\n]{0,25}?\b(20(?:2[5-8]))\b"
    r"|\b(20(?:2[5-8]))\b[^<>{}\"_\n]{0,15}?\b(?:summer|fall|winter|spring|start)\b",
    re.IGNORECASE)
_TITLE = re.compile(r"<title>([^<]*)</title>", re.S)
_TITLE_YEAR = re.compile(r"\b(20\d\d)\s*(?:Start|Summer|Fall|Spring|Winter)?\s*$", re.I)
_TITLE_YEAR_LOOSE = re.compile(r"-\s*(20\d\d)[^-]*$")
_DEGREE = re.compile(r"\s*[（(]\s*(?:BS|MS|PhD|Bachelor|Master)[^)）]*[)）]\s*$", re.I)
_REQ_ID = re.compile(r"(\d{15,})")


def family(link: str) -> str:
    host = urlsplit(link).netloc.lower()
    if "bytedance" in host:
        return "bytedance"
    if "lifeattiktok" in host:
        return "tiktok"
    if host == "apply.workable.com":
        return "workable"
    return "generic"


def probe_url(link: str) -> str | None:
    """URL to actually fetch. ByteDance/TikTok detail pages are JS stubs;
    their /search/<id> form is SSR. Workable pages are JS shells; its public
    widget API serves JSON. None = this link can't be probed."""
    fam = family(link)
    if fam in ("bytedance", "tiktok"):
        m = _REQ_ID.search(link)
        if not m:
            return None
        host = "lifeattiktok.com" if fam == "tiktok" else "joinbytedance.com"
        return f"https://{host}/search/{m.group(1)}"
    if fam == "workable":
        m = re.search(r"apply\.workable\.com/([^/]+)/j/([A-Za-z0-9]+)", link)
        if not m:
            return None
        return f"https://apply.workable.com/api/v2/accounts/{m.group(1)}/jobs/{m.group(2)}"
    return link


def page_title(html: str) -> str:
    m = _TITLE.search(html)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1).split("|")[0]).strip()


def clean_role(title: str) -> str:
    """Strip trailing degree parentheticals — Degree is its own column."""
    prev = None
    while prev != title:
        prev, title = title, _DEGREE.sub("", title).strip()
    return title


def title_term_year(title: str) -> str | None:
    m = _TITLE_YEAR.search(title) or _TITLE_YEAR_LOOSE.search(title)
    return m.group(1) if m else None


def bad_term_markers(text: str) -> tuple[list, bool]:
    """(non-2027 season+year snippets, whether 2027 appears anywhere)."""
    bad = set()
    for m in _TERM.finditer(text):
        year = m.group(1) or m.group(2)
        if year != "2027":
            bad.add(re.sub(r"\s+", " ", m.group(0))[:55])
        if len(bad) > 5:
            break
    return sorted(bad), "2027" in text


def evaluate(link: str, status: int, body: str, stored_role: str) -> dict:
    """Verdict for one probed row. body is HTML (or Workable JSON text)."""
    if status in (404, 410):
        return {"verdict": "dead", "evidence": f"http_{status}"}
    fam = family(link)
    if fam in ("bytedance", "tiktok"):
        title = page_title(body)
        if not title or len(title) < 8:
            return {"verdict": "ambiguous", "evidence": "no ssr title"}
        year = title_term_year(title)
        if year and year != "2027":
            return {"verdict": "wrong_term", "evidence": title[:80]}
        out = {"verdict": "ok"}
        new_role = clean_role(title)
        if new_role and new_role != stored_role:
            out["new_role"] = new_role
        return out
    bad, has27 = bad_term_markers(body)
    if bad and not has27 and "2027" not in stored_role:
        return {"verdict": "wrong_term", "evidence": "; ".join(bad)[:120]}
    return {"verdict": "ok"}


def suppression_links(link: str) -> list:
    """Overlay keys for a wrong-term row. ByteDance/TikTok reqs get both URL
    forms — trackers emit either, and deletion alone re-imports next scrape."""
    fam = family(link)
    m = _REQ_ID.search(link)
    if fam in ("bytedance", "tiktok") and m:
        return [f"https://joinbytedance.com/search/{m.group(1)}",
                f"https://lifeattiktok.com/search/{m.group(1)}"]
    return [link]


def over_cap(n_wrong: int, n_probed: int) -> bool:
    """Abort threshold: a flood of wrong-term flags in one run looks like a
    parser/format break, not real data (cf. the ATS board-rename rule).
    Anything past max(5, 20% of probed) needs human review."""
    return n_wrong > max(5, 0.2 * n_probed)
