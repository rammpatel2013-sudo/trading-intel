/* TEST VS (2026-11-19) - VIX map STRIKE MAP, redesign steps C1 + C2 (claude/vix-map-redesign-spec-20261118.md).
   Test cockpit only (MAP rule 11/09), OFF by default, display only: the production VIX map, its read-out and its numbers
   are unchanged; OFF = production's drawing exactly. Button "Strike map (TEST)" in the VIX map controls.
   Data = the file's v3 "books" (vix_map.py strike_book): one row per listed VIX strike, every 30 minutes + the newest minute.
   C1  rows = listed strikes, drawn where the front forward reaches the strike (y = K - (front forward - VIX)); band edges
       halfway to the next strike. Value = the strike's own dealer gamma (left map) / vanna (right) with the forward on the
       strike, on the map's basis and dealer side (same rule as the columns: OI = s*o, Vol = s*v, OI+vol = s*(o+v),
       OI+signed = s*o + t). Top 10 strikes by size get a role label "#rank strike ROLE arrow" (gamma map):
         dealers LONG gamma at the strike (they lean against moves there): below VIX = FLOOR (helps price up, green),
           above VIX = CEILING (helps price down, red), within 0.1 of VIX = PIN;
         dealers SHORT gamma (they chase): MAGNET with the arrow toward the strike (above = MAGNET up green, below =
           MAGNET down red) - price speeds up toward / through it.  Rule rv-v0, to confirm with Mithil.
       Hover card: role, strength % of the strongest strike in this book, rank of N, customer contracts, dealer behaviour.
       Price = 5-minute candles from each minute's VIX (the feed has one VIX sample a minute), front forward cyan dashed,
       grey price tag on the axis + red dotted line across the chart at VIX now.
   C2  bands are flat between books (book every 30 min, kink at each refresh, dashed line at the newest book); thin white
       contour where neighbouring bands change sign; white rounded outline (pill) around the hottest cores (>= 60% of the
       scale).
   C3  (2026-11-21) whales vs crowd IN CONTRACTS under the maps (vix_whales_live.json from scripts/whales_feed.py: VIX + VIXW
       option prints from tas_crowd_minute, whales >= 20 lots): two cumulative lines from 09:30 (whales blue, crowd orange),
       fill between, zero dashed, purple ticks = opposed 5-minute stretches, the overnight + pre-market total as a number;
       TAPE box (last 5 minutes: who is winning, strength, lots a minute, whale share, which way customers pushed VIX,
       absorption, heavy-volume minute vs the same minute's median of earlier sessions); per-minute volume bars at the
       bottom of the gamma map (green = more bought than sold, red = more sold).
   C4  (2026-11-21) demand / supply zones + dated naked POCs on the gamma map (vixmap file "zones", vix_map.py): VIX option
       volume of the last 60 stored sessions by VIX level (each contract's volume drawn where the front forward reaches its
       strike); a zone = a busy level +- the neighbouring bins with >= 50% of its volume, at most 0.25 wide; label at the
       right "Demand 19.10-19.30 · 683K" (Demand = more bought than sold); brightest = the busiest (POC) with a full-width
       line. Dashed lines from the left with "<- date" = an earlier day's busiest level that no later day has touched
       (today's range so far checked here too). Display only. */
st.tgVxs=load("tgVxs","0")==="1";
var VXW={J:null,at:0,busy:false,err:null};
/* C3 data: vix_whales_live.json every 30 s while the Strike map is on and the VIX map is open */
async function tgVxwFetch(force){ if(!st.tgVxs||st.tab!=="vix"||!st.vxo.vmap||VXW.busy) return; if(!force&&Date.now()-VXW.at<25000) return;
  VXW.busy=true; VXW.at=Date.now(); const was=VXW.J&&VXW.J.asof;
  try{ const r=await fetch("vix_whales_live.json",{cache:"no-cache"}); if(!r.ok) throw new Error("HTTP "+r.status); VXW.J=await r.json(); VXW.err=null; }
  catch(e){ VXW.err=String(e&&e.message||e); }
  VXW.busy=false; if((VXW.J&&VXW.J.asof)!==was||force){ VXM.key=""; if(st.tab==="vix") vxmRender(); } }
setInterval(()=>tgVxwFetch(false),30000);
function tgVxsOn(){ return !!st.tgVxs; }
function tgVxsKey(){ return st.tgVxs?"vs1:"+(VXW.J?(VXW.J.date||"")+(VXW.J.asof||""):VXW.err||""):"vs0"; }
function tgVxsBtn(){ return `<button class="tgb${st.tgVxs?" on":""}" id="tgVxsBtn" style="font-size:15px" title="TEST Strike map (off by default): rows = the listed VIX strikes (0.5 / 1.0 apart), the strike book every 30 minutes, top-10 role labels (FLOOR / CEILING / MAGNET), hover card, 5-minute candles, price tag, white contours and pills. Display only.">Strike map (TEST)</button>`; }
document.addEventListener("click",e=>{ const b=e.target&&e.target.closest&&e.target.closest("#tgVxsBtn"); if(!b) return;
  st.tgVxs=!st.tgVxs; save("tgVxs",st.tgVxs?"1":"0"); VXM.key=""; vxmRender(); if(st.tgVxs) tgVxwFetch(true); });
