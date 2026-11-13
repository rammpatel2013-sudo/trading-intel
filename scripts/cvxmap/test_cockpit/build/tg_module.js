/* ============================================================ TEST COCKPIT (2026-11-08)
   Copy of the production cockpit (commit 824a06c, sha256 d1486e1d…). Every change from the MAP master tracker
   (claude/map-master-tracker-20261108.md) sits behind its own toggle. ALL toggles OFF = the production maths exactly
   (parity-tested on 11/06 data before use). Nothing here writes to production files; the production page keeps
   running on port 8899. Toggles are per browser (localStorage on this port only).
   v0 = a first rule whose numbers still come from the stage-1 investigation; its scores restart when the rule changes. */
const TG_DEF=[ // id, short label, tracker ids, version, help
  ["b2","One IV clock","B2","v1","0DTE IV is solved to 16:15 but the greeks run to 16:00. On: one clock for both. 16:00 = expiring SPXW stop trading at 16:00 and settle on the close; 16:15 = the vendor's convention. With r=0 Black-76 both give the SAME gamma at the current minute; projected columns, charm and vanna differ."],
  ["a5","Half-day clock","A5","v1","On an early-close day (ti.json session, or ?earlyclose=13:00 in the address to simulate) the 0DTE clock, the IV and the map end at that close."],
  ["b3","Quote filter","B3","v0","Drop a side's IV when its quote is unusable: bid 0, ask ≤ bid, or spread > 60% of mid. The other side is used, as for a failed solve."],
  ["b6","Gap fill","B6","v0","A strike left with no IV keeps its position: IV filled linearly from the nearest strikes with IV (production drops that strike)."],
  ["b5","Week IV = live","B5","v1","Week book IV from the live chain (same contract: expiry, AM/PM, strike), rescaled to the contract's real time left. Morning IV + ½×ATM shift only where live IV is missing. OI unchanged."],
  ["b4","Bad-book check","B4a","v0","Flag only (changes nothing): compares the 08:35 book IV with the live chain near spot. ⚠ when the median gap > 8 vol pts or > 25% of strikes are off by > 10 pts, or IVs are outside 3%–150% (10/28 was 50+ pts off). Normal intraday IV drift is several points, so the bar is set well above it."],
  ["e2","Smooth β","E2","v1","Smile β blended across zone centres (Gaussian, half a zone wide) instead of hard steps at the zone edges. Needs Smile ON."],
  ["d1","Full Smile slope","D1","v1","Adds Vega·db/dS to Smile gamma (the slope's own change with price). Needs Smile ON; with hard zones only the 1/S part counts."],
  ["c2","IV path","C2","v0","IV at a map price moves along β from the anchor spot: σ(S) = σ₀ + ∫ β/x dx. Greeks are computed at that IV. Smile ON adds the slope terms on top. Needs the β file."],
  ["d4","Real 5-min charm","D4","v1","Charm = the actual change in delta over the next 5 minutes (instead of rate × 5 min, which overstates the last 10–30 minutes)."],
  ["f2","Levels from raw","F2","v0","Peaks/troughs counted beyond 8% of the RAW field's 95th percentile, not of the colour field (so colour scaling and median removal don't decide which levels count)."],
  ["fx","Display labels","F1 F3–F5","v1","Legend says raw vs median-relative and day vs column scale; flat zone around troughs/peaks; how the lines are found; Smile OFF/ON zero-line check (model-sensitive ≥ 10 pts)."],
  ["sg","Shadow","SG","v0","Alma's shadow greeks (2026-11-13): IV moves with price by our measured β (the Smile button's β file) along the path from that minute's price. Gamma = gamma + vanna × (IV change per point); Delta pane = the plain option delta at that moving IV, computed directly; charm / vanna use the path IV. Overrides Smile while on (Smile measures the min-variance hedge, a different object - never both). Needs the β file."]];
