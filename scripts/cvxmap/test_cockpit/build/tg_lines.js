/* TEST SL + ZR (2026-11-13) - display only, test cockpit only (MAP rule, 11/09). Nothing here changes a map number.
   SL  STRIKE LINES (Mithil 11/13 14:12, from a vendor chart: "learn visualization how strike line look ... just like orbs").
       Flow tab, button "Strike lines" (OFF by default). The current minute's value at each strike, drawn as a full-width line
       across the Flow map with a label at the right edge ("7,545 · sell 1,140 ES"), the 10 biggest strikes in view only,
       strikes under 8% of the biggest dropped, thickness + glow by size, star on the biggest.
       Source switch next to the button:
         Delta = the Delta map's "By strike" value now: the ES contracts that strike's options alone make dealers buy (green) /
                 sell (red) as SPX travels from the current price to the strike (tgDlStrikeCol, same IV rule as the Delta
                 pane: held, or shadow with SG). Model size, naive book: read as relative.
         Gamma = dealer gamma at that strike now, $ per 1% move (the Orbs view's latest column): yellow + / purple -.
       Book = the Map's gamma book (0DTE / Week / All), positions = the Map basis. Lines show NOW across the whole window
       (no history; Orbs / the Delta pane keep the history).
   ZR  ZERO LINE on the right-hand Vanna and Charm maps (Mithil 11/13 14:12: "gamma flip line, we need similar line zero
       vanna on vanna, charm MAP"). Same rule and look as the Gamma Flip line (zoneLines: in each minute's column the
       zero crossing nearest that minute's price, joined while it moves <= 6 rows): yellow line + label
       "vanna zero 7,548 · zone 7,541-7,556". Button "Zero line" in the right pane, shown for Charm / Vanna, OFF by default. */
st.slOn=load("tgSlOn","0")==="1"; st.slSrc=load("tgSlSrc","delta")==="gamma"?"gamma":"delta"; st.zeroR=load("tgZeroR","0")==="1";
const TG_SL={key:null,val:null};
function tgSlBig(v){ const a=Math.abs(v); return a>=1e9?"$"+(a/1e9).toFixed(a>=1e10?0:1)+"B":a>=1e6?"$"+(a/1e6).toFixed(a>=1e7?0:1)+"M":"$"+fmtN(a/1e3,0)+"K"; }
function tgSlValues(){ // {src,unit,S,book,vals:Map k -> value} for the current minute; cached per live tick + settings
  const d=st.live&&st.live[st.sym]; if(!d||!d.rows||!(d.spot>0)) return null;
  const book=st.mapBook||"all", key=[st.live.ts,st.sym,book,st.basis,st.slSrc,tgKey(),st.smileOn?1:0].join("|");
  if(TG_SL.key===key) return TG_SL.val;
  const se=series(st.sym), S=d.spot, c=liveClock(), tN=Math.max(MAP_OPEN,c.h*60+c.m), ty0=Math.max(TG_GEND-tN,1)/525600, day=wkDayISO();
  const rows0=book!=="week"?mapRowsAt(null,se,d):null, near=book==="0dte"?null:nearRows();
  let vals;
  if(st.slSrc==="delta"){ const {f,u}=tgDlFac(); vals=tgDlStrikeCol(rows0,near,tN,ty0,S,f,day); TG_SL.val={src:"delta",unit:u,S,book,vals}; }
  else { vals=new Map(); const usd=0.01*S/2.5*100*S;   // greekCol gamma = dealer option deltas per 2.5 pts -> $ per 1% move (as Orbs)
    const add=(k,v)=>{ if(isFinite(v)&&v) vals.set(k,(vals.get(k)||0)+v*usd); };
    if(rows0) for(const r of rows0) if(r.sg>0) add(r.k,greekCol("gamma",[r],S,ty0));
    if(near) for(const r of near) add(r.k,greekColNear("gamma",[r],S,tN));
    TG_SL.val={src:"gamma",unit:"$/1%",S,book,vals}; }
  TG_SL.key=key; return TG_SL.val; }
