#!/usr/bin/env python3
"""
fundamentals.py — the npstocks comparative dump, keyed to NEPSE symbols.

The dump is keyed on company name. The floor sheet is keyed on symbol. Joining
them is the whole problem, and it is not a fuzzy-matching problem:

    Sana Kisan Bikas Bank      ~  Gandaki Bikas Bank Limited    (0.82)
    Swabalamban Bikas Bank     ~  Salapa Bikas Bank Limited     (0.82)
    National Microfinance ...  ~  NMB Microfinance ...           (0.87)

Every one of those is a different institution, and every one scores high enough
to be taken by any sensible cutoff. Nepali BFI names share too much vocabulary —
"Laghubitta Bittiya Sanstha", "Bikas Bank", "Microfinance" — for string distance
to mean anything. Put NMB's fundamentals on National Microfinance's row and every
number downstream is wrong with nothing to signal it.

So the join is exact-or-declared. A name matches on its normalised form, or it
appears in `reference/fundamentals_alias.csv`, or it is reported unmatched. There
is no third path and deliberately no similarity threshold anywhere in this file.

Two behaviours carried over from the original pipeline because they are correct
and non-obvious:

*   **npstocks writes 0.00 for "not reported".** A zero P/E or zero book value is
    meaningless, and left alone it drags every sector median toward zero. Those
    become NaN. Price-change columns are left alone — 0% there is a real
    unchanged session.
*   **The risk-return trade-off is rendered as a percentage but is a ratio.**
    "11950.00%" means 119.5. It is divided by 100 and the % dropped from the
    header.

    import fundamentals as fm
    d = fm.load("reference/fundamentals.csv")
    d.loc["NABIL", "epsD"]
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

import numpy as np
import pandas as pd

DEFAULT_PATH = os.path.join("reference", "fundamentals.csv")
ALIAS_PATH = os.path.join("reference", "fundamentals_alias.csv")

# Rendered as a percentage, actually a ratio. See the docstring.
RATIO_COLS = ["risk return trade off", "risk-return trade-off"]

# 0.00 means "not reported" on these. Price-change columns are excluded on
# purpose: an unchanged session really is 0%.
ZERO_IS_MISSING = ["pe ", "pe(", "pbv", "roe", "roa", "margin", "eps", "bvp",
                   "bookvalue", "ratio", "multiplier", "revenue", "net income",
                   "capital", "reserves", "market cap", "distributable",
                   "gross profit"]

# Source header -> short key. Only these reach the browser; the dump has 62
# columns and most of them are noise once you have the drivers.
FIELDS = {
    "Latest Close": "close",
    "180 Day Avg": "avg180",
    "Market Cap": "mcap",
    "Paidup Capital": "paidup",
    "Reserves & Surplus": "reserves",
    "Revenue": "revenue",
    "Net Income": "netIncome",
    "Distributable Profit": "distributable",
    "EPS (D)": "epsD",
    "EPS (TTM)": "epsTTM",
    "EPS (MRQ)": "epsMRQ",
    "Bookvalue": "bvps",
    "PE (D)": "peD",
    "PE (TTM)": "peTTM",
    "PBV": "pbv",
    "ROE (TTM) %": "roeTTM",
    "ROA (TTM)%": "roaTTM",
    "Net Profit Margin TTM %": "npmTTM",
    "Current Ratio": "currentRatio",
    "Debt to Asset": "debtToAsset",
    "Equity Multiplier": "equityMult",
    "Beta Weekly": "betaW",
    "RSI": "rsi",
    "Relative Strength %": "relStrength",
    "50 day MA vs 200 day MA %": "ma50v200",
    "Price Vs 52 Week High %": "vs52wHigh",
    "1 Wk Price Chg %": "chg1w",
    "4 Wk Price Chg %": "chg4w",
    "12 Wk Price Chg %": "chg12w",
    "YTD Price Chg %": "chgYtd",
    "1 Year Price Chg %": "chg1y",
}


# ────────────────────────────────────────────────────────────────────────────
def to_num(s: pd.Series) -> pd.Series:
    """'4,375.00' / '-12.81%' / '' -> float. Percentages come back as plain
    numbers, so 12.81% is 12.81 — the dashboard divides where it needs to."""
    return pd.to_numeric(
        s.astype(str)
         .str.replace("%", "", regex=False)
         .str.replace(",", "", regex=False)
         .str.replace("−", "-", regex=False)      # unicode minus
         .str.strip()
         .replace({"": np.nan, "-": np.nan, "nan": np.nan, "N/A": np.nan,
                   "None": np.nan}),
        errors="coerce")


def norm_name(s: str) -> str:
    """Upper, drop corporate suffixes and punctuation.

    Mid-name words like DEVELOPMENT are kept deliberately — stripping them
    collapses 'Agriculture Development Bank' onto half the register.
    """
    s = str(s).upper()
    s = re.sub(r"\b(LIMITED|LTD\.?|PVT\.?|PRIVATE|COMPANY|CO\.?)\b", " ", s)
    return re.sub(r"[^A-Z0-9]", "", s).strip()


def fix_ratio_columns(df: pd.DataFrame) -> pd.DataFrame:
    for c in list(df.columns):
        k = str(c).lower().replace("[", "").replace("]", "")
        if any(p in k for p in RATIO_COLS):
            df[c] = to_num(df[c]) / 100.0
            df.rename(columns={c: str(c).replace("%", "").strip()}, inplace=True)
    return df


def canonicalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Raw dump -> numeric frame with a clean `Company` column."""
    df = raw.copy()
    df.columns = [str(c).replace("﻿", "").strip() for c in df.columns]
    df = df.dropna(how="all")

    key = next((c for c in df.columns
                if c.lower() in ("company", "company name", "name")), df.columns[0])
    df = df.rename(columns={key: "Company"})
    df["Company"] = df["Company"].astype(str).str.strip()
    df = df[df["Company"].str.len() > 0]
    df = df[~df["Company"].str.lower().isin(("nan", "none"))]

    df = fix_ratio_columns(df)

    for c in df.columns:
        if c not in ("Company", "Latest Report"):
            df[c] = to_num(df[c])

    for c in df.columns:
        if any(k in c.lower() for k in ZERO_IS_MISSING):
            if pd.api.types.is_numeric_dtype(df[c]):
                df.loc[df[c] == 0, c] = np.nan

    return df.reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
