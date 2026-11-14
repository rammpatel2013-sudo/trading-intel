/* TEST PF PROFILE (2026-11-13, VS plan step 2) - display only, test cockpit only (MAP rule, 11/09). No map number changes.
   Map tab, Gamma pane, button "Profile" (OFF by default). Reads profile_SPX.json (scripts/vs_profile_feed.py, NAS 08:45 + 17:35):
   dealer gamma of the WHOLE book (every expiry, naive sign, re-priced at each SPX level) as two lines -
     Full (solid cyan) and Ex-0DTE (dashed orange) = the same book without today's expiring options -
   with the zero line, the flip of each line, the live price, and the percentile of gamma at price vs our past sessions
   (our $ size is ~29x VolSignals', so the percentile is the comparable number). Hover / tap = both values at that price.
   VS step 3 (same night): the ES HEDGE TABLE under the chart - ES contracts dealers would buy / sell to stay hedged if SPX
   moved -2 / -1 / -0.5 / +0.5 / +1 / +2 % from now, Full and Ex-0DTE (the profile's own gamma line, integrated; exact
   Black-Scholes delta change within 1%), with one plain line: do they chase the move or absorb it. */
st.mapProf=load("tgProf","0")==="1";
const TG_PF={d:null,at:0,err:null};
function tgPfFetch(force){ if(!force&&Date.now()-TG_PF.at<300000) return; TG_PF.at=Date.now();
  fetch("profile_SPX.json?"+Date.now(),{cache:"no-store"}).then(r=>r.ok?r.json():Promise.reject("HTTP "+r.status))
    .then(j=>{ TG_PF.d=j; TG_PF.err=j&&j.ok?null:(j&&j.reason||"not written yet"); if(st.mapProf) tgProfDraw(); })
    .catch(e=>{ TG_PF.err="profile_SPX.json not found ("+e+") - it is written by vs_profile_feed at 08:45 and 17:35"; if(st.mapProf) tgProfDraw(); }); }
function tgPfOrd(p){ if(p==null) return "–"; const s=p%100>=11&&p%100<=13?"th":({1:"st",2:"nd",3:"rd"})[p%10]||"th"; return p+s; }
function tgPfWord(v){ return v>0?"long":"short"; }
function tgProfInit(){ const b=document.getElementById("tgProfBtn"); if(!b||b._pf) return; b._pf=1;
  b.addEventListener("click",()=>{ st.mapProf=!st.mapProf; save("tgProf",st.mapProf?"1":"0"); if(st.mapProf) tgPfFetch(true); tgProfDraw(); });
  const c=document.getElementById("tgProfC"); if(c){ const mv=e=>{ const r=c.getBoundingClientRect(); const x=(e.touches?e.touches[0].clientX:e.clientX)-r.left; TG_PF.hx=x; tgProfDraw(); };
    c.addEventListener("mousemove",mv); c.addEventListener("touchstart",mv,{passive:true}); c.addEventListener("mouseleave",()=>{ TG_PF.hx=null; tgProfDraw(); }); }
  if(st.mapProf) tgPfFetch(true); }
