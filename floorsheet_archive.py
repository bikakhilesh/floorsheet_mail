#!/usr/bin/env python3
"""
floorsheet_archive.py — keep every scraped floor sheet as parquet on a `data`
branch, and pull ranges of it back out as a panel.

    # after a scrape (the workflow does this)
    python floorsheet_archive.py add --file out/floorsheet_2026-07-30.parquet \
        --dir archive/parquet --manifest archive/manifest.csv

    # research: one frame spanning a date range
    python floorsheet_archive.py panel --dir archive/parquet \
        --from 2026-07-01 --to 2026-07-30 --out panel.parquet

    # broker-day or scrip-day aggregates instead of raw contracts
    python floorsheet_archive.py panel --dir archive/parquet --by broker --out brokers.csv
    python floorsheet_archive.py panel --dir archive/parquet --by scrip  --out scrips.csv

Why parquet on a branch rather than artifacts: artifacts expire (30 days here),
and the whole point of the archive is the long series. A day is roughly 0.6 MB
as parquet against 2.0 MB as csv, so a 250-session year costs about 150 MB.
The branch is force-pushed as a single orphan commit, so the repo carries the
current file set once rather than accumulating a new copy of history each day.
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import sys

import pandas as pd

import floorsheet_viz as fv

MANIFEST_COLS = ["date", "file", "rows", "turnover", "volume", "trades",
                 "scrips", "brokers", "bytes"]


def _date_of(fname: str) -> str | None:
    return fv.filename_date(fname)


def list_archive(d: str) -> list[str]:
    if not os.path.isdir(d):
        return []
    return sorted(f for f in os.listdir(d)
                  if f.endswith((".parquet", ".pq")) and _date_of(f))


def summarise(path: str) -> dict:
    """One manifest row. Only ever called on the newly added file."""
    df = fv.load_floorsheet(path)
    date_str = fv.derive_trade_date(df) or _date_of(os.path.basename(path))
    return {
        "date": date_str,
        "file": os.path.basename(path),
        "rows": len(df),
        "turnover": round(float(df["amount"].sum()), 2),
        "volume": int(df["qty"].sum()),
        "trades": len(df),
        "scrips": int(df["symbol"].nunique()),
        "brokers": int(pd.unique(pd.concat([df["buyer_l"], df["seller_l"]])).size),
        "bytes": os.path.getsize(path),
    }


def write_manifest(rows: pd.DataFrame, path: str) -> str:
    rows = rows.drop_duplicates("date", keep="last").sort_values("date",
                                                                 ascending=False)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    rows.to_csv(path, index=False)
    return path


def write_readme(manifest: pd.DataFrame, out: str, repo: str | None = None) -> str:
    """A browsable landing page for the data branch."""
    n = len(manifest)
    span = f"{manifest['date'].min()} to {manifest['date'].max()}" if n else "—"
    total_mb = manifest["bytes"].sum() / 1e6 if n else 0
    recent = manifest.head(15)
    tbl = "\n".join(
        f"| {r.date} | {r.rows:,} | Rs {r.turnover / 1e7:,.2f} Cr | "
        f"{r.scrips} | {r.brokers} | {r.bytes / 1e6:.2f} MB |"
        for r in recent.itertuples()
    ) or "| — | | | | | |"
    body = f"""# NEPSE floor sheet archive

Daily floor sheets as parquet, one file per trading session, written by the
`nepse-floorsheet` workflow. This branch holds data only — no code.

- Sessions: **{n}** ({span})
- Total size: **{total_mb:,.1f} MB**
- Layout: `parquet/floorsheet_YYYY-MM-DD.parquet`
- Index: [`manifest.csv`](manifest.csv)

Columns are the exchange's own: `Contract No.`, `Stock Symbol`, `Buyer`,
`Seller`, `Quantity`, `Rate (Rs)`, `Amount (Rs)`.

## Reading it

