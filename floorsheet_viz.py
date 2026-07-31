#!/usr/bin/env python3
"""
floorsheet_viz.py — NEPSE floor sheet analytics & visualisation engine.

Input : daily floor sheet CSV with columns
        Contract No. | Stock Symbol | Buyer | Seller | Quantity | Rate (Rs) | Amount (Rs)
Output: PNG charts + CSV summary tables + a single self-contained HTML report
        (images base64-embedded) suitable for use as the body of the daily
        floor-sheet mail, or as a GitHub Actions artifact.

Usage:
    python floorsheet_viz.py --csv 2026_07_30.csv --outdir out
    python floorsheet_viz.py --csv data/2026_07_30.csv --outdir out \
        --top 15 --brokers brokers.csv --dpi 130

Dependencies: pandas, numpy, matplotlib   (no seaborn / plotly — keeps CI light)
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import matplotlib

matplotlib.use("Agg")  # headless — mandatory for GitHub Actions runners
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, LogNorm

# ────────────────────────────────────────────────────────────────────────────
# House style (Siddhartha Capital: navy / gold)
# ────────────────────────────────────────────────────────────────────────────
NAVY = "#0B2545"
GOLD = "#C9A227"
BUY = "#1B7F4C"   # green  — buy side / accumulation
SELL = "#B02A2A"  # red    — sell side / distribution
GREY = "#8A94A6"
LIGHT = "#EEF1F6"
INK = "#1C2331"

SEQ_CMAP = LinearSegmentedColormap.from_list("navygold", ["#FFFFFF", "#9FB3C8", NAVY])
DIV_CMAP = LinearSegmentedColormap.from_list("sellbuy", [SELL, "#F5F5F5", BUY])

BASE_RC = {
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.edgecolor": "#C7CEDB",
    "axes.linewidth": 0.8,
    "axes.labelcolor": INK,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.titlecolor": NAVY,
    "axes.grid": True,
    "grid.color": "#E3E8F0",
    "grid.linewidth": 0.7,
    "xtick.color": INK,
    "ytick.color": INK,
    "font.size": 9.5,
    "font.family": "DejaVu Sans",
    "legend.frameon": False,
    "figure.autolayout": False,
    "savefig.bbox": "tight",
    "savefig.facecolor": "white",
}

CR = 1e7   # 1 crore
LAKH = 1e5


# ────────────────────────────────────────────────────────────────────────────
# Formatting helpers (NPR lakh / crore convention)
# ────────────────────────────────────────────────────────────────────────────
def npr(x: float, prefix: str = "Rs ") -> str:
    """Format an NPR amount in lakh / crore / arba notation."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    sign = "-" if x < 0 else ""
    a = abs(float(x))
    if a >= 100 * CR:
        return f"{sign}{prefix}{a / (100 * CR):,.2f} Ar"
    if a >= CR:
        return f"{sign}{prefix}{a / CR:,.2f} Cr"
    if a >= LAKH:
        return f"{sign}{prefix}{a / LAKH:,.2f} L"
    return f"{sign}{prefix}{a:,.0f}"


def qty(x: float) -> str:
    return f"{x:,.0f}"


def cr_axis(ax, axis: str = "x"):
    """Label an axis in crore."""
    f = mticker.FuncFormatter(lambda v, _: f"{v / CR:,.1f}")
    (ax.xaxis if axis == "x" else ax.yaxis).set_major_formatter(f)


# ────────────────────────────────────────────────────────────────────────────
# Load & normalise
# ────────────────────────────────────────────────────────────────────────────
COLMAP = {
    "contract no.": "contract", "contract no": "contract", "contractno": "contract",
    "sn": "sn", "s.n.": "sn",
    "stock symbol": "symbol", "symbol": "symbol", "scrip": "symbol",
    "buyer": "buyer", "buyer broker": "buyer", "buyer broker no": "buyer",
    "seller": "seller", "seller broker": "seller", "seller broker no": "seller",
    "quantity": "qty", "qty": "qty",
    "rate (rs)": "rate", "rate": "rate", "rate(rs)": "rate",
    "amount (rs)": "amount", "amount": "amount", "amount(rs)": "amount",
}


def read_any(path: str) -> pd.DataFrame:
    """Read a floor sheet from parquet, csv, or compressed csv.

    Parquet is preferred for archives: the 30 Jul sheet is 2.0 MB as csv and
    ~0.35 MB as parquet, and it round-trips dtypes so Contract No. does not
    come back as a float.
    """
    ext = os.path.splitext(path.lower())[1]
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(path)
    if ext == ".feather":
        return pd.read_feather(path)
    # read_csv handles .gz/.bz2/.xz/.zip transparently
    return pd.read_csv(path)


def save_parquet(df: pd.DataFrame, path: str) -> str:
    """Persist a floor sheet as parquet, in the original column names."""
    out = df.rename(columns=OUTCOLS)[[c for c in OUTCOLS.values() if c in
                                      df.rename(columns=OUTCOLS).columns]]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    out.to_parquet(path, index=False, compression="zstd")
    return path


OUTCOLS = {"contract": "Contract No.", "symbol": "Stock Symbol",
           "buyer": "Buyer", "seller": "Seller", "qty": "Quantity",
           "rate": "Rate (Rs)", "amount": "Amount (Rs)"}


