#!/usr/bin/env python3
"""
sector_view.py — the Sectors tab of the dashboard: css, markup and script.

Kept out of dashboard_site.py so that file stays about plumbing (archive to
json to Pages) and this one stays about the analysis. `apply_sector_patch.py`
drops four placeholders into the APP template and dashboard_site.py fills them
from here.

What the tab does, and the reasoning behind each piece:

**The join happens in the browser, not in the parquet.**  Sector is looked up
from `data/sectors.json` at render time against the symbols already in the
cached per-day payloads. So when a new company lists, one 37 KB file changes and
every session back to the start of the archive re-maps on the next page load.
Nothing is recomputed and no cache is invalidated. Baking sector into the day
files would mean a full rebuild of the archive every time NEPSE reclassifies
something.

**Equity only by default.**  NEPSE files a bank's debenture and a bank-sponsored
mutual fund under "Commercial Banks". Aggregating the raw sector column books
that flow into banking, which is not what a sector number is supposed to mean.
`sector_map.py` splits non-equity out into its own buckets and this tab hides
them unless you ask.

**Sector performance is chain-linked from VWAP, not from an index.**  There is
no sector index in the floor sheet, but there is a volume-weighted average price
for every scrip every day. Daily scrip returns are turnover-weighted into a
sector return, then chain-linked. Only names present on both days count, so the
series is immune to mix shift when a scrip stops trading.

**Anything past the circuit is dropped, not clipped.**  NEPSE's daily band is
10%, so a VWAP-to-VWAP move beyond that is a bonus issue, a rights adjustment or
a single odd print — not a return. Those observations are removed from the day's
average and counted; the count is displayed, because a day where twenty names
were dropped is a day to read the sector move sceptically.

**Broker net by sector is a house-level proxy.**  Same caveat as everywhere else
in this dashboard: client identity is not disclosed, and offsetting client
orders net out inside a broker code. The broker x scrip cells feeding the matrix
are also trimmed at Rs 1 lakh gross by `interactive_report.MIN_PAIR_GROSS`, so
the matrix reads flow, not a balance sheet.
"""

from __future__ import annotations

# ────────────────────────────────────────────────────────────────────────────
SECTOR_CSS = r"""
.legend{display:flex;flex-wrap:wrap;gap:5px;margin:9px 0 2px}
.legend span{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;
 padding:2px 8px;border:1px solid var(--line);border-radius:11px;cursor:pointer;
 background:#fff;user-select:none}
.legend span:hover{border-color:var(--grey)}
.legend span.off{opacity:.32}
.legend i{width:9px;height:9px;border-radius:50%;display:inline-block;flex:0 0 auto}
.hm.bs td{width:auto;min-width:36px;height:22px}
.hm.bs td.bc{cursor:pointer;border-radius:2px}
.hm.bs th{white-space:nowrap;text-align:left}
.hm.bs th.v{writing-mode:vertical-rl;transform:rotate(180deg);height:96px;
 text-align:right}
.pill{display:inline-block;padding:1px 7px;border-radius:10px;font-size:10.5px;
 font-weight:600;background:var(--light);color:var(--navy)}
.warn{background:#FBF1D2;color:#7A5F02;padding:7px 10px;border-radius:4px;
 font-size:11.5px;margin:0 0 10px}
.warn b{font-weight:700}
svg.chart.tall{height:250px}
.secgrid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:880px){.secgrid{grid-template-columns:1fr}}
"""

