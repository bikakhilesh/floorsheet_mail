/* _preview.js — render both bubble charts from the real snapshot to build/*.svg.
 * Concatenated after _stubs.js and FUND_JS, same as the assertions.
 * Priced at the vendor close, since there is no floor sheet in this harness.
 */
const _pfs = require('fs');
const _pp = require('path');
const _R = _pp.join(__dirname, '..');

FUND = JSON.parse(_pfs.readFileSync(_pp.join(_R, 'build', 'fundamentals.json'), 'utf8'));
FUNDI = {}; FUND.cols.forEach((c, i) => { FUNDI[c] = i; });
const SEC = JSON.parse(_pfs.readFileSync(_pp.join(_R, 'build', 'sec.json'), 'utf8'));

const PAL = ['#0B2545', '#C9A227', '#1B7F4C', '#B02A2A', '#3D6EA8', '#6B4C9A',
             '#2F8F9D', '#C2703D', '#4F6D2E', '#9A3B5C', '#7A5F02', '#5E8C6A'];
const groups = [...new Set(Object.values(SEC))].sort();
const col = g => PAL[Math.max(0, groups.indexOf(g)) % PAL.length];

const rows = Object.keys(FUND.sym).map(s => {
  const f = fund(s);
  return { sym: s, sector: SEC[s] || '—', peLive: f.peD, pbvLive: f.pbv,
           roeTTM: f.roeTTM, roaTTM: f.roaTTM, mcapLive: f.mcap };
});

const a = bubbleChart(
  rows.filter(r => r.peLive > 0 && r.pbvLive > 0).map(r => ({
    x: r.peLive, y: r.pbvLive, r: r.mcapLive || 0, label: r.sym,
    color: col(r.sector), tip: r.sym })),
  { log: true, iso: [0.05, 0.10, 0.15, 0.20, 0.30, 0.45],
    isoLabel: k => (100 * k).toFixed(0) + '% ROE', labelN: 14,
    title: 'P/B against P/E — dashed rays are constant ROE',
    xlab: 'P/E', ylab: 'P/B' });

const b = bubbleChart(
  rows.filter(r => r.roeTTM > 0 && r.roaTTM > 0).map(r => ({
    x: r.roaTTM, y: r.roeTTM, r: r.mcapLive || 0, label: r.sym,
    color: col(r.sector), tip: r.sym })),
  { log: true, iso: [1, 2, 4, 8, 12, 16], isoLabel: k => k + 'x', labelN: 14,
    title: 'ROE against ROA — dashed rays are constant leverage',
    xlab: 'ROA TTM %', ylab: 'ROE TTM %' });

_pfs.writeFileSync(_pp.join(_R, 'build', 'pe_pb.svg'), a.svg);
_pfs.writeFileSync(_pp.join(_R, 'build', 'roe_roa.svg'), b.svg);
console.log('P/B vs P/E : ' + a.n + ' names, ' + a.off + ' pinned to the edge');
console.log('ROE vs ROA : ' + b.n + ' names, ' + b.off + ' pinned to the edge');
console.log('sectors    : ' + groups.length);
