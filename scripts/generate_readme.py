"""Render README.md from data/*.yaml. Pure transform; writes one file."""
import yaml
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parent.parent

CATEGORIES = [                       # (yaml stem, display title, is_quant)
    ("swe", "Software Engineering", False),
    ("quant", "Quantitative Finance", True),
    ("data_science", "Data Science", False),
    ("ai_ml", "AI/ML", False),
    ("hardware", "Hardware Engineering", False),
    ("actuarial", "Actuarial", False),
]

OPPORTUNITY_KINDS = [                # (yaml stem under data/opportunities/, display title)
    ("programs", "Programs"),
    ("research", "Research"),
    ("competitions", "Competitions"),
]

_OPP_STATUS_RANK = {"open": 0, "upcoming": 1, "unknown": 2, "closed": 3}


def _anchor(title: str) -> str:
    return title.lower().replace(" ", "-").replace("/", "")


def _status_cell(row: dict) -> str:
    cell = "🔒 Closed" if row.get("status") == "closed" else "🟢 Open"
    if row.get("possible_duplicate_of"):
        cell += f" ⚠️dup?({row['possible_duplicate_of']})"
    return cell


def _escape_cell(s: str) -> str:
    """Escape free text for a Markdown table cell: an unescaped pipe splits
    into an extra column, and a raw newline breaks the single-line row."""
    return str(s).replace("|", "\\|").replace("\r", "").replace("\n", " ")


def _row_cells(row: dict, is_quant: bool) -> str:
    cells = [_escape_cell(row["company"]), _escape_cell(row["role"])]
    if is_quant:
        cells.append(_escape_cell(row.get("track", "")))
    cells += [
        _escape_cell(row["location"]),
        f"[Apply](<{row['link']}>)",
        _escape_cell(("~" if row.get("date_estimated") else "") + row["date_posted"]),
        _escape_cell(row["term"]),
        _escape_cell("/".join(row["degree"])),
        _escape_cell(row["last_verified"]),
        _status_cell(row),
    ]
    return "| " + " | ".join(cells) + " |"


def _table(rows: list, is_quant: bool) -> str:
    header = ["Company", "Role"] + (["Track"] if is_quant else []) + \
        ["Location", "Link", "Date Posted", "Term", "Degree", "Last Verified", "Status"]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for r in sorted(rows, key=lambda r: r["date_posted"], reverse=True):
        lines.append(_row_cells(r, is_quant))
    return "\n".join(lines)


def _load(data_dir: Path, stem: str) -> list:
    p = data_dir / f"{stem}.yaml"
    return (yaml.safe_load(p.read_text()) or []) if p.exists() else []


def _load_opportunities(data_dir: Path, stem: str) -> list:
    """Tolerates a missing data/opportunities/ dir entirely (older callers
    and tests that only set up job-category YAML) by rendering []."""
    p = data_dir / "opportunities" / f"{stem}.yaml"
    return (yaml.safe_load(p.read_text()) or []) if p.exists() else []


def _format_opens(value):
    """'YYYY-MM' -> 'Sep 2026', 'YYYY-MM-DD' -> 'Sep 15, 2026'. A hand-edited
    watch-list can leave a date/datetime object (PyYAML) instead of a str,
    or a value that doesn't match either pattern at all — coerce the former,
    fall back to the raw value on the latter rather than raising."""
    if not value:
        return None
    if not isinstance(value, str):
        value = value.isoformat()
    try:
        if len(value) == 7:
            return datetime.strptime(value, "%Y-%m").strftime("%b %Y")
        return datetime.strptime(value, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        return value


def _opp_status_badge(row: dict) -> str:
    status = row.get("status")
    if status == "open":
        return "🟢 **Open**"
    if status == "upcoming":
        formatted = _format_opens(row.get("opens"))
        return f"⏳ `opens {formatted}`" if formatted else "⏳ Upcoming"
    if status == "closed":
        return "🔒 Closed"
    return "⚪ Unknown"


def _opp_sort_key(row: dict):
    """Open first, then upcoming by raw 'opens' ascending (null last among
    upcoming), then unknown, then closed. Sorts on the raw opens value only
    — badge/emoji text never enters the key. str()'d since a hand-edited
    watch-list can leave a date/datetime object instead of a string, which
    would otherwise TypeError against a sibling row's str opens."""
    rank = _OPP_STATUS_RANK.get(row.get("status"), _OPP_STATUS_RANK["unknown"])
    opens = row.get("opens")
    opens_key = (1, "") if not opens else (0, str(opens))
    return (rank, opens_key)


def _opp_row_cells(row: dict) -> str:
    category = row.get("category")
    opens_fmt = _format_opens(row.get("opens"))
    link = row.get("apply_url") or row.get("url") or "#"
    cells = [
        _escape_cell(row.get("name", "")),
        _escape_cell(row.get("org", "")),
        _escape_cell(category) if category else "—",
        _escape_cell(row.get("eligibility", "")),
        _escape_cell(opens_fmt) if opens_fmt else "—",
        f"[Apply](<{link}>)",
        _escape_cell(_opp_status_badge(row)),
    ]
    return "| " + " | ".join(cells) + " |"


def _opp_table(rows: list) -> str:
    header = ["Program", "Org", "Category", "Eligibility", "Opens", "Link", "Status"]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for r in sorted(rows, key=_opp_sort_key):
        lines.append(_opp_row_cells(r))
    return "\n".join(lines)


def render(data_dir=None, readme_path=None, last_run=None) -> Path:
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    rows_by_category = {stem: _load(data_dir, stem) for stem, _, _ in CATEGORIES}
    opp_rows_by_kind = {stem: _load_opportunities(data_dir, stem) for stem, _ in OPPORTUNITY_KINDS}
    open_count = sum(
        row.get("status") == "open"
        for rows in rows_by_category.values() for row in rows
    )
    last_run_clause = ""
    if last_run is not None:
        last_run_clause = (
            f" Last scrape: +{last_run.get('new', 0)} new, "
            f"{last_run.get('closed', 0)} closed."
        )
    out = [
        "# Summer 2027 Internship Tracker",
        "",
        f"_Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')} — "
        f"{open_count} open roles.{last_run_clause}_",
        "",
        "US-based Summer 2027 internships across six role categories.",
        "",
        "## Contents",
        "",
    ]
    out += [f"- [{title}](#{_anchor(title)})" for _, title, _ in CATEGORIES]
    out += [f"- [{title}](#{_anchor(title)})" for _, title in OPPORTUNITY_KINDS]
    out += [
        "",
        "**Legend** — Status: 🟢 Open · 🔒 Closed. Degree = BS/MS/PhD "
        "eligibility. ~Date Posted is estimated from when we first recorded "
        "the role. Last Verified is when the posting was last re-confirmed. "
        "⚠️dup? marks a possible duplicate pending manual review. Programs/"
        "Research/Competitions status: 🟢 **Open** · ⏳ `opens <date>` (or "
        "⏳ Upcoming if unannounced) · 🔒 Closed · ⚪ Unknown.",
        "",
    ]
    for stem, title, is_quant in CATEGORIES:
        rows = rows_by_category[stem]
        out += [f"## {title}", ""]
        out += [_table(rows, is_quant), ""] if rows else ["_No roles tracked yet._", ""]
    for stem, title in OPPORTUNITY_KINDS:
        rows = opp_rows_by_kind[stem]
        out += [f"## {title}", ""]
        out += [_opp_table(rows), ""] if rows else ["_No opportunities tracked yet._", ""]
    readme_path.write_text("\n".join(out) + "\n")
    return readme_path


if __name__ == "__main__":
    print(f"Wrote {render()}")
