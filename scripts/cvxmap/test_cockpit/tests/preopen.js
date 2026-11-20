// Pre-open SPX spot on MAIN (2026-11-20): before 09:30 (and 09:30-09:34 while the index still shows yesterday's close)
// the whole cockpit uses SPX = yesterday's close x live SPY / SPY's close. Folder served on TC_BASE with 4 cases
// a (11/19 13:17 as logged), b (09:15, SPX = close), c (09:31, SPX still within 0.75 of close), d (09:31, SPX printed 7,396);
// each has prod_old.html (before) + prod.html (after), live.json, ti.json, flow logs and a 1-line flow_20261118.ndjson.
const {chromium}=require('playwright');
const BASE=process.env.TC_BASE||'http://localhost:8767/';
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,url,clock){ const ctx=await B.newContext({viewport:{width:1600,height:1200}}); const p=await ctx.newPage(); const errs=[];
  if(clock) await p.clock.setFixedTime(new Date(clock));   // wall clock = the fixture day (the page reads yesterday's log by today's date)
  p.on('pageerror',e=>errs.push(String(e)));
  await p.addInitScript(()=>{ localStorage.setItem('cockpit.tab','map'); });
  await p.goto(url); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.ti&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.prevClose&&T.st.prevClose.SPY; },null,{timeout:90000});
  await p.waitForTimeout(1500); return {p,errs}; }
const info=o=>o.p.evaluate(()=>{ const T=window.__T, st=T.st, d=st.live.SPX, m=T.buildMap("gamma",null,d,"all",null), nr=T.x('nearRows')();
  return {spot:d.spot,raw:d.spot_raw,src:d.spot_src||null,spy:st.live.SPY.spot,pcSPY:st.prevClose.SPY,imp:T.x('impliedSpot')('SPX'),
    feats:m?JSON.stringify(m.feats):null, grid:m?JSON.stringify(m.grid):null, read:(document.getElementById('mapRead')||{}).innerHTML||""}; });
(async()=>{ const B=await chromium.launch();
  const CL='2026-11-19T13:18:00-05:00'; const A0=await open(B,BASE+'a/prod_old.html',CL), A1=await open(B,BASE+'a/prod.html',CL); const a0=await info(A0), a1=await info(A1);
  ok(a1.spot===a1.raw&&a1.src===null,'13:17: SPX spot untouched ('+a1.spot+')');
  ok(a0.grid===a1.grid&&a0.feats===a1.feats,'13:17: gamma map identical to the page before the change');
  for(const [c,want,cl] of [['b','implied','2026-11-19T09:15:30-05:00'],['c','implied','2026-11-19T09:31:30-05:00'],['d','raw','2026-11-19T09:31:30-05:00']]){
    const O=await open(B,BASE+c+'/prod_old.html',cl), N=await open(B,BASE+c+'/prod.html',cl); const o=await info(O), x=await info(N);
    const exp=7322.97*x.spy/x.pcSPY;   // GAP.prev_close x live SPY / SPY close
    if(want==='implied'){
      ok(Math.abs(x.spot-exp)<1e-9&&x.src==='implied via SPY',`${c}: SPX spot = 7,322.97 x SPY ${x.spy} / ${x.pcSPY.toFixed(4)} = ${x.spot.toFixed(2)} (raw ${x.raw})`);
      ok(x.imp&&Math.abs(x.imp.S-x.spot)<1e-9,`${c}: read panel uses the same price`);
      ok(o.spot===x.raw&&o.feats!==x.feats,`${c}: map levels now measured from the implied price (old page used ${o.spot})`);
    } else ok(x.spot===x.raw&&x.src===null&&o.grid===x.grid&&o.feats===x.feats,`${c}: index printed (${x.raw}) -> live SPX, map identical to before`);
    ok(!N.errs.length,`${c}: 0 JS errors `+JSON.stringify(N.errs.slice(0,2)));
  }
  ok(!A1.errs.length,'a: 0 JS errors');
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
