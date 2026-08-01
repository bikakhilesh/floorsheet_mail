#!/usr/bin/env python3
"""
apply_sector_patch.py — wire sector_view into dashboard_site.py.

    python apply_sector_patch.py --dry-run     # show the diff, change nothing
    python apply_sector_patch.py               # apply, keeping a .bak
    python apply_sector_patch.py --check       # is it already patched?

Nineteen edits, every one an insertion or a one-line swap. Each is keyed to an
exact anchor that must appear exactly once in the file; if any anchor is missing
or ambiguous the script reports which one and writes nothing at all, so a
half-patched dashboard_site.py is not a state you can end up in. Running it twice
is refused rather than repeated.

Anchors were cut against dashboard_site.py at d61f06c (55,061 bytes, 1,111
lines) and every one was verified to occur exactly once with exact whitespace.

If you would rather do it by hand, `--list` prints the same edits as before and
after pairs. SECTOR_PATCH.md has them too.
"""

from __future__ import annotations

import argparse
import difflib
import os
import shutil
import sys

TARGET = "dashboard_site.py"
MARKER = "__SECTOR_JS__"          # present once the patch has been applied

# ────────────────────────────────────────────────────────────────────────────
# (name, anchor, replacement)
EDITS: list[tuple[str, str, str]] = [

    ("import sector modules",
     "import floorsheet_viz as fv\nimport interactive_report as ir\n",
     "import floorsheet_viz as fv\nimport interactive_report as ir\n"
     "import sector_map as sm\nimport sector_view as sv\n"),

    ("css placeholder",
     "</style></head><body>",
     "__SECTOR_CSS__\n</style></head><body>"),

    ("nav tab",
     '    <button data-t="blocks">Block trades</button>',
     '    <button data-t="sectors">Sectors</button>\n'
     '    <button data-t="blocks">Block trades</button>'),

    ("panel placeholder",
     '  <div class="panel" id="p-blocks"><div class="card">',
     '__SECTOR_PANEL__\n\n'
     '  <div class="panel" id="p-blocks"><div class="card">'),

    ("scrips sector filter control",
     '<span class="hint" id="cScrip"></span></div>',
     '<select class="f" id="fScripSector">'
     '<option value="">All sectors</option></select>\n'
     '      <span class="hint" id="cScrip"></span></div>'),

    ("scrips sector column",
     "    {k:'sym',h:'Scrip',f:r=>r.sym},\n"
     "    {k:'turnover',h:'Turnover',num:1,sort:1,f:r=>npr(r.turnover,'')},",
     "    {k:'sym',h:'Scrip',f:r=>r.sym},\n"
     "    {k:'sector',h:'Sector',f:r=>r.sector||'—'},\n"
     "    {k:'turnover',h:'Turnover',num:1,sort:1,f:r=>npr(r.turnover,'')},"),

    ("scrips filter predicate",
     "    ()=>{const q=$('#qScrip').value.trim().toUpperCase();\n"
     "      return SCRIPS.filter(s=>!q||s.sym.includes(q));},s=>openScrip(s));",
     "    ()=>{const q=$('#qScrip').value.trim().toUpperCase(),"
     "g=$('#fScripSector').value;\n"
     "      return SCRIPS.filter(s=>(!q||s.sym.includes(q))&&"
     "(!g||s.sector===g));},\n"
     "    s=>openScrip(s));"),

    ("tag scrips with their sector",
     "  BROKERS=rowsOf(DAY,'brokers'); SCRIPS=rowsOf(DAY,'scrips');",
     "  BROKERS=rowsOf(DAY,'brokers'); SCRIPS=rowsOf(DAY,'scrips');\n"
     "  SCRIPS.forEach(s=>{s.sector=secGroupAll(s.sym);}); secFillScripFilter();"),

    ("re-render sectors on day change",
     "  if($('#p-trends').classList.contains('on'))renderTrends();",
     "  if($('#p-trends').classList.contains('on'))renderTrends();\n"
     "  if($('#p-sectors').classList.contains('on'))renderSectors();"),

    ("sector filter listener",
     "['qBroker','fBrokerSide','qScrip','qBlock','fBlockType'].forEach(id=>",
     "['qBroker','fBrokerSide','qScrip','fScripSector','qBlock','fBlockType']"
     ".forEach(id=>"),

    ("sectors tab click",
     "  if(b.dataset.t==='trends')renderTrends();});",
     "  if(b.dataset.t==='trends')renderTrends();\n"
     "  if(b.dataset.t==='sectors')renderSectors();});"),

    ("script placeholder",
     "/* ---------- wiring ---------- */",
     "__SECTOR_JS__\n\n/* ---------- wiring ---------- */"),

    ("load the map at boot",
     "    IDX=await (await fetch('data/index.json')).json();\n  }",
     "    IDX=await (await fetch('data/index.json')).json();\n  }\n"
     "  await secLoad();"),

    ("helpers before build_app",
     "def build_app(out: str) -> str:",
     '''def write_sectors(out: str, listed: str = sm.DEFAULT_PATH) -> str | None:
    """data/sectors.json — the map the browser joins the floor sheet against.

    Rewritten on every build and deliberately not cached. It is 37 KB, and the
    whole point of joining in the browser is that a new listing re-maps every
    session in the archive without a single parquet being reopened. A missing
    listing csv is a warning rather than a failure: the site still builds, the
    Sectors tab just says where the map went.
    """
    try:
        m = sm.load(listed)
    except FileNotFoundError as e:
        print(f"WARNING: {e}\\n         The Sectors tab will be empty.")
        return None
    p = sm.write_payload(m, out)
    print(f"Sector map: {p} ({os.path.getsize(p) / 1024:,.0f} KB, "
          f"{len(m)} securities)")
    return p


def _read_sectors(site_dir: str):
    p = os.path.join(site_dir, "data", "sectors.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _render(embed: str) -> str:
    """APP with the sector blocks filled in and the payload slot set."""
    return (APP.replace("__SECTOR_CSS__", sv.SECTOR_CSS)
               .replace("__SECTOR_PANEL__", sv.SECTOR_PANEL)
               .replace("__SECTOR_JS__", sv.SECTOR_JS)
               .replace("__EMBED__", embed))


def build_app(out: str) -> str:'''),

    ("build_app uses _render",
     '        f.write(APP.replace("__EMBED__", "null"))',
     '        f.write(_render("null"))'),

    ("offline copy carries the map",
     '        "days": {},',
     '        "days": {},\n        "sectors": _read_sectors(site_dir),'),

    ("build_offline uses _render",
     """    html = APP.replace("__EMBED__", '"' + blob + '"')""",
     """    html = _render('"' + blob + '"')"""),

    ("--listed argument",
     '    ap.add_argument("--offline-days", type=int, default=22,\n'
     '                    help="sessions to embed in the offline copy (0 = all)")',
     '    ap.add_argument("--offline-days", type=int, default=22,\n'
     '                    help="sessions to embed in the offline copy (0 = all)")\n'
     '    ap.add_argument("--listed", default=sm.DEFAULT_PATH,\n'
     '                    help="listed-securities csv behind the sector map")'),

    ("write the map during the build",
     "    info = build_site_data(args.archive, args.out, args.rebuild)\n"
     "    build_app(args.out)",
     "    info = build_site_data(args.archive, args.out, args.rebuild)\n"
     "    write_sectors(args.out, args.listed)\n"
     "    build_app(args.out)"),
]