/* C4: today's VIX range so far (minute VIX) -> a naked POC inside it was touched today */
function tgVxsTodayRng(J){ let lo=1e9,hi=-1e9; for(const p of vxmPath(J)){ const v=p[1]; if(v!=null&&isFinite(v)){ lo=Math.min(lo,v); hi=Math.max(hi,v); } } return hi>=lo?[lo,hi]:null; }
function tgVxsZones(J){ const Z=J&&J.zones; if(!Z) return {zones:[],naked:[],Z:null}; const r=tgVxsTodayRng(J);
  return {Z, zones:Z.zones||[], naked:(Z.naked||[]).filter(n=>!r||n.y<r[0]-1e-9||n.y>r[1]+1e-9)}; }
function tgVxsDay(d){ const m=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(d||"")); if(!m) return String(d||""); return ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"][+m[2]-1]+" "+(+m[3]); }
/* one book's values on the map's basis and dealer side (vxmVals on the book arrays) */
function tgVxsVals(b,g){ return vxmVals(b,g); }
function tgVxsBookIdx(J,t){ let q=-1; for(let j=0;j<J.books.length;j++) if(tmin(J.books[j].t)<=t+0.01) q=j; return q; }
/* colour scale: 98th percentile of |value| over every book, strikes inside the range shown */
function tgVxsLim(J,g,R){ const a=[]; for(const b of J.books){ const v=tgVxsVals(b,g); for(let i=0;i<b.k.length;i++){ const y=b.y[i]; if(y>=R.lo-1&&y<=R.hi+1) a.push(Math.abs(v[i]||0)); } }
  a.sort((p,q)=>p-q); return a.length?Math.max(1e-9,a[Math.floor(0.98*(a.length-1))]):1; }
/* band edges halfway to the neighbouring strikes */
function tgVxsEdges(y){ const n=y.length, lo=[], hi=[]; for(let i=0;i<n;i++){ const dn=i>0?(y[i]-y[i-1])/2:(n>1?(y[1]-y[0])/2:0.25), up=i<n-1?(y[i+1]-y[i])/2:dn; lo.push(y[i]-dn); hi.push(y[i]+up); } return {lo,hi}; }
/* roles rv-v0 for one book: [{i,k,y,v,abs,rank,pct,role,dir,up}] sorted by size */
function tgVxsRoles(b,vix){ const v=tgVxsVals(b,"g"), out=[]; let mx=0; for(let i=0;i<b.k.length;i++) mx=Math.max(mx,Math.abs(v[i]||0));
  for(let i=0;i<b.k.length;i++){ const x=v[i]||0; if(!x) continue; const y=b.y[i]; let role, up;
    if(x>0){ if(Math.abs(y-vix)<0.1){ role="PIN"; up=null; } else if(y<vix){ role="FLOOR"; up=true; } else { role="CEILING"; up=false; } }
    else { up=y>vix; role="MAGNET"; }
    out.push({i,k:b.k[i],y,v:x,abs:Math.abs(x),pct:mx?Math.abs(x)/mx:0,role,up}); }
  out.sort((p,q)=>q.abs-p.abs); out.forEach((r,j)=>r.rank=j+1); return out; }
function tgVxsRoleTxt(r){ return r.role+(r.role==="MAGNET"?(r.up?"↑":"↓"):""); }
function tgVxsK(n){ const a=Math.abs(n); return a>=1000?(a/1000).toFixed(a>=10000?0:1)+"K":String(Math.round(a)); }
/* 5-minute candles from each minute's VIX: [t0, o, h, l, c] */
function tgVxsCandles(J){ const out=[]; for(const p of vxmPath(J)){ const v=p[1]; if(v==null||!isFinite(v)) continue; const t=tmin(p[0]), b=Math.floor(t/5)*5, c=out[out.length-1];
    if(c&&c[0]===b){ c[2]=Math.max(c[2],v); c[3]=Math.min(c[3],v); c[4]=v; } else out.push([b,c?c[4]:v,Math.max(v,c?c[4]:v),Math.min(v,c?c[4]:v),v]); } return out; }
