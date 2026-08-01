/* _asserts.js — checks the Sectors tab against numbers worked out separately.
 * Concatenated after _stubs.js and sector_view.SECTOR_JS by run.sh.
 */

SEC = SECTORS_JSON;

/* An independent implementation of the chain-linked index. If this and the
   dashboard agree, the dashboard is at least not disagreeing with itself. */
function refIndex(basis) {
  const grp = sym => {
    const r = SECTORS_JSON.sym[sym];
    if (basis === 'equity' && SECTORS_JSON.instruments[r[2]] !== 'Equity') return null;
    return SECTORS_JSON.groups[r[1]];
  };
  const acc = {};
  Object.keys(SP).forEach(sym => {
    const k = grp(sym); if (k === null) return;
    const t = SP[sym][0], v = SP[sym][1];
    for (let j = 1; j < DATES.length; j++) {
      if (t[j] < 1.0 || t[j - 1] < 1.0) continue;
      const r = v[j] / v[j - 1] - 1;
      if (Math.abs(r) > 0.10) continue;
      [k, '__MKT__'].forEach(x => {
        acc[x] = acc[x] || { w: Array(DATES.length).fill(0),
                             wr: Array(DATES.length).fill(0) };
        acc[x].w[j] += t[j]; acc[x].wr[j] += t[j] * r;
      });
    }
  });
  const out = {};
  Object.keys(acc).forEach(k => {
    let lvl = 100; const idx = [100];
    for (let j = 1; j < DATES.length; j++) {
      if (acc[k].w[j] > 0) lvl *= 1 + acc[k].wr[j] / acc[k].w[j];
      idx.push(lvl);
    }
    out[k] = idx;
  });
  return out;
}

let _fails = 0;
function ok(c, label, extra) {
  if (c) console.log('  ok   ' + label);
  else { _fails++; console.log('  FAIL ' + label + (extra ? '  ' + extra : '')); }
}
function close(a, b, tol) {
  return Math.abs(a - b) < (tol === undefined ? 1e-9 : tol);
}

console.log('\nbasis = equity only');
SECOPT.basis = 'equity'; SECPAN = null; SECRET = null;

const A = secAgg();
const bank = A.find(o => o.g === 'Commercial Banks');
const hyd = A.find(o => o.g === 'Hydropower');
ok(!A.find(o => o.g === 'Debenture'), 'debentures excluded on the equity basis');
ok(bank.n === 2, 'Commercial Banks has 2 traded names', 'got ' + bank.n);
ok(hyd.n === 3, 'Hydropower has 3 traded names', 'got ' + hyd.n);
ok(close(bank.turnover, (150 + 80) * LAKH),
   'the bank bucket does not absorb its own debenture', 'got ' + bank.turnover);
ok(close(bank.breadth, 50), 'bank breadth is 50% — BBB closed under VWAP',
   'got ' + bank.breadth);
ok(bank.listed === 2 && hyd.listed === 3, 'listed counts skip non-equity',
   'got ' + bank.listed + '/' + hyd.listed);
ok(close(bank.share + hyd.share, 100, 1e-6), 'shares sum to 100');
ok(close(bank.top3, 100), 'top-3 concentration is 100% with only two names');
ok(A[0].g === 'Commercial Banks', 'sorted by the ranking metric');

const P = secPanel();
ok(P.byG.Debenture === undefined, 'panel series exclude debentures');
ok(close(P.byG['Commercial Banks'][5], 150 + 80), 'bank panel turnover on d6',
   'got ' + P.byG['Commercial Banks'][5]);

const R = secReturns(), ref = refIndex('equity');
['Commercial Banks', 'Hydropower', '__MKT__'].forEach(k => {
  const a = R.g[k].idx, b = ref[k];
  ok(a.every((v, i) => close(v, b[i], 1e-9)), 'chain-linked index matches for ' + k,
     '\n        js  ' + a.map(v => v.toFixed(5)).join(' ') +
     '\n        ref ' + b.map(v => v.toFixed(5)).join(' '));
});
ok(R.dropped[1] === 1, 'the +20% EEE move on d2 dropped as a corporate action',
   'got ' + R.dropped[1]);
ok(R.dropped.reduce((a, b) => a + b, 0) === 1, 'nothing else was dropped');

const hydNoFFF = (function () {
  const t = SP.CCC[0], v = SP.CCC[1], tE = SP.EEE[0], vE = SP.EEE[1];
  let lvl = 100;
  for (let j = 1; j < DATES.length; j++) {
    let w = 0, wr = 0;
    const rC = v[j] / v[j - 1] - 1;
    if (Math.abs(rC) <= 0.10) { w += t[j]; wr += t[j] * rC; }
    const rE = vE[j] / vE[j - 1] - 1;
    if (Math.abs(rE) <= 0.10) { w += tE[j]; wr += tE[j] * rE; }
    if (w > 0) lvl *= 1 + wr / w;
  }
  return lvl;
})();
ok(close(R.g.Hydropower.idx[5], hydNoFFF, 1e-9),
   'the sub-1-lakh name never enters the sector return',
   'got ' + R.g.Hydropower.idx[5] + ' want ' + hydNoFFF);

