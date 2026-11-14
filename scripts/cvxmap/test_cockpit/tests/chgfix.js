// 2026-11-13 read-out fix check (data check: "Map basis OI + right pane Change -> read-out stops").
// Same harness as hedge.js: a folder served on :8765 with prod_old.html (instrument.py of the page BEFORE the fix),
// prod.html (instrument.py of the fixed page) and one session's data files (live.json, flow_YYYYMMDD.ndjson, ti.json, ...).
// Checks: (1) every buildMap number and the read-out with Charm / Vanna on the right are identical old vs new;
// (2) basis OI + Change: the new page renders the read-out with no JS error and its "As time passes" line equals the
// line with Charm selected; (3) basis OI + vol + Change: same charm line as Charm selected (old page read the change map).
// Exit 1 on any failure.   node chgfix.js
const {chromium}=require('playwright');
const BASE=process.env.TC_BASE||'http://localhost:8765/';
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page){ const ctx=await B.newContext({viewport:{width:1600,height:1150}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e)));
  await p.addInitScript(()=>{ try{ localStorage.setItem('cockpit.tab','map'); localStorage.setItem('tab','"map"'); }catch(e){} });
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  await p.waitForTimeout(1500); return {p,ctx,errs}; }
const maps=o=>o.p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX; const keep={smileOn:st.smileOn,basis:st.basis};
  for(const basis of ["oi","oiv"]) for(const book of ["0dte","all"]) for(const kind of ["gamma","charm","vanna"]){
    st.basis=basis; const m=buildMap(kind,null,d,book,null); R[[basis,book,kind].join("|")]=m?{grid:m.grid,feats:m.feats,vmax:m.vmax}:null;
    if(kind==="gamma"&&book==="all"&&m){ R[[basis,"readC"].join("|")]=mapRead(m,buildMap("charm",null,d,book,null),null,null,d); R[[basis,"readV"].join("|")]=mapRead(m,null,buildMap("vanna",null,d,book,null),null,d); } }
  st.basis=keep.basis; return R; });
async function readWith(o,basis,right){ o.errs.length=0;
  const r=await o.p.evaluate(([b,rt])=>{ const T=window.__T; T.st.tab="map"; T.st.basis=b; T.st.mapRight=rt; try{ T.x("render()"); }catch(e){ return {err:String(e)}; }
    const el=document.getElementById("mapRead"); return {html:el?el.innerHTML:null}; },[basis,right]);
  await o.p.waitForTimeout(200); return Object.assign(r,{errs:o.errs.slice()}); }
const timeLine=h=>{ const m=(h||"").match(/As time passes<\/b> → dealers <b>(buy|sell)<\/b> \(about ([\d,]+) deltas per 5 min\)/); return m?m[1]+" "+m[2]:null; };
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME}:{});
  const A=await open(B,'prod_old.html'), N=await open(B,'prod.html');
  { const ma=await maps(A), mn=await maps(N); let same=0,tot=0; for(const k of Object.keys(ma)){ tot++; if(JSON.stringify(ma[k])===JSON.stringify(mn[k])) same++; else console.log('DIFF',k); }
    ok(same===tot,`old vs new: buildMap + read-out (Charm / Vanna on the right) identical ${same}/${tot}`); }
  for(const basis of ["oi","oiv"]){
    const rc=await readWith(N,basis,"charm"), rx=await readWith(N,basis,"chg");
    ok(!rx.err&&!rx.errs.length,`new page, basis ${basis} + Change: no JS error ${rx.err||rx.errs.join(" ")}`);
    ok(!!timeLine(rc.html),`basis ${basis} + Charm: read-out has the charm line (${timeLine(rc.html)})`);
    ok(timeLine(rx.html)===timeLine(rc.html),`basis ${basis} + Change: charm line = the Charm read (${timeLine(rx.html)} vs ${timeLine(rc.html)})`);
    const ox=await readWith(A,basis,"chg");
    console.log(`  (old page, basis ${basis} + Change: ${ox.err||ox.errs[0]||"charm line "+timeLine(ox.html)})`);
  }
  const ph=await readWith(N,"oi","charm"); ok(!ph.errs.length,'back to Charm: no JS error');
  await A.ctx.close(); await N.ctx.close(); await B.close();
  console.log(fails?`CHGFIX FAILED ${fails}/${n}`:`CHGFIX OK ${n}/${n}`); process.exit(fails?1:0); })();
