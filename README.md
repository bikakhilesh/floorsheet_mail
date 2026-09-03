# NEPSE floor sheet archive

Daily floor sheets as parquet, one file per trading session, written by the
`nepse-floorsheet` workflow. This branch holds data only — no code.

- Sessions: **131** (2026-02-16 to 2026-09-03)
- Total size: **116.7 MB**
- Layout: `parquet/floorsheet_YYYY-MM-DD.parquet`
- Index: [`manifest.csv`](manifest.csv)

Columns are the exchange's own: `Contract No.`, `Stock Symbol`, `Buyer`,
`Seller`, `Quantity`, `Rate (Rs)`, `Amount (Rs)`.

## Reading it

```python
import pandas as pd
df = pd.read_parquet("parquet/floorsheet_2026-09-03.parquet")
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
| 2026-09-03 | 40,500 | Rs 340.13 Cr | 345 | 90 | 0.52 MB |
| 2026-09-02 | 39,500 | Rs 293.84 Cr | 353 | 91 | 0.50 MB |
| 2026-09-01 | 48,341 | Rs 290.69 Cr | 341 | 90 | 0.60 MB |
| 2026-08-31 | 68,137 | Rs 364.86 Cr | 347 | 91 | 0.82 MB |
| 2026-08-27 | 52,575 | Rs 378.65 Cr | 348 | 91 | 0.67 MB |
| 2026-08-26 | 61,442 | Rs 477.39 Cr | 348 | 91 | 0.77 MB |
| 2026-08-25 | 46,589 | Rs 410.98 Cr | 357 | 91 | 0.58 MB |
| 2026-08-24 | 53,153 | Rs 323.98 Cr | 350 | 91 | 0.66 MB |
| 2026-08-21 | 59,805 | Rs 416.76 Cr | 344 | 90 | 0.72 MB |
| 2026-08-20 | 47,919 | Rs 440.14 Cr | 345 | 90 | 0.60 MB |
| 2026-08-19 | 42,709 | Rs 329.80 Cr | 346 | 89 | 0.54 MB |
| 2026-08-18 | 55,246 | Rs 507.35 Cr | 347 | 90 | 0.69 MB |
| 2026-08-17 | 56,500 | Rs 547.27 Cr | 350 | 91 | 0.69 MB |
| 2026-08-14 | 43,000 | Rs 426.26 Cr | 344 | 91 | 0.54 MB |
| 2026-08-13 | 45,500 | Rs 362.20 Cr | 345 | 91 | 0.57 MB |

*This branch is force-pushed as a single orphan commit each run, so the repo
stores the current file set once rather than a new copy of history per day.
Clone it directly with `git clone --branch data --depth 1 https://github.com/bikakhilesh/floorsheet_mail.git`.*
