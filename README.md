# NEPSE Floor Sheet Analytics

Turns a daily NEPSE floor sheet CSV into a chart pack, summary tables, a
self-contained HTML report, and an email-ready body with inline images.

```
Contract No. | Stock Symbol | Buyer | Seller | Quantity | Rate (Rs) | Amount (Rs)
```

## Quick start

```bash
pip install -r requirements.txt

# charts + tables + HTML only
python floorsheet_viz.py --csv data/2026_07_30.csv --outdir out

# build and mail (env vars below); --dry-run writes out/message.eml instead
python mail_floorsheet.py --csv data/2026_07_30.csv --outdir out --dry-run
```

### Options

| Flag | Default | Notes |
|---|---|---|
| `--csv` | — | floor sheet CSV |
| `--outdir` | `out` | output root |
| `--top` | `20` | rows in the top-N charts |
| `--dpi` | `120` | 100 keeps the mail under ~2 MB; 150 for print |
| `--brokers` | — | CSV mapping `code,name`; labels become `58 · Naasa` |
| `--date` | inferred from filename | label override |

### Output tree

```
out/
├── charts/                 01–14 PNG exhibits
├── tables/                 broker_summary, scrip_summary, block_trades,
│                           broker_pair_flow, broker_scrip_net (CSV)
├── floorsheet_<date>.html  full report, images base64-embedded
├── email_body.html         compact body using cid: refs (~30 KB)
└── email_images.json       {cid: filepath} manifest for the mailer
```

## Exhibits

| # | Chart | Reads as |
|---|---|---|
| 01 | KPI banner | headline activity |
| 02 | Top scrips by turnover | where the money went |
| 03 | Broker butterfly (buy vs sell) | gross participation and side bias |
| 04 | Net broker flow | accumulators vs distributors |
| 05 | Largest single transactions | block prints, cross trades flagged |
| 06 | Broker→broker flow matrix | who traded with whom |
| 07 | Ticket-size distribution + Pareto | retail tape vs institutional blocks |
| 08 | Lorenz curves + HHI | concentration of brokers and scrips |
| 09 | Sequence-ordered turnover profile | session shape (**proxy**, see caveats) |
| 10 | Cross trades by broker | negotiated / inter-client transfers |
| 11 | Broker participation panels | top counterparties in the busiest names |
| 12 | Scrip map (turnover × VWAP × range) | where slippage risk sits |
| 13 | Intraday price dispersion vs VWAP | execution spread |
| 14 | Broker × scrip net position matrix | who accumulated what |

## Caveats worth keeping in the mail footer

- **Broker net ≠ client net.** The floor sheet does not disclose client identity.
  Offsetting client orders net out inside a broker code, and a large house net is
  as likely to be one institutional client as a directional view.
- **No timestamp.** NEPSE publishes no trade time. Contract numbers are monotonic
  within each matching stream (digits 9–10 of the contract number), so exhibit 09
  orders trades but does not place them on a clock. Read shape, not levels.
- **Cross trades inflate turnover.** Same-broker-both-legs prints are typically
  negotiated transfers. Screen them (exhibit 10) before reading net flow.
- **Amounts are recomputed** as qty × rate wherever the feed's amount disagrees
  by more than 0.5%, guarding against rounding in the source.

## Can the HTML go in the mail?

Two different questions, two different answers.

**The static report** — yes, and it already does. `email_body.html` is the mail
body: KPI strip, three tables, five inline PNGs via `cid:`, ~31 KB. Safe in
Gmail, Outlook and Apple Mail.

**The interactive dashboard** — not as a body. Every mail client strips
`<script>`, and Gmail additionally clips bodies above ~102 KB, so an inlined
dashboard would arrive as a dead husk. It is delivered two ways instead:

1. **Attached** (`dashboard_<date>.html`, ~0.3 MB). Opening the attachment
   hands it to the browser, where it is fully interactive. No network needed —
   data, CSS and JS are all inlined, no CDN.
2. **Linked** via `--pages-url`, which puts an "Open the interactive dashboard"
   button at the top of the body pointing at the GitHub Pages copy.

## Dashboards

Two different artefacts, because they have incompatible constraints.

**`dashboard_site.py` — the site.** One app over the whole archive with a day
picker: dropdown, calendar input, prev/next buttons, left/right arrow keys, and
`#YYYY-MM-DD` in the URL so a specific session is linkable. Published to
`gh-pages` each run.

```bash
python dashboard_site.py --archive archive/parquet --out site
```

### Single day or a range

The mode selector switches the whole app between one session and an aggregated
window. In range mode you get two date inputs plus `5d` / `1m` / `3m` / `All`
presets, and every tab then reports the aggregate: broker buy/sell/net summed
across sessions, scrip turnover and volume summed with VWAP recomputed from
them, high and low taken across the window, the flow matrix summed, and the
block-trade table gaining a Day column so you can see which session each print
came from. The URL carries `#2026-06-30..2026-07-30`, so a window is linkable.

