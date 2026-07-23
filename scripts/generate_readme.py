"""Render README.md from data/*.yaml. Pure transform; writes one file."""
import yaml
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent

CATEGORIES = [                       # (yaml stem, display title, is_quant)
    ("swe", "Software Engineering", False),
    ("quant", "Quantitative Finance", True),
    ("data_science", "Data Science", False),
    ("ai_ml", "AI/ML", False),
    ("hardware", "Hardware Engineering", False),
    ("actuarial", "Actuarial", False),
    ("consulting", "Consulting", False),
    ("ib", "Investment Banking", False),
]


def _anchor(title: str) -> str:
    return title.lower().replace(" ", "-").replace("/", "")


def _status_cell(row: dict) -> str:
    cell = "🔒 Closed" if row.get("status") == "closed" else "🟢 Open"
    if row.get("possible_duplicate_of"):
        cell += f" ⚠️dup?({row['possible_duplicate_of']})"
    return cell


def _row_cells(row: dict, is_quant: bool) -> str:
    cells = [row["company"], row["role"]]
    if is_quant:
        cells.append(row.get("track", ""))
    cells += [
        row["location"],
        f"[Apply]({row['link']})",
        row["date_posted"],
        row["term"],
        "/".join(row["degree"]),
        _status_cell(row),
    ]
    return "| " + " | ".join(cells) + " |"


def _table(rows: list, is_quant: bool) -> str:
    header = ["Company", "Role"] + (["Track"] if is_quant else []) + \
        ["Location", "Link", "Date Posted", "Term", "Degree", "Status"]
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


def render(data_dir=None, readme_path=None) -> Path:
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    out = [
        "# Summer 2027 Internship Tracker",
        "",
        f"_Last updated: {date.today().isoformat()}_",
        "",
        "US-based Summer 2027 internships across eight role categories. "
        "A market listing — not a personal application tracker.",
        "",
        "## Contents",
        "",
    ]
    out += [f"- [{title}](#{_anchor(title)})" for _, title, _ in CATEGORIES]
    out += [
        "",
        "**Legend** — Status: 🟢 Open · 🔒 Closed. Degree = BS/MS/PhD "
        "eligibility. Date Posted is the source's date where available, else "
        "the date we first recorded the role. ⚠️dup? marks a possible "
        "duplicate pending manual review.",
        "",
    ]
    for stem, title, is_quant in CATEGORIES:
        rows = _load(data_dir, stem)
        out += [f"## {title}", ""]
        out += [_table(rows, is_quant), ""] if rows else ["_No roles tracked yet._", ""]
    readme_path.write_text("\n".join(out) + "\n")
    return readme_path


if __name__ == "__main__":
    print(f"Wrote {render()}")