function tgProfDraw(){ const b=document.getElementById("tgProfBtn"), box=document.getElementById("tgProf"); if(!b||!box) return;
  b.setAttribute("aria-pressed",!!st.mapProf); box.hidden=!st.mapProf; { const tm=document.getElementById("tabMap"); if(tm) tm.classList.toggle("pfon",!!st.mapProf); } if(!st.mapProf) return; tgPfFetch(false);
  const head=document.getElementById("tgProfHead"), c=document.getElementById("tgProfC"), P=TG_PF.d;
  if(st.sym!=="SPX"){ head.innerHTML="<b>Profile: SPX only</b> (the whole-book profile is built for SPX)."; c.hidden=true; return; }
  if(!P||!P.ok){ head.innerHTML="<b>Profile:</b> "+esc(TG_PF.err||"loading…"); c.hidden=true; return; }
  c.hidden=false; tgPfHedge(P); const d=st.live&&st.live.SPX, S=d&&d.spot>0?d.spot:P.spot, f=v=>fmtN(Math.round(v));
  const G=P.grid, px=i=>G.lo+i*G.step, n=G.n, F=P.full, E=P.ex0;
  const at=k=>{ const i=(S-G.lo)/G.step, i0=Math.max(0,Math.min(n-2,Math.floor(i))), w=Math.max(0,Math.min(1,i-i0)); return P[k][i0]*(1-w)+P[k][i0+1]*w; };
  const aF=at("full"), aE=at("ex0"), fl=P.flips;
  const flipTxt=k=>{ const x=fl[k]; return x.nearest==null?"no flip within ±4%":`flip <b>${f(x.nearest)}</b> (${x.nearest<S?f(S-x.nearest)+" below":f(x.nearest-S)+" above"})`; };
  head.innerHTML=`<div class="pf1"><b>Gamma profile, whole book</b> · session ${esc(P.session)} · ${esc(P.book.kind==="am"?"08:35 morning":"17:15 close")} book (${esc(P.book.oi_note)})</div>
    <div class="pf2"><span class="pfF">━ Full</span>: <b>${tgPfWord(aF)} gamma</b> at ${f(S)}, <b>${tgPfOrd(P.pct.full)}</b> percentile · ${flipTxt("full")}</div>
    <div class="pf2"><span class="pfE">╍ Ex-0DTE</span>: <b>${tgPfWord(aE)} gamma</b>, <b>${tgPfOrd(P.pct.ex0)}</b> percentile · ${flipTxt("ex0")}${P.book.expiring_today?"":" · (no options expire today: both lines are the same)"}</div>
    <div class="pf3">${esc(P.plain)} Percentile = today's gamma at price ranked against our last ${P.hist_n} sessions (our $ size is not comparable to VolSignals').</div>`;
  const W=c.clientWidth||900, H=300, dpr=window.devicePixelRatio||1; if(c.width!==Math.round(W*dpr)){ c.width=Math.round(W*dpr); c.height=Math.round(H*dpr); c.style.height=H+"px"; }
  const ctx=c.getContext("2d"); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,W,H);
  const lo=Math.max(G.lo,S*0.975), hi=Math.min(px(n-1),S*1.025), i0=Math.max(0,Math.floor((lo-G.lo)/G.step)), i1=Math.min(n-1,Math.ceil((hi-G.lo)/G.step));
  let ymin=0,ymax=0; for(let i=i0;i<=i1;i++){ ymin=Math.min(ymin,F[i],E[i]); ymax=Math.max(ymax,F[i],E[i]); } const pad=(ymax-ymin)*0.08||1; ymin-=pad; ymax+=pad;
  const L=70,R=16,T=12,B=34, X=v=>L+(v-lo)/(hi-lo)*(W-L-R), Y=v=>T+(ymax-v)/(ymax-ymin)*(H-T-B);
  ctx.font="600 15px system-ui,sans-serif"; ctx.fillStyle="#e8edf2"; ctx.strokeStyle="rgba(255,255,255,0.18)"; ctx.lineWidth=1;
  const step=[10,25,50,100,200].find(s=>(hi-lo)/s<=10)||200; ctx.textAlign="center";
  for(let v=Math.ceil(lo/step)*step; v<=hi; v+=step){ ctx.beginPath(); ctx.moveTo(X(v),T); ctx.lineTo(X(v),H-B); ctx.stroke(); ctx.fillText(fmtN(v),X(v),H-12); }
  ctx.setLineDash([4,4]); ctx.strokeStyle="rgba(255,255,255,0.75)"; ctx.lineWidth=1.5; ctx.beginPath(); ctx.moveTo(L,Y(0)); ctx.lineTo(W-R,Y(0)); ctx.stroke(); ctx.setLineDash([]);
  ctx.textAlign="right"; ctx.fillText("0",L-8,Y(0)+5); ctx.fillText("+"+ymax.toFixed(1),L-8,T+14); ctx.fillText(ymin.toFixed(1),L-8,H-B-2);
  const line=(A,col,dash)=>{ ctx.strokeStyle="rgba(0,0,0,0.7)"; ctx.lineWidth=6; ctx.setLineDash(dash); ctx.beginPath(); for(let i=i0;i<=i1;i++){ const x=X(px(i)),y=Y(A[i]); i===i0?ctx.moveTo(x,y):ctx.lineTo(x,y); } ctx.stroke();
    ctx.strokeStyle=col; ctx.lineWidth=3; ctx.stroke(); ctx.setLineDash([]); };
  line(F,"#3fd0ff",[]); line(E,"#ffa64d",[10,6]);
  const mark=(x,col,txt,row)=>{ if(x==null||x<lo||x>hi) return; ctx.fillStyle=col; ctx.beginPath(); ctx.arc(X(x),Y(0),6,0,7); ctx.fill(); ctx.strokeStyle="#000"; ctx.lineWidth=2; ctx.stroke();
    ctx.font="700 15px system-ui,sans-serif"; ctx.textAlign=X(x)>W-170?"right":"left"; const tx=X(x)+(ctx.textAlign==="left"?9:-9), ty=Y(0)+(row?22:-12);
    ctx.lineWidth=4; ctx.strokeStyle="rgba(0,0,0,0.85)"; ctx.strokeText(txt,tx,ty); ctx.fillStyle=col; ctx.fillText(txt,tx,ty); };
  mark(fl.full.nearest,"#3fd0ff","full flip "+(fl.full.nearest!=null?f(fl.full.nearest):""),0); mark(fl.ex0.nearest,"#ffa64d","ex-0DTE flip "+(fl.ex0.nearest!=null?f(fl.ex0.nearest):""),1);
  ctx.strokeStyle="#ffffff"; ctx.lineWidth=2; ctx.beginPath(); ctx.moveTo(X(S),T); ctx.lineTo(X(S),H-B); ctx.stroke();
  ctx.font="700 15px system-ui,sans-serif"; ctx.textAlign="left"; ctx.lineWidth=4; ctx.strokeStyle="rgba(0,0,0,0.85)"; const lab="SPX "+f(S); ctx.strokeText(lab,X(S)+6,T+16); ctx.fillStyle="#fff"; ctx.fillText(lab,X(S)+6,T+16);
  if(TG_PF.hx!=null&&TG_PF.hx>=L&&TG_PF.hx<=W-R){ const v=lo+(TG_PF.hx-L)/(W-L-R)*(hi-lo), i=Math.max(0,Math.min(n-1,Math.round((v-G.lo)/G.step)));
    ctx.strokeStyle="rgba(255,255,255,0.5)"; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(TG_PF.hx,T); ctx.lineTo(TG_PF.hx,H-B); ctx.stroke();
    const t1=`${f(px(i))}: full ${F[i]>=0?"+":""}${F[i].toFixed(2)} (${tgPfWord(F[i])}) · ex-0DTE ${E[i]>=0?"+":""}${E[i].toFixed(2)} (${tgPfWord(E[i])})`, t2="$bn per 1% move, our naive book";
    ctx.font="700 16px system-ui,sans-serif"; const w=Math.max(ctx.measureText(t1).width,ctx.measureText(t2).width)+20, bx=Math.min(Math.max(L,TG_PF.hx+12),W-R-w);
    ctx.fillStyle="rgba(10,14,20,0.94)"; ctx.fillRect(bx,T+24,w,52); ctx.strokeStyle="#fff"; ctx.strokeRect(bx,T+24,w,52); ctx.fillStyle="#fff"; ctx.textAlign="left"; ctx.fillText(t1,bx+10,T+46); ctx.font="500 14px system-ui,sans-serif"; ctx.fillText(t2,bx+10,T+68); } }