def load_floorsheet(path: str, broker_map_path: str | None = None) -> pd.DataFrame:
    df = read_any(path)
    df.columns = [COLMAP.get(str(c).strip().lower(), str(c).strip().lower()) for c in df.columns]

    required = {"symbol", "buyer", "seller", "qty", "rate", "amount"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Floor sheet is missing column(s): {sorted(missing)}")

    for c in ("qty", "rate", "amount"):
        df[c] = pd.to_numeric(
            df[c].astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
    for c in ("buyer", "seller"):
        df[c] = pd.to_numeric(
            df[c].astype(str).str.extract(r"(\d+)", expand=False), errors="coerce"
        ).astype("Int64")

    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["symbol", "buyer", "seller", "qty", "rate", "amount"])
    df = df[(df["qty"] > 0) & (df["amount"] > 0)]

    # Recompute amount where the source rounds it (guards against feed glitches)
    calc = df["qty"] * df["rate"]
    bad = (df["amount"] - calc).abs() > np.maximum(1.0, 0.005 * calc)
    if bad.any():
        df.loc[bad, "amount"] = calc[bad]

    # Contract no. = YYYYMMDD + SS (matching-engine stream) + 6-digit sequence.
    # Sequence is monotonic *within* a stream, so it is a usable ordering proxy —
    # it is NOT a timestamp. Treated as such throughout.
    if "contract" in df.columns:
        cn = df["contract"].astype(str).str.strip()
        ok = cn.str.fullmatch(r"\d{16}")
        df["stream"] = np.where(ok, cn.str[8:10], "NA")
        df["seq"] = pd.to_numeric(cn.where(ok).str[10:], errors="coerce")
        df["seq_pct"] = df.groupby("stream")["seq"].transform(
            lambda s: s.rank(pct=True) if s.notna().any() else np.nan
        )
    else:
        df["stream"], df["seq"], df["seq_pct"] = "NA", np.nan, np.nan

    df["cross"] = df["buyer"] == df["seller"]          # both legs same broker
    df["buyer_l"] = df["buyer"].astype(int)
    df["seller_l"] = df["seller"].astype(int)

    if broker_map_path and os.path.exists(broker_map_path):
        bm = pd.read_csv(broker_map_path)
        bm.columns = [c.strip().lower() for c in bm.columns]
        code_c = next(c for c in bm.columns if c in ("code", "broker", "broker_no", "id"))
        name_c = next(c for c in bm.columns if c in ("name", "broker_name", "firm"))
        BROKER_NAMES.update(
            {int(r[code_c]): str(r[name_c]).strip() for _, r in bm.iterrows()}
        )
    return df.reset_index(drop=True)


BROKER_NAMES: dict[int, str] = {}


def bl(code: int, short: bool = True) -> str:
    """Broker label: '58' -> '58 · Naasa' if a name map was supplied."""
    name = BROKER_NAMES.get(int(code))
    if not name:
        return f"B-{int(code)}"
    return f"{int(code)} · {name[:14]}" if short else f"{int(code)} · {name}"


# ────────────────────────────────────────────────────────────────────────────
# Analytics
# ────────────────────────────────────────────────────────────────────────────
@dataclass
class Analytics:
    df: pd.DataFrame
    date: str
    kpi: dict = field(default_factory=dict)
    broker: pd.DataFrame = None
    scrip: pd.DataFrame = None
    blocks: pd.DataFrame = None
    pairs: pd.DataFrame = None
    net_pos: pd.DataFrame = None


def build_analytics(df: pd.DataFrame, date_str: str) -> Analytics:
    a = Analytics(df=df, date=date_str)
    total = df["amount"].sum()

    # ---- broker level -----------------------------------------------------
    b = df.groupby("buyer_l").agg(
        buy_amt=("amount", "sum"), buy_qty=("qty", "sum"), buy_trades=("amount", "size")
    )
    s = df.groupby("seller_l").agg(
        sell_amt=("amount", "sum"), sell_qty=("qty", "sum"), sell_trades=("amount", "size")
    )
    br = b.join(s, how="outer").fillna(0.0)
    br.index.name = "broker"
    br["gross"] = br["buy_amt"] + br["sell_amt"]
    br["net"] = br["buy_amt"] - br["sell_amt"]
    br["trades"] = br["buy_trades"] + br["sell_trades"]
    # Each trade is counted on both legs, so the participation base is 2 × turnover
    br["share_pct"] = 100 * br["gross"] / (2 * total)
    br["net_pct_of_gross"] = np.where(br["gross"] > 0, 100 * br["net"] / br["gross"], 0)
    cx = df[df["cross"]].groupby("buyer_l")["amount"].sum()
    br["cross_amt"] = cx.reindex(br.index).fillna(0.0)
    br["cross_pct"] = np.where(br["gross"] > 0, 100 * 2 * br["cross_amt"] / br["gross"], 0)
    br["avg_ticket"] = br["gross"] / br["trades"].replace(0, np.nan)
    a.broker = br.sort_values("gross", ascending=False)

    # ---- scrip level ------------------------------------------------------
    g = df.groupby("symbol")
    sc = g.agg(
        turnover=("amount", "sum"), volume=("qty", "sum"), trades=("amount", "size"),
        high=("rate", "max"), low=("rate", "min"),
        first=("rate", "first"), last=("rate", "last"),
        n_buyers=("buyer_l", "nunique"), n_sellers=("seller_l", "nunique"),
    )
    sc["vwap"] = sc["turnover"] / sc["volume"]
    sc["range_pct"] = 100 * (sc["high"] - sc["low"]) / sc["vwap"]
    sc["avg_ticket"] = sc["turnover"] / sc["trades"]
    sc["mkt_share_pct"] = 100 * sc["turnover"] / total
    # Herfindahl of buy-side concentration per scrip (0–10,000)
    bs = df.groupby(["symbol", "buyer_l"])["amount"].sum()
    sh = bs / bs.groupby(level=0).transform("sum")
    sc["buy_hhi"] = (1e4 * (sh ** 2)).groupby(level=0).sum()
    a.scrip = sc.sort_values("turnover", ascending=False)

    # ---- block / single largest transactions ------------------------------
    a.blocks = df.nlargest(300, "amount")[
        ["symbol", "buyer_l", "seller_l", "qty", "rate", "amount", "cross"]
    ].reset_index(drop=True)

    # ---- broker↔broker pair flow -----------------------------------------
    a.pairs = (
        df.groupby(["buyer_l", "seller_l"])
        .agg(amount=("amount", "sum"), trades=("amount", "size"))
        .reset_index()
        .sort_values("amount", ascending=False)
    )

    # ---- broker × scrip net position -------------------------------------
    nb = df.groupby(["buyer_l", "symbol"])["amount"].sum().rename("buy")
    ns = df.groupby(["seller_l", "symbol"])["amount"].sum().rename("sell")
    nb.index.names = ns.index.names = ["broker", "symbol"]
    npos = pd.concat([nb, ns], axis=1).fillna(0.0)
    npos["net"] = npos["buy"] - npos["sell"]
    npos["gross"] = npos["buy"] + npos["sell"]
    a.net_pos = npos.reset_index().sort_values("net", ascending=False)

    # ---- KPI --------------------------------------------------------------
    shares = a.broker["gross"] / a.broker["gross"].sum()
    a.kpi = {
        "turnover": total,
        "volume": df["qty"].sum(),
        "trades": len(df),
        "scrips": df["symbol"].nunique(),
        "brokers": int(pd.unique(pd.concat([df["buyer_l"], df["seller_l"]])).size),
        "avg_ticket": total / len(df),
        "median_ticket": df["amount"].median(),
        "max_ticket": df["amount"].max(),
        "cross_amt": df.loc[df["cross"], "amount"].sum(),
        "cross_pct": 100 * df.loc[df["cross"], "amount"].sum() / total,
        "top10_broker_pct": 100 * shares.nlargest(10).sum(),
        "top10_scrip_pct": 100 * a.scrip["turnover"].nlargest(10).sum() / total,
        "broker_hhi": 1e4 * (shares ** 2).sum(),
        "scrip_hhi": 1e4 * ((a.scrip["turnover"] / total) ** 2).sum(),
    }
    return a


def lorenz(values: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    """Lorenz curve + Gini for a positive value vector."""
    v = np.sort(np.asarray(values, dtype=float))
    v = v[v > 0]
    n = v.size
    cum = np.cumsum(v) / v.sum()
    x = np.arange(1, n + 1) / n
    trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy <2.0 fallback
    gini = 1 - 2 * trapz(cum, x)
    return np.r_[0, x], np.r_[0, cum], gini


# ────────────────────────────────────────────────────────────────────────────
# Chart helpers
# ────────────────────────────────────────────────────────────────────────────
def _finish(fig, ax_or_axes, path, subtitle=None):
    if subtitle:
        fig.text(0.005, -0.02, subtitle, fontsize=7.5, color=GREY, ha="left")
    fig.savefig(path, dpi=plt.rcParams["figure.dpi"])
    plt.close(fig)
    return path


def _barlabels(ax, bars, labels, pad=0.008, color=INK, size=8):
    xmax = max([b.get_width() for b in bars] + [1])
    for b, t in zip(bars, labels):
        ax.text(b.get_width() + pad * xmax, b.get_y() + b.get_height() / 2, t,
                va="center", ha="left", fontsize=size, color=color)


# ── 01 KPI banner ───────────────────────────────────────────────────────────
def chart_kpi(a: Analytics, out: str) -> str:
    k = a.kpi
    tiles = [
        ("Turnover", npr(k["turnover"]), NAVY),
        ("Volume (shares)", qty(k["volume"]), NAVY),
        ("Trades", qty(k["trades"]), NAVY),
        ("Scrips traded", qty(k["scrips"]), NAVY),
        ("Active brokers", qty(k["brokers"]), NAVY),
        ("Avg ticket", npr(k["avg_ticket"]), GOLD),
        ("Median ticket", npr(k["median_ticket"]), GOLD),
        ("Largest single trade", npr(k["max_ticket"]), GOLD),
        ("Top-10 broker share", f"{k['top10_broker_pct']:.1f}%", GOLD),
        ("Top-10 scrip share", f"{k['top10_scrip_pct']:.1f}%", GOLD),
        ("Cross trades", f"{k['cross_pct']:.1f}% of t/o", SELL),
        ("Broker HHI", f"{k['broker_hhi']:,.0f}", SELL),
    ]
    ncol = 6
    nrow = int(np.ceil(len(tiles) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(15, 1.55 * nrow))
    axes = np.atleast_2d(axes).ravel()
    for ax, (label, value, col) in zip(axes, tiles):
        ax.set_axis_off()
        ax.add_patch(plt.Rectangle((0.02, 0.10), 0.96, 0.80, facecolor=LIGHT,
                                   edgecolor="#D3DAE6", transform=ax.transAxes))
        ax.text(0.5, 0.62, value, ha="center", va="center", fontsize=13.5,
                fontweight="bold", color=col, transform=ax.transAxes)
        ax.text(0.5, 0.28, label.upper(), ha="center", va="center", fontsize=7.8,
                color=GREY, transform=ax.transAxes)
    for ax in axes[len(tiles):]:
        ax.set_axis_off()
    fig.suptitle(f"NEPSE Floor Sheet — Market Snapshot | {a.date}",
                 fontsize=15, fontweight="bold", color=NAVY, y=1.02)
    fig.tight_layout()
    return _finish(fig, axes, out)


# ── 02 top scrips by turnover ───────────────────────────────────────────────
def chart_top_scrips(a: Analytics, out: str, n: int = 20) -> str:
    d = a.scrip.head(n).iloc[::-1]
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(d) + 1.6))
    bars = ax.barh(d.index, d["turnover"], color=NAVY, height=0.68)
    bars[-1].set_color(GOLD)
    _barlabels(ax, bars, [f"{npr(v)}  ({p:.1f}%)"
                          for v, p in zip(d["turnover"], d["mkt_share_pct"])])
    ax.set_xlim(0, d["turnover"].max() * 1.22)
    cr_axis(ax)
    ax.set_xlabel("Turnover (Rs crore)")
    ax.set_title(f"Top {n} scrips by turnover — {a.date}")
    ax.grid(axis="y", visible=False)
    return _finish(fig, ax, out,
                   f"Top {n} scrips = {d['mkt_share_pct'].sum():.1f}% of market turnover")


# ── 03 broker buy vs sell (butterfly) ───────────────────────────────────────
def chart_broker_butterfly(a: Analytics, out: str, n: int = 20) -> str:
    d = a.broker.head(n).iloc[::-1]
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(11, 0.44 * len(d) + 1.8))
    ax.barh(y, -d["sell_amt"], color=SELL, height=0.7, label="Sell")
    ax.barh(y, d["buy_amt"], color=BUY, height=0.7, label="Buy")
    ax.set_yticks(y, [bl(i) for i in d.index])
    lim = max(d["sell_amt"].max(), d["buy_amt"].max()) * 1.35
    ax.set_xlim(-lim, lim)
    ax.axvline(0, color=INK, lw=1)
    for yi, (sv, bv, sh) in enumerate(zip(d["sell_amt"], d["buy_amt"], d["share_pct"])):
        ax.text(-sv - 0.015 * lim, yi, npr(sv, ""), ha="right", va="center", fontsize=7.6, color=SELL)
        ax.text(bv + 0.015 * lim, yi, npr(bv, ""), ha="left", va="center", fontsize=7.6, color=BUY)
        ax.text(0, yi + 0.38, f"{sh:.1f}%", ha="center", va="center", fontsize=6.6, color=GREY)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{abs(v) / CR:,.1f}"))
    ax.set_xlabel("Rs crore  ←  sell    |    buy  →")
    ax.set_title(f"Top {n} brokers by gross turnover — buy vs sell split")
    ax.legend(loc="lower right", ncols=2)
    ax.grid(axis="y", visible=False)
    return _finish(fig, ax, out,
                   "% label = broker share of total two-sided participation")


