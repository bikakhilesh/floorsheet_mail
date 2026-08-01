/* _fund_asserts.js — the Fundamentals tab against the real snapshot.
 * Concatenated after _stubs.js and fundamentals_view.FUND_JS by run.sh.
 *
 * The load-bearing assertion is the round trip: priced at the vendor's own
 * close, every live multiple must equal the vendor's own ratio. That is what
 * proves scaling preserves whatever earnings and book basis npstocks used —
 * including the different book basis it applies to insurers, where recomputing
 * from the Bookvalue column would be off by tens of percent.
 */

const _fs = require('fs');
const _path = require('path');

FUND = JSON.parse(_fs.readFileSync(
  _path.join(__dirname, '..', 'build', 'fundamentals.json'), 'utf8'));
FUNDI = {}; FUND.cols.forEach((c, i) => { FUNDI[c] = i; });

let _f = 0;
function ok(c, label, extra) {
  if (c) console.log('  ok   ' + label);
  else { _f++; console.log('  FAIL ' + label + (extra ? '  ' + extra : '')); }
}
const close = (a, b, tol) => Math.abs(a - b) < (tol === undefined ? 1e-9 : tol);

console.log('\nsnapshot');
ok(FUND.n === 282, 'all 282 companies in the payload', 'got ' + FUND.n);
ok((FUND.unmatched || []).length === 0, 'nothing unmatched',
   JSON.stringify((FUND.unmatched || []).slice(0, 5)));
ok(FUND.asof === '2026-07-30', 'as-of parsed from the filename',
   'got ' + FUND.asof);
ok(!!fund('NABIL') && fund('NABIL').epsD > 0, 'a known symbol resolves');
ok(fund('ZZZZ') === null, 'an unknown symbol returns null, not a partial row');

/* Price every scrip at the vendor's own close and the multiples must come back
   exactly as the vendor stated them. */
console.log('\nround trip at the vendor close');
const syms = Object.keys(FUND.sym);
SCRIPS = syms.map(s => {
  const f = fund(s);
  return { sym: s, turnover: 5e7, vwap: f.close, last: f.close,
           sector: 'Test', volume: 1, trades: 1 };
});
FDOPT.price = 'vwap'; FDOPT.minT = 0;

let peBad = 0, pbBad = 0, mcBad = 0, insurers = 0, worstShare = 0;
SCRIPS.forEach(s => {
  const r = fundRow(s), f = fund(s.sym);
  if (!r) return;
  if (f.peD != null && !close(r.peLive, f.peD, 1e-9)) peBad++;
  if (f.pbv != null && !close(r.pbvLive, f.pbv, 1e-9)) pbBad++;
  // `shares` is an integer in the payload, because a share count is. The
  // vendor's market cap is itself rounded, so mcap/close carries float dust
  // and the round trip can only be exact to within one share's value. That is
  // the honest bound — asserting equality here would be asserting that a
  // rounded input is exact.
  if (f.mcap != null && f.shares != null) {
    const err = Math.abs(r.mcapLive - f.mcap) / f.close;   // in shares
    if (err > worstShare) worstShare = err;
    if (err > 1.0) mcBad++;
  }
  // rows where price/Bookvalue disagrees with the vendor P/B by >1%
  if (f.pbv != null && f.bvps > 0 &&
      Math.abs(f.close / f.bvps - f.pbv) / f.pbv > 0.01) insurers++;
});
ok(peBad === 0, 'live P/E reproduces the vendor P/E exactly', peBad + ' off');
ok(pbBad === 0, 'live P/B reproduces the vendor P/B exactly', pbBad + ' off');
ok(mcBad === 0,
   'live market cap round-trips to within one share (worst: '
   + worstShare.toFixed(3) + ' shares)', mcBad + ' beyond a full share');
ok(insurers > 20,
   'and ' + insurers + ' rows would have been wrong if P/B were recomputed '
   + 'from Bookvalue — which is the whole reason for scaling');

console.log('\nre-pricing');
const probe = SCRIPS.find(s => fund(s.sym).peD > 0 && fund(s.sym).pbv > 0);
const base = fundRow(probe);
probe.vwap = probe.vwap * 2;
const dbl = fundRow(probe);
ok(close(dbl.peLive, base.peLive * 2, 1e-9), 'doubling the price doubles P/E');
ok(close(dbl.pbvLive, base.pbvLive * 2, 1e-9), 'and doubles P/B');
ok(close(dbl.mcapLive, base.mcapLive * 2, 1e-6), 'and doubles market cap');
ok(close(dbl.drift, 100, 1e-9), 'drift against the vendor close reads +100%',
   'got ' + dbl.drift);
probe.vwap = probe.vwap / 2;