// 2026-11-09: the Density DN1-DN6 and VIX VX1/VX2 toggles moved to the production page as options (OFF by default).
const TG_SEL={b2:["","1600","1615"]};
let TG={}; for(const [id] of TG_DEF) TG[id]=id in TG_SEL?"":false;
let TG_ANYG=false, TG_GEND=16*60;
const TG_STATS={rej:0,fill:0,wkLive:0,wkFb:0};
const FEED_END=16*60+15;   // cvx_live.py EXPIRY_MIN: the writer solves 0DTE IV to 16:15
function tgApply(){ TG_ANYG=!!(TG.c2||TG.d1||TG.d4||TG.sg); }
function tgLoad(){ try{ const j=JSON.parse(localStorage.getItem("tg_v1")||"{}"); for(const k in TG) if(k in j) TG[k]=j[k]; }catch(e){} tgApply(); }
function tgSave(){ try{ localStorage.setItem("tg_v1",JSON.stringify(TG)); }catch(e){} }
function tgOn(){ return TG_DEF.filter(([id])=>!!TG[id]).map(([id,lb,tid])=>tid+(TG_SEL[id]?" "+TG[id].slice(0,2)+":"+TG[id].slice(2):"")); }
function tgEarly(){ // early-close minute today, or null
  try{ const q=new URLSearchParams(location.search).get("earlyclose"); if(q&&/^\d\d:\d\d$/.test(q)) return tmin(q); }catch(e){}
  const ss=st.ti&&st.ti.session; const day=(typeof wkDayISO==="function")?wkDayISO():"";
  if(ss&&ss.early_close&&ss.date===day){ const m=/(\d\d):(\d\d)/.exec(String(ss.close||"")); if(m) return (+m[1])*60+(+m[2]); }
  return null; }
function tgEarlySim(){ try{ return !!new URLSearchParams(location.search).get("earlyclose"); }catch(e){ return false; } }
function tgClockSync(){ const e=TG.a5?tgEarly():null; MAP_T1=e||16*60; TG_GEND=MAP_T1+(TG.b2==="1615"?15:0); }
function tgRowsOn(){ return !!(TG.b3||TG.b2||(TG.a5&&tgEarly())); }
function tgKey(){ const a=[]; for(const [id] of TG_DEF) if(TG[id]) a.push(id+(TG[id]===true?"":TG[id]));
  if(TG.a5) a.push("ec"+tgEarly()); if(TG.c2){ const d=st.live&&st.live[st.sym]; a.push("s0"+(d&&d.spot||0).toFixed(1)); }
  return a.join(","); }
function tgN(x){ return ncdf(x); }
/* B2 / A5: the feed IV was solved with T = minutes to 16:15. Re-expressed on the greek clock (end TG_GEND).
   Exact: Black-76 with r = 0 prices on σ√T only, so σ' = σ·√((16:15 − t)/(end − t)) gives the same option price. */
function tgSg(sg,tobs){ if(!(sg>0)) return sg; if(!(TG.b2||(TG.a5&&tgEarly()))) return sg; if(!(tobs<TG_GEND)) return sg;
  const a=Math.max(FEED_END-tobs,1), b=Math.max(TG_GEND-tobs,1); return sg*Math.sqrt(a/b); }
function tgLiveTobs(d){ if(d&&d.mins_to_close>0) return FEED_END-d.mins_to_close; const c=liveClock(); return c.h*60+c.m; }
/* B3 v0 quote rule */
function tgQuoteOK(b,a){ b=+b; a=+a; if(!(b>0)||!(a>b)) return false; return (a-b)/((a+b)/2)<=0.6; }
function tgRowSg(k,S,civ,piv,snap,q,lr,tobs,isLive){ let c=civ||0, p=piv||0;
  if(TG.b3){ const cb=snap?_at(snap.cb,q):(lr&&lr.cB), ca=snap?_at(snap.ca,q):(lr&&lr.cA), pb=snap?_at(snap.pb,q):(lr&&lr.pB), pa=snap?_at(snap.pa,q):(lr&&lr.pA);
    if(c>0&&!tgQuoteOK(cb,ca)){ c=0; if(isLive) TG_STATS._rej++; } if(p>0&&!tgQuoteOK(pb,pa)){ p=0; if(isLive) TG_STATS._rej++; } }
  return tgSg(otmIV(k,S,c,p),tobs); }
/* B6 v0: keep the strike, fill its IV linearly in strike from the nearest strikes that have one */
function tgGapFill(out,isLive){ const ok=out.map(r=>r.sg>0); let n=0;
  for(let i=0;i<out.length;i++){ if(ok[i]) continue; let a=i-1,b=i+1; while(a>=0&&!ok[a]) a--; while(b<out.length&&!ok[b]) b++;
    let v=0; if(a>=0&&b<out.length) v=out[a].sg+(out[b].sg-out[a].sg)*(out[i].k-out[a].k)/(out[b].k-out[a].k); else if(a>=0) v=out[a].sg; else if(b<out.length) v=out[b].sg;
    if(v>0){ out[i].sg=v; out[i].filled=true; n++; } }
  if(isLive) TG_STATS.fill=n; return out; }
