#!/usr/bin/env python3
"""
sector_map.py — the symbol to sector map, and the one place that decides what a
sector *means* for analysis.

The floor sheet gives us a symbol and nothing else. Every sector-level number in
the dashboard is a join against this map, so the join has to be honest about a
few things NEPSE's own listing table is not.

Two decisions worth knowing about before you read a sector number:

1.  **Non-equity instruments carry their issuer's sector.** NEPSE files
    `SEF` (Siddhartha Equity Fund) and `NICAD85/86` (a NIC Asia debenture) under
    "Commercial Banks", because that is who issued them. Summing turnover by the
    raw `Sector` column therefore books mutual-fund and debenture flow into
    banking, which is not what anyone means by "money went into banks today".
    So alongside the raw sector this module derives a `group`: equities keep
    their sector, everything else moves to `Debenture`, `Mutual Fund` or
    `Preference Share`. The dashboard aggregates on `group` and defaults to
    equity only.

2.  **One symbol can appear more than once in the scrape.** The scraper walks
    instrument x status combinations, so a name captured under both the "Equity"
    and the "All Instrument" pass shows up twice. Dedup keeps the row with the
    most authoritative status (Active > Suspended > Delisted) and then the most
    specific instrument, rather than whichever pass happened to run first.

Delisted names are kept. They cost nothing, and they are what lets you open a
2026 floor sheet in 2028 and still resolve a symbol that has since gone.

    import sector_map as sm
    m = sm.load()                       # DataFrame indexed by Symbol
    m.loc["NABIL", "group"]             # 'Commercial Banks'
    sm.payload(m)                       # compact dict for data/sectors.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pandas as pd

DEFAULT_PATH = os.path.join("reference", "listed_securities.csv")
OVERRIDE_PATH = os.path.join("reference", "sector_overrides.csv")

# The canonical file. No `SN` (it is the site's row counter and renumbers on
# every scrape, which would produce a diff on a day nothing changed) and no
# `_instrument_filter` / `_status_filter` (they record which scrape pass caught
# the row, not anything about the security).
CANON_COLS = ["Symbol", "Name", "Sector", "Instrument", "Status",
              "Email", "Website"]

# Columns that describe the security itself. A change in any of these is worth
# rebuilding the dashboard for; a changed email address is not.
MATERIAL_COLS = ["Symbol", "Name", "Sector", "Instrument", "Status"]

# Equities keep their sector. Everything else is its own bucket — see note 1.
GROUP_BY_INSTRUMENT = {
    "Mutual Funds": "Mutual Fund",
    "Non-Convertible Debentures": "Debenture",
    "Preference Shares": "Preference Share",
}

# NEPSE's spellings, tidied for display only. The csv keeps the source strings
# so a diff against the site stays meaningful.
SECTOR_DISPLAY = {
    "Hydro Power": "Hydropower",
    "Manufacturing And Processing": "Manufacturing & Processing",
    "Hotels And Tourism": "Hotels & Tourism",
    "Non Life Insurance": "Non-Life Insurance",
    "Tradings": "Trading",
    "Others": "Others",
}

STATUS_RANK = {"Active": 0, "Suspended": 1, "Delisted": 2}
INSTRUMENT_RANK = {"Equity": 0, "Mutual Funds": 1,
                   "Non-Convertible Debentures": 2, "Preference Shares": 3}

UNMAPPED = "Unmapped"


# ────────────────────────────────────────────────────────────────────────────
# Canonical form
# ────────────────────────────────────────────────────────────────────────────
def _clean(s: pd.Series) -> pd.Series:
    return (s.astype("string").fillna("").str.replace(r"\s+", " ", regex=True)
            .str.strip())


def canonicalise(df: pd.DataFrame) -> pd.DataFrame:
    """Raw scrape (or an old csv) to the committed shape.

    Sorted by symbol and stripped of the volatile columns, so `git diff` on the
    reference file shows securities that changed and nothing else.
    """
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    # Tolerate the header variants the site has used ("Company Name", "Stock
    # Symbol") as well as an already-canonical file.
    alias = {"company name": "Name", "company": "Name", "name": "Name",
             "stock symbol": "Symbol", "symbol": "Symbol",
             "sector": "Sector", "instrument": "Instrument",
             "status": "Status", "email": "Email", "website": "Website"}
    df = df.rename(columns={c: alias[c.lower()] for c in df.columns
                            if c.lower() in alias})

    for c in CANON_COLS:
        if c not in df.columns:
            df[c] = ""
    df = df[CANON_COLS]

    for c in CANON_COLS:
        df[c] = _clean(df[c])
    df["Symbol"] = df["Symbol"].str.upper()
    df = df[df["Symbol"] != ""]

    # Dedup by authority, not by scrape order — see note 2.
    df = df.assign(
        _s=df["Status"].map(STATUS_RANK).fillna(9),
        _i=df["Instrument"].map(INSTRUMENT_RANK).fillna(9),
    ).sort_values(["Symbol", "_s", "_i"]).drop_duplicates("Symbol", keep="first")

    return (df.drop(columns=["_s", "_i"])
              .sort_values("Symbol")
              .reset_index(drop=True))


def group_of(sector: str, instrument: str) -> str:
    return GROUP_BY_INSTRUMENT.get(instrument, SECTOR_DISPLAY.get(sector, sector))


def display_sector(sector: str) -> str:
    return SECTOR_DISPLAY.get(sector, sector)


# ────────────────────────────────────────────────────────────────────────────
# Loading
# ────────────────────────────────────────────────────────────────────────────
def load(path: str = DEFAULT_PATH,
         overrides: str | None = OVERRIDE_PATH) -> pd.DataFrame:
    """The map, indexed by Symbol, with `sector` (display) and `group` added.

    `overrides` is an optional hand-maintained csv with the same columns, applied
    last. It exists for the gap between a security starting to trade and NEPSE
    updating its listing table, and for anything the site gets wrong. Missing
    file is not an error.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run `python get_listed_securities.py` "
            f"(or the nepse-listed workflow) to create it.")

    df = canonicalise(pd.read_csv(path, dtype=str, keep_default_na=False))

    if overrides and os.path.exists(overrides):
        ov = canonicalise(pd.read_csv(overrides, dtype=str, keep_default_na=False))
        if len(ov):
            df = (pd.concat([df[~df["Symbol"].isin(ov["Symbol"])], ov])
                    .sort_values("Symbol").reset_index(drop=True))
            print(f"sector_map: {len(ov)} override(s) applied from {overrides}")

    df["sector"] = [display_sector(s) for s in df["Sector"]]
    df["group"] = [group_of(s, i) for s, i in zip(df["Sector"], df["Instrument"])]
    return df.set_index("Symbol", drop=False)


