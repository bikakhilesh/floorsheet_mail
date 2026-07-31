#!/usr/bin/env python3
"""
interactive_report.py — build a single-file interactive floor sheet dashboard.

Everything (data + CSS + JS) is inlined, with no CDN calls, so the file works
offline, opens straight from a mail attachment, and can be served from GitHub
Pages unchanged.

    python interactive_report.py --csv data/2026.07.30.csv --out docs/reports

Or from another script:

    import floorsheet_viz as fv, interactive_report as ir
    a = fv.build_analytics(fv.load_floorsheet(csv), "2026-07-30")
    ir.build_interactive(a, "report.html")

Note on email: mail clients strip <script>, so this file cannot be *inlined* as
a mail body and stay interactive. Attach it and/or link the Pages copy — see
mail_floorsheet.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
import pandas as pd

import floorsheet_viz as fv

# Trim the payload: broker×scrip cells below this gross value are dropped from
# the drill-down (they are noise and they dominate the file size).
MIN_PAIR_GROSS = 1e5
MAX_BLOCKS = 300
MAX_PAIRS = 1200


# ────────────────────────────────────────────────────────────────────────────
# Payload
# ────────────────────────────────────────────────────────────────────────────
def _py(x):
    """numpy scalars are not JSON-serialisable — unwrap to native Python."""
    return x.item() if hasattr(x, "item") else x


def build_payload(a: fv.Analytics) -> dict:
    br = a.broker.reset_index()
    brokers = [
        [int(r.broker), round(r.buy_amt), round(r.sell_amt), round(r.gross),
         round(r.net), int(r.trades), round(r.share_pct, 2),
         round(r.cross_pct, 1), round(r.avg_ticket)]
        for r in br.itertuples()
    ]

    sc = a.scrip.reset_index()
    scrips = [
        [r.symbol, round(r.turnover), int(r.volume), int(r.trades),
         round(r.vwap, 2), round(r.high, 2), round(r.low, 2), round(r.last, 2),
         round(r.range_pct, 2), int(r.n_buyers), int(r.n_sellers),
         int(round(r.buy_hhi))]
        for r in sc.itertuples()
    ]

    bs = a.net_pos[a.net_pos["gross"] >= MIN_PAIR_GROSS]
    bscrip = [[int(r.broker), r.symbol, round(r.buy), round(r.sell)]
              for r in bs.itertuples()]

    pr = a.pairs.head(MAX_PAIRS)
    pairs = [[int(r.buyer_l), int(r.seller_l), round(r.amount), int(r.trades)]
             for r in pr.itertuples()]

    bk = a.blocks.head(MAX_BLOCKS)
    blocks = [[r.symbol, int(r.buyer_l), int(r.seller_l), int(r.qty),
               round(r.rate, 2), round(r.amount), int(bool(r.cross))]
              for r in bk.itertuples()]

    k = {kk: _py(round(vv, 4) if isinstance(vv, (float, np.floating)) else vv)
         for kk, vv in a.kpi.items()}

    return {
        "date": a.date,
        "kpi": k,
        "names": {str(c): n for c, n in fv.BROKER_NAMES.items()},
        "cols": {
            "brokers": ["code", "buy", "sell", "gross", "net", "trades",
                        "share", "crossPct", "avgTicket"],
            "scrips": ["sym", "turnover", "volume", "trades", "vwap", "high",
                       "low", "last", "rangePct", "nBuy", "nSell", "hhi"],
            "bscrip": ["broker", "sym", "buy", "sell"],
            "pairs": ["buyer", "seller", "amount", "trades"],
            "blocks": ["sym", "buyer", "seller", "qty", "rate", "amount", "cross"],
        },
        "brokers": brokers,
        "scrips": scrips,
        "bscrip": bscrip,
        "pairs": pairs,
        "blocks": blocks,
    }


# ────────────────────────────────────────────────────────────────────────────
# Template
# ────────────────────────────────────────────────────────────────────────────
TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEPSE Floor Sheet — __DATE__</title>
<style>
:root{
  --navy:#0B2545; --gold:#C9A227; --buy:#1B7F4C; --sell:#B02A2A;
  --ink:#1C2331; --grey:#8A94A6; --line:#E3E8F0; --bg:#F7F9FC; --light:#EEF1F6;
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
     Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{max-width:1280px;margin:0 auto;padding:0 18px 60px}
header{background:var(--navy);color:#fff;padding:18px 0 0}
header .wrap{padding-bottom:0}
h1{margin:0;font-size:20px;font-weight:700;letter-spacing:.2px}
.date{color:#9FB3C8;font-size:13px;margin-top:2px}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0 12px}
.kpi{flex:1 1 118px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);
     border-radius:5px;padding:8px 10px}
.kpi b{display:block;font-size:15px;color:#fff}
.kpi span{font-size:10px;color:#9FB3C8;text-transform:uppercase;letter-spacing:.5px}
nav{display:flex;gap:2px;margin-top:6px}
nav button{background:none;border:0;border-bottom:3px solid transparent;color:#9FB3C8;
  padding:10px 16px;font:600 13px inherit;cursor:pointer}
nav button:hover{color:#fff}
nav button.on{color:#fff;border-bottom-color:var(--gold)}
.panel{display:none;margin-top:20px}
.panel.on{display:block}
.card{background:#fff;border:1px solid var(--line);border-radius:6px;padding:14px 16px;
      margin-bottom:16px}
.card h2{margin:0 0 2px;font-size:14px;color:var(--navy);border-left:4px solid var(--gold);
         padding-left:8px}
.card p.note{margin:0 0 10px 12px;font-size:11.5px;color:var(--grey)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:880px){.grid2{grid-template-columns:1fr}}
.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
input[type=search],select{border:1px solid var(--line);border-radius:4px;padding:6px 9px;
  font:13px inherit;background:#fff;color:var(--ink)}
input[type=search]{min-width:210px}
.hint{font-size:11.5px;color:var(--grey)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{background:var(--navy);color:#fff;text-align:left;padding:7px 9px;font-weight:600;
   cursor:pointer;white-space:nowrap;position:sticky;top:0;z-index:2}
th.num,td.num{text-align:right}
th:after{content:"";margin-left:5px;opacity:.5}
th.asc:after{content:"\25B2"} th.desc:after{content:"\25BC"}
td{padding:5px 9px;border-bottom:1px solid var(--line)}
tbody tr:nth-child(even){background:#FAFBFD}
tbody tr.clickable{cursor:pointer}
tbody tr.clickable:hover{background:#EAF0FA}
tbody tr.sel{background:#E4EDFA !important;box-shadow:inset 3px 0 0 var(--gold)}
.pos{color:var(--buy);font-weight:600} .neg{color:var(--sell);font-weight:600}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10.5px;font-weight:600}
.tag.cross{background:#FBF1D2;color:#7A5F02} .tag.inter{background:var(--light);color:var(--grey)}
.scroll{max-height:460px;overflow:auto;border:1px solid var(--line);border-radius:4px}
.bars{width:100%}
.bar-row{display:grid;grid-template-columns:96px 1fr 108px;gap:8px;align-items:center;
         margin:3px 0;font-size:12px}
.bar-row .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-row .val{text-align:right;color:var(--grey);font-size:11.5px}
.track{background:var(--light);border-radius:2px;height:16px;position:relative;overflow:hidden}
.fill{position:absolute;top:0;height:100%;border-radius:2px}
.mid{position:absolute;top:0;bottom:0;width:1px;background:#C7CEDB}
.empty{color:var(--grey);font-size:12.5px;padding:14px 4px}
#drawer{position:fixed;top:0;right:0;width:min(520px,94vw);height:100%;background:#fff;
  border-left:1px solid var(--line);box-shadow:-8px 0 26px rgba(11,37,69,.13);
  transform:translateX(100%);transition:transform .18s ease;overflow:auto;z-index:40}
#drawer.on{transform:none}
#drawer .dhead{position:sticky;top:0;background:var(--navy);color:#fff;padding:12px 16px;
  display:flex;justify-content:space-between;align-items:center}
#drawer .dhead b{font-size:15px}
#drawer .dbody{padding:14px 16px}
#drawer button.x{background:none;border:0;color:#9FB3C8;font-size:22px;cursor:pointer;
  line-height:1}
.stat{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 12px}
.stat div{background:var(--light);border-radius:4px;padding:6px 10px;font-size:12px}
.stat b{display:block;color:var(--navy);font-size:13.5px}
h3.sec{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--grey);
       margin:16px 0 6px}
.hm{border-collapse:separate;border-spacing:1px;font-size:10px}
.hm td{padding:0;width:26px;height:22px;border:0;text-align:center;cursor:default}
.hm td.c{border-radius:2px}
.hm th{background:none;color:var(--ink);font-weight:600;font-size:10px;padding:2px;
       position:static;cursor:default}
.hm th.v{writing-mode:vertical-rl;transform:rotate(180deg);height:52px}
#tip{position:fixed;pointer-events:none;background:var(--navy);color:#fff;padding:6px 9px;
  border-radius:4px;font-size:11.5px;opacity:0;transition:opacity .1s;z-index:60;
  white-space:nowrap}
footer{color:var(--grey);font-size:11.5px;border-top:1px solid var(--line);
        margin-top:24px;padding-top:12px}
</style></head><body>

<header><div class="wrap">
  <h1>NEPSE Floor Sheet Analytics</h1>
  <div class="date">Trading day <b id="hdate"></b> · <span id="hsub"></span></div>
  <div class="kpis" id="kpis"></div>
  <nav id="tabs">
    <button data-t="overview" class="on">Overview</button>
    <button data-t="brokers">Brokers</button>
    <button data-t="scrips">Scrips</button>
    <button data-t="blocks">Block trades</button>
    <button data-t="flow">Flow matrix</button>
  </nav>
</div></header>

<div class="wrap">
  <div class="panel on" id="p-overview">
    <div class="grid2">
      <div class="card"><h2>Top scrips by turnover</h2>
        <p class="note">Click through to the Scrips tab for the broker split.</p>
        <div id="ovScrips" class="bars"></div></div>
      <div class="card"><h2>Top brokers — buy vs sell</h2>
        <p class="note">Green right = buy, red left = sell. Click a row for the drill-down.</p>
        <div id="ovBrokers" class="bars"></div></div>
    </div>
    <div class="grid2">
      <div class="card"><h2>Net broker flow</h2>
        <p class="note">Buy minus sell. House-level, not client-level.</p>
        <div id="ovNet" class="bars"></div></div>
      <div class="card"><h2>Session structure</h2>
        <p class="note">Concentration and ticket-size read.</p>
        <div id="ovStats"></div></div>
    </div>
  </div>

  <div class="panel" id="p-brokers"><div class="card">
    <h2>Broker activity</h2>
    <p class="note">Click any row to open the broker's scrip and counterparty breakdown.</p>
    <div class="toolbar">
      <input type="search" id="qBroker" placeholder="Filter broker code or name…">
      <select id="fBrokerSide">
        <option value="">All brokers</option>
        <option value="buy">Net buyers only</option>
        <option value="sell">Net sellers only</option>
      </select>
      <span class="hint" id="cBroker"></span>
    </div>
    <div class="scroll"><table id="tBroker"></table></div>
  </div></div>

  <div class="panel" id="p-scrips"><div class="card">
    <h2>Scrip activity</h2>
    <p class="note">Click any row for the broker buy/sell split in that scrip.</p>
    <div class="toolbar">
      <input type="search" id="qScrip" placeholder="Filter symbol…">
      <span class="hint" id="cScrip"></span>
    </div>
    <div class="scroll"><table id="tScrip"></table></div>
  </div></div>

  <div class="panel" id="p-blocks"><div class="card">
    <h2>Largest single transactions</h2>
    <p class="note">Top __NBLOCKS__ contracts by value. Cross = same broker on both legs.</p>
    <div class="toolbar">
      <input type="search" id="qBlock" placeholder="Filter symbol or broker code…">
      <select id="fBlockType">
        <option value="">All types</option>
        <option value="1">Cross trades only</option>
        <option value="0">Inter-broker only</option>
      </select>
      <span class="hint" id="cBlock"></span>
    </div>
    <div class="scroll"><table id="tBlock"></table></div>
  </div></div>

  <div class="panel" id="p-flow"><div class="card">
    <h2>Broker-to-broker flow matrix</h2>
    <p class="note">Rows sell, columns buy. Hover a cell for the value; click to open the
      selling broker. Diagonal = cross trades.</p>
    <div class="toolbar">
      <select id="fFlowN">
        <option value="15">Top 15 brokers</option>
        <option value="20" selected>Top 20 brokers</option>
        <option value="30">Top 30 brokers</option>
      </select>
      <span class="hint">Colour scale is logarithmic.</span>
    </div>
    <div style="overflow:auto"><table class="hm" id="tFlow"></table></div>
  </div></div>

  <footer id="foot"></footer>
</div>

<div id="drawer"><div class="dhead"><b id="dTitle"></b>
  <button class="x" onclick="closeDrawer()">&times;</button></div>
  <div class="dbody" id="dBody"></div></div>
<div id="tip"></div>

<script id="payload" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);
const CR = 1e7, LAKH = 1e5;

/* ---------- formatting ---------- */
function npr(x, pre){ pre = pre===undefined ? 'Rs ' : pre;
  if (x === null || isNaN(x)) return '—';
  const s = x < 0 ? '-' : '', a = Math.abs(x);
  if (a >= 100*CR) return s+pre+(a/(100*CR)).toFixed(2)+' Ar';
  if (a >= CR)     return s+pre+(a/CR).toFixed(2)+' Cr';
  if (a >= LAKH)   return s+pre+(a/LAKH).toFixed(2)+' L';
  return s+pre+Math.round(a).toLocaleString('en-IN');
}
const num = x => Math.round(x).toLocaleString('en-IN');
const pct = x => x.toFixed(1)+'%';
const bname = c => D.names[c] ? c+' · '+D.names[c] : 'B-'+c;

/* ---------- objectify rows ---------- */
function rows(key){ const cols = D.cols[key];
  return D[key].map(r => { const o = {}; cols.forEach((c,i)=>o[c]=r[i]); return o; }); }
const BROKERS = rows('brokers'), SCRIPS = rows('scrips'),
      BSCRIP = rows('bscrip'), PAIRS = rows('pairs'), BLOCKS = rows('blocks');
const BR_BY = {}; BROKERS.forEach(b => BR_BY[b.code] = b);
const SC_BY = {}; SCRIPS.forEach(s => SC_BY[s.sym] = s);

/* ---------- header ---------- */
const K = D.kpi;
document.getElementById('hdate').textContent = D.date;
document.getElementById('hsub').textContent =
  num(K.trades)+' trades · '+K.scrips+' scrips · '+K.brokers+' brokers';
document.getElementById('kpis').innerHTML = [
  ['Turnover', npr(K.turnover)], ['Volume', num(K.volume)+' sh'],
  ['Trades', num(K.trades)], ['Avg ticket', npr(K.avg_ticket)],
  ['Top-10 brokers', pct(K.top10_broker_pct)], ['Top-10 scrips', pct(K.top10_scrip_pct)],
  ['Cross trades', pct(K.cross_pct)], ['Broker HHI', num(K.broker_hhi)],
].map(([l,v]) => '<div class="kpi"><b>'+v+'</b><span>'+l+'</span></div>').join('');
document.getElementById('foot').innerHTML =
  'Broker net is a house-level proxy — the floor sheet does not disclose client identity, '+
  'and offsetting client orders net out inside a broker code. Cross trades ('+
  npr(K.cross_amt)+', '+pct(K.cross_pct)+' of turnover) are typically negotiated transfers; '+
  'screen them before reading flow. NEPSE publishes no trade timestamp, so "last" is the '+
  'final contract in sequence order, not a confirmed close.';

/* ---------- tabs ---------- */
document.getElementById('tabs').addEventListener('click', e => {
  const b = e.target.closest('button'); if (!b) return;
  document.querySelectorAll('#tabs button').forEach(x => x.classList.toggle('on', x===b));
  document.querySelectorAll('.panel').forEach(p =>
    p.classList.toggle('on', p.id === 'p'+'-'+b.dataset.t));
});

/* ---------- bar list ---------- */
function barList(el, items, opt){
  opt = opt || {};
  const max = Math.max(1, ...items.map(i => Math.abs(i.v)),
                          ...items.map(i => Math.abs(i.v2 || 0)));
  el.innerHTML = items.map(i => {
    let inner;
    if (opt.diverging){
      const l = 50*Math.abs(Math.min(i.v,0))/max, r = 50*Math.max(i.v,0)/max;
      inner = '<div class="mid" style="left:50%"></div>'+
        '<div class="fill" style="right:50%;width:'+l+'%;background:var(--sell)"></div>'+
        '<div class="fill" style="left:50%;width:'+r+'%;background:var(--buy)"></div>';
    } else if (opt.butterfly){
      const l = 50*Math.abs(i.v2)/max, r = 50*i.v/max;
      inner = '<div class="mid" style="left:50%"></div>'+
        '<div class="fill" style="right:50%;width:'+l+'%;background:var(--sell)"></div>'+
        '<div class="fill" style="left:50%;width:'+r+'%;background:var(--buy)"></div>';
    } else {
      inner = '<div class="fill" style="left:0;width:'+(100*i.v/max)+
              '%;background:'+(opt.color||'var(--navy)')+'"></div>';
    }
    return '<div class="bar-row"'+(i.click?' style="cursor:pointer" onclick="'+i.click+'"':'')+
      '><div class="lbl">'+i.k+'</div><div class="track">'+inner+
      '</div><div class="val">'+i.t+'</div></div>';
  }).join('') || '<div class="empty">No data.</div>';
}

/* ---------- overview ---------- */
barList(document.getElementById('ovScrips'),
  SCRIPS.slice(0,15).map(s => ({k:s.sym, v:s.turnover, t:npr(s.turnover),
    click:"openScrip('"+s.sym+"')"})), {color:'var(--navy)'});
barList(document.getElementById('ovBrokers'),
  BROKERS.slice(0,15).map(b => ({k:bname(b.code), v:b.buy, v2:b.sell,
    t:npr(b.gross), click:'openBroker('+b.code+')'})), {butterfly:true});
const netSorted = BROKERS.slice().sort((a,b)=>b.net-a.net);
barList(document.getElementById('ovNet'),
  netSorted.slice(0,8).concat(netSorted.slice(-8)).map(b => ({k:bname(b.code), v:b.net,
    t:npr(b.net), click:'openBroker('+b.code+')'})), {diverging:true});

const bigTix = BLOCKS.filter(b => b.amount >= CR).length;
document.getElementById('ovStats').innerHTML =
  '<div class="stat">'+
  [['Broker HHI', num(K.broker_hhi)], ['Scrip HHI', num(K.scrip_hhi)],
   ['Median ticket', npr(K.median_ticket)], ['Largest ticket', npr(K.max_ticket)],
   ['Cross trades', npr(K.cross_amt)], ['Tickets ≥ Rs 1 Cr', bigTix]]
   .map(([l,v]) => '<div>'+l+'<b>'+v+'</b></div>').join('')+'</div>'+
  '<h3 class="sec">Most concentrated buying (scrip buy-side HHI)</h3>';
const hhiDiv = document.createElement('div');
document.getElementById('ovStats').appendChild(hhiDiv);
barList(hhiDiv, SCRIPS.filter(s=>s.turnover>2e6).sort((a,b)=>b.hhi-a.hhi).slice(0,8)
  .map(s => ({k:s.sym, v:s.hhi, t:num(s.hhi)+' · '+npr(s.turnover),
    click:"openScrip('"+s.sym+"')"})), {color:'var(--gold)'});

/* ---------- sortable tables ---------- */
function makeTable(elId, cols, getData, onRow){
  const el = document.getElementById(elId);
  let sortKey = cols.find(c=>c.sort).k, sortDir = -1;
  function draw(){
    const data = getData().slice().sort((a,b) => {
      const x = a[sortKey], y = b[sortKey];
      return (typeof x === 'string' ? x.localeCompare(y) : x-y) * sortDir;
    });
    el.innerHTML =
      '<thead><tr>'+cols.map(c => '<th data-k="'+c.k+'" class="'+(c.num?'num ':'')+
        (c.k===sortKey ? (sortDir<0?'desc':'asc') : '')+'">'+c.h+'</th>').join('')+
      '</tr></thead><tbody>'+
      data.map(r => '<tr'+(onRow?' class="clickable" data-id="'+r[cols[0].k]+'"':'')+'>'+
        cols.map(c => '<td class="'+(c.num?'num ':'')+(c.cls?c.cls(r):'')+'">'+
          c.f(r)+'</td>').join('')+'</tr>').join('')+
      '</tbody>';
    el.querySelectorAll('th').forEach(th => th.onclick = () => {
      const k = th.dataset.k;
      if (k === sortKey) sortDir = -sortDir;
      else { sortKey = k; sortDir = (typeof getData()[0][k] === 'string') ? 1 : -1; }
      draw();
    });
    if (onRow) el.querySelectorAll('tbody tr').forEach(tr =>
      tr.onclick = () => onRow(tr.dataset.id));
    return data.length;
  }
  return draw;
}

/* brokers table */
const brokerCols = [
  {k:'code', h:'Broker', f:r=>bname(r.code)},
  {k:'buy', h:'Buy', num:1, f:r=>npr(r.buy,'')},
  {k:'sell', h:'Sell', num:1, f:r=>npr(r.sell,'')},
  {k:'gross', h:'Gross', num:1, sort:1, f:r=>npr(r.gross,'')},
  {k:'net', h:'Net', num:1, f:r=>npr(r.net,''), cls:r=>r.net>=0?'pos':'neg'},
  {k:'share', h:'Share %', num:1, f:r=>r.share.toFixed(2)},
  {k:'trades', h:'Trades', num:1, f:r=>num(r.trades)},
  {k:'crossPct', h:'Cross %', num:1, f:r=>r.crossPct.toFixed(1)},
  {k:'avgTicket', h:'Avg ticket', num:1, f:r=>npr(r.avgTicket,'')},
];
const drawBrokers = makeTable('tBroker', brokerCols, () => {
  const q = document.getElementById('qBroker').value.trim().toLowerCase();
  const side = document.getElementById('fBrokerSide').value;
  return BROKERS.filter(b =>
    (!q || bname(b.code).toLowerCase().includes(q)) &&
    (!side || (side==='buy' ? b.net > 0 : b.net < 0)));
}, code => openBroker(+code));
function refreshBrokers(){
  document.getElementById('cBroker').textContent = drawBrokers()+' brokers'; }
['qBroker','fBrokerSide'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshBrokers));
refreshBrokers();

/* scrips table */
const scripCols = [
  {k:'sym', h:'Scrip', f:r=>r.sym},
  {k:'turnover', h:'Turnover', num:1, sort:1, f:r=>npr(r.turnover,'')},
  {k:'volume', h:'Volume', num:1, f:r=>num(r.volume)},
  {k:'trades', h:'Trades', num:1, f:r=>num(r.trades)},
  {k:'vwap', h:'VWAP', num:1, f:r=>r.vwap.toFixed(1)},
  {k:'low', h:'Low', num:1, f:r=>r.low.toFixed(1)},
  {k:'high', h:'High', num:1, f:r=>r.high.toFixed(1)},
  {k:'last', h:'Last', num:1, f:r=>r.last.toFixed(1)},
  {k:'rangePct', h:'Range %', num:1, f:r=>r.rangePct.toFixed(1)},
  {k:'nBuy', h:'Buyers', num:1, f:r=>r.nBuy},
  {k:'nSell', h:'Sellers', num:1, f:r=>r.nSell},
  {k:'hhi', h:'Buy HHI', num:1, f:r=>num(r.hhi)},
];
const drawScrips = makeTable('tScrip', scripCols, () => {
  const q = document.getElementById('qScrip').value.trim().toUpperCase();
  return SCRIPS.filter(s => !q || s.sym.includes(q));
}, sym => openScrip(sym));
function refreshScrips(){
  document.getElementById('cScrip').textContent = drawScrips()+' scrips'; }
document.getElementById('qScrip').addEventListener('input', refreshScrips);
refreshScrips();

/* blocks table */
const blockCols = [
  {k:'sym', h:'Scrip', f:r=>r.sym},
  {k:'buyer', h:'Buyer', f:r=>bname(r.buyer)},
  {k:'seller', h:'Seller', f:r=>bname(r.seller)},
  {k:'qty', h:'Qty', num:1, f:r=>num(r.qty)},
  {k:'rate', h:'Rate', num:1, f:r=>r.rate.toFixed(1)},
  {k:'amount', h:'Value', num:1, sort:1, f:r=>npr(r.amount,'')},
  {k:'cross', h:'Type', f:r=>r.cross
     ? '<span class="tag cross">Cross</span>' : '<span class="tag inter">Inter-broker</span>'},
];
const drawBlocks = makeTable('tBlock', blockCols, () => {
  const q = document.getElementById('qBlock').value.trim().toLowerCase();
  const t = document.getElementById('fBlockType').value;
  return BLOCKS.filter(b =>
    (!q || b.sym.toLowerCase().includes(q) || String(b.buyer)===q || String(b.seller)===q ||
       bname(b.buyer).toLowerCase().includes(q) || bname(b.seller).toLowerCase().includes(q)) &&
    (t==='' || String(b.cross)===t));
});
function refreshBlocks(){
  document.getElementById('cBlock').textContent = drawBlocks()+' contracts'; }
['qBlock','fBlockType'].forEach(id =>
  document.getElementById(id).addEventListener('input', refreshBlocks));
refreshBlocks();

/* ---------- flow matrix ---------- */
const tip = document.getElementById('tip');
function showTip(e, html){ tip.innerHTML = html; tip.style.opacity = 1;
  tip.style.left = (e.clientX+14)+'px'; tip.style.top = (e.clientY+14)+'px'; }
function hideTip(){ tip.style.opacity = 0; }

function drawFlow(){
  const n = +document.getElementById('fFlowN').value;
  const top = BROKERS.slice(0,n).map(b=>b.code);
  const idx = {}; top.forEach((c,i)=>idx[c]=i);
  const m = top.map(()=>top.map(()=>0));
  PAIRS.forEach(p => { if (idx[p.buyer]!==undefined && idx[p.seller]!==undefined)
    m[idx[p.seller]][idx[p.buyer]] += p.amount; });
  const vals = m.flat().filter(v=>v>0);
  const lo = Math.log(Math.max(Math.min(...vals), 1e4)), hi = Math.log(Math.max(...vals));
  const shade = v => { if (!v) return '#fff';
    const t = Math.min(1, Math.max(0, (Math.log(v)-lo)/(hi-lo || 1)));
    return 'rgb('+Math.round(255-176*t)+','+Math.round(255-218*t)+','+Math.round(255-186*t)+')'; };
  let h = '<thead><tr><th></th>'+top.map(c=>'<th class="v">'+bname(c)+'</th>').join('')+
          '</tr></thead><tbody>';
  top.forEach((rc,i) => {
    h += '<tr><th>'+bname(rc)+'</th>';
    top.forEach((cc,j) => { const v = m[i][j];
      h += '<td class="c" style="background:'+shade(v)+'" data-v="'+v+'" data-s="'+rc+
           '" data-b="'+cc+'"></td>'; });
    h += '</tr>';
  });
  const el = document.getElementById('tFlow');
  el.innerHTML = h+'</tbody>';
  el.querySelectorAll('td.c').forEach(td => {
    td.onmousemove = e => showTip(e, '<b>'+bname(td.dataset.s)+'</b> sold '+
      npr(+td.dataset.v)+'<br>to <b>'+bname(td.dataset.b)+'</b>'+
      (td.dataset.s===td.dataset.b ? '<br><i>cross trade</i>' : ''));
    td.onmouseleave = hideTip;
    td.onclick = () => { hideTip(); openBroker(+td.dataset.s); };
  });
}
document.getElementById('fFlowN').addEventListener('change', drawFlow);
drawFlow();

/* ---------- drawer ---------- */
const drawer = document.getElementById('drawer');
function closeDrawer(){ drawer.classList.remove('on'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeDrawer(); });

function openBroker(code){
  const b = BR_BY[code]; if (!b) return;
  document.getElementById('dTitle').textContent = bname(code);
  const mine = BSCRIP.filter(r => r.broker === code)
    .map(r => ({sym:r.sym, net:r.buy-r.sell, gross:r.buy+r.sell}));
  const byNet = mine.slice().sort((x,y)=>y.net-x.net);
  const boughtFrom = PAIRS.filter(p => p.buyer === code)
    .sort((x,y)=>y.amount-x.amount).slice(0,8);
  const soldTo = PAIRS.filter(p => p.seller === code)
    .sort((x,y)=>y.amount-x.amount).slice(0,8);

  const body = document.getElementById('dBody');
  body.innerHTML = '<div class="stat">'+
    [['Buy', npr(b.buy)], ['Sell', npr(b.sell)], ['Net', npr(b.net)],
     ['Gross', npr(b.gross)], ['Share', b.share.toFixed(2)+'%'],
     ['Trades', num(b.trades)], ['Avg ticket', npr(b.avgTicket)],
     ['Cross', b.crossPct.toFixed(1)+'%']]
    .map(([l,v]) => '<div>'+l+'<b>'+v+'</b></div>').join('')+'</div>'+
    '<h3 class="sec">Net position by scrip</h3><div id="dNet"></div>'+
    '<h3 class="sec">Bought from</h3><div id="dFrom"></div>'+
    '<h3 class="sec">Sold to</h3><div id="dTo"></div>';
  barList(document.getElementById('dNet'),
    byNet.slice(0,7).concat(byNet.slice(-7)).filter((v,i,arr)=>arr.indexOf(v)===i)
      .map(r => ({k:r.sym, v:r.net, t:npr(r.net,''), click:"openScrip('"+r.sym+"')"})),
    {diverging:true});
  barList(document.getElementById('dFrom'),
    boughtFrom.map(p => ({k:bname(p.seller), v:p.amount,
      t:npr(p.amount,'')+' · '+p.trades, click:'openBroker('+p.seller+')'})),
    {color:'var(--buy)'});
  barList(document.getElementById('dTo'),
    soldTo.map(p => ({k:bname(p.buyer), v:p.amount,
      t:npr(p.amount,'')+' · '+p.trades, click:'openBroker('+p.buyer+')'})),
    {color:'var(--sell)'});
  drawer.classList.add('on'); drawer.scrollTop = 0;
}

function openScrip(sym){
  const s = SC_BY[sym]; if (!s) return;
  document.getElementById('dTitle').textContent = sym;
  const mine = BSCRIP.filter(r => r.sym === sym)
    .map(r => ({code:r.broker, buy:r.buy, sell:r.sell, net:r.buy-r.sell,
                gross:r.buy+r.sell}))
    .sort((x,y)=>y.gross-x.gross);
  const body = document.getElementById('dBody');
  body.innerHTML = '<div class="stat">'+
    [['Turnover', npr(s.turnover)], ['Volume', num(s.volume)],
     ['Trades', num(s.trades)], ['VWAP', s.vwap.toFixed(1)],
     ['Low–High', s.low.toFixed(1)+' – '+s.high.toFixed(1)],
     ['Range', s.rangePct.toFixed(1)+'%'], ['Last', s.last.toFixed(1)],
     ['Buy HHI', num(s.hhi)]]
    .map(([l,v]) => '<div>'+l+'<b>'+v+'</b></div>').join('')+'</div>'+
    '<h3 class="sec">Broker participation (buy right, sell left)</h3><div id="dPart"></div>'+
    '<h3 class="sec">Net position by broker</h3><div id="dSNet"></div>'+
    '<h3 class="sec">Largest contracts in this scrip</h3><div id="dSBlk"></div>';
  barList(document.getElementById('dPart'),
    mine.slice(0,14).map(r => ({k:bname(r.code), v:r.buy, v2:r.sell,
      t:npr(r.gross,''), click:'openBroker('+r.code+')'})), {butterfly:true});
  const byNet = mine.slice().sort((x,y)=>y.net-x.net);
  barList(document.getElementById('dSNet'),
    byNet.slice(0,6).concat(byNet.slice(-6)).filter((v,i,arr)=>arr.indexOf(v)===i)
      .map(r => ({k:bname(r.code), v:r.net, t:npr(r.net,''),
        click:'openBroker('+r.code+')'})), {diverging:true});
  const blk = BLOCKS.filter(b => b.sym === sym).slice(0,8);
  document.getElementById('dSBlk').innerHTML = blk.length
    ? '<table><thead><tr><th>Buyer</th><th>Seller</th><th class="num">Qty</th>'+
      '<th class="num">Rate</th><th class="num">Value</th></tr></thead><tbody>'+
      blk.map(b => '<tr><td>'+bname(b.buyer)+'</td><td>'+bname(b.seller)+
        '</td><td class="num">'+num(b.qty)+'</td><td class="num">'+b.rate.toFixed(1)+
        '</td><td class="num">'+npr(b.amount,'')+(b.cross?' <span class="tag cross">X</span>':'')+
        '</td></tr>').join('')+'</tbody></table>'
    : '<div class="empty">No contract in this scrip made the top '+BLOCKS.length+' by value.</div>';
  drawer.classList.add('on'); drawer.scrollTop = 0;
}
</script></body></html>
"""