/* B5: live Week IV per contract */
let _tgWL={ts:null,m:null};
function tgWeekLive(){ const ts=st.live&&st.live.ts; if(_tgWL.ts===ts&&_tgWL.sym===st.sym) return _tgWL.m;
  const wk=st.live&&st.live.week&&st.live.week[st.sym]; let m=null;
  if(wk&&wk.rows&&wk.rows.length){ m=new Map(); for(const r of wk.rows) m.set(r.e+"|"+(r.set||"PM")+"|"+r.k,r); }
  _tgWL={ts,sym:st.sym,m}; return m; }
function tgLiveMin(){ const c=liveClock(); return c.h*60+c.m; }
function tgWeekLiveSg(r,S){ // live IV for this book contract on the real clock, or null
  const L=tgWeekLive(); if(!L) return null; const lr=L.get(r.e+"|"+(r.set||"PM")+"|"+r.k); if(!lr) return null;
  const sgL=otmIV(r.k,S,lr.cIV||0,lr.pIV||0); if(!(sgL>0)||!(lr.dte>=1)) return null;
  const tau=wkTauMin({e:r.e,set:r.set||"PM",exp_t:r.exp_t||(r.e+"T16:00")},wkDayISO(),tgLiveMin()); if(!(tau>0)) return null;
  return Math.max(0.01,sgL*Math.sqrt(lr.dte*1440/tau)); }
/* B4a v0: morning book vs live chain, flag only */
function tgBookCheck(){ const nb=st.ti&&st.ti.near_book&&st.ti.near_book[st.sym]; if(!nb||!nb.rows) return null;
  const S=st.live&&st.live[st.sym]&&st.live[st.sym].spot||0; if(!(S>0)) return null; const diffs=[]; let abs=0, n=0;
  for(const r of nb.rows){ const sg0=otmIV(r.k,S,r.cIV||0,r.pIV||0); if(!(sg0>0)) continue; if(Math.abs(r.k-S)<=0.04*S){ n++; if(sg0<0.03||sg0>1.5) abs++; }
    if(Math.abs(r.k-S)>100) continue; const lv=tgWeekLiveSg(r,S); if(lv>0) diffs.push(Math.abs(sg0-lv)*100); }
  diffs.sort((a,b)=>a-b); const med=diffs.length?diffs[Math.floor(diffs.length/2)]:null, s5=diffs.length?diffs.filter(x=>x>10).length/diffs.length:null;
  const bad=(diffs.length>=10&&(med>8||s5>0.25))||abs>0;
  const txt=diffs.length?`${diffs.length} strikes vs live: median gap ${med.toFixed(1)} pts, ${Math.round(s5*100)}% > 10 pts`:"no live Week quotes to compare";
  return {n:diffs.length,med,s5,abs,bad,txt:txt+(abs?` · ${abs} IVs outside 3–150%`:"")}; }
/* E2: smooth β (Gaussian kernel over zone centres, bandwidth half a zone; offline smooth.py, FD-verified 11/07) */
function tgCentres(edges){ const c=[], w=edges.length>1?edges[1]-edges[0]:1; c.push(edges[0]-w/2); for(let i=1;i<edges.length;i++) c.push((edges[i-1]+edges[i])/2); c.push(edges[edges.length-1]+w/2); return {c,bw:w/2}; }
function tgSmoothAt(m,dte){ if(!SB) return null; let edges, ys;
  if(!(dte>=1)){ edges=SB.dte0.edges_pct; ys=SB.dte0.beta.slice(); }
  else { const M=SB.multi; if(!M) return null; let ti=M.tenors.findIndex(([lo,hi])=>dte>=lo&&dte<=hi); if(ti<0) ti=M.tenors.length-1; edges=M.edges_pct; const row=M.beta[ti]; ys=row.slice();
    for(let i=0;i<ys.length;i++) if(ys[i]==null){ for(let d=1;d<row.length&&ys[i]==null;d++){ ys[i]=row[i-d]; if(ys[i]==null) ys[i]=row[i+d]; } } }
  if(ys.some(v=>v==null)) return null; const {c,bw}=tgCentres(edges); let sw=0,sy=0;
  for(let i=0;i<c.length;i++){ const w=Math.exp(-0.5*((m-c[i])/bw)**2); sw+=w; sy+=w*ys[i]; } return sw>0?sy/sw:null; }
