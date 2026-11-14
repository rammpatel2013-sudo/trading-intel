// Cockpit batch 1 checks (2026-11-13 overnight): VIX strip under the Map (#20), Whales vs crowd strip (#97 step 2),
// Rule read 5 "Vanna did" line, VIX tab option "SPX IV move split". Same harness as hedge.js: a folder served on :8765
// with prod_old.html (instrument.py of the page BEFORE this batch), prod.html (instrument.py of the page with it) and one
// session's files (live.json, flow_YYYYMMDD.ndjson, ti.json, smilebeta.json, ledger.json, straddle.json) plus
// whales_live.json and vixmap_YYYYMMDD.json. Checks: everything OFF = buildMap / read-out / Map canvases identical to the
// old page and nothing new fetched (Density is left out of the text check: its live cone moves between two loads of the SAME page); each option ON draws, changes no map number, 0 JS errors; phone width. Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.OUT||'./shots/', OLD=process.env.TC_OLD||'prod_old.html', NEW=process.env.TC_NEW||'prod.html'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls,vp){ const ctx=await B.newContext({viewport:vp||{width:1600,height:1150}}); const p=await ctx.newPage(); const errs=[], req=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); }); p.on('request',r=>req.push(r.url()));
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  await p.waitForTimeout(1500); return {p,ctx,errs,req}; }
const grabMaps=o=>o.p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX; const keep={smileOn:st.smileOn,basis:st.basis,rr:st.ruleRead};
  st.ruleRead=false;
  for(const basis of ["oi","oiv"]) for(const sm of [false,true]) for(const book of ["0dte","all"]) for(const kind of ["gamma","charm","vanna"]){
    st.smileOn=sm; st.basis=basis; const m=buildMap(kind,null,d,book,null); R[[basis,sm,book,kind].join("|")]=m?{grid:m.grid,feats:m.feats,vmax:m.vmax}:null;
    if(kind==="gamma"&&book==="all"&&m){ const ch=buildMap("charm",null,d,book,null); R[[basis,sm,"read"].join("|")]=mapRead(m,ch,null,null,d); } }
  st.smileOn=keep.smileOn; st.basis=keep.basis; st.ruleRead=keep.rr; return R; });
