// Flow panel F1-F3 checks (2026-11-19 night). Folder served on TC_BASE with test_old.html (instrument.py of the test page
// BEFORE this ship), test.html (the new one), one session's live.json + flow_YYYYMMDD.ndjson + ti.json, terrain.json
// (terrain.py) and fp_expect.json (flowpanel_expect.py). Checks: OFF = Map + Flow numbers / read-outs identical to the old
// page and nothing drawn; ON = the panel draws, F1 / F2 of the last minute equal the independent Python numbers, F3 label =
// terrain.classify, map numbers unchanged, 0 JS errors. Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8766/', OUT=process.env.OUT||'./shots/', OLD=process.env.TC_OLD||'test_old.html', NEW=process.env.TC_NEW||'test.html'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls){ const ctx=await B.newContext({viewport:{width:1600,height:1400}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource|404/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'flow'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100; },null,{timeout:90000});
  await p.waitForTimeout(2000); return {p,ctx,errs}; }
const grab=o=>o.p.evaluate(()=>{ const {st,buildMap}=window.__T, d=st.live.SPX, R={}; for(const book of ["0dte","all"]) for(const kind of ["gamma","charm"]){ const m=buildMap(kind,null,d,book,null); R[book+kind]=m?{g:m.grid,v:m.vmax}:null; } return JSON.stringify(R); });
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  const E=JSON.parse(fs.readFileSync(process.env.FP_EXPECT||'fp_expect.json','utf8'));
  const A=await open(B,OLD), N=await open(B,NEW);
  ok(await grab(A)===await grab(N),'OFF: buildMap identical to the old test page');
  ok(await A.p.$eval('#f2Line',e=>e.innerHTML)===await N.p.$eval('#f2Line',e=>e.innerHTML),'OFF: signed-flow line identical');
  ok(await N.p.$eval('#tgFp',e=>e.hidden)===true,'OFF: panel hidden');
  ok(await N.p.$('#tgFpBtn')!==null,'OFF: Flow panel button present');
  ok(!A.errs.length&&!N.errs.length,'OFF: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  await N.p.click('#tgFpBtn'); await N.p.waitForTimeout(1500);
  ok(await N.p.$eval('#tgFp',e=>!e.hidden),'ON: panel shown');
  const L=await N.p.evaluate(()=>{ const s=window.__T.x('tgFpSeries()'); return s&&s[s.length-1]; });
  console.log('page last', JSON.stringify(L)); console.log('python  ', JSON.stringify(E));
  const rel=(a,b)=>Math.abs(a-b)<=1e-6*Math.max(1,Math.abs(b));
  ok(L&&L.hm===E.t&&rel(L.x,E.x),'ON: last minute = the log\'s last RTH minute');
  ok(L&&rel(L.oi,E.oi)&&rel(L.tr,E.tr),'ON: F1 OI part + measured part = independent Python');
  ok(L&&rel(L.o0,E.o0),'ON: F2 0DTE OVI = independent Python');
  const head=await N.p.$eval('#tgFpHead',e=>e.textContent); console.log('head', head.slice(0,500));
  ok(new RegExp('F3 terrain at [^:]+: '+E.ter+' ').test(head),'ON: F3 badge = terrain.classify ('+E.ter+')');
  ok(/F1 dealer gamma at spot/.test(head)&&/F2 OVI/.test(head),'ON: read-out lines');
  ok(await grab(N)===await grab(A),'ON: map numbers unchanged');
  await N.p.$eval('#tgFp',e=>e.scrollIntoView()); await N.p.waitForTimeout(300); await N.p.screenshot({path:OUT+'flowpanel_on.png'});
  ok(!N.errs.length,'ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  await N.p.click('#tgFpBtn'); await N.p.waitForTimeout(400); ok(await N.p.$eval('#tgFp',e=>e.hidden)===true,'OFF again: hidden');
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
