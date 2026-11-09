/* ---- TEST VIX-tab toggles (2026-11-08; claude/vol-regime-knowledge-20261108.md). Data = test/volx.json, written by the
   TEST server only (free Cboe delayed quotes + daily history, no Convex). Fetched only while VX1, VX2 or DN6 is on. */
let VOLX=null;
async function tgVolxPoll(){ if(!(TG.vx1||TG.vx2||TG.dn6)) return;
  try{ const r=await fetch("test/volx.json?"+Date.now(),{cache:"no-store"}); if(!r.ok) throw new Error("HTTP "+r.status); VOLX=await r.json(); VOLX._err=null; }
  catch(e){ VOLX=VOLX||{}; VOLX._err=String(e&&e.message||e); }
  try{ if(st.tab==="vix") renderVix(); else if(st.tab==="density") renderDensity(series(st.sym)); }catch(e){} }
setInterval(tgVolxPoll,60000); setTimeout(tgVolxPoll,1500);
function tgVixKey(){ return (TG.vx1?"g":"")+(TG.vx2?"s":"")+(TG.vx1||TG.vx2?(VOLX&&(VOLX.asof||VOLX._err)||"none"):""); }
const TGV_LAB={CALM:["#3FD0B0","CALM / CONTANGO","green"],"CALM*":["#9BD06A","CALM — but a filter is on","green*"],UNCONFIRMED:["#FFD23F","UNCONFIRMED CROSS-DOWN","yellow"],
  MIXED:["#FF9A3D","MIXED / TRANSITIONING","orange"],CONFIRMED:["#E5484D","CONFIRMED VOL EXPANSION","red"]};
const TGV_PLAY={CALM:["Buy dips / trend-follow long: ES/MES or ATM–slightly ITM calls 3–7 DTE; trail stops under higher lows, partials into resistance.",
    "Full-size short premium: 0–7 DTE bull puts or iron condors at 15–20Δ; take 50–75% of credit, close before late-day pin risk."],
  UNCONFIRMED:["Fade the fear (usually a trap for late bears): ES/MES or bull call debit spreads; tight stop under the swing low, target VWAP / prior high.",
    "Opportunistic bull puts below support / VWAP while skew is bid; close on the snap-back; stop if SPX loses its 21d EMA."],
  MIXED:["Two-way chop: cut size 50%, futures over options, take profits at fixed levels.",
    "Half size; shorts at 8–12Δ, or bear calls if SPX is under its 8d EMA; 1.5–2× credit stop; nothing short-dated overnight."],
  CONFIRMED:["Sell rips / momentum short: bear put debit spreads (0.60/0.30Δ), ITM puts or short ES/MES; scale out when VIX9D/VIX spikes over 1.15.",
    "Pause short puts and condors; only bear calls well above resistance — until VIX9D/VIX has peaked and is back under 1.00 with SPX over its 8d EMA."]};
function tgVixRender(){ const g=document.getElementById("vxGauge"), s=document.getElementById("vxStack"); if(!g||!s) return;
  g.hidden=!TG.vx1; s.hidden=!TG.vx2; if(!TG.vx1&&!TG.vx2) return;
  try{ if(TG.vx1) tgVixGauge(g); }catch(e){ g.innerHTML=`<div class="vxerr">VX1 error: ${esc(e.message||e)}</div>`; }
  try{ if(TG.vx2) tgVixStack(s); }catch(e){ s.innerHTML=`<div class="vxerr">VX2 error: ${esc(e.message||e)}</div>`; } }