FDOPT.price = 'close';
ok(close(fundRow(probe).drift, 0, 1e-12),
   'the vendor-close basis re-prices nothing');
FDOPT.price = 'vwap';

console.log('\nsector aggregation');
const rows = [
  { sym: 'A', sector: 'Banks', mcapLive: 100, peLive: 10, pbvLive: 1, roeTTM: 10 },
  { sym: 'B', sector: 'Banks', mcapLive: 300, peLive: 30, pbvLive: 3, roeTTM: 30 },
  { sym: 'C', sector: 'Banks', mcapLive: 100, peLive: null, pbvLive: null,
    roeTTM: null },                                   // loss-maker
  { sym: 'D', sector: 'Hydro', mcapLive: 50, peLive: 20, pbvLive: 2, roeTTM: 5 },
];
const agg = fundSectorAgg(rows);
const banks = agg.find(r => r.g === 'Banks');
ok(banks.n === 3, 'the loss-maker still counts as a traded name');
ok(close(banks.mcap, 500), 'and still counts in market cap', 'got ' + banks.mcap);
ok(close(banks.pe, (100 * 10 + 300 * 30) / 400), 'weighted P/E excludes it',
   'got ' + banks.pe);
ok(close(banks.peMed, 20), 'median P/E is of the two priced names',
   'got ' + banks.peMed);
ok(close(banks.roe, (100 * 10 + 300 * 30) / 400), 'weighted ROE matches',
   'got ' + banks.roe);
ok(agg[0].g === 'Banks', 'sorted by market cap');

console.log('\nrendering');
SCRIPS = syms.slice(0, 40).map(s => {
  const f = fund(s);
  return { sym: s, turnover: 5e7, vwap: f.close * 1.03, last: f.close,
           sector: 'Commercial Banks', volume: 100, trades: 5 };
});
SCRIPS.push({ sym: 'NOFUND', turnover: 5e7, vwap: 100, last: 100,
              sector: 'Hydropower', volume: 1, trades: 1 });
$('#fdQ').value = ''; $('#fdPrice').value = 'vwap'; $('#fdMinT').value = '10';
try { renderFund(); ok(true, 'renderFund runs'); }
catch (e) { ok(false, 'renderFund runs', e.message + ' | ' + e.stack.split('\n')[1]); }
ok($('#tFund').innerHTML === '40 rows', 'the unmapped name is left out of the table',
   'got ' + $('#tFund').innerHTML);
ok($('#fdStale').innerHTML.indexOf('NOFUND') > 0,
   'and is named in the coverage banner instead of vanishing');
ok($('#fdStale').innerHTML.indexOf('2026-07-30') > 0, 'the as-of date is shown');

console.log('\nbubble charts');
/* OLS against a line it must recover exactly, then against known scatter. */
const exact = [];
for (let i = 1; i <= 20; i++) exact.push({ x: i, y: 3 * i + 5 });
const fe = ols(exact);
ok(close(fe.b, 3, 1e-9) && close(fe.a, 5, 1e-9), 'OLS recovers a clean line',
   'got b=' + fe.b + ' a=' + fe.a);
ok(close(fe.r2, 1, 1e-9), 'and R² is 1 on a perfect fit', 'got ' + fe.r2);
ok(ols([{ x: 1, y: 1 }]) === null, 'OLS declines fewer than three points');
ok(ols([{ x: 2, y: 1 }, { x: 2, y: 5 }, { x: 2, y: 9 }]) === null,
   'and declines a vertical column rather than dividing by zero');
const noisy = [{ x: 1, y: 2 }, { x: 2, y: 3 }, { x: 3, y: 5 }, { x: 4, y: 4 }];
const fn = ols(noisy);
ok(fn.r2 > 0 && fn.r2 < 1, 'R² is strictly between 0 and 1 on real scatter',
   'got ' + fn.r2);

ok(close(pctl([1, 2, 3, 4, 5], 0), 1) && close(pctl([1, 2, 3, 4, 5], 1), 5),
   'percentile bounds hit the ends');
ok(pctl([], 0.5) === null, 'and an empty series has none');

/* The identity the P/B vs P/E rays encode: P/B / P/E == ROE. */
const idPts = [{ x: 10, y: 1.5, r: 1, label: 'A' }, { x: 20, y: 3.0, r: 1, label: 'B' }];
ok(close(idPts[0].y / idPts[0].x, 0.15) && close(idPts[1].y / idPts[1].x, 0.15),
   'both sample names sit on the same 15% ROE ray');

