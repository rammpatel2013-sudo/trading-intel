// Vol board option (2026-11-14) on the VIX tab of the PRODUCTION page: off = nothing drawn or fetched; on = the vf-v2
// signal card, the market cards (most unusual first), the fear path, the VIX curve and the fund cards; phone width; missing
// volx.json; a FIRE state; an image too old to import the strategy. Harness: a folder served on :8765 with prod.html
// (instrument.py of the page) and one session's files (11/13: live.json, ti.json, flow_20261113.ndjson, ...) plus a
// volx.json written by volx.py; the page clock is fixed at 11/13 17:30 ET. Writes volx_fire.json / volx_err.json and three
// screenshots next to it. Exit 1 on fail. Parity (everything OFF = old maths / canvases / VIX text) is parity_vb.js.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE='http://localhost:8765/', PAGE=process.argv[2]||'prod.html'; let bad=0; const ok=(c,m)=>{ console.log((c?'OK   ':'FAIL ')+m); if(!c) bad++; };
async function open(browser,opts,width,volxFile){
  const ctx=await browser.newContext({viewport:{width:width||1400,height:1000}}); const p=await ctx.newPage(); const errs=[], reqs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('request',r=>reqs.push(r.url()));
  if(volxFile===null) await p.route('**/volx.json*',r=>r.fulfill({status:404,body:'nf'}));
  else if(volxFile) await p.route('**/volx.json*',r=>r.fulfill({path:volxFile,contentType:'application/json'}));
  await p.clock.setFixedTime(new Date('2026-11-13T17:30:00-05:00'));
  await p.addInitScript(o=>{ try{ localStorage.setItem('cockpit.tab','vix'); if(o) localStorage.setItem('cockpit.vxOpt',JSON.stringify(o)); }catch(e){} }, opts||null);
  await p.goto(BASE+PAGE); await p.waitForFunction(()=>window.__T&&window.__T.st&&window.__T.st.live,null,{timeout:60000});
  await p.waitForTimeout(2500); return {ctx,p,errs,reqs}; }
