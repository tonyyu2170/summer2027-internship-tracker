"""Apply an ats_corrections.json (from check_ats.py) to data/*.yaml — the
single serialized writer of a verification run. Applies
set_date/close/delete_non_us, stamps last_verified on every
row whose probe resolved, clears possible_duplicate_of pointers into
deleted rows, validates every touched row against ROW_SCHEMA before
anything is written (an error this apply INTRODUCED aborts the whole run;
one the row already had is warned about and kept), rewrites the category
YAML of every category that changed, and re-renders README.md. Aborts
before writing if a corrections id matches more than one row. Never runs
git.

Usage: python3 scripts/apply_ats_corrections.py [scratch/ats_corrections.json]
"""
import copy
import json
import sys
import yaml
from pathlib import Path
from datetime import date

from schema import validate_row
from generate_readme import render, ROOT, CATEGORIES
from merge import _slug
from normalize import normalize_link

# actions that prove the posting was authoritatively seen this run
_RESOLVED = {"confirm", "set_date", "close"}


def apply_corrections(rows_by_category, actions, today):
    """Pure. Returns (new_rows_by_category, summary); never mutates input.
    summary maps outcome kinds to sorted row-id lists; 'skipped' holds ids
    from the corrections file that no longer exist in the data."""
    # deepcopy, not dict(): a shallow copy shares the nested `degree` and
    # `sources` lists with the caller, so the never-mutates guarantee would
    # hold only as long as no action touches a nested value.
    rows_by_category = copy.deepcopy(rows_by_category)
    index = {}
    for rows in rows_by_category.values():
        for row in rows:
            if row.get("id"):
                index[row["id"]] = row
    summary = {k: [] for k in (
        "confirmed", "date_fixed", "closed", "deleted", "reposted",
        "recategorized", "kept", "dropped",
        "unknown", "skipped", "unrecognized_action")}
    deleted, verified, moved = set(), set(), {}
    for a in actions:
        rid, act = a.get("id"), a.get("action")
        if act == "ambiguous":
            # check_reposts couldn't tell which new posting replaces which
            # row; it prints them for hand review and carries no single id.
            continue
        row = index.get(rid)
        if row is None:
            summary["skipped"].append(rid)
            continue
        if act in _RESOLVED:
            verified.add(rid)
        if act == "confirm":
            summary["confirmed"].append(rid)
        elif act == "set_date":
            # "new" not in a, rather than falsy: a hand-edited corrections
            # file missing the key should degrade to a skipped correction
            # rather than crash a delete-capable run mid-loop, while an
            # empty value still reaches the schema gate.
            if "new" not in a:
                summary["unrecognized_action"].append(rid)
                continue
            row["date_posted"] = a["new"]
            row["date_estimated"] = False
            summary["date_fixed"].append(rid)
        elif act == "close":
            row["status"] = "closed"
            summary["closed"].append(rid)
        elif act == "repost":
            # The role was re-listed under a new requisition id, so the row
            # points at a superseded posting and carries its stale date.
            if "new_link" not in a:
                summary["unrecognized_action"].append(rid)
                continue
            row["link"] = a["new_link"]
            if a.get("new_date"):
                row["date_posted"] = a["new_date"]
                row["date_estimated"] = False
            # The id is a hash of the link (merge._slug); leaving it alone
            # is the known id/link drift bug, which surfaces as duplicate
            # ids on the next scrape. last_verified is stamped here rather
            # than via `verified`, whose lookup keys on the id we just
            # replaced.
            row["id"] = _slug(row["company"], row["role"],
                              normalize_link(a["new_link"]))
            row["last_verified"] = today
            # The NEW id, so the schema gate in run() can find the row it
            # has to validate; the old one no longer exists in the data.
            summary["reposted"].append(row["id"])
        elif act == "delete_non_us":
            deleted.add(rid)
            summary["deleted"].append(rid)
        elif act == "unknown":
            summary["unknown"].append(rid)
        elif act == "recategorize":
            # A typo in `to` must not silently vanish a row: reject it the way
            # a renamed action kind is rejected, and leave the row alone.
            if a.get("to") not in rows_by_category:
                summary["unrecognized_action"].append(rid)
                continue
            moved[rid] = a["to"]
            summary["recategorized"].append(rid)
        elif act == "keep":
            # The row stays put; run() records the decision in
            # manual_categories.yaml so the sweep stops re-reporting it.
            # Falsy, not "from" not in a: run() writes `from` verbatim into
            # that file with no downstream gate, so "" and None must be caught here.
            if not a.get("from"):
                summary["unrecognized_action"].append(rid)
                continue
            summary["kept"].append(rid)
        elif act == "drop":
            deleted.add(rid)
            summary["dropped"].append(rid)
        else:
            # An action kind we don't implement, on a row that DOES exist —
            # a typo or a renamed action, not a stale id. Kept separate from
            # "skipped" so it can't be reported as a missing row.
            summary["unrecognized_action"].append(rid)
    new, relocated = {}, []
    for cat, rows in rows_by_category.items():
        kept = []
        for row in rows:
            rid = row.get("id")
            if rid in deleted:
                continue
            if rid in verified:
                row["last_verified"] = today
            if row.get("possible_duplicate_of") in deleted:
                row["possible_duplicate_of"] = None
            if rid in moved:
                relocated.append((moved[rid], row))
                continue
            kept.append(row)
        new[cat] = kept
    for target, row in relocated:
        new[target].append(row)
    for ids in summary.values():
        ids.sort(key=str)
    return new, summary


