/* ---- TEST Density toggles (2026-11-08, Derivatives Don's 11/06 version; claude/don-density-versions.md v2026-11-06).
   All OFF = production Density exactly. None of them changes the base cone (DEN.runs med/p10/p90); DN1 only adds touch
   levels to the worker input, DN6 only changes the cone width when the symbol is QQQ and Width = VIX. */
function tgDenKey(){ return (TG.dn1?"|dn1":"")+(TG.dn6&&st.sym==="QQQ"&&tgVxn()?"|vxn":""); }
function tgVxn(){ const q=typeof VOLX!=="undefined"&&VOLX&&VOLX.q; return q&&q.VXN&&q.VXN.v>0?q.VXN.v:null; }
function tgDenVol(){ return TG.dn6&&st.sym==="QQQ"&&tgVxn()?tgVxn():denVix(); }
function tgDenVolName(){ return TG.dn6&&st.sym==="QQQ"&&tgVxn()?"VXN":"VIX"; }
// DN1 walls + support. Fade top (production) = largest call OI to +5%, both books. Ours, so the lines differ:
//   call wall = largest 1-7 DTE call OI above spot to +8% · put wall = largest 1-7 DTE put OI below to -8% ·
//   support = the round strike (100 pts SPX, 10 SPY/QQQ) below spot with the most put OI, both books, to -8%.
function tgDenWalls(B){ if(!B) return null; if(B._tgW) return B._tgW;
  const d=st.live&&st.live[st.sym]; if(!d||!d.rows) return null; const S0=B.S0;
  const add=(m,k,v)=>{ if(v>0) m.set(k,(m.get(k)||0)+v); }, c0=new Map(), p0=new Map(), cw=new Map(), pw=new Map();
  for(const r of d.rows){ add(c0,r.k,r.cOI||0); add(p0,r.k,r.pOI||0); }
  const nb=st.ti&&st.ti.near_book&&st.ti.near_book[st.sym]; for(const r of (nb&&nb.rows)||[]){ add(cw,r.k,r.cOI||0); add(pw,r.k,r.pOI||0); add(c0,r.k,r.cOI||0); add(p0,r.k,r.pOI||0); }
  const best=(m,ok)=>{ let k0=null,v0=0; for(const [k,v] of m) if(ok(k)&&v>v0){ v0=v; k0=k; } return [k0,v0]; };
  const unit=st.sym==="SPX"?100:10, rnd=k=>Math.abs(k/unit-Math.round(k/unit))<1e-9;
  const [cwk,cwv]=best(cw,k=>k>S0&&k<=S0*1.08), [pwk,pwv]=best(pw,k=>k<S0&&k>=S0*0.92), [suk,suv]=best(p0,k=>k<S0&&k>=S0*0.92&&rnd(k));
  let near=0; for(const m of [c0,p0]) for(const [k,v] of m) if(Math.abs(k-S0)<=S0*0.015) near+=v;
  const out={cw:cwk,cwOI:cwv,pw:pwk,pwOI:pwv,support:suk,supOI:suv,nearOI:near,unit,week:!!(nb&&nb.rows&&nb.rows.length)};
  if(DEN.board===B) B._tgW=out; return out; }
function tgDenTouch(lv,B){ if(!TG.dn1||!lv) return; const W=tgDenWalls(B); if(W) lv.touch=lv.touch.concat([W.support,W.cw,W.pw]); }
// DN2 split axis: board levels outside the chart's price range go into a strip under the cone
function tgDenSplit(B,lo,hi){ if(!TG.dn2||!B) return null;
  const L=[[B.trough,"#FF4D6A","FULL-BOOK TROUGH · short-gamma plateau"],[B.fade,"#F2B33D","FADE TOP"],[B.fullFlip,"#7FB8FF","FULL FLIP"],[B.breakdown,"#FF7A50","BREAKDOWN"]];
  if(TG.dn1){ const W=tgDenWalls(B); if(W) L.push([W.support,"#FF5070","ROUND-NUMBER SUPPORT"],[W.cw,"#E07BFF","CALL WALL (1–7 DTE)"],[W.pw,"#FF9AC8","PUT WALL (1–7 DTE)"]); }
  const off=L.filter(x=>x[0]!=null&&(x[0]<lo||x[0]>hi)); if(!off.length) return null;
  return {h:Math.min(110,40+off.length*18),levels:off,vals:off.map(x=>x[0])}; }
