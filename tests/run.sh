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

# Anaconda on Windows ships `python` and no `python3`; most Linux boxes are the
# other way round. Worse, Windows puts a Microsoft Store *stub* on PATH named
# `python3` that exists, resolves, and is not Python — it prints "Python was
# not found" and exits. So each candidate is probed by running it, not by
# asking whether the name resolves.
PY=""
for cand in python3 python py; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if [ "$cand" = "py" ]; then
    if py -3 -c "import sys" >/dev/null 2>&1; then PY="py -3"; break; fi
  elif "$cand" -c "import sys" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
if [ -z "$PY" ]; then
  echo "No working python on PATH." >&2
  echo "  A 'python3' that resolves to WindowsApps is the Store stub, not Python." >&2
  echo "  Put your real interpreter first, or run the suites directly:" >&2
  echo "    <your-python> tests/test_promoter_walk.py" >&2
  echo "    <your-python> tests/test_fundamentals.py" >&2
  exit 1
fi
echo "python: $($PY -c 'import sys; print(sys.executable, sys.version.split()[0])')"
HAVE_NODE="$(command -v node || true)"

mkdir -p build
$PY - <<'PYEOF'
import json
import fundamentals as fm
import fundamentals_view as fdv
import sector_map as sm
import sector_view as sv

with open("build/sector.js", "w", encoding="utf-8") as f:
    f.write(sv.SECTOR_JS)
with open("build/fund.js", "w", encoding="utf-8") as f:
    f.write(fdv.FUND_JS)

# The fundamentals assertions run against the real snapshot, not a fixture: the
# insurance book-basis divergence is the thing being guarded and a hand-written
# fixture would not reproduce it.
d = fm.load("reference/fundamentals.csv")
with open("build/fundamentals.json", "w", encoding="utf-8") as f:
    json.dump(fm.payload(d, fm.asof_from_name("E 30072026.csv")), f)

m = sm.load()
with open("build/sec.json", "w", encoding="utf-8") as f:
    json.dump({s: (m.at[s, "group"] if s in m.index else "Unmapped")
               for s in d.index}, f)
PYEOF

if [ -n "$HAVE_NODE" ]; then
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
  echo "── preview svgs ──"
  cat tests/_stubs.js build/fund.js tests/_preview.js > build/preview.js
  node build/preview.js
else
  echo
  echo "node not found — skipping the dashboard js suites."
  echo "They are a safety net, not a gate; the python suites below still run."
fi

echo
echo "── promoter walk ──"
$PY tests/test_promoter_walk.py

echo
echo "── fundamentals join ──"
$PY tests/test_fundamentals.py
