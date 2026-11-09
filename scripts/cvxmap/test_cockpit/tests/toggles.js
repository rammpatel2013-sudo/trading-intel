// Per-toggle checks on the test page (11/06 data). Prints one line per check.
const {chromium}=require('playwright');
const BASE='http://localhost:8765/';
async function open(browser,liveFile,tg,qs,tiFile){
  const ctx=await browser.newContext(); const p=await ctx.newPage();
  await p.route('**/live.json*',r=>r.fulfill({path:(process.env.TC_SRV||'./srv/')+liveFile,contentType:'application/json'}));
  if(tiFile) await p.route('**/ti.json*',r=>r.fulfill({path:(process.env.TC_SRV||'./srv/')+tiFile,contentType:'application/json'}));
  await p.addInitScript(s=>{ try{ localStorage.setItem('tg_v1',s); }catch(e){} }, JSON.stringify(tg||{}));
  const errs=[]; p.on('pageerror',e=>errs.push(String(e)));
  await p.goto(BASE+'test.html'+(qs||'')); await p.waitForFunction(()=>{ const T=window.__T; if(!T) return false; const st=T.st; return st.live&&st.live.SPX&&st.flow&&st.flow.SPX&&st.flow.SPX.length>100&&st.ti&&st.ti.near_book&&T.SB; },null,{timeout:60000});
  return {p,ctx,errs};
}
const log=(...a)=>console.log(...a);
(async()=>{ const B=await chromium.launch();
 // ---------- helpers run in page
 const colAt=`(kind,book,sm,setTG)=>{ const T=window.__T, st=T.st, d=st.live.SPX; st.smileOn=sm; const m=T.buildMap(kind,null,d,book,null); const c=T.liveClock(), now=c.h*60+c.m; let j=0; for(let q=0;q<m.times.length;q++) if(m.times[q]<=Math.max(570,now)) j=q;
   const col=m.grid[j]; const z=[]; for(let i=1;i<col.length;i++) if((col[i-1]<0&&col[i]>=0)||(col[i-1]>0&&col[i]<=0)) z.push(m.prices[i-1]+m.dp*(0-col[i-1])/(col[i]-col[i-1]||1));
   const S=d.spot, i0=Math.round((S-m.prices[0])/m.dp); return {now,end:m.times[m.times.length-1],gS:col[i0],z:z.map(x=>+x.toFixed(2)),grid:m.grid,feats:m.feats.map(f=>f.pk.length+f.tr.length).reduce((a,b)=>a+b,0)}; }`;
 const pages=new Map();
 async function run(lf,tg,fn,qs,tiFile){ const key=lf+'|'+(qs||'')+'|'+(tiFile||''); let o=pages.get(key); if(!o){ o=await open(B,lf,{},qs,tiFile); pages.set(key,o); }
   await o.p.evaluate(t=>{ const T=window.__T; T.x(`for(const k of Object.keys(TG)) TG[k]=(k in TG_SEL)?"":false; Object.assign(TG,${JSON.stringify(t)}); tgApply(); _mapCaches.clear(); WK_CACHE.clear();`); T.st.smileOn=false; }, tg||{});
   const r=await o.p.evaluate(fn); if(o.errs.length){ log('  page errors:',o.errs.slice(0,2)); o.errs.length=0; } return r; }
 const near=(z,S)=>{ let a=null,b=null; for(const v of z){ if(v<S&&(a==null||v>a)) a=v; if(v>S&&(b==null||v<b)) b=v; } return [a,b]; };
 const maxd=(A,Bg)=>{ let m=0; for(let j=0;j<A.length;j++) for(let i=0;i<A[j].length;i++) m=Math.max(m,Math.abs(A[j][i]-Bg[j][i])); return m; };
 const rel=(a,b)=>Math.abs(a-b)/Math.max(1e-12,Math.abs(b));
 const S0=await run('live_1030.json',{},`(()=>window.__T.st.live.SPX.spot)()`);
 // ---- baseline columns at 10:30
 const base={}; for(const [book,sm] of [["all",false],["all",true],["0dte",false],["0dte",true]]) base[book+sm]=await run('live_1030.json',{},`(${colAt})("gamma","${book}",${sm})`);
 log(`spot ${S0} · baseline 10:30 all/plain gamma at spot ${base.allfalse.gS.toFixed(1)} zero lines below/above ${near(base.allfalse.z,S0)}`);
 // ---- each toggle alone: what moves
 for(const [id,val] of [["b2","1600"],["b2","1615"],["b3",true],["b6",true],["b5",true],["e2",true],["d1",true],["c2",true],["d4",true],["f2",true]]){
   for(const sm of [false,true]){ const tg={}; tg[id]=val; const r=await run('live_1030.json',tg,`(${colAt})("gamma","all",${sm})`); const b0=base["all"+sm];
     log(`${id}${val===true?"":"="+val} smile ${sm?"ON ":"off"}: gamma at spot ${b0.gS.toFixed(1)} → ${r.gS.toFixed(1)} (${(100*(r.gS/b0.gS-1)).toFixed(1)}%) · zeros ${JSON.stringify(near(b0.z,S0))} → ${JSON.stringify(near(r.z,S0))} · max cell change ${maxd(r.grid,b0.grid).toFixed(1)} · features ${b0.feats}→${r.feats}`); } }
 // ---- B2: 16:00 vs 16:15 give the same gamma at the current column (0DTE book)
 { const a=await run('live_1030.json',{b2:"1600"},`(${colAt})("gamma","0dte",false)`), b=await run('live_1030.json',{b2:"1615"},`(${colAt})("gamma","0dte",false)`);
   log(`B2 check: 0DTE gamma at spot now, 16:00 clock ${a.gS.toFixed(4)} vs 16:15 clock ${b.gS.toFixed(4)} (rel diff ${rel(a.gS,b.gS).toExponential(1)}; theory = identical now, projections differ); production ${base["0dtefalse"].gS.toFixed(4)}`); }
 // ---- A5 simulated early close 13:00
 { const r=await run('live_1030.json',{a5:true},`(${colAt})("gamma","0dte",false)`,'?earlyclose=13:00'); const r2=await run('live_1030.json',{a5:true},`(${colAt})("gamma","0dte",false)`);
   log(`A5 sim 13:00: map ends ${r.end} (780 expected), gamma at spot ${r.gS.toFixed(1)} vs normal-day ${base["0dtefalse"].gS.toFixed(1)}; A5 on, no early close: ends ${r2.end}, identical to production: ${maxd(r2.grid,base["0dtefalse"].grid)===0}`); }
 // ---- stats: B3 / B6 / B5 / B4
 { const r=await run('live_1030.json',{b3:true,b6:true,b5:true,b4:true},`(()=>{ const T=window.__T; T.buildMap("gamma",null,T.st.live.SPX,"all",null); const S=T.x("TG_STATS"); const bc=T.x("tgBookCheck()"); return {S:JSON.parse(JSON.stringify(S)),bc}; })()`);
   log(`stats 10:30: B3 dropped ${r.S.rej} quote sides · B6 filled ${r.S.fill} · B5 live ${r.S.wkLive} / fallback ${r.S.wkFb} · B4a ${r.bc&&r.bc.txt} → ${r.bc&&r.bc.bad?"FLAG":"ok"}`); }
 // ---- B4 on a corrupted book (+50 vol pts on every 3rd strike near spot)
 { const fs=require('fs'); const T=JSON.parse(fs.readFileSync((process.env.TC_SRV||'./srv/')+'ti.json')); let n=0; for(const r of T.near_book.SPX.rows){ if(Math.abs(r.k-S0)<=150&&(r.k/5)%3===0){ if(r.cIV) r.cIV+=0.5; if(r.pIV) r.pIV+=0.5; n++; } } fs.writeFileSync((process.env.TC_SRV||'./srv/')+'ti_bad.json',JSON.stringify(T));
   const r=await run('live_1030.json',{b4:true},`(()=>window.__T.x("tgBookCheck()"))()`,'', 'ti_bad.json'); log(`B4a corrupted book (${n} rows +50 pts): ${r&&r.txt} → ${r&&r.bad?"FLAG (correct)":"MISSED"}`); }
 // ---- E2: steps in the Smile gamma column (0DTE, 0.25-pt grid, ±2%)
 { const fn=`(()=>{ const T=window.__T, st=T.st, d=st.live.SPX; st.smileOn=true; const rows=T.mapRowsAt(null,null,d); const ty=Math.max(960-630,1)/525600; const out=[]; for(let p=d.spot*0.98;p<=d.spot*1.02;p+=0.25) out.push(T.greekCol("gamma",rows,p,ty)); const dv=out.slice(1).map((v,i)=>Math.abs(v-out[i])); const s=dv.slice().sort((a,b)=>a-b), med=s[Math.floor(s.length/2)]; return {steps:dv.filter(x=>x>5*med).length,max:Math.max(...dv)/med}; })()`;
   const a=await run('live_1030.json',{},fn), b=await run('live_1030.json',{e2:true},fn); log(`E2 steps (>5× typical change on a 0.25-pt grid): hard zones ${a.steps} (max ${a.max.toFixed(1)}×) → smooth ${b.steps} (max ${b.max.toFixed(1)}×)`); }
 // ---- D1: FD check of full-slope gamma vs the derivative of D = Δ + Vega·b along σ(S)
 { const fn=`(()=>{ const T=window.__T, st=T.st, d=st.live.SPX; st.smileOn=true; const X=T.x; const rows=T.mapRowsAt(null,null,d); const ty=365/525600; const out=[];
     const PHI=X("PHI"), N=X("ncdf"), beta=(k,x)=>{ const b=X("smileBeta")(k,x,0); return b==null?0:b; };
     for(const S of [d.spot, d.spot*0.995, d.spot*1.01]){ const g=T.greekCol("gamma",rows,S,ty)/2.5;
       const D=Sp=>{ let t=0; for(const r of rows){ if(!(r.sg>0)) continue; const pos=r.cp-r.pp; if(!pos) continue; const n=20; let integ=0; for(let i=0;i<n;i++){ const a=S+(Sp-S)*i/n, b=S+(Sp-S)*(i+1)/n; integ+=(beta(r.k,a)/a+beta(r.k,b)/b)/2*(b-a); }
         const sg=r.sg+integ, sq=Math.sqrt(ty), d1=(Math.log(Sp/r.k)+0.5*sg*sg*ty)/(sg*sq); t+=pos*(N(d1)+Sp*PHI(d1)*sq*beta(r.k,Sp)/Sp); } return t; };
       for(const h of [1,0.1]){ const fd=(D(S+h)-D(S-h))/(2*h); out.push([+S.toFixed(1),h,+g.toFixed(4),+fd.toFixed(4),+(Math.abs(g-fd)/Math.abs(fd)*100).toFixed(3)]); } }
     return out; })()`;
   const r=await run('live_1030.json',{e2:true,d1:true},fn); log(`D1 FD (S, bump, formula, finite difference, % gap): ${JSON.stringify(r)}`);
   const r0=await run('live_1030.json',{e2:true},fn); log(`   same check with D1 OFF (frozen slope vs full target): ${JSON.stringify(r0)}`); }
 // ---- C2: path IV equals σ0 at the anchor and its slope equals β/S there
 { const r=await run('live_1030.json',{c2:true},`(()=>{ const T=window.__T, X=T.x, d=T.st.live.SPX; const rows=T.mapRowsAt(null,null,d).filter(r=>r.sg>0); const r=rows[Math.floor(rows.length/2)]; const S=d.spot, h=0.5;
     const sg0=X("tgPathSg")(r,S), up=X("tgPathSg")(r,S+h), dn=X("tgPathSg")(r,S-h), slope=(up-dn)/(2*h), b=X("smileBeta")(r.k,S,0)/S; const far=X("tgPathSg")(r,S*0.99);
     return {k:r.k,sg:r.sg,sg0,slope,b,far}; })()`);
   log(`C2 strike ${r.k}: IV at anchor ${r.sg.toFixed(4)} → path ${r.sg0.toFixed(4)} · slope ${r.slope.toExponential(3)} vs β/S ${r.b.toExponential(3)} · IV at −1%: ${r.far.toFixed(4)} (${((r.far-r.sg)*100).toFixed(2)} vol pts)`); }
 // ---- D4: charm at 365 min ≈ production; at 15:50 differs; matches exact 5-min delta change
 for(const lf of ['live_1030.json','live_1550.json']){ const fn=`(()=>{ const T=window.__T, st=T.st, d=st.live.SPX; st.smileOn=false; const rows=T.mapRowsAt(null,null,d); const c=T.liveClock(), now=c.h*60+c.m, ty=Math.max(960-now,1)/525600; return T.greekCol("charm",rows,d.spot,ty); })()`;
   const a=await run(lf,{},fn), b=await run(lf,{d4:true},fn); log(`D4 ${lf}: charm at spot rate×5 ${a.toFixed(2)} vs real 5-min ${b.toFixed(2)} (${(100*(b/a-1)).toFixed(1)}%)`); }
 // ---- FX text + F2
 { const r=await run('live_1030.json',{fx:true},`(()=>{ const T=window.__T, d=T.st.live.SPX; const g=T.buildMap("gamma",null,d,"all",null), c=T.buildMap("charm",null,d,"all",null); const h=T.mapRead(g,c,null,null,d); return {how:h.includes("How the lines are found"),mc:h.includes("Model check")}; })()`);
   log(`FX: details show line rules ${r.how}, Smile OFF/ON model check ${r.mc}`); }
 { const fn=`(()=>{ const T=window.__T, d=T.st.live.SPX; T.st.smileOn=false; const m=T.buildMap("charm",null,d,"all",null); return m.feats.map(f=>f.pk.length+f.tr.length).reduce((a,b)=>a+b,0); })()`;
   const a=await run('live_1030.json',{},fn), b=await run('live_1030.json',{f2:true},fn); log(`F2 charm All book: peaks+troughs over the map ${a} (colour-field threshold) → ${b} (raw-field threshold)`); }
 // ---- performance: full gamma + charm build, all off vs everything on
 for(const [nm,tg] of [["all off",{}],["all on",{b2:"1600",b3:true,b6:true,b5:true,b4:true,e2:true,d1:true,c2:true,d4:true,f2:true,fx:true}]]){
   const r=await run('live_1030.json',tg,`(()=>{ const T=window.__T, d=T.st.live.SPX; T.st.smileOn=true; T.x("_mapCaches.clear(); WK_CACHE.clear();"); const t0=performance.now(); T.buildMap("gamma",null,d,"all",null); T.buildMap("charm",null,d,"all",null); return performance.now()-t0; })()`);
   log(`perf ${nm}, Smile ON, gamma+charm All book from cold: ${r.toFixed(0)} ms`); }
 for(const o of pages.values()) await o.ctx.close(); await B.close(); })();
