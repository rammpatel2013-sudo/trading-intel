// MAIN cockpit map fixes (2026-11-21, Mithil Q2/Q3/Q5/Q6). Three served folders (11/19 13:17 SPX):
//   A = feed without iv_end (old 16:15 clock), B = feed with iv_end 960 (new 16:00 clock), C = B with 4 broken quote sides
//   and one strike with no IV. Each has prod.html (instrumented), live.json, ti.json, flow logs, iv0_expect.json
//   (iv0_expect.py), wk_expect.json (week_expect.py), bq_expect.json (book_expect.py).
// Checks: 0DTE IV per strike = independent Python (quote filter, clock re-expression, gap fill); Week book IVs, forward
// basis and live Week IVs = Python; Week gamma at spot = brute-force sum at the forward;
// Book clean provenance counts = Python; Data check + Close pin buttons off by default, on -> frame / line / read-out;
// 0 JS errors. Exit 1 on any fail.
const {chromium}=require('playwright'); const fs=require('fs');
const A=process.env.MF_A||'http://localhost:8766/', Bu=process.env.MF_B||'http://localhost:8768/', C=process.env.MF_C||'http://localhost:8769/';
const DA=process.env.MF_DA||'/tmp/wsrv', DB=process.env.MF_DB||'/tmp/wsrv2', DC=process.env.MF_DC||'/tmp/wsrv3';
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,url,ls){ const ctx=await B.newContext({viewport:{width:1600,height:1200}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource|404/.test(m.text())) errs.push('console: '+m.text()); });
  await p.clock.setFixedTime(new Date('2026-11-19T13:18:00-05:00'));
  await p.addInitScript(s=>{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(url+'prod.html'); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.ti&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100; },null,{timeout:90000});
  await p.waitForTimeout(2000); return {p,errs}; }
const iv0=o=>o.p.evaluate(()=>{ const T=window.__T, x=T.x, d=T.st.live.SPX; const rows=x('mapRowsAt')(null,x('series')('SPX'),d); const S=x('MF_STATS');
  return {feedEnd:x('FEED_END'), rej:S.rej, fill:S.fill, sg:Object.fromEntries(rows.map(r=>[String(r.k),r.sg]))}; });
function cmpIV(tag,P,E){ let mx=0,nn=0; for(const k in E.sg){ nn++; mx=Math.max(mx,Math.abs((P.sg[k]??-1)-E.sg[k])); }
  ok(P.feedEnd===E.iv_end,`${tag}: feed IV clock read from live.json = ${P.feedEnd} (expect ${E.iv_end})`);
  ok(nn>30&&mx<1e-12,`${tag}: 0DTE IV on ${nn} strikes = independent Python (max diff ${mx.toExponential(2)})`);
  ok(P.rej===E.rej&&P.fill===E.fill,`${tag}: quote sides dropped ${P.rej} (expect ${E.rej}), strikes filled ${P.fill} (expect ${E.fill})`); }
