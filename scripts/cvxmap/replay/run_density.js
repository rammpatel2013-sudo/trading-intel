const { chromium } = require('playwright'); const fs=require('fs');
(async()=>{
  const b=await chromium.launch(); const ctx=await b.newContext({viewport:{width:1700,height:1050}}); const p=await ctx.newPage();
  const logs=[]; p.on('console',m=>{ if(m.type()==='error'||m.type()==='warning') logs.push(m.type()+': '+m.text()); }); p.on('pageerror',e=>logs.push('pageerror: '+e.message));
  await p.goto('http://127.0.0.1:8765/tos_cockpit_v2.html',{waitUntil:'load'});
  await p.waitForTimeout(4000);
  const t0=Date.now(); let n=0, last=-1;
  while(Date.now()-t0<15*60*1000){
    const s=await p.evaluate(()=>{ const D=window.__density; return D?{n:Object.keys(D.runs).filter(k=>!k.startsWith('x')).length, x:Object.keys(D.runs).filter(k=>k.startsWith('x')).length, err:D.err, tripped:D.tripped, flowFile:window.__cockpit.st.flowFile, fromFile:window.__cockpit.st.flowFromFile, snaps:(window.__cockpit.st.flow.SPX||[]).length, basis:window.__cockpit.st.basis}:null; });
    if(s && s.n!==last){ console.log(new Date().toISOString().slice(11,19), JSON.stringify(s)); last=s.n; }
    if(s && (s.n+s.x>=78 || s.tripped)) break;
    await p.waitForTimeout(3000);
  }
  const out=await p.evaluate(()=>{ const D=window.__density; const runs=Object.keys(D.runs).filter(k=>!k.startsWith('x')).map(Number).sort((a,b)=>a-b).map(k=>{const r=D.runs[k]; return {t:r.t,k:r.k,spot:r.spot,strad:r.strad,sd:r.sd,med:r.med,p10:r.p10,p90:r.p90,zone:r.zone,pZone:r.pZone,pAboveGz:r.pAboveGz,pRails:r.pRails,gz:r.gz,vt:r.vt,vb:r.vb};});
    return {runs, basis:window.__cockpit.st.basis, sym:window.__cockpit.st.sym}; });
  fs.writeFileSync(process.argv[2]+'/density_runs.json', JSON.stringify(out));
  // show the Density tab and screenshot it
  await p.evaluate(()=>{ const c=window.__cockpit; c.st.tab='density'; c.render(); }); await p.waitForTimeout(4000);
  await p.screenshot({path:process.argv[2]+'/density_tab.png'});
  out.charm=await p.evaluate(()=>window.__density.charm);
  fs.writeFileSync(process.argv[2]+'/density_runs.json', JSON.stringify(out));
  console.log('runs',out.runs.length,'charm pts',(out.charm||[]).length,'basis',out.basis);
  console.log(logs.slice(0,15).join('\n'));
  await b.close();
})();