const rb = ref['Commercial Banks'];
ok(close(secRet('Commercial Banks', 5, 1), rb[5] / rb[4] - 1), '1-session return');
ok(close(secRet('Commercial Banks', 5, 21), rb[5] / rb[0] - 1),
   'a lookback longer than the archive clamps to the first session');
ok(secRet('Commercial Banks', 0, 1) === null, 'no return before the first session');

const dr = secDrift();
ok(dr !== null, 'drift resolves for a single-day selection');
if (dr) {
  const cb = 150 + 80, ch = 110 + 25 + 0.6;
  const pb = 140 + 65, ph = 105 + 24 + 0.5;
  const want = 100 * cb / (cb + ch) - 100 * pb / (pb + ph);
  ok(close(dr['Commercial Banks'], want, 1e-9), 'share drift matches by hand',
     'got ' + dr['Commercial Banks'] + ' want ' + want);
  ok(close(Object.keys(dr).filter(k => k !== '__window__')
     .reduce((a, k) => a + dr[k], 0), 0, 1e-9), 'drifts sum to zero');
}

console.log('\nbasis = all instruments');
SECOPT.basis = 'all'; SECPAN = null; SECRET = null;
const A2 = secAgg();
ok(!!A2.find(o => o.g === 'Debenture'), 'debentures appear on the all basis');
ok(close(A2.find(o => o.g === 'Commercial Banks').turnover, (150 + 80) * LAKH),
   'the bank bucket is unchanged when debentures are shown separately');
ok(secReturns().g.Debenture.idx.every(v => close(v, 100)),
   'a flat-VWAP debenture indexes flat');

console.log('\nrendering');
SECOPT.basis = 'equity'; SECPAN = null; SECRET = null;
$('#secIdxWin').value = '21'; $('#secIdxN').value = '10';
$('#secBrokerN').value = '20';
$('#secBasis').value = 'equity'; $('#secMetric').value = 'turnover';
[['secDrawIndex', secDrawIndex],
 ['secDrawStack', secDrawStack],
 ['secDrawBrokerMatrix', secDrawBrokerMatrix],
 ['openSector', function () { openSector('Commercial Banks'); }],
 ['secFillScripFilter', function () {
    SCRIPS.forEach(function (s) { s.sector = secGroupAll(s.sym); });
    secFillScripFilter(); }]
].forEach(function (p) {
  try { p[1](); ok(true, p[0] + ' runs'); }
  catch (e) { ok(false, p[0] + ' runs', e.message + ' | ' + e.stack.split('\n')[1]); }
});

(async function () {
  try { await renderSectors(); ok(true, 'renderSectors runs'); }
  catch (e) { ok(false, 'renderSectors runs',
                  e.message + ' | ' + e.stack.split('\n')[1]); }

  ok($('#tSector').innerHTML === '2 rows', 'sector table drew 2 rows',
     'got ' + $('#tSector').innerHTML);
  ok($('#tSecRet').innerHTML === '2 rows', 'returns table drew 2 rows',
     'got ' + $('#tSecRet').innerHTML);
  ok($('#tSecBroker').innerHTML.indexOf('<tbody>') > 0, 'broker matrix drew a body');
  ok($('#secIdxChart').innerHTML.indexOf('<svg') === 0, 'index chart drew an svg');
  ok($('#secStack').innerHTML.indexOf('<svg') === 0, 'stacked area drew an svg');
  ok($('#secWarn').innerHTML === '', 'no unmapped warning when every symbol maps');
  ok($('#secIdxHint').textContent.indexOf('1 scrip-day move') > 0,
     'the dropped corporate action is disclosed in the hint',
     'got ' + $('#secIdxHint').textContent);

  ok(secGroupAll('DDD') === 'Debenture',
     'the scrips column shows the group whatever the basis');
  ok(secGroupAll('ZZZ') === 'Unmapped', 'an unknown symbol falls back to Unmapped');
  ok(secOf('ZZZ') === 'Unmapped', 'unknown symbols are bucketed, never dropped');

  /* an unmapped symbol has to raise the warning */
  SCRIPS.push({ sym: 'ZZZ', turnover: 5e6, volume: 100, trades: 3, vwap: 50,
                high: 51, low: 49, last: 50, rangePct: 4, nBuy: 2, nSell: 2, hhi: 0 });
  SECPAN = null; SECRET = null;
  await renderSectors();
  ok($('#secWarn').innerHTML.indexOf('ZZZ') > 0,
     'an unmapped traded symbol is named in the warning');

  console.log('\n' + (_fails ? _fails + ' FAILURE(S)' : 'all assertions passed'));
  process.exit(_fails ? 1 : 0);
})();