# ── 04 broker net flow ──────────────────────────────────────────────────────
def chart_broker_net(a: Analytics, out: str, n: int = 12) -> str:
    top = a.broker.nlargest(n, "net")
    bot = a.broker.nsmallest(n, "net")
    d = pd.concat([bot, top]).sort_values("net")
    fig, ax = plt.subplots(figsize=(10.5, 0.40 * len(d) + 1.8))
    colors = [BUY if v > 0 else SELL for v in d["net"]]
    bars = ax.barh([bl(i) for i in d.index], d["net"], color=colors, height=0.7)
    lim = d["net"].abs().max() * 1.35
    ax.set_xlim(-lim, lim)
    ax.axvline(0, color=INK, lw=1)
    for b, v, g in zip(bars, d["net"], d["net_pct_of_gross"]):
        off = 0.015 * lim * (1 if v > 0 else -1)
        ax.text(v + off, b.get_y() + b.get_height() / 2,
                f"{npr(v, '')}  ({g:+.0f}% of gross)",
                va="center", ha="left" if v > 0 else "right", fontsize=7.6,
                color=BUY if v > 0 else SELL)
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v / CR:,.1f}"))
    ax.set_xlabel("Net flow (Rs crore)   —   negative = net seller")
    ax.set_title(f"Net broker flow — top {n} accumulators vs top {n} distributors")
    ax.grid(axis="y", visible=False)
    return _finish(fig, ax, out,
                   "Net = buy turnover − sell turnover. Client-level flow is not "
                   "observable; broker net is a proxy for house/client direction.")


