# Sector map + fundamentals, and the two tabs they feed

Adds a symbol → sector map to `floorsheet_mail`, keeps it current from NEPSE's
listing table on its own schedule, and joins it to the floor sheet in the
dashboard.

Nothing in the existing daily chain changes shape. `nepse-scrape` and
`nepse-dashboard` keep running exactly as they do now; they just pick the map up
from the repo on checkout.

---

## Files

| File | What it is |
|---|---|
| `get_listed_securities.py` | new — scraper, canonicaliser, differ |
| `sector_map.py` | new — loads the csv, derives the analysis grouping, writes the browser payload |
| `sector_view.py` | new — css, markup and js for the Sectors tab |
| `apply_sector_patch.py` | new — wires `sector_view` into `dashboard_site.py` (19 edits) |
| `reference/listed_securities.csv` | new — the map, 641 securities, canonicalised from your `Listed.csv` |
| `.github/workflows/nepse-listed.yml` | new — daily, no mail, commits only on change |
| `fundamentals.py` | new — parses the npstocks dump, joins it to symbols, derives share counts |
| `fundamentals_scrape.py` | new — npstocks login and comparative-table extract |
| `fundamentals_view.py` | new — css, markup and js for the Fundamentals tab |
| `apply_fundamentals_patch.py` | new — wires it in (15 edits, run after the sector patch) |
| `reference/fundamentals.csv` | new — the snapshot, 282 companies |
| `reference/fundamentals_alias.csv` | new — 26 company names npstocks spells differently |
| `.github/workflows/nepse-fundamentals.yml` | new — 15:30 NPT, no mail, needs two secrets |
| `tests/` | new — four suites, 121 assertions |
| `dashboard_site.py` | patched twice |
| `requirements.txt` | unchanged — no new dependencies |

---

## Install

```bash
cd /d/path/to/floorsheet_mail        # repo root, on main

cp get_listed_securities.py sector_map.py sector_view.py apply_sector_patch.py .
mkdir -p reference .github/workflows tests
cp reference/listed_securities.csv reference/
cp .github/workflows/nepse-listed.yml .github/workflows/
cp tests/_stubs.js tests/_asserts.js tests/run.sh tests/

python apply_sector_patch.py --check      # confirms all 19 anchors resolve
python apply_sector_patch.py --dry-run    # read the diff
python apply_sector_patch.py              # applies, leaves dashboard_site.py.bak

bash tests/run.sh                         # 45 assertions on the tab's maths
python sector_map.py                      # sanity: the map as the dashboard sees it
git add -A && git commit -m "Sector map and Sectors tab" && git push
```

Then run **nepse-dashboard** by hand once (`skip_mail: true`) to publish a site
with `data/sectors.json` on it. From then on it is automatic.

If `--check` reports an anchor that does not resolve, `dashboard_site.py` has
moved since these were cut. Nothing is written in that case — send me the current
file and I will re-cut them.

---

## How the pieces fit

```
NEPSE /company        ─┐
NEPSE /promoter-share ─┴─nepse-listed (08:00 NPT daily)──▶  reference/listed_securities.csv
                                                              │  committed to main
                                                              │  only when a security changed
                                                              ▼
                              dashboard_site.py ──▶ site/data/sectors.json  (37 KB)
                                                              │
floor sheet parquet ──▶ site/data/day/<date>.json ────────────┤
                    ──▶ site/data/panel.json ─────────────────┤
                                                              ▼
                                                    joined in the browser
```

The join is deliberately client-side. A new listing changes one 37 KB file and
every session back to February re-maps on the next page load — no parquet is
reopened, no cached day file is invalidated, no rebuild of the archive. Baking
sector into the day payloads would mean a full recompute every time NEPSE
reclassifies anything.

---

## The workflow

`nepse-listed.yml`, daily at **02:15 UTC / 08:00 NPT**, plus `workflow_dispatch`.