def load_alias(path: str = ALIAS_PATH) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        a = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return {}
    if "Company" not in a.columns or "Symbol" not in a.columns:
        raise RuntimeError(f"{path} needs Company and Symbol columns")
    return {norm_name(r.Company): str(r.Symbol).strip().upper()
            for r in a.itertuples() if str(r.Symbol).strip()}


def resolve(df: pd.DataFrame, listed: pd.DataFrame,
            alias: dict | None = None) -> pd.DataFrame:
    """Attach Symbol. Exact normalised name, then the alias file, then nothing.

    `listed` is a sector_map frame. Only equities are considered as targets —
    a fundamentals row is never going to be a debenture, and allowing those as
    candidates only creates ways to be wrong.
    """
    alias = {} if alias is None else alias
    eq = listed[listed["Instrument"] == "Equity"]
    by_name: dict[str, str] = {}
    for r in eq.itertuples():
        by_name.setdefault(norm_name(r.Name), r.Symbol)

    out = df.copy()
    keys = out["Company"].map(norm_name)
    out["Symbol"] = [by_name.get(k) or alias.get(k) for k in keys]
    out["_how"] = ["exact" if by_name.get(k) else
                   ("alias" if alias.get(k) else "unmatched") for k in keys]
    return out


def derive(df: pd.DataFrame) -> pd.DataFrame:
    """Shares outstanding, from market cap and the vendor's own close.

    Not from Paidup Capital: dividing paidup by the Rs 100 par disagrees with
    the implied count for about a quarter of the board — 0.952 (a 5% bonus not
    yet reflected in paidup) and worse. Market cap over close is internally
    consistent by construction, which is what the dashboard needs when it
    re-prices on a floor sheet VWAP.
    """
    out = df.copy()
    close = out.get("Latest Close")
    mcap = out.get("Market Cap")
    if close is None or mcap is None:
        out["shares"] = np.nan
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        out["shares"] = np.where((close > 0) & (mcap > 0), mcap / close, np.nan)
    return out


