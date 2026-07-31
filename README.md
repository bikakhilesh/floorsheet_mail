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

## Interactive dashboard

```bash
python interactive_report.py --csv data/2026.07.30.csv \
    --out site/reports --index site/index.html --keep 40
```

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