function tgVixWait(el,head){ el.innerHTML=head+`<div class="vxnote">${VOLX&&VOLX._err?`Could not read test/volx.json (${esc(VOLX._err)}) — the TEST server writes it once a minute; is <code>cvxmap-test</code> running the new code?`:"Loading test/volx.json…"}</div>`; }
function tgVixGauge(el){
  const head=`<div class="vxh"><h2>Regime gauge — VIXM / VIXY</h2><span class="vxsub">TEST VX1 · Doublewide Capital (11/07) · ratio 8-day vs 21-day EMA, confirmed by VIX9D/VIX &gt; 1.00 and SPX &lt; its 21-day EMA · daily closes; today provisional until the cash close · not tested by us as a predictor</span></div>`;
  const G=VOLX&&VOLX.gauge; if(!G||!G.length) return tgVixWait(el,head);
  const x=G[G.length-1], L=TGV_LAB[x.lab]||["#9DB0CA",x.lab,""], P=TGV_PLAY[x.lab==="CALM*"?"CALM":x.lab]||["",""], cu=VOLX.cues||{};
  const ck=b=>b?`<b style="color:#FF7A6B">✓ on</b>`:`<span style="color:#5FE0C0">✗ off</span>`;
  const chip=(t,v,sub)=>`<span style="display:inline-block;margin:2px 10px 2px 0"><small style="color:var(--mute)">${t}</small> <b>${v}</b> ${sub||""}</span>`;
  const cue=cu.close_shorts?`<b style="color:#FFD23F">Close-shorts cue ON</b>: VIX9D/VIX peaked ${vxN(cu.peak5,3)} (&gt; 1.15) and is back to ${vxN(x.v9r,3)} (≤ 1.05) — lock in short-side profits before the snap-back.`
    :cu.reentry?`<b style="color:#3FD0B0">Re-entry cue ON</b>: VIX9D/VIX peaked ${vxN(cu.peak10,3)} and is under 1.00 with SPX over its 8d EMA — bull put spreads can resume (early, before the EMA re-cross).`
    :(cu.peak10>1.15?`VIX9D/VIX peaked <b>${vxN(cu.peak10,3)}</b> in the last 10 sessions (over 1.15). Close-shorts cue needs ≤ 1.05 (now ${vxN(x.v9r,3)}); re-entry needs &lt; 1.00 ${cu.reentry_need&&cu.reentry_need.v9r_below_1?"✓":"✗"} and SPX over its 8d EMA ${vxN(x.s8,0)} ${cu.reentry_need&&cu.reentry_need.spx_above_8d?"✓":"✗"}.`
    :`No VIX9D/VIX spike over 1.15 in the last 10 sessions.`);
  const hist=G.slice(-10).map(r=>{ const l=TGV_LAB[r.lab]||["#9DB0CA"]; return `<span title="${r.d} · ratio ${r.r} · 8d ${r.e8} / 21d ${r.e21} · 9D/VIX ${r.v9r} · SPX ${r.spx} vs 21d ${r.s21}" style="display:inline-block;background:${l[0]};color:#000;border-radius:3px;padding:0 5px;margin:1px;font-size:11px;font-weight:700">${r.d.slice(5)}${r.prov?"*":""}</span>`; }).join("");
  el.innerHTML=head+`<div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px">
      <span class="vxtag" style="background:${L[0]};color:#000;font-size:15px;padding:3px 10px">${L[1]}${x.prov?" · provisional":""}</span>
      ${chip("VIXM/VIXY",vxN(x.r,4),`· 8d <b>${vxN(x.e8,4)}</b> ${x.e8>x.e21?"&gt;":"&lt;"} 21d <b>${vxN(x.e21,4)}</b>`)}
      ${chip("VIX9D/VIX",vxN(x.v9r,3),"&gt; 1.00 "+ck(x.f_v9))}
      ${chip("SPX",vxN(x.spx,0),`vs 21d EMA ${vxN(x.s21,0)}: below ${ck(x.f_spx)} · 8d EMA ${vxN(x.s8,0)}`)}
      <span style="color:var(--mute);font-size:12px">${esc(x.d)}</span></div>
    <div class="vxnote" style="color:var(--ink)">${cue}</div>
    <div class="vxnote" style="color:var(--ink)"><b>Directional:</b> ${esc(P[0])}<br><b>Credit spreads / condors:</b> ${esc(P[1])}${x.lab==="CALM*"?" <b>(a confirmation filter is on — he has no row for this; treat with care)</b>":""}</div>
    <canvas id="tgGaugeC" style="width:100%;height:170px;display:block;margin-top:8px"></canvas>
    <div class="vxnote">Last 10 sessions: ${hist} <span style="color:var(--faint)">(* = provisional) · history through ${esc(JSON.stringify(VOLX.hist_through||{}).replace(/[{}"]/g,"").replace(/,/g,", "))} · ${esc(VOLX.src||"")} · updated ${esc(String(VOLX.asof||"").slice(11,16))} ET</span></div>`;
  const cv=document.getElementById("tgGaugeC"); if(!cv) return; const {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h);
  const padL=46,padR=10,padT=8,padB=18,W=w-padL-padR,H=h-padT-padB, n=G.length; let lo=1e9,hi=-1e9; for(const r of G) for(const v of [r.r,r.e8,r.e21]){ lo=Math.min(lo,v); hi=Math.max(hi,v); }
  const pd=(hi-lo)*0.08||0.01; lo-=pd; hi+=pd; const X=i=>padL+W*(i+0.5)/n, Y=v=>padT+H*(1-(v-lo)/(hi-lo));
  G.forEach((r,i)=>{ const l=TGV_LAB[r.lab]||["#9DB0CA"]; ctx.fillStyle=l[0]+"26"; ctx.fillRect(padL+W*i/n,padT,W/n+0.5,H); });
  for(const [k,c,lw] of [["r","#ffffff",1.4],["e8","#3FE0A0",2.2],["e21","#FF6B6B",2.2]]){ ctx.strokeStyle=c; ctx.lineWidth=lw; ctx.beginPath(); G.forEach((r,i)=>i?ctx.lineTo(X(i),Y(r[k])):ctx.moveTo(X(i),Y(r[k]))); ctx.stroke(); }
  ctx.fillStyle="#C9D3E0"; ctx.font="11px system-ui"; ctx.fillText(hi.toFixed(3),2,padT+10); ctx.fillText(lo.toFixed(3),2,padT+H);
  ctx.textAlign="center"; for(let i=0;i<n;i+=Math.max(1,Math.round(n/6))) ctx.fillText(G[i].d.slice(5),X(i),h-4); ctx.textAlign="left";
  ctx.fillStyle="#fff"; ctx.fillText("ratio",padL+6,padT+12); ctx.fillStyle="#3FE0A0"; ctx.fillText("8d EMA",padL+44,padT+12); ctx.fillStyle="#FF6B6B"; ctx.fillText("21d EMA",padL+96,padT+12); }
