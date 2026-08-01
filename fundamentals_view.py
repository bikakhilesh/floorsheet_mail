#!/usr/bin/env python3
"""
fundamentals_view.py — the Fundamentals tab: css, markup and script.

The point of this tab is that the multiples are priced on the session you are
looking at, not on whenever npstocks last refreshed. Pick a day in the timeline
and every P/E on screen is that day's P/E.

**Live multiples scale the vendor's ratio; they are not recomputed from book
value.** That is not laziness, it is a correctness requirement. Checking the
dump, `PE (D)` reproduces `Latest Close / EPS (D)` to about 5e-4 across the
board — but `PBV` misses `Latest Close / Bookvalue` by more than 1% on 26 names,
and they are almost all insurers: IGI 1.24 against 2.39 computed, NLG 2.11
against 3.42, Nepal Life 6.94 against 5.95. The errors run both directions and
the P/E on those same rows is fine, so the close is not stale — npstocks is
using a different book basis for insurance than the Bookvalue column it
displays, which is what you would expect if it follows the NIA net-worth
definition. Recomputing from Bookvalue would therefore hand you a quietly wrong
P/B for the entire insurance sector.

Scaling sidesteps the question entirely:

    peLive  = peD * (price / vendorClose)
    pbvLive = pbv * (price / vendorClose)
    mcapLive = shares * price

Whatever earnings or book basis the vendor used is preserved; only the price
moves. Where the vendor field does reconcile, this is algebraically identical
to recomputing. Where it does not, this is the one that stays right.

`shares` comes from `Market Cap / Latest Close`, not from paidup capital — see
fundamentals.py for why.

The join is symbol-to-symbol against data/fundamentals.json, in the browser,
same as sectors. A name npstocks renames shows up as unmatched in the scrape
run, not as a silently missing row here.
"""

from __future__ import annotations

FUND_CSS = r"""
.fundbar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.stale{background:#FBF1D2;color:#7A5F02;padding:7px 10px;border-radius:4px;
 font-size:11.5px;margin:0 0 10px}
.stale b{font-weight:700}
td.rich{color:var(--sell)}td.cheap{color:var(--buy)}
.fundgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:880px){.fundgrid{grid-template-columns:1fr}}
"""

FUND_PANEL = r"""
  <div class="panel" id="p-fund">
    <div class="card">
      <h2>Valuation on the selected session</h2>
      <p class="note">Multiples are the vendor's own ratios re-priced onto this
        session's floor sheet — <code>PE x (price / vendor close)</code> — so the
        earnings and book basis behind them is whatever npstocks used, including
        the NIA net-worth basis it applies to insurers. Recomputing from the
        Bookvalue column instead would move every insurance P/B by tens of
        percent.</p>
      <div class="fundbar">
        <select class="f" id="fdPrice">
          <option value="vwap" selected>Price = session VWAP</option>
          <option value="last">Price = last contract</option>
          <option value="close">Price = vendor close (no re-pricing)</option>
        </select>
        <select class="f" id="fdMinT">
          <option value="0">All traded names</option>
          <option value="10" selected>Turnover ≥ Rs 10 L</option>
          <option value="100">Turnover ≥ Rs 1 Cr</option>
        </select>
        <input type="search" id="fdQ" placeholder="Filter symbol…">
        <span class="hint" id="fdHint"></span>
      </div>
      <div id="fdStale"></div>
      <div class="scroll"><table id="tFund"></table></div>
    </div>

    <div class="card">
      <h2>Sector valuation</h2>
      <p class="note">Market-cap weighted on the live price, with the median
        alongside — the weighted number tells you what the sector costs, the
        median tells you whether one name is carrying it. Only names that traded
        in this selection are included, so this is the valuation of what actually
        changed hands, not of the listed universe.</p>
      <div class="scroll" style="max-height:380px"><table id="tFundSec"></table></div>
    </div>

    <div class="fundgrid">
      <div class="card"><h2>Cheapest by live P/E</h2>
        <p class="note">Positive earnings only. Click for the scrip.</p>
        <div id="fdCheap"></div></div>
      <div class="card"><h2>Biggest move against the vendor close</h2>
        <p class="note">How far this session's price has travelled from the
          snapshot the fundamentals were priced at. A large gap means the
          multiple on the left is doing real work.</p>
        <div id="fdDrift"></div></div>
    </div>
  </div>
"""

