/* TEST DL (2026-11-13) - DELTA map: Map tab, right pane, button "Delta" (Mithil's yes 2026-11-12 22:5x to QuantNug's
   "custom delta heatmap ... where supportive / bearish zones to buy / sell are"; 00:10 11/13: combine it with Alma's shadow
   greeks). Test cockpit only (MAP rule, 11/09). Two views (switch "From here | By strike" next to the button):
   FROM HERE  every price x minute = the ES contracts dealers would BUY (green) or SELL (red) on the way if SPX went from that
              minute's price to that level:  H(p) = -F x [D(p) - D(price)], D = dealer delta of the same book/basis/clock,
              computed DIRECTLY from each option's delta (kind "delta" in greekColTG: sum of position x N(d1)). With the Shadow
              toggle (SG) each option's IV moves with price by our measured beta, so D is Alma's shadow delta; without it IV is
              held (sticky strike). F = ES contracts per option delta: SPX 2 (100 x SPX / 50 x SPX), SPY 2 x SPY / SPX (live
              ratio), QQQ shown in $M.
   BY STRIKE  each strike's own share: the ES contracts that strike's options alone make dealers buy / sell as SPX travels
              from that minute's price to the strike, drawn as a band at the strike. Green below price = support (dealers buy
              into it), red below = it speeds a drop; red above = resistance, green above = it speeds a rise. The views do not
              add up to each other: "From here" sums every strike at each level, "By strike" shows one strike at its own level.
   The column's price = the logged price of that minute (the 09:30 row carries the prior close, so 09:31 is used there), spot
   for the projection to the close.  Lines every N ES (From here), dotted = where the net hedge from here comes back to zero.
   #1-#6 (From here) = where dealers hedge the most per 5 points right now (peaks of |gamma| - shadow gamma with SG - at least
   8% of the column's strongest); (By strike) = the six biggest strike bands now. Solid = long gamma at the level (absorbs:
   works against the move into it), dashed = short gamma (speeds it).
   Sizes are model numbers: the naive book counts every contract of the basis as dealer-held, so read them as relative.
   Display only: buildMap for gamma / charm / vanna, the read-out (it keeps Charm, as when Charm is selected) and every other
   number are unchanged. Scored nightly by delta_score.py from the 15-minute read-outs (field "dl": SPX, OI + vol, All book,
   production maths, Smile off; levels with Shadow off and on). */
const TG_DL_V="dl-v1";
const TG_DL_RAMP={pos:[[12,22,42],[16,78,46],[30,150,78],[100,220,140],[220,252,230]],neg:[[12,22,42],[105,18,30],[200,45,45],[250,125,80],[255,226,196]]};
let TG_DL_LAST=null;
st.dlMode=(()=>{ try{ const v=localStorage.getItem(NS+"dlMode"); return v==="strike"?"strike":"here"; }catch(e){ return "here"; } })();
function tgDlFac(sym){ const L=st.live||{}, s=sym||st.sym, spx=L.SPX&&L.SPX.spot, spy=L.SPY&&L.SPY.spot, q=L.QQQ&&L.QQQ.spot;
  if(s==="SPX") return {f:2,u:"ES"};
  if(s==="SPY"&&spx>0&&spy>0) return {f:2*spy/spx,u:"ES"};
  const x=s==="SPY"?spy:q; return {f:100*(x||0)/1e6,u:"$M"}; }
function tgDlInterp(col,p0,dp,a){ const n=col.length, r=(a-p0)/dp; if(r<=0) return col[0]+(col[1]-col[0])*r; if(r>=n-1) return col[n-1]+(col[n-1]-col[n-2])*(r-(n-1));
  const k=Math.floor(r), u=r-k; return col[k]+(col[k+1]-col[k])*u; }
