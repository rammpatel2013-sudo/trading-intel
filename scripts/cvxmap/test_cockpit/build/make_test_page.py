"""Build scripts/cvxmap/test_cockpit/cvx_cockpit_test.html from the production cockpit (2026-11-08).
Every edit is an exact, single-occurrence replacement; the script stops if production text is not as expected.
2026-11-09: MAP toggles only. The Density (DN1-DN6) and VIX (VX1/VX2) toggles moved to the production page as options
(OFF by default), so the test page simply inherits them from production; tg_den.js / tg_vix.js are gone.
Usage: python make_test_page.py <production cvx_cockpit.html> <out.html>"""
import sys, hashlib, os
src, out = sys.argv[1], sys.argv[2]
P = open(src, encoding="utf-8").read()
PROD_SHA = hashlib.sha256(P.encode("utf-8")).hexdigest()
assert PROD_SHA.startswith("d6c72f15dc2d542f"), "production page is not the locked version: " + PROD_SHA[:16]
HERE = os.path.dirname(os.path.abspath(__file__))
MOD = open(os.path.join(HERE, "tg_module.js"), encoding="utf-8").read()
UI = open(os.path.join(HERE, "tg_ui.js"), encoding="utf-8").read()
MOD = MOD.replace("__TG_PAGE_V__", "test-" + hashlib.sha256((MOD + UI).encode("utf-8")).hexdigest()[:12])
H = P
def rep(old, new, count=1):
    global H
    n = H.count(old)
    assert n == count, "expected %d of %r, found %d" % (count, old[:90], n)
    H = H.replace(old, new)

CSS = """<style>
.app{grid-template-rows:auto auto auto 1fr}   /* TEST: the bar is the first row */
.tgbar{background:#ffcc00;color:#000;padding:6px 10px;font:600 14px system-ui,sans-serif;border-bottom:3px solid #000;display:grid;gap:4px}
.tgbar .tg1{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px}
.tgbar .tg2{display:flex;flex-wrap:wrap;gap:4px}
.tgbar .tg3{font-weight:700;min-height:1em}
.tgb{font:600 13px system-ui,sans-serif;background:#fff;color:#000;border:2px solid #000;border-radius:4px;padding:3px 7px;cursor:pointer}
.tgb.on{background:#000;color:#ffcc00}
.tgb .tid{font-family:ui-monospace,Consolas,monospace;font-weight:800}
.tgb .v0{background:#c00;color:#fff;border-radius:3px;padding:0 3px;font-size:11px}
.tgall{background:#c00;color:#fff;border:2px solid #000;font:800 14px system-ui,sans-serif;padding:4px 10px;border-radius:4px;cursor:pointer}
.tgprod{color:#000;font-weight:800;text-decoration:underline}
.tgt{font-size:17px;letter-spacing:.05em}
.tgnote{display:inline-flex;gap:4px;align-items:center}
#tgRead{width:min(340px,60vw);font:14px system-ui,sans-serif;padding:3px 6px;border:2px solid #000;background:#fff;color:#000}
#tgReadBtn{font:700 13px system-ui,sans-serif;border:2px solid #000;background:#fff;color:#000;border-radius:4px;padding:3px 8px;cursor:pointer}
.tgsec{border:2px dashed #ffcc00 !important}
</style>"""

# 1. title + bar
rep("<title>SPX Cockpit</title>", "<title>TEST · SPX Cockpit</title>")
rep('<body>\n<div class="app">', '<body>\n' + CSS + '\n<div class="app">\n<div id="tgBar" class="tgbar">TEST COCKPIT loading…</div>')
# 2. map clock: MAP_T1 becomes the axis end (A5 may move it); TG_GEND = greek clock end
rep("const MAP_T0 = 8*60, MAP_T1 = 16*60, MAP_STEP = 2, MAP_SUB = 2, MAP_OPEN = 9*60+30;",
    "let MAP_T1 = 16*60; const MAP_T0 = 8*60, MAP_STEP = 2, MAP_SUB = 2, MAP_OPEN = 9*60+30;   // TEST: MAP_T1 is let (A5)")
