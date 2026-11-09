// Parity: production page vs test page with ALL toggles off, on real 11/06 data, at 4 clock times.
// Compares every number of buildMap (grid, colour grid, features) and the map read text. Exit 1 on any difference.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE='http://localhost:8765/'; const SC=process.argv[2]||'all';
const scen=[["eod","live.json"],["1030","live_1030.json"],["1330","live_1330.json"],["1550","live_1550.json"]];
async function grab(browser,page,liveFile,tgSet){
  const ctx=await browser.newContext(); const p=await ctx.newPage();
  await p.route('**/live.json*',r=>r.fulfill({path:(process.env.TC_SRV||'./srv/')+liveFile,contentType:'application/json'}));
  if(tgSet) await p.addInitScript(s=>{ try{ localStorage.setItem('tg_v1',s); }catch(e){} }, JSON.stringify(tgSet));
  await p.addInitScript(()=>{ try{ localStorage.setItem('tab','"map"'); }catch(e){} });
  const errs=[]; p.on('pageerror',e=>errs.push(String(e)));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; if(!T) return false; const st=T.st; return st.live&&st.live.SPX&&st.flow&&st.flow.SPX&&st.flow.SPX.length>100&&st.ti&&st.ti.near_book&&T.SB; },null,{timeout:60000});
  const out=await p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX; const keep={smileOn:st.smileOn,basis:st.basis};
    for(const basis of ["oi","ois"]) for(const sm of [false,true]) for(const book of ["0dte","week","all"]) for(const kind of ["gamma","charm","vanna"]){
      st.smileOn=sm; st.basis=basis; const m=buildMap(kind,null,d,book,null); if(!m){ R[[basis,sm,book,kind].join("|")]=null; continue; }
      R[[basis,sm,book,kind].join("|")]={grid:m.grid,cgrid:m.cgrid,feats:m.feats,vmax:m.vmax,times:m.times,prices:m.prices,share:m.share};
      if(kind==="gamma"&&book==="all"){ const ch=buildMap("charm",null,d,book,null), va=buildMap("vanna",null,d,book,null); R[[basis,sm,"read"].join("|")]=mapRead(m,ch,null,null,d)+"||"+mapRead(m,null,va,null,d); } }
    st.smileOn=keep.smileOn; st.basis=keep.basis; return R; });
  await ctx.close(); return {out,errs};
}
(async()=>{ const browser=await chromium.launch(); let bad=0, n=0;
  for(const [nm,lf] of scen){ if(SC!=='all'&&SC!==nm) continue;
    const A=await grab(browser,'prod.html',lf), B=await grab(browser,'test.html',lf,{});
    if(A.errs.length||B.errs.length) console.log(nm,'page errors',A.errs.slice(0,3),B.errs.slice(0,3));
    for(const k of Object.keys(A.out)){ n++; const a=JSON.stringify(A.out[k]), b=JSON.stringify(B.out[k]); if(a!==b){ bad++; console.log('DIFF',nm,k,a.length,b.length); } }
    const cells=Object.values(A.out).filter(x=>x&&x.grid).reduce((s,x)=>s+x.grid.length*x.grid[0].length,0);
    console.log(nm,'compared',Object.keys(A.out).length,'outputs,',cells,'grid cells');
  }
  await browser.close(); console.log(bad?`PARITY FAILED: ${bad}/${n} differ`:`PARITY OK: ${n}/${n} identical (byte-for-byte JSON)`); process.exit(bad?1:0); })();