Aggregating a month is the point of the archive: a broker's net over one
session is mostly noise, and over twenty it is a position. Medians are the one
figure that cannot be aggregated, so median ticket shows as `—` in range mode.

Only the sessions in the window are downloaded, roughly 0.2 MB each — a month
takes a few seconds, the full archive around 25. Ranges over 80 sessions ask
for confirmation first.

Tabs are Overview, Brokers, Scrips, Block trades, Flow matrix and **Trends**.
Trends works across sessions rather than within one: market turnover per
session as a clickable bar chart, per-broker daily/cumulative net flow, and
per-scrip turnover or VWAP. Opening a broker or scrip drawer on any day also
shows that name's full history, so a position can be read as built-over-time
rather than a single day's snapshot.

Only the selected day is downloaded — the app opens at the same speed whether
the archive holds ten sessions or a thousand. `panel.json` loads lazily the
first time Trends is opened.

| File | Size | Loaded |
|---|---|---|
| `index.html` | 31 KB | always |
| `data/index.json` | ~1 KB per session | always |
| `data/day/<date>.json` | ~200 KB | on demand |
| `data/panel.json` | ~3 KB per session | on first Trends open |

A rebuild reuses per-day JSON from the live site and only computes the new
session: 107 sessions cold takes about 70 seconds, warm about 1 second.

**`interactive_report.py` — the mail attachment.** A single self-contained file
for one day, everything inlined, no `fetch()`. That is what makes it work
opened straight from an attachment with no network. The site app cannot do this
because it fetches per-day JSON, and mail clients strip scripts anyway.

### Input formats

`--data` takes parquet, csv, or compressed csv — the format is picked off the
extension. Parquet and csv produce byte-identical reports; parquet is about 30%
of the size and round-trips dtypes, so `Contract No.` does not come back as a
float. `--to-parquet PATH` writes a parquet copy alongside the report.

### It always builds from the current session

The trading date is read out of the **contract numbers** (`YYYYMMDD` prefix),
not the filename, because that is the exchange's own numbering and a filename
can lie. `--probe` reports it without writing anything:

```
$ python interactive_report.py --data data/2026.07.30.csv --probe
date=2026-07-30
fresh=true
age_days=0
rows=44651
turnover=3381318248.02
```

`--require-fresh` exits 3 rather than writing a report when the sheet is not the
current Nepal-time session, and `--max-age-days N` widens that if you are
backfilling. The workflow gates the publish and mail steps on `fresh=true`, so a
market holiday, a cached page, or a scraper that silently returned the previous
session cannot republish yesterday's numbers as today's — the run passes and
skips instead. A filename that disagrees with the contract numbers is reported
as a warning, and the contract numbers win.

Single file, vanilla JS, no dependencies. Tabs: Overview, Brokers, Scrips,
Block trades, Flow matrix.

- Every table sorts on any column and filters as you type.
- Clicking a broker row opens a drawer: net position by scrip, who they bought
  from, who they sold to — each of those clickable in turn.
- Clicking a scrip opens the broker butterfly for that name, net position by
  broker, and the largest contracts in it.
- The flow matrix is a hoverable heatmap; the diagonal is cross trades.

Payload is aggregates, not raw trades: broker summary, scrip summary,
broker×scrip cells above Rs 1 lakh gross, the top 1,200 broker pairs and the
top 300 contracts. That is what keeps it a few hundred KB rather than several MB.

## Parquet archive

Every session is committed to the **`data` branch** as parquet, one file per
trading day, with a `manifest.csv` index and a generated `README.md`:

```
data branch
├── parquet/floorsheet_2026-07-30.parquet
├── manifest.csv        date, rows, turnover, volume, scrips, brokers, bytes
└── README.md           session count, span, size, recent sessions
```

Artifacts expire after 30 days, which is useless for a long series, so the
archive lives on a branch instead. `floorsheet_archive.py add` copies the file
in and appends one manifest row — it only opens the new file, so the step stays
constant-time as the archive grows.

### Backfilling an existing dump

One time only. The daily workflow appends after that — it never runs `ingest`.

```bash
cd ~/floorsheet_mail
bash backfill_archive.sh "/d/analysis/Floorsheet"           # convert, review
bash backfill_archive.sh "/d/analysis/Floorsheet" --push    # publish to data
```

The script builds the archive in `~/fs-archive-build`, outside the repo, so it
cannot commit to or force-push your code branch. It refuses to start if it is
not in the repo root, if the dump folder does not exist, or if no working Python
is on PATH — on Windows a bare `python` is often the Microsoft Store alias stub
that prints "Python was not found", so the script probes `py`, `python3` and
`python` and uses whichever actually runs.

