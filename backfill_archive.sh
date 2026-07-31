#!/usr/bin/env bash
#
# backfill_archive.sh — one-time import of an existing floor sheet dump into
# the `data` branch. Run it once; the daily workflow appends after that.
#
#   bash backfill_archive.sh "/d/analysis/Floorsheet"           # convert only
#   bash backfill_archive.sh "/d/analysis/Floorsheet" --push     # convert + push
#
# Every step is guarded. The archive is built in a directory OUTSIDE the repo,
# so nothing here can commit to, re-init, or force-push your code branch.

set -euo pipefail

SRC="${1:-}"
PUSH="${2:-}"

if [ -z "$SRC" ]; then
  echo "usage: bash backfill_archive.sh <dump-folder> [--push]" >&2
  echo "   eg: bash backfill_archive.sh \"/d/analysis/Floorsheet\" --push" >&2
  exit 2
fi

# ---- must be run from the repo, which is where floorsheet_archive.py lives --
if [ ! -f floorsheet_archive.py ]; then
  echo "ERROR: run this from the repo root (floorsheet_archive.py is not here)." >&2
  exit 2
fi

if [ ! -d "$SRC" ]; then
  echo "ERROR: dump folder not found: $SRC" >&2
  echo "  Windows drives look like /d/analysis/Floorsheet in Git Bash." >&2
  echo "  Tip: drag the folder onto this window to paste its path." >&2
  exit 2
fi

# ---- find a real Python -----------------------------------------------------
# On Windows, a bare `python` is often the Microsoft Store alias stub, which
# prints "Python was not found" and exits nonzero. Probe for one that runs.
PY=""
for c in py python3 python; do
  if command -v "$c" >/dev/null 2>&1 && [ "$("$c" -c 'print(42)' 2>/dev/null)" = "42" ]; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  cat >&2 <<'MSG'
ERROR: no working Python found.

  "Python was not found" means Windows is routing `python` to the Microsoft
  Store stub rather than a real interpreter. Either:

    1. Install from https://www.python.org/downloads/ and tick
       "Add python.exe to PATH", then reopen Git Bash; or
    2. Settings > Apps > Advanced app settings > App execution aliases,
       turn OFF python.exe and python3.exe.

  Then check with:  py --version
MSG
  exit 3
fi
echo "Using $PY ($("$PY" --version 2>&1))"

if ! "$PY" -c 'import pyarrow' 2>/dev/null; then
  echo "Installing dependencies..."
  "$PY" -m pip install -q -r requirements.txt
fi

# ---- build the archive outside the repo -------------------------------------
WORK="${FS_ARCHIVE_DIR:-$HOME/fs-archive-build}"
REPO_URL="$(git config --get remote.origin.url)"
echo "Building in $WORK (outside the repo)"
rm -rf "$WORK"
mkdir -p "$WORK/parquet"

"$PY" floorsheet_archive.py ingest \
  --src "$SRC" \
  --dir "$WORK/parquet" \
  --manifest "$WORK/manifest.csv" \
  --readme "$WORK/README.md" \
  --repo "$REPO_URL"

COUNT="$(find "$WORK/parquet" -name '*.parquet' | wc -l | tr -d ' ')"
if [ "$COUNT" -eq 0 ]; then
  echo "ERROR: no sessions were converted — nothing to push." >&2
  exit 4
fi
SIZE="$(du -sh "$WORK/parquet" | cut -f1)"
echo
echo "Converted $COUNT session(s), $SIZE total."
echo "Range: $(tail -1 "$WORK/manifest.csv" | cut -d, -f1) to $(sed -n 2p "$WORK/manifest.csv" | cut -d, -f1)"

if [ "$PUSH" != "--push" ]; then
  cat <<MSG

Nothing pushed. Review it:

  head -5 "$WORK/manifest.csv"
  ls "$WORK/parquet" | head

Then re-run with --push to publish to the data branch:

  bash backfill_archive.sh "$SRC" --push
MSG
  exit 0
fi

# ---- publish ----------------------------------------------------------------
# Subshell + explicit cd guard: if the cd fails, the push cannot run.
(
  cd "$WORK" || exit 5
  git init -q
  git config user.name  "$(git -C "$OLDPWD" config user.name  || echo 'floorsheet')"
  git config user.email "$(git -C "$OLDPWD" config user.email || echo 'floorsheet@local')"
  git checkout -q --orphan archive-import
  git add -A
  git commit -q -m "Backfill floor sheet archive: $COUNT sessions"
  git push -q --force "$REPO_URL" archive-import:data
)

echo
echo "Pushed $COUNT session(s) to the data branch."
echo "Check: ${REPO_URL%.git}/tree/data"
echo "The daily workflow appends to this from here on — do not run this again."
