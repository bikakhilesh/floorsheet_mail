#!/usr/bin/env bash
# tests/run.sh — build the dashboard js out of the *_view.py modules, glue the
# stubs and the assertions around it, and run the lot under node. Then the
# python suites, which need neither node nor a browser.
#
# One concatenated file per tab rather than modules on purpose: each tab
# declares its state with `let` at top level and expects the dashboard's
# helpers to be in the same scope. Importing or eval-ing would put those
# bindings somewhere the assertions cannot reach.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p build
python3 - <<'PY'
import json, sector_view as sv, fundamentals_view as fv, fundamentals as fm
open("build/sector.js", "w", encoding="utf-8").write(sv.SECTOR_JS)
open("build/fund.js", "w", encoding="utf-8").write(fv.FUND_JS)
# The fundamentals assertions run against the real snapshot, not a fixture:
# the insurance book-basis divergence is the thing being guarded and a
# hand-written fixture would not reproduce it.
d = fm.load("reference/fundamentals.csv")
json.dump(fm.payload(d, fm.asof_from_name("reference/E 30072026.csv")),
          open("build/fundamentals.json", "w", encoding="utf-8"))
PY

node --check build/sector.js && echo "sector.js parses"
node --check build/fund.js   && echo "fund.js parses"

echo
echo "── sectors ──"
cat tests/_stubs.js build/sector.js tests/_asserts.js > build/sector_test.js
node build/sector_test.js

echo
echo "── fundamentals ──"
cat tests/_stubs.js build/fund.js tests/_fund_asserts.js > build/fund_test.js
node build/fund_test.js

echo
echo "── promoter walk ──"
python3 tests/test_promoter_walk.py

echo
echo "── fundamentals join ──"
python3 tests/test_fundamentals.py