# 3. module after PHI
anchor = "const PHI = x => Math.exp(-x*x/2)*0.398942280401433;\n"
rep(anchor, anchor + MOD + "\n")
# 4. E2 inside smileBeta
rep("function smileBeta(k,S,dte){ if(!SB) return null; ",
    "function smileBeta(k,S,dte){ if(!SB) return null; if(TG.e2){ const bs=tgBetaSmooth(k,S,dte); if(bs!=null) return bs; } ")
# 5. greekCol dispatch
rep("function greekCol(kind, rows, S, ty, cm){ // one price point: sum over strikes\n",
    "function greekCol(kind, rows, S, ty, cm){ // one price point: sum over strikes\n  if(TG_ANYG) return greekColTG(kind,rows,S,ty,cm);   // TEST toggles C2 / D1 / D4\n")
# 6. mapRowsAt: tobs, B3/B2/A5 IV, s0, B6
rep("  const ks=snap?snap.k:[...live.keys()]; const out=[];\n",
    "  const ks=snap?snap.k:[...live.keys()]; const out=[]; const TGR=tgRowsOn(), isLive=snapIdx==null, tobs=TGR?(snap?tmin(snap.t):tgLiveTobs(d)):0; if(isLive) TG_STATS._rej=0;\n")
rep("    const sg=otmIV(k,S,civ||0,piv||0);\n    const [cp,pp]=mapCP(",
    "    const sg=TGR?tgRowSg(k,S,civ,piv,snap,q,lr,tobs,isLive):otmIV(k,S,civ||0,piv||0);\n    const [cp,pp]=mapCP(")
rep("    out.push({k,sg,cp,pp}); });\n  return out;\n}",
    "    out.push({k,sg,cp,pp,s0:S}); });\n  if(isLive) TG_STATS.rej=TG_STATS._rej||0; if(TG.b6) tgGapFill(out,isLive);\n  return out;\n}")
# 7. Week book: B5 live IV, s0
rep("  for(const r of nb.rows){ const sg0=otmIV(r.k,S,r.cIV||0,r.pIV||0); if(!(sg0>0)) continue; const sg=Math.max(0.01,sg0+dv);\n",
    "  let nL=0,nF=0; for(const r of nb.rows){ const sg0=otmIV(r.k,S,r.cIV||0,r.pIV||0); if(!(sg0>0)) continue; let sg=Math.max(0.01,sg0+dv);\n"
    "    if(TG.b5){ const lv=tgWeekLiveSg(r,S); if(lv>0){ sg=lv; nL++; } else nF++; }\n")
rep("out.push({k:r.k,sg,cp,pp,dte:r.dte,e:r.e,set:r.set||\"PM\",exp_t:r.exp_t||(r.e+\"T16:00\"),cal_ok:r.cal_ok!==false}); }\n  return out.length?out:null; }",
    "out.push({k:r.k,sg,cp,pp,dte:r.dte,e:r.e,set:r.set||\"PM\",exp_t:r.exp_t||(r.e+\"T16:00\"),cal_ok:r.cal_ok!==false,s0:S}); }\n  if(TG.b5){ TG_STATS.wkLive=nL; TG_STATS.wkFb=nF; }\n  return out.length?out:null; }")
# 8. Week fast path off when a greek toggle is on
rep("function greekColWeek(kind,rows,S,t,day){ let tot=0;\n  if(st.smileOn){",
    "function greekColWeek(kind,rows,S,t,day){ let tot=0;\n  if(st.smileOn||TG_ANYG){")
# 9. Week field cache key
rep("wkHash(rows),prices.length,prices[0],prices[prices.length-1]].join(\"|\");",
    "wkHash(rows),tgKey(),prices.length,prices[0],prices[prices.length-1]].join(\"|\");")
# 10. buildMap: clock sync, greek clock end, cache key, level threshold
rep("function buildMap(kind,se,d,bookArg,view){\n", "function buildMap(kind,se,d,bookArg,view){\n  tgClockSync();   // TEST: A5 / B2 clock\n")
rep("  const _mapCache=mapCacheFor(key);", "  const _mapCache=mapCacheFor(key+\"|TG\"+tgKey());")
rep("const VC=!!st.clockVar, tyNow=Math.max(MAP_T1-Math.max(now,MAP_OPEN),1)/525600,",
    "const VC=!!st.clockVar, tyNow=Math.max(TG_GEND-Math.max(now,MAP_OPEN),1)/525600,")