function tgDlColumn(D,prices,dp,a,f){ const Da=tgDlInterp(D,prices[0],dp,a); return D.map(x=>-f*(x-Da)); }
function tgDlLevels(prices,col,S,f,H,nmax){ // peaks of |gamma| = most hedging per 5 pts; buy/sell = what dealers do travelling there from S
  const n=col.length; let mx=0; for(const v of col) if(v!=null&&Math.abs(v)>mx) mx=Math.abs(v); if(!(mx>0)) return [];
  const c=[]; for(let i=1;i<n-1;i++){ const a=Math.abs(col[i]); if(a>=Math.abs(col[i-1])&&a>Math.abs(col[i+1])&&a>=0.08*mx) c.push(i); }
  c.sort((x,y)=>Math.abs(col[y])-Math.abs(col[x])||x-y);
  return c.slice(0,nmax||6).map((i,r)=>{ const p=prices[i], g=col[i], side=Math.abs(p-S)<1e-9?0:Math.sign(p-S);
    return {rank:r+1,p:+p.toFixed(2),es5:Math.round(Math.abs(g)*2*f),cls:g>0?"hold":"speed",
      dir:side===0?(g>0?"absorbs":"speeds"):(-Math.sign(g)*side>0?"buy":"sell"),H:H?Math.round(H[i]):null}; }); }
function tgDlStep(v){ for(const s of [25,50,100,250,500,1000,2500,5000,10000,25000,50000,100000,250000]) if(v/s<=4) return s; return 500000; }
function tgDlRef(t,now,S,px){ if(t>now) return S; for(let q=(t===MAP_OPEN?1:0);q<4;q++) if(px.has(t+q)) return px.get(t+q); for(let q=1;q<4;q++) if(px.has(t-q)) return px.get(t-q); return S; }
function tgDlRowSg(r,p){ return !SB?r.sg:TG.sg?tgShadowSg(r,p):TG.c2?tgPathSg(r,p):r.sg; }   // same IV rule as greekColTG
function tgDlStrikeCol(rows0,near,t,ty0,a,f,day){ // each strike's own hedge from price a to the strike (ES), summed over expiries at that strike
  const out=new Map(); const add=(r,ty)=>{ if(!(r.sg>0)) return; const pos=r.cp-r.pp; if(!pos||!(ty>0)) return; const sq=Math.sqrt(ty);
    const n=p=>{ const sg=tgDlRowSg(r,p); return tgN((Math.log(p/r.k)+0.5*sg*sg*ty)/(sg*sq)); };
    out.set(r.k,(out.get(r.k)||0)-f*pos*(n(r.k)-n(a))); };
  if(rows0) for(const r of rows0) add(r,ty0);
  if(near) for(const r of near){ const m=wkTauMin(r,day,t); if(m!=null) add(r,m/525600); }
  return out; }