Each file's session date comes from its **contract numbers**, so filenames need
no convention — `2026.07.30.csv`, `floorsheet_2026-07-30.csv` and `dump_003.csv`
all land correctly. `.csv`, `.csv.gz` and `.parquet` are read; `--recursive`
walks sub-folders.

A bad file is reported and skipped rather than killing the run. Two files
claiming the same session keep the one with more rows, which is what you want
when a dump holds both a partial and a complete capture of the same day.
Re-running skips sessions already archived unless you pass `--overwrite`, so it
is safe to repeat.

### Pulling data back out

```bash
git clone --branch data --depth 1 https://github.com/bikakhilesh/floorsheet_mail.git fs-data

# a date range as one frame
python floorsheet_archive.py panel --dir fs-data/parquet \
    --from 2026-07-01 --to 2026-07-30 --out panel.parquet

# broker-day: date, broker, buy, sell, gross, net
python floorsheet_archive.py panel --dir fs-data/parquet --by broker --out brokers.csv

# scrip-day: date, symbol, turnover, volume, trades, high, low, last, vwap
python floorsheet_archive.py panel --dir fs-data/parquet --by scrip --out scrips.csv
```

The broker-day and scrip-day frames are the ones worth keeping around — a
250-session year of broker-day is about 25,000 rows, small enough to hold in
memory and pivot however you like.

### Size

A session is roughly 0.6 MB as parquet against 2.0 MB as csv, so a 250-session
year costs about 150 MB. The branch is force-pushed as a single orphan commit,
so the repo stores the current file set once rather than accumulating a new copy
of history each day. `--keep N` caps the retained sessions if you ever want a
ceiling; the default keeps everything.

One cost to know about: the workflow shallow-clones the branch each run to
append to it, so once the archive is a year deep that step is pulling ~150 MB a
day. If that ever gets annoying, the fix is sharding by year and cloning only
the current shard.

## Mail integration

Env vars: `SMTP_HOST` (default `smtp.gmail.com`), `SMTP_PORT` (587),
`SMTP_USER`, `SMTP_PASS`, `MAIL_FROM`, `MAIL_TO` (comma-separated).

The message is nested `mixed → related → alternative`, in that order. Inline
images break silently if the `related` part is not the first child of `mixed`,
or the `alternative` part not the first child of `related`. The body stays near
30 KB because Gmail clips HTML bodies above ~102 KB — the full report rides as
an attachment instead.

## Wiring into floorsheet_mail

`.github/workflows/nepse-floorsheet.yml` replaces the existing workflow. It is a
single job, because the current scraper writes the CSV to `tempfile.gettempdir()`
and that directory is wiped when the job ends — nothing downstream can pick the
file up.

**One line to change in `floorsheet_scrape_and_mail.py`:**

```python
-OUTPUT_DIR = tempfile.gettempdir()
+OUTPUT_DIR = os.environ.get("OUTPUT_DIR", tempfile.gettempdir())
```

The workflow then sets `OUTPUT_DIR: ${{ github.workspace }}/data` and the rest
of the job reads from there. Local runs are unaffected.

Other changes folded into the workflow:

- **Cron moved to `15 10 * * 0,1,2,3,4`** — 16:00 NPT, Sunday–Thursday. The
  current `1,2,3,4,5` is Mon–Fri, which misses every Sunday session and burns a
  run every Friday, and 09:15 UTC fires at the bell before the sheet settles.
- **`MAIL_TO` is not passed to the scraper step.** Otherwise it sends its own
  plain-text mail and you get two a day. The scraper still exits nonzero on
  ERROR/MISMATCH, and a `if: failure()` step sends the alert instead.
- **`concurrency: floorsheet`** so a manual dispatch cannot collide with cron.
- `actions/checkout@v7` and `setup-python@v7` are correct — v7.0.0 shipped
  17 Jun 2026. (An earlier review of mine called those tags invalid. That was
  wrong.)

### GitHub Pages

The publish step clones the existing `gh-pages` branch, adds the new dashboard,
prunes to the last 40, regenerates `index.html`, then force-pushes a fresh
orphan commit. Orphaning matters: without it the branch accumulates ~0.3 MB of
history every trading day and the repo is a few hundred MB inside a year.

Enable it once under **Settings → Pages → Source: Deploy from a branch →
`gh-pages` / root**. The site lands at
`https://bikakhilesh.github.io/floorsheet_mail/`, and each mail links straight
to that day's report.

The alternative — `actions/upload-pages-artifact@v3` + `actions/deploy-pages@v4`
with Source set to GitHub Actions — replaces the whole site on every deploy, so
keeping an archive means committing the reports into the repo anyway. The branch
route is simpler here.
