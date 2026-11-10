// Main cockpit checks for the 2026-11-09 move (Orbs tab, Density DN options, VIX VX options + VIX map).
// Same harness as parity.js: a folder served on :8765 with prod.html (tests/instrument.py of cvx_cockpit.html) and the
// data files (live.json, flow_YYYYMMDD.ndjson, ti.json, smilebeta.json, wide_*.json, wide_live_*.json, vixmap_*.json,
// volx.json). Prints one line per check, exits 1 on any failure. Screenshots go to OUT (default ./).
//   node main_options.js            all checks + screenshots
const {chromium}=require('playwright'); const path=require('path');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.TC_OUT||'./';
let fails=0, n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,opts={}){ const ctx=await B.newContext({viewport:{width:1600,height:1150},deviceScaleFactor:opts.dsf||1}); const p=await ctx.newPage();
  const errs=[], req=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); });
  p.on('request',r=>req.push(r.url()));
  if(opts.missing) await p.route(/\/(wide_|vixmap_|volx)[^/]*\.json/,r=>r.fulfill({status:404,body:'missing'}));
  if(opts.live) await p.route('**/live.json*',r=>r.fulfill({path:path.join(process.env.TC_SRV||'./srv/',opts.live),contentType:'application/json'}));
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(opts.ls||{}));
  await p.goto(BASE+'prod.html'); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  return {p,ctx,errs,req}; }
const X=(o,s)=>o.p.evaluate(s=>window.__T.x(s),s);
async function tab(o,t){ await o.p.click(`#pageTabs button[data-tab="${t}"]`); await o.p.waitForTimeout(400); }
async function px(o,id,fx,fy){ return o.p.evaluate(([id,fx,fy])=>{ const c=document.getElementById(id); const g=c.getContext('2d'); const d=g.getImageData(Math.floor(c.width*fx),Math.floor(c.height*fy),1,1).data; return [d[0],d[1],d[2]]; },[id,fx,fy]); }
async function colours(o,id,x0,x1){ return o.p.evaluate(([id,x0,x1])=>{ const c=document.getElementById(id), g=c.getContext('2d'), w=c.width, h=c.height; const d=g.getImageData(0,0,w,h).data; let gr=0,rd=0;
  for(let y=0;y<h;y+=2) for(let x=Math.floor(w*x0);x<Math.floor(w*x1);x+=2){ const i=(y*w+x)*4, r=d[i],G=d[i+1],b=d[i+2]; if(G>180&&r<120&&b>110&&b<170) gr++; if(r>220&&G>95&&G<140&&b>90&&b<140) rd++; } return {gr,rd}; },[id,x0,x1]); }
