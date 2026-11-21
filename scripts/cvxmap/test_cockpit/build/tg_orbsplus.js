/* TEST OP (2026-11-21) - ORBS+ : the replay-video features as a live view on the Orbs tab (Mithil 11/21 "build all
   together"). Test cockpit only, five buttons on the Orbs header, all OFF by default; off = nothing drawn, nothing
   fetched, Orbs unchanged. Data = what the Orbs tab already has (wide_YYYYMMDD.json + wide_live_<SYM>.json) plus the
   Map's own minute log (flip line) and whales_live.json (push bars). No new Convex calls.
   Ridge  = display: each strike as a line, spikes = contracts traded at that strike in each block (calls + puts),
            cyan when calls traded more, pink when puts did; candles drawn on top. Orbs bubbles hidden while on.
   Flip   = MAP maths: the gamma flip of the Map's OI + vol book (same buildMap + zoneLines as the Map's Flip line, the
            Orbs "Expiries" choice as the book) - yellow dashes. 11/20 replay: price topped on it at 10:00 (7,192).
   Walls  = MAP maths: the 5 biggest dealer gamma strikes NOW on OI + signed ($ per 1% move), labels right of the
            current block. Yellow + = dealers long (absorb), purple - = short (speed up).
   Notes  = display: rule-built labels, no AI - biggest block at one strike today, first touch of the flip line,
            price crossing the biggest wall near it. Newest 4 shown.
   Strip  = MAP maths + tape: under the Orbs, charm (OI + signed, ES contracts per hour dealers must trade as time
            passes: green = BUY, red = SELL) and, per 15 min, outside traders' net delta (orange, whales_live.json,
            customer signed $ delta) vs dealers forced by the move (blue = -gamma at spot x move, OI + signed), in ES.
   Greeks come from the Orbs feed (wide_feed: one flat IV per expiry); scored nightly with the replay before main. */
const TG_OP_KEYS=[["ridge","Ridge","Each strike as a line; spikes = contracts traded there each block (cyan = calls traded more, pink = puts). Bubbles hidden."],
  ["flip","Flip","Gamma flip of the OI + volume book (yellow dashes), same rule as the Map's Flip line. Price touching it from below/above = test of the line."],
  ["walls","Walls $","5 biggest dealer gamma strikes now, OI + signed, $ per 1% move. Yellow + = dealers long (absorb), purple − = short (speed up)."],
  ["notes","Notes","Plain labels: biggest block at one strike, first touch of the flip line, price crossing the biggest wall."],
  ["strip","Strip","Under the Orbs: charm (ES per hour dealers trade as time passes) and, per 15 min, outside traders vs dealers forced by the move."]];
st.tgOp=(()=>{ try{ return Object.assign({},JSON.parse(load("tgOp","{}"))); }catch(e){ return {}; } })();
const TG_OP={flipKey:null,flip:null,wh:null,whAt:0,whBusy:false,notes:[]};
function tgOpOn(k){ return !!(st.tgOp&&st.tgOp[k]); }
function tgOpAny(){ return TG_OP_KEYS.some(([k])=>tgOpOn(k)); }
function tgOpBtns(){ return `<span class="orgrp"><span class="orlab" style="color:#ffcc00">TEST</span><span class="tgopseg">`+TG_OP_KEYS.map(([k,t,tt])=>
  `<button class="tgopb" data-op="${k}" aria-pressed="${tgOpOn(k)}" style="border:1px dashed #ffcc00" title="${esc(tt)}">${t}</button>`).join("")+`</span></span>`; }
document.addEventListener("click",e=>{ const b=e.target&&e.target.closest&&e.target.closest(".tgopb"); if(!b) return;
  const k=b.dataset.op; st.tgOp[k]=!st.tgOp[k]; save("tgOp",JSON.stringify(st.tgOp)); b.setAttribute("aria-pressed",String(!!st.tgOp[k]));
  if(k==="strip"&&st.tgOp.strip) tgOpWhFetch(); OR.key=""; try{ renderOrbs(); }catch(err){ console.error(err); } });
setInterval(()=>{ if(tgOpOn("strip")&&st.tab==="orbs") tgOpWhFetch(); },60000);
function tgOpWhFetch(){ if(TG_OP.whBusy) return; TG_OP.whBusy=true;
  fetch("whales_live.json",{cache:"no-cache"}).then(r=>r.ok?r.json():null).then(j=>{ TG_OP.wh=j&&j.ok?j:null; TG_OP.whAt=Date.now(); })
    .catch(()=>{ TG_OP.wh=null; }).finally(()=>{ TG_OP.whBusy=false; if(st.tab==="orbs"){ OR.key=""; try{ renderOrbs(); }catch(e){} } }); }