# ────────────────────────────────────────────────────────────────────────────
def load(path: str = DEFAULT_PATH, listed_path: str | None = None,
         alias_path: str | None = ALIAS_PATH) -> pd.DataFrame:
    """The joined frame, indexed by Symbol. Unmatched rows are dropped here but
    their names are preserved on the frame as `.attrs['unmatched']`."""
    import sector_map as sm

    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{path} not found. Run `python fundamentals_scrape.py` "
            f"(or the nepse-fundamentals workflow) to create it.")

    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    df = derive(canonicalise(raw))
    listed = sm.load(listed_path or sm.DEFAULT_PATH)
    df = resolve(df, listed, load_alias(alias_path))

    unmatched = sorted(df.loc[df["_how"] == "unmatched", "Company"])
    got = df[df["Symbol"].notna()].drop_duplicates("Symbol", keep="first")
    got = got.set_index("Symbol", drop=False)
    got.attrs["unmatched"] = unmatched
    got.attrs["n_alias"] = int((got["_how"] == "alias").sum())
    return got


def payload(df: pd.DataFrame, asof: str | None = None) -> dict:
    """data/fundamentals.json — column-major, so it stays small enough to fetch
    eagerly next to sectors.json."""
    cols = [k for k in FIELDS.values()]
    src = {v: k for k, v in FIELDS.items()}

    def val(row, key):
        c = src[key]
        if c not in df.columns:
            return None
        v = row[c]
        if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
            return None
        return round(float(v), 4)

    sym = {}
    report = {}
    for _, row in df.iterrows():
        s = row["Symbol"]
        sym[s] = [val(row, k) for k in cols] + [
            None if pd.isna(row.get("shares")) else int(row["shares"])]
        r = row.get("Latest Report")
        if isinstance(r, str) and r.strip():
            report[s] = r.strip()

    return {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "asof": asof or "",
        "n": len(sym),
        "cols": cols + ["shares"],
        "sym": sym,
        "report": report,
        "unmatched": df.attrs.get("unmatched", []),
    }


def write_payload(df: pd.DataFrame, out_dir: str, asof: str | None = None) -> str:
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    p = os.path.join(out_dir, "data", "fundamentals.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload(df, asof), fh, separators=(",", ":"))
    return p


def asof_from_name(path: str) -> str:
    """'E 30072026.csv' -> '2026-07-30'."""
    m = re.search(r"(\d{2})(\d{2})(\d{4})", os.path.basename(path))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""


# ────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Inspect the fundamentals join")
    ap.add_argument("--file", default=DEFAULT_PATH)
    ap.add_argument("--listed", default=None)
    ap.add_argument("--alias", default=ALIAS_PATH)
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    d = load(args.file, args.listed, args.alias)
    un = d.attrs["unmatched"]
    print(f"{len(d)} symbols joined · {d.attrs['n_alias']} via the alias file "
          f"· {len(un)} unmatched")
    if un:
        print("\nUnmatched — add these to reference/fundamentals_alias.csv:")
        for c in un:
            print(f"  {c}")

    if args.symbol:
        s = args.symbol.strip().upper()
        if s not in d.index:
            print(f"\n{s}: no fundamentals")
            return 1
        r = d.loc[s]
        print(f"\n{s}  {r['Company']}  [{r['_how']}]")
        for c in ("Latest Report", "Latest Close", "Market Cap", "EPS (D)",
                  "Bookvalue", "PE (D)", "PBV", "ROE (TTM) %"):
            if c in d.columns:
                print(f"  {c:<16} {r[c]}")
        print(f"  {'shares':<16} {r['shares']:,.0f}" if pd.notna(r["shares"])
              else "  shares           —")

    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)) or ".",
                    exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload(d, asof_from_name(args.file)), fh,
                      separators=(",", ":"))
        print(f"\nPayload: {args.json} "
              f"({os.path.getsize(args.json) / 1024:,.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
