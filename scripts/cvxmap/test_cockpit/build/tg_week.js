/* TEST WK (2026-11-20, Mithil 00:19: "8:35 book will be thin, overnight gap affects greeks ... use delta and real
   calculation to fill IV ... live week IV after market open is imp") + AB data check + P3 close pin.
   Toggle "Week IV" (WK v1), OFF by default; off = production maths.

   What the 11/19 data showed (claude/week-book-check-20261120.md):
     - our Week clock matches the vendor's own greeks on 4,000 trades (time ratio 1.00 on PM weeklies);
     - the 08:35 book has call IV BELOW put IV at the same strike (6.6 vol pts on the 11/20 AM monthly near price,
       2.6 on 1-day PM, ~0.6 on 4-6 days; the live chain: 0.0). Solved pre-open against a spot that does not match the
       overnight futures. Production takes the put side below price and the call side above -> a step at price;
     - the vendor prices 1-6 DTE off a FORWARD 5-14 pts above spot (live Week deltas; trade prints agree, 4-10);
       production uses spot for the Week book.
   WK, per Week-book contract:
     1. after 09:35 with the live Week chain for that contract: live IV (B5 maths: rescaled from whole days to the
        contract's real time left - verified: live Week IV / trade-print IV = 1.066 vs 1.055 expected on 11/20);
     2. otherwise the 08:35 book DELTA-WEIGHTED: sigma = w*put IV + (1-w)*call IV, w = N(ln(F/K) / (sigma_mid*sqrt(T))):
        at the money the average of the two (a full re-solve at the parity forward agreed within 0.5 vol pt on 11/19),
        far out the out-of-the-money side. A side outside 3%-150% is not used; > 10 vol pts apart -> Book clean (BQ)
        rule; nothing usable -> BQ fill or left out. The production ATM / VIX9D shift then applies as before.
     3. forward: F = S + B(expiry), B from the live Week deltas (median over strikes within 40 pts, 0.10 < delta < 0.90,
        >= 6 strikes); an expiry without its own B takes the nearest-DTE expiry's. Gamma, charm, vanna of the Week
        book are taken at F (d1 = ln(F/K)...; gamma per point of SPX = per point of F). No live Week chain -> B = 0.
   0DTE is unchanged (live 0DTE IVs are already forward-consistent; its own basis, ~2.5 pts on 11/19, needs the feed
   to carry deltas - locked file, later). */
const TG_WKB={ts:null,sym:null,m:null};
function tgWkBasis(){ const ts=st.live&&st.live.ts; if(TG_WKB.ts===ts&&TG_WKB.sym===st.sym) return TG_WKB.m;
  const wk=st.live&&st.live.week&&st.live.week[st.sym], S=st.live&&st.live[st.sym]&&st.live[st.sym].spot||0; let m=null;
  if(wk&&wk.rows&&wk.rows.length&&S>0){ const by=new Map();
    for(const r of wk.rows){ if(Math.abs(r.k-S)>40||!(r.dte>=1)) continue; const tau=r.dte*1440/525600, sq=Math.sqrt(tau);
      for(const [iv,D,c] of [[r.cIV,r.cD,1],[r.pIV,r.pD,0]]){ if(!(iv>0)||D==null) continue; const dd=c?D:D+1; if(!(dd>0.1&&dd<0.9)) continue;
        const d1=tgInvN(dd), F=r.k*Math.exp(d1*iv*sq-0.5*iv*iv*tau), id=r.e+"|"+(r.set||"PM");
        if(!by.has(id)) by.set(id,{dte:r.dte,v:[]}); by.get(id).v.push(F-S); } }
    m=new Map(); for(const [id,o] of by){ if(o.v.length<6) continue; o.v.sort((a,b)=>a-b); m.set(id,{dte:o.dte,b:o.v[Math.floor(o.v.length/2)]}); }
    if(!m.size) m=null; }
  TG_WKB.ts=ts; TG_WKB.sym=st.sym; TG_WKB.m=m; return m; }