function tgDeltaMap(se,d,book,V){
  const g=buildMap("gamma",se,d,book,V), dm=buildMap("delta",se,d,book,V); if(!g||!dm) return null;
  const {f,u}=tgDlFac(); const px=new Map(); for(const s of (st.flow[st.sym]||[])) if(s.x>0) px.set(tmin(s.t),s.x);
  const anchors=g.times.map(t=>tgDlRef(t,g.now,g.S,px)); const strike=st.dlMode==="strike";
  let jn=0; for(let j=0;j<g.times.length;j++) if(g.times[j]<=Math.max(g.now,MAP_OPEN)) jn=j;
  let grid, levels, kcols=null;
  const here=dm.grid.map((D,j)=>tgDlColumn(D,g.prices,g.dp,anchors[j],f));
  if(!strike){ grid=here; levels=tgDlLevels(g.prices,g.grid[jn],anchors[jn],f,here[jn],6); }
  else { // per-strike bands: rows inside each strike's half-step go to that strike
    const snaps=st.flow[st.sym]||[], byT=new Map(snaps.map((s,j)=>[tmin(s.t),j])), live=mapRowsAt(null,se,d), use0=book!=="week";
    const near=book==="0dte"?null:nearRows(), day=wkDayISO(), step=d.step||5, memo=new Map();
    kcols=g.times.map((t,j)=>{ let rows=null; if(use0){ if(t<MAP_OPEN||t>g.now) rows=live; else { let jj=null; for(let q=0;q<4;q++) if(byT.has(t+q)){ jj=byT.get(t+q); break; } rows=jj==null?live:(memo.get(jj)||(memo.set(jj,mapRowsAt(jj,se,d)),memo.get(jj))); } }
      const ty=Math.max(TG_GEND-Math.max(t,t<MAP_OPEN?MAP_OPEN:t),1)/525600; return tgDlStrikeCol(rows,near,Math.max(t,MAP_OPEN),ty,anchors[j],f,day); });
    grid=kcols.map(km=>g.prices.map(p=>{ const k=Math.round(p/step)*step; return Math.abs(p-k)<=step*0.45?(km.get(k)||0):0; }));
    const now=[...kcols[jn].entries()].filter(([k])=>k>=g.prices[0]&&k<=g.prices[g.prices.length-1]).sort((a,b)=>Math.abs(b[1])-Math.abs(a[1])).slice(0,6);
    levels=now.map(([k,H],r)=>{ const i=Math.max(0,Math.min(g.prices.length-1,Math.round((k-g.prices[0])/g.dp))), gv=g.grid[jn][i], side=Math.sign(k-anchors[jn]);
      return {rank:r+1,p:k,es5:Math.round(Math.abs(gv)*2*f),cls:gv>0?"hold":"speed",dir:H>=0?"buy":"sell",H:Math.round(H),own:true,side}; }); }
  const all=[]; for(const col of grid) for(const v of col) all.push(Math.abs(v)); all.sort((a,b)=>a-b);
  const vmax=Math.max(1e-9,all[Math.floor(all.length*0.95)]||all[all.length-1]||1e-9);
  const feats=grid.map((col,j)=>{ const z=[]; if(!strike){ const ra=(anchors[j]-g.prices[0])/g.dp;
      for(let i=1;i<col.length;i++){ if((col[i-1]<0&&col[i]>=0)||(col[i-1]>0&&col[i]<=0)){ const zi=i-1+(0-col[i-1])/(col[i]-col[i-1]||1); if(Math.abs(zi-ra)>1.5) z.push(zi); } } }
    return {z,pk:[],tr:[],sp:[]}; });
  const colMax=grid.map(col=>{ let a=1e-9; for(const v of col) if(v!=null&&Math.abs(v)>a) a=Math.abs(v); return a; });
  return {kind:"delta",prices:g.prices,times:g.times,grid,cgrid:grid,base:null,vmax,now:g.now,S:g.S,feats,dp:g.dp,near:g.near,nearN:g.nearN,
    book:g.book,share:null,colMax,view:V,led:g.led,unit:u,fac:f,gm:g,here,kcols,anchors,jn,levels,step:tgDlStep(vmax),strike,shadow:!!(TG.sg&&SB)}; }
function tgDeltaLegend(m,bkTxt){ if(!m) return "Delta: needs the gamma map";
  return (m.strike?`Delta by strike · each strike's own dealer buy (green) / sell (red) if SPX went from that minute's price to the strike`
    :`Delta · green = dealers BUY on the way to that level from that minute's price · red = SELL · lines every ${fmtN(m.step)} ${m.unit} (model) · dotted = net zero`)
    +` · ${m.shadow?"SHADOW: IV moves with price (measured β)":"IV held (sticky strike)"} · #1–#6 = ${m.strike?"biggest strike bands":"most hedging per 5 pts"} now · ${bkTxt}`; }