```python
import pandas as pd
df = pd.read_parquet("parquet/floorsheet_{manifest['date'].iloc[0] if n else 'YYYY-MM-DD'}.parquet")
```

A date range as one frame, from the repo root on `main`:

```bash
python floorsheet_archive.py panel --dir archive/parquet \\
    --from 2026-07-01 --to 2026-07-30 --out panel.parquet
```

Or broker-day / scrip-day aggregates:

```bash
python floorsheet_archive.py panel --dir archive/parquet --by broker --out brokers.csv
```

## Most recent sessions

| Date | Rows | Turnover | Scrips | Brokers | Size |
|---|---:|---:|---:|---:|---:|
{tbl}

*This branch is force-pushed as a single orphan commit each run, so the repo
stores the current file set once rather than a new copy of history per day.
Clone it directly with `git clone --branch data --depth 1 {repo or '<repo-url>'}`.*
"""
    with open(out, "w", encoding="utf-8") as f:
        f.write(body)
    return out


def cmd_ingest(args) -> int:
    """Bulk-convert a folder of historical sheets into the parquet archive."""
    pats = ("*.csv", "*.csv.gz", "*.CSV", "*.txt", "*.parquet", "*.pq")
    src = args.src
    if os.path.isdir(src):
        files = []
        for pat in pats:
            files += glob.glob(os.path.join(src, "**", pat) if args.recursive
                               else os.path.join(src, pat), recursive=args.recursive)
    else:
        files = glob.glob(src, recursive=args.recursive)
    files = sorted(set(files))
    if not files:
        print(f"Nothing matched {src!r}.", file=sys.stderr)
        return 1

    os.makedirs(args.dir, exist_ok=True)
    existing = {_date_of(f): f for f in list_archive(args.dir)}
    print(f"Found {len(files)} file(s); archive currently holds "
          f"{len(existing)} session(s).\n")

    seen: dict[str, tuple[str, int]] = {}   # date -> (source file, rows)
    done, skipped, failed = [], [], []

    for i, f in enumerate(files, 1):
        base = os.path.basename(f)
        try:
            df = fv.load_floorsheet(f)
            if df.empty:
                raise ValueError("no usable rows after cleaning")
            d = fv.derive_trade_date(df) or _date_of(base)
            if not d:
                raise ValueError("no trading date in the contract numbers "
                                 "or the filename")
        except Exception as e:                      # noqa: BLE001 — report and continue
            print(f"[{i}/{len(files)}] {base}: FAILED — {e}")
            failed.append((base, str(e)))
            continue

        # Two files claiming the same session: keep the fuller one.
        if d in seen:
            prev_file, prev_rows = seen[d]
            if len(df) <= prev_rows:
                print(f"[{i}/{len(files)}] {base}: duplicate of {d} "
                      f"({len(df):,} rows vs {prev_rows:,} in {prev_file}) — skipped")
                skipped.append((base, f"duplicate of {d}"))
                continue
            print(f"[{i}/{len(files)}] {base}: duplicate of {d} but fuller "
                  f"({len(df):,} vs {prev_rows:,}) — replacing")

        target = os.path.join(args.dir, f"floorsheet_{d}.parquet")
        if d in existing and not args.overwrite and d not in seen:
            print(f"[{i}/{len(files)}] {base}: {d} already archived — skipped "
                  f"(--overwrite to replace)")
            skipped.append((base, "already archived"))
            continue

        if args.dry_run:
            print(f"[{i}/{len(files)}] {base}: would write {os.path.basename(target)} "
                  f"({len(df):,} rows, {fv.npr(df['amount'].sum())})")
        else:
            fv.save_parquet(df, target)
            print(f"[{i}/{len(files)}] {base} -> {os.path.basename(target)} "
                  f"({len(df):,} rows, {fv.npr(df['amount'].sum())}, "
                  f"{os.path.getsize(target) / 1e6:.2f} MB)")
        seen[d] = (base, len(df))
        done.append(d)

    print(f"\nConverted {len(done)}, skipped {len(skipped)}, failed {len(failed)}.")
    if failed:
        print("\nFailures:")
        for name, err in failed:
            print(f"  {name}: {err}")

    if args.dry_run:
        print("\nDry run — nothing written. Drop --dry-run to commit the conversion.")
        return 0

    # Rebuilding is the honest choice here: a bulk import can add hundreds of
    # sessions and the incremental path is built for one at a time.
    return cmd_manifest(args)


def _keep_closest(args) -> bool:
    """True when the sheet already archived for this date beats the new one.

    A re-scrape of a session we already hold only earns its place if its
    turnover lands closer to NEPSE's own figure for the day. Ties, and runs
    with no NEPSE figure to compare against, leave the archive alone.
    """
    exp = getattr(args, "expected_turnover", None)
    if exp is None or not os.path.exists(args.manifest):
        return False
    prev = pd.read_csv(args.manifest)
    if prev.empty or "turnover" not in prev.columns:
        return False
    new = summarise(args.file)
    hit = prev[prev["date"].astype(str) == str(new["date"])]
    if hit.empty:
        return False
    old = hit.iloc[-1]
    if not os.path.exists(os.path.join(args.dir, str(old["file"]))):
        return False
    old_diff = abs(float(old["turnover"]) - exp)
    new_diff = abs(float(new["turnover"]) - exp)
    print(f"{new['date']} is already archived. NEPSE turnover Rs {exp:,.2f} — "
          f"archived sheet is off by Rs {old_diff:,.2f}, "
          f"the new one by Rs {new_diff:,.2f}.")
    return old_diff <= new_diff


# ────────────────────────────────────────────────────────────────────────────
def cmd_add(args) -> int:
    os.makedirs(args.dir, exist_ok=True)
    if _keep_closest(args):
        print("The archived sheet is closer to NEPSE's turnover — keeping it.")
        return 0
    dest = os.path.join(args.dir, os.path.basename(args.file))
    if os.path.abspath(args.file) != os.path.abspath(dest):
        shutil.copy2(args.file, dest)
    print(f"Archived {os.path.basename(dest)} "
          f"({os.path.getsize(dest) / 1e6:.2f} MB)")

    # Incremental: only the new file is opened, the rest come from the manifest.
    prev = (pd.read_csv(args.manifest) if os.path.exists(args.manifest)
            else pd.DataFrame(columns=MANIFEST_COLS))
    rows = pd.concat([prev, pd.DataFrame([summarise(dest)])], ignore_index=True)

    if args.keep and args.keep > 0:
        keep = set(sorted(rows["date"].unique(), reverse=True)[:args.keep])
        for f in list_archive(args.dir):
            if _date_of(f) not in keep:
                os.remove(os.path.join(args.dir, f))
                print(f"Pruned {f}")
        rows = rows[rows["date"].isin(keep)]

    # Drop manifest entries whose file has gone missing
    present = {f for f in list_archive(args.dir)}
    rows = rows[rows["file"].isin(present)]

    write_manifest(rows, args.manifest)
    m = pd.read_csv(args.manifest)
    if args.readme:
        write_readme(m, args.readme, args.repo)
    print(f"Manifest: {args.manifest} — {len(m)} session(s), "
          f"{m['bytes'].sum() / 1e6:,.1f} MB total")
    return 0


def cmd_manifest(args) -> int:
    """Rebuild the manifest from scratch by reading every file."""
    files = list_archive(args.dir)
    rows = [summarise(os.path.join(args.dir, f)) for f in files]
    m = pd.DataFrame(rows, columns=MANIFEST_COLS)
    write_manifest(m, args.manifest)
    if args.readme:
        write_readme(pd.read_csv(args.manifest), args.readme, args.repo)
    print(f"Rebuilt manifest from {len(files)} file(s) -> {args.manifest}")
    return 0


def cmd_panel(args) -> int:
    files = list_archive(args.dir)
    sel = [f for f in files
           if (not args.start or _date_of(f) >= args.start)
           and (not args.end or _date_of(f) <= args.end)]
    if not sel:
        print("No sessions in that range.", file=sys.stderr)
        return 1

    frames = []
    for f in sel:
        d = fv.load_floorsheet(os.path.join(args.dir, f))
        d["date"] = fv.derive_trade_date(d) or _date_of(f)
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    print(f"{len(sel)} session(s), {len(df):,} contracts, "
          f"{fv.npr(df['amount'].sum())}")

    if args.by == "broker":
        b = df.groupby(["date", "buyer_l"])["amount"].sum().rename("buy")
        s = df.groupby(["date", "seller_l"])["amount"].sum().rename("sell")
        b.index.names = s.index.names = ["date", "broker"]
        out = pd.concat([b, s], axis=1).fillna(0.0)
        out["gross"] = out["buy"] + out["sell"]
        out["net"] = out["buy"] - out["sell"]
        out = out.reset_index()
    elif args.by == "scrip":
        g = df.groupby(["date", "symbol"])
        out = g.agg(turnover=("amount", "sum"), volume=("qty", "sum"),
                    trades=("amount", "size"), high=("rate", "max"),
                    low=("rate", "min"), last=("rate", "last")).reset_index()
        out["vwap"] = out["turnover"] / out["volume"]
    else:
        out = df.rename(columns=fv.OUTCOLS)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    if args.out.lower().endswith((".parquet", ".pq")):
        out.to_parquet(args.out, index=False, compression="zstd")
    else:
        out.to_csv(args.out, index=False)
    print(f"Wrote {args.out} ({len(out):,} rows, "
          f"{os.path.getsize(args.out) / 1e6:.2f} MB)")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Floor sheet parquet archive")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="add one session and update the manifest")
    a.add_argument("--file", required=True)
    a.add_argument("--dir", default="archive/parquet")
    a.add_argument("--manifest", default="archive/manifest.csv")
    a.add_argument("--readme", default=None)
    a.add_argument("--repo", default=None, help="clone URL shown in the readme")
    a.add_argument("--keep", type=int, default=0,
                   help="sessions to retain, 0 = keep everything")
    a.add_argument("--expected-turnover", type=float, default=None,
                   help="NEPSE's own turnover for the session; on a re-scrape "
                        "the sheet closest to it wins")
    a.set_defaults(fn=cmd_add)

    m = sub.add_parser("manifest", help="rebuild the manifest from the files")
    m.add_argument("--dir", default="archive/parquet")
    m.add_argument("--manifest", default="archive/manifest.csv")
    m.add_argument("--readme", default=None)
    m.add_argument("--repo", default=None)
    m.set_defaults(fn=cmd_manifest)

    g = sub.add_parser("ingest", help="bulk-convert a folder of csv dumps")
    g.add_argument("--src", required=True,
                   help="folder or glob, e.g. '/d/analysis/Floorsheet'")
    g.add_argument("--dir", default="archive/parquet")
    g.add_argument("--manifest", default="archive/manifest.csv")
    g.add_argument("--readme", default=None)
    g.add_argument("--repo", default=None)
    g.add_argument("--recursive", action="store_true",
                   help="also search sub-folders")
    g.add_argument("--overwrite", action="store_true",
                   help="replace sessions already in the archive")
    g.add_argument("--dry-run", action="store_true",
                   help="report what would happen, write nothing")
    g.set_defaults(fn=cmd_ingest)

    p = sub.add_parser("panel", help="concatenate a date range")
    p.add_argument("--dir", default="archive/parquet")
    p.add_argument("--from", dest="start", default=None)
    p.add_argument("--to", dest="end", default=None)
    p.add_argument("--by", choices=["contract", "broker", "scrip"],
                   default="contract")
    p.add_argument("--out", default="panel.parquet")
    p.set_defaults(fn=cmd_panel)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