rep("  for(const t of times){ const ty=Math.max(MAP_T1-t,1)/525600; let col, jj=null;",
    "  for(const t of times){ const ty=Math.max(TG_GEND-t,1)/525600; let col, jj=null;")
rep("col=prices.map(p=>greekCol(kind,liveRows,p,Math.max(MAP_T1-MAP_OPEN,1)/525600));",
    "col=prices.map(p=>greekCol(kind,liveRows,p,Math.max(TG_GEND-MAP_OPEN,1)/525600));")
rep("const tN=Math.max(MAP_OPEN,now); const ty=Math.max(MAP_T1-tN,1)/525600;",
    "const tN=Math.max(MAP_OPEN,now); const ty=Math.max(TG_GEND-tN,1)/525600;")
rep("  const feats=grid.map(col=>{ const z=[],pk=[],tr=[];",
    "  const vsel=TG.f2?tgRawP95(grid):vmax;   // TEST F2: level threshold from the raw field\n  const feats=grid.map(col=>{ const z=[],pk=[],tr=[];")
rep("if(col[i]>col[i-1]&&col[i]>=col[i+1]&&col[i]>0.08*vmax) pk.push(i); if(col[i]<col[i-1]&&col[i]<=col[i+1]&&col[i]<-0.08*vmax) tr.push(i); }",
    "if(col[i]>col[i-1]&&col[i]>=col[i+1]&&col[i]>0.08*vsel) pk.push(i); if(col[i]<col[i-1]&&col[i]<=col[i+1]&&col[i]<-0.08*vsel) tr.push(i); }")
# 11. FX display (viz candidate 7fd6345a, reapplied onto 824a06c; modelCheck uses the per-contract clock at 'now')
VIZFN = r'''/* TEST FX (viz candidate 2026-11-07, reapplied 2026-11-08). Display only — no maths changed. Active only with the FX toggle.
   mapZone(): flat zone around a trough/peak (contiguous rows within 5% of its value). colZeros(): same zero rule as the lines.
   modelCheck(): this minute's gamma column with Smile OFF and ON, same book and clock, for the read-out only. */
function mapZone(m,j,price){ if(price==null) return null; const col=m.grid[j], i=Math.round((price-m.prices[0])/m.dp); if(!(i>=0&&i<col.length)) return null;
  const v=col[i]; if(!v) return null; let a=i,b=i; const ok=x=>x!=null&&(v<0?x<=0.95*v:x>=0.95*v);
  while(a>0&&ok(col[a-1])) a--; while(b<col.length-1&&ok(col[b+1])) b++; return {lo:m.prices[a],hi:m.prices[b],w:(b-a)*m.dp}; }
function mapZoneTxt(z,dp){ return z&&z.w>=4*dp?` <i>(flat zone ${fmtN(z.lo)}–${fmtN(z.hi)})</i>`:""; }
function colZeros(prices,col){ const z=[]; for(let i=1;i<col.length;i++) if((col[i-1]<0&&col[i]>=0)||(col[i-1]>0&&col[i]<=0)) z.push(prices[i-1]+(prices[i]-prices[i-1])*(0-col[i-1])/((col[i]-col[i-1])||1)); return z; }
function modelCheck(g,se,d){ if(!g||!d||!d.rows) return null; const keep=st.smileOn;
  try{ const book=g.book||"all", near=book==="0dte"?null:nearRows(), use0=book!=="week"; const c=liveClock(), now=Math.max(MAP_OPEN,c.h*60+c.m), ty=Math.max(TG_GEND-now,1)/525600;
    const out={}; for(const sm of [false,true]){ st.smileOn=sm; const rows=mapRowsAt(null,se,d);
      const col=g.prices.map(p=>(use0?greekCol("gamma",rows,p,ty):0)+(near?greekColNear("gamma",near,p,now):0)); const z=colZeros(g.prices,col);
      out[sm?"on":"off"]={below:nearest(z,g.S,-1),above:nearest(z,g.S,1),n:z.length}; }
    return out; } finally { st.smileOn=keep; } }
'''
rep("function nearest(arr,S,dir){ let b=null; for(const v of arr){ if(dir>0&&v>S&&(b==null||v<b)) b=v; if(dir<0&&v<S&&(b==null||v>b)) b=v; } return b; }\n",
    "function nearest(arr,S,dir){ let b=null; for(const v of arr){ if(dir>0&&v>S&&(b==null||v<b)) b=v; if(dir<0&&v<S&&(b==null||v>b)) b=v; } return b; }\n" + VIZFN)