function tgInvN(p){ // inverse standard normal (Acklam), |err| < 1.2e-9
  const a=[-39.6968302866538,220.946098424521,-275.928510446969,138.357751867269,-30.6647980661472,2.50662827745924],
    b=[-54.4760987982241,161.585836858041,-155.698979859887,66.8013118877197,-13.2806815528857],
    c=[-0.00778489400243029,-0.322396458041136,-2.40075827716184,-2.54973253934373,4.37466414146497,2.93816398269878],
    d=[0.00778469570904146,0.32246712907004,2.445134137143,3.75440866190742], pl=0.02425;
  if(p<pl){ const q=Math.sqrt(-2*Math.log(p)); return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  if(p>1-pl){ const q=Math.sqrt(-2*Math.log(1-p)); return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5])/((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1); }
  const q=p-0.5, r=q*q; return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q/(((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1); }
function tgWkB(r){ const m=tgWkBasis(); if(!m) return 0; const id=r.e+"|"+(r.set||"PM"); const o=m.get(id); if(o) return o.b;
  let best=null; for(const v of m.values()) if(best==null||Math.abs(v.dte-(r.dte||1))<Math.abs(best.dte-(r.dte||1))) best=v; return best?best.b:0; }
function tgWkTau(r){ const t=wkTauMin({e:r.e,set:r.set||"PM",exp_t:r.exp_t||(r.e+"T16:00")},wkDayISO(),Math.max(tgLiveMin(),MAP_T0)); return t>0?t/525600:null; }
function tgWkSg08(r,S){ // step 2: delta-weighted 08:35 IV
  const c=+r.cIV||0, p=+r.pIV||0, cv=tgBqValid(c), pv=tgBqValid(p);
  if(cv&&pv){ if(Math.abs(c-p)>BQ_GAP) return tgBqSg(r,S);
    const tau=tgWkTau(r); if(!tau) return r.k>=S?c:p; const F=S+tgWkB(r), sm=(c+p)/2, w=tgN(Math.log(F/r.k)/(sm*Math.sqrt(tau)));
    return w*p+(1-w)*c; }
  if(cv||pv) return cv?c:p;
  return tgBqSg(r,S); }
const TG_WKS={live:0,blend:0,fb:{}};
function tgWkLine(){ if(!TG.wk) return ""; const m=tgWkBasis(), S=st.live&&st.live[st.sym]&&st.live[st.sym].spot||0;
  const bs=m?[...m.entries()].sort((a,b)=>a[1].dte-b[1].dte).slice(0,5).map(([id,o])=>`${id.slice(5,10)}${id.endsWith("AM")?" AM":""} +${o.b.toFixed(1)}`).join(" · "):"no live Week chain - forward = spot";
  const g=tgWkGap(S); const c=liveClock(), open=c.h*60+c.m>=MAP_OPEN+5;
  return `<div class="bl"><b>TEST Week IV (WK v1):</b> ${TG_WKS.live} contracts on live Week IV · ${TG_WKS.blend} on the 08:35 book delta-weighted${open?"":" (pre-open: 08:35 only)"} · forward vs spot: ${bs}${g!=null?` · 08:35 call−put IV gap near price ${g.toFixed(1)} vol pts (raw; the blend removes the step)`:""}.</div>`; }
function tgWkGap(S){ const nb=st.ti&&st.ti.near_book&&st.ti.near_book[st.sym]; if(!nb||!nb.rows||!(S>0)) return null; const g=[];
  for(const r of nb.rows){ if(Math.abs(r.k-S)>50||!(r.dte<=1)) continue; const c=+r.cIV||0,p=+r.pIV||0; if(tgBqValid(c)&&tgBqValid(p)) g.push((c-p)*100); }
  if(g.length<5) return null; g.sort((a,b)=>a-b); return g[Math.floor(g.length/2)]; }

/* AB v0 DATA CHECK (round-2 step 3; Mithil: "show warning about data is bad" -> warn, never hide). Toggle "Data check".
   Each reason is a frozen rule; the map is outlined amber and the read-out says why. Levels stay drawn.
   Rules: live feed older than 3 min in 09:30-16:00 · Week book not from today · 08:35 1-day call/put gap near price > 3 vol pts
   (WK off) · B4a (08:35 vs live > 8 pts median) · after 09:35 < 50% of near-price Week contracts on live IV (WK on) ·
   > 20% of 0DTE strikes within 1% of price with no IV · pre-open (before 09:30: 08:35 book only).
   Far-out-of-the-money 0DTE IVs rising into the close are NOT a warning (normal tail repricing, Mithil 00:25). */
function tgWarn(){ const w=[]; const d=st.live&&st.live[st.sym], S=d&&d.spot||0, c=liveClock(), now=c.h*60+c.m;
  if(!d||!(S>0)) return ["no live data"];
  const ts=st.live.ts?Date.parse(st.live.ts.length<=19?st.live.ts+"-05:00":st.live.ts):NaN;
  if(now>=MAP_OPEN&&now<=MAP_T1&&isFinite(ts)&&Date.now()-ts>3*60000&&!tgEarlySim()) w.push(`live feed ${Math.round((Date.now()-ts)/60000)} min old`);
  const wi=typeof wkBookInfo==="function"?wkBookInfo():null; if(wi&&wi.stale) w.push(`Week book from ${wi.asof}, not today`);
  if(now<MAP_OPEN) w.push("pre-open: Week book is the 08:35 pull only");
  if(!TG.wk){ const g=tgWkGap(S); if(g!=null&&Math.abs(g)>3) w.push(`08:35 1-day call/put IV ${Math.abs(g).toFixed(1)} vol pts apart near price (Week IV wrong level, step at price - WK fixes)`); }
  else if(now>=MAP_OPEN+5){ const tot=TG_WKS.live+TG_WKS.blend; if(tot>0&&TG_WKS.nearLive!=null&&TG_WKS.nearTot>=10&&TG_WKS.nearLive/TG_WKS.nearTot<0.5) w.push(`only ${TG_WKS.nearLive}/${TG_WKS.nearTot} near-price Week contracts on live IV`); }
  try{ const bc=tgBookCheck(); if(bc&&bc.bad&&!TG.wk) w.push("08:35 book vs live: "+bc.txt); }catch(e){}
  let n=0,miss=0; for(const r of d.rows||[]){ if(Math.abs(r.k-S)>0.01*S) continue; n++; if(!((+r.cIV||0)>0)&&!((+r.pIV||0)>0)) miss++; }
  if(n>=5&&miss/n>0.2) w.push(`${miss}/${n} 0DTE strikes near price have no IV`);
  return w; }
function tgWarnLine(){ if(!TG.ab) return ""; const w=tgWarn();
  return w.length?`<div class="bl" style="border-left:4px solid #FFB020;padding-left:6px"><b style="color:#FFB020">⚠ DATA CHECK - read levels with care:</b> ${w.join(" · ")}.</div>`:`<div class="bl"><b>TEST Data check:</b> OK.</div>`; }
function tgWarnFrame(ctx,W,H){ if(!TG.ab) return; const w=tgWarn(); if(!w.length) return; ctx.save(); ctx.strokeStyle="#FFB020"; ctx.lineWidth=3; ctx.setLineDash([10,6]);
  ctx.strokeRect(2,2,W-4,H-4); ctx.setLineDash([]); ctx.font="bold 13px system-ui"; const t="⚠ DATA CHECK: "+w[0]+(w.length>1?` (+${w.length-1})`:""), tw=ctx.measureText(t).width;
  ctx.fillStyle="#000000d8"; ctx.fillRect(8,6,tw+12,20); ctx.fillStyle="#FFB020"; ctx.fillText(t,14,21); ctx.restore(); }

/* P3 v0 CLOSE PIN (round-2 step 4; Mithil 00:19: "i want close pin level"). Toggle "Close pin". Frozen rule cp-v0:
   the gamma profile projected to 15:50 (close - 10 min; the map's book and basis, current minute's positions and IVs),
   grid spot +-1.5%, half-strike step:
     dealers long gamma at spot at 15:50 -> PIN = the positive-gamma peak (>= 8% of the profile's 95th pct |value|)
       nearest spot; none -> the nearest zero of the profile (edge of the long-gamma zone);
     short gamma at spot -> no pin; the nearest zero is drawn as the flip price must reclaim (label "no pin").
   One dashed magenta line across the map at the level, label with the rule outcome. Scored nightly against the
   16:00 close with the other read-outs (out.close.pin). */
const TG_PIN={key:null,v:null};
function tgPinCalc(){ const d=st.live&&st.live[st.sym]; if(!d||!d.rows||!(d.spot>0)) return null; const c=liveClock(), now=c.h*60+c.m;
  const book=st.mapBook||"all", key=[st.live.ts,st.sym,book,st.basis,tgKey(),st.smileOn?1:0,MAP_T1].join("|"); if(TG_PIN.key===key) return TG_PIN.v;
  const S=d.spot, dp=(d.step||5)/MAP_SUB, day=wkDayISO(), P=tgGrid(S,0.015,dp), tC=MAP_T1-10;
  const live=book!=="week"?mapRowsAt(null,series(st.sym),d):[], near=book!=="0dte"?(nearRows()||[]):[];
  const tyC=Math.max(TG_GEND-Math.max(tC,now),1)/525600;
  const col=P.map(p=>(live.length?greekCol("gamma",live,p,tyC):0)+(near.length?greekColWeek("gamma",near,p,tC,day):0));
  const v=tgPinFrom(P,col,S,dp);
  if(v) v.t=tC; TG_PIN.key=key; TG_PIN.v=v; return v; }
function tgPinFrom(P,col,S,dp){ // cp-v0 on a gamma profile (any grid); only points within +-1.5% of spot count
  const keep=P.map((p,i)=>Math.abs(p-S)<=0.015*S+1e-9?i:-1).filter(i=>i>=0); if(keep.length<3) return null;
  const P2=keep.map(i=>P[i]), c2=keep.map(i=>col[i]), R=tgP95(c2), zs=tgZeros(P2,c2,R), ex=tgExtremes(P2,c2,R);
  let i0=0; for(let i=1;i<P2.length;i++) if(Math.abs(P2[i]-S)<Math.abs(P2[i0]-S)) i0=i;
  if(c2[i0]>0){ let best=null; for(const x of ex.pk) if(best==null||Math.abs(x.p-S)<Math.abs(best-S)) best=x.p;
    if(best!=null) return {lvl:best,kind:"pin"}; const z=tgNearestZero(zs,S); return {lvl:z,kind:z!=null?"edge":"none"}; }
  const z=tgNearestZero(zs,S); return {lvl:z,kind:"nopin"}; }
function tgPinOverlay(ctx,m,X,Y,padL,padT,W,H){ if(!TG.pin) return; const v=tgPinCalc(); if(!v||v.lvl==null) return;
  const row=(v.lvl-m.prices[0])/m.dp, y=Y(row); if(!(y>=padT&&y<=padT+H)) return;
  ctx.save(); ctx.strokeStyle=v.kind==="nopin"?"#FF6BD5aa":"#FF6BD5"; ctx.lineWidth=v.kind==="pin"?3:2; ctx.setLineDash([12,6]);
  ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W,y); ctx.stroke(); ctx.setLineDash([]);
  const t=v.kind==="pin"?`close pin ${fmtN(v.lvl)}`:v.kind==="edge"?`close pull ${fmtN(v.lvl)} (no peak - long-gamma edge)`:`no pin (short gamma) · flip ${fmtN(v.lvl)}`;
  ctx.font="bold 13px system-ui"; const tw=ctx.measureText(t).width, x=padL+8; ctx.fillStyle="#000000d0"; ctx.fillRect(x-4,y-15,tw+8,18); ctx.fillStyle="#FF6BD5"; ctx.fillText(t,x,y-1); ctx.restore(); }
function tgPinLine(){ if(!TG.pin) return ""; const v=tgPinCalc(); if(!v) return "";
  return `<div class="bl"><b>TEST Close pin (cp-v0, 15:50 profile):</b> ${v.lvl==null?"no level in ±1.5%":v.kind==="pin"?`dealers long gamma into the close → pin <b>${fmtN(v.lvl)}</b>`:v.kind==="edge"?`long gamma, no clear peak → pull to the zone edge ${fmtN(v.lvl)}`:`dealers SHORT gamma into the close → no pin; flip ${fmtN(v.lvl)}`}.</div>`; }