function tgBetaSmooth(k,S,dte){ return tgSmoothAt(100*(k/S-1),dte); }
/* D1: db/dS for b = β(m(S))/S, m = 100(k/S − 1) */
function tgDbdS(r,S){ const dte=r.dte||0; const b=SMILE_MEASURED?smileBeta(r.k,S,dte):null;
  if(b==null) return (r.k/(S*S))*(r.sk||0);
  let dbdm=0; if(TG.e2){ const m=100*(r.k/S-1), e=1e-4, a=tgSmoothAt(m+e,dte), c=tgSmoothAt(m-e,dte); if(a!=null&&c!=null) dbdm=(a-c)/(2*e); }
  return dbdm*(-100*r.k/(S*S))/S - b/(S*S); }
/* C2 v0: IV along the β path from the anchor spot s0 (trapezoid, 8 steps); cached per row and price */
function tgPathSg(r,S){ const s0=r.s0||(st.live&&st.live[st.sym]&&st.live[st.sym].spot)||S; if(Math.abs(S-s0)<1e-9) return r.sg;
  const ck=(TG.e2?"e":"h")+(SB&&SB.generated)+"|"+s0+"|"+r.sg; if(!r._c2||r._c2k!==ck){ r._c2=new Map(); r._c2k=ck; }
  const key=Math.round(S*1e4); const hit=r._c2.get(key); if(hit!==undefined) return hit;
  const n=8, dte=r.dte||0; let integ=0, prev=null;
  for(let i=0;i<=n;i++){ const x=s0+(S-s0)*i/n; const b=smileBeta(r.k,x,dte); const f=(b==null?0:b)/x; if(prev!=null) integ+=(prev+f)/2*(S-s0)/n; prev=f; }
  const v=Math.max(0.01,r.sg+integ); r._c2.set(key,v); return v; }
/* SG (2026-11-13): IV along the measured-β path from the row's anchor s0 (Alma's shadow greeks), integrated EXACTLY:
   β is constant inside each moneyness zone (m = 100(k/x - 1)), so σ(S) = σ0 + Σ β_zone · ln(x_b / x_a) over the zone pieces
   between s0 and S; with E2 (smooth β) Simpson's rule, 64 steps. Its slope is β(S)/S at every price, so the shadow gamma
   (gamma + vanna·β/S) is exactly the derivative of the shadow delta. C2's 8-step path (tgPathSg) is left as it was. */
function tgShadowSg(r,S){ const s0=r.s0||(st.live&&st.live[st.sym]&&st.live[st.sym].spot)||S; if(!SB||Math.abs(S-s0)<1e-9) return r.sg;
  const ck="sg"+(TG.e2?"e":"h")+(SB&&SB.generated)+"|"+s0+"|"+r.sg; if(!r._sg||r._sgk!==ck){ r._sg=new Map(); r._sgk=ck; }
  const key=Math.round(S*1e4); const hit=r._sg.get(key); if(hit!==undefined) return hit;
  const dte=r.dte||0; let integ=0;
  if(TG.e2){ const n=64, h=(S-s0)/n; for(let i=0;i<=n;i++){ const x=s0+h*i, b=smileBeta(r.k,x,dte), w=(i===0||i===n)?1:(i%2?4:2); integ+=w*((b==null?0:b)/x); } integ*=h/3; }
  else { const E=dte>=1?(SB.multi&&SB.multi.edges_pct)||[]:(SB.dte0&&SB.dte0.edges_pct)||[]; const lo=Math.min(s0,S), hi=Math.max(s0,S);
    const xs=E.map(e=>r.k/(1+e/100)).filter(x=>x>lo&&x<hi).sort((a,b)=>a-b); const pts=[lo,...xs,hi];
    for(let i=0;i<pts.length-1;i++){ const a=pts[i], b2=pts[i+1], bm=smileBeta(r.k,(a+b2)/2,dte); integ+=(bm==null?0:bm)*Math.log(b2/a); }
    if(S<s0) integ=-integ; }
  const v=Math.max(0.01,r.sg+integ); r._sg.set(key,v); return v; }
