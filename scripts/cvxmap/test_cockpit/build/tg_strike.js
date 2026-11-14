/* TEST SM (2026-11-13 night) - SHORT STRIKES marker. Test cockpit only (MAP rule 11/09), OFF by default, display only:
   no map number changes (buildMap, the read-out and every other number are untouched; off = nothing is drawn).
   Mithil 11/13 22:59: "use this range as an inner strike to sell credit spread and make money ... its about is this line act
   as a reaction zone or transition zone". Buttons "Short strikes" on the Flow tab (next to Strike lines) and the Map's gamma
   pane (next to Profile) - one switch.
   Rule sm-v0, identical to scripts/cvxmap/rail_score.py marks() (which freezes it at 09:45 and scores it every night):
     today's 0DTE board, start-of-day OI, naive sign (dealers long calls, short puts) - always, whatever the Map's book /
     basis / Smile buttons say. Each strike's dealer gamma if price goes there = (call OI - put OI) x phi(d1) /
     (k x sigma x sqrt(T)) x 2.5, d1 = sigma x sqrt(T) / 2, sigma = the strike's OTM IV, T = minutes to 16:00 / 525,600.
     Straddle = the live 0DTE ATM straddle (nearest strike with both mids). Significant = at least 20% of the biggest
     |gamma| within 3 straddles of price.
     Calls: from the 1.5x rail out to 3x, the first significant POSITIVE strike plus the positive significant strikes right
     after it = the reaction zone (green band: dealers absorb there); short = the next strike beyond it (amber line).
     Puts: the same from the 1.75x rail (puts run further). None found = "no absorbing strike - transition" (red rail).
   Live: re-computed each minute from the live chain. Labels sit on the LEFT (Strike lines keep the right edge). */
st.smOn=load("tgSmOn","0")==="1";
const TG_SM={key:null,val:null}, SM_SIG=0.20, SM_CALL=1.5, SM_PUT=1.75, SM_LIM=3.0;
function tgSmOtm(k,S,ci,pi){ const a=k>=S?ci:pi, b=k>=S?pi:ci; return a>0?a:(b>0?b:0); }
function tgSmCalc(){ // -> {S, em, t, call, put} for the current minute (null when there is no live chain)
  const d=st.live&&st.live[st.sym]; if(!d||!d.rows||!d.rows.length||!(d.spot>0)) return null;
  const c=liveClock(), t=Math.min(Math.max(c.h*60+c.m,570),959), key=[st.live.ts,st.sym,t].join("|");
  if(TG_SM.key===key) return TG_SM.val;
  const S=d.spot; let em=0, best=null;
  for(const r of d.rows){ if(r.cM>0&&r.pM>0&&(best==null||Math.abs(r.k-S)<best)){ best=Math.abs(r.k-S); em=r.cM+r.pM; } }
  let out=null;
  if(em>0){ const ty=Math.max(960-t,1)/525600, sq=Math.sqrt(ty);
    const R=d.rows.map(r=>{ const k=+r.k, sg=tgSmOtm(k,S,r.cIV||0,r.pIV||0); let g=0;
      if(sg>0){ const d1=0.5*sg*sq; g=((r.cOI||0)-(r.pOI||0))*Math.exp(-0.5*d1*d1)/Math.sqrt(2*Math.PI)/(k*sg*sq)*2.5; }
      return {k,g}; }).sort((a,b)=>a.k-b.k);
    let M=0; for(const r of R) if(Math.abs(r.k-S)<=SM_LIM*em) M=Math.max(M,Math.abs(r.g));
    out={S,em,t,call:null,put:null};
    if(M>0) for(const [side,mult,sgn] of [["call",SM_CALL,1],["put",SM_PUT,-1]]){
      const rail=S+sgn*mult*em, lim=S+sgn*SM_LIM*em, ord=sgn>0?R:R.slice().reverse();
      const seq=ord.filter(r=>(r.k-rail)*sgn>=0&&(r.k-lim)*sgn<=0), i0=seq.findIndex(r=>r.g>=SM_SIG*M);
      if(i0<0){ out[side]={rail,mult,short:null}; continue; }
      let j=i0; while(j+1<seq.length&&seq[j+1].g>=SM_SIG*M) j++;
      const beyond=ord.filter(r=>(r.k-seq[j].k)*sgn>0), short=beyond.length?beyond[0].k:seq[j].k+sgn*5;
      out[side]={rail,mult,zone:[Math.min(seq[i0].k,seq[j].k),Math.max(seq[i0].k,seq[j].k)],short,x:(short-S)*sgn/em}; } }
  TG_SM.key=key; TG_SM.val=out; return out; }
