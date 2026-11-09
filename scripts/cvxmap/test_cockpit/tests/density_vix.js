// Density + VIX checks for the 2026-11-08 evening toggles (DN1-DN6, VX1-VX2). Same harness as parity.js: ./srv/ served on :8765
// with prod.html / test.html (tests/instrument.py), the 11/06 data, live_1555.json (live.json with ts 15:55) and test/volx*.json.
// node density_vix.js parity  -> all toggles off: Density runs/odds/text/canvas pixels + every VIX section identical to production
// node density_vix.js shots [volx file] -> all new toggles on: screenshots den_all.png, vx_gauge.png, vx_stack.png + printed readings
const {chromium}=require('playwright'); const fs=require('fs');
const BASE='http://localhost:8765/', SRV='./srv/';
async function open(B,page,tg,liveFile,volx){ const ctx=await B.newContext({viewport:{width:1600,height:1300}}); const p=await ctx.newPage();
  await p.route('**/live.json*',r=>r.fulfill({path:SRV+liveFile,contentType:'application/json'}));
  if(volx) await p.route('**/test/volx.json*',r=>r.fulfill({path:SRV+'test/'+volx,contentType:'application/json'}));
  await p.addInitScript(s=>{ try{ localStorage.setItem('tg_v1',s); localStorage.setItem('cockpit.denOn','1'); }catch(e){} }, JSON.stringify(tg||{}));
  const errs=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error') errs.push('console: '+m.text()); });
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti&&T.st.ti.near_book; },null,{timeout:60000});
  return {p,ctx,errs}; }
async function tab(o,t){ await o.p.evaluate(t=>{ const T=window.__T; T.x(`st.tab="${t}"; document.querySelectorAll("main.tab").forEach(m=>m.hidden=true); document.getElementById("tab${t==="vix"?"Vix":"Density"}").hidden=false; render();`); },t); }
async function denDone(o){ let last=-1, same=0; for(let i=0;i<120;i++){ await o.p.waitForTimeout(2000);
   const n=await o.p.evaluate(()=>window.__T.x('(DEN.busy?-1:Object.keys(DEN.runs).length)')); if(n>=20&&n===last){ if(++same>=3) break; } else same=0; last=n; }
  await o.p.evaluate(()=>window.__T.x('render()')); await o.p.waitForTimeout(800); }
async function grab(o){ return await o.p.evaluate(()=>{ const T=window.__T; return T.x(`(()=>{ const R={}; for(const id of ["denKey","denOddsTxt","denLeg","denRead","denLog"]) R[id]=(document.getElementById(id)||{}).innerHTML;
   R.runs=JSON.stringify(Object.keys(DEN.runs).sort().map(k=>{ const r=DEN.runs[k]; return r===true?k:[k,r.med,r.p10,r.p90,r.pZone,r.odds&&r.odds.b,r.odds&&r.odds.touch]; }));
   R.board=JSON.stringify(DEN.board,(k,v)=>k==="_tgW"?undefined:v); const cv=document.getElementById("denMainC"); R.canvas=cv.toDataURL().length+":"+require_hash(cv.toDataURL());
   const co=document.getElementById("denOddsC"); R.odds=require_hash(co.toDataURL()); return R;
   function require_hash(s){ let h=0; for(let i=0;i<s.length;i++){ h=(h*31+s.charCodeAt(i))|0; } return h; } })()`); }); }
async function grabVix(o){ return await o.p.evaluate(()=>{ const out={}; document.querySelectorAll('#tabVix section').forEach(s=>{ if(s.id) out[s.id]=s.innerHTML; }); return out; }); }
(async()=>{ const B=await chromium.launch(); const mode=process.argv[2]||'parity';
 if(mode==='parity'){
  const A=await open(B,'prod.html',{},'live_1555.json'), Bt=await open(B,'test.html',{},'live_1555.json');
  await Bt.p.evaluate(()=>{ const b=document.getElementById('tgBar'); if(b) b.remove(); document.querySelectorAll('style').forEach(s=>{ if(s.textContent.includes('TEST: the bar is the first row')) s.textContent=s.textContent.replace('.app{grid-template-rows:auto auto auto 1fr}',''); }); }); await tab(A,'density'); await tab(Bt,'density'); await denDone(A); await denDone(Bt);
  const a=await grab(A), b=await grab(Bt); for(const [o,n] of [[A,'cA.png'],[Bt,'cB.png']]) await o.p.locator('#denMainC').screenshot({path:n}); let bad=0; for(const k of Object.keys(a)){ if(a[k]!==b[k]){ bad++; console.log('DENSITY DIFF',k,String(a[k]).length,String(b[k]).length); } }
  console.log('density compared',Object.keys(a).length,'outputs, runs',JSON.parse(a.runs).length);
  await tab(A,'vix'); await tab(Bt,'vix'); await A.p.waitForTimeout(800); await Bt.p.waitForTimeout(800);
  const va=await grabVix(A), vb=await grabVix(Bt); for(const k of Object.keys(va)){ if(va[k]!==vb[k]){ bad++; console.log('VIX DIFF',k); } }
  const extra=Object.keys(vb).filter(k=>!(k in va)); console.log('vix sections compared',Object.keys(va).length,'· test-only (hidden):',extra.join(','));
  console.log('errors prod',A.errs.filter(e=>!/404/.test(e)).slice(0,3),'test',Bt.errs.filter(e=>!/404/.test(e)).slice(0,3)); if(0) console.log('errors prod',A.errs.slice(0,3),'test',Bt.errs.slice(0,3));
  console.log(bad?`PARITY FAILED ${bad}`:'DENSITY+VIX PARITY OK (all toggles off)'); await B.close(); process.exit(bad?1:0); }
 if(mode==='shots'){
  const all={dn1:true,dn2:true,dn3:true,dn5:true,dn6:true,vx1:true,vx2:true};
  const o=await open(B,'test.html',all,'live_1555.json',process.argv[3]||'volx_intra.json');
  await tab(o,'density'); await denDone(o); await o.p.waitForTimeout(2500);
  await o.p.locator('#tabDensity').screenshot({path:'den_all.png'});
  const info=await o.p.evaluate(()=>window.__T.x(`JSON.stringify({walls:tgDenWalls(DEN.board),board:DEN.board&&{t:DEN.board.t,S0:DEN.board.S0,fade:DEN.board.fade,trough:DEN.board.trough,pin:[DEN.board.pinLo,DEN.board.pinHi],flip:DEN.board.fullFlip},touch:(()=>{const ks=Object.keys(DEN.runs).filter(x=>!x.startsWith("x")).map(Number); const r=DEN.runs[Math.max(...ks)]; return r.odds&&{lv:r.odds.touchLv,p:r.odds.touch}; })(),odds:document.getElementById("denOddsTxt").innerText,key:document.getElementById("denKey").innerText})`));
  console.log(info);
  await tab(o,'vix'); await o.p.waitForTimeout(2500);
  await o.p.locator('#vxGauge').screenshot({path:'vx_gauge.png'}); await o.p.locator('#vxStack').screenshot({path:'vx_stack.png'});
  console.log('gauge text:',(await o.p.locator('#vxGauge').innerText()).slice(0,900));
  console.log('stack text:',(await o.p.locator('#vxStack').innerText()).slice(0,700));
  console.log('errors',o.errs.slice(0,5)); await B.close(); }
})();