rep("""  L.push(`<div class="l">Above: ${pkA!=null?`peak <b>${f(pkA)}</b>${pct(pkA)} = ceiling / pin candidate`:"no peak"}${trA!=null?`; trough ${f(trA)} = acceleration zone`:""}. Below: ${trB!=null?`trough <b>${f(trB)}</b>${pct(trB)} = where chasing is strongest`:"no trough"}${pkB!=null?`; peak ${f(pkB)} = cushion`:""}.</div>`);""",
"""  const zPkA=TG.fx?mapZone(g,jN,pkA):null, zTrB=TG.fx?mapZone(g,jN,trB):null;
  L.push(`<div class="l">Above: ${pkA!=null?`peak <b>${f(pkA)}</b>${pct(pkA)} = ceiling / pin candidate${mapZoneTxt(zPkA,g.dp)}`:"no peak"}${trA!=null?`; trough ${f(trA)} = acceleration zone`:""}. Below: ${trB!=null?`trough <b>${f(trB)}</b>${pct(trB)} = where chasing is strongest${mapZoneTxt(zTrB,g.dp)}`:"no trough"}${pkB!=null?`; peak ${f(pkB)} = cushion`:""}.</div>`);
  let mcDiff=false; if(TG.fx){
  L.push(`<div class="l"><i>How the lines are found (this minute's column, raw values — never the colours): zero line = the raw value changes sign between two price rows ${g.dp} pts apart (linear in between); the flip quoted = the nearest zero below / above spot; ${gN.z.length} zero line${gN.z.length===1?"":"s"} in the window now${gN.z.length?": "+gN.z.map(f).join(", "):""}. Peak / trough = a local high / low beyond 8% of the ${TG.f2?"raw field's":"colour"} scale; the flat zone = rows within 5% of it.</i></div>`);
  const MC=modelCheck(g,se,d); const mcTxt=o=>o?(o.n?`${o.below!=null?f(o.below)+" below":""}${o.below!=null&&o.above!=null?" · ":""}${o.above!=null?f(o.above)+" above":""}${o.n>2?` (${o.n} zero lines)`:""}`:"no zero line in the window"):"–";
  const df=(a,b)=>a==null||b==null?(a==null)!==(b==null):Math.abs(a-b)>=10; mcDiff=MC?df(MC.off.below,MC.on.below)||df(MC.off.above,MC.on.above):false;
  if(MC) L.push(`<div class="l">Model check, gamma zero lines this minute (same book and clock): Smile OFF ${mcTxt(MC.off)} · Smile ON ${mcTxt(MC.on)}${mcDiff?" — <b>model-sensitive</b> (≥10 pts apart or one has none)":" — agree within 10 pts"}. The map draws the selected model only (Smile ${st.smileOn?"ON":"OFF"}).</div>`); }""")
rep("""  if(pkA!=null) B.push(`<div class="bl"><b>Ceiling ${f(pkA)}</b> — price tends""",
    """  if(pkA!=null) B.push(`<div class="bl"><b>Ceiling ${f(pkA)}</b>${mapZoneTxt(zPkA,g.dp)} — price tends""")
rep("""  if(lg) B.push(zB!=null?`<div class="bl"><b>Flip line ${f(zB)}</b> — below""",
    """  const ms=mcDiff?` <i>(model-sensitive — see Details)</i>`:"";
  if(lg) B.push(zB!=null?`<div class="bl"><b>Flip line ${f(zB)}</b>${ms} — below""")
rep("""  else B.push(zA!=null?`<div class="bl"><b>Flip line ${f(zA)}</b> — above""",
    """  else B.push(zA!=null?`<div class="bl"><b>Flip line ${f(zA)}</b>${ms} — above""")
rep("""  if(trB!=null) B.push(`<div class="bl"><b>Downside magnet ${f(trB)}</b> — ${lg""",
    """  if(trB!=null) B.push(`<div class="bl"><b>Downside magnet ${f(trB)}</b>${mapZoneTxt(zTrB,g.dp)} — ${lg""")