function greekColTG(kind, rows, S, ty, cm){ // greekCol with the C2 / D1 / D4 / SG options (same maths as greekCol when all are off) + kind "delta"
  const sq=Math.sqrt(Math.max(ty,1e-7)); let tot=0; const sgOn=!!TG.sg&&!!SB, sm=!!st.smileOn&&!sgOn; if(sm) smileSlopes(rows); const c2=TG.c2&&!!SB;
  for(const r of rows){ if(!(r.sg>0)) continue; const pos=r.cp-r.pp; if(!pos) continue;
    const sg=sgOn?tgShadowSg(r,S):c2?tgPathSg(r,S):r.sg;
    const d1=(Math.log(S/r.k)+0.5*sg*sg*ty)/(sg*sq), d2=d1-sg*sq, p=PHI(d1);
    if(kind==="delta"){ tot+=pos*tgN(d1); continue; }   // TEST DL: dealer delta in contracts, the part that moves (calls and puts share N(d1); a put's -1 is a constant)
    const sS=sm?smileSS(r,S):0;
    if(kind==="gamma"){ let g=p/(S*sg*sq) + (sm? 2*sS*(-p*d2/sg) + sS*sS*(S*p*sq*d1*d2/sg) : 0);
      if(sm&&TG.d1) g+=S*p*sq*tgDbdS(r,S);                          // + Vega · db/dS
      if(sgOn){ const b=smileBeta(r.k,S,r.dte||0); if(b!=null) g+=(-p*d2/sg)*(b/S); }   // TEST SG: + Vanna · dσ/dS along the β path (Alma's shadow gamma)
      tot+=pos*g*2.5; }
    else if(kind==="charm"){ const smc=sm? sS*S*p*(d1*d2+1)/(2*sq)*(5/525600) : 0; let dd;
      if(TG.d4){ const t2=ty-5/525600; const n1=t2>0?tgN((Math.log(S/r.k)+0.5*sg*sg*t2)/(sg*Math.sqrt(t2))):(S>r.k?1:(S<r.k?0:0.5)); dd=n1-tgN(d1); }
      else dd=p*d2/(2*ty)*(5/525600);
      tot-=pos*(dd-smc)*(cm||1); }
    else tot-=pos*(-p*d2/sg)*0.01;
  }
  return tot; }

/* ===================== SCORING v1 read-outs (Mithil 2026-11-08 16:12 / 16:23; tracker §0.6, frozen rules) =====================
   One read-out per 15-min slot (09:45 … 15:45 ET): for production maths and each maths toggle alone, Smile OFF and ON,
   fixed basis OI + vol, ledger off, All book. Records only what is known at that minute (live chain + morning Week book).
   Grid: spot ± 2% (± 3% for 1DTE / Friday), step = strike step / 2. R = 95th percentile of |column| on that grid.
   Zero line: raw column changes sign between two rows (linear). Band = contiguous run around it with |g| ≤ 0.10·R
     (boundaries interpolated where |g| = 0.10·R).
   Peak / trough: local high (g > 0) / low (g < 0) beyond 0.08·R; ties (equal neighbours) count as part of the extreme.
     Band = the contiguous same-sign run around it with |g| ≥ 0.95·|extreme| (interpolated). Other runs never join it.
   Close / 1DTE / Friday prediction: zero line nearest spot on the profile at the target (target close − 10 min);
     equal distance → the lower price; no zero line on the grid → null (a miss, never dropped).
   The scorer (score_test.py) only joins these frozen records to later prices. */
