/* _stubs.js — everything the Sectors tab expects the dashboard to have already
 * defined, plus a synthetic six-session archive.
 *
 * Concatenated ahead of sector_view.SECTOR_JS by run.sh so the tab, the stubs
 * and the assertions share one lexical scope. eval would not do: `let` inside
 * eval never escapes the eval, so the tab's own state would be invisible.
 */

const CR = 1e7, LAKH = 1e5;

const _els = {};
function _fakeEl() {
  return {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
    classList: { contains: () => false, add() {}, remove() {}, toggle() {} },
    querySelectorAll: () => [], addEventListener() {}, appendChild() {},
    insertAdjacentHTML() {},
  };
}
const $ = s => (_els[s] = _els[s] || _fakeEl());

function npr(x, pre) {
  pre = pre === undefined ? 'Rs ' : pre;
  if (x === null || x === undefined || isNaN(x)) return '-';
  const s = x < 0 ? '-' : '', a = Math.abs(x);
  if (a >= CR) return s + pre + (a / CR).toFixed(2) + ' Cr';
  if (a >= LAKH) return s + pre + (a / LAKH).toFixed(2) + ' L';
  return s + pre + Math.round(a);
}
const num = x => String(Math.round(x));
const pct = x => x.toFixed(1) + '%';
const bname = c => 'B-' + c;

function barList(el, items) { el.innerHTML = items.length + ' bars'; return items.length; }

/* Calls every formatter and class function, which is where the typos hide. */
function makeTable(id, cols, getData) {
  return function draw() {
    const d = getData();
    d.forEach(r => cols.forEach(c => { c.f(r); if (c.cls) c.cls(r); }));
    $('#' + id).innerHTML = d.length + ' rows';
    return d.length;
  };
}
function lineChart(labels, vals) { return '<svg>' + vals.length + '</svg>'; }
function showTip() {} function hideTip() {}
function openBroker() {} function openScrip() {}
const drawer = { classList: { add() {}, remove() {} }, scrollTop: 0 };

let EMB = null, IDX = null, DAY = null, RANGE = null;

/* ---------- synthetic archive ---------- */
const DATES = ['2026-07-01', '2026-07-02', '2026-07-03',
               '2026-07-04', '2026-07-05', '2026-07-06'];
//              turnover in lakh                      vwap
const SP = {
  AAA: [[100, 120, 110, 130, 140, 150], [200, 204, 210, 205, 209, 213]],
  BBB: [[50, 60, 55, 70, 65, 80],       [400, 396, 400, 408, 404, 412]],
  CCC: [[80, 90, 100, 95, 105, 110],    [100, 103, 101, 104, 106, 108]],
  DDD: [[10, 12, 11, 13, 14, 15],       [1000, 1000, 1000, 1000, 1000, 1000]],
  EEE: [[20, 22, 21, 23, 24, 25],       [50, 60, 55, 57, 58, 59]],  // +20% on d2
  FFF: [[0.5, 0.6, 0.4, 0.5, 0.5, 0.6], [90, 92, 94, 96, 98, 100]], // under 1 lakh
  // AAA's promoter line: same sector, own instrument, priced at a discount the
  // way NEPSE promoter shares actually trade
  AAAP: [[8, 9, 7, 10, 11, 12],         [120, 122, 126, 123, 125, 128]],
};
let PANEL = { dates: DATES, brokers: {}, scrips: SP };
let DATE = DATES[5];
async function ensurePanel() { return PANEL; }

const SECTORS_JSON = {
  generated: 'test', n: 7,
  sectors: ['Commercial Banks', 'Hydropower'],
  groups: ['Commercial Banks', 'Debenture', 'Hydropower'],
  instruments: ['Equity', 'Non-Convertible Debentures', 'Promoter Share'],
  statuses: ['Active'],
  sym: {
    AAA: [0, 0, 0, 0], BBB: [0, 0, 0, 0],
    CCC: [1, 2, 0, 0], EEE: [1, 2, 0, 0], FFF: [1, 2, 0, 0],
    DDD: [0, 1, 1, 0],                       // bank-issued debenture
    AAAP: [0, 0, 2, 0],                      // promoter: bank group, own instrument
  },
  name: { AAA: 'Alpha Bank', BBB: 'Beta Bank', CCC: 'Chandi Power',
          DDD: 'Alpha Debenture', EEE: 'Everest Power', FFF: 'Fewa Power',
          AAAP: 'Alpha Bank Promoter Share' },
};

let SCRIPS = Object.keys(SP).map(sym => {
  const t = SP[sym][0][5] * LAKH, v = SP[sym][1][5];
  return { sym: sym, turnover: t, volume: Math.round(t / v), trades: 40,
           vwap: v, high: v * 1.02, low: v * 0.98,
           last: sym === 'BBB' ? v * 0.99 : v * 1.01,   // BBB closes under VWAP
           rangePct: 4, nBuy: 9, nSell: 8, hhi: 900 };
});
let BROKERS = [
  { code: 1, buy: 5e6, sell: 3e6, gross: 8e6, net: 2e6, trades: 40, share: 10,
    crossPct: 1, avgTicket: 2e5 },
  { code: 2, buy: 2e6, sell: 6e6, gross: 8e6, net: -4e6, trades: 30, share: 9,
    crossPct: 0, avgTicket: 2.6e5 }];
let BSCRIP = [{ broker: 1, sym: 'AAA', buy: 4e6, sell: 1e6 },
              { broker: 1, sym: 'CCC', buy: 1e6, sell: 2e6 },
              { broker: 2, sym: 'AAA', buy: 1e6, sell: 4e6 },
              { broker: 2, sym: 'DDD', buy: 1e6, sell: 2e6 }];