function tgVxsDraw(cv,J,k,lim,isG,R){
  const {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h); const padL=62,padR=96,padT=10,padB=34, W=w-padL-padR, H=h-padT-padB, t0=570, t1=975;
  const ylo=R.lo-0.5, yhi=R.hi+0.5;
  const X=t=>padL+W*(t-t0)/(t1-t0), Y=v=>padT+H*(1-(v-ylo)/((yhi-ylo)||1));
  const POS=[61,220,132], NEG=[255,77,77], BASE=[14,27,51];   // green = dealers long (lean against), red = short (chase)
  const col=v=>{ const u=Math.max(-1,Math.min(1,v/lim)), c=u>=0?POS:NEG, a=Math.pow(Math.abs(u),0.75); return `rgb(${BASE.map((b,j)=>Math.round(b+(c[j]-b)*a)).join(",")})`; };
  ctx.fillStyle="rgb(14,27,51)"; ctx.fillRect(padL,padT,W,H);
  ctx.save(); ctx.beginPath(); ctx.rect(padL,padT,W,H); ctx.clip();
  const B=J.books, seg=[];
  for(let q=0;q<B.length;q++){ const b=B[q], ta=tmin(b.t), tb=q<B.length-1?tmin(B[q+1].t):t1; const xa=X(ta), xb=Math.max(xa+1,X(tb)); const a=tgVxsVals(b,k), E=tgVxsEdges(b.y);
    seg.push({b,xa,xb,a,E});
    for(let i=0;i<b.k.length;i++){ const ya=Y(E.hi[i]), yb=Y(E.lo[i]); if(yb<padT||ya>padT+H) continue; ctx.fillStyle=col(a[i]||0); ctx.fillRect(xa,ya,xb-xa+0.5,yb-ya+0.5); } }
  // C2: white contours where neighbouring bands change sign; pills around the hottest cores
  ctx.strokeStyle="#ffffffd0"; ctx.lineWidth=1.2;
  for(const s of seg){ for(let i=1;i<s.b.k.length;i++){ const p=s.a[i-1]||0, c=s.a[i]||0; if((p>0&&c<0)||(p<0&&c>0)){ const y=Y(s.E.lo[i]); ctx.beginPath(); ctx.moveTo(s.xa,y); ctx.lineTo(s.xb,y); ctx.stroke(); } } }
  // pills: the 4 strongest strikes of the newest book; one rounded outline per run of books where that strike stays >= 60% of the scale
  ctx.lineWidth=2.2; ctx.strokeStyle="#ffffff";
  const lb0=B[B.length-1], la=tgVxsVals(lb0,k), hot=lb0.k.map((kk,i)=>[kk,Math.abs(la[i]||0)]).filter(o=>o[1]>=0.6*lim).sort((p,q)=>q[1]-p[1]).slice(0,4).map(o=>o[0]);
  const pill=(xa,xb,ya,yb)=>{ if(yb-ya<4||xb-xa<6) return; const r=Math.min(10,(yb-ya)/2); ctx.beginPath(); ctx.moveTo(xa+r,ya); ctx.lineTo(xb-r,ya); ctx.arcTo(xb,ya,xb,ya+r,r); ctx.lineTo(xb,yb-r); ctx.arcTo(xb,yb,xb-r,yb,r); ctx.lineTo(xa+r,yb); ctx.arcTo(xa,yb,xa,yb-r,r); ctx.lineTo(xa,ya+r); ctx.arcTo(xa,ya,xa+r,ya,r); ctx.stroke(); };
  for(const kk of hot){ let run=null; const flush=()=>{ if(run) pill(run.xa+3,run.xb-3,Y(run.hi)+2,Y(run.lo)-2); run=null; };
    for(const s of seg){ const i=s.b.k.indexOf(kk); if(i<0||Math.abs(s.a[i]||0)<0.6*lim){ flush(); continue; }
      if(run){ run.xb=s.xb; run.lo=Math.max(run.lo,s.E.lo[i]); run.hi=Math.min(run.hi,s.E.hi[i]); } else run={xa:s.xa,xb:s.xb,lo:s.E.lo[i],hi:s.E.hi[i]}; } flush(); }
  // book refresh: dashed vertical at the newest stored (30-min) book
  const lastBook=[...B].reverse().find(b=>!b.live)||B[B.length-1];
  if(lastBook){ const x=X(tmin(lastBook.t)); ctx.strokeStyle="#ffffff90"; ctx.lineWidth=1; ctx.setLineDash([5,5]); ctx.beginPath(); ctx.moveTo(x,padT); ctx.lineTo(x,padT+H); ctx.stroke(); ctx.setLineDash([]); }
  // C4: demand / supply zones (right labels) + naked POCs (dashed from the left) - gamma map only
  if(isG){ const ZZ=tgVxsZones(J); const lab=[]; ctx.save(); ctx.font="bold 13px "+C("--font");
    ZZ.zones.forEach((z,j)=>{ if(z.hi<ylo||z.lo>yhi) return; const y0=Y(z.hi), y1=Y(z.lo), dem=z.kind!=="Supply", top=j===0;
      ctx.fillStyle=dem?(top?"#39E07A40":"#39E07A22"):(top?"#FF5A5A40":"#FF5A5A22"); ctx.fillRect(padL,y0,W,Math.max(2,y1-y0));
      if(top){ ctx.strokeStyle=dem?"#39E07A":"#FF5A5A"; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(padL,Y(z.poc)); ctx.lineTo(padL+W,Y(z.poc)); ctx.stroke(); }
      lab.push({y:(y0+y1)/2,txt:`${z.kind} ${vxN(z.lo,2)}–${vxN(z.hi,2)} · ${tgVxsK(z.n)}`,dem,top}); });
    lab.sort((a,b)=>a.y-b.y); let prv=-1e9; for(const o of lab){ o.ly=Math.max(o.y,prv+20); prv=o.ly; }
    for(const o of lab){ const tw=ctx.measureText(o.txt).width, x=padL+W-tw-14; ctx.fillStyle=o.top?(o.dem?"#39E07A":"#FF5A5A"):"#0A1426"; ctx.fillRect(x,o.ly-9,tw+10,18);
      ctx.strokeStyle=o.dem?"#39E07A":"#FF5A5A"; ctx.lineWidth=1.5; ctx.strokeRect(x,o.ly-9,tw+10,18); ctx.fillStyle=o.top?"#0A1426":(o.dem?"#39E07A":"#FF5A5A"); ctx.fillText(o.txt,x+5,o.ly+5); }
    for(const n of ZZ.naked){ if(n.y<ylo||n.y>yhi) continue; const y=Y(n.y), txt="← "+tgVxsDay(n.day)+" "+vxN(n.y,2), tw=ctx.measureText(txt).width;
      ctx.strokeStyle="#39E07Acc"; ctx.lineWidth=1.5; ctx.setLineDash([7,5]); ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W*0.55,y); ctx.stroke(); ctx.setLineDash([]);
      const x=padL+W*0.55+4; ctx.fillStyle="#0A1426"; ctx.fillRect(x,y-9,tw+10,18); ctx.strokeStyle="#39E07A"; ctx.lineWidth=1.2; ctx.strokeRect(x,y-9,tw+10,18); ctx.fillStyle="#39E07A"; ctx.fillText(txt,x+5,y+5); }
    ctx.restore(); }
  // candles (5 min) + front forward
  const pth=vxmPath(J); ctx.strokeStyle="#7FE7FF"; ctx.lineWidth=1.6; ctx.setLineDash([6,4]); ctx.beginPath(); let st0=false;
  for(const p of pth){ const v=p[2]; if(v==null||!isFinite(v)) continue; const x=X(tmin(p[0])), y=Y(v); st0?ctx.lineTo(x,y):ctx.moveTo(x,y); st0=true; } ctx.stroke(); ctx.setLineDash([]);
  const cw=Math.max(2,(X(5)-X(0))*0.7);
  for(const c of tgVxsCandles(J)){ const xm=X(c[0]+2.5), up=c[4]>=c[1], clr=up?"#39E07A":"#FF5A5A"; ctx.strokeStyle="#000"; ctx.lineWidth=3; ctx.beginPath(); ctx.moveTo(xm,Y(c[2])); ctx.lineTo(xm,Y(c[3])); ctx.stroke();
    ctx.strokeStyle=clr; ctx.lineWidth=1.4; ctx.beginPath(); ctx.moveTo(xm,Y(c[2])); ctx.lineTo(xm,Y(c[3])); ctx.stroke();
    const ya=Y(Math.max(c[1],c[4])), yb=Y(Math.min(c[1],c[4])); ctx.fillStyle=clr; ctx.fillRect(xm-cw/2,ya,cw,Math.max(1.5,yb-ya)); ctx.strokeStyle="#000"; ctx.lineWidth=1; ctx.strokeRect(xm-cw/2,ya,cw,Math.max(1.5,yb-ya)); }
  // price line across the whole chart at VIX now
  const lp=pth[pth.length-1]; if(lp&&lp[1]>=ylo&&lp[1]<=yhi){ ctx.strokeStyle="#FF5A5A"; ctx.lineWidth=1.2; ctx.setLineDash([2,3]); ctx.beginPath(); ctx.moveTo(padL,Y(lp[1])); ctx.lineTo(padL+W,Y(lp[1])); ctx.stroke(); ctx.setLineDash([]); }
  // C3: per-minute volume bars at the bottom of the gamma map (green = more bought than sold, red = more sold)
  const VW=VXW.J; if(isG&&VW&&VW.ok&&VW.vol&&(!J.day||!VW.date||VW.date===J.day)){ const tot=VW.vol.tot||[], mx=Math.max(1,...tot), bh=H*0.16, x0=tmin(VW.t0||"09:30"), bw=Math.max(1,(X(1)-X(0))*0.85);
    for(let i=0;i<tot.length;i++){ if(!tot[i]) continue; const x=X(x0+i), hh=bh*tot[i]/mx, up=(VW.vol.buy[i]||0)>=(VW.vol.sell[i]||0);
      ctx.fillStyle=up?"#39E07Ab0":"#FF5A5Ab0"; ctx.fillRect(x-bw/2,padT+H-hh,bw,hh); } }
  // role labels (gamma map): top 10 of the newest book, boxes at the left inside the plot
  const lb=B[B.length-1], vixNow=lp?lp[1]:lb.x, roles=isG?tgVxsRoles(lb,vixNow).slice(0,10):[];
  if(roles.length){ ctx.font="bold 13px "+C("--font"); const L=roles.filter(r=>r.y>=ylo&&r.y<=yhi).map(r=>({r,y:Y(r.y)})).sort((a,b)=>a.y-b.y); let prev=-1e9;
    for(const o of L){ o.ly=Math.max(o.y,prev+19); prev=o.ly; }
    const over=prev-(padT+H-8); if(over>0) for(const o of L) o.ly-=over;
    const x=padL+14; for(const o of L){ const r=o.r; o.clr=r.role==="PIN"?"#FFE27A":(r.role==="FLOOR"||(r.role==="MAGNET"&&r.up))?"#39E07A":"#FF5A5A";
      ctx.strokeStyle=o.clr; ctx.lineWidth=1.5; ctx.beginPath(); ctx.moveTo(padL,o.y); ctx.lineTo(x,o.ly); ctx.stroke(); }
    for(const o of L){ const r=o.r, clr=o.clr, txt=`#${r.rank} ${vxN(r.k,r.k%1?1:0)} ${tgVxsRoleTxt(r)}`, tw=ctx.measureText(txt).width;
      ctx.fillStyle="#0A1426"; ctx.fillRect(x,o.ly-9,tw+10,18); ctx.fillStyle=r.rank===1?clr+"55":"#0A1426"; ctx.fillRect(x,o.ly-9,tw+10,18); ctx.strokeStyle=clr; ctx.lineWidth=2; ctx.strokeRect(x,o.ly-9,tw+10,18);
      ctx.fillStyle=r.rank===1?"#fff":clr; ctx.fillText(txt,x+5,o.ly+5); } }
  ctx.restore();
  // axes
  ctx.font="15px "+C("--font"); ctx.fillStyle="#F2F6FC"; ctx.strokeStyle="#ffffff1c"; ctx.lineWidth=1;
  const sp=orNice(yhi-ylo,8); for(let v=Math.ceil(ylo/sp)*sp; v<=yhi+1e-9; v+=sp){ const y=Y(v); ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W,y); ctx.stroke(); ctx.textAlign="right"; ctx.fillText(vxN(v,sp<1?1:0),padL-6,y+5); }
  ctx.textAlign="center"; for(let hh=10;hh<=16;hh++) ctx.fillText(String(hh>12?hh-12:hh)+(hh>=12?" pm":" am"),X(hh*60),h-12); ctx.textAlign="left";
  // right axis tags: price (grey) + newest book time
  const xr=padL+W+4, tag=(y,txt,bg,fg)=>{ ctx.font="bold 14px "+C("--font"); const tw=ctx.measureText(txt).width; ctx.fillStyle=bg; ctx.fillRect(xr,y-10,Math.min(tw+8,padR-4),20); ctx.fillStyle=fg; ctx.fillText(txt,xr+4,y+5); };
  if(lp){ const yv=Math.max(padT+10,Math.min(padT+H-10,Y(lp[1]))); tag(yv,(lp[1]>yhi?"▲ ":lp[1]<ylo?"▼ ":"")+vxN(lp[1],2),"#C9D4E3","#0A1426"); if(lastBook) tag(Math.min(padT+H-10,yv+22),"book "+lastBook.t,"#FF5A5A","#fff"); }
  return {X,Y,padL,padT,W,H,lo:ylo,hi:yhi,t0,t1,k,lim,isG,strike:true}; }