# B4a flag on the map read
rep("""  { const wi=WEEK_CLOCK_V2&&g&&g.book!=="0dte"?wkBookInfo():null; if(wi&&wi.warn.length) B.push(`<div class="bl"><b>⚠ Week book:</b> ${wi.warn.join(" · ")}.</div>`); }""",
    """  { const wi=WEEK_CLOCK_V2&&g&&g.book!=="0dte"?wkBookInfo():null; if(wi&&wi.warn.length) B.push(`<div class="bl"><b>⚠ Week book:</b> ${wi.warn.join(" · ")}.</div>`); }
  if(TG.b4&&g&&g.book!=="0dte"){ const bc=tgBookCheck(); if(bc&&bc.bad) B.push(`<div class="bl"><b>⚠ TEST B4a — morning book may be bad:</b> ${bc.txt}. Nothing is replaced unless B5 is on.</div>`); }""")
# legends
GOLD = """drawHeat("mapGammaC",g,se,d,`Dealer Δ per 2.5 pts · ${(st.mapBook||"0dte")==="0dte"?"0DTE":(st.mapBook==="week"?"1–7 DTE book":"0DTE + 1–7 DTE")}${v1On()?" + next expiry's volume":""} · ${st.gDet?"DE-TRENDED: blue/red = above/below the column average, NOT long/short":"blue long / red short"} · green peak · yellow trough · dotted zero · ±1σ from 9:35`);"""
GNEW = """drawHeat("mapGammaC",g,se,d,TG.fx?`Dealer Δ per 2.5 pts · ${(st.mapBook||"0dte")==="0dte"?"0DTE":(st.mapBook==="week"?"1–7 DTE book":"0DTE + 1–7 DTE")}${v1On()?" + next expiry's volume":""} · ${st.gDet?"DE-TRENDED: blue/red = above/below the column median, NOT long/short":"colour = raw modeled value, blue long / red short"} · ${st.mapScale==="col"?"COLUMN scale: brightness is NOT comparable across times":"one colour scale for the whole map"} · green peak · yellow trough · dotted = raw zero${st.gDet?" (not the colour edge)":""} · ±1σ from 9:35`:`Dealer Δ per 2.5 pts · ${(st.mapBook||"0dte")==="0dte"?"0DTE":(st.mapBook==="week"?"1–7 DTE book":"0DTE + 1–7 DTE")}${v1On()?" + next expiry's volume":""} · ${st.gDet?"DE-TRENDED: blue/red = above/below the column average, NOT long/short":"blue long / red short"} · green peak · yellow trough · dotted zero · ±1σ from 9:35`);"""
rep(GOLD, GNEW)
ROLD = """:(st.mapRight==="vanna"?"Dealer Δ per +1 vol pt":"Dealer Δ per 5 min")+" · orange = dealer buys · cyan = sells · green peak · yellow trough · dotted zero · "+(bk!=="0dte"&&!(r&&r.near)?"week book not loaded (am_feed near_book)":bkTxt));"""
RNEW = """:(st.mapRight==="vanna"?"Dealer Δ per +1 vol pt":"Dealer Δ per 5 min")+(TG.fx?((r&&r.base?" · colour = vs this column's median, NOT buy/sell":" · orange = dealer buys · cyan = sells")+" · "+(st.mapScale==="col"?"COLUMN scale: brightness not comparable across times":"one scale for the whole map")+" · green peak · yellow trough · dotted = raw zero"+(r&&r.base?" (not the colour edge)":"")+" · "):" · orange = dealer buys · cyan = sells · green peak · yellow trough · dotted zero · ")+(bk!=="0dte"&&!(r&&r.near)?"week book not loaded (am_feed near_book)":bkTxt));"""
rep(ROLD, RNEW)
# 13. (2026-11-08 evening) Density DN1-DN6 / VIX VX1-VX2 hooks: removed 2026-11-09 - they are production options now.
# 12. UI script before </body>
rep("\n})();\n</script>\n</body>", "\n" + UI + "\n})();\n</script>\n</body>")
open(out, "w", encoding="utf-8", newline="\n").write(H)
print("built", out, hashlib.sha256(H.encode("utf-8")).hexdigest()[:16], "from production", PROD_SHA[:16])