function tgDlOverlay(ctx,o){ const m=o.m, X=o.X, Y=o.Y; if(!m||m.kind!=="delta") return;
  m._geo={padL:o.padL,padT:o.padT,W:o.W,H:o.H,T0:m.times[0]}; TG_DL_LAST=m;
  const P=r=>m.prices[0]+r*m.dp;
  ctx.lineWidth=1; ctx.setLineDash([]);
  if(!m.strike) for(let k=-8;k<=8;k++){ if(!k) continue; const L=k*m.step; ctx.strokeStyle=Math.abs(k)%2?"#ffffff55":"#ffffff99"; ctx.beginPath(); let any=false;
    const cr=m.grid.map(col=>{ const o=[]; for(let i=1;i<col.length;i++){ const a=col[i-1], b=col[i]; if((a-L)*(b-L)<0) o.push(i-1+(L-a)/((b-a)||1)); } return o; });
    for(let j=0;j<cr.length-1;j++){ for(const i of cr[j]){ let best=null; for(const q of cr[j+1]) if(Math.abs(q-i)<=3&&(best==null||Math.abs(q-i)<Math.abs(best-i))) best=q;
        if(best!=null){ ctx.moveTo(X(m.times[j]),Y(P(i))); ctx.lineTo(X(m.times[j+1]),Y(P(best))); any=true; } } }
    if(any) ctx.stroke(); }
  const xn=X(m.times[m.jn]), xr=o.padL+o.W; ctx.font="bold 13px system-ui"; const used=[];
  for(const L of m.levels){ const y=Y(L.p); if(y<o.padT||y>o.padT+o.H) continue; const col=L.dir==="buy"?"#2ECC71":L.dir==="sell"?"#FF5A5A":"#FFD84D";
    ctx.strokeStyle=col; ctx.lineWidth=2; ctx.setLineDash(L.cls==="hold"?[]:[6,4]); ctx.beginPath(); ctx.moveTo(xn,y); ctx.lineTo(xr,y); ctx.stroke(); ctx.setLineDash([]);
    const txt=`#${L.rank} ${fmtN(L.p)}`, tw=ctx.measureText(txt).width; let yy=y+5; while(used.some(u=>Math.abs(u-yy)<16)) yy+=16; used.push(yy);
    const x=Math.max(o.padL+2,Math.min(xr-tw-10,xn+8)); ctx.fillStyle="#000000d8"; ctx.fillRect(x-4,yy-13,tw+8,18); ctx.fillStyle=col; ctx.fillText(txt,x,yy); } }
function tgDlTipHTML(m,t,p){ const g=m.gm, j=(()=>{ let b=0; for(let q=0;q<m.times.length;q++) if(Math.abs(m.times[q]-t)<Math.abs(m.times[b]-t)) b=q; return b; })();
  const i=Math.max(0,Math.min(m.prices.length-1,Math.round((p-m.prices[0])/m.dp))), H=m.here[j][i], gv=g.grid[j][i], a=m.anchors[j], u=m.unit;
  const amt=x=>(u==="ES"?fmtN(Math.abs(x))+" ES":"$"+fmtN(Math.abs(x),1)+"M")+" (model)";
  const lv=m.levels.find(L=>Math.abs(L.p-m.prices[i])<=Math.max(m.dp*1.01,m.strike?2.5:0)&&j===m.jn);
  let h=`<b>${tlabel(m.times[j])} · ${fmtN(m.prices[i])}</b>${lv?` · <b>#${lv.rank}</b>`:""}${m.shadow?" · shadow":""}<br>From ${fmtN(a)} to here, all strikes: dealers <b style="color:${H>=0?"#2ECC71":"#FF5A5A"}">${H>=0?"buy":"sell"} ${amt(H)}</b>`;
  if(m.strike&&m.kcols){ const step=(st.live&&st.live[st.sym]&&st.live[st.sym].step)||5, k=Math.round(m.prices[i]/step)*step, v=m.kcols[j].get(k);
    if(v!=null) h+=`<br>Strike ${fmtN(k)} alone: dealers <b style="color:${v>=0?"#2ECC71":"#FF5A5A"}">${v>=0?"buy":"sell"} ${amt(v)}</b> as price reaches it`; }
  return h+`<br>At this level: ${amt(gv*2*m.fac)} per 5 pts — ${gv>0?"long gamma: works against the move (calms it)":"short gamma: works with the move (speeds it)"}`; }