(async()=>{ const B=await chromium.launch();
 // ---------------- 1. every tab renders, options OFF by default, nothing new fetched
 { const o=await open(B,{});
   const tabs=await o.p.$$eval('#pageTabs button',bs=>bs.map(b=>b.dataset.tab));
   ok(tabs.indexOf('orbs')===tabs.indexOf('flow')+1,'Orbs tab button sits next to Flow');
   for(const t of tabs){ await tab(o,t); const vis=await o.p.evaluate(t=>{ const m=[...document.querySelectorAll('main.tab')].filter(x=>!x.hidden).map(x=>x.id); return m; },t);
     ok(vis.length===1&&vis[0].toLowerCase().includes(t==='voleod'?'voleod':t==='constel'?'constel':t==='premium'?'premium':t==='exposure'?'exposure':t==='strikes'?'strikes':t==='density'?'density':t==='ledger'?'ledger':t),`tab ${t} renders (${vis.join(',')})`); }
   await tab(o,'orbs'); ok(/Off\./.test(await o.p.innerText('#orBody')),'Orbs OFF by default shows the off note');
   await tab(o,'density'); ok((await o.p.$$eval('#denOpts button',bs=>bs.filter(b=>b.getAttribute('aria-pressed')==='true').length))===0&&(await o.p.$$('#denOpts button')).length===5,'Density: 5 option buttons, all OFF');
   await tab(o,'vix'); ok((await o.p.$$eval('#vxOpts button',bs=>bs.map(b=>b.getAttribute('aria-pressed')).join()))==='false,false,false','VIX: 3 option buttons, all OFF');
   ok(await o.p.$eval('#vxMap',e=>e.hidden)&&await o.p.$eval('#vxGauge',e=>e.hidden)&&await o.p.$eval('#vxStack',e=>e.hidden),'VIX: option sections hidden while OFF');
   await o.p.waitForTimeout(3500);
   ok(!o.req.some(u=>/wide_|vixmap_|volx/.test(u)),'OFF: no wide / vixmap / volx file fetched ('+o.req.filter(u=>/wide_|vixmap_|volx/.test(u)).length+')');
   ok(o.errs.length===0,'0 JS errors across all tabs, options OFF '+JSON.stringify(o.errs.slice(0,3)));
   await o.ctx.close(); }
 // ---------------- 2. Orbs
 { const o=await open(B,{dsf:2,ls:{'cockpit.orOn':'1','cockpit.tab':'orbs','cockpit.orProf':'net'}});
   await o.p.waitForFunction(()=>window.__T.x('!!(OR.P&&OR.P.bk.length)'),null,{timeout:30000}); await o.p.waitForTimeout(600);
   const nb=await X(o,'OR.P.bk.length'), nslot=await X(o,'orSym().slots.length');
   ok(nb>300&&nb===nslot,`Orbs: 1-min blocks loaded (${nb} blocks = ${nslot} slots)`);
   ok(await X(o,'Math.abs(orSym().slots[100].c0.reduce((a,b)=>a+b,0)-orSym().slots[99].c0.reduce((a,b)=>a+b,0))<1e6&&orSym().slots[100].c0.every(v=>v>=0)'),'Orbs: day file decoded (cumulative volumes non-negative)');
   const nn=await colours(o,'orProfC',0,0.5), np=await colours(o,'orProfC',0.5,1);
   ok(nn.rd>20&&np.gr>20,`Orbs Net: red bars left of centre (${nn.rd}) and green right (${np.gr})`);
   ok(nn.gr<5&&np.rd<5,`Orbs Net: no green on the left / red on the right (${nn.gr}/${np.rd})`);
   await o.p.locator('#tabOrbs').screenshot({path:OUT+'cockpit_orbs_tab.png'});
   // hover
   const box=await o.p.locator('#orMainC').boundingBox(); await o.p.mouse.move(box.x+box.width*0.92,box.y+box.height*0.5); await o.p.waitForTimeout(200);
   const tip=await o.p.$eval('#orTip',e=>e.hidden?'':e.innerText); ok(/gamma/.test(tip)&&/calls/.test(tip),'Orbs hover shows a tooltip: '+tip.replace(/\n/g,' | ').slice(0,90));
   const pb=await o.p.locator('#orProfC').boundingBox(); await o.p.mouse.move(pb.x+pb.width*0.5,pb.y+pb.height*0.45); await o.p.waitForTimeout(200);
   ok(/bull/.test(await o.p.$eval('#orTip',e=>e.innerText)),'Volume profile hover shows bull / bear / net');
   // slider
   await o.p.$eval('#orSlider',e=>{ e.value='120'; e.dispatchEvent(new Event('input')); }); await o.p.waitForTimeout(200);
   ok(await X(o,'OR.i===120&&OR.follow===false')&&/11:/.test(await o.p.innerText('#orT')),'Orbs slider moves the time ('+await o.p.innerText('#orT')+')');
   await o.p.click('#orNow'); ok(await X(o,'OR.i===OR.P.bk.length-1'),'Now returns to the latest block');
   // modes
   for(const [v,lab] of [['cp','Calls/Puts'],['bb','Bull/Bear'],['net','Net']]){ /* 2026-11-09 follow-ups: 3 modes */ await o.p.click(`#orProfSeg button[data-v="${v}"]`); await o.p.waitForTimeout(150);
     ok(await o.p.$$eval('#orProfSeg button[aria-pressed=true]',bs=>bs.map(b=>b.dataset.v).join())===v,`Volume mode ${lab}: single-select`); }
   await o.p.click('#orProfSeg button[data-v="bb"]'); await o.p.waitForTimeout(200);
   const bl=await colours(o,'orProfC',0,0.5), br=await colours(o,'orProfC',0.5,1); ok(br.gr>20&&bl.rd>20&&br.rd<5&&bl.gr<5,`Bull/Bear: bull green right (${br.gr}), bear red left (${bl.rd})`);
   await o.p.locator('#tabOrbs').screenshot({path:OUT+'cockpit_orbs_bullbear.png'});
   const pocBull=await X(o,'(OR.prof="bb",orProfile(OR.P.bk[OR.i].s).poc)'), pocNet=await X(o,'(OR.prof="net",orProfile(OR.P.bk[OR.i].s).poc)'), maxNet=await X(o,'(()=>{ OR.prof="net"; const p=orProfile(OR.P.bk[OR.i].s); let b=0; p.net.forEach((v,i)=>{ if(Math.abs(v)>Math.abs(p.net[b])) b=i; }); return p.k[b]; })()');
   ok(pocNet===maxNet,`Net POC = strike with the biggest |net| (${pocNet}); Bull POC ${pocBull}`);
   for(const v of ['oi','vol','oiv','ois']){ await o.p.click(`#orBasisSeg button[data-v="${v}"]`); await o.p.waitForTimeout(150); ok(await o.p.$$eval('#orBasisSeg button[aria-pressed=true]',bs=>bs.length)===1&&await X(o,'OR.basis')===v,`Basis ${v}: one at a time`); }
   ok(await o.p.$$eval('#orBasisSeg button',bs=>bs.map(b=>b.dataset.v+'='+b.textContent).join())==='oi=OI,vol=Vol,oiv=OI + vol,ois=OI + signed','Basis buttons = the MAP\'s set OI | Vol | OI + vol | OI + signed');
   await o.p.click('#orBlkSeg button[data-v="5"]'); await o.p.waitForTimeout(300);
   const n5=await X(o,'OR.P.bk.length'); ok(n5>=Math.floor(nb/5)&&n5<=Math.ceil(nb/5)+1,`5-min blocks: ${n5} from ${nb} minutes`);
   ok(await X(o,'(()=>{ const b=OR.P.bk[10]; return b.s===orSym().slots[b.j]&&b.cd.h>=b.cd.l&&tmin(b.s.t)===b.t0+4; })()'),'5-min block = its last minute; candle OHLC over the block');
   await o.p.click('#orBlkSeg button[data-v="3"]'); await o.p.waitForTimeout(200); ok(await X(o,'OR.blk===3'),'3-min blocks');
   // symbol follows the cockpit buttons
   await o.p.click('#symTabs button[data-sym="QQQ"]'); await o.p.waitForTimeout(400); ok(await X(o,'OR.P.sym==="QQQ"&&OR.P.step===1'),'Orbs follows the QQQ button');
   await o.p.click('#symTabs button[data-sym="SPX"]'); await o.p.waitForTimeout(300);
   ok(o.errs.length===0,'Orbs: 0 JS errors '+JSON.stringify(o.errs.slice(0,3)));
   await o.ctx.close(); }
 // ---------------- 2b. Orbs live merge: truncated day + newest minute from the live file
 { const o=await open(B,{ls:{'cockpit.orOn':'1','cockpit.tab':'orbs'}});
   await o.p.route(/wide_20261106\.json/,r=>r.fulfill({path:path.join(process.env.TC_SRV||'./srv/','wide_trunc_20261106.json'),contentType:'application/json'}));
   await o.p.route(/wide_live_SPX\.json/,r=>r.fulfill({path:path.join(process.env.TC_SRV||'./srv/','wide_live_trunc_SPX.json'),contentType:'application/json'}));
   const nReq0=o.req.length; await o.p.reload(); await o.p.waitForFunction(()=>window.__T&&window.__T.x('!!(typeof OR!=="undefined"&&OR.P&&OR.P.bk.length)'),null,{timeout:30000});
   const n0=await X(o,'OR.P.bk.length'), t0=await X(o,'OR.P.bk[OR.P.bk.length-1].s.t'); await o.p.waitForTimeout(4000);
   const n1=await X(o,'OR.P.bk.length'), t1=await X(o,'OR.P.bk[OR.P.bk.length-1].s.t');
   ok(n1===n0+1&&t1>t0,`live file appends the newest minute (${t0} → ${t1}, ${n0} → ${n1} blocks) without reloading the day`);
   const rq=o.req.slice(nReq0); ok(rq.filter(u=>/wide_live_SPX/.test(u)).length>=1&&rq.filter(u=>/wide_20261106/.test(u)).length===1,`day file fetched once, live file polled (${rq.filter(u=>/wide_live_SPX/.test(u)).length} polls)`);
   ok(o.errs.length===0,'live merge: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 3. Density options
 { const o=await open(B,{dsf:2,live:'live_1555.json',ls:{'cockpit.tab':'density'}});
   await o.p.waitForTimeout(800);
   for(const k of ['dn1','dn2','dn3','dn5','dn6']){ await o.p.click(`#denOpts button[data-o="${k}"]`); await o.p.waitForTimeout(250); ok(await X(o,`st.dno.${k}===true`),`Density option ${k} ON`); }
   ok(JSON.parse(await o.p.evaluate(()=>localStorage.getItem('cockpit.dnOpt'))).dn3===true,'Density options remembered');
   // let the cone run
   let runs=0; for(let i=0;i<60;i++){ await o.p.waitForTimeout(2000); runs=await X(o,'Object.keys(DEN.runs).filter(x=>!x.startsWith("x")).length'); if(runs>=60&&!(await X(o,'!!DEN.busy'))) break; }
   await X(o,'render()'); await o.p.waitForTimeout(1500);
   const odds=await o.p.innerText('#denOddsTxt'), key=await o.p.innerText('#denKey');
   ok(/Odds extras P25 [\d,]+ · P75 [\d,]+/.test(odds),'DN3 odds extras shown (buckets need a pin band: '+(/Buckets/.test(odds)?'shown':'no pin in this data')+')');
   ok(/Support \+ walls/.test(odds),'DN1 touch odds shown');
   ok(/Replay summary/i.test(key)&&/Status extras/i.test(key)&&/round-number support/.test(key),'DN5 + DN6 lines in Key readings');
   ok(o.req.some(u=>/volx\.json/.test(u)),'DN6 fetches volx.json');
   await o.p.locator('#tabDensity').screenshot({path:OUT+'cockpit_density_options.png'});
   for(const k of ['dn1','dn2','dn3','dn5','dn6']) await o.p.click(`#denOpts button[data-o="${k}"]`); await o.p.waitForTimeout(600); await X(o,'render()'); await o.p.waitForTimeout(400);
   ok(!/Odds extras|Replay summary|Status extras/i.test((await o.p.innerText('#denOddsTxt'))+(await o.p.innerText('#denKey'))),'Density options OFF again: extras gone');
   ok(o.errs.length===0,'Density: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 4. VIX options + VIX map
 { const o=await open(B,{dsf:2,ls:{'cockpit.tab':'vix'}}); await o.p.waitForTimeout(600);
   await o.p.click('#vxOpts button[data-o="vx1"]'); await o.p.click('#vxOpts button[data-o="vx2"]'); await o.p.waitForTimeout(2500);
   const g=await o.p.innerText('#vxGauge'), s=await o.p.innerText('#vxStack');
   ok(/Regime gauge/.test(g)&&/Untested predictor/.test(g)&&!/Doublewide|Cboe|TEST/.test(g),'VX1 gauge renders, says untested, no source names');
   ok(/Variance stack/.test(s)&&/Transmission share/.test(s)&&!/Cboe|TEST/.test(s),'VX2 variance stack renders');
   const r1=await o.p.locator('#vxOpts').boundingBox(), r2=await o.p.locator('#vxStack').boundingBox();
   await o.p.screenshot({path:OUT+'cockpit_vix_options.png',clip:{x:r1.x-4,y:r1.y-4,width:Math.max(r1.width,r2.width)+8,height:r2.y+r2.height-r1.y+8},fullPage:true});
   await o.p.click('#vxOpts button[data-o="vx1"]'); await o.p.click('#vxOpts button[data-o="vx2"]');
   await o.p.click('#vxOpts button[data-o="vmap"]'); await o.p.waitForFunction(()=>!!document.getElementById('vxmGC'),null,{timeout:15000}); await o.p.waitForTimeout(500);
   ok(await o.p.$eval('#vxGauge',e=>e.hidden)&&!(await o.p.$eval('#vxMap',e=>e.hidden)),'VX1 off hides it; VIX map shown');
   const read=await o.p.innerText('#vxmRead'); ok(/Gamma flips sign at/.test(read)&&/Biggest call open interest/.test(read)&&/front forward/.test(read),'VIX map read-out: flip, biggest OI strikes, front forward');
   const a=await px(o,'vxmGC',0.35,0.5); await o.p.click('#vxmSignSeg button[data-v="naive"]'); await o.p.waitForTimeout(400); const b=await px(o,'vxmGC',0.35,0.5);
   ok(JSON.stringify(a)!==JSON.stringify(b)&&((a[2]>a[0])!==(b[2]>b[0])),`sign switch flips the colour at a cell (${a} → ${b})`);
   ok(/SPX-style/.test(await o.p.innerText('#vxmRead')),'naive sign named in the read-out');
   await o.p.click('#vxmSignSeg button[data-v="dealer"]'); await o.p.waitForTimeout(300);
   await o.p.click('#vxmBookSeg button[data-v="a"]'); await o.p.waitForTimeout(300); ok(/all \d+ expiries/.test(await o.p.innerText('#vxmRead')),'All expiries combined');
   await o.p.click('#vxmBookSeg button[data-v="f"]'); await o.p.click('#vxmBasisSeg button[data-v="vol"]'); await o.p.waitForTimeout(300); ok(/today's volume/.test(await o.p.innerText('#vxmRead')),'basis: today\'s volume');
   await o.p.click('#vxmBasisSeg button[data-v="oi"]'); await o.p.waitForTimeout(300);
   const mb=await o.p.locator('#vxmGC').boundingBox(); await o.p.mouse.move(mb.x+mb.width*0.4,mb.y+mb.height*0.45); await o.p.waitForTimeout(200);
   ok(/dealer gamma/.test(await o.p.innerText('#vxmHov')),'VIX map hover: '+(await o.p.innerText('#vxmHov')).slice(0,100));
   await o.p.locator('#vxMap').screenshot({path:OUT+'cockpit_vix_map.png'});
   ok(o.errs.length===0,'VIX: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 5. every new option ON with its data files missing
 { const o=await open(B,{missing:true,ls:{'cockpit.orOn':'1','cockpit.dnOpt':'{"dn1":true,"dn2":true,"dn3":true,"dn5":true,"dn6":true}','cockpit.vxOpt':'{"vx1":true,"vx2":true,"vmap":true}'}});
   await tab(o,'orbs'); await o.p.waitForTimeout(2500); ok(/No Orbs data yet/.test(await o.p.innerText('#orBody')),'Orbs with no files: plain message');
   await tab(o,'density'); await o.p.waitForTimeout(3000); await tab(o,'vix'); await o.p.waitForTimeout(3000);
   ok(/Could not read volx\.json/.test(await o.p.innerText('#vxGauge')),'VX1 with no volx.json: plain message');
   ok(/Waiting for vixmap_\d{8}\.json/.test(await o.p.innerText('#vxMap')),'VIX map with no file: says which file it is waiting for');
   for(const t of ['flow','map','levels','tenor','risk','help']) await tab(o,t);
   ok(o.errs.length===0,'missing data files: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 6. phone width
 { const ctx=await B.newContext({viewport:{width:400,height:900}}); const p=await ctx.newPage(); const errs=[]; p.on('pageerror',e=>errs.push(String(e)));
   await p.addInitScript(()=>{ localStorage.setItem('cockpit.orOn','1'); localStorage.setItem('cockpit.tab','orbs'); });
   await p.goto(BASE+'prod.html'); await p.waitForTimeout(5000);
   const sw=await p.evaluate(()=>document.documentElement.scrollWidth); ok(errs.length===0&&await p.evaluate(()=>window.__T.st.tab)==='orbs','phone: Orbs tab allowed, 0 errors (scrollWidth '+sw+')'); await ctx.close(); }
 await B.close(); console.log(fails?`FAILED ${fails}/${n}`:`ALL OK ${n}/${n}`); process.exit(fails?1:0); })();
