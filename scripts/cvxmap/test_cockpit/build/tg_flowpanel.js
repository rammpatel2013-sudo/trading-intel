/* TEST FP (2026-11-19 night) - FLOW PANEL F1-F3 on the Flow tab (four-thread plan section F). Test cockpit only, OFF by
   default, display only: no map / flow number changes; off = nothing drawn, nothing fetched. Button "Flow panel" in the
   Flow by strike header. Follows the SPX / SPY / QQQ buttons. Data: the minute log already on the page (st.flow) + the
   start-of-day OI in live.json rows + data/terrain.json (terrain.py, written by cvxmap-wide at 09:26).
   F1 LIVE DEALER GAMMA at spot, every minute of today's 0DTE book, $ per 1% move (Black-Scholes, the strike's OTM IV
      from the minute log, clock to 16:00):
        OI part        = sum (call OI - put OI) x gamma$      (the Map's naive sign: dealers long calls, short puts)
        measured part  = sum dealer contracts from today's tagged trades x gamma$, dealer contracts =
                         -(calls bought - calls sold) - (puts bought - puts sold)   (customer buy -> dealer short)
        line = OI part + measured part (= the Map's "OI + signed" basis at spot); dashed = OI part only.
        gamma$ = n x 100 x gamma x S^2 x 0.01.
   F2 OVI (option volume imbalance) by expiry, contracts, cumulative from the open: (calls bought - calls sold) -
      (puts bought - puts sold), customer side. 0DTE (rows) and the next expiry (minute log "n1"). Longer expiries are not
      in the minute log (needs the tape writer - later). Read-out gives the last 15 minutes' change.
   F3 PRICE TERRAIN badge at spot (rule tr-v0, terrain.py): minutes price spent in spot's bin over the last 3 sessions:
      HVN (>= 70th pct of visited bins, accepted ground) / LVN (<= 30th pct, passed through fast) / MID / NEW (never there). */
st.tgFp=load("tgFp","0")==="1";
const TG_FP={key:null,ser:null,ter:null,terAt:0,terBusy:false};
document.addEventListener("click",e=>{ const b=e.target&&e.target.closest&&e.target.closest("#tgFpBtn"); if(!b) return;
  st.tgFp=!st.tgFp; save("tgFp",st.tgFp?"1":"0"); TG_FP.key=null; try{ tgFpDraw(); }catch(err){ console.error(err); } });
function tgFpG$(S,k,sg,ty){ if(!(S>0&&k>0&&sg>0&&ty>0)) return 0; const sq=sg*Math.sqrt(ty), d1=(Math.log(S/k)+0.5*sg*sg*ty)/sq;
  return 100*Math.exp(-0.5*d1*d1)/Math.sqrt(2*Math.PI)/(S*sq)*S*S*0.01; }
/* one minute of the log -> {t, x, oi, tr, o0, o1} (gamma in $, OVI in contracts) */
function tgFpRow(s,oiMap){ const S=+s.x, t=tmin(s.t), ty=Math.max(960-t,1)/525600; let oi=0,tr=0,o0=0,o1=null;
  const ks=s.k||[]; for(let i=0;i<ks.length;i++){ const k=+ks[i], g=tgFpG$(S,k,otmIV(k,S,+(s.ci&&s.ci[i])||0,+(s.pi&&s.pi[i])||0),ty);
    const cb=+(s.cvb&&s.cvb[i])||0, cs=+(s.cvs&&s.cvs[i])||0, pb=+(s.pvb&&s.pvb[i])||0, ps=+(s.pvs&&s.pvs[i])||0, o=oiMap.get(k);
    if(o) oi+=((o[0]||0)-(o[1]||0))*g; tr+=(-(cb-cs)-(pb-ps))*g; o0+=(cb-cs)-(pb-ps); }
  const n=s.n1; if(n&&n.k){ o1=0; for(let i=0;i<n.k.length;i++) o1+=((+(n.cvb&&n.cvb[i])||0)-(+(n.cvs&&n.cvs[i])||0))-((+(n.pvb&&n.pvb[i])||0)-(+(n.pvs&&n.pvs[i])||0)); }
  return {t,hm:s.t,x:S,oi,tr,o0,o1}; }