def build_interactive(a: fv.Analytics, out_path: str) -> str:
    payload = build_payload(a)
    # </script> inside the JSON would terminate the block early
    data = json.dumps(payload, separators=(",", ":"),
                      default=_py).replace("</", "<\\/")
    html = (TEMPLATE
            .replace("__DATE__", a.date)
            .replace("__NBLOCKS__", str(len(payload["blocks"])))
            .replace("__DATA__", data))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return out_path


def build_index(reports_dir: str, index_path: str, site_title: str) -> str:
    """Regenerate the Pages landing page from whatever reports are on disk."""
    files = sorted((f for f in os.listdir(reports_dir)
                    if f.startswith("floorsheet_") and f.endswith(".html")),
                   reverse=True)
    rel = os.path.relpath(reports_dir, os.path.dirname(os.path.abspath(index_path)))
    items = "".join(
        f'<li><a href="{rel}/{f}">{f[11:-5]}</a></li>' for f in files
    ) or "<li>No reports published yet.</li>"
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{site_title}</title><style>
body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;
 background:#F7F9FC;color:#1C2331;margin:0}}
.w{{max-width:720px;margin:0 auto;padding:36px 20px}}
h1{{color:#0B2545;font-size:22px;margin:0 0 4px}}
p.s{{color:#8A94A6;font-size:13px;margin:0 0 22px}}
ul{{list-style:none;padding:0;margin:0}}
li{{border-bottom:1px solid #E3E8F0}}
li a{{display:block;padding:11px 6px;color:#0B2545;text-decoration:none;font-weight:600}}
li a:hover{{background:#EAF0FA}}
</style></head><body><div class="w">
<h1>{site_title}</h1>
<p class="s">Daily interactive floor sheet dashboards, most recent first.</p>
<ul>{items}</ul></div></body></html>"""
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(html)
    return index_path


def prune(reports_dir: str, keep: int) -> list[str]:
    files = sorted((f for f in os.listdir(reports_dir)
                    if f.startswith("floorsheet_") and f.endswith(".html")),
                   reverse=True)
    removed = []
    for f in files[keep:]:
        os.remove(os.path.join(reports_dir, f))
        removed.append(f)
    return removed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Interactive floor sheet dashboard")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", default="docs/reports", help="reports directory")
    ap.add_argument("--index", default=None, help="also write a Pages index here")
    ap.add_argument("--keep", type=int, default=40, help="reports to retain")
    ap.add_argument("--brokers", default=None)
    ap.add_argument("--date", default=None)
    args = ap.parse_args(argv)

    date_str = args.date or fv.infer_date(args.csv)
    df = fv.load_floorsheet(args.csv, args.brokers)
    a = fv.build_analytics(df, date_str)

    os.makedirs(args.out, exist_ok=True)
    path = build_interactive(a, os.path.join(args.out, f"floorsheet_{date_str}.html"))
    size = os.path.getsize(path) / 1024
    print(f"Interactive report: {path} ({size:,.0f} KB)")

    dropped = prune(args.out, args.keep)
    if dropped:
        print(f"Pruned {len(dropped)} old report(s), keeping {args.keep}.")
    if args.index:
        build_index(args.out, args.index, "NEPSE Floor Sheet Analytics")
        print(f"Index: {args.index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