(async()=>{ const b=await chromium.launch();
  { const {ctx,p,errs,reqs}=await open(b,null);
    ok(await p.$eval('#vxBoard',e=>e.hidden&&e.innerHTML===''),'OFF: board hidden and empty');
    ok(!reqs.some(u=>u.includes('volx.json')),'OFF: volx.json not fetched (all VIX options off)');
    ok(await p.$$eval('#vxOpts button[data-o="vb"]',x=>x.length===1&&x[0].getAttribute('aria-pressed')==='false'),'OFF: Vol board button shown, not pressed');
    ok(errs.length===0,'OFF: 0 JS errors '+errs.slice(0,2)); await ctx.close(); }
  { const {ctx,p,errs,reqs}=await open(b,{vb:true});
    ok(reqs.some(u=>u.includes('volx.json')),'ON: volx.json fetched');
    ok(await p.$eval('#vxBoard',e=>!e.hidden),'ON: board visible');
    const n=await p.$$eval('.vbcard',x=>x.length), f=await p.$$eval('.vbfund',x=>x.length); ok(n===13&&f===7,`ON: 13 market cards (${n}), 7 fund cards (${f})`);
    const first=await p.$$eval('.vbcard .s',x=>x.slice(0,4).map(e=>e.textContent)); ok(first[0]==='RVX','ON: most unusual first: '+first.join(','));
    ok(await p.$eval('.vbsig',e=>e.classList.contains('ph-ARMED')&&/ARMED/.test(e.textContent)),'ON: signal card ARMED (11/06 close 25.64)');
    ok(await p.$$eval('#vbPath svg rect[data-vt]',x=>x.length===40),'ON: 40-session fear path with hover cells');
    ok(await p.$$eval('.vbsec svg circle',x=>x.length>=6),'ON: VIX curve drawn');
    const edge=await p.$eval('.vbcard',e=>getComputedStyle(e).borderLeftColor); ok(/rgb/.test(edge),'ON: coloured edge '+edge);
    await p.hover('.vbcard'); await p.waitForTimeout(150); const tip=await p.$eval('#vbTip',e=>!e.hidden&&e.textContent); ok(tip&&tip.includes('Russell'),'ON: hover card: '+String(tip).slice(0,60));
    await p.mouse.move(5,5); await p.waitForTimeout(100); ok(await p.$eval('#vbTip',e=>e.hidden),'ON: hover card hides');
    await p.screenshot({path:'vb_desktop.png',fullPage:false,clip:await p.$eval('#vxBoard',e=>{const r=e.getBoundingClientRect(); return {x:0,y:r.top+scrollY,width:1400,height:Math.min(r.height,2400)};})});
    ok(errs.length===0,'ON: 0 JS errors '+errs.slice(0,2));
    // turn it off again from the button
    await p.click('#vxOpts button[data-o="vb"]'); await p.waitForTimeout(400);
    ok(await p.$eval('#vxBoard',e=>e.hidden&&e.innerHTML===''),'button OFF: board hidden and emptied'); await ctx.close(); }
  { const {ctx,p,errs}=await open(b,{vb:true},390);
    const ov=await p.$eval('#vxBoard',e=>[e.scrollWidth,e.clientWidth]); ok(ov[0]<=ov[1]+1,'phone 390px: no sideways overflow '+ov);
    ok(await p.$$eval('#vbPath svg rect[data-vt]',x=>x.length===24),'phone: fear path shows the last 24 sessions');
    const chip=await p.$$eval('.vbchip',x=>x.map(e=>e.textContent)); ok(chip.length&&/^[A-Z][a-z]{2} \d+ '\d\d$/.test(chip[0]),'fire chips read like "Mar 17 \'26": '+chip.join(' | '));
    await p.screenshot({path:'vb_phone.png',fullPage:true}); ok(errs.length===0,'phone: 0 JS errors'); await ctx.close(); }
  { const {ctx,p,errs}=await open(b,{vb:true},1400,null);
    ok(await p.$eval('#vxBoard',e=>/Could not read volx\.json/.test(e.textContent)),'volx.json missing: says so'); ok(errs.length===0,'missing: 0 JS errors'); await ctx.close(); }
  { const j=JSON.parse(fs.readFileSync('volx.json')); const s=j.board.sig; s.state.phase='FIRE'; s.state.fire={d:'2026-11-13',vix:19.3,vvix:115,strength:'strong',units:1};
    s.state.trades=[]; s.headline='FIRE - fear released after the VIX spike, STRONG (VVIX 110+). Buy SVXY at the next open, 1 unit; sell at the close of the 20th session.'; s.state.vvix=115;
    fs.writeFileSync('volx_fire.json',JSON.stringify(j));
    const {ctx,p,errs}=await open(b,{vb:true},1400,'volx_fire.json');
    ok(await p.$eval('.vbsig',e=>e.classList.contains('ph-FIRE')&&/Buy SVXY/.test(e.textContent)&&/strong/.test(e.textContent)),'FIRE state: green card, buy SVXY, strong');
    await p.screenshot({path:'vb_fire.png',clip:await p.$eval('.vbsig',e=>{const r=e.getBoundingClientRect(); return {x:0,y:r.top+scrollY-60,width:1400,height:r.height+70};})});
    { const j2=JSON.parse(fs.readFileSync('volx.json')); j2.board.sig={err:'vol_fade not importable: No module named trading_intel'}; fs.writeFileSync('volx_err.json',JSON.stringify(j2)); }
    ok(errs.length===0,'FIRE: 0 JS errors'); await ctx.close(); }
  { const {ctx,p,errs}=await open(b,{vb:true},1400,'volx_err.json');
    ok(await p.$eval('.vbsig',e=>/Rebuild the image/.test(e.textContent))&&await p.$$eval('.vbcard',x=>x.length===13),'old image: signal says rebuild, cards still drawn'); ok(errs.length===0,'old image: 0 JS errors'); await ctx.close(); }
  await b.close(); console.log(bad?`VOL BOARD TEST FAILED: ${bad}`:'VOL BOARD TEST OK'); process.exit(bad?1:0); })();