function tgFpSeries(){ const snaps=(st.flow&&st.flow[st.sym])||[], d=st.live&&st.live[st.sym]; if(!snaps.length||!d||!d.rows) return null;
  const key=[st.sym,snaps.length,snaps[snaps.length-1].t,st.live.ts].join("|"); if(TG_FP.key===key) return TG_FP.ser;
  const oiMap=new Map(d.rows.map(r=>[+r.k,[+r.cOI||0,+r.pOI||0]])), out=[];
  for(const s of snaps){ const t=tmin(s.t); if(t<570||t>960||!(s.x>0)) continue; out.push(tgFpRow(s,oiMap)); }
  TG_FP.key=key; TG_FP.ser=out; return out; }
/* F3: terrain */
async function tgFpTerrainFetch(){ if(TG_FP.terBusy||Date.now()-TG_FP.terAt<300000) return; TG_FP.terBusy=true; TG_FP.terAt=Date.now();
  try{ const r=await fetch("terrain.json",{cache:"no-cache"}); TG_FP.ter=r.ok?await r.json():{_err:"HTTP "+r.status}; }catch(e){ TG_FP.ter={_err:String(e&&e.message||e)}; }
  TG_FP.terBusy=false; try{ tgFpDraw(); }catch(e){ console.error(e); } }
function tgFpTerrain(sym,x){ const T=TG_FP.ter; if(!T||T._err||!T.syms||!T.syms[sym]||!(x>0)) return null; const o=T.syms[sym], b=o.bin, lo=Math.floor(x/b)*b;
  const n=+(o.m[String(+lo.toFixed(2))]||o.m[lo.toFixed(1)]||o.m[lo.toFixed(2)]||0);
  return {lab:n<=0?"NEW":n>=o.hvn?"HVN":n<=o.lvn?"LVN":"MID",n,lo,hi:lo+b,from:T.from||[]}; }
