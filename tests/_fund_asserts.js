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

FUND = null;
try { renderFund(); ok($('#fdStale').innerHTML.indexOf('No fundamentals') > 0,
                       'a missing snapshot says so rather than throwing'); }
catch (e) { ok(false, 'a missing snapshot says so rather than throwing', e.message); }

console.log('\n' + (_f ? _f + ' FAILURE(S)' : 'all assertions passed'));
process.exit(_f ? 1 : 0);