/* hover card: strike roles (returns true when it handled the event) */
function tgVxsHover(e){ const g=VXM.geo, J=VXM.J; if(!g||!J||!J.books||!J.books.length) return false; const cv=e.target, G=cv.id==="vxmGC"?g.G:g.V; if(!G||!G.strike) return false;
  const r=cv.getBoundingClientRect(), x=e.clientX-r.left, y=e.clientY-r.top; const t=G.t0+(x-G.padL)/G.W*(G.t1-G.t0), v=G.lo+(G.hi-G.lo)*(1-(y-G.padT)/G.H);
  const hv=document.getElementById("vxmHov"); if(!hv) return true; const q=tgVxsBookIdx(J,t);
  if(q<0||v<G.lo||v>G.hi){ hv.textContent="Hover the map for the strike roles."; return true; }
  const b=J.books[q], E=tgVxsEdges(b.y); let i=-1; for(let j=0;j<b.k.length;j++) if(v>=E.lo[j]&&v<E.hi[j]) i=j;
  const pth=vxmPath(J); let pm=null; for(const p of pth) if(tmin(p[0])<=t+0.01) pm=p; const vx=pm?pm[1]:b.x;
  const head=`<span style="font-family:ui-monospace,Consolas,monospace">STRIKE ROLES · VIX options · ${VXM.book==="f"?"front expiry":"all months"} · book ${esc(b.t)}${b.live?" (live)":""} · cursor ${vxN(v,2)}</span>`;
  if(i<0){ hv.innerHTML=head+"<br>No listed strike here."; return true; }
  const R=tgVxsRoles(b,vx), ro=R.find(o=>o.i===i), gv=tgVxsVals(b,"g")[i]||0, vv=tgVxsVals(b,"v")[i]||0, c=(VXM.book==="f"?b.cf:b.ca)[i]||[0,0,0,0,0,0,0,0], dl=VXM.sign==="dealer";
  const help=ro?(ro.role==="PIN"?"holds price here":ro.up?"helps price UP":"helps price DOWN"):"";
  const bar=ro?`<span style="display:inline-block;vertical-align:middle;width:120px;height:10px;background:#ffffff22;margin-right:6px"><span style="display:block;height:10px;width:${Math.round(ro.pct*100)}%;background:${ro.role==="PIN"?"#FFE27A":ro.up?"#39E07A":"#FF5A5A"}"></span></span>${Math.round(ro.pct*100)}% of the strongest strike in this book · rank #${ro.rank} of ${R.length}`:"no dealer position on this basis";
  const cust=`customers ${dl?"long":"short"} ${tgVxsK(c[0])} calls, ${dl?"short":"long"} ${tgVxsK(c[1])} puts (open interest, assumed side)${c[4]||c[5]||c[6]||c[7]?` · today customers bought ${tgVxsK(c[4])} / sold ${tgVxsK(c[5])} calls, bought ${tgVxsK(c[6])} / sold ${tgVxsK(c[7])} puts (tape)`:""}`;
  const beh=gv>=0?`dealers are long gamma here: they sell VIX into rises and buy dips at ${vxN(b.k[i],b.k[i]%1?1:0)}`:`dealers are short gamma here: they buy VIX into rises and sell into drops - moves through ${vxN(b.k[i],b.k[i]%1?1:0)} speed up`;
  const ZZ=tgVxsZones(J), zh=ZZ.zones.find(z=>v>=z.lo&&v<=z.hi), nk=ZZ.naked.find(n=>Math.abs(n.y-v)<=0.06);
  const zl=(zh?`<br><b style="color:${zh.kind==="Supply"?"#FF5A5A":"#39E07A"}">${zh.kind} zone ${vxN(zh.lo,2)}–${vxN(zh.hi,2)}</b>: ${tgVxsK(zh.n)} VIX option contracts traded at these VIX levels over the last ${ZZ.Z.days} stored sessions${zh.buy_pct!=null?` · ${zh.buy_pct}% bought`:""}${zh===ZZ.zones[0]?" · the busiest zone (POC "+vxN(zh.poc,2)+")":""}`:"")+
    (nk?`<br><b style="color:#39E07A">Naked POC ${vxN(nk.y,2)}</b> from ${tgVxsDay(nk.day)} (${tgVxsK(nk.n)} contracts that day) - no session since has traded VIX through it`:"");
  hv.innerHTML=head+`<br><b>${ro?tgVxsRoleTxt(ro):"–"} ${vxN(b.k[i],b.k[i]%1?1:0)}</b> ${help} · strike drawn at VIX ≈ ${vxN(b.y[i],2)} (front forward on the strike) · gamma <b>${vxmK(gv)}</b> per pt · vanna <b>${vxmK(vv)}</b> per vol pt<br>${bar}<br>${cust}<br>${beh}${zl}`;
  return true; }