def diff(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    """What changed between two canonical frames.

    Split into material (the security itself) and cosmetic (contact details) so
    the workflow can commit a new email address without rebuilding the site.
    """
    o = old.set_index("Symbol") if "Symbol" in old.columns else old
    n = new.set_index("Symbol") if "Symbol" in new.columns else new

    added = sorted(set(n.index) - set(o.index))
    removed = sorted(set(o.index) - set(n.index))
    both = sorted(set(o.index) & set(n.index))

    changed: list[dict] = []
    for sym in both:
        for col in MATERIAL_COLS[1:]:
            a, b = str(o.at[sym, col]), str(n.at[sym, col])
            if a != b:
                changed.append({"symbol": sym, "field": col, "from": a, "to": b})

    cosmetic = 0
    for sym in both:
        for col in ("Email", "Website"):
            if str(o.at[sym, col]) != str(n.at[sym, col]):
                cosmetic += 1

    material = bool(added or removed or changed)
    return {"added": added, "removed": removed, "changed": changed,
            "cosmetic": cosmetic, "material": material,
            "any": material or cosmetic > 0}


def format_diff(d: dict, new: pd.DataFrame) -> str:
    """One markdown block for the changelog and the Actions summary."""
    n = new.set_index("Symbol") if "Symbol" in new.columns else new
    out: list[str] = []
    if d["added"]:
        out.append(f"**New listings ({len(d['added'])})**\n")
        for s in d["added"]:
            out.append(f"- `{s}` — {n.at[s, 'Name']} · {n.at[s, 'Sector']} · "
                       f"{n.at[s, 'Instrument']} · {n.at[s, 'Status']}")
        out.append("")
    if d["removed"]:
        out.append(f"**Gone from the listing table ({len(d['removed'])})**\n")
        out += [f"- `{s}`" for s in d["removed"]]
        out.append("")
    if d["changed"]:
        out.append(f"**Reclassified ({len(d['changed'])})**\n")
        for c in d["changed"]:
            out.append(f"- `{c['symbol']}` {c['field']}: {c['from']} → {c['to']}")
        out.append("")
    if d["cosmetic"]:
        out.append(f"_{d['cosmetic']} contact detail(s) updated._\n")
    return "\n".join(out) or "_No change._\n"


# ────────────────────────────────────────────────────────────────────────────
# Browser payload
# ────────────────────────────────────────────────────────────────────────────
def payload(df: pd.DataFrame) -> dict:
    """data/sectors.json — index-encoded to keep it small enough to fetch eagerly.

    The dashboard joins this to the floor sheet in the browser rather than baking
    sectors into the cached per-day json. That is the whole point: when a new
    listing lands, one 40 KB file changes and every session in the archive re-maps
    on the next page load. Nothing has to be recomputed.
    """
    sectors = sorted(df["sector"].unique())
    groups = sorted(df["group"].unique())
    instruments = sorted(df["Instrument"].unique())
    statuses = sorted(df["Status"].unique(), key=lambda s: STATUS_RANK.get(s, 9))

    si = {v: i for i, v in enumerate(sectors)}
    gi = {v: i for i, v in enumerate(groups)}
    ii = {v: i for i, v in enumerate(instruments)}
    ti = {v: i for i, v in enumerate(statuses)}

    sym = {r.Symbol: [si[r.sector], gi[r.group], ii[r.Instrument], ti[r.Status]]
           for r in df.itertuples()}
    name = {r.Symbol: r.Name for r in df.itertuples()}

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n": len(df),
        "sectors": sectors, "groups": groups,
        "instruments": instruments, "statuses": statuses,
        "sym": sym, "name": name,
    }


