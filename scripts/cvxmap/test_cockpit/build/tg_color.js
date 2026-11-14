/* ============================================================ TEST: COLOR strip (2026-11-13 overnight, backlog #87)
   Mithil 11/13 20:18 "build" (reversing the drop): live Color on the Map = how fast dealer gamma changes with TIME alone.
   Button "Color" (TEST) in the Map > Gamma header, OFF by default; a strip under the gamma map.
   Maths: the gamma map's own projection. Right of "now" buildMap carries today's book to the close as time passes
   (sticky-strike IV, no new trades), so at each price row
       color(30 min) = gamma at (now + 30 min) - gamma now        (same book, basis, clock and toggles as the map)
   = the gamma that time alone adds (+, the pin strengthens: dealers lean harder against moves there) or takes away
   (-, the level lets go). Bars by price over the map's price range; spot marked; the into-the-close change (now -> 15:45)
   as a dotted line. Units = the map's (dealer delta per 2.5 pts for SPX). Display only: no map number changes. EOD
   get_color_at_spot already gives the end-of-day number; this is the live version. */
st.tgColor=load("tgColor","0")==="1";
const TG_CO={geo:null,hx:null,last:null};
function tgColorCalc(m){ if(!m||!m.grid||!m.times||m.kind!=="gamma") return null;
  const now=Math.max(MAP_OPEN,m.now||MAP_OPEN), jN=colAt(m,now), last=m.times.length-1;
  const j30=colAt(m,Math.min(now+30,m.times[last])), jC=colAt(m,Math.min(15*60+45,m.times[last]));
  if(j30<=jN) return {late:true};
  const g0=m.grid[jN], g3=m.grid[j30], gC=m.grid[jC], n=m.prices.length, col=[], cls=[];
  for(let i=0;i<n;i++){ const a=g0[i], b=g3[i], c=gC[i]; col.push(a!=null&&b!=null?b-a:null); cls.push(a!=null&&c!=null&&jC>jN?c-a:null); }
  const S=m.S, iS=Math.max(0,Math.min(n-1,Math.round((S-m.prices[0])/m.dp)));
  let up=null,dn=null; for(let i=0;i<n;i++){ const v=col[i]; if(v==null) continue; if(up==null||v>col[up]) up=i; if(dn==null||v<col[dn]) dn=i; }
  return {col,cls,g0,S,iS,up,dn,t0:m.times[jN],t30:m.times[j30],tC:m.times[jC],prices:m.prices}; }
function tgColorInit(){ const b=document.getElementById("tgColorBtn"); if(!b||b._co) return; b._co=1;
  b.addEventListener("click",()=>{ st.tgColor=!st.tgColor; save("tgColor",st.tgColor?"1":"0"); render(); window.dispatchEvent(new Event("resize")); });
  const c=document.getElementById("tgColorC"); if(c){ const mv=e=>{ const r=c.getBoundingClientRect(); TG_CO.hx=(e.touches?e.touches[0].clientX:e.clientX)-r.left; if(TG_CO.last) tgColorDraw(TG_CO.last); };
    c.addEventListener("mousemove",mv); c.addEventListener("touchstart",mv,{passive:true}); c.addEventListener("mouseleave",()=>{ TG_CO.hx=null; if(TG_CO.last) tgColorDraw(TG_CO.last); }); } }