# ── 05 largest single transactions ──────────────────────────────────────────
def chart_block_trades(a: Analytics, out: str, n: int = 20) -> str:
    d = a.blocks.head(n).iloc[::-1]
    labels = [f"{r.symbol}  {bl(r.buyer_l)} ← {bl(r.seller_l)}" for r in d.itertuples()]
    fig, ax = plt.subplots(figsize=(11.5, 0.44 * len(d) + 1.8))
    colors = [GOLD if c else NAVY for c in d["cross"]]
    bars = ax.barh(labels, d["amount"], color=colors, height=0.68)
    _barlabels(ax, bars, [f"{npr(r.amount)}   {qty(r.qty)} @ {r.rate:,.1f}"
                          for r in d.itertuples()], size=7.8)
    ax.set_xlim(0, d["amount"].max() * 1.35)
    cr_axis(ax)
    ax.set_xlabel("Contract value (Rs crore)")
    ax.set_title(f"{n} largest single transactions — {a.date}")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", labelsize=8)
    handles = [plt.Rectangle((0, 0), 1, 1, color=NAVY),
               plt.Rectangle((0, 0), 1, 1, color=GOLD)]
    ax.legend(handles, ["Inter-broker", "Cross trade (same broker both sides)"],
              loc="lower right")
    return _finish(fig, ax, out, "Label reads: SCRIP  buyer ← seller")


# ── 06 broker → broker flow heatmap ─────────────────────────────────────────
def chart_pair_heatmap(a: Analytics, out: str, n: int = 18) -> str:
    top = a.broker.head(n).index.tolist()
    m = (a.pairs[a.pairs["buyer_l"].isin(top) & a.pairs["seller_l"].isin(top)]
         .pivot_table(index="seller_l", columns="buyer_l", values="amount", aggfunc="sum")
         .reindex(index=top, columns=top).fillna(0.0))
    v = m.values.copy()
    if not (v > 0).any():
        return ""
    fig, ax = plt.subplots(figsize=(10.5, 8.6))
    im = ax.imshow(np.where(v > 0, v, np.nan), cmap=SEQ_CMAP,
                   norm=LogNorm(vmin=max(v[v > 0].min(), 1e4), vmax=v.max()))
    ax.set_xticks(range(n), [bl(c) for c in top], rotation=90, fontsize=7.5)
    ax.set_yticks(range(n), [bl(c) for c in top], fontsize=7.5)
    ax.set_xlabel("Buying broker", fontweight="bold")
    ax.set_ylabel("Selling broker", fontweight="bold")
    ax.set_title(f"Broker-to-broker flow matrix — top {n} brokers")
    for i in range(n):
        for j in range(n):
            if v[i, j] > 0.30 * CR:
                ax.text(j, i, f"{v[i, j] / CR:.1f}", ha="center", va="center",
                        fontsize=6.2, color="white" if v[i, j] > 0.6 * v.max() else INK)
    ax.grid(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.045, pad=0.02)
    cb.set_label("Traded value (Rs, log scale)")
    diag = np.trace(v)
    return _finish(fig, ax, out,
                   f"Cell = value transferred seller→buyer. Diagonal (cross trades) "
                   f"= {npr(diag)} within this subset.")


# ── 07 trade size distribution + Pareto ─────────────────────────────────────
def chart_trade_size(a: Analytics, out: str) -> str:
    amt = a.df["amount"].values
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 4.6))

    bins = np.logspace(np.log10(amt.min()), np.log10(amt.max()), 45)
    ax1.hist(amt, bins=bins, color=NAVY, alpha=0.85)
    ax1.set_xscale("log")
    for q, c in [(0.5, GOLD), (0.95, SELL)]:
        x = np.quantile(amt, q)
        ax1.axvline(x, color=c, ls="--", lw=1.3)
        ax1.text(x, ax1.get_ylim()[1] * 0.92, f" p{int(q * 100)} = {npr(x)}",
                 color=c, fontsize=8)
    ax1.set_xlabel("Contract value (Rs, log scale)")
    ax1.set_ylabel("Number of trades")
    ax1.set_title("Trade-size distribution")

    s = np.sort(amt)[::-1]
    cum = np.cumsum(s) / s.sum() * 100
    x = np.arange(1, s.size + 1) / s.size * 100
    ax2.plot(x, cum, color=NAVY, lw=2)
    ax2.fill_between(x, cum, color=NAVY, alpha=0.10)
    for p in (1, 5, 10, 20):
        yv = np.interp(p, x, cum)
        ax2.plot([p, p], [0, yv], color=GOLD, lw=1, ls=":")
        ax2.plot(p, yv, "o", color=GOLD, ms=5)
        ax2.text(p + 1.5, yv - 3, f"top {p}% of trades → {yv:.0f}% of turnover",
                 fontsize=8, color=INK)
    ax2.set_xlim(0, 100); ax2.set_ylim(0, 101)
    ax2.set_xlabel("Trades ranked by size (%)")
    ax2.set_ylabel("Cumulative turnover (%)")
    ax2.set_title("Turnover concentration by ticket size")
    fig.tight_layout()
    return _finish(fig, (ax1, ax2), out)


# ── 08 Lorenz / concentration ───────────────────────────────────────────────
def chart_concentration(a: Analytics, out: str) -> str:
    fig, ax = plt.subplots(figsize=(7.2, 6.2))
    for vals, label, col in [
        (a.broker["gross"].values, "Brokers", NAVY),
        (a.scrip["turnover"].values, "Scrips", GOLD),
    ]:
        x, y, g = lorenz(vals)
        ax.plot(100 * x, 100 * y, color=col, lw=2.2, label=f"{label} (Gini {g:.2f})")
        ax.fill_between(100 * x, 100 * y, 100 * x, color=col, alpha=0.08)
    ax.plot([0, 100], [0, 100], color=GREY, ls="--", lw=1, label="Perfect equality")
    ax.set_xlabel("Cumulative share of participants (%, smallest first)")
    ax.set_ylabel("Cumulative share of turnover (%)")
    ax.set_title("Concentration of activity — Lorenz curves")
    ax.legend(loc="upper left")
    k = a.kpi
    ax.text(0.98, 0.06,
            f"Broker HHI {k['broker_hhi']:,.0f}   |   Scrip HHI {k['scrip_hhi']:,.0f}\n"
            f"Top-10 brokers {k['top10_broker_pct']:.1f}%   |   "
            f"Top-10 scrips {k['top10_scrip_pct']:.1f}%",
            transform=ax.transAxes, ha="right", fontsize=8.5, color=INK,
            bbox=dict(fc=LIGHT, ec="#D3DAE6"))
    return _finish(fig, ax, out, "HHI on turnover shares, 0–10,000 scale.")


# ── 09 sequence-ordered turnover profile (time proxy) ───────────────────────
def chart_sequence_profile(a: Analytics, out: str, bins: int = 20) -> str:
    d = a.df.dropna(subset=["seq_pct"])
    if d.empty:
        return ""
    b = pd.cut(d["seq_pct"], np.linspace(0, 1, bins + 1), include_lowest=True)
    g = d.groupby(b, observed=True).agg(amt=("amount", "sum"), n=("amount", "size"))
    x = np.arange(len(g))
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.bar(x, g["amt"], color=NAVY, width=0.82)
    ax.set_xticks(x[::2], [f"{int(100 * i / bins)}%" for i in x[::2]])
    cr_axis(ax, "y")
    ax.set_ylabel("Turnover (Rs crore)")
    ax.set_xlabel("Position in the day's contract sequence (proxy for session progress)")
    ax2 = ax.twinx()
    ax2.plot(x, g["n"], color=GOLD, lw=2, marker="o", ms=4)
    ax2.set_ylabel("Trade count", color=GOLD)
    ax2.grid(False)
    ax.set_title("Turnover profile across the trading session (sequence proxy)")
    return _finish(fig, ax, out,
                   "NEPSE floor sheet carries no trade timestamp. Contract numbers are "
                   "monotonic within each matching stream, so this is an ordering proxy, "
                   "not clock time — read shape, not levels.")