const TG_SCORE_V="score-v1"; const TG_PAGE_V="__TG_PAGE_V__";
const TG_CFGS=[["off",{}],["b2_1600",{b2:"1600"}],["b2_1615",{b2:"1615"}],["a5",{a5:true}],["b3",{b3:true}],["b6",{b6:true}],["b5",{b5:true}],["e2",{e2:true}],["d1",{d1:true}],["c2",{c2:true}],["sg",{sg:true}]];
function tgGrid(S,pct,dp){ const out=[]; for(let p=Math.round(S*(1-pct)/dp)*dp;p<=S*(1+pct)+1e-9;p+=dp) out.push(+p.toFixed(4)); return out; }
function tgP95(col){ const a=col.map(Math.abs).sort((x,y)=>x-y); return Math.max(1e-12,a[Math.floor(a.length*0.95)]||1e-12); }
function tgCross(prices,col,i,lev){ const a=col[i-1], b=col[i]; return prices[i-1]+(prices[i]-prices[i-1])*(lev-a)/((b-a)||1); }
function tgZeros(prices,col,R){ const out=[], thr=0.10*R, n=col.length;
  for(let i=1;i<n;i++){ if(!((col[i-1]<0&&col[i]>=0)||(col[i-1]>0&&col[i]<=0))) continue; const z=tgCross(prices,col,i,0);
    let lo, hi;
    if(Math.abs(col[i-1])>thr) lo=tgCross(prices,col,i,Math.sign(col[i-1])*thr);
    else { let a=i-1; while(a>0&&Math.abs(col[a-1])<=thr) a--; lo=a>0?tgCross(prices,col,a,Math.sign(col[a-1])*thr):prices[0]; }
    if(Math.abs(col[i])>thr) hi=tgCross(prices,col,i,Math.sign(col[i])*thr);
    else { let b=i; while(b<n-1&&Math.abs(col[b+1])<=thr) b++; hi=b<n-1?tgCross(prices,col,b+1,Math.sign(col[b+1])*thr):prices[n-1]; }
    out.push({z:+z.toFixed(2),lo:+Math.min(lo,z).toFixed(2),hi:+Math.max(hi,z).toFixed(2)}); }
  return out; }
function tgExtremes(prices,col,R){ const pk=[],tr=[];
  for(let i=1;i<col.length-1;i++){ const v=col[i];
    const isPk=v>0.08*R&&v>col[i-1]&&v>=col[i+1], isTr=v<-0.08*R&&v<col[i-1]&&v<=col[i+1]; if(!isPk&&!isTr) continue;
    const lev=0.95*v, ok=x=>isPk?x>=lev:x<=lev; let a=i,b=i; while(a>0&&ok(col[a-1])) a--; while(b<col.length-1&&ok(col[b+1])) b++;
    const lo=a>0?tgCross(prices,col,a,lev):prices[a], hi=b<col.length-1?tgCross(prices,col,b+1,lev):prices[b];
    (isPk?pk:tr).push({p:prices[i],v:+v.toFixed(4),lo:+lo.toFixed(2),hi:+hi.toFixed(2)}); }
  return {pk,tr}; }
function tgNearestZero(zs,S){ let best=null; for(const z of zs){ if(best==null||Math.abs(z.z-S)<Math.abs(best-S)-1e-9||(Math.abs(Math.abs(z.z-S)-Math.abs(best-S))<=1e-9&&z.z<best)) best=z.z; } return best; }
function tgSessionClose(){ const e=tgEarly(); return e||16*60; }
function tgTargets(near,day){ // next PM expiry (1DTE) and the last PM expiry of this calendar week (Friday), each with its endpoint
  const ex=[...new Set((near||[]).filter(r=>(r.set||"PM")==="PM"&&r.e>day).map(r=>r.e))].sort(); if(!ex.length) return {d1:null,fri:null};
  const row=e=>near.find(r=>r.e===e&&(r.set||"PM")==="PM"); const wk=iso=>{ const d=new Date(iso+"T12:00:00Z"); const dow=(d.getUTCDay()+6)%7; return Math.floor((d.getTime()/86400000-dow)); };
  const thisWk=wk(day), inWk=ex.filter(e=>wk(e)===thisWk); const fri=inWk.length?inWk[inWk.length-1]:null;
  const mk=e=>{ if(!e) return null; const r=row(e); const xi=expInstant(r); return {e,exp_t:r.exp_t||(e+"T16:00"),target_ms:xi-10*60000}; };
  return {d1:mk(ex[0]),fri:fri?mk(fri):null}; }
function tgProfileAt(rows,prices,targetMs){ // gamma at a future instant: each contract on its own time left then; expired ones excluded
  const live=rows.filter(r=>expInstant(r)>targetMs);
  return prices.map(p=>{ let t=0; for(const r of live){ const tau=(expInstant(r)-targetMs)/60000; t+=greekCol("gamma",[r],p,tau/525600); } return t; }); }
