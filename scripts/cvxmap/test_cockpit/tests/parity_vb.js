// Parity for the Vol board ship (2026-11-14): with everything OFF the new pages compute and draw exactly what the old ones did.
// Pairs: production old vs new, test old vs new, production new vs test new. 11/13 session files, clock fixed at 11/13 17:30 ET.
const {chromium}=require('playwright'); const BASE='http://localhost:8765/'; let fails=0; const ok=(c,m)=>{ if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,tab){ const ctx=await B.newContext({viewport:{width:1600,height:1150}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); await p.clock.setFixedTime(new Date('2026-11-13T17:30:00-05:00'));
  await p.addInitScript(t=>{ try{ localStorage.setItem('cockpit.tab',t); }catch(e){} }, tab||'map');
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  await p.waitForTimeout(2000); return {p,ctx,errs}; }
const grab=o=>o.p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX; const keep={smileOn:st.smileOn,basis:st.basis};
  for(const basis of ["oi","oiv","ois"]) for(const sm of [false,true]) for(const book of ["0dte","week","all"]) for(const kind of ["gamma","charm","vanna"]){
    st.smileOn=sm; st.basis=basis; const m=buildMap(kind,null,d,book,null); R[[basis,sm,book,kind].join("|")]=m?{grid:m.grid,cgrid:m.cgrid,feats:m.feats,vmax:m.vmax,times:m.times,prices:m.prices}:null;
    if(kind==="gamma"&&book==="all"&&m){ const ch=buildMap("charm",null,d,book,null), va=buildMap("vanna",null,d,book,null); R[[basis,sm,"read"].join("|")]=mapRead(m,ch,null,null,d)+"||"+mapRead(m,null,va,null,d); } }
  st.smileOn=keep.smileOn; st.basis=keep.basis; return R; });
const canv=o=>o.p.evaluate(()=>["mapGammaC","mapRightC","mapBreakC"].map(id=>{ const c=document.getElementById(id); return c?id+":"+c.width+"x"+c.height+":"+c.toDataURL():id+":none"; }));
const vixText=o=>o.p.evaluate(()=>{ const t=document.getElementById('tabVix'); return t?t.innerText.replace(/\d{1,2}:\d\d(:\d\d)?/g,"t"):""; });
async function pair(B,a,b,label,withVix,canvases=true){
  const A=await open(B,a), N=await open(B,b); const ma=await grab(A), mn=await grab(N); let same=0,tot=0;
  for(const k of Object.keys(ma)){ tot++; if(JSON.stringify(ma[k])===JSON.stringify(mn[k])) same++; else console.log('  DIFF',k); }
  ok(same===tot&&tot>0,`${label}: buildMap + read ${same}/${tot} identical`);
  if(canvases) ok(JSON.stringify(await canv(A))===JSON.stringify(await canv(N)),`${label}: Map canvases pixel-identical`);
  ok(await A.p.$eval('#mapRead',e=>e.innerHTML)===await N.p.$eval('#mapRead',e=>e.innerHTML),`${label}: read-out HTML identical`);
  ok(!A.errs.length&&!N.errs.length,`${label}: 0 JS errors ${A.errs.concat(N.errs).slice(0,2)}`);
  await A.ctx.close(); await N.ctx.close();
  if(withVix){ const AV=await open(B,a,'vix'), NV=await open(B,b,'vix'); const ta=await vixText(AV), tn=await vixText(NV);
    const drop=t=>t.split('\n').filter(l=>l!=='Vol board').join('\n'); ok(drop(ta)===drop(tn)&&tn.split('\n').includes('Vol board'),`${label}: VIX tab text identical with Vol board off (only the new button) [${ta.length}/${tn.length} chars]`);
    await AV.ctx.close(); await NV.ctx.close(); } }
(async()=>{ const B=await chromium.launch();
  await pair(B,'prod_old.html','prod.html','production old vs new',true);
  await pair(B,'test_old.html','test.html','test page old vs new',true);
  await pair(B,'prod.html','test.html','production new vs test new (all toggles off; canvases differ in size by the test rows, as before)',false,false);
  await B.close(); console.log(fails?`PARITY FAILED: ${fails}`:'PARITY OK'); process.exit(fails?1:0); })();
