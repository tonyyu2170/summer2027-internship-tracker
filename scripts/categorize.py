"""Pure category assignment. No network, no LLM.

Category is assigned once, at first sight of a link, and is stable
thereafter: merge_category dedupes within a single category file only, so a
link that changed category would silently exist in two files. assign_category
therefore always prefers the category a link already has in data/*.yaml."""
import re
import yaml
from pathlib import Path

from normalize import normalize_link

ROOT = Path(__file__).resolve().parent.parent

# Sentinel: upstream category has no local equivalent; drop the posting.
DROP = "__drop__"

# Order matters. Hardware is checked before quant so hardware roles at quant
# firms (Jane Street, Akuna, IMC) route to hardware.yaml — the convention,
# and the bug fixed by hand in 0fdf5dd. data_science precedes ai_ml so
# "Data Scientist" wins over a bare AI match.
_RULES = [
    ("hardware", r"hardware|fpga|\basic\b|firmware|silicon|verilog|\brtl\b|embedded|\bpcb\b"),
    ("actuarial", r"actuar"),
    ("ib", r"investment bank|\bibd\b"),
    ("consulting", r"consult"),
    ("quant", r"quantitative|\bquant\b(?!ity)"),
    ("data_science", r"data scien|data analy|analytics"),
    ("ai_ml", r"machine learning|deep learning|\bml\b|\bai\b|\bnlp\b|computer vision"),
    ("swe", r"software|\bswe\b|engineer|developer|programmer|full.?stack|backend|frontend"),
]

_UPSTREAM = {
    "software": "swe",
    "software engineering": "swe",
    "quant": "quant",
    "quantitative finance": "quant",
    "hardware": "hardware",
    "product": DROP,
}

# Both spellings of the combined AI/data bucket seen in the wild.
_AI_DATA = {"ai/ml/data", "data & ml/ai"}


def classify_role(role: str) -> str | None:
    """Return a local category from role text, or None if no rule matches."""
    text = (role or "").lower()
    for category, pattern in _RULES:
        if re.search(pattern, text):
            return category
    return None


def map_upstream_category(value: str, role: str) -> str | None:
    """Map a tracker's own category string onto a local category.

    Returns DROP for upstream categories with no local equivalent. Unknown
    values fall through to classify_role rather than being dropped —
    upstream repos add and rename categories without notice."""
    key = (value or "").strip().lower()
    if key in _AI_DATA:
        return "data_science" if re.search(
            r"data scien|data analy|analytics", (role or "").lower()
        ) else "ai_ml"
    if key in _UPSTREAM:
        return _UPSTREAM[key]
    return classify_role(role)


def known_link_categories(data_dir=None) -> dict:
    """Map normalized link -> category, over every row in data/*.yaml."""
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    known = {}
    for path in sorted(data_dir.glob("*.yaml")):
        for row in (yaml.safe_load(path.read_text()) or []):
            link = row.get("link")
            if link:
                known[normalize_link(link)] = path.stem
    return known


def known_link_locations(data_dir=None) -> dict:
    """Map normalized link -> location, over every row in data/*.yaml.

    Lets a source with no location data of its own (e.g. chieler's README
    has no Location column) still confirm/refresh an already-tracked
    posting, by reusing the location an earlier source already
    established, rather than being dropped by the required-field gate."""
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    known = {}
    for path in sorted(data_dir.glob("*.yaml")):
        for row in (yaml.safe_load(path.read_text()) or []):
            link, location = row.get("link"), row.get("location")
            if link and location:
                known[normalize_link(link)] = location
    return known


def assign_category(posting: dict, known: dict) -> str | None:
    """Category for one posting: its existing one if the link is already
    tracked, else the tracker's own category, else role-text rules, else
    None (meaning: hand to the session to classify)."""
    link = posting.get("link")
    if link:
        existing = known.get(normalize_link(link))
        if existing:
            return existing
    if posting.get("upstream_category"):
        return map_upstream_category(posting["upstream_category"], posting.get("role", ""))
    return classify_role(posting.get("role", ""))