const tgOpBook=()=>({"0":"0dte","7":"week",all:"all"})[OR.book]||"all";
/* per-strike value maps for one slot */
function tgOpMap(sl,f){ const a=orArr(sl,f), m=new Map(); sl.k.forEach((k,i)=>m.set(k,a[i]||0)); return m; }
function tgOpSigned(sl,f){ const o=orArr(sl,f), t=orArr(sl,f+"t"); return o.map((v,i)=>(v||0)+(t[i]||0)); }   // OI + signed
/* flip line of the Map's OI + vol book (Map maths unchanged: basis / ledger restored right after) */
function tgOpFlip(){ const snaps=(st.flow&&st.flow[st.sym])||[], d=st.live&&st.live[st.sym]; if(!snaps.length||!d||!d.rows) return null;
  const book=tgOpBook(), key=[st.sym,book,snaps.length,snaps[snaps.length-1].t,st.smileOn?1:0].join("|"); if(TG_OP.flipKey===key) return TG_OP.flip;
  const b0=st.basis, l0=st.mapLed; let m=null;
  try{ st.basis="oiv"; st.mapLed=false; m=buildMap("gamma",series(st.sym),d,book); }finally{ st.basis=b0; st.mapLed=l0; }
  let pts=null; if(m){ const ZL=zoneLines(m); pts=[]; ZL.forEach((o,j)=>{ if(o&&m.times[j]<=m.now) pts.push([m.times[j],m.prices[0]+o.z*m.dp]); }); }
  TG_OP.flipKey=key; TG_OP.flip=pts; return pts; }
function tgOpFlipAt(pts,t){ if(!pts||!pts.length) return null; let best=null,bd=1e9; for(const p of pts){ const dd=Math.abs(p[0]-t); if(dd<bd){ bd=dd; best=p; } } return bd<=4?best[1]:null; }
/* traded contracts per strike per block (calls, puts) = change of the cumulative day volume */
function tgOpVol(P){ const out=[]; let prev=null;
  for(let q=0;q<P.bk.length;q++){ const sl=P.bk[q].s, c=tgOpMap(sl,"c"), p=tgOpMap(sl,"p"), m=new Map();
    const gap=!prev||P.bk[q].t0-P.bk[q-1].t0>OR.blk*3;   // first block / collector gap: volume of unknown minutes, not drawn
    if(!gap) for(const [k,v] of c){ const pc=prev.c.get(k)||0, pp=prev.p.get(k)||0; m.set(k,{c:Math.max(0,v-pc),p:Math.max(0,(p.get(k)||0)-pp)}); }
    out.push(m); prev={c,p}; }
  return out; }