function tgColorDraw(m){ const b=document.getElementById("tgColorBtn"), box=document.getElementById("tgColor"); if(!b||!box) return;
  b.setAttribute("aria-pressed",!!st.tgColor); box.hidden=!st.tgColor; if(!st.tgColor) return; TG_CO.last=m;
  const head=document.getElementById("tgColorHead"), c=document.getElementById("tgColorC"), R=tgColorCalc(m), f=v=>fmtN(Math.round(v));
  if(!R){ head.innerHTML="<b>Color (TEST):</b> needs the gamma map."; c.hidden=true; return; }
  if(R.late){ head.innerHTML="<b>Color (TEST):</b> less than 30 minutes of projection left - nothing to show."; c.hidden=true; return; }
  c.hidden=false; const vS=R.col[R.iS], gS=R.g0[R.iS], pct=gS&&Math.abs(gS)>1e-9?100*vS/Math.abs(gS):null;
  const word=v=>v>0?"adds":"takes away", pinW=(v,g)=>(v>0)===(g>0)?(g>0?"the calm (long-gamma) zone gets stronger":"the fast (short-gamma) zone gets faster"):(g>0?"the calm zone weakens":"the fast zone calms");
  head.innerHTML=`<b>Color (TEST)</b> — gamma that <b>time alone</b> ${word(vS)} at spot ${f(R.S)} by ${tlabel(R.t30)}: <b>${vS>=0?"+":"−"}${f(Math.abs(vS))}</b>${pct!=null?(Math.abs(pct)<1?" (under 1% of now)":` (${pct>=0?"+":"−"}${Math.abs(pct).toFixed(0)}% of now)`):""} → ${pinW(vS,gS)}.`+
    (R.up!=null&&R.col[R.up]>0?` Grows most at <b style="color:#3ddc84">${f(R.prices[R.up])}</b>.`:"")+(R.dn!=null&&R.col[R.dn]<0?` Fades most at <b style="color:#ff6bd6">${f(R.prices[R.dn])}</b>.`:"")+
    ` <i>Bars: next 30 min · dotted: now → ${tlabel(R.tC)}. Same book and buttons as the map; no new trades assumed.</i>`;
  const {ctx,w,h}=fitCanvas(c); ctx.clearRect(0,0,w,h); const padL=46,padR=12,padT=8,padB=20, W=w-padL-padR, H=h-padT-padB, P=R.prices, n=P.length;
  let mx=0; for(const v of R.col.concat(R.cls)) if(v!=null) mx=Math.max(mx,Math.abs(v)); if(!(mx>0)) mx=1;
  const X=i=>padL+W*i/(n-1), Y=v=>padT+H*(0.5-0.5*v/mx), bw=Math.max(1,W/(n-1)*0.85);
  ctx.strokeStyle="#ffffff40"; ctx.beginPath(); ctx.moveTo(padL,Y(0)); ctx.lineTo(padL+W,Y(0)); ctx.stroke();
  for(let i=0;i<n;i++){ const v=R.col[i]; if(v==null) continue; ctx.fillStyle=v>=0?"#3ddc84":"#ff6bd6"; const y0=Y(0), y1=Y(v); ctx.fillRect(X(i)-bw/2,Math.min(y0,y1),bw,Math.max(1,Math.abs(y1-y0))); }
  ctx.strokeStyle="#ffcc00"; ctx.setLineDash([2,3]); ctx.lineWidth=1.5; ctx.beginPath(); let on=false; for(let i=0;i<n;i++){ const v=R.cls[i]; if(v==null){ on=false; continue; } on?ctx.lineTo(X(i),Y(v)):ctx.moveTo(X(i),Y(v)); on=true; } ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth=1;
  ctx.strokeStyle="#ff4d4d"; ctx.beginPath(); ctx.moveTo(X(R.iS),padT); ctx.lineTo(X(R.iS),padT+H); ctx.stroke();
  ctx.fillStyle="#AFC0D8"; ctx.font="12px system-ui"; const step=Math.max(1,Math.round((n-1)/8));
  for(let i=0;i<n;i+=step) ctx.fillText(f(P[i]),X(i)-14,h-4);
  ctx.fillText("+"+fmtN(Math.round(mx)),2,padT+10); ctx.fillText("−"+fmtN(Math.round(mx)),2,padT+H);
  if(TG_CO.hx!=null&&TG_CO.hx>=padL&&TG_CO.hx<=padL+W){ const i=Math.round((TG_CO.hx-padL)/W*(n-1)), v=R.col[i], v2=R.cls[i];
    const t=`${f(P[i])}: next 30 min ${v==null?"–":(v>=0?"+":"−")+f(Math.abs(v))} · to ${tlabel(R.tC)} ${v2==null?"–":(v2>=0?"+":"−")+f(Math.abs(v2))} · now ${R.g0[i]==null?"–":f(R.g0[i])}`;
    ctx.font="bold 13px system-ui"; const tw=ctx.measureText(t).width, x=Math.min(Math.max(padL,TG_CO.hx-tw/2),w-tw-8); ctx.fillStyle="#000000d8"; ctx.fillRect(x-4,padT,tw+8,18); ctx.fillStyle="#fff"; ctx.fillText(t,x,padT+13);
    ctx.strokeStyle="#ffffff80"; ctx.beginPath(); ctx.moveTo(X(i),padT); ctx.lineTo(X(i),padT+H); ctx.stroke(); } }
setTimeout(tgColorInit,0);