def run(corrections_path, data_dir=None, readme_path=None, overrides_path=None):
    corrections_path = Path(corrections_path)
    data_dir = Path(data_dir) if data_dir else ROOT / "data"
    readme_path = Path(readme_path) if readme_path else ROOT / "README.md"
    overrides_path = (Path(overrides_path) if overrides_path
                      else ROOT / "sources" / "manual_categories.yaml")
    # A corrections file is hand-inspectable and hand-editable between the
    # probe and the apply, so bad JSON is operator error, not a bug — say so
    # plainly instead of surfacing a traceback from a delete-capable tool.
    try:
        doc = json.loads(corrections_path.read_text())
        actions = doc["actions"]
        if not isinstance(actions, list):
            raise TypeError("'actions' is not a list")
    except FileNotFoundError:
        raise SystemExit(f"no corrections file at {corrections_path}")
    except (ValueError, KeyError, TypeError) as e:
        raise SystemExit(
            f"{corrections_path} is not a valid corrections file ({e}); "
            f"nothing written.")

    rows_by_category = {}
    for stem, _title, _is_quant in CATEGORIES:
        path = data_dir / f"{stem}.yaml"
        rows_by_category[stem] = (
            (yaml.safe_load(path.read_text()) or []) if path.exists() else [])

    # Corrections are matched to rows by id alone, so a duplicate id would
    # apply one row's correction to another row entirely: a delete would
    # remove every row sharing the id (reporting one), and a set_date
    # would land on whichever row loaded last. Duplicate ids are a known,
    # unfixed upstream bug in merge.py's id hash, and run_scrape_merge
    # deliberately writes them to disk anyway rather than lose a listing.
    # Refuse to touch a dataset in that state instead of guessing.
    seen, dupes = {}, set()
    for cat, rows in rows_by_category.items():
        for row in rows:
            rid = row.get("id")
            if rid is None:
                continue
            if rid in seen:
                dupes.add(rid)
            seen[rid] = cat
    colliding = sorted(dupes.intersection(
        {a.get("id") for a in actions}))
    if colliding:
        for rid in colliding:
            print(f"DUPLICATE ID: {rid!r} matches more than one row")
        raise SystemExit(
            f"{len(colliding)} corrections id(s) match multiple rows; "
            f"nothing written. Resolve the duplicate ids first.")

    today = date.today().isoformat()
    new_rows, summary = apply_corrections(rows_by_category, actions, today)

    # Validate the rows this run touched — but only fail on errors this run
    # INTRODUCED. A row that already failed schema before the apply is a
    # pre-existing hand-edit, tolerated with a warning exactly as
    # run_scrape_merge does for rows loaded from disk. Without the
    # before-comparison, `confirm` (the modal outcome, which changes nothing
    # but last_verified) would drag every such row into the gate and let one
    # stale typo anywhere in the dataset block the whole verification.
    touched = set()
    for kind in ("confirmed", "date_fixed", "closed", "reposted"):
        touched.update(summary[kind])
    before_errors = {}
    for rows in rows_by_category.values():
        for row in rows:
            if row.get("id") in touched:
                before_errors[row["id"]] = set(validate_row(row))
    errors, tolerated = [], []
    for cat, rows in new_rows.items():
        for row in rows:
            if row.get("id") not in touched:
                continue
            was = before_errors.get(row["id"], set())
            for err in validate_row(row):
                if err in was:
                    tolerated.append(f"[{cat}] {row['id']}: {err}")
                else:
                    errors.append(f"[{cat}] {row['id']}: {err}")
    for t in tolerated:
        print(f"    warn: pre-existing schema error, kept as-is — {t}")
    if errors:
        for e in errors:
            print(f"SCHEMA: {e}")
        raise SystemExit(
            f"{len(errors)} schema error(s) introduced by this apply; "
            f"nothing written.")

    # Write only categories whose rows actually changed. safe_dump re-wraps
    # long lines, so rewriting all six turns a one-row correction into a
    # diff across every category file — noise in exactly the diff a human
    # reviews before committing a delete-capable run.
    for cat, rows in new_rows.items():
        if rows == rows_by_category.get(cat):
            continue
        (data_dir / f"{cat}.yaml").write_text(
            yaml.safe_dump(rows, sort_keys=False, allow_unicode=True))
    render(data_dir, readme_path)

    # Report deletes from the summary, not from the raw actions: an action
    # naming a row id that isn't in the data deletes nothing, and announcing
    # it would claim a destructive act that never happened.
    detail = {a.get("id"): a for a in actions
              if a.get("action") == "delete_non_us"}
    for rid in summary["deleted"]:
        a = detail.get(rid, {})
        print(f"    DELETED (non-US): [{rid}] "
              f"api_locations={a.get('api_locations')} "
              f"country={a.get('country')}")
    for rid in summary["closed"]:
        print(f"    closed: [{rid}]")
    # Suppress every superseded link, or the next scrape re-imports the old
    # posting as a second row carrying the stale date all over again.
    superseded = sorted({a["old_link"] for a in actions
                         if a.get("action") == "repost" and a.get("old_link")})
    if summary["reposted"] and superseded:
        with open(overrides_path, "a") as f:
            f.write("# auto apply_ats_corrections: superseded by a repost.\n")
            f.write(yaml.safe_dump({l: "__drop__" for l in superseded},
                                   sort_keys=True))
    # Record every category adjudication so check_categories stops re-reporting
    # it. Keyed off the summary, not the raw actions, so an action whose row no
    # longer exists never writes a decision about a row that is not there.
    applied_keep, applied_drop = set(summary["kept"]), set(summary["dropped"])
    adjudicated = {}
    for a in actions:
        rid, link = a.get("id"), a.get("link")
        if not link:
            continue
        if a.get("action") == "keep" and rid in applied_keep:
            adjudicated[link] = a["from"]
        elif a.get("action") == "drop" and rid in applied_drop:
            adjudicated[link] = "__drop__"
    if adjudicated:
        with open(overrides_path, "a") as f:
            f.write("# auto apply_ats_corrections: category adjudication.\n")
            f.write(yaml.safe_dump(adjudicated, sort_keys=True))
    for rid in summary["recategorized"]:
        print(f"    recategorized: [{rid}]")
    for rid in summary["kept"]:
        print(f"    kept: [{rid}]")
    for rid in summary["dropped"]:
        print(f"    dropped: [{rid}]")
    for rid in summary["reposted"]:
        print(f"    reposted -> [{rid}]")
    for rid in summary["skipped"]:
        print(f"    warn: skipped correction for unknown row id {rid!r}")
    for rid in summary["unrecognized_action"]:
        print(f"    warn: unrecognized action kind for existing row {rid!r}")
    print(", ".join(f"{k}={len(v)}" for k, v in summary.items()))
    return summary


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1
        else ROOT / "scratch" / "ats_corrections.json")
