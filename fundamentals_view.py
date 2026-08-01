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
svg.bub{width:100%;height:420px;display:block;overflow:visible}
svg.bub .gl{stroke:#EDF1F7;stroke-width:1}
svg.bub .ax{stroke:#C7CEDB;stroke-width:1}
svg.bub .iso{stroke:#C7CEDB;stroke-width:1;stroke-dasharray:4 3;fill:none}
svg.bub .isolab{font-size:9.5px;fill:var(--grey)}
svg.bub .ols{stroke:var(--navy);stroke-width:1.8;stroke-dasharray:6 3}
svg.bub text{font-size:10px;fill:var(--grey)}
svg.bub .atitle{font-size:11px;fill:var(--ink);font-weight:600}
svg.bub circle{cursor:pointer;stroke:#fff;stroke-width:.8}
svg.bub circle:hover{stroke:var(--ink);stroke-width:1.6}
svg.bub .plab{font-size:9px;fill:var(--ink);pointer-events:none}
.fitline{font-size:11.5px;color:var(--grey);margin:6px 0 0 2px}
.fitline b{color:var(--navy)}
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

    <div class="card">
      <h2>Valuation and returns</h2>
      <p class="note">Bubble area is live market cap, colour is sector. Both of
        these pairs are related by an identity, not by a correlation —
        <code>ROE = ROA × equity multiplier</code> and
        <code>P/B = P/E × ROE</code> — so the dashed rays are exact iso-lines,
        not fitted. Where a name sits between two rays <em>is</em> its leverage,
        or its ROE. A least-squares line through either is available, but it is
        fitting scatter around an identity; its residual is a relative-value
        read, not a relationship.</p>
      <div class="fundbar">
        <select class="f" id="fdFit">
          <option value="iso" selected>Overlay: iso-lines (exact)</option>
          <option value="ols">Overlay: least squares</option>
          <option value="none">Overlay: none</option>
        </select>
        <select class="f" id="fdScale">
          <option value="log" selected>Log axes</option>
          <option value="linear">Linear axes</option>
        </select>
        <select class="f" id="fdLabel">
          <option value="20" selected>Label top 20 by size</option>
          <option value="0">No labels</option>
          <option value="999">Label everything</option>
        </select>
        <span class="hint">Click a bubble to open the scrip.</span>
      </div>
      <div class="fundgrid">
        <div>
          <div id="fdPEPB"></div>
          <div class="fitline" id="fdPEPBFit"></div>
        </div>
        <div>
          <div id="fdROE"></div>
          <div class="fitline" id="fdROEFit"></div>
        </div>
      </div>
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

/* ---------- bubble chart ---------- */
/* Ordinary least squares. Returned in whatever space it was given, so the
   caller fits in log space when the axes are logarithmic — a straight line on
   a log-log plot is a power law, which is the right family for multiples. */
function ols(pts){
  const n=pts.length;
  if(n<3)return null;
  let sx=0,sy=0;
  pts.forEach(p=>{sx+=p.x;sy+=p.y;});
  const mx=sx/n,my=sy/n;
  let sxy=0,sxx=0,syy=0;
  pts.forEach(p=>{const dx=p.x-mx,dy=p.y-my;sxy+=dx*dy;sxx+=dx*dx;syy+=dy*dy;});
  if(sxx<=0)return null;
  const b=sxy/sxx, a=my-b*mx;
  const r2=(syy>0)?(sxy*sxy)/(sxx*syy):0;
  return {a:a,b:b,r2:r2,n:n};
}
/* Percentile bound. Multiples are long-tailed — one 400x P/E flattens every
   other point into a corner — so the axes are trimmed and the strays are drawn
   pinned to the edge rather than dropped, which would hide them entirely. */
function pctl(vals,q){
  if(!vals.length)return null;
  const a=vals.slice().sort((x,y)=>x-y);
  const i=Math.min(a.length-1,Math.max(0,Math.round(q*(a.length-1))));
  return a[i];
}

/* pts: {x,y,r,label,color,tip}. `iso` draws y = k*x rays, which is exact for
   both pairs on this tab. `fit` overlays OLS instead. */
function bubbleChart(pts,o){
  o=o||{};
  const W=580,H=420,P={l:52,r:16,t:26,b:46};
  const log=!!o.log;
  const good=pts.filter(p=>p.x!=null&&p.y!=null&&isFinite(p.x)&&isFinite(p.y)&&
    (!log||(p.x>0&&p.y>0)));
  if(good.length<2)return {svg:'<div class="empty">Not enough priced names.</div>',
                           fit:null,n:0,off:0};

  const xs=good.map(p=>p.x), ys=good.map(p=>p.y);
  let x0=pctl(xs,0.02), x1=pctl(xs,0.98), y0=pctl(ys,0.02), y1=pctl(ys,0.98);
  if(log){x0=Math.max(x0,1e-3);y0=Math.max(y0,1e-3);}
  if(!(x1>x0)){x1=x0*1.5+1;} if(!(y1>y0)){y1=y0*1.5+1;}
  /* Padding has to match the axis. Subtracting a linear margin from a log
     domain walks the low end toward zero — a P/E floor of 3 becomes 0.001 —
     and every tick then prints as 0.00. Pad by a factor instead. */
  if(log){
    const fx=Math.pow(x1/x0,0.04), fy=Math.pow(y1/y0,0.04);
    x0/=fx; x1*=fx; y0/=fy; y1*=fy;
    x0=Math.max(x0,1e-3); y0=Math.max(y0,1e-3);
  }else{
    const px=(x1-x0)*0.06, py=(y1-y0)*0.06;
    x0-=px; x1+=px; y0-=py; y1+=py;
  }

  const tx=v=>log?Math.log(Math.max(v,1e-6)):v;
  const X0=tx(x0),X1=tx(x1),Y0=tx(y0),Y1=tx(y1);
  const sx=v=>P.l+(W-P.l-P.r)*((tx(v)-X0)/(X1-X0||1));
  const sy=v=>H-P.b-(H-P.t-P.b)*((tx(v)-Y0)/(Y1-Y0||1));
  const clampX=v=>Math.min(Math.max(v,x0),x1);
  const clampY=v=>Math.min(Math.max(v,y0),y1);

  const rmax=Math.max(...good.map(p=>p.r||0),1);
  const rad=v=>3+16*Math.sqrt(Math.max(v,0)/rmax);   // area, not radius

  const ticks=(lo,hi)=>{
    if(log){
      const out=[];
      for(let e=Math.floor(Math.log10(lo));e<=Math.ceil(Math.log10(hi));e++)
        for(const m of [1,2,5]){const v=m*Math.pow(10,e);
          if(v>=lo&&v<=hi)out.push(v);}
      return out.length>1?out:[lo,hi];
    }
    const out=[];for(let i=0;i<=5;i++)out.push(lo+(hi-lo)*i/5);return out;
  };
  const fmt=v=>Math.abs(v)>=100?v.toFixed(0):(Math.abs(v)>=10?v.toFixed(1):v.toFixed(2));

  let g='';
  ticks(x0,x1).forEach(v=>{g+='<line class="gl" x1="'+sx(v).toFixed(1)+'" y1="'+P.t+
    '" x2="'+sx(v).toFixed(1)+'" y2="'+(H-P.b)+'"/><text x="'+sx(v).toFixed(1)+
    '" y="'+(H-P.b+14)+'" text-anchor="middle">'+fmt(v)+'</text>';});
  ticks(y0,y1).forEach(v=>{g+='<line class="gl" x1="'+P.l+'" y1="'+sy(v).toFixed(1)+
    '" x2="'+(W-P.r)+'" y2="'+sy(v).toFixed(1)+'"/><text x="'+(P.l-6)+'" y="'+
    (sy(v)+3).toFixed(1)+'" text-anchor="end">'+fmt(v)+'</text>';});

  /* Exact rays y = k*x. Labelled where they leave the plot, so the label sits
     next to the line it belongs to rather than in a legend. */
  let iso='';
  if(o.iso&&o.iso.length){
    o.iso.forEach(k=>{
      const seg=[];
      for(let i=0;i<=40;i++){
        const xv=log?Math.exp(X0+(X1-X0)*i/40):x0+(x1-x0)*i/40;
        const yv=k*xv;
        if(yv>=y0&&yv<=y1)seg.push([sx(xv),sy(yv)]);
      }
      if(seg.length<2)return;
      iso+='<path class="iso" d="M'+seg.map(p=>p[0].toFixed(1)+' '+
        p[1].toFixed(1)).join(' L')+'"/>';
      const e=seg[seg.length-1];
      iso+='<text class="isolab" x="'+(e[0]-3).toFixed(1)+'" y="'+
        (e[1]-4).toFixed(1)+'" text-anchor="end">'+
        (o.isoLabel?o.isoLabel(k):k)+'</text>';
    });
  }

  let fit=null,fitPath='';
  if(o.fit==='ols'){
    fit=ols(good.map(p=>({x:log?Math.log(p.x):p.x,y:log?Math.log(p.y):p.y})));
    if(fit){
      const at=xv=>{const t=fit.a+fit.b*(log?Math.log(xv):xv);
        return log?Math.exp(t):t;};
      const seg=[];
      for(let i=0;i<=40;i++){
        const xv=log?Math.exp(X0+(X1-X0)*i/40):x0+(x1-x0)*i/40;
        const yv=at(xv);
        if(yv>=y0&&yv<=y1)seg.push([sx(xv),sy(yv)]);
      }
      if(seg.length>1)fitPath='<path class="ols" d="M'+seg.map(p=>
        p[0].toFixed(1)+' '+p[1].toFixed(1)).join(' L')+'"/>';
      fit.at=at;
    }
  }

  let off=0;
  const byR=good.slice().sort((a,b)=>(b.r||0)-(a.r||0));
  const labelN=o.labelN||0;
  const labelled=new Set(byR.slice(0,labelN).map(p=>p.label));
  let dots='',labs='';
  byR.slice().reverse().forEach(p=>{
    const outside=p.x<x0||p.x>x1||p.y<y0||p.y>y1;
    if(outside)off++;
    const cx=sx(clampX(p.x)),cy=sy(clampY(p.y));
    dots+='<circle cx="'+cx.toFixed(1)+'" cy="'+cy.toFixed(1)+'" r="'+
      rad(p.r||0).toFixed(1)+'" fill="'+(p.color||'var(--navy)')+'" opacity="'+
      (outside?0.35:0.72)+'" data-s="'+p.label+'" data-t="'+
      (p.tip||'').replace(/"/g,'&quot;')+'"/>';
    if(labelled.has(p.label))
      labs+='<text class="plab" x="'+(cx+rad(p.r||0)+2).toFixed(1)+'" y="'+
        (cy+3).toFixed(1)+'">'+p.label+'</text>';
  });

  const svg='<svg class="bub" viewBox="0 0 '+W+' '+H+'">'+
    '<text class="atitle" x="'+P.l+'" y="14">'+(o.title||'')+'</text>'+
    g+iso+fitPath+dots+labs+
    '<line class="ax" x1="'+P.l+'" y1="'+(H-P.b)+'" x2="'+(W-P.r)+'" y2="'+
      (H-P.b)+'"/>'+
    '<line class="ax" x1="'+P.l+'" y1="'+P.t+'" x2="'+P.l+'" y2="'+(H-P.b)+'"/>'+
    '<text x="'+((P.l+W-P.r)/2)+'" y="'+(H-8)+'" text-anchor="middle">'+
      (o.xlab||'')+'</text>'+
    '<text x="14" y="'+((P.t+H-P.b)/2)+'" text-anchor="middle" transform="rotate(-90 14 '+
      ((P.t+H-P.b)/2)+')">'+(o.ylab||'')+'</text></svg>';
  return {svg:svg,fit:fit,n:good.length,off:off};
}

function bindBubbles(el){
  el.querySelectorAll('circle[data-s]').forEach(c=>{
    c.onmousemove=e=>showTip(e,c.dataset.t);
    c.onmouseleave=hideTip;
    c.onclick=()=>{hideTip();openScrip(c.dataset.s);};});
}
function secColorSafe(g){
  return (typeof secColor==='function')?secColor(g):'var(--navy)';
}

function renderFundCharts(rows){
  const log=$('#fdScale').value==='log';
  const fitMode=$('#fdFit').value;
  const labelN=+$('#fdLabel').value;
  const iso=fitMode==='iso';

  const mk=(sel,fsel,pts,o)=>{
    const r=bubbleChart(pts,Object.assign({log:log,labelN:labelN,
      fit:fitMode==='ols'?'ols':null},o));
    $(sel).innerHTML=r.svg; bindBubbles($(sel));
    let note=r.n+' names';
    if(r.off)note+=' · '+r.off+' pinned to the edge, outside the 2–98% range';
    if(r.fit)note+=' · <b>'+o.fitLabel(r.fit)+'</b>  R²='+r.fit.r2.toFixed(2);
    $(fsel).innerHTML=note;
  };

  /* P/B = P/E x ROE, so a ray is a line of constant ROE. */
  mk('#fdPEPB','#fdPEPBFit',
     rows.filter(r=>r.peLive>0&&r.pbvLive>0).map(r=>({
       x:r.peLive,y:r.pbvLive,r:r.mcapLive||0,label:r.sym,
       color:secColorSafe(r.sector),
       tip:'<b>'+r.sym+'</b> · '+r.sector+'<br>P/E '+r.peLive.toFixed(1)+
         ' · P/B '+r.pbvLive.toFixed(2)+'<br>implied ROE '+
         (100*r.pbvLive/r.peLive).toFixed(1)+'%<br>'+npr(r.mcapLive)})),
     {title:'P/B against P/E — rays are constant ROE',
      xlab:'P/E (live)',ylab:'P/B (live)',
      iso:iso?[0.05,0.10,0.15,0.20,0.30,0.45]:null,
      isoLabel:k=>(100*k).toFixed(0)+'% ROE',
      fitLabel:f=>log?('P/B ∝ P/E^'+f.b.toFixed(2)):
                     ('P/B = '+f.b.toFixed(3)+'·P/E + '+f.a.toFixed(2))});

  /* ROE = ROA x equity multiplier, so a ray is constant leverage. */
  mk('#fdROE','#fdROEFit',
     rows.filter(r=>r.roeTTM>0&&r.roaTTM>0).map(r=>({
       x:r.roaTTM,y:r.roeTTM,r:r.mcapLive||0,label:r.sym,
       color:secColorSafe(r.sector),
       tip:'<b>'+r.sym+'</b> · '+r.sector+'<br>ROA '+r.roaTTM.toFixed(2)+
         '% · ROE '+r.roeTTM.toFixed(1)+'%<br>implied leverage '+
         (r.roeTTM/r.roaTTM).toFixed(1)+'x<br>'+npr(r.mcapLive)})),
     {title:'ROE against ROA — rays are constant leverage',
      xlab:'ROA TTM %',ylab:'ROE TTM %',
      iso:iso?[1,2,4,8,12,16]:null,
      isoLabel:k=>k+'x',
      fitLabel:f=>log?('ROE ∝ ROA^'+f.b.toFixed(2)):
                     ('ROE = '+f.b.toFixed(2)+'·ROA + '+f.a.toFixed(2))});
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

  renderFundCharts(rows);
}

/* ---------- fundamentals wiring ---------- */
['fdPrice','fdMinT'].forEach(id=>$('#'+id).addEventListener('change',()=>{
  FDOPT.price=$('#fdPrice').value;
  FDOPT.minT=+$('#fdMinT').value;
  renderFund();}));
$('#fdQ').addEventListener('input',renderFund);
/* The charts read the same rows the tables do, so redraw only those. */
['fdFit','fdScale','fdLabel'].forEach(id=>
  $('#'+id).addEventListener('change',()=>{
    if(FUND)renderFundCharts(fundRows());}));
"""
