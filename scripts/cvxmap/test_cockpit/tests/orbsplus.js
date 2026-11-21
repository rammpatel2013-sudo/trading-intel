// Orbs+ checks (2026-11-21). Folder served on TC_BASE with test_old.html (instrument.py of the test page BEFORE this
// ship), test.html (the new one), one session's live.json + flow_YYYYMMDD.ndjson + ti.json, wide_YYYYMMDD.json,
// wide_index.json, whales_live.json and op_expect.json (orbsplus_expect.py). Checks: OFF = Orbs canvases + Map numbers
// identical to the old page, strip hidden, five buttons present; ON = each button draws, the strip's charm / gamma of
// the last minute equal the independent Python numbers, the flip line = the Map's own OI + vol flip line, map numbers
// unchanged and the user's basis restored, 0 JS errors. Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8767/', OUT=process.env.OUT||'./shots/', OLD=process.env.TC_OLD||'test_old.html', NEW=process.env.TC_NEW||'test.html'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls){ const ctx=await B.newContext({viewport:{width:1700,height:1500}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e.stack||e).split('\n').slice(0,3).join(' | '))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource|404/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'orbs','cockpit.orOn':'1','cockpit.basis':'oi'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&document.getElementById('orMainC'); },null,{timeout:90000});
  await p.waitForTimeout(2500); return {p,ctx,errs}; }
const grab=o=>o.p.evaluate(()=>{ const {st,buildMap}=window.__T, d=st.live.SPX, R={}; for(const book of ["0dte","all"]) for(const kind of ["gamma","charm"]){ const m=buildMap(kind,null,d,book,null); R[book+kind]=m?{g:m.grid,v:m.vmax}:null; } return JSON.stringify(R); });
const canv=o=>o.p.evaluate(()=>['orMainC','orLadC','orProfC'].map(id=>{ const c=document.getElementById(id); return c?c.toDataURL():null; }).join('|'));
const click=async(o,k)=>{ await o.p.click(`.tgopb[data-op="${k}"]`); await o.p.waitForTimeout(900); };
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  const E=JSON.parse(fs.readFileSync(process.env.OP_EXPECT||'op_expect.json','utf8'));
  const A=await open(B,OLD), N=await open(B,NEW);
  ok(await grab(A)===await grab(N),'OFF: buildMap identical to the old test page');
  ok(await canv(A)===await canv(N),'OFF: Orbs canvases pixel-identical');
  ok(await A.p.$eval('#orRead',e=>e.innerHTML)===await N.p.$eval('#orRead',e=>e.innerHTML),'OFF: Orbs read-out identical');
  ok((await N.p.$$(".tgopb")).length===5,'OFF: five TEST buttons on the Orbs header');
  ok(await N.p.$eval('#tgOp',e=>e.hidden)===true,'OFF: strip hidden');
  ok(!A.errs.length&&!N.errs.length,'OFF: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  const c0=await canv(N);
  await click(N,'ridge'); const c1=await canv(N); ok(c1!==c0,'Ridge: main canvas redrawn');
  await N.p.screenshot({path:OUT+'op_ridge.png'}); await click(N,'ridge');
  ok(await canv(N)===c0,'Ridge off: back to the plain Orbs');
  await click(N,'flip'); const F=await N.p.evaluate(()=>{ const T=window.__T; const pts=T.x('TG_OP.flip');
    const st=T.st, b0=st.basis; st.basis='oiv'; const m=T.buildMap('gamma',T.x('series(st.sym)'),st.live.SPX,'all'); st.basis=b0; const ZL=T.x('zoneLines')(m); const ref=[];
    ZL.forEach((o,j)=>{ if(o&&m.times[j]<=m.now) ref.push([m.times[j],m.prices[0]+o.z*m.dp]); }); return {pts,ref,basis:st.basis}; });
  ok(F.pts&&F.pts.length>50,'Flip: line has '+(F.pts?F.pts.length:0)+' points');
  ok(JSON.stringify(F.pts)===JSON.stringify(F.ref),'Flip: = the Map\'s own OI + vol flip line (buildMap + zoneLines)');
  ok(F.basis==='oi','Flip: the user\'s Map basis restored (oi)');
  ok(await grab(N)===await grab(A),'Flip: map numbers unchanged');
  await click(N,'walls'); await click(N,'notes');
  const notes=await N.p.evaluate(()=>{ const T=window.__T; return T.x('tgOpNotes(OR.P,tgOpVol(OR.P),TG_OP.flip)').map(x=>x.s); });
  console.log('notes', JSON.stringify(notes)); ok(notes.length>=1&&/biggest block/.test(notes[0]+notes.join()),'Notes: biggest block found');
  ok(notes.some(s=>s.startsWith('biggest block: '+E.big_k+' ')),'Notes: biggest block strike = independent Python ('+E.big_k+')');
  await click(N,'strip'); await N.p.waitForTimeout(1500);
  ok(await N.p.$eval('#tgOp',e=>!e.hidden),'Strip: shown');
  const S=await N.p.evaluate(()=>window.__T.x('TG_OP.last'));
  const rel=(a,b)=>Math.abs(a-b)<=1e-3*Math.max(1,Math.abs(b));
  console.log('page  ', JSON.stringify({es:S&&S.es,g:S&&S.g})); console.log('python', JSON.stringify(E));
  ok(S&&rel(S.es,E.es_last),'Strip: charm ES/h (last minute before 15:50) = independent Python');
  ok(S&&rel(S.g,E.g_last),'Strip: gamma at spot (OI + signed) = independent Python');
  ok(S&&S.bars.some(b=>b.ou!=null),'Strip: outside-trader bars from whales_live.json');
  const head=await N.p.$eval('#tgOpHead',e=>e.textContent); console.log('head', head.slice(0,300)); ok(/charm now: dealers (BUY|SELL)/.test(head),'Strip: read-out line');
  await N.p.evaluate(()=>{ document.getElementById('orMainC').scrollIntoView(); }); await N.p.waitForTimeout(300); await N.p.screenshot({path:OUT+'op_all.png',fullPage:false});
  ok(await grab(N)===await grab(A),'ALL ON: map numbers unchanged');
  ok(!N.errs.length,'ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  for(const k of ['flip','walls','notes','strip']) await click(N,k);
  ok(await canv(N)===c0&&await N.p.$eval('#tgOp',e=>e.hidden),'ALL OFF again: Orbs pixel-identical, strip hidden');
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