(async()=>{ const B=await chromium.launch();
  // --- 0DTE IV
  for(const [tag,url,dir] of [['A old 16:15 feed',A,DA],['B 16:00 feed',Bu,DB],['C bad quotes',C,DC]]){
    const O=await open(B,url); cmpIV(tag,await iv0(O),JSON.parse(fs.readFileSync(dir+'/iv0_expect.json','utf8'))); ok(!O.errs.length,`${tag}: 0 JS errors `+JSON.stringify(O.errs.slice(0,2))); }
  // --- Week IV + forward + Book clean (folder B)
  const E=JSON.parse(fs.readFileSync(DB+'/wk_expect.json','utf8')), Q=JSON.parse(fs.readFileSync(DB+'/bq_expect.json','utf8'));
  const M=await open(B,Bu);
  const P=await M.p.evaluate(()=>{ const T=window.__T, st=T.st, x=T.x, nb=st.ti.near_book.SPX, S=st.live.SPX.spot, bas={};
    for(const [k,v] of x('mfBasis')()) bas[k]=v.b;
    const bl={}; for(const r of nb.rows) bl[r.e+"|"+(r.set||"PM")+"|"+r.k]=x('mfWkSg08')(r,S,nb);
    const R=x('mfBqBuild')(nb,S), bq={}; for(const r of nb.rows) bq[r.e+"|"+(r.set||"PM")+"|"+r.k]=x('mfBqSg')(r,S,nb);
    const rows=x('nearRows')(), fin={}, fb={}; for(const r of rows){ const k=r.e+"|"+r.set+"|"+r.k; fin[k]=r.sg; fb[k]=r.fb; }
    const day=x('wkDayISO')(), now=x('mfLiveMin')(), W=x('greekColWeek'), g=W("gamma",rows,S,now,day);
    const fd=[-40,0,40].map(dS=>{ const p=S+dS,h=0.01,D=q=>W("delta",rows,q,now,day); return [W("gamma",rows,p,now,day),(D(p+h)-D(p-h))/(2*h)*2.5]; });
    return {S,bas,bl,bq,stats:R.stats,fin,fb,g,fd,now,rows:rows.map(r=>[r.k,r.sg,r.cp,r.pp,r.exp_t,r.fb])}; });
  const key=k=>k.replace(/\|(\d+)\.0$/,'|$1');
  let mb=0; for(const k in E.basis) mb=Math.max(mb,Math.abs((P.bas[k]??1e9)-E.basis[k])); ok(Object.keys(P.bas).length===Object.keys(E.basis).length&&mb<1e-6,'Week: forward basis per expiry = Python (max diff '+mb.toExponential(2)+')');
  let ml=0,nl=0; for(const k in E.blend){ nl++; ml=Math.max(ml,Math.abs((P.bl[key(k)]??1e9)-E.blend[k])); } ok(nl>500&&ml<1e-6,`Week: 08:35 delta-weighted IV = Python on ${nl} contracts (max diff ${ml.toExponential(2)})`);
  let mf=0,nf=0; for(const k in E.final_live){ if(!(key(k) in P.fin)) continue; nf++; mf=Math.max(mf,Math.abs(P.fin[key(k)]-E.final_live[k])); } ok(nf>250&&mf<1e-9,`Week: live Week IV after 09:35 = Python on ${nf} contracts (max diff ${mf.toExponential(2)})`);
  let mfb=0; for(const k in E.fb){ if(!(key(k) in P.fb)) continue; mfb=Math.max(mfb,Math.abs(P.fb[key(k)]-E.fb[k])); } ok(mfb<1e-6,'Week: every row carries its forward gap');
  ok(["observed","conflict","paired","interpolated","unavailable","rows"].every(k=>P.stats[k]===Q.counts[k]),'Book clean: provenance counts = Python '+JSON.stringify(P.stats));
  let mq=0; for(const k in Q.sg) mq=Math.max(mq,Math.abs((P.bq[k]||0)-(Q.sg[k]||0))); ok(mq<1e-12,'Book clean: every cleaned IV = Python (max diff '+mq+')');
  const PHI=x=>Math.exp(-x*x/2)/Math.sqrt(2*Math.PI); let g=0; const nowI=Date.UTC(2026,10,19,0,0)+(P.now+300)*60000;
  for(const [k,sg,cp,pp,et,fb] of P.rows){ const pos=cp-pp; if(!pos||!(sg>0)) continue; const xi=Date.parse(et.length>16?et:et+":00-05:00"); const m=(xi-nowI)/60000; if(!(m>0)) continue;
    const ty=m/525600, sq=Math.sqrt(ty), F=P.S+(fb||0), d1=(Math.log(F/k)+0.5*sg*sg*ty)/(sg*sq); g+=pos*PHI(d1)/(F*sg*sq)*2.5; }
  ok(Math.abs(g-P.g)<=1e-9*Math.abs(g)+1e-9,`Week gamma at spot = independent sum at the forward (${P.g.toFixed(3)} vs ${g.toFixed(3)})`);
  const read=await M.p.$eval('#mapRead',e=>e.innerHTML);
  ok(/<b>Week IV:<\/b> \d+ contracts on live Week IV/.test(read),'Read-out: Week IV line');
  ok(!/DATA CHECK|Data check:/.test(read)&&!/Close pin \(15:50/.test(read),'Data check + Close pin off by default');
  ok(await M.p.$eval('#dataCheckBtn',b=>b.getAttribute('aria-pressed'))==='false'&&await M.p.$eval('#closePinBtn',b=>b.getAttribute('aria-pressed'))==='false','Buttons present, off');
  await M.p.click('#dataCheckBtn'); await M.p.click('#closePinBtn'); await M.p.waitForTimeout(800);
  const r2=await M.p.$eval('#mapRead',e=>e.innerHTML), pin=await M.p.evaluate(()=>window.__T.x('mfPinCalc')());
  ok(/Data check:|DATA CHECK/.test(r2),'Data check on: read-out line');
  ok(/Close pin \(15:50 profile\):/.test(r2)&&pin&&["pin","nopin","edge","none"].includes(pin.kind),'Close pin on: read-out line + rule result '+JSON.stringify(pin));
  ok(await M.p.evaluate(()=>{ const ks=Object.keys(localStorage); return ks.some(k=>k.endsWith('mapDataCheck')&&localStorage.getItem(k)==='1')&&ks.some(k=>k.endsWith('mapClosePin')&&localStorage.getItem(k)==='1'); }),'Buttons remembered');
  ok(!M.errs.length,'Week/buttons: 0 JS errors '+JSON.stringify(M.errs.slice(0,2)));
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