Early morning on purpose: it is hours clear of the 16:00 NPT scrape chain, and
NEPSE adds a security to the listing table before its first trading day, so the
map normally leads the floor sheet rather than chasing it.

Two jobs:

**`listed`** — scrape both pages, canonicalise, diff against what is committed.

The two pages behave nothing alike. `/company` has filter dropdowns and a
page-size control, so it is walked as 15 instrument × status combinations.
`/promoter-share` has neither — it is ngx-pagination over 15 pages of 20.

Three things that walk has to get right, all learned the hard way:

- **Every DOM read is a single `execute_script`.** Locating an element and then
  reading or clicking it are two round trips, and the gap between them is exactly
  the moment the walk is waiting for — Angular swaps the tbody, the handle dies,
  `StaleElementReferenceException`. Query and act in one call and there is no
  handle to go stale.
- **Advancing is confirmed, not assumed.** The poll watches the first symbol
  change rather than sleeping a fixed interval, because a fixed sleep races the
  re-render into silently re-reading the page you were already on.
- **Every early exit raises.** A walk that gives up at page eleven still returns
  216 rows, clears the 150 floor, and commits a truncated register with the job
  green. The walk is retried from page one up to three times, since a mistimed
  re-render is uncorrelated between attempts.
- **Completeness is decided by the pager, not the next-link.** A missing
  next-link means "last page" *or* "control is mid-render and momentarily gone",
  and there is no way to tell from the reading itself. So the last page number is
  read before the walk starts — ngx-pagination always renders it — and the walk
  must cover that many pages or raise. The next-link is a timing observation; the
  page count is a fact about the register. A missing link is also re-checked
  eight times over two seconds before it is believed.

- **No change → nothing happens.** No commit, no rebuild, no mail. The job goes
  green and stops.
- **Change → one commit to `main`** carrying the csv and an appended
  `reference/listed_changelog.md`. `git log -p reference/` becomes your listing
  calendar.
- **Implausible scrape → nothing is written and the job fails red.** Gates: at
  least 300 rows from `/company` and 150 from the promoter register, at least 300
  combined, at least 95% of currently-active symbols must come back, at least 80%
  of all known symbols. The per-source floors matter: if the promoter page breaks
  while `/company` is fine, the combined count still looks plausible and only
  those catch it. A page that half-renders would otherwise blank the sector map
  for every dashboard reading the file. `--force` overrides.

**`publish`** — runs only when the change was *material*: a symbol added or gone,
or a sector, instrument or status that moved. A changed email address gets
committed but does not rebuild anything. It rebuilds and force-pushes `gh-pages`
and **sends no mail**. It shares the `floorsheet-dashboard` concurrency group
with `nepse-dashboard`, so the two can never force-push Pages at the same moment;
they queue.

To change the cadence, edit the one cron line. Weekly would be
`'15 2 * * 6'`.

---

## What the map does that the raw csv does not

**`SN` and the filter columns are dropped, rows are sorted by symbol.** NEPSE
renumbers `SN` on every request and the scraper walks instrument × status in a
fixed order, so a naive dump differs from yesterday's on a day nothing happened.
Stripped and sorted, `git diff` shows securities and nothing else.

**Duplicates resolve by authority, not by scrape order.** A name caught in both
the "Equity" and "All Instrument" passes keeps the row with the better status
(Active > Suspended > Delisted), then the more specific instrument.

**Non-equity gets its own bucket.** NEPSE files `SEF` (Siddhartha Equity Fund)
and `NICAD85/86` (a NIC Asia debenture) under **Commercial Banks**, because that
is who issued them. Summing the raw sector column books mutual-fund and debenture
flow into banking. `sector_map.py` derives a `group`: equities keep their sector,
everything else moves to `Debenture`, `Mutual Fund` or `Preference Share`. The
tab defaults to equity only; the basis selector shows the rest.