function tgDenDrawSplit(ctx,SP,padL,W,y0){ if(!SP) return; const top=y0+10, h=SP.h-14;
  ctx.fillStyle="#0b101a"; ctx.fillRect(padL,top,W,h); ctx.strokeStyle="#ffffff22"; ctx.lineWidth=1; ctx.strokeRect(padL,top,W,h);
  ctx.strokeStyle="#C9D3E0"; ctx.lineWidth=1.5; for(const x of [padL-6,padL+W-6]){ ctx.beginPath(); ctx.moveTo(x,top+2); ctx.lineTo(x+12,top-6); ctx.stroke(); }
  const v=SP.levels.map(l=>l[0]), mn=Math.min(...v), mx=Math.max(...v), pad=Math.max(5,(mx-mn)*0.25), a=mn-pad, b=mx+pad, Y=p=>top+6+(h-12)*(1-(p-a)/(b-a||1));
  ctx.font="12px system-ui"; ctx.fillStyle="#C9D3E0"; ctx.textAlign="right"; ctx.fillText(fmtN(Math.round(b)),padL-6,top+12); ctx.fillText(fmtN(Math.round(a)),padL-6,top+h-2); ctx.textAlign="left";
  const lab=SP.levels.slice().sort((p,q)=>q[0]-p[0]).map(l=>[Y(l[0]),l]); for(let i=1;i<lab.length;i++) if(lab[i][0]-lab[i-1][0]<16) lab[i][0]=lab[i-1][0]+16;
  ctx.font="bold 12px system-ui"; ctx.textAlign="right";
  for(const [yl,[p,c,t]] of lab){ const y=Y(p); ctx.strokeStyle=c; ctx.lineWidth=1.6; ctx.beginPath(); ctx.moveTo(padL,y); ctx.lineTo(padL+W,y); ctx.stroke();
    const s=`${t} ${fmtN(p,0)}`, tw=ctx.measureText(s).width; ctx.fillStyle="#05080fd9"; ctx.fillRect(padL+W-tw-12,yl-12,tw+10,16); ctx.fillStyle=c; ctx.fillText(s,padL+W-7,yl); }
  ctx.textAlign="left"; ctx.font="11px system-ui"; ctx.fillStyle="#8a97a8"; ctx.fillText("split axis — levels outside the chart's price range (TEST DN2)",padL+6,top+h-3); }
// DN1 lines on the main chart (drawn with the production hl() helper)
function tgDenLines(hl,B){ if(!TG.dn1||!B) return; const W=tgDenWalls(B); if(!W) return;
  if(W.cw!=null&&W.cw!==B.fade) hl(W.cw,"#E07BFF",[4,3],`CALL WALL ${fmtN(W.cw,0)} (1–7 DTE)`);
  if(W.pw!=null&&W.pw!==B.trough) hl(W.pw,"#FF9AC8",[4,3],`PUT WALL ${fmtN(W.pw,0)} (1–7 DTE)`);
  if(W.support!=null) hl(W.support,"#FF5070",[1,3],`ROUND-NUMBER SUPPORT ${fmtN(W.support,0)}`); }
// histogram helpers (odds chart): quantile and mass of the drawn closes
function tgHq(H,q){ const tot=H.c.reduce((a,b)=>a+b,0)||1; let cum=0; for(let i=0;i<H.c.length;i++){ const c=H.c[i]/tot; if(cum+c>=q) return H.lo+(i+(q-cum)/(c||1))*H.w; cum+=c; } return H.lo+H.c.length*H.w; }
function tgHm(H,a,b){ const tot=H.c.reduce((x,y)=>x+y,0)||1; let m=0; for(let i=0;i<H.c.length;i++){ const x0=H.lo+i*H.w, x1=x0+H.w, ov=Math.max(0,Math.min(b,x1)-Math.max(a,x0)); m+=H.c[i]*ov/H.w; } return m/tot; }
function tgOddsLines(vl,B){ if(!TG.dn1) return; const W=tgDenWalls(B); if(!W) return; vl.push([W.support,"#FF5070","support"],[W.cw,"#E07BFF","call wall"],[W.pw,"#FF9AC8","put wall"]); }
function tgOddsDraw(ctx,o,X,padT,H){ if(!TG.dn3||!o||!o.hist) return; const Hh=o.hist;
  ctx.font="bold 11px system-ui"; ctx.textAlign="center";
  for(const [q,t] of [[0.25,"P25"],[0.75,"P75"]]){ const v=tgHq(Hh,q), x=X(v); ctx.strokeStyle="#ffffffaa"; ctx.lineWidth=1; ctx.setLineDash([]); ctx.beginPath(); ctx.moveTo(x,padT+H*0.25); ctx.lineTo(x,padT+H); ctx.stroke();
    ctx.fillStyle="#fff"; ctx.fillText(`${t} ${fmtN(v,0)}`,x,padT+H*0.25-3); } ctx.textAlign="left"; }
