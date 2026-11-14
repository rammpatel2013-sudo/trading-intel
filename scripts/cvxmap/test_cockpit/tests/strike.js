// Short strikes marker sm-v0 checks (2026-11-13 night). Same harness as cockpit1.js: a folder served on :8765 with
// test_old.html (instrument.py of the test page BEFORE this ship), test.html (instrument.py of the new test page) and one
// session's files (live.json, flow_YYYYMMDD.ndjson, ti.json, smilebeta.json, ledger.json, straddle.json).
// Checks: button OFF = buildMap / read-out / Flow + Map canvases identical to the old test page; ON = the marker draws on
// the Flow map and the Map gamma pane, no map number changes, 0 JS errors; the page's marker equals rail_score.marks()
// (written by the caller to marks_py.json for the same live.json minute). Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.OUT||'./shots/', OLD=process.env.TC_OLD||'test_old.html', NEW=process.env.TC_NEW||'test.html'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls,tab){ const ctx=await B.newContext({viewport:{width:1600,height:1150}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':tab||'map'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  await p.waitForTimeout(1500); return {p,ctx,errs}; }
const grabMaps=o=>o.p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX; const keep={smileOn:st.smileOn,basis:st.basis};
  for(const basis of ["oi","oiv"]) for(const sm of [false,true]) for(const book of ["0dte","all"]) for(const kind of ["gamma","charm","vanna"]){
    st.smileOn=sm; st.basis=basis; const m=buildMap(kind,null,d,book,null); R[[basis,sm,book,kind].join("|")]=m?{grid:m.grid,feats:m.feats,vmax:m.vmax}:null;
    if(kind==="gamma"&&book==="all"&&m){ const ch=buildMap("charm",null,d,book,null); R[[basis,sm,"read"].join("|")]=mapRead(m,ch,null,null,d); } }
  st.smileOn=keep.smileOn; st.basis=keep.basis; return R; });
const canv=(o,ids)=>o.p.evaluate(ids=>ids.map(id=>{ const c=document.getElementById(id); return c?id+":"+c.width+"x"+c.height+":"+c.toDataURL().length+":"+c.toDataURL().slice(-200):id+":none"; }),ids);
const tab=async(o,t)=>{ await o.p.evaluate(t=>{ const T=window.__T; T.st.tab=t; T.x('render()'); },t); await o.p.waitForTimeout(700); };
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  const A=await open(B,OLD), N=await open(B,NEW);
  { const ma=await grabMaps(A), mn=await grabMaps(N); let same=0,tot=0; for(const k of Object.keys(ma)){ tot++; if(JSON.stringify(ma[k])===JSON.stringify(mn[k])) same++; else console.log('DIFF',k); }
    ok(same===tot,`OFF: buildMap + read ${same}/${tot} identical to the old test page`);
    ok(JSON.stringify(await canv(A,["mapGammaC","mapRightC","mapBreakC"]))===JSON.stringify(await canv(N,["mapGammaC","mapRightC","mapBreakC"])),'OFF: Map tab canvases pixel-identical');
    ok(await A.p.$eval('#mapRead',e=>e.innerHTML)===await N.p.$eval('#mapRead',e=>e.innerHTML),'OFF: read-out HTML identical');
    await tab(A,'flow'); await tab(N,'flow');
    // (Flow canvas pixels are not compared: two loads of the SAME page differ in canvas height by a few px - layout timing)
    ok(await N.p.evaluate(()=>{ const c=document.createElement('canvas'); c.width=300; c.height=300; const x=c.getContext('2d');
      window.__T.x('tgSmDraw')(x,{Y:p=>150-(p-window.__T.st.live.SPX.spot),x0:0,x1:300,yMin:0,yMax:300},"all");
      const d=x.getImageData(0,0,300,300).data; for(let i=3;i<d.length;i+=4) if(d[i]) return false; return true; }),'OFF: the marker draws nothing');
    ok(await A.p.$eval('#mapLegend',e=>e.innerHTML)===await N.p.$eval('#mapLegend',e=>e.innerHTML),'OFF: Flow legend identical');
    ok(!A.errs.length&&!N.errs.length,'OFF: 0 JS errors '+JSON.stringify(N.errs.slice(0,3))); }
  const M=await open(B,NEW,{'cockpit.tgSmOn':'1'});
  { const mk=await M.p.evaluate(()=>JSON.stringify(window.__T.x('tgSmCalc()')));
    fs.writeFileSync(OUT+'marks_js.json',mk); console.log('marker', mk);
    ok(mk&&mk!=='null','ON: marker computed from the live chain');
    const mo=await grabMaps(M), mn=await grabMaps(N); ok(JSON.stringify(mo)===JSON.stringify(mn),'ON: no map number changes (buildMap + read identical to OFF)');
    ok(JSON.stringify(await canv(M,["mapGammaC"]))!==JSON.stringify(await canv(N,["mapGammaC"])),'ON: Map gamma pane draws the marker');
    ok(await M.p.$eval('#tgSmBtn2',e=>e.getAttribute('aria-pressed'))==='true','ON: Map button shows pressed');
    await M.p.screenshot({path:OUT+'sm_map.png'});
    await tab(M,'flow'); await tab(N,'flow');
    ok(await M.p.evaluate(()=>{ const c=document.createElement('canvas'); c.width=300; c.height=300; const x=c.getContext('2d');
      window.__T.x('tgSmDraw')(x,{Y:p=>150-(p-window.__T.st.live.SPX.spot),x0:0,x1:300,yMin:0,yMax:300},"all");
      const d=x.getImageData(0,0,300,300).data; let k=0; for(let i=3;i<d.length;i+=4) if(d[i]) k++; return k>500; }),'ON: the marker draws (Flow map uses the same tgSmDraw)');
    ok((await M.p.$eval('#mapLegend',e=>e.innerHTML)).includes('TEST Short strikes'),'ON: Flow legend line');
    await M.p.screenshot({path:OUT+'sm_flow.png'});
    await M.p.click('#tgSmBtn'); await M.p.waitForTimeout(600);
    ok(await M.p.evaluate(()=>window.__T.st.smOn===false&&localStorage.getItem('cockpit.tgSmOn')==='0'),'Flow button turns it off and remembers it');
    ok(!(await M.p.$eval('#mapLegend',e=>e.innerHTML)).includes('TEST Short strikes'),'OFF again: legend line gone');
    ok(!M.errs.length,'ON: 0 JS errors '+JSON.stringify(M.errs.slice(0,3)));
    if(fs.existsSync(OUT+'marks_py.json')){ const py=JSON.parse(fs.readFileSync(OUT+'marks_py.json','utf8')), js=JSON.parse(mk);
      const same=s=>{ const a=py[s], b=js[s]; if(!a&&!b) return true; if(!a||!b) return false; return a.short===b.short&&JSON.stringify(a.zone||null)===JSON.stringify(b.zone||null)&&Math.abs(a.rail-b.rail)<0.01; };
      ok(same('call')&&same('put'),'page marker = rail_score.marks() (calls '+JSON.stringify(js.call)+' / puts '+JSON.stringify(js.put)+')'); } }
  await B.close(); console.log(fails?`STRIKE CHECKS FAILED: ${fails}/${n}`:`STRIKE CHECKS OK: ${n}/${n}`); process.exit(fails?1:0); })();
