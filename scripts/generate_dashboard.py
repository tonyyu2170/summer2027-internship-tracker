"""Render docs/dashboard.html — a one-page market view of data/*.yaml.

Pure and network-free: it reads the data files, the watch-list and the
scrape state, and writes one self-contained HTML page (inline SVG charts,
no libraries). `summarize` and `render_dashboard` are the tested
entrypoints; the CLI writes the file.

  python3 scripts/generate_dashboard.py [out.html]
"""
import html
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CATEGORIES = [("swe", "Software Engineering"), ("quant", "Quantitative Finance"),
              ("data_science", "Data Science"), ("ai_ml", "AI/ML"),
              ("hardware", "Hardware Engineering"), ("actuarial", "Actuarial")]
LABEL = dict(CATEGORIES)
WEEKS = 16
TOP_COMPANIES = 12
NEWEST = 40
ATS_ORDER = ["workday", "greenhouse", "ashby", "lever", "smartrecruiters", "icims", "custom"]

# Categorical slots in fixed order (dataviz reference palette, validated
# light + dark); a category keeps its slot on every chart.
_SLOT = {cat: i + 1 for i, (cat, _) in enumerate(CATEGORIES)}
_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]


def _load(data_dir, sources_dir):
    rows = []
    for cat, _ in CATEGORIES:
        path = data_dir / f"{cat}.yaml"
        for row in (yaml.safe_load(path.read_text()) if path.exists() else None) or []:
            rows.append({**row, "category": cat})
    boards = yaml.safe_load((sources_dir / "companies.yaml").read_text()) or {}
    state_path = sources_dir / "scrape_state.yaml"
    state = yaml.safe_load(state_path.read_text()) if state_path.exists() else {}
    return rows, boards, state or {}