function tgSlLabel(o,k,v,star){
  const amt=o.src==="delta"?(o.unit==="ES"?`${v>=0?"buy":"sell"} ${fmtN(Math.abs(v))} ES`:`${v>=0?"buy":"sell"} $${fmtN(Math.abs(v),1)}M`):`${v>=0?"+":"−"}${tgSlBig(v)} /1%`;
  return `${star?"★ ":""}${fmtN(k)} · ${amt}`; }
function tgSlCol(o,v){ return o.src==="delta"?(v>=0?"#2ECC71":"#FF5A5A"):(v>=0?"#FFD23F":"#C99BFF"); }
function tgSlPick(o,yOk,n){ // the n biggest strikes in view, at least 8% of the biggest in view
  const inV=[...o.vals.entries()].filter(([k,v])=>v&&yOk(k)); if(!inV.length) return [];
  const mx=Math.max(...inV.map(([,v])=>Math.abs(v)));
  return inV.filter(([,v])=>Math.abs(v)>=0.08*mx).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,n||10).map(([k,v],i)=>({k,v,u:Math.abs(v)/mx,star:i===0})); }
function tgStrikeLines(ctx,g,part){ // Flow map: g = {Yk, x0, x1, yMin, yMax}; part "lines" (under the bubbles) / "labels" (on top)
  if(!st.slOn) return; const o=tgSlValues(); if(!o) return;
  const P=tgSlPick(o,k=>{ const y=g.Yk(k); return y>=g.yMin&&y<=g.yMax; },10); if(!P.length) return;
  ctx.save();
  if(part==="lines"){ for(const L of P.slice().sort((a,b)=>a.u-b.u)){ const y=g.Yk(L.k), col=tgSlCol(o,L.v);   // small first, the biggest on top
      ctx.globalAlpha=0.35+0.6*L.u; ctx.strokeStyle=col; ctx.lineWidth=1+5*Math.sqrt(L.u); ctx.shadowColor=col; ctx.shadowBlur=4+12*L.u; ctx.setLineDash([]);
      ctx.beginPath(); ctx.moveTo(g.x0,y); ctx.lineTo(g.x1,y); ctx.stroke(); } }
  else { ctx.font="bold 13px system-ui"; ctx.textAlign="right";
    const lab=P.map(L=>({y:g.Yk(L.k),L})).sort((a,b)=>a.y-b.y); for(let i=1;i<lab.length;i++) if(lab[i].y-lab[i-1].y<18) lab[i].y=lab[i-1].y+18;
    for(const {y,L} of lab){ const t=tgSlLabel(o,L.k,L.v,L.star), tw=ctx.measureText(t).width, col=tgSlCol(o,L.v), xr=g.x1-6;
      ctx.fillStyle="#05080fe6"; ctx.fillRect(xr-tw-8,y-11,tw+12,20); ctx.strokeStyle=col; ctx.lineWidth=1.2; ctx.strokeRect(xr-tw-8,y-11,tw+12,20);
      ctx.fillStyle=col; ctx.fillText(t,xr,y+4); } }
  ctx.restore(); }
function tgSlLegend(){ if(!st.slOn) return ""; const o=tgSlValues(); const bk={"0dte":"0DTE","week":"1–7 DTE","all":"0DTE + 1–7 DTE"}[st.mapBook||"all"];
  return `<span style="color:#FFD84D;font-weight:700">TEST Strike lines</span><span>${st.slSrc==="delta"?`Delta by strike now: <b style="color:#2ECC71">buy</b> / <b style="color:#FF5A5A">sell</b> ${o&&o.unit==="ES"?"ES":"$M"} if price goes there (model, ${TG.sg&&SB?"shadow IV":"IV held"})`:`Gamma by strike now, $ per 1%: <b style="color:#FFD23F">+ absorbs</b> / <b style="color:#C99BFF">− speeds</b>`} · ${bk} · ${String(st.basis).toUpperCase()} · top 10 · ★ biggest</span>`; }
