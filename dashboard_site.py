#!/usr/bin/env python3
"""
dashboard_site.py — build a single interactive site over the whole parquet
archive, with a day picker and cross-day trends.

    python dashboard_site.py --archive archive/parquet --out site

Layout produced:

    site/
    ├── index.html              the app
    ├── data/index.json         available dates + per-day headline KPIs
    ├── data/panel.json         broker-day and scrip-day series (trends tab)
    └── data/day/<date>.json    per-day payload, fetched on demand

Only the selected day is downloaded, so the app opens fast no matter how deep
the archive goes. panel.json loads lazily when the Trends tab is first opened.

This is the Pages app. interactive_report.py still builds the self-contained
single-day file that goes out as the mail attachment — that one has to work
offline from an attachment, which rules out fetch().
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

import pandas as pd

import floorsheet_viz as fv
import interactive_report as ir
import fundamentals as fm
import fundamentals_view as fmv
import sector_map as sm
import sector_view as sv

L = 1e5  # lakh — panel series are stored in lakh to keep the file small


def _py(x):
    return x.item() if hasattr(x, "item") else x


def _sig(path: str) -> str:
    """Content signature of one archived session.

    The cache key cannot be "does the day file exist". A corrective re-scrape
    replaces floorsheet_<date>.parquet under the same name, and the cached json
    would then be published against data it no longer describes — the mail,
    which reads the parquet directly, and the site would disagree. mtime is no
    use either: the archive is a fresh shallow clone on every run, so every
    file looks newly written. Hash the bytes.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def build_site_data(archive: str, out: str, rebuild: bool = False) -> dict:
    files = sorted(f for f in os.listdir(archive)
                   if f.endswith((".parquet", ".pq")) and fv.filename_date(f))
    if not files:
        raise SystemExit(f"No parquet sessions in {archive}")

    ddir = os.path.join(out, "data", "day")
    os.makedirs(ddir, exist_ok=True)

    # Sidecar rather than a key inside each payload: the day files are served
    # to the browser and embedded in the mail attachment, and neither should
    # carry build metadata. Losing it just costs one full rebuild.
    sig_path = os.path.join(ddir, "_sigs.json")
    try:
        with open(sig_path, encoding="utf-8") as fh:
            sigs = json.load(fh)
    except (OSError, ValueError):
        sigs = {}

    dates, kpis = [], {}
    br_series: dict[str, dict[str, list]] = {}
    sc_series: dict[str, dict[str, list]] = {}

    for i, f in enumerate(files, 1):
        d = fv.filename_date(f)
        src = os.path.join(archive, f)
        day_path = os.path.join(ddir, f"{d}.json")
        cached = os.path.exists(day_path)
        sig = _sig(src)
        need = rebuild or not cached or sigs.get(d) != sig

        if need:
            # Only new or changed sessions touch parquet. Everything else is
            # rebuilt from the cached day file, which keeps the daily run O(1)
            # rather than O(archive) once the history is a year deep.
            df = fv.load_floorsheet(src)
            a = fv.build_analytics(df, d)
            payload = ir.build_payload(a)
            with open(day_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, separators=(",", ":"), default=_py)
            sigs[d] = sig
            note = f"{len(df):,} rows"
            if cached:
                note += " · replaced"
        else:
            with open(day_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            note = "cached"

        k = payload["kpi"]
        dates.append(d)
        kpis[d] = {
            "turnover": round(k["turnover"] / L, 1),
            "trades": int(k["trades"]),
            "volume": int(k["volume"]),
            "scrips": int(k["scrips"]),
            "brokers": int(k["brokers"]),
            "crossPct": round(float(k["cross_pct"]), 2),
            "top10Broker": round(float(k["top10_broker_pct"]), 1),
            "avgTicket": round(float(k["avg_ticket"])),
        }

        bcols = payload["cols"]["brokers"]
        ci, ni, gi = (bcols.index("code"), bcols.index("net"), bcols.index("gross"))
        for row in payload["brokers"]:
            br_series.setdefault(str(int(row[ci])), {})[d] = [
                round(row[ni] / L, 1), round(row[gi] / L, 1)]
        scols = payload["cols"]["scrips"]
        si, ti, vi = (scols.index("sym"), scols.index("turnover"), scols.index("vwap"))
        for row in payload["scrips"]:
            sc_series.setdefault(row[si], {})[d] = [
                round(row[ti] / L, 1), round(float(row[vi]), 2)]

        print(f"[{i}/{len(files)}] {d}  {note}  {fv.npr(k['turnover'])}")

    # Written only after every session is through, so a crash mid-build leaves
    # the old index in place and the affected days recompute on the next run.
    sigs = {d: s for d, s in sigs.items() if d in set(dates)}
    with open(sig_path, "w", encoding="utf-8") as fh:
        json.dump(sigs, fh, separators=(",", ":"), sort_keys=True)

    # Align the series to the date axis, null where a broker or scrip was absent
    def align(series: dict[str, dict[str, list]], n: int) -> dict:
        out_ = {}
        for key, per_day in series.items():
            cols = [[None] * len(dates) for _ in range(n)]
            for j, d in enumerate(dates):
                v = per_day.get(d)
                if v is not None:
                    for c in range(n):
                        cols[c][j] = v[c]
            out_[key] = cols
        return out_

    panel = {
        "dates": dates,
        "brokers": align(br_series, 2),   # [net, gross] in lakh
        "scrips": align(sc_series, 2),    # [turnover in lakh, vwap]
    }

    with open(os.path.join(out, "data", "index.json"), "w", encoding="utf-8") as fh:
        json.dump({"dates": dates, "kpi": kpis}, fh, separators=(",", ":"),
                  default=_py)
    with open(os.path.join(out, "data", "panel.json"), "w", encoding="utf-8") as fh:
        json.dump(panel, fh, separators=(",", ":"), default=_py)

    return {"dates": dates, "n": len(dates)}


APP = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NEPSE Floor Sheet Analytics</title>
<style>
:root{--navy:#0B2545;--gold:#C9A227;--buy:#1B7F4C;--sell:#B02A2A;--ink:#1C2331;
 --grey:#8A94A6;--line:#E3E8F0;--bg:#F7F9FC;--light:#EEF1F6}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,
 Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg)}