/* C3: whales vs crowd pane + tape box (under the read-out) */
function tgVxwPane(J){ const old=document.getElementById("tgVxw"); if(old) old.remove(); const rd=document.getElementById("vxmRead"); if(!rd) return;
  const W_=VXW.J, box=document.createElement("div"); box.id="tgVxw"; box.style.cssText="border:2px dashed #ffcc00;margin-top:8px;padding:8px 10px;background:#0b0f14;color:#F2F6FC;font:16px system-ui,sans-serif";
  if(!W_||!W_.ok){ const pre=W_&&W_.pre&&W_.pre.tot?` Overnight + pre-market so far: whales ${W_.pre.whale>=0?"+":""}${W_.pre.whale} lots, crowd ${W_.pre.crowd>=0?"+":""}${W_.pre.crowd} lots (${W_.pre.tot} contracts).`:"";
    box.innerHTML=`<b>TEST Whales vs crowd · VIX options</b> — ${W_?esc(W_.reason||"no data"):`waiting for <b>vix_whales_live.json</b> (${esc(VXW.err||"loading")}) - written every minute from 09:31 ET by the whales feed (DSM "TI whales feed")`}.${pre}`; rd.after(box); return; }
  const stale=J&&J.day&&W_.date!==J.day, t=W_.tape||{}, wc=t.who==="BUYERS"?"#39E07A":t.who==="SELLERS"?"#FF5A5A":"#C9D4E3", pre=W_.pre||{};
  const sg=x=>(x>=0?"+":"−")+tgVxsK(Math.abs(x));
  box.innerHTML=`<div style="display:flex;flex-wrap:wrap;gap:10px;align-items:flex-start"><div style="flex:1 1 520px;min-width:300px"><div style="font-size:17px"><b>Whales vs crowd · VIX options</b> · whales ≥ ${W_.whale_min} lots, crowd smaller · signed lots (bought − sold) from 09:30 · as of ${esc(W_.asof)}${stale?` · <span style="color:#FFE27A">tape day ${esc(W_.date)} ≠ map day ${esc(J.day)}</span>`:""}</div>`+
    `<div style="margin:3px 0"><b style="color:#4DA3FF">whales ${sg(W_.totals.whale)}</b> · <b style="color:#FFA64D">crowd ${sg(W_.totals.crowd)}</b> · <b style="color:#C77DFF">${W_.opp.length} opposed stretch${W_.opp.length===1?"":"es"}</b> (5 min each, purple ticks)${pre.tot?` · overnight + pre-market${pre.from?" since "+esc(pre.from):""}: whales ${sg(pre.whale)}, crowd ${sg(pre.crowd)} (${tgVxsK(pre.tot)} contracts)`:""}</div>`+
    `<canvas id="tgVxwC" style="width:100%;height:220px;display:block"></canvas></div>`+
    `<div id="tgVxwTape" style="flex:0 1 330px;min-width:260px;border:1px solid #556;border-radius:6px;padding:8px 10px;background:#111820;font-size:16px;line-height:1.45">`+
    `<div><b style="color:${wc};font-size:18px">${t.who==="NOBODY"?"BALANCED":esc(t.who)+" winning"}</b> <span style="color:#C9D4E3">(${esc(t.strength||"")})</span></div>`+
    `<div>last ${t.win_min||5} min: bought <b>${tgVxsK(t.bought||0)}</b> · sold <b>${tgVxsK(t.sold||0)}</b> · ${t.lots_min} lots a minute${t.whale_pct!=null?` · whales ${t.whale_pct}%`:""}</div>`+
    `<div>customers pushed <b>${esc(t.push||"flat")}</b>${t.vix_move!=null?` · VIX ${t.vix_move>=0?"+":""}${vxN(t.vix_move,2)} over the window`:""}${t.vix!=null?` · VIX ${vxN(t.vix,2)}`:""}</div>`+
    (t.absorption?`<div style="color:#FF9F43"><b>ABSORPTION</b> — ${esc(t.absorption)}</div>`:"")+
    (t.heavy?`<div style="color:#FFE27A"><b>Heavy volume</b>: ${t.heavy.n} contracts at ${esc(t.heavy.t)}, ${t.heavy.x}x normal (${esc(t.heavy.basis)})</div>`:"")+
    `<div style="color:#C9D4E3;font-size:14px">Source: exchange tape (VIX + VIXW options, aggressor side), every minute. Display only.</div></div></div>`;
  rd.after(box); tgVxwDraw(); }