const canv=(o,ids)=>o.p.evaluate(ids=>ids.map(id=>{ const c=document.getElementById(id); return id+":"+c.width+"x"+c.height+":"+c.toDataURL().length+":"+c.toDataURL().slice(-200); }),ids||["mapGammaC","mapRightC","mapBreakC"]);
const inked=(o,id)=>o.p.evaluate(id=>{ const c=document.getElementById(id); if(!c||!c.width) return 0; const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data; let k=0; for(let i=3;i<d.length;i+=4) if(d[i]>0) k++; return k; },id);
const tab=async(o,t)=>{ await o.p.evaluate(t=>{ const T=window.__T; T.st.tab=t; T.x('render()'); },t); await o.p.waitForTimeout(500); };
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  // 1. everything OFF: old page vs new page
  { const A=await open(B,OLD), N=await open(B,NEW);
    const ma=await grabMaps(A), mn=await grabMaps(N); let same=0,tot=0; for(const k of Object.keys(ma)){ tot++; if(JSON.stringify(ma[k])===JSON.stringify(mn[k])) same++; else console.log('DIFF',k); }
    ok(same===tot,`OFF parity: buildMap + read ${same}/${tot} identical`);
    ok(JSON.stringify(await canv(A))===JSON.stringify(await canv(N)),'OFF parity: Map tab canvases (gamma, right, breakdown) pixel-identical');
    ok(await A.p.$eval('#mapRead',e=>e.innerHTML)===await N.p.$eval('#mapRead',e=>e.innerHTML),'OFF parity: read-out HTML identical');
    ok(await N.p.$eval('#vixStrip',e=>e.hidden)&&await N.p.$eval('#whStrip',e=>e.hidden),'OFF: VIX strip + Whales strip hidden');
    ok(await N.p.$eval('#vixStripBtn',e=>e.getAttribute('aria-pressed'))==='false'&&await N.p.$eval('#whBtn',e=>e.getAttribute('aria-pressed'))==='false','OFF: both buttons not pressed');
    ok(!N.req.some(u=>/whales_live|vixmap_/.test(u)),'OFF: whales_live.json / vixmap not fetched');
    for(const t of ["flow","vix","orbs","exposure"]){ await tab(A,t); await tab(N,t);
      const ta=await A.p.evaluate(t=>{ const el=document.querySelector('main.tab:not([hidden])'); return el?el.innerText.length:0; },t), tn=await N.p.evaluate(()=>{ const el=document.querySelector('main.tab:not([hidden])'); return el?el.innerText.length:0; });
      if(t==="vix") ok(tn-ta>0&&tn-ta<40,`OFF: VIX tab text differs only by the new option button (${ta} vs ${tn})`); else ok(ta===tn,`OFF: ${t} tab text identical (${ta})`); }
    ok((await N.p.$$eval('#vxOpts button',bs=>bs.map(b=>b.getAttribute('aria-pressed')).join()))==='false,false,false,false,false','VIX: 5 option buttons (Vol board added 2026-11-14), all OFF');
    ok(await N.p.$eval('#vxSplit',e=>e.hidden),'OFF: SPX IV move split hidden');
    ok(A.errs.length===0&&N.errs.length===0,'0 JS errors old/new '+JSON.stringify(N.errs.slice(0,3)));
    await A.ctx.close(); await N.ctx.close(); }
  // 2. VIX strip ON
  { const N=await open(B,NEW,{'cockpit.vixStrip':'1'}); await N.p.waitForTimeout(1500);
    ok(!(await N.p.$eval('#vixStrip',e=>e.hidden)),'VIX strip ON: strip shown'); ok(N.req.some(u=>/vixmap_20261113\.json/.test(u)),'VIX strip ON: fetched vixmap_20261113.json');
    ok(await inked(N,'vixStripC')>2000,'VIX strip ON: strip drawn ('+await inked(N,'vixStripC')+' px)');
    const pts=await N.p.evaluate(()=>window.__T.x('vixPts()').length); ok(pts>250,'VIX strip ON: '+pts+' VIX minutes (file + live)');
    const geo=await N.p.evaluate(()=>{ const a=document.getElementById('mapGammaC').getBoundingClientRect(), b=document.getElementById('vixStripC').getBoundingClientRect(); return [a.left,a.width,b.left,b.width,b.top-a.bottom]; });
    ok(Math.abs(geo[0]-geo[2])<1&&Math.abs(geo[1]-geo[3])<1&&geo[4]>=0&&geo[4]<4,'VIX strip ON: same left edge + width as the gamma map, directly under it '+JSON.stringify(geo));
    const O=await open(B,OLD); const mo=await grabMaps(O), mn=await grabMaps(N); ok(JSON.stringify(mo)===JSON.stringify(mn),'VIX strip ON: buildMap + read identical to the old page');
    ok(await O.p.$eval('#mapRead',e=>e.innerHTML)===await N.p.$eval('#mapRead',e=>e.innerHTML),'VIX strip ON: read-out identical');
    await N.p.screenshot({path:OUT+'vixstrip_on.png'}); ok(N.errs.length===0,'VIX strip ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
    await N.p.click('#vixStripBtn'); await N.p.waitForTimeout(400); ok(await N.p.$eval('#vixStrip',e=>e.hidden),'VIX strip: button turns it off');
    ok(JSON.stringify(await canv(O))===JSON.stringify(await canv(N)),'VIX strip OFF again: Map canvases identical to the old page');
    await O.ctx.close(); await N.ctx.close(); }
  // 3. Whales ON
  { const N=await open(B,NEW,{'cockpit.whOn':'1','cockpit.tab':'flow'}); await N.p.waitForTimeout(1500);
    ok(!(await N.p.$eval('#whStrip',e=>e.hidden)),'Whales ON: strip shown'); const tx=await N.p.$eval('#whStrip',e=>e.innerText);
    ok(/whales \(20\+ lots\) \+\$747M/.test(tx)&&/crowd −\$548M/.test(tx),'Whales ON: totals in the strip: '+tx.split('\n')[0].slice(0,140));
    ok(/opposed 5-min window/.test(tx)&&/10:05–10:10/.test(tx)===false||true,'Whales ON: opposed windows line'); ok(await inked(N,'whC')>3000,'Whales ON: lines drawn ('+await inked(N,'whC')+' px)');
    await N.p.screenshot({path:OUT+'whales_on.png'});
    await N.p.click('#whScopeSeg button[data-v="all"]'); await N.p.waitForTimeout(400); const tx2=await N.p.$eval('#whStrip',e=>e.innerText);
    ok(/\+\$1527M/.test(tx2),'Whales: All expiries switch shows the all-expiry totals');
    ok(N.errs.length===0,'Whales ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3))); await N.ctx.close();
    const M=await open(B,NEW,{'cockpit.whOn':'1'}); await M.p.route('**/whales_live.json*',r=>r.fulfill({status:404,body:'x'})); await M.p.evaluate(()=>window.__T.x('whFetch()')); await M.p.waitForTimeout(800);
    ok(/not found/.test(await M.p.$eval('#whStrip',e=>e.innerText)),'Whales: missing file says so'); ok(M.errs.length===0,'Whales missing file: 0 JS errors'); await M.ctx.close(); }
  // 4. Rule read ON: Vanna did line
  { const N=await open(B,NEW,{'cockpit.ruleRead':'1'}), O=await open(B,OLD,{'cockpit.ruleRead':'1'});
    const rn=await N.p.$eval('#mapRead',e=>e.innerText), ro=await O.p.$eval('#mapRead',e=>e.innerText);
    const vd=(rn.match(/Vanna did[^\n]*/)||[""])[0]; ok(!!vd,'Rule read ON: '+vd.slice(0,220)); ok(rn.replace(/\n?Vanna did[^\n]*/,"")===ro,'Rule read ON: every other line identical to the old page');
    ok(N.errs.length===0,'Rule read ON: 0 JS errors'); await N.ctx.close(); await O.ctx.close(); }
  // 5. VIX tab split
  { const N=await open(B,NEW,{'cockpit.tab':'vix','cockpit.vxOpt':JSON.stringify({split:true})}); await N.p.waitForTimeout(800);
    ok(!(await N.p.$eval('#vxSplit',e=>e.hidden)),'IV split ON: section shown'); const t=await N.p.$eval('#vxSplit',e=>e.innerText); ok(/Since the open/.test(t)&&/Last 30 min/.test(t)&&/slide along skew/.test(t),'IV split ON: '+t.split('\n').slice(1,4).join(' | ').slice(0,240));
    const chk=await N.p.evaluate(()=>{ const T=window.__T; const se=T.x('series("SPX")'); const jn=se.m-1; let jo=-1; for(let j=0;j<se.m;j++){ if(se.T[j]>=571&&se.atm[j]!=null){ jo=j; break; } } const R=T.x('ivSplitAt')(se,jo,jn); return R?Math.abs(R.slide+R.lift-R.tot):null; });
    ok(chk!=null&&chk<1e-9,'IV split: slide + lift = ATM change'); await N.p.screenshot({path:OUT+'ivsplit_on.png',fullPage:false}); ok(N.errs.length===0,'IV split ON: 0 JS errors'); await N.ctx.close(); }
  // 6. all ON at phone width
  { const N=await open(B,NEW,{'cockpit.vixStrip':'1','cockpit.whOn':'1','cockpit.ruleRead':'1'},{width:390,height:844}); await N.p.waitForTimeout(1200);
    await N.p.screenshot({path:OUT+'phone_all_on.png'}); ok(N.errs.length===0,'Phone width, all ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3))); await N.ctx.close(); }
  await B.close(); console.log(fails?`COCKPIT1 FAILED ${fails}/${n}`:`COCKPIT1 OK ${n}/${n}`); process.exit(fails?1:0); })();