.wrap{max-width:1280px;margin:0 auto;padding:0 18px 60px}
header{background:var(--navy);color:#fff;padding:14px 0 0}
h1{margin:0;font-size:19px;font-weight:700}
.picker{display:flex;align-items:center;gap:8px;margin:10px 0 4px;flex-wrap:wrap}
.picker select,.picker input{font:600 13px inherit;padding:5px 8px;border-radius:4px;
 border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.10);color:#fff}
.picker select option{color:#000}
.picker button{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);
 color:#fff;border-radius:4px;padding:5px 11px;cursor:pointer;font:600 13px inherit}
.picker button:hover:not(:disabled){background:rgba(255,255,255,.22)}
.picker button:disabled{opacity:.35;cursor:default}
.picker .meta{color:#9FB3C8;font-size:12px}
.kpis{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
.kpi{flex:1 1 112px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.14);
 border-radius:5px;padding:7px 9px}
.kpi b{display:block;font-size:15px}
.kpi span{font-size:10px;color:#9FB3C8;text-transform:uppercase;letter-spacing:.5px}
.kpi i{font-style:normal;font-size:10.5px;margin-left:5px}
.up{color:#6FD39B}.down{color:#F09A9A}
nav{display:flex;gap:2px;margin-top:4px;overflow-x:auto}
nav button{background:none;border:0;border-bottom:3px solid transparent;color:#9FB3C8;
 padding:9px 15px;font:600 13px inherit;cursor:pointer;white-space:nowrap}
nav button:hover{color:#fff}
nav button.on{color:#fff;border-bottom-color:var(--gold)}
.panel{display:none;margin-top:18px}.panel.on{display:block}
.card{background:#fff;border:1px solid var(--line);border-radius:6px;padding:14px 16px;
 margin-bottom:16px}
.card h2{margin:0 0 2px;font-size:14px;color:var(--navy);border-left:4px solid var(--gold);
 padding-left:8px}
.card p.note{margin:0 0 10px 12px;font-size:11.5px;color:var(--grey)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:880px){.grid2{grid-template-columns:1fr}}
.toolbar{display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
input[type=search],select.f{border:1px solid var(--line);border-radius:4px;padding:6px 9px;
 font:13px inherit;background:#fff;color:var(--ink)}
input[type=search]{min-width:200px}
.hint{font-size:11.5px;color:var(--grey)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th{background:var(--navy);color:#fff;text-align:left;padding:7px 9px;font-weight:600;
 cursor:pointer;white-space:nowrap;position:sticky;top:0;z-index:2}
th.num,td.num{text-align:right}
th.asc:after{content:"\25B2";margin-left:5px;opacity:.6}
th.desc:after{content:"\25BC";margin-left:5px;opacity:.6}
td{padding:5px 9px;border-bottom:1px solid var(--line)}
tbody tr:nth-child(even){background:#FAFBFD}
tbody tr.clickable{cursor:pointer}
tbody tr.clickable:hover{background:#EAF0FA}
.pos{color:var(--buy);font-weight:600}.neg{color:var(--sell);font-weight:600}
.tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10.5px;font-weight:600}
.tag.cross{background:#FBF1D2;color:#7A5F02}.tag.inter{background:var(--light);color:var(--grey)}
.scroll{max-height:460px;overflow:auto;border:1px solid var(--line);border-radius:4px}
.bar-row{display:grid;grid-template-columns:92px 1fr 104px;gap:8px;align-items:center;
 margin:3px 0;font-size:12px}
.bar-row .lbl{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.bar-row .val{text-align:right;color:var(--grey);font-size:11.5px}
.track{background:var(--light);border-radius:2px;height:16px;position:relative;overflow:hidden}
.fill{position:absolute;top:0;height:100%;border-radius:2px}
.mid{position:absolute;top:0;bottom:0;width:1px;background:#C7CEDB}
.empty{color:var(--grey);font-size:12.5px;padding:14px 4px}
.stat{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 12px}
.stat div{background:var(--light);border-radius:4px;padding:6px 10px;font-size:12px}
.stat b{display:block;color:var(--navy);font-size:13.5px}
h3.sec{font-size:12px;text-transform:uppercase;letter-spacing:.5px;color:var(--grey);
 margin:16px 0 6px}
.hm{border-collapse:separate;border-spacing:1px;font-size:10px}
.hm td{padding:0;width:26px;height:22px;border:0}
.hm th{background:none;color:var(--ink);font-weight:600;font-size:10px;padding:2px;
 position:static;cursor:default}
.hm th.v{writing-mode:vertical-rl;transform:rotate(180deg);height:52px}
#drawer{position:fixed;top:0;right:0;width:min(520px,94vw);height:100%;background:#fff;
 border-left:1px solid var(--line);box-shadow:-8px 0 26px rgba(11,37,69,.13);
 transform:translateX(100%);transition:transform .18s;overflow:auto;z-index:40}
#drawer.on{transform:none}
#drawer .dhead{position:sticky;top:0;background:var(--navy);color:#fff;padding:12px 16px;
 display:flex;justify-content:space-between;align-items:center}
#drawer .dbody{padding:14px 16px}
#drawer button.x{background:none;border:0;color:#9FB3C8;font-size:22px;cursor:pointer}
#tip{position:fixed;pointer-events:none;background:var(--navy);color:#fff;padding:6px 9px;
 border-radius:4px;font-size:11.5px;opacity:0;z-index:60;white-space:nowrap}
#load{position:fixed;inset:0;background:rgba(247,249,252,.75);display:none;
 align-items:center;justify-content:center;z-index:70;font-weight:600;color:var(--navy)}
#load.on{display:flex}
#timeline{background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.14);
 border-radius:6px;padding:8px 10px 6px;margin:8px 0 4px}
.tlbar{display:flex;align-items:center;gap:10px;margin-bottom:6px;flex-wrap:wrap}
.tlbar b{font-size:13px}
.tlbar .sub{color:#9FB3C8;font-size:11.5px}
.tlbar select,.tlbar button{font:600 12px inherit;padding:3px 8px;border-radius:4px;
 border:1px solid rgba(255,255,255,.25);background:rgba(255,255,255,.10);color:#fff;
 cursor:pointer}
.tlbar select option{color:#000}
#tlTrack{position:relative;height:46px;cursor:crosshair;touch-action:none;
 border-bottom:1px solid rgba(255,255,255,.18)}
#tlTrack .bk{position:absolute;bottom:0;background:#8FA6BF;border-radius:1px 1px 0 0}
#tlTrack .bk.on{background:var(--gold)}
#tlSel{position:absolute;top:0;bottom:0;background:rgba(201,162,39,.16);
 border-left:2px solid var(--gold);border-right:2px solid var(--gold);pointer-events:none}
.tlh{position:absolute;top:0;bottom:0;width:11px;margin-left:-5px;cursor:ew-resize;
 z-index:3}
.tlh:after{content:"";position:absolute;left:3px;top:50%;margin-top:-9px;width:5px;
 height:18px;background:var(--gold);border-radius:2px}
#tlAxis{position:relative;height:14px;color:#9FB3C8;font-size:10px}
#tlAxis span{position:absolute;transform:translateX(-50%);white-space:nowrap}
svg.chart{width:100%;height:210px;display:block}
svg.chart .ax{stroke:#C7CEDB;stroke-width:1}
svg.chart .gl{stroke:#EDF1F7;stroke-width:1}
svg.chart text{font-size:10px;fill:var(--grey)}
footer{color:var(--grey);font-size:11.5px;border-top:1px solid var(--line);
 margin-top:24px;padding-top:12px}
__SECTOR_CSS__
__FUND_CSS__
</style></head><body>

<header><div class="wrap">
  <h1>NEPSE Floor Sheet Analytics</h1>
  <div class="picker">
    <span id="dayCtl">
      <button id="prev" title="Previous session">&lsaquo;</button>
      <select id="daySel"></select>
      <input type="date" id="dayDate">
      <button id="next" title="Next session">&rsaquo;</button>
      <button id="latest">Latest</button>
    </span>
    <span class="meta" id="meta"></span>
  </div>
  <div id="timeline">
    <div class="tlbar">
      <b id="tlLabel">—</b><span class="sub" id="tlSub"></span>
      <span style="flex:1"></span>
      <select id="tlLevel">
        <option value="day">Days</option>
        <option value="week">Weeks</option>
        <option value="month" selected>Months</option>
        <option value="quarter">Quarters</option>
      </select>
      <button id="tlAll">Select all</button>
    </div>
    <div id="tlTrack">
      <div id="tlSel"></div>
      <div class="tlh" id="tlH0"></div><div class="tlh" id="tlH1"></div>
    </div>
    <div id="tlAxis"></div>
  </div>
  <div class="kpis" id="kpis"></div>
  <nav id="tabs">
    <button data-t="overview" class="on">Overview</button>
    <button data-t="brokers">Brokers</button>
    <button data-t="scrips">Scrips</button>
    <button data-t="sectors">Sectors</button>
    <button data-t="fund">Fundamentals</button>
    <button data-t="blocks">Block trades</button>
    <button data-t="flow">Flow matrix</button>
    <button data-t="trends">Trends</button>
  </nav>
</div></header>

<div class="wrap">
  <div class="panel on" id="p-overview"><div class="grid2">
    <div class="card"><h2>Top scrips by turnover</h2>
      <p class="note">Click for the broker split in that name.</p><div id="ovScrips"></div></div>
    <div class="card"><h2>Top brokers — buy vs sell</h2>
      <p class="note">Green right buy, red left sell. Click for the drill-down.</p>
      <div id="ovBrokers"></div></div>
  </div><div class="grid2">
    <div class="card"><h2>Net broker flow</h2>
      <p class="note">Buy minus sell, house level.</p><div id="ovNet"></div></div>
    <div class="card"><h2>Session structure</h2><p class="note">Concentration and ticket size.</p>
      <div id="ovStats"></div></div>
  </div></div>

  <div class="panel" id="p-brokers"><div class="card">
    <h2>Broker activity</h2><p class="note">Click a row for scrips and counterparties.</p>
    <div class="toolbar"><input type="search" id="qBroker" placeholder="Filter broker…">
      <select class="f" id="fBrokerSide"><option value="">All</option>
        <option value="buy">Net buyers</option><option value="sell">Net sellers</option></select>
      <span class="hint" id="cBroker"></span></div>
    <div class="scroll"><table id="tBroker"></table></div></div></div>

  <div class="panel" id="p-scrips"><div class="card">
    <h2>Scrip activity</h2><p class="note">Click a row for the broker split.</p>
    <div class="toolbar"><input type="search" id="qScrip" placeholder="Filter symbol…">
      <select class="f" id="fScripSector"><option value="">All sectors</option></select>
      <span class="hint" id="cScrip"></span></div>
    <div class="scroll"><table id="tScrip"></table></div></div></div>

__SECTOR_PANEL__
__FUND_PANEL__

  <div class="panel" id="p-blocks"><div class="card">
    <h2>Largest single transactions</h2><p class="note">Cross = same broker both legs.</p>
    <div class="toolbar"><input type="search" id="qBlock" placeholder="Filter symbol or broker…">
      <select class="f" id="fBlockType"><option value="">All types</option>
        <option value="1">Cross only</option><option value="0">Inter-broker only</option></select>
      <span class="hint" id="cBlock"></span></div>
    <div class="scroll"><table id="tBlock"></table></div></div></div>

  <div class="panel" id="p-flow"><div class="card">
    <h2>Broker-to-broker flow matrix</h2>
    <p class="note">Rows sell, columns buy. Hover for value, click to open the seller.</p>
    <div class="toolbar"><select class="f" id="fFlowN">
      <option value="15">Top 15</option><option value="20" selected>Top 20</option>
      <option value="30">Top 30</option></select>
      <span class="hint">Logarithmic colour scale.</span></div>
    <div style="overflow:auto"><table class="hm" id="tFlow"></table></div></div></div>

  <div class="panel" id="p-trends">
    <div class="card"><h2>Market turnover by session</h2>
      <p class="note">Click a bar to jump to that day. Grey band marks the selected session.</p>
      <div id="trMarket"></div></div>
    <div class="grid2">
      <div class="card"><h2>Broker net flow over time</h2>
        <p class="note">Cumulative net tells you whether a position is being built or churned.</p>
        <div class="toolbar"><select class="f" id="trBroker"></select>
          <select class="f" id="trBrokerMode">
            <option value="net">Daily net</option>
            <option value="cum">Cumulative net</option>
            <option value="gross">Daily gross</option></select></div>
        <div id="trBrokerChart"></div></div>
      <div class="card"><h2>Scrip over time</h2>
        <p class="note">Turnover and VWAP across the archive.</p>
        <div class="toolbar"><select class="f" id="trScrip"></select>
          <select class="f" id="trScripMode">
            <option value="turnover">Turnover</option>
            <option value="vwap">VWAP</option></select></div>
        <div id="trScripChart"></div></div>
    </div>
    <div class="card"><h2>Session table</h2>
      <p class="note">Click any row to open that day.</p>
      <div class="scroll"><table id="tDays"></table></div></div>
  </div>

  <footer id="foot"></footer>
</div>

<div id="drawer"><div class="dhead"><b id="dTitle"></b>
  <button class="x" onclick="closeDrawer()">&times;</button></div>
  <div class="dbody" id="dBody"></div></div>
<div id="tip"></div><div id="load">Loading…</div>

<script>
const CR=1e7, LAKH=1e5;
const EMBED_GZ=__EMBED__;          // null on the site, gzip+base64 when offline
let IDX=null, PANEL=null, DAY=null, DATE=null, RANGE=null, EMB=null;
const CACHE=new Map();

async function inflate(b64){
  if(typeof DecompressionStream==='undefined')
    throw new Error('This browser cannot decompress the embedded data. '+
      'Open the online dashboard instead.');
  const bin=Uint8Array.from(atob(b64),c=>c.charCodeAt(0));
  const st=new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'));
  return JSON.parse(await new Response(st).text());
}
async function getDay(date){
  if(!CACHE.has(date))
    CACHE.set(date, EMB ? EMB.days[date]
                        : await (await fetch(`data/day/${date}.json`)).json());
  return CACHE.get(date);
}

function npr(x,pre){pre=pre===undefined?'Rs ':pre;
 if(x===null||x===undefined||isNaN(x))return '—';
 const s=x<0?'-':'',a=Math.abs(x);
 if(a>=100*CR)return s+pre+(a/(100*CR)).toFixed(2)+' Ar';
 if(a>=CR)return s+pre+(a/CR).toFixed(2)+' Cr';
 if(a>=LAKH)return s+pre+(a/LAKH).toFixed(2)+' L';
 return s+pre+Math.round(a).toLocaleString('en-IN');}
const num=x=>Math.round(x).toLocaleString('en-IN');
const pct=x=>x.toFixed(1)+'%';
const bname=c=>(DAY&&DAY.names&&DAY.names[c])?c+' · '+DAY.names[c]:'B-'+c;
const $=s=>document.querySelector(s);
const rowsOf=(p,k)=>{const c=p.cols[k];return p[k].map(r=>{const o={};c.forEach((n,i)=>o[n]=r[i]);return o;});};

/* ---------- boot ---------- */
async function boot(){
  if(EMBED_GZ){
    $('#load').classList.add('on'); $('#load').textContent='Unpacking…';
    try{ EMB=await inflate(EMBED_GZ); }
    catch(e){ $('#load').textContent=e.message; return; }
    IDX=EMB.index; PANEL=EMB.panel;
    $('#load').classList.remove('on'); $('#load').textContent='Loading…';
  } else {
    IDX=await (await fetch('data/index.json')).json();
  }
  await secLoad();
  await fundLoad();
  const sel=$('#daySel');
  sel.innerHTML=IDX.dates.slice().reverse().map(d=>`<option value="${d}">${d}</option>`).join('');
  $('#dayDate').min=IDX.dates[0]; $('#dayDate').max=IDX.dates[IDX.dates.length-1];
  buildBuckets();
  const want=location.hash.slice(1);
  if(want.includes('..')){
    const [f,t]=want.split('..');
    let i0=TL.buckets.findIndex(b=>b.to>=f), i1=TL.buckets.findIndex(b=>b.from>t);
    TL.sel=[i0<0?0:i0,(i1<0?TL.buckets.length:i1)-1];
    if(TL.sel[1]<TL.sel[0])TL.sel[1]=TL.sel[0];
    renderTimeline(); await showRange(f,t);
  } else {
    const d=IDX.dates.includes(want)?want:IDX.dates[IDX.dates.length-1];
    TL.level='day'; $('#tlLevel').value='day'; buildBuckets();
    const i=TL.buckets.findIndex(b=>b.key===d);
    TL.sel=[Math.max(0,i),Math.max(0,i)];
    renderTimeline(); await show(d);
  }
  offlineNote();
  $('#foot').innerHTML+='Broker net is a house-level proxy — the floor sheet does not '+
   'disclose client identity, and offsetting client orders net out inside a broker code. '+
   'Cross trades are typically negotiated transfers; screen them before reading flow. '+
   'NEPSE publishes no trade timestamp, so "last" is the final contract in sequence order. '+
   `Archive: ${IDX.dates.length} sessions, ${IDX.dates[0]} to ${IDX.dates[IDX.dates.length-1]}.`;
}

/* ---------- aggregate several sessions into one payload ---------- */
function mergeDays(days, dates){
  const C = days[0].cols;
  const bi = {}; C.brokers.forEach((c,i)=>bi[c]=i);
  const si = {}; C.scrips.forEach((c,i)=>si[c]=i);
  const xi = {}; C.bscrip.forEach((c,i)=>xi[c]=i);
  const pi = {}; C.pairs.forEach((c,i)=>pi[c]=i);
  const ki = {}; C.blocks.forEach((c,i)=>ki[c]=i);

  const B=new Map(), S=new Map(), X=new Map(), P=new Map();
  let blocks=[], names={}, kpi={turnover:0,volume:0,trades:0,cross_amt:0,max_ticket:0};

  days.forEach((d,di)=>{
    Object.assign(names, d.names||{});
    kpi.turnover+=d.kpi.turnover; kpi.volume+=d.kpi.volume; kpi.trades+=d.kpi.trades;
    kpi.cross_amt+=d.kpi.cross_amt; kpi.max_ticket=Math.max(kpi.max_ticket,d.kpi.max_ticket);

    d.brokers.forEach(r=>{const k=r[bi.code], o=B.get(k)||{code:k,buy:0,sell:0,trades:0,cross:0};
      o.buy+=r[bi.buy]; o.sell+=r[bi.sell]; o.trades+=r[bi.trades];
      o.cross+=r[bi.gross]*r[bi.crossPct]/200; B.set(k,o);});

    d.scrips.forEach(r=>{const k=r[si.sym];
      const o=S.get(k)||{sym:k,turnover:0,volume:0,trades:0,high:-1e18,low:1e18,last:0,
                         nBuy:0,nSell:0};
      o.turnover+=r[si.turnover]; o.volume+=r[si.volume]; o.trades+=r[si.trades];
      o.high=Math.max(o.high,r[si.high]); o.low=Math.min(o.low,r[si.low]);
      o.last=r[si.last];                              // days arrive in date order
      o.nBuy=Math.max(o.nBuy,r[si.nBuy]); o.nSell=Math.max(o.nSell,r[si.nSell]);
      S.set(k,o);});

    d.bscrip.forEach(r=>{const k=r[xi.broker]+'|'+r[xi.sym];
      const o=X.get(k)||{broker:r[xi.broker],sym:r[xi.sym],buy:0,sell:0};
      o.buy+=r[xi.buy]; o.sell+=r[xi.sell]; X.set(k,o);});

    d.pairs.forEach(r=>{const k=r[pi.buyer]+'|'+r[pi.seller];
      const o=P.get(k)||{buyer:r[pi.buyer],seller:r[pi.seller],amount:0,trades:0};
      o.amount+=r[pi.amount]; o.trades+=r[pi.trades]; P.set(k,o);});

    d.blocks.forEach(r=>blocks.push([r[ki.sym],r[ki.buyer],r[ki.seller],r[ki.qty],
      r[ki.rate],r[ki.amount],r[ki.cross],dates[di]]));
  });

  const brokers=[...B.values()].map(o=>{const gross=o.buy+o.sell;
    return [o.code,o.buy,o.sell,gross,o.buy-o.sell,o.trades,
      100*gross/(2*kpi.turnover), gross?100*2*o.cross/gross:0, gross/(o.trades||1)];})
    .sort((a,b)=>b[3]-a[3]);

  // buy-side HHI per scrip, from the aggregated broker x scrip cells
  const buyBy=new Map();
  X.forEach(o=>{const m=buyBy.get(o.sym)||new Map(); m.set(o.broker,(m.get(o.broker)||0)+o.buy);
    buyBy.set(o.sym,m);});
  const scrips=[...S.values()].map(o=>{
    const vwap=o.volume?o.turnover/o.volume:0;
    const m=buyBy.get(o.sym); let hhi=0;
    if(m){const tot=[...m.values()].reduce((a,b)=>a+b,0);
      if(tot>0)m.forEach(v=>hhi+=1e4*Math.pow(v/tot,2));}
    return [o.sym,o.turnover,o.volume,o.trades,vwap,o.high,o.low,o.last,
      vwap?100*(o.high-o.low)/vwap:0,o.nBuy,o.nSell,Math.round(hhi)];})
    .sort((a,b)=>b[1]-a[1]);

  const gs=brokers.map(r=>r[3]), tg=gs.reduce((a,b)=>a+b,0);
  kpi.scrips=scrips.length; kpi.brokers=brokers.length;
  kpi.avg_ticket=kpi.turnover/kpi.trades;
  kpi.median_ticket=null;                       // medians do not aggregate
  kpi.cross_pct=100*kpi.cross_amt/kpi.turnover;
  kpi.top10_broker_pct=100*gs.slice(0,10).reduce((a,b)=>a+b,0)/(tg||1);
  kpi.top10_scrip_pct=100*scrips.slice(0,10).reduce((a,r)=>a+r[1],0)/(kpi.turnover||1);
  kpi.broker_hhi=gs.reduce((a,v)=>a+1e4*Math.pow(v/(tg||1),2),0);
  kpi.scrip_hhi=scrips.reduce((a,r)=>a+1e4*Math.pow(r[1]/(kpi.turnover||1),2),0);

  return {date:`${dates[0]} to ${dates[dates.length-1]}`, kpi, names, cols:{...C,
      blocks:[...C.blocks,'day']},
    brokers, scrips,
    bscrip:[...X.values()].map(o=>[o.broker,o.sym,o.buy,o.sell]),
    pairs:[...P.values()].map(o=>[o.buyer,o.seller,o.amount,o.trades])
      .sort((a,b)=>b[2]-a[2]).slice(0,4000),
    blocks: blocks.sort((a,b)=>b[5]-a[5]).slice(0,400)};
}

async function showRange(from,to){
  const sel=IDX.dates.filter(d=>d>=from&&d<=to);
  if(!sel.length){alert('No sessions between '+from+' and '+to);return;}
  if(sel.length>80&&!confirm(`${sel.length} sessions will be downloaded `+
     `(about ${Math.round(sel.length*0.2)} MB). Continue?`))return;
  $('#load').classList.add('on');
  try{
    const days=[];
    for(let i=0;i<sel.length;i++){
      $('#load').textContent=`Loading ${i+1} of ${sel.length}…`;
      days.push(await getDay(sel[i]));
    }
    DAY=mergeDays(days,sel); DATE=sel[sel.length-1]; RANGE=sel;
    history.replaceState(null,'',`#${from}..${to}`);
    $('#meta').textContent=`${sel.length} session${sel.length>1?'s':''} aggregated`;
    $('#daySel').value=sel[sel.length-1];
    renderAll();
  }catch(e){alert('Could not load the range: '+e.message);}
  finally{$('#load').classList.remove('on');$('#load').textContent='Loading…';}
}

async function show(date){
  if(!IDX.dates.includes(date))return;
  $('#load').classList.add('on');
  try{
    DAY=await getDay(date); DATE=date; RANGE=null;
    $('#daySel').value=date; $('#dayDate').value=date;
    history.replaceState(null,'','#'+date);
    const i=IDX.dates.indexOf(date);
    $('#prev').disabled=i<=0; $('#next').disabled=i>=IDX.dates.length-1;
    $('#meta').textContent=`session ${i+1} of ${IDX.dates.length}`;
    syncTimelineToDay(date);
    renderAll();
  }catch(e){ alert('Could not load '+date+': '+e.message); }
  finally{ $('#load').classList.remove('on'); }
}

/* ---------- header ---------- */
function renderKpis(){
  if(RANGE){                                  // aggregated: use the merged kpi
    const K=DAY.kpi;
    $('#kpis').innerHTML=[['Turnover',npr(K.turnover)],['Trades',num(K.trades)],
      ['Volume',num(K.volume)],['Sessions',RANGE.length],['Scrips',num(K.scrips)],
      ['Brokers',num(K.brokers)],['Avg ticket',npr(K.avg_ticket)],
      ['Cross',pct(K.cross_pct)]]
      .map(([l,v])=>`<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join('');
    return;
  }
  const k=IDX.kpi[DATE], i=IDX.dates.indexOf(DATE);
  const p=i>0?IDX.kpi[IDX.dates[i-1]]:null;
  const chg=(cur,prev)=>{if(!prev)return '';const d=100*(cur-prev)/prev;
    return ` <i class="${d>=0?'up':'down'}">${d>=0?'+':''}${d.toFixed(1)}%</i>`;};
  $('#kpis').innerHTML=[
    ['Turnover',npr(k.turnover*LAKH),chg(k.turnover,p&&p.turnover)],
    ['Trades',num(k.trades),chg(k.trades,p&&p.trades)],
    ['Volume',num(k.volume),chg(k.volume,p&&p.volume)],
    ['Scrips',num(k.scrips),''],['Brokers',num(k.brokers),''],
    ['Avg ticket',npr(k.avgTicket),''],
    ['Top-10 brokers',pct(k.top10Broker),''],['Cross',pct(k.crossPct),''],
  ].map(([l,v,c])=>`<div class="kpi"><b>${v}${c}</b><span>${l}</span></div>`).join('');
}

/* ---------- shared bar list ---------- */
function barList(el,items,opt){opt=opt||{};
 const max=Math.max(1,...items.map(i=>Math.abs(i.v)),...items.map(i=>Math.abs(i.v2||0)));
 el.innerHTML=items.map(i=>{let inner;
  if(opt.diverging||opt.butterfly){
    const neg=opt.butterfly?Math.abs(i.v2):Math.abs(Math.min(i.v,0));
    const pos=opt.butterfly?i.v:Math.max(i.v,0);
    inner=`<div class="mid" style="left:50%"></div>
     <div class="fill" style="right:50%;width:${50*neg/max}%;background:var(--sell)"></div>
     <div class="fill" style="left:50%;width:${50*pos/max}%;background:var(--buy)"></div>`;
  } else inner=`<div class="fill" style="left:0;width:${100*i.v/max}%;background:${opt.color||'var(--navy)'}"></div>`;
  return `<div class="bar-row"${i.click?` style="cursor:pointer" onclick="${i.click}"`:''}>
   <div class="lbl">${i.k}</div><div class="track">${inner}</div>
   <div class="val">${i.t}</div></div>`;}).join('')||'<div class="empty">No data.</div>';}

/* ---------- sortable table ---------- */
function makeTable(elId,cols,getData,onRow){
  const el=$('#'+elId); let sk=cols.find(c=>c.sort).k, sd=-1;
  function draw(){
    const data=getData().slice().sort((a,b)=>{const x=a[sk],y=b[sk];
      return (typeof x==='string'?x.localeCompare(y):x-y)*sd;});
    el.innerHTML='<thead><tr>'+cols.map(c=>`<th data-k="${c.k}" class="${c.num?'num ':''}${c.k===sk?(sd<0?'desc':'asc'):''}">${c.h}</th>`).join('')+
     '</tr></thead><tbody>'+data.map(r=>`<tr${onRow?` class="clickable" data-id="${r[cols[0].k]}"`:''}>`+
      cols.map(c=>`<td class="${c.num?'num ':''}${c.cls?c.cls(r):''}">${c.f(r)}</td>`).join('')+'</tr>').join('')+'</tbody>';
    el.querySelectorAll('th').forEach(th=>th.onclick=()=>{const k=th.dataset.k;
      if(k===sk)sd=-sd;else{sk=k;sd=(typeof getData()[0][k]==='string')?1:-1;}draw();});
    if(onRow)el.querySelectorAll('tbody tr').forEach(tr=>tr.onclick=()=>onRow(tr.dataset.id));
    return data.length;}
  return draw;}

/* ---------- per-day rendering ---------- */
let BROKERS=[],SCRIPS=[],BSCRIP=[],PAIRS=[],BLOCKS=[],BR_BY={},SC_BY={};
let drawBrokers,drawScrips,drawBlocks;

function renderAll(){
  BROKERS=rowsOf(DAY,'brokers'); SCRIPS=rowsOf(DAY,'scrips');
  SCRIPS.forEach(s=>{s.sector=secGroupAll(s.sym);
    const _f=(typeof fundRow==='function')?fundRow(s):null;
    s.pe=_f?_f.peLive:null; s.pb=_f?_f.pbvLive:null;});
  secFillScripFilter();
  BSCRIP=rowsOf(DAY,'bscrip'); PAIRS=rowsOf(DAY,'pairs'); BLOCKS=rowsOf(DAY,'blocks');
  BR_BY={};BROKERS.forEach(b=>BR_BY[b.code]=b);
  SC_BY={};SCRIPS.forEach(s=>SC_BY[s.sym]=s);
  renderKpis();

  barList($('#ovScrips'),SCRIPS.slice(0,15).map(s=>({k:s.sym,v:s.turnover,
    t:npr(s.turnover),click:`openScrip('${s.sym}')`})));
  barList($('#ovBrokers'),BROKERS.slice(0,15).map(b=>({k:bname(b.code),v:b.buy,v2:b.sell,
    t:npr(b.gross),click:`openBroker(${b.code})`})),{butterfly:1});
  const ns=BROKERS.slice().sort((a,b)=>b.net-a.net);
  barList($('#ovNet'),ns.slice(0,8).concat(ns.slice(-8)).map(b=>({k:bname(b.code),v:b.net,
    t:npr(b.net),click:`openBroker(${b.code})`})),{diverging:1});
  const K=DAY.kpi, big=BLOCKS.filter(b=>b.amount>=CR).length;
  $('#ovStats').innerHTML='<div class="stat">'+[['Broker HHI',num(K.broker_hhi)],
    ['Scrip HHI',num(K.scrip_hhi)],['Median ticket',K.median_ticket===null?'—':npr(K.median_ticket)],
    ['Largest ticket',npr(K.max_ticket)],['Cross trades',npr(K.cross_amt)],
    ['Tickets ≥ 1 Cr',big]].map(([l,v])=>`<div>${l}<b>${v}</b></div>`).join('')+'</div>'+
    '<h3 class="sec">Most concentrated buying</h3><div id="hhiBars"></div>';
  barList($('#hhiBars'),SCRIPS.filter(s=>s.turnover>2e6).sort((a,b)=>b.hhi-a.hhi)
    .slice(0,8).map(s=>({k:s.sym,v:s.hhi,t:num(s.hhi)+' · '+npr(s.turnover),
    click:`openScrip('${s.sym}')`})),{color:'var(--gold)'});

  drawBrokers=makeTable('tBroker',[
    {k:'code',h:'Broker',f:r=>bname(r.code)},
    {k:'buy',h:'Buy',num:1,f:r=>npr(r.buy,'')},
    {k:'sell',h:'Sell',num:1,f:r=>npr(r.sell,'')},
    {k:'gross',h:'Gross',num:1,sort:1,f:r=>npr(r.gross,'')},
    {k:'net',h:'Net',num:1,f:r=>npr(r.net,''),cls:r=>r.net>=0?'pos':'neg'},
    {k:'share',h:'Share %',num:1,f:r=>r.share.toFixed(2)},
    {k:'trades',h:'Trades',num:1,f:r=>num(r.trades)},
    {k:'crossPct',h:'Cross %',num:1,f:r=>r.crossPct.toFixed(1)}],
    ()=>{const q=$('#qBroker').value.trim().toLowerCase(),s=$('#fBrokerSide').value;
      return BROKERS.filter(b=>(!q||bname(b.code).toLowerCase().includes(q))&&
        (!s||(s==='buy'?b.net>0:b.net<0)));},c=>openBroker(+c));
  drawScrips=makeTable('tScrip',[
    {k:'sym',h:'Scrip',f:r=>r.sym},
    {k:'sector',h:'Sector',f:r=>r.sector||'—'},
    {k:'pe',h:'P/E',num:1,f:r=>r.pe==null?'—':r.pe.toFixed(1)},
    {k:'pb',h:'P/B',num:1,f:r=>r.pb==null?'—':r.pb.toFixed(2)},
    {k:'turnover',h:'Turnover',num:1,sort:1,f:r=>npr(r.turnover,'')},
    {k:'volume',h:'Volume',num:1,f:r=>num(r.volume)},
    {k:'trades',h:'Trades',num:1,f:r=>num(r.trades)},
    {k:'vwap',h:'VWAP',num:1,f:r=>r.vwap.toFixed(1)},
    {k:'low',h:'Low',num:1,f:r=>r.low.toFixed(1)},
    {k:'high',h:'High',num:1,f:r=>r.high.toFixed(1)},
    {k:'rangePct',h:'Range %',num:1,f:r=>r.rangePct.toFixed(1)},
    {k:'hhi',h:'Buy HHI',num:1,f:r=>num(r.hhi)}],
    ()=>{const q=$('#qScrip').value.trim().toUpperCase(),g=$('#fScripSector').value;
      return SCRIPS.filter(s=>(!q||s.sym.includes(q))&&(!g||s.sector===g));},
    s=>openScrip(s));
  const blockCols=[
    {k:'sym',h:'Scrip',f:r=>r.sym},{k:'buyer',h:'Buyer',f:r=>bname(r.buyer)},
    {k:'seller',h:'Seller',f:r=>bname(r.seller)},
    {k:'qty',h:'Qty',num:1,f:r=>num(r.qty)},
    {k:'rate',h:'Rate',num:1,f:r=>r.rate.toFixed(1)},
    {k:'amount',h:'Value',num:1,sort:1,f:r=>npr(r.amount,'')},
    {k:'cross',h:'Type',f:r=>r.cross?'<span class="tag cross">Cross</span>':
      '<span class="tag inter">Inter-broker</span>'}];
  if(RANGE)blockCols.splice(1,0,{k:'day',h:'Day',f:r=>r.day});
  drawBlocks=makeTable('tBlock',blockCols,
    ()=>{const q=$('#qBlock').value.trim().toLowerCase(),t=$('#fBlockType').value;
      return BLOCKS.filter(b=>(!q||b.sym.toLowerCase().includes(q)||String(b.buyer)===q||
        String(b.seller)===q)&&(t===''||String(b.cross)===t));});
  refreshTables(); drawFlow();
  if($('#p-trends').classList.contains('on'))renderTrends();
  if($('#p-sectors').classList.contains('on'))renderSectors();
  if($('#p-fund').classList.contains('on'))renderFund();
}
function refreshTables(){
  $('#cBroker').textContent=drawBrokers()+' brokers';
  $('#cScrip').textContent=drawScrips()+' scrips';
  $('#cBlock').textContent=drawBlocks()+' contracts';}

/* ---------- flow matrix ---------- */
const tip=$('#tip');
function showTip(e,h){tip.innerHTML=h;tip.style.opacity=1;
 tip.style.left=(e.clientX+14)+'px';tip.style.top=(e.clientY+14)+'px';}
function hideTip(){tip.style.opacity=0;}
function drawFlow(){
  const n=+$('#fFlowN').value, top=BROKERS.slice(0,n).map(b=>b.code), idx={};
  top.forEach((c,i)=>idx[c]=i);
  const m=top.map(()=>top.map(()=>0));
  PAIRS.forEach(p=>{if(idx[p.buyer]!==undefined&&idx[p.seller]!==undefined)
    m[idx[p.seller]][idx[p.buyer]]+=p.amount;});
  const vals=m.flat().filter(v=>v>0);
  if(!vals.length){$('#tFlow').innerHTML='';return;}
  const lo=Math.log(Math.max(Math.min(...vals),1e4)),hi=Math.log(Math.max(...vals));
  const shade=v=>{if(!v)return '#fff';
    const t=Math.min(1,Math.max(0,(Math.log(v)-lo)/(hi-lo||1)));
    return `rgb(${Math.round(255-176*t)},${Math.round(255-218*t)},${Math.round(255-186*t)})`;};
  let h='<thead><tr><th></th>'+top.map(c=>`<th class="v">${bname(c)}</th>`).join('')+'</tr></thead><tbody>';
  top.forEach((rc,i)=>{h+=`<tr><th>${bname(rc)}</th>`;
    top.forEach((cc,j)=>{h+=`<td style="background:${shade(m[i][j])}" data-v="${m[i][j]}" data-s="${rc}" data-b="${cc}"></td>`;});
    h+='</tr>';});
  const el=$('#tFlow'); el.innerHTML=h+'</tbody>';
  el.querySelectorAll('td[data-v]').forEach(td=>{
    td.onmousemove=e=>showTip(e,`<b>${bname(td.dataset.s)}</b> sold ${npr(+td.dataset.v)}<br>to <b>${bname(td.dataset.b)}</b>`+
      (td.dataset.s===td.dataset.b?'<br><i>cross trade</i>':''));
    td.onmouseleave=hideTip; td.onclick=()=>{hideTip();openBroker(+td.dataset.s);};});}

/* ---------- drawer ---------- */
const drawer=$('#drawer');
function closeDrawer(){drawer.classList.remove('on');}
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeDrawer();});
function openBroker(code){
  const b=BR_BY[code]; if(!b)return;
  $('#dTitle').textContent=bname(code);
  const mine=BSCRIP.filter(r=>r.broker===code).map(r=>({sym:r.sym,net:r.buy-r.sell}));
  const byNet=mine.slice().sort((x,y)=>y.net-x.net);
  const from=PAIRS.filter(p=>p.buyer===code).sort((x,y)=>y.amount-x.amount).slice(0,8);
  const to=PAIRS.filter(p=>p.seller===code).sort((x,y)=>y.amount-x.amount).slice(0,8);
  $('#dBody').innerHTML='<div class="stat">'+[['Buy',npr(b.buy)],['Sell',npr(b.sell)],
    ['Net',npr(b.net)],['Gross',npr(b.gross)],['Share',b.share.toFixed(2)+'%'],
    ['Trades',num(b.trades)],['Cross',b.crossPct.toFixed(1)+'%']]
    .map(([l,v])=>`<div>${l}<b>${v}</b></div>`).join('')+'</div>'+
    `<h3 class="sec">Net position by scrip</h3><div id="dNet"></div>
     <h3 class="sec">Bought from</h3><div id="dFrom"></div>
     <h3 class="sec">Sold to</h3><div id="dTo"></div>
     <h3 class="sec">This broker across the archive</h3><div id="dTrend"></div>`;
  barList($('#dNet'),byNet.slice(0,7).concat(byNet.slice(-7))
    .filter((v,i,a)=>a.indexOf(v)===i).map(r=>({k:r.sym,v:r.net,t:npr(r.net,''),
    click:`openScrip('${r.sym}')`})),{diverging:1});
  barList($('#dFrom'),from.map(p=>({k:bname(p.seller),v:p.amount,
    t:npr(p.amount,'')+' · '+p.trades,click:`openBroker(${p.seller})`})),{color:'var(--buy)'});
  barList($('#dTo'),to.map(p=>({k:bname(p.buyer),v:p.amount,
    t:npr(p.amount,'')+' · '+p.trades,click:`openBroker(${p.buyer})`})),{color:'var(--sell)'});
  ensurePanel().then(()=>{const s=PANEL.brokers[String(code)];
    if(s)$('#dTrend').innerHTML=lineChart(PANEL.dates,s[0],{zero:1,unit:'L',
      label:'daily net'});});
  drawer.classList.add('on'); drawer.scrollTop=0;}
function openScrip(sym){
  const s=SC_BY[sym]; if(!s)return;
  $('#dTitle').textContent=sym;
  const mine=BSCRIP.filter(r=>r.sym===sym).map(r=>({code:r.broker,buy:r.buy,sell:r.sell,
    net:r.buy-r.sell,gross:r.buy+r.sell})).sort((x,y)=>y.gross-x.gross);
  $('#dBody').innerHTML='<div class="stat">'+[['Turnover',npr(s.turnover)],
    ['Volume',num(s.volume)],['Trades',num(s.trades)],['VWAP',s.vwap.toFixed(1)],
    ['Low–High',s.low.toFixed(1)+' – '+s.high.toFixed(1)],['Range',s.rangePct.toFixed(1)+'%'],
    ['Buy HHI',num(s.hhi)]].map(([l,v])=>`<div>${l}<b>${v}</b></div>`).join('')+'</div>'+
    `<h3 class="sec">Broker participation</h3><div id="dPart"></div>
     <h3 class="sec">Net by broker</h3><div id="dSNet"></div>
     <h3 class="sec">This scrip across the archive</h3><div id="dTrend"></div>`;
  barList($('#dPart'),mine.slice(0,14).map(r=>({k:bname(r.code),v:r.buy,v2:r.sell,
    t:npr(r.gross,''),click:`openBroker(${r.code})`})),{butterfly:1});
  const bn=mine.slice().sort((x,y)=>y.net-x.net);
  barList($('#dSNet'),bn.slice(0,6).concat(bn.slice(-6)).filter((v,i,a)=>a.indexOf(v)===i)
    .map(r=>({k:bname(r.code),v:r.net,t:npr(r.net,''),click:`openBroker(${r.code})`})),{diverging:1});
  ensurePanel().then(()=>{const ser=PANEL.scrips[sym];
    if(ser)$('#dTrend').innerHTML=lineChart(PANEL.dates,ser[0],{unit:'L',label:'turnover'});});
  drawer.classList.add('on'); drawer.scrollTop=0;}

/* ---------- charts ---------- */
function lineChart(labels,vals,o){
  o=o||{}; const W=680,H=200,P={l:46,r:8,t:10,b:22};
  const pts=vals.map((v,i)=>[i,v]).filter(p=>p[1]!==null&&p[1]!==undefined);
  if(!pts.length)return '<div class="empty">No data.</div>';
  let mn=Math.min(...pts.map(p=>p[1])), mx=Math.max(...pts.map(p=>p[1]));
  if(o.zero){mn=Math.min(mn,0);mx=Math.max(mx,0);} if(mn===mx){mn-=1;mx+=1;}
  const x=i=>P.l+(W-P.l-P.r)*(labels.length<2?0.5:i/(labels.length-1));
  const y=v=>P.t+(H-P.t-P.b)*(1-(v-mn)/(mx-mn));
  const d=pts.map((p,k)=>(k?'L':'M')+x(p[0]).toFixed(1)+' '+y(p[1]).toFixed(1)).join(' ');
  const ticks=[mn,(mn+mx)/2,mx].map(v=>
    `<line class="gl" x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}"/>
     <text x="${P.l-5}" y="${y(v)+3}" text-anchor="end">${(v>=1000?(v/100).toFixed(0)+'Cr':v.toFixed(0)+(o.unit||''))}</text>`).join('');
  const zero=(mn<0&&mx>0)?`<line class="ax" x1="${P.l}" y1="${y(0)}" x2="${W-P.r}" y2="${y(0)}"/>`:'';
  const step=Math.max(1,Math.ceil(labels.length/6));
  const xl=labels.map((l,i)=>i%step?'':`<text x="${x(i)}" y="${H-6}" text-anchor="middle">${l.slice(5)}</text>`).join('');
  const marker=o.mark!==undefined&&o.mark>=0?
    `<line class="ax" x1="${x(o.mark)}" y1="${P.t}" x2="${x(o.mark)}" y2="${H-P.b}" stroke="var(--gold)" stroke-dasharray="3 3"/>`:'';
  const area=o.zero?'':`<path d="${d} L ${x(pts[pts.length-1][0])} ${y(mn)} L ${x(pts[0][0])} ${y(mn)} Z" fill="var(--navy)" opacity=".07"/>`;
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${ticks}${zero}${marker}${area}
   <path d="${d}" fill="none" stroke="var(--navy)" stroke-width="2"/>${xl}</svg>`;}

function barChart(labels,vals,onClick,markIdx){
  const W=1180,H=210,P={l:52,r:8,t:10,b:24};
  const mx=Math.max(...vals,1), bw=(W-P.l-P.r)/vals.length;
  const y=v=>P.t+(H-P.t-P.b)*(1-v/mx);
  const bars=vals.map((v,i)=>`<rect x="${(P.l+i*bw+bw*.12).toFixed(1)}" y="${y(v).toFixed(1)}"
    width="${(bw*.76).toFixed(1)}" height="${(H-P.b-y(v)).toFixed(1)}"
    fill="${i===markIdx?'var(--gold)':'var(--navy)'}" style="cursor:pointer"
    onclick="${onClick}('${labels[i]}')"><title>${labels[i]} — ${npr(vals[i]*LAKH)}</title></rect>`).join('');
  const ticks=[0,mx/2,mx].map(v=>`<line class="gl" x1="${P.l}" y1="${y(v)}" x2="${W-P.r}" y2="${y(v)}"/>
    <text x="${P.l-5}" y="${y(v)+3}" text-anchor="end">${(v/100).toFixed(0)} Cr</text>`).join('');
  const step=Math.max(1,Math.ceil(labels.length/12));
  const xl=labels.map((l,i)=>i%step?'':`<text x="${(P.l+i*bw+bw/2).toFixed(1)}" y="${H-6}" text-anchor="middle">${l.slice(5)}</text>`).join('');
  return `<svg class="chart" style="height:230px" viewBox="0 0 ${W} ${H}">${ticks}${bars}${xl}</svg>`;}

/* ---------- trends ---------- */
async function ensurePanel(){
  if(!PANEL)PANEL=await (await fetch('data/panel.json')).json();
  return PANEL;}
function offlineNote(){
  if(!EMB)return;
  $('#foot').insertAdjacentHTML('afterbegin',
    `<div style="background:#FBF1D2;color:#7A5F02;padding:7px 10px;border-radius:4px;
      margin-bottom:10px"><b>Offline copy</b> — the ${IDX.dates.length} most recent
      sessions are embedded in this file. The full archive is online.</div>`);}
async function renderTrends(){
  await ensurePanel();
  const d=IDX.dates, mark=d.indexOf(DATE);
  $('#trMarket').innerHTML=barChart(d,d.map(x=>IDX.kpi[x].turnover),'show',mark);
  if(RANGE)$('#trMarket').insertAdjacentHTML('afterbegin',
    `<div class="hint" style="margin-bottom:6px">Aggregating ${RANGE.length} `+
    `session(s): ${RANGE[0]} to ${RANGE[RANGE.length-1]}.</div>`);

  const bsel=$('#trBroker');
  if(!bsel.options.length){
    const order=Object.keys(PANEL.brokers).sort((a,b)=>
      (PANEL.brokers[b][1].reduce((s,v)=>s+(v||0),0))-(PANEL.brokers[a][1].reduce((s,v)=>s+(v||0),0)));
    bsel.innerHTML=order.map(c=>`<option value="${c}">${bname(c)}</option>`).join('');
    const ssel=$('#trScrip');
    const sorder=Object.keys(PANEL.scrips).sort((a,b)=>
      (PANEL.scrips[b][0].reduce((s,v)=>s+(v||0),0))-(PANEL.scrips[a][0].reduce((s,v)=>s+(v||0),0)));
    ssel.innerHTML=sorder.map(s=>`<option value="${s}">${s}</option>`).join('');
  }
  drawBrokerTrend(); drawScripTrend();}
function drawBrokerTrend(){
  const s=PANEL.brokers[$('#trBroker').value]; if(!s)return;
  const m=$('#trBrokerMode').value;
  let v=m==='gross'?s[1]:s[0];
  if(m==='cum'){let t=0;v=s[0].map(x=>{t+=(x||0);return t;});}
  $('#trBrokerChart').innerHTML=lineChart(PANEL.dates,v,
    {zero:m!=='gross',unit:'L',mark:PANEL.dates.indexOf(DATE)});}
function drawScripTrend(){
  const s=PANEL.scrips[$('#trScrip').value]; if(!s)return;
  const m=$('#trScripMode').value;
  $('#trScripChart').innerHTML=lineChart(PANEL.dates,m==='vwap'?s[1]:s[0],
    {unit:m==='vwap'?'':'L',mark:PANEL.dates.indexOf(DATE)});}

__SECTOR_JS__
__FUND_JS__

/* ---------- wiring ---------- */
/* ---------- timeline slicer ---------- */
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const TL={level:'month',buckets:[],sel:[0,0],drag:null,syncing:false};

function bucketKey(d,level){
  const y=+d.slice(0,4), m=+d.slice(5,7), day=+d.slice(8,10);
  if(level==='day')return d;
  if(level==='month')return d.slice(0,7);
  if(level==='quarter')return y+'-Q'+Math.ceil(m/3);
  const dt=new Date(Date.UTC(y,m-1,day));               // week: Monday start
  dt.setUTCDate(dt.getUTCDate()-((dt.getUTCDay()+6)%7));
  return dt.toISOString().slice(0,10);
}
function bucketLabel(key,level){
  if(level==='day')  return MON[+key.slice(5,7)-1]+' '+(+key.slice(8,10));
  if(level==='month')return MON[+key.slice(5,7)-1]+" '"+key.slice(2,4);
  if(level==='quarter')return key.slice(5)+" '"+key.slice(2,4);
  return MON[+key.slice(5,7)-1]+' '+(+key.slice(8,10));
}
function buildBuckets(){
  const m=new Map();
  IDX.dates.forEach(d=>{
    const k=bucketKey(d,TL.level);
    const b=m.get(k)||{key:k,label:bucketLabel(k,TL.level),dates:[],turnover:0};
    b.dates.push(d); b.turnover+=IDX.kpi[d].turnover; m.set(k,b);
  });
  TL.buckets=[...m.values()].sort((a,b)=>a.key<b.key?-1:1);
  TL.buckets.forEach(b=>{b.from=b.dates[0];b.to=b.dates[b.dates.length-1];});
}
function selDates(){
  const a=TL.buckets[TL.sel[0]], b=TL.buckets[TL.sel[1]];
  return IDX.dates.filter(d=>d>=a.from&&d<=b.to);
}
function renderTimeline(){
  const track=$('#tlTrack'), n=TL.buckets.length;
  const mx=Math.max(...TL.buckets.map(b=>b.turnover),1);
  track.querySelectorAll('.bk').forEach(e=>e.remove());
  const w=100/n;
  TL.buckets.forEach((b,i)=>{
    const el=document.createElement('div');
    el.className='bk'+(i>=TL.sel[0]&&i<=TL.sel[1]?' on':'');
    el.style.left=(i*w+w*0.12)+'%'; el.style.width=(w*0.76)+'%';
    el.style.height=Math.max(3,42*b.turnover/mx)+'px';
    el.title=b.label+' — '+npr(b.turnover*LAKH)+' · '+b.dates.length+' session(s)';
    track.appendChild(el);
  });
  const l=TL.sel[0]*w, r=(TL.sel[1]+1)*w;
  $('#tlSel').style.left=l+'%'; $('#tlSel').style.width=(r-l)+'%';
  $('#tlH0').style.left=l+'%'; $('#tlH1').style.left=r+'%';
  const step=Math.max(1,Math.ceil(n/14));
  $('#tlAxis').innerHTML=TL.buckets.map((b,i)=>i%step?'':
    `<span style="left:${(i*w+w/2)}%">${b.label}</span>`).join('');
  const a=TL.buckets[TL.sel[0]], z=TL.buckets[TL.sel[1]];
  $('#tlLabel').textContent=TL.sel[0]===TL.sel[1]?a.label:a.label+' – '+z.label;
  const ds=selDates();
  $('#tlSub').textContent=`${ds.length} session${ds.length>1?'s':''} · `+
    `${ds[0]} to ${ds[ds.length-1]}`;
}
function applyTimeline(){
  const ds=selDates();
  if(!ds.length)return;
  TL.syncing=true;
  (ds.length===1?show(ds[0]):showRange(ds[0],ds[ds.length-1]))
    .finally(()=>{TL.syncing=false;});
}
function idxFromX(clientX){
  const r=$('#tlTrack').getBoundingClientRect();
  return Math.max(0,Math.min(TL.buckets.length-1,
    Math.floor((clientX-r.left)/r.width*TL.buckets.length)));
}
function startDrag(e,mode){
  e.preventDefault();
  const i=idxFromX(e.clientX);
  TL.drag = mode==='h0' ? {anchor:TL.sel[1]}
          : mode==='h1' ? {anchor:TL.sel[0]}
          : {anchor:i};
  if(mode==='track')TL.sel=[i,i];
  moveDrag(e);
  window.addEventListener('pointermove',moveDrag);
  window.addEventListener('pointerup',endDrag,{once:true});
}
function moveDrag(e){
  if(!TL.drag)return;
  const i=idxFromX(e.clientX), a=TL.drag.anchor;
  TL.sel=[Math.min(a,i),Math.max(a,i)];
  renderTimeline();                       // instant feedback, no data loaded yet
}
function endDrag(){
  window.removeEventListener('pointermove',moveDrag);
  TL.drag=null;
  applyTimeline();                        // load only once, on release
}
$('#tlTrack').addEventListener('pointerdown',e=>{
  if(e.target.classList.contains('tlh'))return;
  startDrag(e,'track');});
$('#tlH0').addEventListener('pointerdown',e=>startDrag(e,'h0'));
$('#tlH1').addEventListener('pointerdown',e=>startDrag(e,'h1'));
$('#tlLevel').onchange=()=>{
  const keep=selDates();
  TL.level=$('#tlLevel').value; buildBuckets();
  const from=keep[0], to=keep[keep.length-1];
  let i0=TL.buckets.findIndex(b=>b.to>=from), i1=TL.buckets.findIndex(b=>b.from>to);
  TL.sel=[i0<0?0:i0, (i1<0?TL.buckets.length:i1)-1];
  if(TL.sel[1]<TL.sel[0])TL.sel[1]=TL.sel[0];
  renderTimeline(); applyTimeline();};
$('#tlAll').onclick=()=>{TL.sel=[0,TL.buckets.length-1];renderTimeline();applyTimeline();};

/* Day controls stay authoritative for stepping one session at a time; using
   them drops the timeline to day level so the two never disagree. */
function syncTimelineToDay(date){
  if(TL.syncing)return;
  if(TL.level!=='day'){TL.level='day';$('#tlLevel').value='day';buildBuckets();}
  const i=TL.buckets.findIndex(b=>b.key===date);
  if(i>=0){TL.sel=[i,i];renderTimeline();}
}
$('#daySel').onchange=e=>show(e.target.value);
$('#dayDate').onchange=e=>{ if(IDX.dates.includes(e.target.value))show(e.target.value);
  else{ const near=IDX.dates.filter(d=>d<=e.target.value).pop()||IDX.dates[0];
    alert(`No session on ${e.target.value}. Opening ${near}.`); show(near);} };
$('#prev').onclick=()=>show(IDX.dates[IDX.dates.indexOf(DATE)-1]);
$('#next').onclick=()=>show(IDX.dates[IDX.dates.indexOf(DATE)+1]);
$('#latest').onclick=()=>show(IDX.dates[IDX.dates.length-1]);
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT')return;
  if(e.key==='ArrowLeft'&&!$('#prev').disabled)$('#prev').click();
  if(e.key==='ArrowRight'&&!$('#next').disabled)$('#next').click();});
['qBroker','fBrokerSide','qScrip','fScripSector','qBlock','fBlockType'].forEach(id=>
  $('#'+id).addEventListener('input',refreshTables));
$('#fFlowN').addEventListener('change',drawFlow);
['trBroker','trBrokerMode'].forEach(id=>$('#'+id).addEventListener('change',drawBrokerTrend));
['trScrip','trScripMode'].forEach(id=>$('#'+id).addEventListener('change',drawScripTrend));
$('#tabs').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;
  document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
  document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('on',p.id==='p-'+b.dataset.t));
  if(b.dataset.t==='trends')renderTrends();
  if(b.dataset.t==='sectors')renderSectors();
  if(b.dataset.t==='fund')renderFund();});
window.addEventListener('hashchange',()=>{const h=location.hash.slice(1);
  if(h&&h!==DATE&&IDX.dates.includes(h))show(h);});
boot();
</script></body></html>
"""


def write_sectors(out: str, listed: str = sm.DEFAULT_PATH) -> str | None:
    """data/sectors.json — the map the browser joins the floor sheet against.

    Rewritten on every build and deliberately not cached. It is 37 KB, and the
    whole point of joining in the browser is that a new listing re-maps every
    session in the archive without a single parquet being reopened. A missing
    listing csv is a warning rather than a failure: the site still builds, the
    Sectors tab just says where the map went.
    """
    try:
        m = sm.load(listed)
    except FileNotFoundError as e:
        print(f"WARNING: {e}\n         The Sectors tab will be empty.")
        return None
    p = sm.write_payload(m, out)
    print(f"Sector map: {p} ({os.path.getsize(p) / 1024:,.0f} KB, "
          f"{len(m)} securities)")
    return p


def write_fundamentals(out: str, path: str,
                       listed: str | None = None) -> str | None:
    """data/fundamentals.json — the npstocks snapshot, keyed to symbols.

    Missing is a warning, not a failure: the site is useful without it and the
    tab says so itself. A join that resolves nothing, though, is a broken alias
    file rather than an empty one, so that gets said out loud.
    """
    try:
        d = fm.load(path, listed)
    except FileNotFoundError as e:
        print(f"WARNING: {e}\n         The Fundamentals tab will be empty.")
        return None
    p = fm.write_payload(d, out, fm.asof_from_name(path))
    un = d.attrs.get("unmatched", [])
    print(f"Fundamentals: {p} ({os.path.getsize(p) / 1024:,.0f} KB, "
          f"{len(d)} symbols"
          + (f", {len(un)} unmatched" if un else "") + ")")
    if un:
        print("  unmatched: " + ", ".join(un[:8])
              + (" …" if len(un) > 8 else ""))
    return p


def _read_json(site_dir: str, name: str):
    p = os.path.join(site_dir, "data", name)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _read_sectors(site_dir: str):
    p = os.path.join(site_dir, "data", "sectors.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _render(embed: str) -> str:
    """APP with the sector blocks filled in and the payload slot set."""
    return (APP.replace("__SECTOR_CSS__", sv.SECTOR_CSS)
               .replace("__SECTOR_PANEL__", sv.SECTOR_PANEL)
               .replace("__SECTOR_JS__", sv.SECTOR_JS)
               .replace("__FUND_CSS__", fmv.FUND_CSS)
               .replace("__FUND_PANEL__", fmv.FUND_PANEL)
               .replace("__FUND_JS__", fmv.FUND_JS)
               .replace("__EMBED__", embed))


def build_app(out: str) -> str:
    """The Pages app: fetches its data from data/*.json alongside it."""
    p = os.path.join(out, "index.html")
    os.makedirs(out, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(_render("null"))
    return p


def build_offline(site_dir: str, out_html: str, days: int = 22) -> str:
    """The same app with the last N sessions embedded, for the mail attachment.

    Mail clients strip scripts, so this cannot be the body — but opening the
    attachment hands it to the browser, where it runs with no network at all.
    The payload is gzipped before base64 because raw JSON for a month is 4.9 MB
    against 2.6 MB compressed.
    """
    import base64
    import gzip

    ddir = os.path.join(site_dir, "data", "day")
    with open(os.path.join(site_dir, "data", "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    with open(os.path.join(site_dir, "data", "panel.json"), encoding="utf-8") as f:
        panel = json.load(f)

    sel = index["dates"][-days:] if days > 0 else index["dates"]
    keep = set(sel)
    payload = {
        "index": {"dates": sel, "kpi": {d: index["kpi"][d] for d in sel}},
        "panel": {
            "dates": [d for d in panel["dates"] if d in keep],
            "brokers": {}, "scrips": {},
        },
        "days": {},
        "sectors": _read_sectors(site_dir),
        "fundamentals": _read_json(site_dir, "fundamentals.json"),
    }
    mask = [i for i, d in enumerate(panel["dates"]) if d in keep]
    for grp in ("brokers", "scrips"):
        for key, cols in panel[grp].items():
            payload["panel"][grp][key] = [[c[i] for i in mask] for c in cols]
    for d in sel:
        with open(os.path.join(ddir, f"{d}.json"), encoding="utf-8") as f:
            payload["days"][d] = json.load(f)

    blob = base64.b64encode(
        gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), 6)
    ).decode()
    html = _render('"' + blob + '"')
    os.makedirs(os.path.dirname(os.path.abspath(out_html)), exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Multi-day floor sheet dashboard site")
    ap.add_argument("--archive", default="archive/parquet")
    ap.add_argument("--out", default="site")
    ap.add_argument("--rebuild", action="store_true",
                    help="regenerate every per-day file, not just the missing ones")
    ap.add_argument("--offline", default=None,
                    help="also write a self-contained html here, for mailing")
    ap.add_argument("--offline-days", type=int, default=22,
                    help="sessions to embed in the offline copy (0 = all)")
    ap.add_argument("--listed", default=sm.DEFAULT_PATH,
                    help="listed-securities csv behind the sector map")
    ap.add_argument("--fundamentals", default=fm.DEFAULT_PATH,
                    help="npstocks snapshot behind the Fundamentals tab")
    args = ap.parse_args(argv)

    info = build_site_data(args.archive, args.out, args.rebuild)
    write_sectors(args.out, args.listed)
    write_fundamentals(args.out, args.fundamentals, args.listed)
    build_app(args.out)
    if args.offline:
        p = build_offline(args.out, args.offline, args.offline_days)
        print(f"Offline copy: {p} ({os.path.getsize(p) / 1e6:.2f} MB, "
              f"{args.offline_days or info['n']} sessions)")
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(args.out) for f in fs)
    print(f"\nSite: {args.out}  ({info['n']} sessions, {total / 1e6:.1f} MB)")
    print(f"  index.html + data/index.json + data/panel.json + "
          f"data/day/*.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