function tgOpDraw(cv){ const P=OR.P, g=OR.g; if(!P||!g) return; const strip=document.getElementById("tgOp");
  if(strip) strip.hidden=!tgOpOn("strip"); if(!tgOpAny()) return;
  const {ctx}={ctx:cv.getContext("2d")}, y=g.y, b=P.bk[OR.i], step=P.step, colW=Math.max(2,g.W*OR.blk/(g.t1-g.t0)), X=g.X, top=OR_PAD.t, bot=g.h-OR_PAD.b;
  const vol=(tgOpOn("ridge")||tgOpOn("notes"))?tgOpVol(P):null; const notes=[];
  ctx.save(); ctx.beginPath(); ctx.rect(g.AXL,top,g.w-g.AXL,bot-top); ctx.clip();
  if(tgOpOn("ridge")&&vol){ ctx.fillStyle="#0A1426"; ctx.globalAlpha=0.94; ctx.fillRect(g.AXL,top,g.w-g.AXL,bot-top); ctx.globalAlpha=1;
    let vmax=1; for(let q=0;q<=OR.i;q++) for(const v of vol[q].values()) vmax=Math.max(vmax,v.c+v.p);
    const rowH=Math.abs(y.Y(0)-y.Y(step)), amp=Math.max(6,rowH*2.6);
    for(const k of P.ks){ if(k<y.lo-step||k>y.hi+step) continue; const y0=y.Y(k); let c=0,p=0; ctx.beginPath(); ctx.moveTo(X(P.bk[0].t0),y0);
      for(let q=0;q<=OR.i;q++){ const v=vol[q].get(k)||{c:0,p:0}; c+=v.c; p+=v.p; ctx.lineTo(X(P.bk[q].t0+OR.blk/2),y0-amp*Math.sqrt((v.c+v.p)/vmax)); }
      const f=Math.min(1,(c+p)/(vmax*8)); ctx.strokeStyle=(c>=p?"#3FC1E8":"#FF5FA2")+orA(0.35+0.65*f); ctx.lineWidth=0.8+1.6*f; ctx.stroke(); }
    for(let q=0;q<=OR.i;q++){ const c=P.bk[q].cd, x=X(c.t+OR.blk/2), up=c.c>=c.o, bw=Math.max(1.5,colW*0.6); ctx.strokeStyle=up?"#F1F5FB":"#9FB2CC"; ctx.fillStyle=up?"#0A1426":"#9FB2CC";
      ctx.beginPath(); ctx.moveTo(x,y.Y(c.h)); ctx.lineTo(x,y.Y(c.l)); ctx.stroke(); const yo=y.Y(Math.max(c.o,c.c)), yc=y.Y(Math.min(c.o,c.c)); ctx.fillRect(x-bw/2,yo,bw,Math.max(1,yc-yo)); ctx.strokeRect(x-bw/2,yo,bw,Math.max(1,yc-yo)); } }
  let flip=null; if(tgOpOn("flip")||tgOpOn("notes")) flip=tgOpFlip();
  const tNow=b.t0+OR.blk-1;
  if(tgOpOn("flip")&&flip&&flip.length){ ctx.save(); ctx.strokeStyle="#FFD23F"; ctx.lineWidth=2.2; ctx.setLineDash([7,5]); ctx.beginPath(); let on=false, last=null;
    for(const [t,v] of flip){ if(t>tNow) break; const xx=X(t), yy=y.Y(v); if(!on){ ctx.moveTo(xx,yy); on=true; } else ctx.lineTo(xx,yy); last=[t,v]; } ctx.stroke(); ctx.restore();
    if(last){ ctx.font="bold 15px "+C("--font"); const s="flip (OI + vol) "+Math.round(last[1]).toLocaleString(), tw=ctx.measureText(s).width, xx=Math.max(g.AXL+4,X(last[0])-tw-12), yy=y.Y(last[1])-8;
      ctx.fillStyle="#0A1426d9"; ctx.fillRect(xx-3,yy-15,tw+6,19); ctx.fillStyle="#FFD23F"; ctx.fillText(s,xx,yy); } }
  if(tgOpOn("walls")){ const sl=b.s, v=tgOpSigned(sl,"g"), idx=sl.k.map((k,i)=>i).filter(i=>sl.k[i]>=y.lo&&sl.k[i]<=y.hi).sort((i,j)=>Math.abs(v[j])-Math.abs(v[i])), pick=[];
    for(const i of idx){ if(pick.every(j=>Math.abs(sl.k[j]-sl.k[i])>=step*3)) pick.push(i); if(pick.length===5) break; }
    ctx.font="bold 15px "+C("--font"); const xs=Math.min(X(b.t0)+colW+10,g.w-170);
    for(const i of pick){ const k=sl.k[i], val=v[i], col=val>=0?OR_COL.pos:OR_COL.neg, yy=y.Y(k), s=orFk(k)+"  "+orMoney(val), tw=ctx.measureText(s).width;
      ctx.strokeStyle=col+"aa"; ctx.lineWidth=1.5; ctx.beginPath(); ctx.moveTo(g.AXL,yy); ctx.lineTo(xs-4,yy); ctx.setLineDash([2,4]); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle="#0A1426e6"; ctx.fillRect(xs-3,yy-11,tw+8,21); ctx.strokeStyle=col; ctx.strokeRect(xs-3,yy-11,tw+8,21); ctx.fillStyle=col; ctx.fillText(s,xs+1,yy+5); } }
  if(tgOpOn("notes")) for(const n of tgOpNotes(P,vol,flip)) notes.push(n);
  if(notes.length){ ctx.font="15px "+C("--font"); notes.slice(-4).forEach((n,i)=>{ const xx=X(n.t), yy=y.Y(n.v);
      ctx.fillStyle=n.col; ctx.beginPath(); ctx.arc(xx,yy,5,0,7); ctx.fill();
      const tw=ctx.measureText(n.s).width, bx=Math.max(g.AXL+4,Math.min(xx-tw/2,g.w-tw-12)), by=top+22+i*24;
      ctx.strokeStyle=n.col; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(xx,yy); ctx.lineTo(bx+tw/2,by+4); ctx.stroke();
      ctx.fillStyle="#0A1426ee"; ctx.fillRect(bx-4,by-15,tw+8,21); ctx.strokeStyle=n.col; ctx.strokeRect(bx-4,by-15,tw+8,21); ctx.fillStyle="#fff"; ctx.fillText(n.s,bx,by); }); }
  ctx.restore();
  if(tgOpOn("strip")) tgOpStrip();
  const sub=document.getElementById("orSub"); if(sub) sub.textContent+=" · TEST "+TG_OP_KEYS.filter(([k])=>tgOpOn(k)).map(x=>x[1]).join(" + "); }
