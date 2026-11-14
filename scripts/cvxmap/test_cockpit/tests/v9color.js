// TEST page checks (2026-11-13 overnight): V9 "Week IV = VIX9D" toggle (#21) + the Color strip (#87). Served folder on :8765
// with test.html (instrument.py of cvx_cockpit_test.html) and one session's files. Exit 1 on any failure.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.OUT||'./shots/'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,ls,vp){ const ctx=await B.newContext({viewport:vp||{width:1600,height:1150}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(BASE+'test.html'); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti&&T.TG; },null,{timeout:60000});
  await p.waitForTimeout(1200); return {p,ctx,errs}; }
(async()=>{ const B=await chromium.launch();
  const day=JSON.parse(fs.readFileSync((process.env.TC_SRV||'./')+'live.json','utf8')).ts.slice(0,10);
  { const o=await open(B,{['cockpit.tg_v9dq0_'+day]:'17.00',['cockpit.atm0_'+day+'_SPX']:'0.20'});
    const r=await o.p.evaluate(()=>{ const T=window.__T, st=T.st, d=st.live.SPX; const TG=T.TG; const keep=TG.v9; const out={};
      TG.v9=false; const a=T.weekIVShift(); const g0=T.buildMap("gamma",null,d,"week",null), z0=T.buildMap("gamma",null,d,"0dte",null);
      TG.v9=true; const b=T.weekIVShift(); const g1=T.buildMap("gamma",null,d,"week",null), z1=T.buildMap("gamma",null,d,"0dte",null);
      TG.v9=keep; out.a=a; out.b=b; out.weekSame=JSON.stringify(g0.grid)===JSON.stringify(g1.grid); out.zeroSame=JSON.stringify(z0.grid)===JSON.stringify(z1.grid);
      out.err=T.x('tgWkIvErr()'); out.cfg=T.x('TG_CFGS.map(c=>c[0]).join()'); return out; });
    ok(/VIX9D 17\.00→17\.67/.test(r.b.src)&&Math.abs(r.b.dv-0.0067)<1e-9,'V9 on: shift = Δ VIX9D (v9dq) '+r.b.src+' dv '+r.b.dv);
    ok(!/^TEST/.test(r.a.src),'V9 off: production rule '+r.a.src);
    ok(!r.weekSame&&r.zeroSame,'V9 on: Week book gamma changes, 0DTE book identical');
    ok(r.err&&r.err.n>0&&r.err.half!=null&&r.err.v9!=null,'read-out records both rules vs the live week chain: '+JSON.stringify(r.err));
    ok(/,v9$/.test(r.cfg),'TG_CFGS records config v9 every 15 min');
    ok(o.errs.length===0,'V9: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
  { const o=await open(B,{'cockpit.tgColor':'1'}); await o.p.waitForTimeout(800);
    ok(!(await o.p.$eval('#tgColor',e=>e.hidden)),'Color ON: strip shown'); const t=await o.p.$eval('#tgColorHead',e=>e.innerText); ok(/time alone/.test(t),'Color ON: '+t.slice(0,200));
    const chk=await o.p.evaluate(()=>{ const T=window.__T, st=T.st, d=st.live.SPX; const m=T.buildMap("gamma",null,d); const R=T.x('tgColorCalc')(m); if(!R||R.late) return null;
      const jN=T.x('colAt')(m,Math.max(570,m.now)), j3=T.x('colAt')(m,Math.min(m.now+30,m.times[m.times.length-1])); const i=R.iS; return Math.abs(R.col[i]-(m.grid[j3][i]-m.grid[jN][i])); });
    ok(chk!=null&&chk<1e-9,'Color = gamma(now+30) − gamma(now) from the map projection');
    const ink=await o.p.evaluate(()=>{ const c=document.getElementById('tgColorC'); const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let k=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) k++; return k; }); ok(ink>2000,'Color ON: bars drawn ('+ink+' px)');
    await o.p.screenshot({path:OUT+'color_on.png'}); ok(o.errs.length===0,'Color ON: 0 JS errors '+JSON.stringify(o.errs.slice(0,3)));
    await o.p.click('#tgColorBtn'); await o.p.waitForTimeout(300); ok(await o.p.$eval('#tgColor',e=>e.hidden),'Color: button turns it off'); await o.ctx.close(); }
  await B.close(); console.log(fails?`V9COLOR FAILED ${fails}/${n}`:`V9COLOR OK ${n}/${n}`); process.exit(fails?1:0); })();