function tgReadoutOne(d,day,now){ // current toggles + st.smileOn; basis/ledger fixed by the caller
  const S=d.spot, dp=(d.step||5)/MAP_SUB, live=mapRowsAt(null,null,d), near=nearRows()||[];
  const P=tgGrid(S,0.02,dp);
  const tyNow=Math.max(TG_GEND-now,1)/525600, colNow=P.map(p=>greekCol("gamma",live,p,tyNow)+(near.length?greekColWeek("gamma",near,p,now,day):0));
  const R=tgP95(colNow), i0=Math.max(0,Math.min(P.length-1,Math.round((S-P[0])/dp))), zN=tgZeros(P,colNow,R), ex=tgExtremes(P,colNow,R);
  const inWin=x=>Math.abs(x-S)<=0.01*S;
  const gS=colNow[i0]+(colNow[Math.min(P.length-1,i0+1)]-colNow[i0])*((S-P[i0])/dp);
  const tC=MAP_T1-10, tyC=Math.max(TG_GEND-tC,1)/525600, colC=P.map(p=>greekCol("gamma",live,p,tyC)+(near.length?greekColWeek("gamma",near,p,tC,day):0)), zC=tgZeros(P,colC,tgP95(colC));
  const out={g_spot:+gS.toFixed(4),R:+R.toFixed(4),zeros:zN.filter(z=>inWin(z.z)),peaks:ex.pk.filter(x=>inWin(x.p)),troughs:ex.tr.filter(x=>inWin(x.p)),
    close:{target_min:tC,pred:tgNearestZero(zC,S),zeros:zC.map(z=>z.z)}};
  const T=tgTargets(near,day), P3=tgGrid(S,0.03,dp);
  for(const k of ["d1","fri"]){ const tg=T[k]; if(!tg){ out[k]=null; continue; } const col=tgProfileAt(near,P3,tg.target_ms), zs=tgZeros(P3,col,tgP95(col));
    out[k]={e:tg.e,exp_t:tg.exp_t,pred:tgNearestZero(zs,S),zeros:zs.map(z=>z.z)}; }
  return out; }
function tgReadoutCfg(cfg,sm){ // atomic: set this config, compute, restore everything (safe to interleave with renders)
  const d=st.live&&st.live.SPX; if(!d||!d.rows||!(d.spot>0)) return null;
  const keepTG=Object.assign({},TG), keep={sym:st.sym,smileOn:st.smileOn,basis:st.basis,mapLed:st.mapLed}; const c=liveClock(), now=c.h*60+c.m, day=wkDayISO();
  try{ st.sym="SPX"; st.basis="oiv"; st.mapLed=false; for(const k in TG) TG[k]=(k in TG_SEL)?"":false; Object.assign(TG,cfg); tgApply(); tgClockSync(); st.smileOn=sm;
    return tgReadoutOne(d,day,now); }
  catch(e){ return {err:String(e).slice(0,200)}; }
  finally{ for(const k in TG) TG[k]=keepTG[k]; tgApply(); st.sym=keep.sym; st.smileOn=keep.smileOn; st.basis=keep.basis; st.mapLed=keep.mapLed; tgClockSync(); } }
function tgReadoutMeta(){ const d=st.live&&st.live.SPX; if(!d||!d.rows||!(d.spot>0)) return null; const c=liveClock(), now=c.h*60+c.m, day=wkDayISO();
  const nb=st.ti&&st.ti.near_book&&st.ti.near_book.SPX, close=tgSessionClose(), mtc=Math.max(close-now,1), terms={};
  for(const t of (d.term||[])) if(t&&t.dte!=null) terms[t.dte]=t.straddle;
  return {v:TG_SCORE_V,date:day,t:now,hm:tlabel(now),live_ts:st.live.ts,spot:d.spot,step:d.step||5,session_close:close,straddle0:d.straddle||null,
    im30:d.straddle>0&&close-now>=30?+(d.straddle*Math.sqrt(30/mtc)).toFixed(3):null,term_straddle:terms,
    ids:{page:TG_PAGE_V,smilebeta:SB&&SB.generated||null,book_as_of:nb&&(nb.as_of_date||nb.as_of)||null,book_pull:nb&&nb.pull_time||null,book_conflicts:nb&&nb.recon&&nb.recon.n_conflicts||0},
    basis:"oiv",book:"all"}; }
function tgReadoutAll(){ const m=tgReadoutMeta(); if(!m) return null; m.cfgs={}; for(const [name,cfg] of TG_CFGS) for(const sm of [false,true]) m.cfgs[name+(sm?"|S":"|P")]=tgReadoutCfg(cfg,sm); return m; }
function tgRawP95(grid){ const all=[]; for(const col of grid) for(const v of col) all.push(Math.abs(v)); all.sort((a,b)=>a-b); return Math.max(1e-9, all[Math.floor(all.length*0.95)]||1e-9); }
tgLoad();