**Promoter shares keep the parent's sector but not its instrument.** `NABILP`
groups under Commercial Banks alongside `NABIL` — the turnover is money moving
through a bank's register either way. But it is restricted stock, locked in and
needing NRB approval to transfer for a BFI, and it trades at a wide discount to
the ordinary share, so counting it as free-float equity without saying so would
overstate participation. It carries `Instrument = "Promoter Share"` and the basis
selector decides. The sector table gains a **Promoter %** column whenever they
are included, because a sector that is a third promoter turnover is a sector
whose "flow" is mostly register transfers.

Note that nothing infers a parent from the symbol. It cannot: of the 289 promoter
lines, **146 end in `PO`, 140 in `P`, and 3 in neither** — `MBLPO` but `NABILP`,
`KBLPO` but `SHINEP`. NEPSE's promoter page states the sector outright, and that
is what gets used.

Totals: **930 securities, ~531 active — 278 active equities, 126 active promoter
lines, 79 debentures, 42 mutual funds, 6 preference shares.**

**Delisted names are kept.** They cost nothing and they are what lets you open a
2026 floor sheet in 2028 and still resolve a symbol that has since gone.

**`reference/sector_overrides.csv`** is optional and applied last. Same columns.
For the gap between a security starting to trade and NEPSE updating the table.

---

## The Sectors tab

Sits between Scrips and Block trades. Everything respects the day picker and the
timeline slicer, so a multi-session selection aggregates the same way the other
tabs do.