# ────────────────────────────────────────────────────────────────────────────
def apply(text: str) -> tuple[str, list[str]]:
    """Every anchor is validated before anything is written."""
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
    ap = argparse.ArgumentParser(description="Wire sector_view into dashboard_site")
    ap.add_argument("--file", default=TARGET)
    ap.add_argument("--dry-run", action="store_true", help="print the diff only")
    ap.add_argument("--check", action="store_true", help="report state and exit")
    ap.add_argument("--list", action="store_true", help="print the edits")
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

    patched = MARKER in src
    if args.check:
        print(f"{args.file}: {'already patched' if patched else 'not patched'}")
        if not patched:
            _, probs = apply(src)
            if probs:
                print("Anchors that would fail:")
                print("\n".join(probs))
            else:
                print(f"All {len(EDITS)} anchors resolve cleanly.")
        return 0

    if patched:
        print(f"{args.file} already contains {MARKER} — nothing to do.\n"
              f"Revert from git or the .bak before re-running.")
        return 0

    out, probs = apply(src)
    if probs:
        print(f"Refusing to patch {args.file}. "
              f"{len(probs)} anchor(s) did not resolve:\n", file=sys.stderr)
        print("\n".join(probs), file=sys.stderr)
        print("\nThe file has not been touched. This usually means "
              "dashboard_site.py moved on since the patch was written — send me "
              "the current file and I will re-cut the anchors.", file=sys.stderr)
        return 2

    if args.dry_run:
        sys.stdout.writelines(difflib.unified_diff(
            src.splitlines(keepends=True), out.splitlines(keepends=True),
            fromfile=f"a/{args.file}", tofile=f"b/{args.file}"))
        print(f"\n-- dry run: {len(EDITS)} edits resolve, nothing written.")
        return 0

    if not args.no_backup:
        shutil.copy2(args.file, args.file + ".bak")
        print(f"Backup: {args.file}.bak")
    with open(args.file, "w", encoding="utf-8") as fh:
        fh.write(out)
    print(f"Patched {args.file} — {len(EDITS)} edits.\n"
          f"Next: python dashboard_site.py --archive archive/parquet --out site")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
