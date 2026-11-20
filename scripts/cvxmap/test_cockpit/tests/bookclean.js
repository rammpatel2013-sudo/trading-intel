// BQ Book clean checks (2026-11-20). Served folder (TC_BASE) with test_old.html (test page BEFORE this ship), test.html (new),
// one session's live.json + flow_YYYYMMDD.ndjson + ti.json (with near_book) and bq_expect.json (book_expect.py).
// Checks: OFF = maps / read-out identical to the old page; ON = provenance counts and every cleaned IV equal the
// independent Python, 0DTE map unchanged (BQ only touches the 1-7 DTE book), read-out line, 0 JS errors. Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8766/', OLD=process.env.TC_OLD||'test_old.html', NEW=process.env.TC_NEW||'test.html';
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls){ const ctx=await B.newContext({viewport:{width:1600,height:1200}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource|404/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.ti&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100; },null,{timeout:90000});
  await p.waitForTimeout(2000); return {p,ctx,errs}; }
const grab=(o,book)=>o.p.evaluate(b=>{ const {st,buildMap}=window.__T, d=st.live.SPX, m=buildMap("gamma",null,d,b,null); return JSON.stringify(m?{g:m.grid,f:m.feats}:null); },book);
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  const E=JSON.parse(fs.readFileSync(process.env.BQ_EXPECT||'bq_expect.json','utf8'));
  const A=await open(B,OLD), N=await open(B,NEW);
  for(const bk of ["0dte","week","all"]) ok(await grab(A,bk)===await grab(N,bk),'OFF: gamma map identical to the old page, book '+bk);
  ok(await A.p.$eval('#mapRead',e=>e.innerHTML)===await N.p.$eval('#mapRead',e=>e.innerHTML),'OFF: read-out identical');
  ok(!A.errs.length&&!N.errs.length,'OFF: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  const M=await open(B,NEW,{tg_v1:JSON.stringify({bq:true})});
  const P=await M.p.evaluate(()=>{ const T=window.__T, st=T.st, nb=st.ti.near_book.SPX, S=st.live.SPX.spot, R=T.x('tgBqBuild')(nb,S), sg={};
    for(const r of nb.rows) sg[r.e+"|"+(r.set||"PM")+"|"+r.k]=T.x('tgBqSg')(r,S); return {S,stats:R.stats,sg}; });
  console.log('page', JSON.stringify(P.stats), 'python', JSON.stringify(E.counts));
  ok(Math.abs(P.S-E.spot)<1e-9,'ON: same spot as the Python');
  ok(["observed","conflict","paired","interpolated","unavailable","rows"].every(k=>P.stats[k]===E.counts[k]),'ON: provenance counts = independent Python');
  let mx=0; for(const k in E.sg) mx=Math.max(mx,Math.abs((P.sg[k]||0)-(E.sg[k]||0))); ok(mx<1e-12,'ON: every cleaned IV = Python (max diff '+mx+')');
  ok(await grab(M,"0dte")===await grab(A,"0dte"),'ON: 0DTE map unchanged (BQ only touches the 1-7 DTE book)');
  const wk=await grab(M,"week"); console.log('week map changed by BQ:', wk!==await grab(A,"week"));
  ok(/TEST Book quality \(08:35 book, \d+ strikes\)/.test(await M.p.$eval('#mapRead',e=>e.innerHTML)),'ON: read-out Book quality line');
  ok(!M.errs.length,'ON: 0 JS errors '+JSON.stringify(M.errs.slice(0,3)));
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