function tgVxwDraw(){ const cv=document.getElementById("tgVxwC"), W_=VXW.J; if(!cv||!W_||!W_.ok) return; const {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h);
  const padL=62,padR=96,padT=14,padB=26, Wp=w-padL-padR, Hp=h-padT-padB, t0=570, t1=975, x0=tmin(W_.t0||"09:30");
  const A=W_.series.whale||[], Bc=W_.series.crowd||[], all=A.concat(Bc,[0]), lo=Math.min(...all), hi=Math.max(...all), pad=(hi-lo)*0.08||1;
  const X=t=>padL+Wp*(t-t0)/(t1-t0), Y=v=>padT+Hp*(1-(v-(lo-pad))/((hi-lo+2*pad)||1));
  ctx.fillStyle="rgb(14,27,51)"; ctx.fillRect(padL,padT,Wp,Hp);
  // fill between the lines (blue when whales above, orange when the crowd is above)
  for(let i=1;i<A.length;i++){ const xa=X(x0+i-1), xb=X(x0+i); ctx.fillStyle=(A[i]>=Bc[i])?"#1E4E8C80":"#8C5A1E80";
    ctx.beginPath(); ctx.moveTo(xa,Y(A[i-1])); ctx.lineTo(xb,Y(A[i])); ctx.lineTo(xb,Y(Bc[i])); ctx.lineTo(xa,Y(Bc[i-1])); ctx.closePath(); ctx.fill(); }
  ctx.strokeStyle="#ffffff80"; ctx.setLineDash([5,5]); ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(padL,Y(0)); ctx.lineTo(padL+Wp,Y(0)); ctx.stroke(); ctx.setLineDash([]);
  const line=(arr,clr)=>{ ctx.strokeStyle=clr; ctx.lineWidth=2.2; ctx.beginPath(); arr.forEach((v,i)=>{ const x=X(x0+i), y=Y(v); i?ctx.lineTo(x,y):ctx.moveTo(x,y); }); ctx.stroke(); };
  line(A,"#4DA3FF"); line(Bc,"#FFA64D");
  ctx.fillStyle="#C77DFF"; for(const o of W_.opp||[]){ const xa=X(tmin(o.t0)), xb=X(tmin(o.t1)); ctx.fillRect(xa,padT,Math.max(2,xb-xa),6); }
  ctx.font="14px "+C("--font"); ctx.fillStyle="#F2F6FC"; ctx.textAlign="right"; for(const v of [lo,0,hi]){ ctx.fillText((v<0?"−":v>0?"+":"")+tgVxsK(v),padL-6,Y(v)+5); }
  ctx.textAlign="center"; for(let hh=10;hh<=16;hh++) ctx.fillText(String(hh>12?hh-12:hh)+(hh>=12?" pm":" am"),X(hh*60),h-8); ctx.textAlign="left";
  const tag=(v,txt,bg)=>{ ctx.font="bold 14px "+C("--font"); const tw=ctx.measureText(txt).width, y=Math.max(padT+9,Math.min(padT+Hp-9,Y(v))); ctx.fillStyle=bg; ctx.fillRect(padL+Wp+4,y-10,Math.min(tw+8,padR-4),20); ctx.fillStyle="#0A1426"; ctx.fillText(txt,padL+Wp+8,y+5); };
  const fa=A[A.length-1]||0, fb=Bc[Bc.length-1]||0; tag(fa,(fa>=0?"+":"−")+tgVxsK(Math.abs(fa)),"#4DA3FF"); tag(fb+(Math.abs(Y(fa)-Y(fb))<20?(fb<fa?-1:1)*(hi-lo+2*pad)*22/Hp:0),(fb>=0?"+":"−")+tgVxsK(Math.abs(fb)),"#FFA64D"); }
