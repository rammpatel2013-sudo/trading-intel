const { chromium } = require('playwright'); const fs=require('fs');
const R=process.argv[2]; const base=JSON.parse(fs.readFileSync(R+'/live_1600.json','utf8'));
const flow=fs.readFileSync(R+'/site/flow_20261028.ndjson','utf8').split('\n').filter(Boolean).map(l=>JSON.parse(l)).filter(j=>j.s==='SPX');
const xAt={}; flow.forEach(j=>xAt[j.t]=j.x);
const pad=n=>String(n).padStart(2,'0');
const times=[]; for(let m=9*60+40;m<=16*60;m+=10) times.push(m); [13*60+55,14*60+5,14*60+15,14*60+25,14*60+35,14*60+45].forEach(m=>times.push(m)); times.sort((a,b)=>a-b);
(async()=>{
  const b=await chromium.launch(); let f=0;
  for(const m of times){
    const t=pad(Math.floor(m/60))+':'+pad(m%60); let x=xAt[t]; for(let q=1;q<4&&x==null;q++){ const tt=pad(Math.floor((m-q)/60))+':'+pad((m-q)%60); x=xAt[tt]; }
    const L=JSON.parse(JSON.stringify(base)); L.ts='2026-10-28T'+t+':00'; if(x) L.SPX.spot=x;
    L.SPX.tl=L.SPX.tl.filter(p=>p[0]<=t);
    fs.writeFileSync(R+'/site/live.json',JSON.stringify(L));
    const ctx=await b.newContext({viewport:{width:1700,height:1050}}); const p=await ctx.newPage();
    await p.goto('http://127.0.0.1:8765/tos_cockpit_v2.html',{waitUntil:'load'});
    const last=Math.min(385,Math.floor((m-570)/5)*5); const t0=Date.now();
    while(Date.now()-t0<60000){ const ok=await p.evaluate(l=>{ const D=window.__density; return !!(D&&D.runs[0]&&D.runs[l]); },last); if(ok) break; await p.waitForTimeout(500); }
    await p.evaluate(()=>{ const c=window.__cockpit; c.st.tab='density'; c.render(); }); await p.waitForTimeout(1500);
    await p.screenshot({path:R+'/frames/f'+pad(f)+'.png'}); f++; console.log(t,'last run',last); await ctx.close();
  }
  fs.writeFileSync(R+'/site/live.json',JSON.stringify(base)); await b.close();
})();