function tgFpFmt$(v){ const a=Math.abs(v), s=v<0?"−":"+"; return a>=1e9?s+"$"+(a/1e9).toFixed(2)+"B":a>=1e6?s+"$"+(a/1e6).toFixed(a>=1e8?0:1)+"M":s+"$"+(a/1e3).toFixed(0)+"k"; }
function tgFpFmtN(v){ const a=Math.abs(v), s=v<0?"−":"+"; return s+(a>=1e4?(a/1e3).toFixed(1)+"K":Math.round(a).toLocaleString()); }
function tgFpDraw(){ const el=document.getElementById("tgFp"); if(!el) return; const btn=document.getElementById("tgFpBtn");
  if(btn) btn.setAttribute("aria-pressed",st.tgFp?"true":"false");
  el.hidden=!st.tgFp; if(!st.tgFp) return; tgFpTerrainFetch();
  const ser=tgFpSeries(), head=document.getElementById("tgFpHead");
  if(!ser||ser.length<2){ head.innerHTML=`<b>TEST Flow panel</b> — waiting for the minute log and live.json (${esc(st.sym)}).`; const c=document.getElementById("tgFpC"); if(c) c.getContext("2d").clearRect(0,0,c.width,c.height); return; }
  const L=ser[ser.length-1], P=ser.slice().reverse().find(r=>r.t<=L.t-15)||ser[0], tot=r=>r.oi+r.tr, ter=tgFpTerrain(st.sym,L.x);
  const tc=ter?{HVN:"#3DDC84",LVN:"#FFB347",MID:"#C9D4E3",NEW:"#FF6B6B"}[ter.lab]:"#C9D4E3";
  const terTxt=ter?`<b style="color:#000;background:${tc};padding:0 6px;border-radius:4px">${ter.lab}</b> ${fmtN(ter.lo)}–${fmtN(ter.hi)}: ${ter.n} min in the last ${ter.from.length} sessions (${ter.lab==="HVN"?"accepted ground, moves tend to slow":ter.lab==="LVN"?"thin ground, price passed through fast":ter.lab==="NEW"?"fresh ground, no recent trade here":"middling"})`:
    `<i>${TG_FP.ter&&TG_FP.ter._err?"terrain.json not found ("+esc(TG_FP.ter._err)+") - written by cvxmap-wide at 09:26 (terrain.py)":TG_FP.ter?"no terrain for "+esc(st.sym):"loading terrain…"}</i>`;
  head.innerHTML=`<div class="pf1"><b>TEST Flow panel</b> · ${esc(st.sym)} 0DTE · ${esc(L.hm)}</div>`+
    `<div class="pf2">F1 dealer gamma at spot <b class="${tot(L)>=0?"pfS":"pfE"}">${tgFpFmt$(tot(L))}</b> per 1% = OI ${tgFpFmt$(L.oi)} + today's trades ${tgFpFmt$(L.tr)} · 15 min ${tgFpFmt$(tot(L)-tot(P))} · ${tot(L)>=0?"dealers long gamma: they lean against moves":"dealers short gamma: they chase moves"}</div>`+
    `<div class="pf2">F2 OVI (customers, contracts, from the open): 0DTE <b class="${L.o0>=0?"pfS":"pfE"}">${tgFpFmtN(L.o0)}</b> (15 min ${tgFpFmtN(L.o0-P.o0)})${L.o1!=null?` · next expiry <b class="${L.o1>=0?"pfS":"pfE"}">${tgFpFmtN(L.o1)}</b> (15 min ${P.o1!=null?tgFpFmtN(L.o1-P.o1):"–"})`:" · next expiry not in the log"} · + = calls bought / puts sold</div>`+
    `<div class="pf2">F3 terrain at ${fmtN(L.x)}: ${terTxt}</div>`+
    `<div class="pf3">Gamma: naive OI sign (dealers long calls, short puts) + measured trade tags, Black-Scholes at each minute's IV, clock to 16:00. OVI split 1–5D / 30D+ needs the tape writer (later). Display only.</div>`;
  const cv=document.getElementById("tgFpC"); const {ctx,w,h}=fitCanvas(cv); ctx.clearRect(0,0,w,h);
  const padL=70,padR=14,padT=8,gap=26,H1=Math.round((h-padT-gap-24)*0.55),H2=h-padT-gap-24-H1, W=w-padL-padR, t0=570,t1=960, X=t=>padL+W*(t-t0)/(t1-t0);
  const panel=(y0,H,lines,fmt,title)=>{ let lo=0,hi=0; for(const [f] of lines) for(const r of ser){ const v=f(r); if(v==null) continue; lo=Math.min(lo,v); hi=Math.max(hi,v); } if(hi-lo<1e-9){ hi+=1; lo-=1; }
    const pad=(hi-lo)*0.08; lo-=pad; hi+=pad; const Y=v=>y0+H*(1-(v-lo)/(hi-lo));
    ctx.fillStyle="#0E1B33"; ctx.fillRect(padL,y0,W,H); ctx.strokeStyle="#ffffff40"; ctx.setLineDash([3,3]); ctx.beginPath(); ctx.moveTo(padL,Y(0)); ctx.lineTo(padL+W,Y(0)); ctx.stroke(); ctx.setLineDash([]);
    ctx.font="13px "+C("--font"); ctx.fillStyle="#DCE6F2"; ctx.textAlign="right"; ctx.fillText(fmt(hi),padL-4,y0+12); ctx.fillText(fmt(lo),padL-4,y0+H-2); ctx.textAlign="left"; ctx.fillText(title,padL+6,y0+14);
    for(const [f,colr,dash,lw] of lines){ ctx.strokeStyle=colr; ctx.lineWidth=lw; ctx.setLineDash(dash); ctx.beginPath(); let on=false; for(const r of ser){ const v=f(r); if(v==null){ on=false; continue; } const x=X(r.t), y=Y(v); on?ctx.lineTo(x,y):ctx.moveTo(x,y); on=true; } ctx.stroke(); } ctx.setLineDash([]); };
  panel(padT,H1,[[r=>r.oi,"#9AA7B8",[5,4],1.5],[tot,"#3FD0FF",[],2.4]],tgFpFmt$,"F1 dealer gamma at spot, $ per 1% — solid = OI + trades, dashed = OI only");
  panel(padT+H1+gap,H2,[[r=>r.o0,"#FFA64D",[],2.2],[r=>r.o1,"#7BE3A6",[],1.8]],tgFpFmtN,"F2 OVI, contracts — orange 0DTE, green next expiry");
  ctx.fillStyle="#DCE6F2"; ctx.font="13px "+C("--font"); ctx.textAlign="center"; for(let hh=10;hh<=16;hh++) ctx.fillText(String(hh>12?hh-12:hh)+(hh>=12?" pm":" am"),X(hh*60),h-6); ctx.textAlign="left"; }