# ── 10 cross-trade concentration ────────────────────────────────────────────
def chart_cross_trades(a: Analytics, out: str, n: int = 15) -> str:
    d = a.broker[a.broker["cross_amt"] > 0].nlargest(n, "cross_amt").iloc[::-1]
    if d.empty:
        return ""
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(d) + 1.8))
    bars = ax.barh([bl(i) for i in d.index], d["cross_amt"], color=GOLD, height=0.68)
    _barlabels(ax, bars, [f"{npr(v)}   ({p:.1f}% of own gross)"
                          for v, p in zip(d["cross_amt"], d["cross_pct"])])
    ax.set_xlim(0, d["cross_amt"].max() * 1.35)
    cr_axis(ax)
    ax.set_xlabel("Cross-trade value (Rs crore)")
    ax.set_title("Cross trades — same broker on both legs")
    ax.grid(axis="y", visible=False)
    return _finish(fig, ax, out,
                   f"Market-wide cross trades: {npr(a.kpi['cross_amt'])} "
                   f"({a.kpi['cross_pct']:.1f}% of turnover). Typically negotiated / "
                   "inter-client transfers, worth screening before reading net flow.")


# ── 11 per-scrip broker breakdown (small multiples) ─────────────────────────
def chart_scrip_broker_panels(a: Analytics, out: str, n_scrips: int = 6, n_br: int = 6) -> str:
    syms = a.scrip.head(n_scrips).index.tolist()
    ncol = 3
    nrow = int(np.ceil(len(syms) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.0 * ncol, 3.5 * nrow))
    axes = np.atleast_1d(axes).ravel()
    for ax, sym in zip(axes, syms):
        d = a.df[a.df["symbol"] == sym]
        b = d.groupby("buyer_l")["amount"].sum().nlargest(n_br)
        s = d.groupby("seller_l")["amount"].sum().nlargest(n_br)
        idx = list(dict.fromkeys(list(b.index) + list(s.index)))[: n_br * 2]
        bb = b.reindex(idx).fillna(0)
        ss = s.reindex(idx).fillna(0)
        order = (bb + ss).sort_values().index
        bb, ss = bb.reindex(order), ss.reindex(order)
        y = np.arange(len(order))
        ax.barh(y, -ss.values, color=SELL, height=0.72)
        ax.barh(y, bb.values, color=BUY, height=0.72)
        ax.set_yticks(y, [bl(i) for i in order], fontsize=7.5)
        lim = max(ss.max(), bb.max()) * 1.25
        ax.set_xlim(-lim, lim)
        ax.axvline(0, color=INK, lw=0.9)
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{abs(v) / LAKH:,.0f}"))
        r = a.scrip.loc[sym]
        ax.set_title(f"{sym} — {npr(r['turnover'])} | VWAP {r['vwap']:,.1f} "
                     f"| rng {r['range_pct']:.1f}%", fontsize=10)
        ax.set_xlabel("Rs lakh  ← sell | buy →", fontsize=8)
        ax.grid(axis="y", visible=False)
        ax.tick_params(labelsize=7.5)
    for ax in axes[len(syms):]:
        ax.set_axis_off()
    fig.suptitle("Broker participation in the day's most-traded scrips",
                 fontsize=14, fontweight="bold", color=NAVY, y=1.005)
    fig.tight_layout()
    return _finish(fig, axes, out)


# ── 12 VWAP vs turnover bubble ──────────────────────────────────────────────
def chart_scrip_bubble(a: Analytics, out: str, n_label: int = 15) -> str:
    d = a.scrip.copy()
    fig, ax = plt.subplots(figsize=(10.5, 6.6))
    sizes = 12 + 260 * (d["trades"] / d["trades"].max()) ** 0.55
    sc = ax.scatter(d["turnover"], d["vwap"], s=sizes, c=d["range_pct"],
                    cmap=DIV_CMAP.reversed(), alpha=0.85, edgecolor=NAVY, linewidth=0.5)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xlabel("Turnover (Rs, log)")
    ax.set_ylabel("VWAP (Rs, log)")
    ax.set_title("Scrip map — turnover vs price level (bubble = trade count)")
    for sym in d.head(n_label).index:
        r = d.loc[sym]
        ax.annotate(sym, (r["turnover"], r["vwap"]), fontsize=8, color=NAVY,
                    xytext=(5, 4), textcoords="offset points", fontweight="bold")
    cb = fig.colorbar(sc, ax=ax, fraction=0.04, pad=0.02)
    cb.set_label("Intraday range as % of VWAP")
    ax.xaxis.set_major_formatter(mticker.FuncFormatter(
        lambda v, _: npr(v, "").replace(".00", "")))
    return _finish(fig, ax, out,
                   "Wide-range, low-turnover names in the upper band are where "
                   "execution slippage is worst.")


# ── 13 price dispersion of top scrips ───────────────────────────────────────
def chart_price_dispersion(a: Analytics, out: str, n: int = 20) -> str:
    d = a.scrip.head(n).iloc[::-1]
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(10, 0.42 * len(d) + 1.8))
    for i, (sym, r) in enumerate(d.iterrows()):
        lo, hi, vw = r["low"], r["high"], r["vwap"]
        ax.plot([100 * (lo / vw - 1), 100 * (hi / vw - 1)], [i, i],
                color=NAVY, lw=3.2, solid_capstyle="round", alpha=0.8)
        ax.plot(0, i, "|", color=GOLD, ms=14, mew=2.4)
        ax.plot(100 * (r["last"] / vw - 1), i, "o", color=SELL, ms=5)
    ax.set_yticks(y, d.index, fontsize=8.5)
    ax.axvline(0, color=GREY, lw=0.8, ls="--")
    ax.set_xlabel("Deviation from VWAP (%)")
    ax.set_title(f"Intraday price dispersion — top {n} scrips by turnover")
    ax.grid(axis="y", visible=False)
    handles = [plt.Line2D([], [], color=NAVY, lw=3, label="Low–high band"),
               plt.Line2D([], [], color=GOLD, marker="|", ls="", ms=12, mew=2.4, label="VWAP"),
               plt.Line2D([], [], color=SELL, marker="o", ls="", label="Last traded")]
    ax.legend(handles=handles, loc="lower right", fontsize=8)
    return _finish(fig, ax, out,
                   "'Last' = final contract in sequence order, a proxy for the "
                   "closing print.")


