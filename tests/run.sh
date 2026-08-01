#!/usr/bin/env bash
# tests/run.sh — build the Sectors tab js out of sector_view.py, glue the stubs
# and the assertions around it, and run the lot under node.
#
# One concatenated file rather than three modules on purpose: the tab declares
# its state with `let` at top level and expects the dashboard's helpers to be in
# the same scope. Importing or eval-ing it would put those bindings somewhere the
# assertions cannot reach.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p build
python3 - <<'PY'
import sector_view as sv
with open("build/sector.js", "w", encoding="utf-8") as f:
    f.write(sv.SECTOR_JS)
PY

node --check build/sector.js
echo "sector.js parses"

cat tests/_stubs.js build/sector.js tests/_asserts.js > build/sector_test.js
node build/sector_test.js