/* rule-built notes up to the current block: [{t,v,s,col}] */
function tgOpNotes(P,vol,flip){ const out=[]; if(!P) return out;
  if(vol){ let best=null; for(let q=0;q<=OR.i;q++) for(const [k,v] of vol[q]){ const n=v.c+v.p; if(!best||n>best.n) best={n,k,q}; }
    if(best&&best.n>0) out.push({t:P.bk[best.q].t0,v:best.k,s:`biggest block: ${orFk(best.k)} · ${Math.round(best.n).toLocaleString()} lots ${tlabel(P.bk[best.q].t0)}`,col:"#3FC1E8",o:P.bk[best.q].t0}); }
  if(flip&&flip.length){ for(let q=1;q<=OR.i;q++){ const c=P.bk[q].cd, t=P.bk[q].t0; if(t<585) continue; const f=tgOpFlipAt(flip,t); if(f==null) continue;
      if(c.l<=f+2&&c.h>=f-2){ out.push({t,v:f,s:`touched the flip ${Math.round(f).toLocaleString()} at ${tlabel(t)}`,col:"#FFD23F",o:t}); break; } } }
  let lastX=-1; for(let q=1;q<=OR.i;q++){ const sl=P.bk[q].s, v=tgOpSigned(sl,"g"), x0=P.bk[q-1].cd.c, x1=P.bk[q].cd.c; let wi=-1;
    for(let i=0;i<sl.k.length;i++) if(Math.abs(sl.k[i]-x0)<=30&&(wi<0||Math.abs(v[i])>Math.abs(v[wi]))) wi=i;
    if(wi<0) continue; const k=sl.k[wi]; if((x0-k)*(x1-k)<0&&P.bk[q].t0-lastX>=30){ lastX=P.bk[q].t0; out.push({t:P.bk[q].t0,v:k,s:`crossed the ${orFk(k)} wall (${v[wi]>=0?"long":"short"} gamma) ${x1>k?"up":"down"} at ${tlabel(P.bk[q].t0)}`,col:v[wi]>=0?OR_COL.pos:OR_COL.neg,o:P.bk[q].t0}); } }
  return out.sort((a,b)=>a.o-b.o); }
/* strip series from the 1-minute slots (not the blocks): charm ES/h, gamma $ at spot, spot */
function tgOpSeries(){ const S=orSym(); if(!S||!S.slots.length) return null; const tEnd=OR.P?OR.P.bk[OR.i].t0+OR.blk-1:1e9, out=[];
  for(const sl of S.slots){ const t=tmin(sl.t); if(t>tEnd) break; const x=sl.x; let ch=0,gm=0; const c=tgOpSigned(sl,"ch"), g=tgOpSigned(sl,"g");
    for(let i=0;i<c.length;i++){ ch+=c[i]; gm+=g[i]; } out.push({t,x,es:-ch*1e9/(50*x),g:gm}); }
  return out; }
