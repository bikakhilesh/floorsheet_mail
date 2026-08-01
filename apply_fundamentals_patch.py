#!/usr/bin/env python3
"""
apply_fundamentals_patch.py — wire fundamentals_view into dashboard_site.py.

    python apply_fundamentals_patch.py --check
    python apply_fundamentals_patch.py --dry-run
    python apply_fundamentals_patch.py

Runs *after* apply_sector_patch.py and refuses to run before it: several anchors
are lines that patch inserted. Same contract as that one — every anchor must
appear exactly once, nothing is written unless all of them resolve, and running
twice is refused rather than repeated.
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import sys

TARGET = "dashboard_site.py"
MARKER = "__FUND_JS__"
NEEDS = "__SECTOR_JS__"        # the sector patch must have run first

EDITS: list[tuple[str, str, str]] = [

    # `fv` is already floorsheet_viz — binding fundamentals_view to it shadows
    # the module build_site_data calls for filename_date, and the build dies on
    # the first parquet it looks at. Hence `fdv`.
    ("import fundamentals modules",
     "import sector_map as sm\nimport sector_view as sv\n",
     "import fundamentals as fm\nimport fundamentals_view as fdv\n"
     "import sector_map as sm\nimport sector_view as sv\n"),

    ("css placeholder",
     "__SECTOR_CSS__\n</style></head><body>",
     "__SECTOR_CSS__\n__FUND_CSS__\n</style></head><body>"),

    ("nav tab",
     '    <button data-t="sectors">Sectors</button>',
     '    <button data-t="sectors">Sectors</button>\n'
     '    <button data-t="fund">Fundamentals</button>'),

    ("panel placeholder",
     "__SECTOR_PANEL__\n\n"
     '  <div class="panel" id="p-blocks"><div class="card">',
     "__SECTOR_PANEL__\n__FUND_PANEL__\n\n"
     '  <div class="panel" id="p-blocks"><div class="card">'),

    ("scrips valuation columns",
     "    {k:'sector',h:'Sector',f:r=>r.sector||'—'},",
     "    {k:'sector',h:'Sector',f:r=>r.sector||'—'},\n"
     "    {k:'pe',h:'P/E',num:1,f:r=>r.pe==null?'—':r.pe.toFixed(1)},\n"
     "    {k:'pb',h:'P/B',num:1,f:r=>r.pb==null?'—':r.pb.toFixed(2)},"),

    ("tag scrips with live multiples",
     "  SCRIPS.forEach(s=>{s.sector=secGroupAll(s.sym);}); secFillScripFilter();",
     "  SCRIPS.forEach(s=>{s.sector=secGroupAll(s.sym);\n"
     "    const _f=(typeof fundRow==='function')?fundRow(s):null;\n"
     "    s.pe=_f?_f.peLive:null; s.pb=_f?_f.pbvLive:null;});\n"
     "  secFillScripFilter();"),

    ("re-render fundamentals on day change",
     "  if($('#p-sectors').classList.contains('on'))renderSectors();",
     "  if($('#p-sectors').classList.contains('on'))renderSectors();\n"
     "  if($('#p-fund').classList.contains('on'))renderFund();"),

    ("fundamentals tab click",
     "  if(b.dataset.t==='sectors')renderSectors();});",
     "  if(b.dataset.t==='sectors')renderSectors();\n"
     "  if(b.dataset.t==='fund')renderFund();});"),

    ("script placeholder",
     "__SECTOR_JS__\n\n/* ---------- wiring ---------- */",
     "__SECTOR_JS__\n__FUND_JS__\n\n/* ---------- wiring ---------- */"),

    ("load the snapshot at boot",
     "  await secLoad();",
     "  await secLoad();\n  await fundLoad();"),

    ("write the snapshot during the build",
     "    write_sectors(args.out, args.listed)",
     "    write_sectors(args.out, args.listed)\n"
     "    write_fundamentals(args.out, args.fundamentals, args.listed)"),

    ("--fundamentals argument",
     '    ap.add_argument("--listed", default=sm.DEFAULT_PATH,\n'
     '                    help="listed-securities csv behind the sector map")',
     '    ap.add_argument("--listed", default=sm.DEFAULT_PATH,\n'
     '                    help="listed-securities csv behind the sector map")\n'
     '    ap.add_argument("--fundamentals", default=fm.DEFAULT_PATH,\n'
     '                    help="npstocks snapshot behind the Fundamentals tab")'),

    ("offline copy carries the snapshot",
     '        "sectors": _read_sectors(site_dir),',
     '        "sectors": _read_sectors(site_dir),\n'
     '        "fundamentals": _read_json(site_dir, "fundamentals.json"),'),

    ("helpers",
     "def _read_sectors(site_dir: str):",
     '''def write_fundamentals(out: str, path: str,
                       listed: str | None = None) -> str | None:
    """data/fundamentals.json — the npstocks snapshot, keyed to symbols.

    Missing is a warning, not a failure: the site is useful without it and the
    tab says so itself. A join that resolves nothing, though, is a broken alias
    file rather than an empty one, so that gets said out loud.
    """
    try:
        d = fm.load(path, listed)
    except FileNotFoundError as e:
        print(f"WARNING: {e}\\n         The Fundamentals tab will be empty.")
        return None
    p = fm.write_payload(d, out, fm.asof_from_name(path))
    un = d.attrs.get("unmatched", [])
    print(f"Fundamentals: {p} ({os.path.getsize(p) / 1024:,.0f} KB, "
          f"{len(d)} symbols"
          + (f", {len(un)} unmatched" if un else "") + ")")
    if un:
        print("  unmatched: " + ", ".join(un[:8])
              + (" …" if len(un) > 8 else ""))
    return p


def _read_json(site_dir: str, name: str):
    p = os.path.join(site_dir, "data", name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _read_sectors(site_dir: str):'''),

    ("render the fundamentals blocks",
     '    return (APP.replace("__SECTOR_CSS__", sv.SECTOR_CSS)\n'
     '               .replace("__SECTOR_PANEL__", sv.SECTOR_PANEL)\n'
     '               .replace("__SECTOR_JS__", sv.SECTOR_JS)\n'
     '               .replace("__EMBED__", embed))',
     '    return (APP.replace("__SECTOR_CSS__", sv.SECTOR_CSS)\n'
     '               .replace("__SECTOR_PANEL__", sv.SECTOR_PANEL)\n'
     '               .replace("__SECTOR_JS__", sv.SECTOR_JS)\n'
     '               .replace("__FUND_CSS__", fdv.FUND_CSS)\n'
     '               .replace("__FUND_PANEL__", fdv.FUND_PANEL)\n'
     '               .replace("__FUND_JS__", fdv.FUND_JS)\n'
     '               .replace("__EMBED__", embed))'),
]


def apply(text: str) -> tuple[str, list[str]]:
    problems = []
    for name, old, _ in EDITS:
        n = text.count(old)
        if n != 1:
            problems.append(
                f"  [{name}] anchor appears {n} times, expected 1\n"
                f"      {old.splitlines()[0][:88]!r}")
    if problems:
        return text, problems
    for _, old, new in EDITS:
        text = text.replace(old, new, 1)
    return text, []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Wire fundamentals into dashboard_site")
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args(argv)

    if args.list:
        for i, (name, old, new) in enumerate(EDITS, 1):
            print(f"\n{'=' * 74}\n{i:2}. {name}\n{'-' * 74}\nBEFORE\n{old}\n"
                  f"{'-' * 74}\nAFTER\n{new}")
        return 0

    if not os.path.exists(args.file):
        print(f"{args.file} not found — run this from the repo root.",
              file=sys.stderr)
        return 1
    with open(args.file, encoding="utf-8") as fh:
        src = fh.read()

    if NEEDS not in src:
        print(f"{args.file} has not been sector-patched yet.\n"
              f"Run `python apply_sector_patch.py` first — several anchors here "
              f"are lines that patch inserts.", file=sys.stderr)
        return 1

    patched = MARKER in src
    if args.check:
        print(f"{args.file}: {'already patched' if patched else 'not patched'} "
              f"for fundamentals")
        if not patched:
            _, probs = apply(src)
            print("\n".join(probs) if probs
                  else f"All {len(EDITS)} anchors resolve cleanly.")
        return 0
    if patched:
        print(f"{args.file} already contains {MARKER} — nothing to do.")
        return 0

    out, probs = apply(src)
    if probs:
        print(f"Refusing to patch {args.file}. {len(probs)} anchor(s) did not "
              f"resolve:\n", file=sys.stderr)
        print("\n".join(probs), file=sys.stderr)
        print("\nNothing was written.", file=sys.stderr)
        return 2

    if args.dry_run:
        sys.stdout.writelines(difflib.unified_diff(
            src.splitlines(keepends=True), out.splitlines(keepends=True),
            fromfile=f"a/{args.file}", tofile=f"b/{args.file}"))
        print(f"\n-- dry run: {len(EDITS)} edits resolve, nothing written.")
        return 0

    if not args.no_backup:
        shutil.copy2(args.file, args.file + ".fund.bak")
        print(f"Backup: {args.file}.fund.bak")
    with open(args.file, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"Patched {args.file} — {len(EDITS)} edits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