function tgOddsTxt(r,B){ if(!r||!r.odds||!r.odds.hist) return ""; const o=r.odds, Hh=o.hist, p=v=>v==null?"–":(v*100).toFixed(1)+"%", out=[];
  if(TG.dn3){ out.push(`<div><b>TEST DN3</b> P25 <b>${fmtN(tgHq(Hh,0.25),0)}</b> · P75 <b>${fmtN(tgHq(Hh,0.75),0)}</b></div>`);
    const bd=B&&B.breakdown, top=B&&B.pinHi, fl=B?(B.fullFlip!=null?B.fullFlip:B.frontFlip):null;
    if(bd!=null&&top!=null&&fl!=null){ const hi=Math.max(top,fl);
      out.push(`<div>Don's buckets: below breakdown <b>${p(tgHm(Hh,-1e9,bd))}</b> · in pin <b>${p(tgHm(Hh,bd,top))}</b> · pin top → flip <b>${p(fl>top?tgHm(Hh,top,fl):0)}</b> · above flip <b>${p(tgHm(Hh,hi,1e9))}</b> <span class="note">(from the close histogram, ±${Hh.w} pts)</span></div>`); } }
  if(TG.dn1){ const T=o.touch||[], TL=o.touchLv||[]; const nm=["round-number support","call wall","put wall"], s=[];
    for(let i=3;i<6;i++) if(TL[i]!=null) s.push(`${nm[i-3]} ${fmtN(TL[i],0)} <b>${p(T[i])}</b>`);
    if(s.length) out.push(`<div class="note"><b>TEST DN1</b> touch before the close: ${s.join(" · ")}</div>`); }
  return out.join(""); }
// DN5 summary line + DN6 key-reading extras (appended to Key readings)
function tgDenExtra(cur,B,S,d){ if(!TG.dn5&&!TG.dn6) return ""; const out=[], now=denNowMin();
  if(TG.dn5&&cur&&cur.odds&&cur.odds.hist){ const M=denPull()&&cur.pull?cur.pull:cur, Hh=cur.odds.hist, fl=B?(B.fullFlip!=null?B.fullFlip:B.frontFlip):null;
    const sn=denSnaps().filter(r=>r[1]>RTH_OPEN&&r[1]<=MAP_T1).map(r=>r[2].x).filter(x=>x>0);
    const O=sn.length?sn[0]:null, Hi=sn.length?Math.max(...sn):null, Lo=sn.length?Math.min(...sn):null, pin=denPinShare(B), W=tgDenWalls(B);
    const pp=v=>v==null?"–":Math.round(v*100)+"%";
    out.push(`<div class="k" style="border:1px solid #d9a400;border-radius:6px;padding:6px 8px"><span class="lab">TEST DN5 · to ${tlabel(Math.min(now,MAP_T1))}</span>spot <b>${fmtN(S,2)}</b> · final cone (from ${fmtN(cur.spot,0)} @ ${cur.t}): median <b>${fmtN(M.med,0)}</b> · P10 ${fmtN(M.p10,0)} · P90 ${fmtN(M.p90,0)}`+
      (B&&B.pinLo!=null?` · P(in pin) <b>${pp(tgHm(Hh,B.pinLo,B.pinHi))}</b>`:"")+(fl!=null?` · P(above flip) <b>${pp(tgHm(Hh,fl,1e9))}</b>`:"")+
      `<br>Session O ${fmtN(O,2)} · H ${fmtN(Hi,2)} · L ${fmtN(Lo,2)} · time in pin ${pin!=null?Math.round(pin*100)+"%":"–"}`+
      (W&&W.support!=null&&Lo!=null?` · round-number support ${fmtN(W.support,0)} <b class="${Lo>W.support?"up":"dn"}">${Lo>W.support?"held":"broken"}</b>`:"")+`</div>`); }
  if(TG.dn6){ const W=tgDenWalls(B), LE=denLetf(S), nb=st.ti&&st.ti.near_book&&st.ti.near_book[st.sym], day=liveClock().date;
    const ob=nb&&(nb.as_of_date||nb.as_of||null);
    out.push(`<div class="k"><span class="lab">TEST DN6</span>levered reset at the mark <b class="${LE&&LE.bn<0?"dn":"up"}">${LE?denBn(LE.bn):"–"}</b>${LE?` (builds 15:00–16:00, not charm)`:""} · 1–7 DTE OI book ${ob?esc(String(ob).slice(0,10))+(String(ob).slice(0,10)!==day?" <b class=\"dn\">not today's</b>":" (today)"):"<b class=\"dn\">missing</b>"}`+
      (W?` · OI within ±1.5%: ${fmtN(Math.round(W.nearOI/1000),0)}k${W.nearOI<150000?` <b class="dn">thin book — levels soft</b>`:""}`:"")+
      (st.sym==="QQQ"?` · width vol ${tgVxn()?`VXN <b>${tgVxn().toFixed(2)}</b>`:"VXN not loaded (VIX used)"}`:"")+`</div>`); }
  return out.join(""); }
// DN6 status-box line on the chart
function tgDenStatus(ctx,padL,W,padT,S){ if(!TG.dn6) return; const LE=denLetf(S); if(!LE) return;
  const s=`levered reset @ mark ${denBn(LE.bn)} (15:00–16:00)`; ctx.font="bold 13px system-ui"; const tw=ctx.measureText(s).width;
  ctx.textAlign="right"; ctx.fillStyle="#05080fd0"; ctx.fillRect(padL+W-tw-16,padT+86,tw+12,20); ctx.fillStyle=LE.bn<0?"#FF7A6B":"#3FE0A0"; ctx.fillText(s,padL+W-10,padT+101); ctx.textAlign="left"; }