def _d(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def summarize(rows, boards, today):
    """All the numbers the page shows, as plain data."""
    open_rows = [r for r in rows if r.get("status") == "open"]
    by_cat = Counter(r["category"] for r in open_rows)
    week0 = today - timedelta(days=today.weekday())          # Monday of this week
    weeks = [week0 - timedelta(weeks=i) for i in range(WEEKS - 1, -1, -1)]
    per_week = {w: Counter() for w in weeks}
    for r in rows:
        posted = _d(r.get("date_posted"))
        if not posted:
            continue
        monday = posted - timedelta(days=posted.weekday())
        if monday in per_week:
            per_week[monday][r["category"]] += 1
    companies = Counter(r["company"] for r in open_rows)
    degrees = Counter(d for r in open_rows for d in (r.get("degree") or []))
    sources = Counter()
    for r in open_rows:
        # "github_tracker:chieler" -> "chieler"; "company:acme" stays whole so
        # board sources read as such next to the tracker handles.
        handles = {str(s).removeprefix("github_tracker:") for s in r.get("sources") or []}
        handles.discard("github_tracker")
        sources.update(handles)
    ats, unverified = Counter(), 0
    for entries in boards.values():
        for e in entries or []:
            if e.get("verified") is False:
                unverified += 1
            else:
                ats[e.get("ats") or "custom"] += 1
    newest = sorted(open_rows, key=lambda r: (str(r.get("date_posted")), str(r.get("date_added"))),
                    reverse=True)[:NEWEST]
    since = lambda days: sum(1 for r in open_rows
                             if (_d(r.get("date_posted")) or date.min) >= today - timedelta(days=days))
    estimated = sum(1 for r in rows if r.get("date_estimated"))
    return {
        "open": len(open_rows), "closed": len(rows) - len(open_rows),
        "last7": since(7), "last30": since(30),
        "companies": len(companies),
        "boards": sum(ats.values()), "boards_unverified": unverified,
        "by_cat": [(c, by_cat.get(c, 0)) for c, _ in CATEGORIES],
        "weeks": [(w, per_week[w]) for w in weeks],
        "top_companies": companies.most_common(TOP_COMPANIES),
        "degrees": [(d, degrees.get(d, 0)) for d in ("BS", "MS", "PhD")],
        "sources": sources.most_common(10),
        "ats": [(a, ats[a]) for a in ATS_ORDER if ats.get(a)] +
               [(a, n) for a, n in ats.items() if a not in ATS_ORDER],
        "newest": newest,
        "estimated_share": round(100 * estimated / len(rows)) if rows else 0,
    }


# ---- SVG ---------------------------------------------------------------

def _e(text):
    return html.escape(str(text), quote=True)


def _trunc(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def hbars(items, color, width=560, row=26, label_w=170):
    """Horizontal bars: items = [(label, value)], color(label) -> css color."""
    if not items:
        return '<p class="empty">No data yet.</p>'
    vmax = max(v for _, v in items) or 1
    plot_w = width - label_w - 56
    out = [f'<svg class="chart" viewBox="0 0 {width} {row * len(items) + 4}" role="img">']
    for i, (label, value) in enumerate(items):
        y = i * row + 2
        w = max(4, round(plot_w * value / vmax))
        out.append(
            f'<text x="{label_w - 10}" y="{y + 16}" text-anchor="end" class="lbl">{_e(_trunc(str(label), 26))}</text>'
            f'<rect x="{label_w}" y="{y + 4}" width="{w}" height="{row - 10}" rx="4" fill="{color(label)}" '
            f'data-tip="{_e(f"{label}: {value}")}"></rect>'
            f'<text x="{label_w + w + 8}" y="{y + 16}" class="val">{value}</text>')
    out.append("</svg>")
    return "".join(out)


def stacked_weeks(weeks, width=560, height=220):
    """Stacked weekly bars by category with a 2px surface gap per segment."""
    totals = [sum(c.values()) for _, c in weeks]
    vmax = max(totals) or 1
    left, bottom, top = 36, 28, 8
    plot_w, plot_h = width - left - 8, height - bottom - top
    slot = plot_w / len(weeks)
    bar_w = max(6, slot - 6)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img">']
    for frac in (0.5, 1.0):
        y = top + plot_h - plot_h * frac
        out.append(f'<line x1="{left}" x2="{width - 8}" y1="{y:.1f}" y2="{y:.1f}" class="grid"></line>'
                   f'<text x="{left - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{round(vmax * frac)}</text>')
    out.append(f'<line x1="{left}" x2="{width - 8}" y1="{top + plot_h}" y2="{top + plot_h}" class="axis"></line>')
    for i, (monday, counts) in enumerate(weeks):
        x = left + i * slot + (slot - bar_w) / 2
        y = top + plot_h
        for cat, _ in CATEGORIES:
            n = counts.get(cat, 0)
            if not n:
                continue
            seg = plot_h * n / vmax
            y -= seg
            tip = f"Week of {monday:%b %-d}: {n} {LABEL[cat]} · {totals[i]} total"
            out.append(f'<rect x="{x:.1f}" y="{y + 1:.1f}" width="{bar_w:.1f}" height="{max(seg - 2, 1):.1f}" '
                       f'fill="var(--c{_SLOT[cat]})" data-tip="{_e(tip)}"></rect>')
        if i % (2 if width >= 900 else 4) == (1 if width >= 900 else 3) or i == len(weeks) - 1:
            out.append(f'<text x="{x + bar_w / 2:.1f}" y="{height - 10}" text-anchor="middle" class="tick">{monday:%b %-d}</text>')
    out.append("</svg>")
    return "".join(out)


def _table(headers, rows):
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return (f'<details class="tbl"><summary>Show as table</summary><table><thead><tr>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></details>')


def _cat_color(cat):
    return f"var(--c{_SLOT[cat]})"


def _panel(title, note, chart, table):
    return f'<section class="panel"><h2>{_e(title)}</h2><p class="note">{_e(note)}</p>{chart}{table}</section>'


def _theme(bg, surface, ink, ink2, muted, rule, accent, grid, slots):
    tokens = (f"--bg:{bg}; --surface:{surface}; --ink:{ink}; --ink2:{ink2}; --muted:{muted}; "
              f"--rule:{rule}; --accent:{accent}; --grid:{grid}; ")
    return tokens + " ".join(f"--c{i + 1}:{c};" for i, c in enumerate(slots))


# One accent (desaturated, links and focus only) that is not one of the six
# category hues, so a single-series chart never reads as "the swe series":
# those bars take the secondary ink instead.
_LIGHT_THEME = ("#f5f6f8", "#eceef2", "#191c21", "#565d69", "#858c99", "#d9dde4", "#2c6e8e", "#e6e9ee")
_DARK_THEME = ("#15171b", "#1d2025", "#eceef1", "#b3b9c3", "#7c8391", "#2a2e35", "#6fb0cc", "#23272d")


def render_dashboard(data_dir=None, sources_dir=None, today=None, now=None):
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    sources_dir = Path(sources_dir) if sources_dir else ROOT / "sources"
    today = today or date.today()
    rows, boards, state = _load(data_dir, sources_dir)
    s = summarize(rows, boards, today)
    last = state.get("_last_run") or {}
    neutral = lambda label: "var(--ink2)"
    cat_items = [(LABEL[c], n) for c, n in s["by_cat"]]
    cat_of_label = {LABEL[c]: c for c, _ in CATEGORIES}
    legend = "".join(f'<span class="key"><i style="background:{_cat_color(c)}"></i>{LABEL[c]}</span>'
                     for c, _ in CATEGORIES)
    week_rows = [(f"{w:%Y-%m-%d}", *[c.get(cat, 0) for cat, _ in CATEGORIES], sum(c.values()))
                 for w, c in s["weeks"]]
    newest = "".join(
        f'<tr><td class="num">{_e(r.get("date_posted"))}{"~" if r.get("date_estimated") else ""}</td>'
        f'<td>{_e(r["company"])}</td><td>{_e(r["role"])}</td>'
        f'<td><span class="chip"><i style="background:{_cat_color(r["category"])}"></i>{LABEL[r["category"]]}</span></td>'
        f'<td class="num">{_e("/".join(r.get("degree") or []))}</td>'
        f'<td><a href="{_e(r["link"])}" target="_blank" rel="noopener">Apply</a></td></tr>'
        for r in s["newest"])
    stamp = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    figures = [("Open roles", s["open"], f'{s["closed"]} closed roles kept in the data'),
               ("Posted in the last 7 days", s["last7"], "by source posting date"),
               ("Posted in the last 30 days", s["last30"], f'{s["estimated_share"]}% of dates are first-seen estimates'),
               ("Companies hiring", s["companies"], "with at least one open role"),
               ("Boards watched", s["boards"], f'{s["boards_unverified"]} unverified boards are skipped')]
    figures_html = "".join(
        f'<div class="figure"><span class="label">{_e(t)}</span><span class="big">{n:,}</span><span class="sub">{_e(sub)}</span></div>'
        for t, n, sub in figures)
    last_run = f' Last scrape: +{last.get("new", 0)} new, {last.get("closed", 0)} closed.' if last else ""
    light, dark = _theme(*_LIGHT_THEME, _LIGHT), _theme(*_DARK_THEME, _DARK)
    breakdowns = [
        _panel("Open roles by category", "Closed roles are kept in the data but not counted here.",
               hbars(cat_items, lambda l: _cat_color(cat_of_label[l])),
               _table(["Category", "Open roles"], [(_e(l), n) for l, n in cat_items])),
        _panel("Companies with the most open roles", f'Top {TOP_COMPANIES} of {s["companies"]:,} companies with an open role.',
               hbars(s["top_companies"], neutral),
               _table(["Company", "Open roles"], [(_e(c), n) for c, n in s["top_companies"]])),
        _panel("Boards watched, by applicant tracking system",
               "Career boards on the watch-list. Workday, Greenhouse, Ashby, Lever and SmartRecruiters are pulled by the scrape; custom and iCIMS are not.",
               hbars(s["ats"], neutral), _table(["ATS", "Boards"], [(_e(a), n) for a, n in s["ats"]])),
        _panel("Where open listings come from", "Sources that reported each open role. A role can come from several.",
               hbars(s["sources"], neutral), _table(["Source", "Open roles"], [(_e(a), n) for a, n in s["sources"]])),
        _panel("Degree eligibility", "Open roles listing each degree. Most roles list more than one.",
               hbars(s["degrees"], neutral), _table(["Degree", "Open roles"], s["degrees"])),
    ]
    trend = _panel("New postings per week", f"Last {WEEKS} weeks by source posting date, open and closed rows alike.",
                   f'<div class="legend">{legend}</div>' + stacked_weeks(s["weeks"], width=1120, height=240),
                   _table(["Week of", *[l for _, l in CATEGORIES], "Total"], week_rows))
    return f"""<title>Summer 2027 Internship Radar</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Geist+Mono:wght@400;500&display=swap">
<style>
:root {{ color-scheme: light; {light} }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ color-scheme: dark; {dark} }} }}
:root[data-theme="dark"] {{ color-scheme: dark; {dark} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font-family:"Geist", "Helvetica Neue", Arial, sans-serif; font-size:14px; line-height:1.5; }}
.wrap {{ max-width:1180px; margin:0 auto; padding:28px 24px 64px; }}
.skip {{ position:absolute; left:-999px; top:8px; background:var(--ink); color:var(--bg); padding:6px 10px; border-radius:4px; }}
.skip:focus {{ left:8px; }}
h1 {{ font-weight:600; font-size:26px; letter-spacing:-0.02em; margin:0 0 4px; text-wrap:balance; }}
h2 {{ font-weight:600; font-size:15px; letter-spacing:-0.01em; margin:0 0 2px; }}
.status {{ color:var(--ink2); margin:0 0 28px; max-width:70ch; }}
.figures {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:16px 28px; border-top:1px solid var(--rule); padding-top:14px; margin-bottom:36px; }}
.figure {{ display:flex; flex-direction:column; gap:2px; min-width:0; }}
.label {{ font-size:12.5px; color:var(--ink2); font-weight:500; }}
.big {{ font-family:"Geist Mono", ui-monospace, Menlo, monospace; font-weight:500; font-size:30px; line-height:1.15; letter-spacing:-0.02em; font-variant-numeric:tabular-nums; }}
.sub {{ color:var(--muted); font-size:12px; }}
.trend {{ margin-bottom:32px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(440px, 1fr)); gap:28px 40px; }}
.panel {{ border-top:1px solid var(--rule); padding-top:12px; min-width:0; }}
.note {{ margin:0 0 12px; color:var(--ink2); font-size:12.5px; max-width:70ch; }}
.legend {{ display:flex; flex-wrap:wrap; gap:6px 16px; margin:0 0 10px; font-size:12px; color:var(--ink2); }}
.key i, .chip i {{ display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; vertical-align:0; }}
svg.chart {{ width:100%; height:auto; display:block; overflow:visible; }}
svg .lbl {{ font-size:12px; fill:var(--ink); font-family:"Geist", "Helvetica Neue", Arial, sans-serif; }}
svg .val, svg .tick {{ font-size:11px; fill:var(--ink2); font-family:"Geist Mono", ui-monospace, Menlo, monospace; }}
svg .grid {{ stroke:var(--grid); stroke-width:1; }}
svg .axis {{ stroke:var(--rule); stroke-width:1; }}
svg rect[data-tip] {{ transition:opacity 160ms ease; }}
svg rect[data-tip]:hover {{ opacity:0.75; }}
details.tbl {{ margin-top:10px; font-size:12px; }}
details.tbl summary {{ color:var(--accent); cursor:pointer; width:max-content; }}
details.tbl summary:hover {{ text-decoration:underline; }}
table {{ border-collapse:collapse; width:100%; margin-top:8px; }}
th, td {{ text-align:left; padding:7px 10px; vertical-align:top; }}
th {{ font-size:12px; color:var(--ink2); font-weight:500; border-bottom:1px solid var(--rule); }}
tbody tr:nth-child(even) td {{ background:var(--surface); }}
td.num {{ font-family:"Geist Mono", ui-monospace, Menlo, monospace; font-size:12.5px; white-space:nowrap; }}
.chip {{ white-space:nowrap; }}
.newest {{ margin-top:36px; border-top:1px solid var(--rule); padding-top:12px; overflow-x:auto; }}
.newest table {{ min-width:760px; }}
a {{ color:var(--accent); text-decoration:none; transition:color 160ms ease; }}
a:hover {{ text-decoration:underline; }}
a:active {{ transform:translateY(1px); display:inline-block; }}
a:focus-visible, summary:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; border-radius:2px; }}
#tip {{ position:fixed; pointer-events:none; background:var(--ink); color:var(--bg); padding:6px 9px; border-radius:4px; font-size:12px; font-family:"Geist Mono", ui-monospace, Menlo, monospace; white-space:nowrap; z-index:2; }}
footer {{ margin-top:40px; color:var(--muted); font-size:12px; max-width:80ch; border-top:1px solid var(--rule); padding-top:12px; }}
.empty {{ color:var(--muted); }}
@media (prefers-reduced-motion: reduce) {{ *, *::before, *::after {{ transition:none !important; }} }}
@media (max-width: 640px) {{ .wrap {{ padding:20px 16px 48px; }} .grid {{ grid-template-columns:1fr; }} }}
</style>
<a class="skip" href="#newest">Skip to the newest roles</a>
<main class="wrap">
<h1>Summer 2027 Internship Radar</h1>
<p class="status">US-only market listing across six categories, regenerated from the tracker's data files on {stamp}.{_e(last_run)}</p>
<div class="figures">{figures_html}</div>
<div class="trend">{trend}</div>
<div class="grid">{"".join(breakdowns)}</div>
<section class="newest" id="newest"><h2>Newest open roles</h2>
<p class="note">{NEWEST} most recent by posting date. A ~ marks a date estimated from when the role was first seen.</p>
<table><thead><tr><th>Posted</th><th>Company</th><th>Role</th><th>Category</th><th>Degree</th><th></th></tr></thead><tbody>{newest}</tbody></table></section>
<footer>Every row is a US-located Summer 2027 internship from the GitHub trackers and the direct company boards the tracker scrapes. Individual locations are not tracked. Source: data/*.yaml and sources/companies.yaml in the repository. The README is the full listing.</footer>
</main>
<div id="tip" hidden></div>
<script>
(function () {{
  var tip = document.getElementById('tip');
  document.addEventListener('mouseover', function (e) {{
    var t = e.target.closest && e.target.closest('[data-tip]');
    if (!t) {{ tip.hidden = true; return; }}
    tip.textContent = t.getAttribute('data-tip'); tip.hidden = false;
  }});
  document.addEventListener('mousemove', function (e) {{
    if (tip.hidden) return;
    var x = e.clientX + 14, y = e.clientY + 14;
    if (x + tip.offsetWidth > window.innerWidth - 8) x = e.clientX - tip.offsetWidth - 14;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }});
}})();
</script>
"""


def main(argv):
    out = Path(argv[0]) if argv else ROOT / "docs" / "dashboard.html"
    out.write_text(render_dashboard())
    print(f"wrote {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