# ── 14 broker × scrip net position heatmap ──────────────────────────────────
def chart_net_position(a: Analytics, out: str, n_br: int = 14, n_sc: int = 14) -> str:
    brs = a.broker.head(n_br).index.tolist()
    scs = a.scrip.head(n_sc).index.tolist()
    m = (a.net_pos[a.net_pos["broker"].isin(brs) & a.net_pos["symbol"].isin(scs)]
         .pivot_table(index="broker", columns="symbol", values="net", aggfunc="sum")
         .reindex(index=brs, columns=scs).fillna(0.0)) / CR
    lim = np.abs(m.values).max()
    fig, ax = plt.subplots(figsize=(1.0 + 0.72 * n_sc, 1.2 + 0.48 * n_br))
    im = ax.imshow(m.values, cmap=DIV_CMAP, vmin=-lim, vmax=lim)
    ax.set_xticks(range(n_sc), scs, rotation=90, fontsize=8)
    ax.set_yticks(range(n_br), [bl(b) for b in brs], fontsize=8)
    for i in range(n_br):
        for j in range(n_sc):
            v = m.values[i, j]
            if abs(v) > 0.10:
                ax.text(j, i, f"{v:+.1f}", ha="center", va="center", fontsize=6.6,
                        color="white" if abs(v) > 0.62 * lim else INK)
    ax.grid(False)
    ax.set_title(f"Net broker position by scrip (Rs crore) — top {n_br} brokers × "
                 f"top {n_sc} scrips")
    cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.015)
    cb.set_label("Net buy (+) / net sell (−), Rs crore")
    return _finish(fig, ax, out,
                   "Cells above ±0.10 Cr labelled. Reads as 'who accumulated what'.")


# ────────────────────────────────────────────────────────────────────────────
# HTML report (self-contained — images base64-embedded)
# ────────────────────────────────────────────────────────────────────────────
def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def df_to_html(d: pd.DataFrame) -> str:
    return d.to_html(index=False, border=0, escape=False,
                     classes="tbl", justify="left")