function tgSlSync(){ const b=document.getElementById("tgSlBtn"), s=document.getElementById("tgSlSrcSeg"); if(!b) return;
  b.setAttribute("aria-pressed",st.slOn); if(s){ s.style.display=st.slOn?"":"none"; s.querySelectorAll("button").forEach(x=>x.setAttribute("aria-pressed",x.dataset.v===st.slSrc)); } }
function tgSlInit(){ const b=document.getElementById("tgSlBtn"); if(!b||b._sl) return; b._sl=1;
  b.addEventListener("click",()=>{ st.slOn=!st.slOn; save("tgSlOn",st.slOn?"1":"0"); tgSlSync(); render(); });
  document.querySelectorAll("#tgSlSrcSeg button").forEach(x=>x.addEventListener("click",()=>{ st.slSrc=x.dataset.v==="gamma"?"gamma":"delta"; save("tgSlSrc",st.slSrc); TG_SL.key=null; tgSlSync(); render(); }));
  tgSlSync(); }
/* ZR */
function tgZeroOverlay(ctx,m,X,Y,padL,padT,W,H){
  const ZL=zoneLines(m), P=r=>m.prices[0]+r*m.dp, runs=[]; let cur=[];
  ZL.forEach((o,j)=>{ if(o&&cur.length&&Math.abs(o.z-cur[cur.length-1].o.z)<=6){ cur.push({j,o}); return; } if(cur.length) runs.push(cur); cur=o?[{j,o}]:[]; }); if(cur.length) runs.push(cur);
  for(const [w,c] of [[6,"rgba(0,0,0,0.70)"],[3,"#FFD84D"]]){ ctx.strokeStyle=c; ctx.lineWidth=w; ctx.lineJoin="round"; ctx.setLineDash([]);
    for(const R of runs){ ctx.beginPath(); R.forEach((q,n)=>{ const x=X(m.times[q.j]), y=Y(P(q.o.z)); n?ctx.lineTo(x,y):ctx.moveTo(x,y); }); if(R.length===1) ctx.lineTo(X(m.times[R[0].j])+2,Y(P(R[0].o.z))); ctx.stroke(); } }
  let jn=0; for(let j=0;j<m.times.length;j++) if(m.times[j]<=Math.max(m.now,MAP_OPEN)) jn=j;
  const o=ZL[jn], col=m.grid[jn]||[], nm=m.kind==="vanna"?"vanna":"charm"; ctx.font="bold 13px system-ui";
  const all=col.length&&col.every(v=>v==null||v>=0);
  const txt=o?`${nm} zero ${fmtN(Math.round(P(o.z)))} · zone ${fmtN(Math.round(P(o.lo)))}–${fmtN(Math.round(P(o.hi)))}`
    :`no ${nm} zero in view: dealers ${all?"buy":"sell"} everywhere${m.kind==="vanna"?" on a vol rise":" from time"}`;
  const tw=ctx.measureText(txt).width, xn=X(m.times[jn]); let x=xn+8; if(x+tw+8>padL+W) x=xn-tw-16; x=Math.max(padL+4,x);
  let y=o?Y(P(o.z))-10:padT+46; y=Math.max(padT+40,Math.min(padT+H-6,y));
  ctx.fillStyle="#000000d0"; ctx.fillRect(x-4,y-13,tw+8,18); ctx.fillStyle="#FFD84D"; ctx.fillText(txt,x,y); }
function tgZeroSync(){ const b=document.getElementById("tgZeroBtn"); if(!b) return; const ok=st.mapRight==="vanna"||st.mapRight==="charm"||!st.mapRight;
  b.style.display=ok?"":"none"; b.setAttribute("aria-pressed",!!st.zeroR);
  if(!b._zr){ b._zr=1; b.addEventListener("click",()=>{ st.zeroR=!st.zeroR; save("tgZeroR",st.zeroR?"1":"0"); tgZeroSync(); render(); }); } }