const c1 = bubbleChart(
  syms.slice(0, 60).map(s => {
    const f = fund(s);
    return { x: f.peD, y: f.pbv, r: f.mcap, label: s, color: '#0B2545',
             tip: s };
  }).filter(p => p.x > 0 && p.y > 0),
  { log: true, iso: [0.10, 0.20], isoLabel: k => (100 * k) + '%', labelN: 5,
    title: 'T', xlab: 'P/E', ylab: 'P/B' });
ok(c1.svg.indexOf('<svg') === 0, 'the chart renders an svg');
ok((c1.svg.match(/<circle/g) || []).length === c1.n, 'one bubble per name',
   'got ' + (c1.svg.match(/<circle/g) || []).length + ' for ' + c1.n);
ok((c1.svg.match(/class="iso"/g) || []).length === 2, 'both iso rays drawn');
ok(c1.svg.indexOf('class="ols"') < 0, 'and no fit line when none was asked for');
ok((c1.svg.match(/class="plab"/g) || []).length === 5, 'top 5 labelled');

const c2 = bubbleChart(
  [{ x: 1, y: 2, r: 1, label: 'A' }, { x: 2, y: 4, r: 1, label: 'B' },
   { x: 3, y: 6, r: 1, label: 'C' }, { x: 4, y: 8, r: 1, label: 'D' }],
  { log: false, fit: 'ols', labelN: 0, title: 'T' });
ok(c2.fit !== null && close(c2.fit.b, 2, 1e-9), 'linear fit slope is right',
   c2.fit && 'got ' + c2.fit.b);
ok(c2.svg.indexOf('class="ols"') > 0, 'and the fit line is drawn');

/* A single wild outlier must not be dropped, and must not set the scale. */
const withOutlier = [];
for (let i = 1; i <= 30; i++) withOutlier.push({ x: 10 + i * 0.1, y: 2, r: 1,
                                                 label: 'N' + i });
withOutlier.push({ x: 4000, y: 2, r: 1, label: 'WILD' });
const c3 = bubbleChart(withOutlier, { log: false, labelN: 0 });
ok(c3.off >= 1, 'the outlier is counted as off-scale', 'off=' + c3.off);
ok((c3.svg.match(/<circle/g) || []).length === 31,
   'but still drawn, pinned to the edge rather than hidden');

/* Padding a log domain additively walks the low end to ~0 and every tick then
   prints as 0.00. Caught by eye on the real data; asserted here so it stays
   caught. */
const logged = [];
for (let i = 0; i < 40; i++) logged.push({ x: 5 + i, y: 1 + i * 0.05, r: 1,
                                           label: 'N' + i });
const c4 = bubbleChart(logged, { log: true, labelN: 0 });
const zeroTicks = (c4.svg.match(/>0\.00</g) || []).length;
ok(zeroTicks === 0, 'log axes do not degenerate to 0.00 ticks',
   zeroTicks + ' zero ticks');
// A 2-98% clip on 40 evenly spaced points necessarily puts the extreme one or
// two outside the domain — that is the trimming doing its job, not a fault.
// What matters is that a well-behaved series loses a handful, not a third.
ok(c4.off <= 2, 'and a clean series pins at most the extremes',
   'off=' + c4.off + ' of ' + c4.n);

ok(bubbleChart([], {}).n === 0, 'an empty series degrades to a message');
ok(bubbleChart([{ x: -1, y: 5, r: 1, label: 'A' }, { x: -2, y: 6, r: 1, label: 'B' },
                { x: -3, y: 7, r: 1, label: 'C' }], { log: true }).n === 0,
   'log axes drop non-positive values rather than producing NaN geometry');

$('#fdFit').value = 'iso'; $('#fdScale').value = 'log'; $('#fdLabel').value = '20';
try { renderFundCharts(fundRows()); ok(true, 'renderFundCharts runs'); }
catch (e) { ok(false, 'renderFundCharts runs',
                 e.message + ' | ' + e.stack.split('\n')[1]); }
ok($('#fdPEPB').innerHTML.indexOf('<svg') === 0, 'P/B vs P/E drew');
ok($('#fdROE').innerHTML.indexOf('<svg') === 0, 'ROE vs ROA drew');
$('#fdFit').value = 'ols';
renderFundCharts(fundRows());
ok($('#fdPEPBFit').innerHTML.indexOf('R²') > 0, 'the OLS mode reports R²',
   $('#fdPEPBFit').innerHTML);

FUND = null;
try { renderFund(); ok($('#fdStale').innerHTML.indexOf('No fundamentals') > 0,
                       'a missing snapshot says so rather than throwing'); }
catch (e) { ok(false, 'a missing snapshot says so rather than throwing', e.message); }

console.log('\n' + (_f ? _f + ' FAILURE(S)' : 'all assertions passed'));
process.exit(_f ? 1 : 0);