function tgPfK(v){ const a=Math.abs(v); return (v>=0?"+":"−")+(a>=1e4?(a/1e3).toFixed(0)+"k":a>=1e3?(a/1e3).toFixed(1)+"k":String(a)); }
function tgPfHedge(P){ const box=document.getElementById("tgProf"); let el=document.getElementById("tgProfHedge");
  if(!el){ el=document.createElement("div"); el.id="tgProfHedge"; box.appendChild(el); }
  const H=P.hedge||[]; if(!H.length){ el.innerHTML=""; return; }
  const cell=r=>{ const col=v=>v>=0?"#3ddc84":"#ff5c5c", w=v=>v>=0?"buy":"sell";
    return `<div class="pfh"><div class="pfhm">${r.move_pct>0?"+":""}${r.move_pct}% → ${fmtN(Math.round(r.spx))}</div>
      <div class="pfhv" style="color:${col(r.es)}">${w(r.es)} ${tgPfK(Math.abs(r.es)).slice(1)} ES</div>
      <div class="pfhx" style="color:${col(r.es_ex0)}">ex-0DTE: ${w(r.es_ex0)} ${tgPfK(Math.abs(r.es_ex0)).slice(1)}</div></div>`; };
  const dn=H.find(r=>r.move_pct===-1), up=H.find(r=>r.move_pct===1);
  const say=(r,dir)=>{ if(!r) return ""; const buy=r.es>=0, chase=dir<0?!buy:buy;
    return `If SPX ${dir<0?"falls":"rises"} 1% to ${fmtN(Math.round(r.spx))}, dealers <b>${buy?"buy":"sell"}</b> about ${tgPfK(Math.abs(r.es)).slice(1)} ES → they <b>${chase?"chase":"absorb"}</b> the move${chase?" (it can speed up)":" (it tends to slow)"}.`; };
  el.innerHTML=`<div class="pfht"><b>ES hedge table</b> (whole book; ${esc(P.hedge_note||"")})</div><div class="pfhrow">${H.map(cell).join("")}</div>
    <div class="pfhs">${say(dn,-1)} ${say(up,1)}</div>`; }
setTimeout(tgProfInit,0);
