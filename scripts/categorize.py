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

# A title naming a software craft is never re-routed by the discipline rules,
# however else the title reads. This guard, not adjacency to "engineer", is
# what keeps those rules from over-matching: without it `civil` claims
# "Privacy and Civil Liberties Software Engineer Intern" and `quality` claims
# TikTok's "Code Intelligence & Quality Validation" backend role.
_NOT_SOFTWARE = (r"^(?!.*(?:software|developer|programmer|full.?stack|backend"
                 r"|front.?end|\bswe\b|computer science))")


def _discipline(words):
    r"""Pattern matching an engineering title that names one of `words`.

    The discipline may sit anywhere in the title: an earlier
    `<discipline>\s+engineer` rule required adjacency and so missed
    "Mechanical Design Engineering Intern" (a word in between) and "Student
    Engineering Intern - Civil" (inverted), filing both under swe.
    """
    return _NOT_SOFTWARE + r"(?=.*engineer).*(?:" + words + r")"


# Order matters. Hardware is checked before quant so hardware roles at quant
# firms (Jane Street, Akuna, IMC) route to hardware.yaml — the convention,
# and the bug fixed by hand in 0fdf5dd. data_science precedes ai_ml so
# "Data Scientist" wins over a bare AI match.
_RULES = [
    ("hardware", r"hardware|fpga|\basic\b|firmware|silicon|verilog|\brtl\b|embedded|\bpcb\b|analog design|wireless systems|design verification|digital logic"
                 r"|\bic design|mixed.signal|\brf\b"),
    ("actuarial", r"actuar"),
    (DROP, r"investment bank|\bibd\b|consult"),
    # `trading`/`trader` is the trading-firm family (DV Trading, PIMCO, BNY
    # desks); a "Supply, Trading & Shipping" commercial intern is not.
    ("quant", r"quantitative|\bquant\b(?!ity)|^(?!.*(?:supply|shipping)).*(?:\btrading\b|\btrader\b)"),
    ("data_science", r"data scien|data analy|analytics|business intelligence"
                     r"|\bdata intern|data management intern|data services intern|statistic|predictive model|reporting analyst"),
    ("ai_ml", r"machine learning|deep learning|reinforcement learning|\bml\b|\bai\b|\bnlp\b|computer vision"
              r"|artificial intelligence|large language|\bllm\b|research scientist|ph\.?d\.? research"
              r"|computational intelligence|\bperception\b|\bautonomy\b"
              r"|applied scientist(?!.*\b(?:materials|chemist\w*|chemical|optics|polymer|metallurg\w*)\b)"),
    # Non-software engineering disciplines, checked just before swe so its
    # bare `engineer` match can't claim them (RTX/Bosch/HNTB mechanical,
    # industrial and civil interns were all filing as swe). Physical-product
    # electronics disciplines are in scope and route to hardware; the rest are
    # out of scope for a six-category listing and drop, rather than making
    # hardware.yaml the catch-all it had become. Hardware is checked first so
    # Draper's "Mechanical Engineering & System Packaging" keeps its
    # discipline instead of dropping on `packaging`. In-scope specialties
    # above still win, so a firmware-flavoured mechanical role stays hardware
    # on its own merits.
    ("hardware", _discipline(r"electrical|electronic|mechanical|mechatronic"
                             r"|semiconductor|microwave|\bmems\b|metrology"
                             r"|optical|photonic")),
    # thermal, manufactur, aerospace and materials also appear in the
    # out-of-scope family at the bottom of this table, but no title naming
    # "engineer" ever reaches it — swe claims the row first. This is where
    # those words actually take effect for an engineering title.
    # The \b on structur/facilit/materials is load-bearing: a bare substring
    # check matches "Infrastructure" and would drop ByteDance's AI
    # Infrastructure Engineer roles — the same class of bug as the location
    # filter's Milwaukee/Dayton false positives.
    (DROP, _discipline(r"civil|chemical|environmental|geotechnical|petroleum"
                       r"|\bstructur|industrial|manufactur|\bfacilit|packaging"
                       r"|\bquality\b|mining|agricultural|nuclear|aerospace"
                       r"|biomedical|\bmaterials\b|thermal|sustainab")),
    # `computer science` earns its place next to `software`: a bare "Computer
    # Science Intern" matches nothing else here, so it fell through to the
    # out-of-scope family below and Gulfstream's CS intern dropped on the
    # `materials` in its subtitle.
    # The generic enterprise technology family (banks, airlines, insurers):
    # "Technology Intern", "Digital Technology Intern", "IT Infrastructure
    # Intern", "Information Security Co-op" recur in chieler/zapplyjobs and
    # parked 40+ rows per scrape while unclassified.
    ("swe", r"software|\bswe\b|engineer|developer|programmer|full.?stack|backend|frontend|cyber|malware|algorithm|application development|computer science|\bcomputing\b"
            r"|\btechnology intern(?:ship)?\b(?!.*program)|digital technology|information technology|\bit intern|it infrastructure"
            r"|information security|information systems|devops|\bpython\b|mobile app"),
    # Out-of-scope families, checked last so any in-scope keyword above wins
    # first ("Supply Chain Software Engineer" is swe, "Quantitative Finance"
    # is quant). A role left as None re-blocks every unattended merge under
    # the scheduled scrape, so families that recur in zapplyjobs/chieler must
    # resolve deterministically; one-off oddballs belong in
    # sources/manual_categories.yaml instead of new patterns here.
    (DROP, r"\bproduct\b|\bsupply\b|logistic|purchasing|procurement|distribution"
           r"|human resources|\bhr\b|recruiting intern|recruiting coordinator|\brecruiter\b|\btalent\b"
           r"|accounting|\bfinance\b|financial analys|credit analyst|venture capital|investment services|wealth management"
           r"|marketing|market research|\bbrand\b|social media|\bmedia\b|editorial"
           r"|newsgathering|content (?:strateg|marketing|writ|produc|moderat|design|creat)|community engagement|sponsorship|\bsports\b"
           r"|outside sales|\bretail\b|\binsights?\b"
           r"|manufacturing|mechanical|\boperations\b|maintenance|warranty|thermal|drafter"
           r"|graphic design|visual design|instructional design|industrial design"
           r"|biolog|vaccine|clinical|formulation|pharmac|\bmaterials\b|paint|coating"
           r"|radiolog|chemist|skillbridge"
           r"|legal|counsel|compliance|administrat|archivist|polling|real estate|returning plann"
           r"|communication|publicity"
           r"|relationship manager|investor engage|business development"
           r"|aerospace|payload|\bgnc\b|guidance, navigation|propulsion"
           # Company watch-list boards are a company's whole intern programme.
           # fetch_companies used to fall back to the watch-list category when
           # no rule matched (Uline's warehouse and sales interns filed as swe,
           # Caterpillar's EHS intern as data_science); since 2026-09-02 an
           # unmatched title is dropped and counted instead, and these keep
           # the recurring families deterministic.
           r"|warehouse management|sales analyst|\binspector\b"
           r"|project controls|health and safety"
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