def build_html(a: Analytics, charts: list[tuple[str, str, str]], out: str) -> str:
    k = a.kpi
    top_br = a.broker.head(10).reset_index()
    top_br = pd.DataFrame({
        "Broker": [bl(i, short=False) for i in top_br["broker"]],
        "Buy": top_br["buy_amt"].map(npr),
        "Sell": top_br["sell_amt"].map(npr),
        "Gross": top_br["gross"].map(npr),
        "Net": top_br["net"].map(npr),
        "Share %": top_br["share_pct"].round(2),
        "Trades": top_br["trades"].astype(int),
    })
    top_sc = a.scrip.head(10).reset_index()
    top_sc = pd.DataFrame({
        "Scrip": top_sc["symbol"],
        "Turnover": top_sc["turnover"].map(npr),
        "Volume": top_sc["volume"].map(qty),
        "Trades": top_sc["trades"],
        "VWAP": top_sc["vwap"].round(1),
        "Low–High": [f"{lo:,.0f} – {hi:,.0f}" for lo, hi in zip(top_sc["low"], top_sc["high"])],
        "Range %": top_sc["range_pct"].round(1),
        "Buy HHI": top_sc["buy_hhi"].round(0).astype(int),
    })
    blocks = a.blocks.head(15).copy()
    blocks = pd.DataFrame({
        "Scrip": blocks["symbol"],
        "Buyer": [bl(b, short=False) for b in blocks["buyer_l"]],
        "Seller": [bl(s, short=False) for s in blocks["seller_l"]],
        "Qty": blocks["qty"].map(qty),
        "Rate": blocks["rate"].map(lambda v: f"{v:,.1f}"),
        "Value": blocks["amount"].map(npr),
        "Type": np.where(blocks["cross"], "Cross", "Inter-broker"),
    })

    kpi_html = "".join(
        f'<div class="kpi"><div class="kv">{v}</div><div class="kl">{lbl}</div></div>'
        for lbl, v in [
            ("Turnover", npr(k["turnover"])), ("Volume", qty(k["volume"])),
            ("Trades", qty(k["trades"])), ("Scrips", qty(k["scrips"])),
            ("Brokers", qty(k["brokers"])), ("Avg ticket", npr(k["avg_ticket"])),
            ("Top-10 brokers", f"{k['top10_broker_pct']:.1f}%"),
            ("Cross trades", f"{k['cross_pct']:.1f}%"),
        ]
    )
    charts_html = "".join(
        f'<section><h2>{title}</h2><p class="note">{note}</p>'
        f'<img src="data:image/png;base64,{_b64(p)}" alt="{title}"></section>'
        for title, note, p in charts if p and os.path.exists(p)
    )

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>NEPSE Floor Sheet Analytics — {a.date}</title>
<style>
 body{{font-family:Calibri,'Segoe UI',Arial,sans-serif;color:{INK};margin:0;
      background:#F7F9FC;}}
 .wrap{{max-width:1180px;margin:0 auto;padding:24px;background:#fff;}}
 header{{border-bottom:3px solid {GOLD};padding-bottom:12px;margin-bottom:18px;}}
 h1{{color:{NAVY};font-size:24px;margin:0 0 4px;}}
 .sub{{color:{GREY};font-size:13px;}}
 h2{{color:{NAVY};font-size:16px;margin:26px 0 4px;border-left:4px solid {GOLD};
     padding-left:8px;}}
 .note{{color:{GREY};font-size:11.5px;margin:0 0 8px;}}
 img{{width:100%;border:1px solid #E1E6EF;border-radius:4px;}}
 .kpis{{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 22px;}}
 .kpi{{flex:1 1 128px;background:{LIGHT};border:1px solid #DDE3ED;border-radius:4px;
       padding:10px 8px;text-align:center;}}
 .kv{{font-size:16px;font-weight:700;color:{NAVY};}}
 .kl{{font-size:10px;color:{GREY};text-transform:uppercase;letter-spacing:.4px;}}
 table.tbl{{border-collapse:collapse;width:100%;font-size:12px;margin:6px 0 4px;}}
 table.tbl th{{background:{NAVY};color:#fff;text-align:left;padding:6px 8px;
               font-weight:600;}}
 table.tbl td{{padding:5px 8px;border-bottom:1px solid #E6EAF2;}}
 table.tbl tr:nth-child(even) td{{background:#FAFBFD;}}
 footer{{margin-top:28px;padding-top:12px;border-top:1px solid #E1E6EF;
         color:{GREY};font-size:11px;}}
</style></head><body><div class="wrap">
<header><h1>NEPSE Floor Sheet Analytics</h1>
<div class="sub">Trading day {a.date} &nbsp;·&nbsp; generated {datetime.now():%Y-%m-%d %H:%M}</div>
</header>
<div class="kpis">{kpi_html}</div>
<h2>Top 10 brokers by gross turnover</h2>{df_to_html(top_br)}
<h2>Top 10 scrips by turnover</h2>{df_to_html(top_sc)}
<h2>15 largest single transactions</h2>{df_to_html(blocks)}
{charts_html}
<footer>Generated by floorsheet_viz.py from the NEPSE daily floor sheet.
Broker net flow is a house-level proxy — client identity is not disclosed in the
floor sheet, and a broker's net can net off unrelated client orders. The floor
sheet carries no trade timestamp; any session-progress view is a contract-sequence
ordering proxy.</footer>
</div></body></html>"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


# ────────────────────────────────────────────────────────────────────────────
# Compact email body (cid: images — Gmail clips bodies above ~102 KB, so the
# mail body must NOT carry base64 images; the full HTML goes as an attachment)
# ────────────────────────────────────────────────────────────────────────────
EMAIL_CHARTS = ["01_kpi.png", "02_top_scrips.png", "03_broker_butterfly.png",
                "04_broker_net.png", "05_block_trades.png"]


def build_email_body(a: Analytics, cdir: str, out_html: str,
                     out_manifest: str, dashboard_url: str | None = None
                     ) -> tuple[str, dict]:
    """Write an email-client-safe HTML body + a {cid: filepath} manifest."""
    import json

    def tbl(d: pd.DataFrame, right_cols=()) -> str:
        th = "".join(
            f'<th style="background:{NAVY};color:#fff;padding:6px 8px;'
            f'text-align:left;font:600 12px Calibri,Arial">{c}</th>' for c in d.columns
        )
        rows = []
        for i, (_, r) in enumerate(d.iterrows()):
            bg = "#FAFBFD" if i % 2 else "#FFFFFF"
            tds = "".join(
                f'<td style="padding:5px 8px;border-bottom:1px solid #E6EAF2;'
                f'background:{bg};font:12px Calibri,Arial;'
                f'text-align:{"right" if c in right_cols else "left"}">{v}</td>'
                for c, v in r.items()
            )
            rows.append(f"<tr>{tds}</tr>")
        return (f'<table cellpadding="0" cellspacing="0" border="0" width="100%" '
                f'style="border-collapse:collapse;margin:4px 0 14px">'
                f"<tr>{th}</tr>{''.join(rows)}</table>")

    k = a.kpi
    kpis = [("Turnover", npr(k["turnover"])), ("Trades", qty(k["trades"])),
            ("Volume", qty(k["volume"])), ("Scrips", qty(k["scrips"])),
            ("Brokers", qty(k["brokers"])), ("Top-10 brokers", f"{k['top10_broker_pct']:.1f}%")]
    kcells = "".join(
        f'<td width="16.6%" style="background:{LIGHT};border:1px solid #DDE3ED;'
        f'padding:9px 4px;text-align:center">'
        f'<div style="font:700 15px Calibri,Arial;color:{NAVY}">{v}</div>'
        f'<div style="font:10px Calibri,Arial;color:{GREY};text-transform:uppercase">'
        f"{lbl}</div></td>" for lbl, v in kpis)

    br = a.broker.head(10).reset_index()
    t_br = pd.DataFrame({
        "Broker": [bl(i, short=False) for i in br["broker"]],
        "Buy": br["buy_amt"].map(npr), "Sell": br["sell_amt"].map(npr),
        "Gross": br["gross"].map(npr), "Net": br["net"].map(npr),
        "Share": br["share_pct"].map(lambda v: f"{v:.2f}%"),
    })
    sc = a.scrip.head(10).reset_index()
    t_sc = pd.DataFrame({
        "Scrip": sc["symbol"], "Turnover": sc["turnover"].map(npr),
        "Volume": sc["volume"].map(qty), "Trades": sc["trades"].map(qty),
        "VWAP": sc["vwap"].map(lambda v: f"{v:,.1f}"),
        "Range": sc["range_pct"].map(lambda v: f"{v:.1f}%"),
    })
    bk = a.blocks.head(10)
    t_bk = pd.DataFrame({
        "Scrip": bk["symbol"], "Buyer": [bl(b) for b in bk["buyer_l"]],
        "Seller": [bl(s) for s in bk["seller_l"]], "Qty": bk["qty"].map(qty),
        "Rate": bk["rate"].map(lambda v: f"{v:,.1f}"), "Value": bk["amount"].map(npr),
        "Type": np.where(bk["cross"], "Cross", "Inter-broker"),
    })

    manifest, imgs = {}, []
    for i, fn in enumerate(EMAIL_CHARTS, start=1):
        p = os.path.join(cdir, fn)
        if not os.path.exists(p):
            continue
        cid = f"chart{i:02d}"
        manifest[cid] = os.path.abspath(p)
        imgs.append(f'<img src="cid:{cid}" width="720" '
                    f'style="width:100%;max-width:720px;border:1px solid #E1E6EF;'
                    f'margin:6px 0 16px" alt="{fn}">')

    cta = ""
    if dashboard_url:
        cta = (f'<tr><td style="padding:14px 0 2px" align="center">'
               f'<a href="{dashboard_url}" style="display:inline-block;background:{NAVY};'
               f'color:#fff;text-decoration:none;font:600 14px Calibri,Arial;'
               f'padding:11px 26px;border-radius:4px">Open the interactive dashboard '
               f'&rsaquo;</a>'
               f'<div style="font:11px Calibri,Arial;color:{GREY};padding-top:6px">'
               f'Sortable broker and scrip tables, per-broker drill-down, flow matrix. '
               f'The same file is attached if you prefer to open it offline.</div>'
               f'</td></tr>')

    html = f"""<html><body style="margin:0;background:#F7F9FC">
<table cellpadding="0" cellspacing="0" border="0" width="100%"><tr><td align="center">
<table cellpadding="0" cellspacing="0" border="0" width="760"
       style="background:#fff;padding:22px">
<tr><td style="border-bottom:3px solid {GOLD};padding-bottom:10px">
  <div style="font:700 22px Calibri,Arial;color:{NAVY}">NEPSE Floor Sheet Analytics</div>
  <div style="font:13px Calibri,Arial;color:{GREY}">Trading day {a.date}</div>
</td></tr>
<tr><td style="padding-top:14px">
  <table cellpadding="0" cellspacing="4" border="0" width="100%"><tr>{kcells}</tr></table>
</td></tr>
{cta}
<tr><td>
  <h3 style="font:600 15px Calibri,Arial;color:{NAVY};border-left:4px solid {GOLD};
             padding-left:8px;margin:18px 0 2px">Top 10 brokers by gross turnover</h3>
  {tbl(t_br, right_cols=("Buy", "Sell", "Gross", "Net", "Share"))}
  <h3 style="font:600 15px Calibri,Arial;color:{NAVY};border-left:4px solid {GOLD};
             padding-left:8px;margin:18px 0 2px">Top 10 scrips by turnover</h3>
  {tbl(t_sc, right_cols=("Turnover", "Volume", "Trades", "VWAP", "Range"))}
  <h3 style="font:600 15px Calibri,Arial;color:{NAVY};border-left:4px solid {GOLD};
             padding-left:8px;margin:18px 0 2px">10 largest single transactions</h3>
  {tbl(t_bk, right_cols=("Qty", "Rate", "Value"))}
  {''.join(imgs)}
</td></tr>
<tr><td style="border-top:1px solid #E1E6EF;padding-top:10px;
               font:11px Calibri,Arial;color:{GREY}">
  Full report with all {len(EMAIL_CHARTS)}+ exhibits and the underlying summary
  tables is attached. Broker net flow is a house-level proxy: the floor sheet does
  not disclose client identity, and offsetting client orders net out within a
  broker code. No trade timestamp is published, so session-progress views are
  contract-sequence proxies.
</td></tr></table></td></tr></table></body></html>"""

    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    with open(out_manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return out_html, manifest


# ────────────────────────────────────────────────────────────────────────────
# Tables
# ────────────────────────────────────────────────────────────────────────────
def dump_tables(a: Analytics, tdir: str) -> list[str]:
    os.makedirs(tdir, exist_ok=True)
    paths = []
    for name, d in [
        ("broker_summary", a.broker.reset_index()),
        ("scrip_summary", a.scrip.reset_index()),
        ("block_trades", a.blocks),
        ("broker_pair_flow", a.pairs.head(300)),
        ("broker_scrip_net", a.net_pos[a.net_pos["gross"] > 10 * LAKH]),
    ]:
        p = os.path.join(tdir, f"{name}.csv")
        d.round(2).to_csv(p, index=False)
        paths.append(p)
    return paths


# ────────────────────────────────────────────────────────────────────────────
# Orchestration
# ────────────────────────────────────────────────────────────────────────────
NPT_OFFSET = timedelta(hours=5, minutes=45)


def npt_today() -> date:
    """Today's date in Nepal time (UTC+5:45) — the runner clock is UTC."""
    return (datetime.now(timezone.utc) + NPT_OFFSET).date()


def derive_trade_date(df: pd.DataFrame) -> str | None:
    """Trading date from the contract numbers (YYYYMMDD prefix).

    This is authoritative: it comes from the exchange's own numbering, so it
    catches the case where a scrape silently returns the previous session.
    """
    if "contract" not in df.columns:
        return None
    cn = df["contract"].astype(str).str.strip()
    pre = cn[cn.str.fullmatch(r"\d{16}")].str[:8]
    if pre.empty:
        return None
    top = pre.value_counts()
    d = top.index[0]
    try:
        parsed = date(int(d[:4]), int(d[4:6]), int(d[6:8])).isoformat()
    except ValueError:
        return None
    if len(top) > 1:
        print(f"WARNING: contract numbers span {len(top)} dates "
              f"{dict(list(top.items())[:3])}; using {parsed}.")
    return parsed


def freshness(date_str: str, max_age_days: int = 0) -> tuple[bool, int, str]:
    """Is this floor sheet the current session's? Returns (ok, age, message)."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return False, -1, f"Unparseable trading date {date_str!r}."
    age = (npt_today() - d).days
    if age < 0:
        return False, age, f"Trading date {date_str} is in the future (NPT today {npt_today()})."
    if age > max_age_days:
        return (False, age,
                f"Floor sheet is {age} day(s) old ({date_str}); NPT today is "
                f"{npt_today()}. Refusing to publish it as a fresh report.")
    return True, age, f"Floor sheet is current ({date_str}, {age} day(s) old)."


def filename_date(path: str) -> str | None:
    """Date embedded in the filename, or None if there isn't one."""
    m = re.search(r"(20\d{2})[._\-]?(\d{2})[._\-]?(\d{2})", os.path.basename(path))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def infer_date(path: str) -> str:
    return filename_date(path) or npt_today().isoformat()


def run(csv_path: str, outdir: str, top: int = 20, dpi: int = 120,
        broker_map: str | None = None, date_str: str | None = None,
        dashboard_url: str | None = None) -> dict:
    plt.rcParams.update(BASE_RC)
    plt.rcParams["figure.dpi"] = dpi

    df = load_floorsheet(csv_path, broker_map)
    date_str = date_str or derive_trade_date(df) or infer_date(csv_path)
    a = build_analytics(df, date_str)

    cdir = os.path.join(outdir, "charts")
    os.makedirs(cdir, exist_ok=True)
    P = lambda n: os.path.join(cdir, n)

    spec = [
        ("Market snapshot", "Headline activity for the session.",
         chart_kpi(a, P("01_kpi.png"))),
        ("Where the turnover went", "Turnover leadership across scrips.",
         chart_top_scrips(a, P("02_top_scrips.png"), n=top)),
        ("Broker activity — buy vs sell", "Gross participation split by side.",
         chart_broker_butterfly(a, P("03_broker_butterfly.png"), n=top)),
        ("Net broker flow", "Accumulators vs distributors on the day.",
         chart_broker_net(a, P("04_broker_net.png"), n=12)),
        ("Largest single transactions", "Block prints worth a second look.",
         chart_block_trades(a, P("05_block_trades.png"), n=20)),
        ("Broker-to-broker flow matrix", "Who traded with whom, by value.",
         chart_pair_heatmap(a, P("06_pair_heatmap.png"), n=18)),
        ("Ticket-size structure", "Distribution and concentration of trade sizes.",
         chart_trade_size(a, P("07_trade_size.png"))),
        ("Concentration of activity", "Lorenz curves and HHI for brokers and scrips.",
         chart_concentration(a, P("08_concentration.png"))),
        ("Session turnover profile", "Sequence-ordered activity (proxy, not clock time).",
         chart_sequence_profile(a, P("09_sequence_profile.png"))),
        ("Cross trades", "Same broker on both legs — screen before reading flow.",
         chart_cross_trades(a, P("10_cross_trades.png"), n=15)),
        ("Broker participation by scrip", "Top counterparties in the busiest names.",
         chart_scrip_broker_panels(a, P("11_scrip_panels.png"), n_scrips=6)),
        ("Scrip map", "Turnover vs price level vs intraday range.",
         chart_scrip_bubble(a, P("12_scrip_bubble.png"))),
        ("Intraday price dispersion", "Execution spread around VWAP.",
         chart_price_dispersion(a, P("13_price_dispersion.png"), n=top)),
        ("Net position matrix", "Broker × scrip net accumulation.",
         chart_net_position(a, P("14_net_position.png"))),
    ]

    tables = dump_tables(a, os.path.join(outdir, "tables"))
    html = build_html(a, spec, os.path.join(outdir, f"floorsheet_{date_str}.html"))
    body, manifest = build_email_body(
        a, cdir,
        os.path.join(outdir, "email_body.html"),
        os.path.join(outdir, "email_images.json"),
        dashboard_url=dashboard_url,
    )

    return {"analytics": a, "charts": [p for _, _, p in spec if p],
            "tables": tables, "html": html,
            "email_body": body, "email_images": manifest}


def main(argv=None):
    ap = argparse.ArgumentParser(description="NEPSE floor sheet visual analytics")
    ap.add_argument("--csv", required=True, help="floor sheet CSV")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--top", type=int, default=20, help="rows in top-N charts")
    ap.add_argument("--dpi", type=int, default=120)
    ap.add_argument("--brokers", default=None,
                    help="optional CSV mapping broker code -> name")
    ap.add_argument("--date", default=None, help="override trading date label")
    args = ap.parse_args(argv)

    res = run(args.csv, args.outdir, top=args.top, dpi=args.dpi,
              broker_map=args.brokers, date_str=args.date)
    k = res["analytics"].kpi
    print(f"Turnover {npr(k['turnover'])} | {k['trades']:,} trades | "
          f"{k['scrips']} scrips | {k['brokers']} brokers")
    print(f"Charts : {len(res['charts'])} -> {os.path.join(args.outdir, 'charts')}")
    print(f"Tables : {len(res['tables'])} -> {os.path.join(args.outdir, 'tables')}")
    print(f"Report : {res['html']}")
    print(f"Mail   : {res['email_body']} "
          f"({len(res['email_images'])} inline images, see email_images.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