function tgSmDraw(ctx,g,part){ // g = {Y, x0, x1, yMin, yMax}; part "lines" / "labels" / "all"
  if(!st.smOn) return; const o=tgSmCalc(); if(!o) return;
  const inV=y=>y>=g.yMin&&y<=g.yMax; ctx.save();
  for(const side of ["call","put"]){ const v=o[side]; if(!v) continue; const yr=g.Y(v.rail);
    if(part!=="labels"){
      if(v.short!=null){ const ya=g.Y(v.zone[0]-2.5), yb=g.Y(v.zone[1]+2.5), ys=g.Y(v.short);
        ctx.fillStyle="rgba(46,204,113,0.28)"; ctx.fillRect(g.x0,Math.min(ya,yb),g.x1-g.x0,Math.abs(yb-ya));
        if(inV(ys)){ ctx.strokeStyle="#FFB000"; ctx.lineWidth=3; ctx.setLineDash([10,6]); ctx.beginPath(); ctx.moveTo(g.x0,ys); ctx.lineTo(g.x1,ys); ctx.stroke(); } }
      if(inV(yr)){ ctx.strokeStyle=v.short!=null?"#ffffffb0":"#FF5A5A"; ctx.lineWidth=v.short!=null?1.5:2.5; ctx.setLineDash([2,4]);
        ctx.beginPath(); ctx.moveTo(g.x0,yr); ctx.lineTo(g.x1,yr); ctx.stroke(); }
      ctx.setLineDash([]); }
    if(part!=="lines"){ ctx.font="bold 14px system-ui"; ctx.textAlign="left";
      const y=v.short!=null?g.Y(v.short):yr, off=!inV(y)?(y<g.yMin?"↑ ":"↓ "):"";
      const t=off+(v.short!=null?`SELL ${side==="call"?"CALLS ≥":"PUTS ≤"} ${fmtN(v.short)} · behind +γ ${fmtN(v.zone[0])}–${fmtN(v.zone[1])} · ${v.x.toFixed(1)}× straddle`
                              :`${side==="call"?"CALLS":"PUTS"}: no absorbing strike ${v.mult}×–3× → transition, stand aside`);
      const ty=Math.min(Math.max(y+(side==="call"?-8:20),g.yMin+16),g.yMax-6), tw=ctx.measureText(t).width;
      ctx.fillStyle="#05080fe6"; ctx.fillRect(g.x0+4,ty-15,tw+12,21); ctx.strokeStyle=v.short!=null?"#FFB000":"#FF5A5A"; ctx.lineWidth=1.2;
      ctx.strokeRect(g.x0+4,ty-15,tw+12,21); ctx.fillStyle=v.short!=null?"#FFB000":"#FF7A7A"; ctx.fillText(t,g.x0+10,ty+1); } }
  ctx.restore(); }
function tgSmLegend(){ if(!st.smOn) return ""; const o=tgSmCalc();
  if(!o) return `<span style="color:#FFD84D;font-weight:700">TEST Short strikes</span><span>no live 0DTE chain with both mids yet</span>`;
  const f=v=>!v?"–":v.short!=null?`<b style="color:#FFB000">${fmtN(v.short)}</b> (behind +γ ${fmtN(v.zone[0])}–${fmtN(v.zone[1])})`:`<b style="color:#FF7A7A">none - transition</b>`;
  return `<span style="color:#FFD84D;font-weight:700">TEST Short strikes sm-v0</span><span>calls ${f(o.call)} · puts ${f(o.put)} · straddle ${fmtN(o.em,1)} · 0DTE OI board, live; scored nightly at 09:45</span>`; }
function tgSmSync(){ for(const id of ["tgSmBtn","tgSmBtn2"]){ const b=document.getElementById(id); if(b) b.setAttribute("aria-pressed",st.smOn); } }
function tgSmInit(){ for(const id of ["tgSmBtn","tgSmBtn2"]){ const b=document.getElementById(id); if(!b||b._sm) continue; b._sm=1;
    b.addEventListener("click",()=>{ st.smOn=!st.smOn; save("tgSmOn",st.smOn?"1":"0"); tgSmSync(); render(); }); }
  tgSmSync(); }
setTimeout(tgSmInit,0);