# ────────────────────────────────────────────────────────────────────────────
SECTOR_PANEL = r"""
  <div class="panel" id="p-sectors">
    <div class="card">
      <h2>Sector composition</h2>
      <p class="note">Sectors come from NEPSE's listing table, joined to the floor
        sheet by symbol in the browser. Participation is scrips that traded against
        scrips listed and active. Breadth is the share of a sector's traded names
        that closed at or above their own VWAP. Promoter shares keep the parent's
        sector — <code>NABILP</code> sits in Commercial Banks — but they are a
        restricted instrument at a discount to the ordinary share, so the basis
        selector decides whether they count.</p>
      <div class="toolbar">
        <select class="f" id="secBasis">
          <option value="equity" selected>Equity only</option>
          <option value="equityprom">Equity + promoter</option>
          <option value="all">All instruments</option>
        </select>
        <select class="f" id="secMetric">
          <option value="turnover">Rank by turnover</option>
          <option value="volume">Rank by volume</option>
          <option value="trades">Rank by trades</option>
        </select>
        <span class="hint" id="secHint"></span>
      </div>
      <div id="secWarn"></div>
      <div class="scroll"><table id="tSector"></table></div>
    </div>

    <div class="secgrid">
      <div class="card"><h2>Where the money went</h2>
        <p class="note">Share of turnover in the current selection. Click for the
          sector detail.</p>
        <div id="secShareBars"></div></div>
      <div class="card"><h2>Rotation</h2>
        <p class="note">Change in turnover share against the preceding window of
          equal length. Right means the sector took share.</p>
        <div id="secDriftBars"></div></div>
    </div>

    <div class="card">
      <h2>Sector price performance</h2>
      <p class="note">Turnover-weighted VWAP returns, chain-linked, rebased to 100
        at the start of the window. Only names that traded on both days count, and
        moves beyond the 10% circuit are dropped as corporate actions rather than
        clipped. Click a legend chip to hide a series.</p>
      <div class="toolbar">
        <select class="f" id="secIdxWin">
          <option value="0">Full archive</option>
          <option value="63">Last 63 sessions</option>
          <option value="21" selected>Last 21 sessions</option>
          <option value="5">Last 5 sessions</option>
        </select>
        <select class="f" id="secIdxN">
          <option value="6">Top 6 sectors</option>
          <option value="10" selected>Top 10 sectors</option>
          <option value="99">All sectors</option>
        </select>
        <span class="hint" id="secIdxHint"></span>
      </div>
      <div id="secIdxChart"></div>
      <div class="legend" id="secIdxLegend"></div>
      <h3 class="sec">Returns and relative strength</h3>
      <div class="scroll" style="max-height:340px"><table id="tSecRet"></table></div>
    </div>

    <div class="secgrid">
      <div class="card"><h2>Turnover share over time</h2>
        <p class="note">Every session in the archive, normalised to 100%.
          Hover a band for the day.</p>
        <div id="secStack"></div>
        <div class="legend" id="secStackLegend"></div></div>
      <div class="card"><h2>Breadth and dispersion</h2>
        <p class="note">Share of each sector's traded names closing at or above
          VWAP, with the average intraday range alongside.</p>
        <div id="secBreadth"></div></div>
    </div>

    <div class="card">
      <h2>Broker net flow by sector</h2>
      <p class="note">Buy minus sell for the current selection. Green is
        accumulation, red distribution. Cells come from the broker x scrip
        breakdown, which is trimmed at Rs 1 lakh gross, so small positions are
        absent. Click a cell to open the broker.</p>
      <div class="toolbar">
        <select class="f" id="secBrokerN">
          <option value="12">Top 12 brokers</option>
          <option value="20" selected>Top 20 brokers</option>
          <option value="30">Top 30 brokers</option>
        </select>
        <span class="hint">Colour is square-root scaled on the largest cell.</span>
      </div>
      <div style="overflow:auto"><table class="hm bs" id="tSecBroker"></table></div>
    </div>
  </div>
"""