def write_payload(df: pd.DataFrame, out_dir: str) -> str:
    """Write data/sectors.json under a built site directory."""
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    p = os.path.join(out_dir, "data", "sectors.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload(df), fh, separators=(",", ":"), sort_keys=False)
    return p


# ────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Inspect the symbol to sector map")
    ap.add_argument("--listed", default=DEFAULT_PATH)
    ap.add_argument("--overrides", default=OVERRIDE_PATH)
    ap.add_argument("--symbol", default=None, help="look one symbol up")
    ap.add_argument("--json", default=None, help="write the browser payload here")
    args = ap.parse_args(argv)

    m = load(args.listed, args.overrides)

    if args.symbol:
        s = args.symbol.strip().upper()
        if s not in m.index:
            print(f"{s}: not in the map")
            return 1
        r = m.loc[s]
        print(f"{s}  {r['Name']}\n  sector    {r['sector']}  (raw: {r['Sector']})"
              f"\n  group     {r['group']}\n  instrument {r['Instrument']}"
              f"\n  status    {r['Status']}")
        return 0

    act = m[m["Status"] == "Active"]
    eq = act[act["Instrument"] == "Equity"]
    print(f"{len(m)} securities · {len(act)} active · {len(eq)} active equity")
    print("\nActive equity by sector")
    for s, n in eq["group"].value_counts().items():
        print(f"  {s:<30} {n:>4}")
    print("\nActive non-equity")
    for s, n in act[act["Instrument"] != "Equity"]["group"].value_counts().items():
        print(f"  {s:<30} {n:>4}")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)) or ".",
                    exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload(m), fh, separators=(",", ":"))
        print(f"\nPayload: {args.json} "
              f"({os.path.getsize(args.json) / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
