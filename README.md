# NEPSE floor sheet archive

Daily floor sheets as parquet, one file per trading session, written by the
`nepse-floorsheet` workflow. This branch holds data only — no code.

- Sessions: **113** (2026-02-16 to 2026-08-07)
- Total size: **105.4 MB**
- Layout: `parquet/floorsheet_YYYY-MM-DD.parquet`
- Index: [`manifest.csv`](manifest.csv)

Columns are the exchange's own: `Contract No.`, `Stock Symbol`, `Buyer`,
`Seller`, `Quantity`, `Rate (Rs)`, `Amount (Rs)`.

## Reading it

```python
import pandas as pd
df = pd.read_parquet("parquet/floorsheet_2026-08-07.parquet")
```

A date range as one frame, from the repo root on `main`:

```bash
python floorsheet_archive.py panel --dir archive/parquet \
    --from 2026-07-01 --to 2026-07-30 --out panel.parquet
```

Or broker-day / scrip-day aggregates:

```bash
python floorsheet_archive.py panel --dir archive/parquet --by broker --out brokers.csv
```

## Most recent sessions

| Date | Rows | Turnover | Scrips | Brokers | Size |
|---|---:|---:|---:|---:|---:|
| 2026-08-07 | 46,912 | Rs 377.44 Cr | 334 | 91 | 0.59 MB |
| 2026-08-06 | 60,381 | Rs 462.28 Cr | 347 | 91 | 0.72 MB |
| 2026-08-05 | 61,000 | Rs 440.16 Cr | 349 | 91 | 0.72 MB |
| 2026-08-04 | 52,272 | Rs 407.09 Cr | 343 | 91 | 0.65 MB |
| 2026-08-03 | 63,648 | Rs 457.21 Cr | 349 | 91 | 0.75 MB |
| 2026-07-31 | 46,752 | Rs 375.97 Cr | 337 | 91 | 0.59 MB |
| 2026-07-30 | 44,651 | Rs 338.13 Cr | 337 | 91 | 0.56 MB |
| 2026-07-29 | 48,071 | Rs 402.93 Cr | 343 | 90 | 0.60 MB |
| 2026-07-28 | 55,580 | Rs 442.73 Cr | 342 | 91 | 0.69 MB |
| 2026-07-27 | 69,803 | Rs 608.09 Cr | 360 | 91 | 0.87 MB |
| 2026-07-24 | 70,158 | Rs 626.72 Cr | 353 | 90 | 0.87 MB |
| 2026-07-23 | 57,782 | Rs 469.34 Cr | 338 | 91 | 0.71 MB |
| 2026-07-22 | 56,094 | Rs 518.75 Cr | 346 | 91 | 0.70 MB |
| 2026-07-21 | 75,010 | Rs 667.10 Cr | 343 | 91 | 0.94 MB |
| 2026-07-20 | 67,155 | Rs 551.27 Cr | 343 | 90 | 0.83 MB |

*This branch is force-pushed as a single orphan commit each run, so the repo
stores the current file set once rather than a new copy of history per day.
Clone it directly with `git clone --branch data --depth 1 https://github.com/bikakhilesh/floorsheet_mail.git`.*