# ────────────────────────────────────────────────────────────────────────────
SECTOR_JS = r"""
/* ---------- sectors ---------- */
let SEC=null, SECPAN=null, SECRET=null;
const SECOPT={basis:'equity',metric:'turnover'};
const SECHIDE=new Set();
const SECPAL=['#0B2545','#C9A227','#1B7F4C','#B02A2A','#3D6EA8','#6B4C9A',
  '#2F8F9D','#C2703D','#4F6D2E','#9A3B5C','#7A5F02','#5E8C6A','#8A6BB1','#A0522D'];
const MKT='__MKT__';
const PROM='Promoter Share';
const MAXRET=0.10;   /* NEPSE daily circuit — past this it is a corporate action */
const MINTL=1.0;     /* lakh; a name has to trade this much on both days to count */

async function secLoad(){
  try{
    SEC = EMB ? (EMB.sectors||null)
              : await (await fetch('data/sectors.json')).json();
  }catch(e){ SEC=null; }
  if(SEC&&!SEC.sym)SEC=null;
  return SEC;
}
function secInfo(sym){
  if(!SEC)return null;
  const r=SEC.sym[sym]; if(!r)return null;
  return {sector:SEC.sectors[r[0]],group:SEC.groups[r[1]],
          inst:SEC.instruments[r[2]],status:SEC.statuses[r[3]],
          name:(SEC.name||{})[sym]||''};
}
/* basis-independent, for the Scrips tab column */
function secGroupAll(sym){const i=secInfo(sym);return i?i.group:'Unmapped';}
function secIsProm(sym){const i=secInfo(sym);return !!i&&i.inst===PROM;}
/* Does this instrument survive the current basis?
   equity      ordinary shares only
   equityprom  ordinary plus the promoter register — same sector either way,
               so NABIL and NABILP both land in Commercial Banks
   all         debentures, funds and preference shares as well, each in its own
               bucket because those are separate asset classes wearing the
               issuer's sector as a label */
function secKeeps(inst){
  if(SECOPT.basis==='all')return true;
  if(SECOPT.basis==='equityprom')return inst==='Equity'||inst===PROM;
  return inst==='Equity';
}
/* null means "excluded by the current basis" */
function secOf(sym){
  const i=secInfo(sym);
  if(!i)return 'Unmapped';
  return secKeeps(i.inst)?i.group:null;
}
function secColor(g){
  if(g==='Unmapped')return '#8A94A6';
  if(g===MKT)return '#1C2331';
  const i=SEC?SEC.groups.indexOf(g):-1;
  return SECPAL[(i<0?0:i)%SECPAL.length];
}
function secListedCounts(){
  const c={};
  if(!SEC)return c;
  Object.keys(SEC.sym).forEach(s=>{
    const r=SEC.sym[s];
    if(SEC.statuses[r[3]]!=='Active')return;
    if(!secKeeps(SEC.instruments[r[2]]))return;
    const g=SEC.groups[r[1]]; c[g]=(c[g]||0)+1;});
  return c;
}

/* ---------- selection-level aggregate ---------- */
function secAgg(){
  const m=new Map();
  SCRIPS.forEach(s=>{
    const g=secOf(s.sym); if(g===null)return;
    let o=m.get(g);
    if(!o){o={g:g,turnover:0,volume:0,trades:0,n:0,above:0,rng:0,tops:[],
              prom:0,nProm:0};
           m.set(g,o);}
    o.turnover+=s.turnover; o.volume+=s.volume; o.trades+=s.trades; o.n++;
    if(s.vwap>0&&s.last>=s.vwap)o.above++;
    /* Tracked even when promoter shares are excluded, so the column can say 0
       rather than go missing. A sector that is a third promoter turnover is a
       sector whose "flow" is mostly register transfers, and that should be
       visible on the face of the table. */
    if(secIsProm(s.sym)){o.prom+=s.turnover; o.nProm++;}
    o.rng+=s.rangePct; o.tops.push([s.sym,s.turnover]);
  });
  const tot=[...m.values()].reduce((a,o)=>a+o.turnover,0)||1;
  const listed=secListedCounts();
  const key=SECOPT.metric;
  return [...m.values()].map(o=>{
    o.tops.sort((a,b)=>b[1]-a[1]);
    o.top3=100*o.tops.slice(0,3).reduce((a,r)=>a+r[1],0)/(o.turnover||1);
    o.share=100*o.turnover/tot;
    o.listed=listed[o.g]||0;
    o.part=o.listed?100*o.n/o.listed:0;
    o.breadth=o.n?100*o.above/o.n:0;
    o.avgTicket=o.trades?o.turnover/o.trades:0;
    o.rangeAvg=o.n?o.rng/o.n:0;
    o.promPct=o.turnover?100*o.prom/o.turnover:0;
    return o;}).sort((a,b)=>b[key]-a[key]);
}
function secUnmapped(){
  return SCRIPS.filter(s=>!secInfo(s.sym));
}

/* ---------- archive-wide series off panel.json ---------- */
function secPanel(){
  if(SECPAN&&SECPAN.basis===SECOPT.basis)return SECPAN;
  const D=PANEL.dates, byG={};
  Object.keys(PANEL.scrips).forEach(sym=>{
    const g=secOf(sym); if(g===null)return;
    const t=PANEL.scrips[sym][0];
    let a=byG[g]; if(!a)a=byG[g]=new Array(D.length).fill(0);
    for(let j=0;j<D.length;j++) if(t[j]!=null) a[j]+=t[j];
  });
  SECPAN={basis:SECOPT.basis,dates:D,byG:byG};
  return SECPAN;
}

/* Chain-linked, turnover-weighted VWAP return index. See the module docstring
   for why moves past the circuit are dropped rather than winsorised. */
function secReturns(){
  if(SECRET&&SECRET.basis===SECOPT.basis)return SECRET;
  const D=PANEL.dates, n=D.length, acc={}, dropped=new Array(n).fill(0);
  function add(g,j,w,r){
    let a=acc[g];
    if(!a)a=acc[g]={w:new Array(n).fill(0),wr:new Array(n).fill(0),
                    c:new Array(n).fill(0)};
    a.w[j]+=w; a.wr[j]+=w*r; a.c[j]++;
  }
  Object.keys(PANEL.scrips).forEach(sym=>{
    const g=secOf(sym); if(g===null)return;
    const t=PANEL.scrips[sym][0], v=PANEL.scrips[sym][1];
    for(let j=1;j<n;j++){
      const v0=v[j-1], v1=v[j], t0=t[j-1], t1=t[j];
      if(v0==null||v1==null||v0<=0||v1<=0)continue;
      if(t0==null||t1==null||t0<MINTL||t1<MINTL)continue;
      const r=v1/v0-1;
      if(Math.abs(r)>MAXRET){dropped[j]++;continue;}
      add(g,j,t1,r); add(MKT,j,t1,r);
    }
  });
  const out={};
  Object.keys(acc).forEach(g=>{
    const a=acc[g], ret=new Array(n).fill(null), idx=new Array(n).fill(100);
    let lvl=100;
    for(let j=1;j<n;j++){
      if(a.c[j]>0&&a.w[j]>0){ret[j]=a.wr[j]/a.w[j]; lvl*=(1+ret[j]);}
      idx[j]=lvl;
    }
    out[g]={ret:ret,idx:idx,c:a.c};
  });
  SECRET={basis:SECOPT.basis,dates:D,g:out,dropped:dropped};
  return SECRET;
}
function secRet(g,end,k){
  const R=secReturns().g[g]; if(!R)return null;
  const a=Math.max(0,end-k);
  if(a===end||!R.idx[a])return null;
  return R.idx[end]/R.idx[a]-1;
}

/* Turnover share now against the preceding window of the same length. */
function secDrift(){
  const P=secPanel(), D=P.dates;
  const sel=RANGE?RANGE:[DATE];
  const i0=D.indexOf(sel[0]), i1=D.indexOf(sel[sel.length-1]);
  if(i0<0||i1<0)return null;
  const k=i1-i0+1, p1=i0-1, p0=Math.max(0,i0-k);
  if(p1<p0)return null;
  const gs=Object.keys(P.byG);
  const sum=(g,a,b)=>{const arr=P.byG[g];let s=0;for(let j=a;j<=b;j++)s+=arr[j]||0;
                      return s;};
  const cur={},prv={};
  let ct=0,pt=0;
  gs.forEach(g=>{cur[g]=sum(g,i0,i1);prv[g]=sum(g,p0,p1);ct+=cur[g];pt+=prv[g];});
  if(!ct||!pt)return null;
  const out={};
  gs.forEach(g=>{out[g]=100*cur[g]/ct-100*prv[g]/pt;});
  out.__window__=p1-p0+1;
  return out;
}

/* ---------- charts ---------- */
function multiLine(labels,series,o){
  o=o||{};
  const W=1180,H=250,P={l:54,r:10,t:10,b:24};
  const vis=series.filter(s=>!SECHIDE.has(s.name));
  const all=[];
  vis.forEach(s=>{for(let i=0;i<s.vals.length;i++)
    if(s.vals[i]!=null)all.push(s.vals[i]);});
  if(!all.length)return '<div class="empty">Not enough overlapping sessions.</div>';
  let mn=all[0],mx=all[0];
  for(let i=1;i<all.length;i++){if(all[i]<mn)mn=all[i];if(all[i]>mx)mx=all[i];}
  if(o.base!=null){mn=Math.min(mn,o.base);mx=Math.max(mx,o.base);}
  if(mn===mx){mn-=1;mx+=1;}
  const pad=(mx-mn)*0.06; mn-=pad; mx+=pad;
  const x=i=>P.l+(W-P.l-P.r)*(labels.length<2?0.5:i/(labels.length-1));
  const y=v=>P.t+(H-P.t-P.b)*(1-(v-mn)/(mx-mn));
  const ticks=[mn,(mn+mx)/2,mx].map(v=>
    '<line class="gl" x1="'+P.l+'" y1="'+y(v).toFixed(1)+'" x2="'+(W-P.r)+
    '" y2="'+y(v).toFixed(1)+'"/><text x="'+(P.l-5)+'" y="'+(y(v)+3).toFixed(1)+
    '" text-anchor="end">'+v.toFixed(1)+'</text>').join('');
  const base=o.base!=null?'<line class="ax" x1="'+P.l+'" y1="'+y(o.base).toFixed(1)+
    '" x2="'+(W-P.r)+'" y2="'+y(o.base).toFixed(1)+'" stroke-dasharray="3 3"/>':'';
  const paths=vis.map(s=>{
    let d='',open=false;
    for(let i=0;i<s.vals.length;i++){
      const v=s.vals[i];
      if(v==null){open=false;continue;}
      d+=(open?'L':'M')+x(i).toFixed(1)+' '+y(v).toFixed(1)+' ';
      open=true;
    }
    return d?'<path d="'+d+'" fill="none" stroke="'+s.color+'" stroke-width="'+
      (s.name===MKT?'2.4':'1.8')+'"'+(s.name===MKT?' stroke-dasharray="5 3"':'')+
      '><title>'+(s.name===MKT?'Market':s.name)+'</title></path>':'';
  }).join('');
  const step=Math.max(1,Math.ceil(labels.length/10));
  const xl=labels.map((l,i)=>i%step?'':'<text x="'+x(i).toFixed(1)+'" y="'+(H-6)+
    '" text-anchor="middle">'+l.slice(5)+'</text>').join('');
  const mk=o.mark!=null&&o.mark>=0?'<line class="ax" x1="'+x(o.mark).toFixed(1)+
    '" y1="'+P.t+'" x2="'+x(o.mark).toFixed(1)+'" y2="'+(H-P.b)+
    '" stroke="var(--gold)" stroke-dasharray="3 3"/>':'';
  return '<svg class="chart tall" viewBox="0 0 '+W+' '+H+'">'+ticks+base+mk+
         paths+xl+'</svg>';
}

function stackArea(labels,series,markIdx){
  const W=1180,H=210,P={l:34,r:8,t:8,b:22};
  const n=labels.length;
  series=series.filter(s=>s&&s.vals&&s.vals.length);
  if(!n||!series.length)return '<div class="empty">No data.</div>';
  const tot=new Array(n).fill(0);
  series.forEach(s=>{for(let j=0;j<n;j++)tot[j]+=s.vals[j]||0;});
  const x=j=>P.l+(W-P.l-P.r)*(n<2?0.5:j/(n-1));
  const y=f=>P.t+(H-P.t-P.b)*(1-f);
  const base=new Array(n).fill(0);
  const bands=series.map(s=>{
    const lo=base.slice();
    for(let j=0;j<n;j++)base[j]+=tot[j]?(s.vals[j]||0)/tot[j]:0;
    let d='M'+x(0).toFixed(1)+' '+y(lo[0]).toFixed(1);
    for(let j=1;j<n;j++)d+='L'+x(j).toFixed(1)+' '+y(lo[j]).toFixed(1);
    for(let j=n-1;j>=0;j--)d+='L'+x(j).toFixed(1)+' '+y(base[j]).toFixed(1);
    return '<path d="'+d+'Z" fill="'+s.color+'" opacity=".88"><title>'+s.name+
           '</title></path>';
  }).join('');
  const ticks=[0,.5,1].map(f=>'<line class="gl" x1="'+P.l+'" y1="'+y(f).toFixed(1)+
    '" x2="'+(W-P.r)+'" y2="'+y(f).toFixed(1)+'"/><text x="'+(P.l-5)+'" y="'+
    (y(f)+3).toFixed(1)+'" text-anchor="end">'+(f*100)+'</text>').join('');
  const step=Math.max(1,Math.ceil(n/10));
  const xl=labels.map((l,j)=>j%step?'':'<text x="'+x(j).toFixed(1)+'" y="'+(H-6)+
    '" text-anchor="middle">'+l.slice(5)+'</text>').join('');
  const mk=markIdx>=0?'<line x1="'+x(markIdx).toFixed(1)+'" y1="'+P.t+'" x2="'+
    x(markIdx).toFixed(1)+'" y2="'+(H-P.b)+'" stroke="var(--gold)" '+
    'stroke-width="1.5" stroke-dasharray="3 3"/>':'';
  return '<svg class="chart" viewBox="0 0 '+W+' '+H+'">'+bands+ticks+mk+xl+'</svg>';
}

function secLegend(el,names,onToggle){
  el.innerHTML=names.map(g=>'<span class="'+(SECHIDE.has(g)?'off':'')+
    '" data-g="'+g+'"><i style="background:'+secColor(g)+'"></i>'+
    (g===MKT?'Market':g)+'</span>').join('');
  if(onToggle)el.querySelectorAll('span').forEach(s=>s.onclick=()=>{
    const g=s.dataset.g;
    if(SECHIDE.has(g))SECHIDE.delete(g);else SECHIDE.add(g);
    onToggle();});
}

/* ---------- render ---------- */
function secFillScripFilter(){
  const el=$('#fScripSector'); if(!el||!SEC)return;
  const cur=el.value;
  const gs=[...new Set(SCRIPS.map(s=>s.sector).filter(Boolean))].sort();
  el.innerHTML='<option value="">All sectors</option>'+
    gs.map(g=>'<option value="'+g+'">'+g+'</option>').join('');
  if(gs.indexOf(cur)>=0)el.value=cur;
}

async function renderSectors(){
  if(!SEC){
    $('#secWarn').innerHTML='<div class="warn"><b>No sector map.</b> '+
      'data/sectors.json was not published with this build — run '+
      '<code>get_listed_securities.py</code>, then rebuild the site.</div>';
    return;
  }
  await ensurePanel();

  const A=secAgg(), tot=A.reduce((a,o)=>a+o.turnover,0)||1;
  const un=secUnmapped();
  if(!A.length){
    $('#secHint').textContent='';
    $('#secWarn').innerHTML='<div class="warn">Nothing in this selection maps to '+
      'a sector on the current basis.</div>';
    ['tSector','tSecRet','tSecBroker'].forEach(id=>{$('#'+id).innerHTML='';});
    ['secShareBars','secDriftBars','secIdxChart','secIdxLegend','secStack',
     'secStackLegend','secBreadth'].forEach(id=>{$('#'+id).innerHTML='';});
    return;
  }
  $('#secHint').textContent=A.length+' sector'+(A.length===1?'':'s')+' · '+
    A.reduce((a,o)=>a+o.n,0)+' scrips · '+npr(tot);
  $('#secWarn').innerHTML = un.length
    ? '<div class="warn"><b>'+un.length+' symbol'+(un.length===1?'':'s')+
      '</b> traded but are not in the listing table: '+
      un.slice(0,12).map(s=>s.sym).join(', ')+
      (un.length>12?' …':'')+'. They are bucketed as Unmapped. Run the '+
      '<code>nepse-listed</code> workflow, or add them to '+
      '<code>reference/sector_overrides.csv</code>.</div>'
    : '';

  const secCols=[
    {k:'g',h:'Sector',f:r=>r.g},
    {k:'turnover',h:'Turnover',num:1,sort:1,f:r=>npr(r.turnover,'')},
    {k:'share',h:'Share %',num:1,f:r=>r.share.toFixed(1)},
    {k:'volume',h:'Volume',num:1,f:r=>num(r.volume)},
    {k:'trades',h:'Trades',num:1,f:r=>num(r.trades)},
    {k:'n',h:'Traded',num:1,f:r=>r.n},
    {k:'listed',h:'Listed',num:1,f:r=>r.listed||'—'},
    {k:'part',h:'Particip %',num:1,f:r=>r.listed?r.part.toFixed(0):'—'},
    {k:'avgTicket',h:'Avg ticket',num:1,f:r=>npr(r.avgTicket,'')},
    {k:'top3',h:'Top-3 %',num:1,f:r=>r.top3.toFixed(0)},
    {k:'breadth',h:'Above VWAP %',num:1,f:r=>r.breadth.toFixed(0),
     cls:r=>r.breadth>=50?'pos':'neg'}];
  /* Only worth a column once promoter shares are actually in the numbers —
     under the equity basis it would be a column of zeroes. */
  if(SECOPT.basis!=='equity')
    secCols.splice(3,0,{k:'promPct',h:'Promoter %',num:1,
      f:r=>r.nProm?r.promPct.toFixed(1):'—'});
  makeTable('tSector',secCols,()=>A, g=>openSector(g))();

  barList($('#secShareBars'),A.slice(0,14).map(o=>({k:o.g,v:o.turnover,
    t:npr(o.turnover,'')+' · '+o.share.toFixed(1)+'%',
    click:"openSector('"+o.g.replace(/'/g,"\\'")+"')"})));

  const dr=secDrift();
  if(!dr){
    $('#secDriftBars').innerHTML='<div class="empty">No earlier window of the '+
      'same length to compare against.</div>';
  }else{
    const rows=A.map(o=>({k:o.g,v:dr[o.g]||0,
      t:(dr[o.g]>=0?'+':'')+(dr[o.g]||0).toFixed(2)+' pp',
      click:"openSector('"+o.g.replace(/'/g,"\\'")+"')"}))
      .sort((a,b)=>b.v-a.v);
    barList($('#secDriftBars'),rows,{diverging:1});
  }

  secDrawIndex();
  secDrawStack();

  barList($('#secBreadth'),A.slice(0,14).map(o=>({k:o.g,v:o.breadth,
    t:o.breadth.toFixed(0)+'% · range '+o.rangeAvg.toFixed(1)+'%',
    click:"openSector('"+o.g.replace(/'/g,"\\'")+"')"})),{color:'var(--buy)'});

  secDrawBrokerMatrix();
}

function secDrawIndex(){
  const R=secReturns(), D=R.dates;
  const win=+$('#secIdxWin').value, topN=+$('#secIdxN').value;
  const i0=win>0?Math.max(0,D.length-win-1):0;
  const labels=D.slice(i0);
  const A=secAgg();
  const names=A.slice(0,topN).map(o=>o.g).filter(g=>R.g[g]);
  const series=names.concat([MKT]).map(g=>{
    const idx=R.g[g].idx, b=idx[i0]||100;
    return {name:g,color:secColor(g),
            vals:idx.slice(i0).map(v=>v==null?null:100*v/b)};
  });
  $('#secIdxChart').innerHTML=multiLine(labels,series,
    {base:100,mark:D.indexOf(DATE)-i0});
  secLegend($('#secIdxLegend'),names.concat([MKT]),secDrawIndex);

  const drop=R.dropped.slice(i0).reduce((a,b)=>a+b,0);
  $('#secIdxHint').textContent=labels.length+' sessions · '+drop+
    ' scrip-day move'+(drop===1?'':'s')+' past the 10% circuit dropped';

  /* NA sorts to the bottom rather than poisoning the numeric compare in
     makeTable, so the sentinel goes in the data and the formatter reads it. */
  const NA=-9;
  const nz=v=>v==null?NA:v;
  const e=D.length-1;
  const rows=names.map(g=>{
    const a21=secRet(g,e,21), m21=secRet(MKT,e,21);
    return {g:g, r1:nz(secRet(g,e,1)), r5:nz(secRet(g,e,5)), r21:nz(a21),
            rw:nz(secRet(g,e,labels.length-1)),
            rs:nz((a21==null||m21==null)?null:(1+a21)/(1+m21)-1)};});
  const fp=v=>v<=NA?'—':(v>=0?'+':'')+(100*v).toFixed(2);
  const cl=v=>v<=NA?'':(v>=0?'pos':'neg');
  makeTable('tSecRet',[
    {k:'g',h:'Sector',f:r=>r.g},
    {k:'r1',h:'1d %',num:1,f:r=>fp(r.r1),cls:r=>cl(r.r1)},
    {k:'r5',h:'5d %',num:1,f:r=>fp(r.r5),cls:r=>cl(r.r5)},
    {k:'r21',h:'21d %',num:1,sort:1,f:r=>fp(r.r21),cls:r=>cl(r.r21)},
    {k:'rw',h:'Window %',num:1,f:r=>fp(r.rw),cls:r=>cl(r.rw)},
    {k:'rs',h:'RS 21d %',num:1,f:r=>fp(r.rs),cls:r=>cl(r.rs)}],
    ()=>rows, g=>openSector(g))();
}

function secDrawStack(){
  const P=secPanel(), A=secAgg();
  /* A sector can exist in today's selection and not in panel.json — a name that
     started trading after the panel was last written, or one the map has but the
     archive does not. Take the intersection rather than trusting either side. */
  const names=A.slice(0,9).map(o=>o.g).filter(g=>P.byG[g]);
  const rest=Object.keys(P.byG).filter(g=>names.indexOf(g)<0);
  const series=names.map(g=>({name:g,color:secColor(g),vals:P.byG[g]}));
  if(rest.length){
    const other=new Array(P.dates.length).fill(0);
    rest.forEach(g=>{for(let j=0;j<other.length;j++)other[j]+=P.byG[g][j]||0;});
    series.push({name:'Other',color:'#C7CEDB',vals:other});
  }
  $('#secStack').innerHTML=stackArea(P.dates,series,P.dates.indexOf(DATE));
  secLegend($('#secStackLegend'),series.map(s=>s.name),null);
}

function secDrawBrokerMatrix(){
  const A=secAgg(), groups=A.slice(0,12).map(o=>o.g);
  const n=+$('#secBrokerN').value;
  const top=BROKERS.slice(0,n).map(b=>b.code);
  const gi={}; groups.forEach((g,i)=>gi[g]=i);
  const bi={}; top.forEach((c,i)=>bi[c]=i);
  const m=top.map(()=>groups.map(()=>0));
  BSCRIP.forEach(r=>{
    const g=secOf(r.sym);
    if(g===null||gi[g]===undefined||bi[r.broker]===undefined)return;
    m[bi[r.broker]][gi[g]]+=r.buy-r.sell;});
  let mxa=1;
  m.forEach(row=>row.forEach(v=>{if(Math.abs(v)>mxa)mxa=Math.abs(v);}));
  const shade=v=>{
    if(!v)return '#fff';
    const t=Math.sqrt(Math.min(1,Math.abs(v)/mxa));
    return v>0?'rgba(27,127,76,'+(0.08+0.82*t).toFixed(3)+')'
              :'rgba(176,42,42,'+(0.08+0.82*t).toFixed(3)+')';};
  let h='<thead><tr><th></th>'+groups.map(g=>'<th class="v">'+g+'</th>').join('')+
        '<th class="v">Net total</th></tr></thead><tbody>';
  top.forEach((c,i)=>{
    const tot=m[i].reduce((a,b)=>a+b,0);
    h+='<tr><th>'+bname(c)+'</th>';
    groups.forEach((g,j)=>{
      h+='<td class="bc" style="background:'+shade(m[i][j])+'" data-v="'+m[i][j]+
         '" data-b="'+c+'" data-g="'+g+'"></td>';});
    h+='<td class="num" style="font-weight:600;color:'+
       (tot>=0?'var(--buy)':'var(--sell)')+'">'+npr(tot,'')+'</td></tr>';});
  const el=$('#tSecBroker');
  el.innerHTML=h+'</tbody>';
  el.querySelectorAll('td.bc').forEach(td=>{
    td.onmousemove=e=>showTip(e,'<b>'+bname(td.dataset.b)+'</b> · '+
      td.dataset.g+'<br>net '+npr(+td.dataset.v));
    td.onmouseleave=hideTip;
    td.onclick=()=>{hideTip();openBroker(+td.dataset.b);};});
}

/* ---------- sector drawer ---------- */
function openSector(g){
  if(!SEC)return;
  const A=secAgg(), o=A.find(x=>x.g===g); if(!o)return;
  $('#dTitle').textContent=g;
  const mine=SCRIPS.filter(s=>secOf(s.sym)===g);
  const net=new Map();
  BSCRIP.forEach(r=>{if(secOf(r.sym)!==g)return;
    net.set(r.broker,(net.get(r.broker)||0)+r.buy-r.sell);});
  const nb=[...net.entries()].map(([c,v])=>({c:c,v:v})).sort((a,b)=>b.v-a.v);
  const R=secReturns().g[g], e=secReturns().dates.length-1;
  const fp=v=>v==null?'—':(v>=0?'+':'')+(100*v).toFixed(2)+'%';

  $('#dBody').innerHTML='<div class="stat">'+
    [['Turnover',npr(o.turnover)],['Share',o.share.toFixed(1)+'%'],
     ['Volume',num(o.volume)],['Trades',num(o.trades)],
     ['Traded / listed',o.n+' / '+(o.listed||'—')],
     ['Above VWAP',o.breadth.toFixed(0)+'%'],
     ['Promoter',o.nProm?o.promPct.toFixed(1)+'% of turnover':'—'],
     ['1d',fp(secRet(g,e,1))],['21d',fp(secRet(g,e,21))]]
    .map(([l,v])=>'<div>'+l+'<b>'+v+'</b></div>').join('')+'</div>'+
    '<h3 class="sec">Scrips by turnover</h3><div id="dSecScrips"></div>'+
    '<h3 class="sec">Net buyers and sellers in the sector</h3><div id="dSecBr"></div>'+
    '<h3 class="sec">Chain-linked VWAP index across the archive</h3>'+
    '<div id="dSecIdx"></div>';

  barList($('#dSecScrips'),mine.sort((a,b)=>b.turnover-a.turnover).slice(0,14)
    .map(s=>({k:s.sym,v:s.turnover,t:npr(s.turnover,''),
      click:"openScrip('"+s.sym+"')"})));
  barList($('#dSecBr'),nb.slice(0,7).concat(nb.slice(-7))
    .filter((v,i,a)=>a.indexOf(v)===i)
    .map(r=>({k:bname(r.c),v:r.v,t:npr(r.v,''),click:'openBroker('+r.c+')'})),
    {diverging:1});
  if(R)$('#dSecIdx').innerHTML=lineChart(secReturns().dates,R.idx,
    {unit:'',mark:secReturns().dates.indexOf(DATE)});
  drawer.classList.add('on'); drawer.scrollTop=0;
}

/* ---------- sector wiring ---------- */
['secBasis','secMetric'].forEach(id=>$('#'+id).addEventListener('change',()=>{
  SECOPT.basis=$('#secBasis').value;
  SECOPT.metric=$('#secMetric').value;
  SECHIDE.clear();
  renderSectors();}));
['secIdxWin','secIdxN'].forEach(id=>
  $('#'+id).addEventListener('change',()=>{SECHIDE.clear();secDrawIndex();}));
$('#secBrokerN').addEventListener('change',secDrawBrokerMatrix);
"""