function tgVixStack(el){
  const head=`<div class="vxh"><h2>Variance stack — VIX² + DSPX² ≈ VIXEQ²</h2><span class="vxsub">TEST VX2 · vol doesn't add, variance does · VIX² = what correlation transmits to the index · DSPX² = dispersion, diversified away · transmission share = VIX² ÷ (VIX² + DSPX²), related to but not the same as COR1M · Cboe 1-min, ~15 min delayed</span></div>`;
  if(!VOLX||!VOLX.q) return tgVixWait(el,head);
  const Q=VOLX.q, N=VOLX.stack_now, Pv=VOLX.stack_prev, v=s=>Q[s]&&Q[s].v, pv=s=>Q[s]&&Q[s].prev;
  const row=(t,a,b,d)=>`<span style="display:inline-block;margin:2px 14px 2px 0"><small style="color:var(--mute)">${t}</small> <b>${a}</b>${b!=null?` <span style="color:var(--mute)">prev ${b}</span>`:""}${d||""}</span>`;
  const I=VOLX.intra||{t:[]}, has=I.t&&I.t.length>1;
  el.innerHTML=head+`<div>${row("VIX",vxN(v("VIX")),vxN(pv("VIX")))}${row("DSPX",vxN(v("DSPX")),vxN(pv("DSPX")))}${row("VIXEQ",vxN(v("VIXEQ")),vxN(pv("VIXEQ")))}
      ${N?row("√(VIX²+DSPX²)",vxN(N.sqrt),null,` vs VIXEQ ${vxN(v("VIXEQ"))} · gap ${vxSgn(N.gap,2)}`):""}
      ${N?row("Transmission share",(N.share*100).toFixed(1)+"%",Pv?(Pv.share*100).toFixed(1)+"%":null,Pv?` · ${Math.abs(N.share-Pv.share)<0.002?"unchanged":N.share>Pv.share?"<b style='color:#FF9A6B'>thickening</b> (correlation carrying more)":"<b style='color:#5FE0C0'>thinning</b>"}`:""):""}
      ${row("COR1M",vxN(v("COR1M")),vxN(pv("COR1M")))}${row("COR3M",vxN(v("COR3M")),vxN(pv("COR3M")))}</div>
    ${has?`<canvas id="tgStackC" style="width:100%;height:190px;display:block;margin-top:6px"></canvas><canvas id="tgCorrC" style="width:100%;height:150px;display:block;margin-top:6px"></canvas>`
      :`<div class="vxnote">The session-to-date charts fill from 09:30 while the test server runs (1-minute Cboe quotes). Numbers above are the latest quotes and the prior close.</div>`}
    <div class="vxnote">Identity check = √(VIX² + DSPX²) − VIXEQ (async prints make it jump a few tenths in spikes). Read: share rising with COR1M = the index is catching single-name stress (correlation-led); VIXEQ up with the share flat = single-name demand, diversified away. Updated ${esc(String(VOLX.asof||"").slice(11,16))} ET.</div>`;
  if(!has) return;
  const T=I.t.map(tmin), t0=RTH_OPEN, t1=16*60;
  { const cv=document.getElementById("tgStackC"), {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h);
    const padL=46,padR=10,padT=8,padB=18,W=w-padL-padR,H=h-padT-padB, X=t=>padL+W*(t-t0)/(t1-t0);
    const a=I.VIX.map(x=>x>0?x*x:null), b=I.DSPX.map(x=>x>0?x*x:null), e=I.VIXEQ.map(x=>x>0?x*x:null);
    let hi=0; a.forEach((x,i)=>{ if(x!=null&&b[i]!=null) hi=Math.max(hi,x+b[i]); if(e[i]!=null) hi=Math.max(hi,e[i]); }); if(Pv) hi=Math.max(hi,Pv.tot); hi*=1.08; const Y=v=>padT+H*(1-v/(hi||1));
    const area=(top,bot,col)=>{ ctx.fillStyle=col; ctx.beginPath(); let st0=false; const pts=[]; T.forEach((t,i)=>{ if(top[i]!=null&&bot[i]!=null) pts.push([X(t),Y(top[i]),Y(bot[i])]); });
      if(!pts.length) return; ctx.moveTo(pts[0][0],pts[0][1]); pts.forEach(p=>ctx.lineTo(p[0],p[1])); for(let i=pts.length-1;i>=0;i--) ctx.lineTo(pts[i][0],pts[i][2]); ctx.closePath(); ctx.fill(); };
    area(a,a.map(()=>0),"#B8485Acc"); area(a.map((x,i)=>x!=null&&b[i]!=null?x+b[i]:null),a,"#7468B8cc");
    ctx.strokeStyle="#9FE3FF"; ctx.lineWidth=1.6; ctx.setLineDash([3,3]); ctx.beginPath(); let on=false; T.forEach((t,i)=>{ if(e[i]==null){ on=false; return; } on?ctx.lineTo(X(t),Y(e[i])):ctx.moveTo(X(t),Y(e[i])); on=true; }); ctx.stroke(); ctx.setLineDash([]);
    if(Pv){ ctx.strokeStyle="#ffffff66"; ctx.setLineDash([6,4]); for(const v0 of [Pv.tot,Pv.vix2]){ ctx.beginPath(); ctx.moveTo(padL,Y(v0)); ctx.lineTo(padL+W,Y(v0)); ctx.stroke(); } ctx.setLineDash([]); }
    ctx.fillStyle="#C9D3E0"; ctx.font="11px system-ui"; ctx.fillText(Math.round(hi),2,padT+10); ctx.fillText("0",2,padT+H);
    for(const t of [570,630,690,750,810,870,930,960]){ ctx.textAlign=t===570?"left":t===960?"right":"center"; ctx.fillText(tlabel(t),X(t),h-4); } ctx.textAlign="left";
    ctx.fillStyle="#FF8FA0"; ctx.fillText("VIX² (index variance)",padL+6,padT+H-6); ctx.fillStyle="#B9B0FF"; ctx.fillText("DSPX² (dispersion)",padL+6,padT+14); ctx.fillStyle="#9FE3FF"; ctx.fillText("dotted = VIXEQ² · dashed = prior close stack / VIX²",padL+150,padT+14); }
  { const cv=document.getElementById("tgCorrC"), {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h);
    const padL=46,padR=10,padT=8,padB=18,W=w-padL-padR,H=h-padT-padB, X=t=>padL+W*(t-t0)/(t1-t0);
    const sh=I.VIX.map((x,i)=>x>0&&I.DSPX[i]>0?100*x*x/(x*x+I.DSPX[i]*I.DSPX[i]):null), S=[[sh,"#FFD23F","share %"],[I.COR1M,"#FF9A3D","COR1M"],[I.COR3M,"#3FD0B0","COR3M"]];
    let lo=1e9,hi=-1e9; for(const [s] of S) for(const v of s) if(v!=null&&v>0){ lo=Math.min(lo,v); hi=Math.max(hi,v); } if(lo>hi){ lo=0; hi=1; } const pd=(hi-lo)*0.1||1; lo-=pd; hi+=pd; const Y=v=>padT+H*(1-(v-lo)/(hi-lo));
    for(const [s,c] of S){ ctx.strokeStyle=c; ctx.lineWidth=1.8; ctx.beginPath(); let on=false; T.forEach((t,i)=>{ const v=s[i]; if(!(v>0)){ on=false; return; } on?ctx.lineTo(X(t),Y(v)):ctx.moveTo(X(t),Y(v)); on=true; }); ctx.stroke(); }
    ctx.fillStyle="#C9D3E0"; ctx.font="11px system-ui"; ctx.fillText(hi.toFixed(1),2,padT+10); ctx.fillText(lo.toFixed(1),2,padT+H);
    for(const t of [570,630,690,750,810,870,930,960]){ ctx.textAlign=t===570?"left":t===960?"right":"center"; ctx.fillText(tlabel(t),X(t),h-4); } ctx.textAlign="left";
    let xo=padL+6; for(const [,c,n] of S){ ctx.fillStyle=c; ctx.fillText(n,xo,padT+12); xo+=ctx.measureText(n).width+14; } }
}