**Sector composition** — turnover, share, volume, trades, scrips traded against
scrips listed, participation, average ticket, top-3 concentration, and breadth
(share of the sector's traded names closing at or above their own VWAP). The
basis selector runs **Equity only** (default) / **Equity + promoter** / **All
instruments**, and a **Promoter %** column appears on the latter two.

**Where the money went / Rotation** — turnover share for the selection, and the
change in that share against the *preceding window of equal length*. A single day
compares to the day before; a 10-session selection compares to the 10 before it.
Drifts sum to zero by construction.

**Sector price performance** — this is the part that did not exist before.

There is no sector index in the floor sheet, but there is a VWAP for every scrip
every day. Daily scrip returns are turnover-weighted into a sector return and
chain-linked into an index rebased to 100. Construction notes, because they
change how you should read it:

- Only names that traded on **both** days enter a day's return, so the series is
  immune to mix shift when a scrip stops trading.
- A name must clear **Rs 1 lakh on both days** to count. Below that a single odd
  print moves the VWAP more than the market does.
- Moves beyond **±10%** are **dropped, not clipped**. NEPSE's daily band is 10%,
  so a VWAP-to-VWAP move past it is a bonus issue, a rights adjustment or a bad
  print — not a return. The count of dropped scrip-days is shown next to the
  chart, and a day that dropped twenty names is a day to read sceptically.
- The dashed dark line is the market on the same construction. The table gives
  1d / 5d / 21d / window returns and 21-day relative strength against it.

**Turnover share over time** — every session in the archive, normalised to 100%,
top nine sectors plus Other.

**Broker net flow by sector** — top brokers × sectors, green accumulation, red
distribution, square-root colour scale, click through to the broker drawer. Cells
come from the broker × scrip breakdown, which `interactive_report.MIN_PAIR_GROSS`
trims at Rs 1 lakh gross, so small positions are absent. Same house-level caveat
as everywhere else in this dashboard: client identity is not disclosed and
offsetting client orders net out inside a broker code.

**Sector drawer** — click any sector for constituents by turnover, net buyers and
sellers within it, and the chain-linked index across the whole archive.

**Scrips tab** gains a Sector column and a sector filter.

**Unmapped symbols** are bucketed as `Unmapped` and named in a banner rather than
being silently dropped. If that banner appears, either `nepse-listed` has not run
since the listing or the symbol needs an override row.

---

## Tests

```bash
bash tests/run.sh                    # both suites
python tests/test_promoter_walk.py   # the walk alone, no node needed
```

`test_promoter_walk.py` drives the pagination walk against a fake driver that
reproduces the two things that actually go wrong on that page: a tbody swapped
between a locate and a read, and a click that lands but never re-renders. The
invariant it holds is not "did it get rows" but "did it reach the end of the
register" — a truncated walk must raise, never return. It needs neither a
browser nor selenium installed.

`run.sh` extracts the tab's js out of `sector_view.py`, syntax-checks it, runs it
against a synthetic six-session archive containing a deliberate bonus issue, a
deliberately illiquid name and a promoter line priced at a discount to its
parent. 54 assertions: the chain-linked index is checked against an
independently written implementation on every basis, the corporate-action drop
and the liquidity floor are checked to actually exclude what they claim to,
promoter turnover is checked to land in the parent sector and to move the index
when included, share drifts are checked to sum to zero, and every render function
is called so a typo surfaces here rather than on Pages.

Bugs these have caught so far: a sector present in today's selection but absent
from `panel.json` crashed the stacked area chart; an empty
`sector_overrides.csv` raised rather than being ignored; and a truncated
promoter walk returned partial data through the success path where the row floor
could not see it.

The one that got to production first: a single false reading of the pagination
control ended a walk at page eleven and committed a 216-row register. The row
floor could not catch it — 216 is a perfectly plausible number. Only counting
pages against the pager catches that class of failure, which is why the test
asserts on pages covered rather than rows returned.

---

---

## Fundamentals

Absorbed out of `analysis-daily` so that repo can go away. The npstocks
comparative table is scraped directly here, keyed to NEPSE symbols, and priced
onto whichever session you have selected.

### Two secrets

Settings → Secrets and variables → Actions → New repository secret:

| Secret | Value |
|---|---|
| `NPSTOCKS_EMAIL` | your npstocks login |
| `NPSTOCKS_PASSWORD` | your npstocks password |

They reach the script through the environment only — never as arguments, so
they never land in a process listing, and nothing echoes them. This workflow
does not mail, so it needs no Gmail secrets.

### The join, which is the whole problem

The dump is keyed on company name; the floor sheet is keyed on symbol. Fuzzy
matching is not an option here — at a 0.80 cutoff it places **Sana Kisan onto
Gandaki, Swabalamban onto Salapa, National Microfinance onto NMB Microfinance**.
Six of fifteen candidates were a different institution. Nepali BFI names share
too much vocabulary for string distance to carry information.

So: exact normalised name, or a line in `reference/fundamentals_alias.csv`, or
reported unmatched. There is no third path, and `tests/test_fundamentals.py`
asserts that no similarity function exists anywhere in the module. The shipped
alias file has **26 entries and gets all 282 companies to a symbol**.

When npstocks renames something, it surfaces as unmatched in the scrape run's
summary — one line to add, and it fails visibly rather than silently dropping a
company.

### Live multiples scale the vendor's ratio

    peLive   = peD * (price / vendorClose)
    pbvLive  = pbv * (price / vendorClose)
    mcapLive = shares * price

Not recomputed from EPS and Bookvalue, and the reason matters. `PE (D)`
reproduces `Latest Close / EPS (D)` to about 5e-4 across the file — but `PBV`
misses `Latest Close / Bookvalue` by more than 1% on **27 names, nearly all
insurers**: IGI 1.24 against 2.39 computed, NLG 2.11 against 3.42, Nepal Life
6.94 against 5.95. Errors run both directions and the P/E on those same rows is
fine, so the close is not stale — npstocks is using a different book basis for
insurance than the Bookvalue column it shows, which is what you would expect if
it follows the NIA net-worth definition.

Recomputing would therefore hand you a quietly wrong P/B for the entire
insurance sector. Scaling preserves whatever basis the vendor used and moves
only the price; where the vendor field does reconcile the two are algebraically
identical. The test suite prices every scrip at the vendor's own close and
asserts the multiples come back exactly as stated.

`shares` comes from `Market Cap / Latest Close`, not paidup capital — paidup
over the Rs 100 par disagrees on **91 of 282 names** (0.952 shows up repeatedly,
a 5% bonus not yet in paidup).

### The tab

Sits after Sectors. Valuation table for every traded name with fundamentals —
price, drift against the vendor close, live market cap, live P/E and P/B, EPS,
BVPS, ROE, net margin, RSI. Price basis switches between session VWAP, last
contract, and vendor close (which re-prices nothing, for reconciliation).

Below it, **market-cap-weighted sector P/E, P/B and ROE with the median
alongside** — the weighted number says what the sector costs, the median says
whether one name is carrying it. Loss-makers are dropped from the ratios rather
than clamped: a loss-making name has no P/E, not a large one. Only names that
traded in the selection are included, so it values what actually changed hands.

The Scrips tab gains P/E and P/B columns on the same basis.

### The two bubble charts, and why they have no regression line by default

`P/B against P/E` and `ROE against ROA`, bubble area = live market cap, colour =
sector, click through to the scrip.

Both pairs are related by an identity rather than a correlation:

    ROE = ROA × equity multiplier
    P/B = P/E × ROE

So a ray from the origin is an exact iso-line — constant leverage on the first
chart, constant ROE on the second — and where a name sits between two rays *is*
its leverage, or its ROE. That is strictly more informative than a fitted line,
which would be describing scatter around an identity and reporting an R² that
means nothing. A commercial bank at 12x leverage and a hydro at 1.2x are not two
points on a trend; they are on different rays, and the chart says so directly.

Least squares is still available on the toggle, because the *residual* is a
legitimate relative-value read — which names are cheap against what their
returns justify. On log axes the fit is run in log space, so it reports a power
law (`P/B ∝ P/E^0.62`) rather than a slope, which is the right family for
multiples. R² is shown so you can see how little it is explaining.

Axes are trimmed to the 2nd–98th percentile because multiples are long-tailed —
one 400x P/E flattens everything else into a corner. Outliers are **pinned to
the edge at reduced opacity, not dropped**, and the count is stated under the
chart. On the shipped snapshot that is 12 of 224 on P/B-P/E and 7 of 213 on
ROE-ROA.

To see them without a browser: `node tests/_preview.js` after `tests/run.sh`
writes `build/pe_pb.svg` and `build/roe_roa.svg`.

Coverage gaps are shown, not hidden: names that traded above Rs 10 L with no
fundamentals are listed by symbol, and the snapshot's as-of date sits next to
the session you are viewing so staleness is visible.

### Before you delete analysis-daily

Two things in it were wrong and are worth knowing, since the same mistakes are
easy to reintroduce:

- **Its cron was `0 10 * * 0,1,2,3,4`** — Sunday to Thursday. NEPSE moved to
  Monday–Friday in April 2026, so it fired on Sundays when nothing traded and
  **never produced Friday's fundamentals at all**. The workflow here runs
  `45 9 * * 1,2,3,4,5`.
- **It scraped NEPSE's listed register with absolute XPaths**, which is what
  produced its `[PARTIAL]` fallbacks. That job is now `nepse-listed`, which
  reads the sector page and the promoter register with gates and a page-count
  check.

Also: the repo is public at the moment and its own README says not to be. Before
or after deleting it, check `nepse-github.zip` in its root — if that snapshot
predates moving credentials into Actions secrets, it is currently world-readable.

---

## Two things to decide

**The cadence.** Daily at 08:00 NPT is what is set. New listings in Nepal are
frequent enough that weekly would occasionally leave a symbol unmapped for a few
sessions, and the job is one page scrape. If the Actions minutes bother you,
`'15 2 * * 6'` makes it Saturdays.

**Whether Others should be split.** Nine active equities sit in NEPSE's "Others"
bucket, and they have nothing in common: NTC, NRIC, HRL, NRM, TTL, NWCL, MKCL and
two hydros (JHAPA, PURE) that were never reclassified. Aggregating them as a
sector is close to meaningless. `reference/sector_overrides.csv` fixes it without
touching any code — same columns, applied last. Moving JHAPA and PURE to
Hydropower is the obvious first entry.
