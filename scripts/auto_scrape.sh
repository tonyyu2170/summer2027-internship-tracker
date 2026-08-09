#!/bin/bash
# One-shot plain-"scrape" runner (explicit request only — scraping is
# never scheduled; the 2026-08-09 launchd experiment was reverted same day).
#
# Runs exactly the plain-"scrape" pipeline from docs/SCRAPING.md (GitHub
# trackers only, no opt-in sources, no LLM), commits locally when data/
# actually changed, and stops loudly — appending to
# scratch/auto_scrape/NEEDS_ATTENTION — whenever the runbook calls for
# human judgment (unclassified postings, integrity violations, a dirty
# working tree). It never runs git push.
set -u
PATH=/usr/bin:/bin

REPO="/Users/turdy/unemploy/summer2027/internship-tracker"
PY="$REPO/.venv/bin/python3"
GIT="/usr/bin/git"
RUN_DIR="$REPO/scratch/auto_scrape"
LOG="$RUN_DIR/auto_scrape.log"
MARKER="$RUN_DIR/NEEDS_ATTENTION"
LOCK="$RUN_DIR/lock"

mkdir -p "$RUN_DIR"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }
attention() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$MARKER"; log "ATTENTION: $*"; }

cd "$REPO" || exit 1

# Single-writer discipline (docs/SCRAPING.md): never scrape while an ATS
# review is in flight or another writer is running.
if [ -f "$REPO/scratch/ats_corrections.json" ]; then
    log "skip: scratch/ats_corrections.json exists (ATS review in flight)"
    exit 0
fi
if pgrep -f 'run_scrape_merge\.py|apply_ats_corrections\.py' > /dev/null; then
    log "skip: another writer process is running"
    exit 0
fi

# A crashed run must not block the schedule forever.
if [ -d "$LOCK" ] && [ -n "$(find "$LOCK" -maxdepth 0 -mmin +180 2>/dev/null)" ]; then
    log "warn: removing stale lock (>3h old)"
    rmdir "$LOCK" 2>/dev/null
fi
if ! mkdir "$LOCK" 2>/dev/null; then
    log "skip: another run holds the lock"
    exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Don't run on top of in-progress manual work. scrape_state.yaml dirt is
# expected steady-state (every fetch rewrites it) and is exempt.
if [ -n "$($GIT status --porcelain -- data/ README.md)" ]; then
    attention "skip: uncommitted changes in data/ or README.md"
    exit 0
fi

log "run: fetch_trackers.py"
"$PY" scripts/fetch_trackers.py >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    attention "fetch_trackers.py exited $rc — see log"
    exit 1
fi

log "run: run_scrape_merge.py"
"$PY" scripts/run_scrape_merge.py scratch/fetch_reports >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    # Either unclassified postings are pending (nothing written) or the
    # integrity check failed (data written but uncommitted). Both need a
    # human; fetch reports stay in scratch/fetch_reports/ for that.
    attention "run_scrape_merge.py exited $rc — see log; fetch reports kept"
    exit 1
fi

if $GIT diff --quiet -- data/; then
    # No listing changes. Drop the README timestamp-only churn so "Last
    # updated" keeps reflecting when listings last changed; scrape_state
    # stays dirty (it's the SHA skip cache) and rides into the next real
    # commit.
    $GIT checkout -q -- README.md
    rm -f scratch/fetch_reports/*.json
    rm -f "$MARKER"
    log "done: no listing changes"
    exit 0
fi

$GIT add data/ sources/scrape_state.yaml README.md
if ! $GIT commit -q -m "scrape: auto-update roles as of $(date '+%Y-%m-%d %H:%M')" >> "$LOG" 2>&1; then
    attention "git commit failed — see log"
    exit 1
fi
rm -f scratch/fetch_reports/*.json
rm -f "$MARKER"
log "done: committed $($GIT rev-parse --short HEAD)"