/* read-out add-on: the top 5 roles now */
function tgVxsRead(){ if(!tgVxsOn()) return; const J=VXM.J, el=document.getElementById("vxmRead"); if(!J||!el) return;
  if(!J.books||!J.books.length){ el.insertAdjacentHTML("beforeend",`<div style="border:2px dashed #ffcc00;padding:4px 8px">TEST Strike map: this file has no strike book (written by vix_map.py before 2026-11-19) - showing the production map.</div>`); return; }
  const b=J.books[J.books.length-1], pth=vxmPath(J), lp=pth[pth.length-1], R=tgVxsRoles(b,lp?lp[1]:b.x).slice(0,5);
  document.querySelectorAll("#vxMap .vxmleg i").forEach(i=>i.style.background="linear-gradient(90deg,#FF4D4D,#0E1B33,#3DDC84)");
  el.insertAdjacentHTML("beforeend",`<div style="border:2px dashed #ffcc00;padding:4px 8px">TEST Strike map colours: <b style="color:#3DDC84">green</b> = dealers long gamma / vanna at the strike, <b style="color:#FF4D4D">red</b> = short (the key above describes the production map). White line edges = sign changes between strikes; white pills = the strongest strikes while they stay hot; dashed line = newest 30-min book. Book ${esc(b.t)}${b.live?" (live)":""}: `+R.map(r=>`<b style="color:${r.role==="PIN"?"#FFE27A":r.up?"#39E07A":"#FF5A5A"}">#${r.rank} ${vxN(r.k,r.k%1?1:0)} ${tgVxsRoleTxt(r)}</b>`).join(" · ")+
    `. Roles rule rv-v0: dealers long gamma = FLOOR below VIX / CEILING above (they lean against moves there); short gamma = MAGNET toward the strike (moves speed up). Green helps VIX up, red helps it down. Strikes drawn where the front forward reaches them.</div>`);
  const ZZ=tgVxsZones(J); el.insertAdjacentHTML("beforeend",ZZ.Z&&ZZ.Z.days?`<div style="border:2px dashed #ffcc00;padding:4px 8px">TEST zones: VIX option volume of the last <b>${ZZ.Z.days}</b> stored sessions (${esc(ZZ.Z.from)} → ${esc(ZZ.Z.to)}) by VIX level (each contract where the front forward reaches its strike). `+
    ZZ.zones.slice(0,4).map(z=>`<b style="color:${z.kind==="Supply"?"#FF5A5A":"#39E07A"}">${z.kind} ${vxN(z.lo,2)}–${vxN(z.hi,2)} · ${tgVxsK(z.n)}</b>`).join(" · ")+
    (ZZ.naked.length?` · naked POCs (dashed, untouched since): `+ZZ.naked.slice(0,5).map(n=>`${tgVxsDay(n.day)} ${vxN(n.y,2)}`).join(", "):"")+`. Demand = more contracts bought than sold there.</div>`
    :`<div style="border:2px dashed #ffcc00;padding:4px 8px">TEST zones: no earlier session in this file's history carries volume by level yet (written from 2026-11-21; older days need <code>run_wide.sh vixdays</code> once).</div>`);
  tgVxwPane(J); tgVxwFetch(false); }