function tgDlHover(){ const cv=document.getElementById("mapRightC"), pane=document.getElementById("mapRightPane"); if(!cv||!pane||cv._dl) return; cv._dl=1;
  const tip=document.createElement("div"); tip.className="tip"; tip.id="tgDlTip"; tip.style.minWidth="260px"; tip.style.fontSize="14px"; pane.style.position=pane.style.position||"relative"; pane.appendChild(tip);
  const move=e=>{ const m=TG_DL_LAST; if(st.mapRight!=="delta"||!m||!m._geo){ tip.style.display="none"; return; } const r=cv.getBoundingClientRect(), x=e.clientX-r.left, y=e.clientY-r.top, G=m._geo;
    if(x<G.padL||x>G.padL+G.W||y<G.padT||y>G.padT+G.H){ tip.style.display="none"; return; }
    const t=G.T0+(x-G.padL)/G.W*(MAP_T1-G.T0), p=m.prices[0]+(1-(y-G.padT)/G.H)*(m.prices[m.prices.length-1]-m.prices[0]);
    tip.innerHTML=tgDlTipHTML(m,t,p); const pr=pane.getBoundingClientRect(); tip.style.display="block"; tip.style.left=Math.min(e.clientX-pr.left+14,pr.width-300)+"px"; tip.style.top=(e.clientY-pr.top+14)+"px"; };
  cv.addEventListener("mousemove",move); cv.addEventListener("click",move); cv.addEventListener("mouseleave",()=>{ tip.style.display="none"; });
  document.querySelectorAll("#tgDlModeSeg button").forEach(b=>b.addEventListener("click",()=>{ st.dlMode=b.dataset.v==="strike"?"strike":"here"; try{ localStorage.setItem(NS+"dlMode",st.dlMode); }catch(e){} render(); })); }
function tgDlSeg(){ const s=document.getElementById("tgDlModeSeg"); if(!s) return; s.style.display=st.mapRight==="delta"?"":"none"; syncSeg("tgDlModeSeg",st.dlMode); }
function tgDeltaReadout(){ // fixed config for scoring: SPX, OI + vol, All book, production maths, Smile off; levels with Shadow off and on; restores everything
  const d=st.live&&st.live.SPX; if(!d||!d.rows||!(d.spot>0)) return null;
  const keepTG=Object.assign({},TG), keep={sym:st.sym,smileOn:st.smileOn,basis:st.basis,mapLed:st.mapLed}; const c=liveClock(), now=c.h*60+c.m, day=wkDayISO();
  try{ st.sym="SPX"; st.basis="oiv"; st.mapLed=false; st.smileOn=false; const out={v:TG_DL_V,basis:"oiv",book:"all",spot:d.spot};
    for(const sg of [false,true]){ for(const k in TG) TG[k]=(k in TG_SEL)?"":false; TG.sg=sg; tgApply(); tgClockSync();
      if(sg&&!SB){ out.levels_sg=null; continue; }
      const S=d.spot, dp=(d.step||5)/MAP_SUB, live=mapRowsAt(null,null,d), near=nearRows()||[], P=tgGrid(S,0.02,dp), tyNow=Math.max(TG_GEND-now,1)/525600;
      const col=P.map(p=>greekCol("gamma",live,p,tyNow)+(near.length?greekColWeek("gamma",near,p,now,day):0));
      const D=P.map(p=>greekCol("delta",live,p,tyNow)+(near.length?greekColWeek("delta",near,p,now,day):0));
      out[sg?"levels_sg":"levels"]=tgDlLevels(P,col,S,2,tgDlColumn(D,P,dp,S,2),6); }
    return out; }
  catch(e){ return {v:TG_DL_V,err:String(e).slice(0,200)}; }
  finally{ for(const k in TG) TG[k]=keepTG[k]; tgApply(); st.sym=keep.sym; st.smileOn=keep.smileOn; st.basis=keep.basis; st.mapLed=keep.mapLed; tgClockSync(); } }
