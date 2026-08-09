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
    ("hardware", r"hardware|fpga|\basic\b|firmware|silicon|verilog|\brtl\b|embedded|\bpcb\b|analog design|wireless systems|design verification|digital logic"),
    ("actuarial", r"actuar"),
    (DROP, r"investment bank|\bibd\b|consult"),
    ("quant", r"quantitative|\bquant\b(?!ity)"),
    ("data_science", r"data scien|data analy|analytics|business intelligence"),
    ("ai_ml", r"machine learning|deep learning|\bml\b|\bai\b|\bnlp\b|computer vision"),
    ("swe", r"software|\bswe\b|engineer|developer|programmer|full.?stack|backend|frontend|cyber|malware|algorithm|application development"),
    # Out-of-scope families, checked last so any in-scope keyword above wins
    # first ("Supply Chain Software Engineer" is swe, "Quantitative Finance"
    # is quant). A role left as None re-blocks every unattended merge under
    # the scheduled scrape, so families that recur in zapplyjobs/chieler must
    # resolve deterministically; one-off oddballs belong in
    # sources/manual_categories.yaml instead of new patterns here.
    (DROP, r"\bproduct\b|supply chain|logistic|purchasing|procurement|distribution"
           r"|human resources|\bhr\b|recruit|\btalent\b"
           r"|accounting|\bfinance\b|financial analys|credit analyst|venture capital|investment services"
           r"|marketing|market research|\bbrand\b|social media|\bmedia\b|editorial"
           r"|newsgathering|\bcontent\b|community engagement|sponsorship|\bsports\b"
           r"|outside sales|\bretail\b|\binsights?\b"
           r"|manufacturing|mechanical|\boperations\b|maintenance|warranty|thermal|drafter"
           r"|graphic design|visual design|instructional design|industrial design"
           r"|biolog|vaccine|clinical|formulation|\bmaterials\b|paint|coating"
           r"|radiolog|chemist|skillbridge"
           r"|legal|counsel|compliance|administrat|archivist|polling|real estate"
           r"|communication|publicity"
           r"|relationship manager|investor engage|business development"
           r"|aerospace|payload|\bgnc\b|guidance, navigation"
           r"|internship program|talent community|^\s*intern\s*$"),
]

_UPSTREAM = {
    "software": "swe",
    "software engineering": "swe",
    "quant": "quant",
    "quantitative finance": "quant",
    "hardware": "hardware",
    "consulting": DROP,
    "investment banking": DROP,
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


def manual_link_categories(path=None) -> dict:
    """Map normalized link -> category from sources/manual_categories.yaml.

    Per-link judgments recorded once, by hand, for postings no rule can
    classify (ambiguous titles, upstream-truncated text). Values are a
    category stem or DROP. Rows already in data/*.yaml always win over this
    file — see the merge order at fetch_trackers' call site — so a manual
    entry can never recategorize a tracked link."""
    path = Path(path) if path else ROOT / "sources" / "manual_categories.yaml"
    if not path.exists():
        return {}
    return {normalize_link(link): category
            for link, category in (yaml.safe_load(path.read_text()) or {}).items()}


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