FUND_JS = r"""
/* ---------- fundamentals ---------- */
let FUND=null, FUNDI=null;
const FDOPT={price:'vwap',minT:10};

async function fundLoad(){
  try{
    FUND = EMB ? (EMB.fundamentals||null)
               : await (await fetch('data/fundamentals.json')).json();
  }catch(e){ FUND=null; }
  if(FUND&&!FUND.sym)FUND=null;
  if(FUND){FUNDI={};FUND.cols.forEach((c,i)=>FUNDI[c]=i);}
  return FUND;
}
function fund(sym){
  if(!FUND)return null;
  const r=FUND.sym[sym]; if(!r)return null;
  const o={sym:sym,report:(FUND.report||{})[sym]||''};
  FUND.cols.forEach((c,i)=>o[c]=r[i]);
  return o;
}
/* The session price this scrip is being valued on. */
function fundPrice(s){
  if(FDOPT.price==='last')return s.last;
  if(FDOPT.price==='close'){const f=fund(s.sym);return f?f.close:null;}
  return s.vwap;
}
/* Scale, do not recompute — see the module docstring. */
function fundRow(s){
  const f=fund(s.sym); if(!f)return null;
  const p=fundPrice(s);
  const k=(f.close&&p&&f.close>0)?p/f.close:null;
  return Object.assign({},f,{
    price:p,
    drift:(k==null)?null:100*(k-1),
    peLive:(k==null||f.peD==null)?null:f.peD*k,
    pbvLive:(k==null||f.pbv==null)?null:f.pbv*k,
    mcapLive:(p==null||f.shares==null)?null:f.shares*p,
    turnover:s.turnover, sector:s.sector||'—'});
}
function fundRows(){
  if(!FUND)return [];
  const minT=FDOPT.minT*LAKH;
  const q=($('#fdQ')&&$('#fdQ').value||'').trim().toUpperCase();
  return SCRIPS.map(fundRow).filter(r=>r&&r.turnover>=minT&&
    (!q||r.sym.indexOf(q)>=0));
}

/* Market-cap weighted, with the median beside it. A weighted P/E is the honest
   "what does this sector cost" number; the median says whether one name is
   carrying the average. Negative and null earnings are dropped from both
   rather than clamped — a loss-making name has no P/E, it does not have a
   large one. */
function fundSectorAgg(rows){
  const m=new Map();
  rows.forEach(r=>{
    const g=r.sector||'—';
    let o=m.get(g);
    if(!o){o={g:g,n:0,mcap:0,wPE:0,wPEw:0,wPB:0,wPBw:0,wROE:0,wROEw:0,
              pes:[],pbs:[]};m.set(g,o);}
    o.n++;
    if(r.mcapLive>0)o.mcap+=r.mcapLive;
    const w=r.mcapLive>0?r.mcapLive:0;
    if(w&&r.peLive>0){o.wPE+=w*r.peLive;o.wPEw+=w;o.pes.push(r.peLive);}
    if(w&&r.pbvLive>0){o.wPB+=w*r.pbvLive;o.wPBw+=w;o.pbs.push(r.pbvLive);}
    if(w&&r.roeTTM!=null){o.wROE+=w*r.roeTTM;o.wROEw+=w;}
  });
  const med=a=>{if(!a.length)return null;const b=a.slice().sort((x,y)=>x-y);
    const i=b.length>>1;return b.length%2?b[i]:(b[i-1]+b[i])/2;};
  return [...m.values()].map(o=>({
    g:o.g,n:o.n,mcap:o.mcap,
    pe:o.wPEw?o.wPE/o.wPEw:null, peMed:med(o.pes),
    pb:o.wPBw?o.wPB/o.wPBw:null, pbMed:med(o.pbs),
    roe:o.wROEw?o.wROE/o.wROEw:null})).sort((a,b)=>b.mcap-a.mcap);
}

/* ---------- render ---------- */
function renderFund(){
  if(!FUND){
    $('#fdStale').innerHTML='<div class="stale"><b>No fundamentals.</b> '+
      'data/fundamentals.json was not published with this build — run '+
      '<code>fundamentals_scrape.py</code>, then rebuild the site.</div>';
    return;
  }
  const rows=fundRows();
  const covered=SCRIPS.filter(s=>fund(s.sym)).length;
  const missing=SCRIPS.filter(s=>!fund(s.sym)&&s.turnover>=10*LAKH);
  $('#fdHint').textContent=rows.length+' of '+SCRIPS.length+' traded names · '+
    covered+' with fundamentals';

  const notes=[];
  if(FUND.asof)notes.push('Snapshot as of <b>'+FUND.asof+'</b>'+
    (DATE?(' · viewing <b>'+(RANGE?RANGE[RANGE.length-1]:DATE)+'</b>'):''));
  if(missing.length)notes.push('<b>'+missing.length+'</b> name'+
    (missing.length===1?'':'s')+' traded above Rs 10 L with no fundamentals: '+
    missing.slice(0,10).map(s=>s.sym).join(', ')+(missing.length>10?' …':''));
  if((FUND.unmatched||[]).length)notes.push('<b>'+FUND.unmatched.length+
    '</b> vendor row(s) never resolved to a symbol — add them to '+
    '<code>reference/fundamentals_alias.csv</code>.');
  $('#fdStale').innerHTML=notes.length
    ? '<div class="stale">'+notes.join('<br>')+'</div>' : '';

  const n2=v=>v==null?'—':v.toFixed(2);
  const n1=v=>v==null?'—':v.toFixed(1);
  makeTable('tFund',[
    {k:'sym',h:'Scrip',f:r=>r.sym},
    {k:'sector',h:'Sector',f:r=>r.sector},
    {k:'report',h:'Report',f:r=>r.report||'—'},
    {k:'price',h:'Price',num:1,f:r=>n1(r.price)},
    {k:'drift',h:'vs close %',num:1,f:r=>r.drift==null?'—':
      (r.drift>=0?'+':'')+r.drift.toFixed(1),
     cls:r=>r.drift==null?'':(r.drift>=0?'pos':'neg')},
    {k:'mcapLive',h:'Mkt cap',num:1,sort:1,f:r=>npr(r.mcapLive,'')},
    {k:'peLive',h:'P/E',num:1,f:r=>n2(r.peLive)},
    {k:'pbvLive',h:'P/B',num:1,f:r=>n2(r.pbvLive)},
    {k:'epsD',h:'EPS (D)',num:1,f:r=>n2(r.epsD)},
    {k:'bvps',h:'BVPS',num:1,f:r=>n1(r.bvps)},
    {k:'roeTTM',h:'ROE %',num:1,f:r=>n1(r.roeTTM)},
    {k:'npmTTM',h:'Net margin %',num:1,f:r=>n1(r.npmTTM)},
    {k:'rsi',h:'RSI',num:1,f:r=>n1(r.rsi)},
    {k:'turnover',h:'Turnover',num:1,f:r=>npr(r.turnover,'')}],
    ()=>rows, s=>openScrip(s))();

  const sec=fundSectorAgg(rows);
  makeTable('tFundSec',[
    {k:'g',h:'Sector',f:r=>r.g},
    {k:'n',h:'Names',num:1,f:r=>r.n},
    {k:'mcap',h:'Mkt cap',num:1,sort:1,f:r=>npr(r.mcap,'')},
    {k:'pe',h:'P/E wtd',num:1,f:r=>n2(r.pe)},
    {k:'peMed',h:'P/E med',num:1,f:r=>n2(r.peMed)},
    {k:'pb',h:'P/B wtd',num:1,f:r=>n2(r.pb)},
    {k:'pbMed',h:'P/B med',num:1,f:r=>n2(r.pbMed)},
    {k:'roe',h:'ROE % wtd',num:1,f:r=>n1(r.roe)}],
    ()=>sec)();

  const cheap=rows.filter(r=>r.peLive>0).sort((a,b)=>a.peLive-b.peLive).slice(0,14);
  barList($('#fdCheap'),cheap.map(r=>({k:r.sym,v:1/r.peLive,
    t:r.peLive.toFixed(1)+'x · '+npr(r.turnover,''),
    click:"openScrip('"+r.sym+"')"})),{color:'var(--buy)'});

  const drift=rows.filter(r=>r.drift!=null)
    .sort((a,b)=>Math.abs(b.drift)-Math.abs(a.drift)).slice(0,14);
  barList($('#fdDrift'),drift.map(r=>({k:r.sym,v:r.drift,
    t:(r.drift>=0?'+':'')+r.drift.toFixed(1)+'%',
    click:"openScrip('"+r.sym+"')"})),{diverging:1});
}

/* ---------- fundamentals wiring ---------- */
['fdPrice','fdMinT'].forEach(id=>$('#'+id).addEventListener('change',()=>{
  FDOPT.price=$('#fdPrice').value;
  FDOPT.minT=+$('#fdMinT').value;
  renderFund();}));
$('#fdQ').addEventListener('input',renderFund);
"""
