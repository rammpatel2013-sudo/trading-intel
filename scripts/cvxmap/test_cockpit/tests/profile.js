// Profile check (2026-11-13 night, VS plan step 2), test cockpit Map > Gamma button "Profile".
// Same harness as parity.js / hedge.js: a folder on :8765 with prod.html + test.html (instrument.py) and one session's
// data files plus a profile_SPX.json (vs_profile_feed.py output). Checks (+ the VS step 3 ES hedge table): OFF = no panel, parity of buildMap/read-out
// prod vs test; ON = panel with both lines, flips, percentiles, no JS error; hover card; OFF again hides it; a missing
// file shows a plain message, not an error. Screenshots to OUT. Exit 1 on any failure.   node profile.js
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.OUT||'./shots/'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls,route404){ const ctx=await B.newContext({viewport:{width:1600,height:1300}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); });
  if(route404) await p.route('**/profile_SPX.json*',r=>r.fulfill({status:404,body:'nf'}));
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  await p.evaluate(()=>{ const T=window.__T; T.st.tab="map"; T.x("render()"); }); await p.waitForTimeout(1200); return {p,ctx,errs}; }
const maps=o=>o.p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX;
  for(const book of ["0dte","all"]) for(const kind of ["gamma","charm"]){ const m=buildMap(kind,null,d,book,null); R[book+kind]=m?{grid:m.grid,feats:m.feats}:null; }
  const g=buildMap("gamma",null,d,"all",null); R.read=mapRead(g,buildMap("charm",null,d,"all",null),null,null,d); return R; });
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME}:{});
  const P=await open(B,'prod.html'), T=await open(B,'test.html');
  { const a=await maps(P), b=await maps(T); let s=0,t=0; for(const k in a){ t++; if(JSON.stringify(a[k])===JSON.stringify(b[k])) s++; else console.log('DIFF',k); } ok(s===t,`Profile OFF: prod vs test buildMap + read-out identical ${s}/${t}`); }
  ok(await T.p.evaluate(()=>{ const b=document.getElementById("tgProfBtn"), x=document.getElementById("tgProf"); return !!b&&b.getAttribute("aria-pressed")==="false"&&x.hidden; }),'button present, OFF by default, panel hidden');
  await T.p.click('#tgProfBtn'); await T.p.waitForTimeout(1500);
  const st1=await T.p.evaluate(()=>{ const x=document.getElementById("tgProf"), c=document.getElementById("tgProfC"); return {hidden:x.hidden,head:document.getElementById("tgProfHead").innerText,w:c.width,h:c.height,data:c.toDataURL().length}; });
  ok(!st1.hidden&&st1.w>0&&st1.data>20000,`ON: panel shown, canvas drawn (${st1.w}x${st1.h}, ${st1.data} bytes)`);
  ok(/83rd percentile/.test(st1.head)&&/41st percentile/.test(st1.head),'ON: both percentiles in plain words');
  ok(/flip 7,482/.test(st1.head)&&/flip 7,481/.test(st1.head),'ON: both flips named ('+st1.head.split("\n").slice(1,3).join(" | ")+')');
  ok(/long gamma/.test(st1.head),'ON: long / short at price in words');
  ok(/Signed: long gamma at price · flip 7,536 \(positions from 2026-11-12's trades on the tape, packages/.test(st1.head),'VS step 4: signed line named in words ('+(st1.head.split("\n").find(l=>/Signed/.test(l))||'')+')');
  { const h=await T.p.evaluate(()=>{ const e=document.getElementById("tgProfHedge"); return e?{n:e.querySelectorAll(".pfh").length,t:e.innerText}:null; });
    ok(h&&h.n===6,'VS step 3: hedge table has 6 moves ('+(h?h.n:0)+')');
    ok(h&&/falls 1% to 7,480, dealers buy about 19k ES → they absorb/.test(h.t)&&/rises 1% to 7,631, dealers sell about 50k ES → they absorb/.test(h.t),'VS step 3: plain line says which way and absorb / chase ('+(h?h.t.split("\n").slice(-1)[0]:'')+')'); }
  { const c=await T.p.evaluate(()=>{ const e=document.getElementById("tgProfCharm"), cv=document.getElementById("tgProfCharmC"); return e?{t:e.innerText,n:e.querySelectorAll(".pfck").length,img:cv.toDataURL().length}:null; });
    ok(c&&/Charm by expiry/.test(c.t)&&c.n===9&&c.img>20000,'VS step 5: charm chart + 9 clock readings ('+(c?c.n:0)+')');
    ok(c&&/No red flag/.test(c.t)&&/delta a day to (sell|buy)/.test(c.t),'VS step 5: plain charm line + no red flag on this file');
    const rf=await T.p.evaluate(()=>{ const T=window.__T; const P=T.x("TG_PF.d"); P.charm.red_flag={flag:true,changes:2,minor_share:0.4,band:[7540,7570]}; T.x("tgProfDraw()"); return document.getElementById("tgProfCharm").innerText; });
    ok(/RED FLAG: charm changes sign 2× within ±½ straddle \(7,540–7,570/.test(rf),'VS step 5: red flag shown in words when set'); }
  await T.p.locator('#tgProf').screenshot({path:OUT+'profile_on.png'});
  const box=await T.p.locator('#tgProfC').boundingBox(); await T.p.mouse.move(box.x+box.width*0.3,box.y+box.height*0.5); await T.p.waitForTimeout(300);
  const d2=await T.p.evaluate(()=>document.getElementById("tgProfC").toDataURL().length); ok(d2!==st1.data,'hover card drawn');
  await T.p.locator('#tgProf').screenshot({path:OUT+'profile_hover.png'});
  await T.p.screenshot({path:OUT+'profile_page.png',fullPage:false});
  ok(!T.errs.length,'no JS errors with Profile on '+T.errs.slice(0,2).join(' | '));
  await T.p.click('#tgProfBtn'); await T.p.waitForTimeout(300); ok(await T.p.evaluate(()=>document.getElementById("tgProf").hidden),'OFF again: panel hidden');
  const M=await open(B,'test.html',{'cockpit.tgProf':"1"},true); await M.p.waitForTimeout(800);
  const mh=await M.p.evaluate(()=>document.getElementById("tgProfHead").innerText); ok(/not found/.test(mh)&&!M.errs.length,'missing file: plain message, no error ('+mh.slice(0,80)+')');
  await M.p.setViewportSize({width:390,height:844}); await M.p.evaluate(()=>{ window.__T.x("render()"); }); await M.p.waitForTimeout(500); ok(!M.errs.length,'phone width: no error');
  await B.close(); console.log(fails?`PROFILE FAILED ${fails}/${n}`:`PROFILE OK ${n}/${n}`); process.exit(fails?1:0); })();