function tgOpStrip(){ const box=document.getElementById("tgOp"), cv=document.getElementById("tgOpC"), head=document.getElementById("tgOpHead"); if(!box||!cv) return;
  const ser=tgOpSeries(); if(!ser||!ser.length){ head.textContent="Strip: no Orbs data yet."; return; }
  const {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h); const AXL=66, t0=570, t1=975, W=w-AXL-10, X=t=>AXL+W*(t-t0)/(t1-t0), hA=Math.round(h*0.45), gap=18;
  ctx.font="15px "+C("--font"); ctx.fillStyle="#DCE6F2"; ctx.strokeStyle="#ffffff14";
  for(let hh=10;hh<=16;hh++){ const x=X(hh*60); ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,h-20); ctx.stroke(); ctx.fillText(String(hh>12?hh-12:hh)+(hh>=12?" pm":" am"),x-14,h-4); }
  // charm, cut where the expiry blows it up (last 20 min)
  const cs=ser.filter(p=>p.t<=950), cm=Math.max(1,...cs.map(p=>Math.abs(p.es))), Yc=v=>hA/2-(hA/2-6)*v/cm;
  ctx.strokeStyle="#ffffff40"; ctx.beginPath(); ctx.moveTo(AXL,Yc(0)); ctx.lineTo(w,Yc(0)); ctx.stroke();
  for(const p of cs){ ctx.fillStyle=p.es>=0?"#3EE08Acc":"#FF5A5Acc"; const y0=Yc(0), y1=Yc(p.es); ctx.fillRect(X(p.t),Math.min(y0,y1),Math.max(1,W/(t1-t0)),Math.abs(y1-y0)); }
  ctx.fillStyle="#DCE6F2"; ctx.fillText("charm, ES/h",4,14);
  // push bars per 15 min: outside traders (orange, whales_live.json) vs dealers forced (blue)
  const by=hA+gap, hB=h-by-24, B=[]; const wh=TG_OP.wh, scope=OR.book==="0"?"0dte":"all";
  const cum=wh&&wh.series&&wh.series[scope]?(()=>{ const a=wh.series[scope].whale||[], b=wh.series[scope].crowd||[]; return a.map((v,i)=>(v||0)+(b[i]||0)); })():null;
  const t0w=wh?tmin(wh.t0||"09:30"):570;
  for(let s=570;s<975;s+=15){ const pts=ser.filter(p=>p.t>=s&&p.t<s+15); if(!pts.length) continue; let dl=0;
    for(let i=1;i<ser.length;i++){ const p=ser[i], q=ser[i-1]; if(p.t<s||p.t>=s+15) continue; dl+=-q.g*1e9*((p.x-q.x)/q.x*100)/(50*p.x); }
    let ou=null; if(cum){ const a=Math.min(cum.length-1,Math.max(0,s-1-t0w)), e=Math.min(cum.length-1,Math.min(s+14,pts[pts.length-1].t)-t0w); if(e>=0) ou=(cum[e]-(s>t0w?cum[a]:0))*1e6/(50*pts[pts.length-1].x); }
    B.push({s,dl,ou}); }
  const bm=Math.max(1,...B.map(b=>Math.max(Math.abs(b.dl),Math.abs(b.ou||0)))), Yb=v=>by+hB/2-(hB/2-4)*v/bm, bw=Math.max(3,W*15/(t1-t0)*0.34);
  ctx.strokeStyle="#ffffff40"; ctx.beginPath(); ctx.moveTo(AXL,Yb(0)); ctx.lineTo(w,Yb(0)); ctx.stroke();
  for(const b of B){ const x=X(b.s); if(b.ou!=null){ ctx.fillStyle="#FFB347"; ctx.fillRect(x+2,Math.min(Yb(0),Yb(b.ou)),bw,Math.abs(Yb(b.ou)-Yb(0))); }
    ctx.fillStyle="#5EC8FF"; ctx.fillRect(x+4+bw,Math.min(Yb(0),Yb(b.dl)),bw,Math.abs(Yb(b.dl)-Yb(0))); }
  ctx.fillStyle="#DCE6F2"; ctx.fillText("per 15 min, ES",4,by+12);
  const L=cs[cs.length-1]||ser[ser.length-1], lb=B[B.length-1], f=v=>Math.round(Math.abs(v)).toLocaleString(), gNow=ser[ser.length-1].g;
  head.innerHTML=`<b>TEST Strip</b> · charm now: dealers <b style="color:${L.es>=0?"#3EE08A":"#FF7A7A"}">${L.es>=0?"BUY":"SELL"} ≈ ${f(L.es)} ES/hour</b> just because time passes`+
    ` · dealers <b style="color:${gNow>=0?"#FFD23F":"#C99BFF"}">${gNow>=0?"LONG":"SHORT"}</b> gamma at price (${orMoney(gNow)} per 1%, OI + signed)`+
    (lb?` · last 15 min: <span style="color:#FFB347">traders ${lb.ou==null?"n/a (whales_live.json)":(lb.ou>=0?"bought ":"sold ")+f(lb.ou)+" ES"}</span>, <span style="color:#5EC8FF">dealers forced to ${lb.dl>=0?"buy":"sell"} ${f(lb.dl)} ES</span>`:"")+
    `<br><span style="color:#9FB2CC">Top: green = dealers BUY as time passes, red = SELL (last 20 min cut: blows up at expiry). Bottom: orange = outside traders' net delta, blue = dealers forced by the move (model). Book = the Expiries button.</span>`;
  TG_OP.last={es:L.es,g:gNow,bars:B}; }
