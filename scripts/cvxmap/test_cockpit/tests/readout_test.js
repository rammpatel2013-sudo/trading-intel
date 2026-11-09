// Read-out v1: content, determinism across fresh page loads, speed. Writes srv/readouts_test.jsonl for the scorer test.
const {chromium}=require('playwright'); const fs=require('fs');
async function one(B,lf){ const ctx=await B.newContext(); const p=await ctx.newPage(); const errs=[]; p.on('pageerror',e=>errs.push(String(e)));
  await p.route('**/live.json*',r=>r.fulfill({path:(process.env.TC_SRV||'./srv/')+lf,contentType:'application/json'}));
  await p.route('**/readout',r=>r.fulfill({status:204})); await p.route('**/note',r=>r.fulfill({status:204}));
  await p.goto('http://localhost:8765/test.html'); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.ti&&T.st.ti.near_book&&T.SB; },null,{timeout:60000});
  const r=await p.evaluate(()=>{ const t0=performance.now(); const x=window.__T.x("tgReadoutAll()"); x.ms=Math.round(performance.now()-t0); return x; });
  await ctx.close(); return {r,errs}; }
(async()=>{ const B=await chromium.launch(); const out=[];
  for(const lf of ['live_1030.json','live_1330.json','live_1550.json']){
    const a=await one(B,lf), b=await one(B,lf); const ma=a.r.ms, mb=b.r.ms; delete a.r.ms; delete b.r.ms;
    const same=JSON.stringify(a.r)===JSON.stringify(b.r);
    const o=a.r.cfgs['off|P'], s=a.r.cfgs['off|S'];
    console.log(`${lf}: ${ma} ms / ${mb} ms · deterministic across fresh loads: ${same} · errors ${a.errs.length+b.errs.length} · cfg errors ${Object.values(a.r.cfgs).filter(x=>x&&x.err).length}`);
    console.log(`  meta: date ${a.r.date} ${a.r.hm} spot ${a.r.spot} straddle0 ${a.r.straddle0} im30 ${a.r.im30} ids ${JSON.stringify(a.r.ids)}`);
    console.log(`  off|P: g_spot ${o.g_spot} zeros ${JSON.stringify(o.zeros)} peaks ${o.peaks.length} troughs ${JSON.stringify(o.troughs).slice(0,160)}`);
    console.log(`  off|P close ${JSON.stringify(o.close).slice(0,160)} · d1 ${JSON.stringify(o.d1).slice(0,160)} · fri ${JSON.stringify(o.fri).slice(0,120)}`);
    for(const k of Object.keys(a.r.cfgs)) { const c=a.r.cfgs[k]; if(c&&!c.err) console.log(`   ${k.padEnd(12)} close ${c.close.pred} d1 ${c.d1&&c.d1.pred} gS ${c.g_spot} zeros ${c.zeros.length} pk ${c.peaks.length} tr ${c.troughs.length}`); }
    a.r.slot=a.r.hm; a.r.server_ts='test'; out.push(JSON.stringify(a.r)); }
  fs.writeFileSync((process.env.TC_SRV||'./srv/')+'readouts_test.jsonl',out.join('\n')+'\n'); await B.close(); })();
