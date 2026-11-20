// WK Week IV + forward, AB Data check, P3 Close pin, grouped bar (2026-11-20). Served folder (TC_BASE) with test_old.html
// (page before this ship), test.html (new), one session's live.json + flow + ti.json and wk_expect.json (week_expect.py).
// (tolerance 1e-6 on basis-derived values: the page inverts the normal with Acklam, |err| < 1.2e-9 -> ~2e-8 pts of basis)
// OFF = identical to the old page; WK ON = basis, blended IVs and live IVs equal the independent Python, 0DTE map unchanged,
// Week gamma at spot = Python at the forward; P3 + AB render and record; groups drive the flags; 0 JS errors. Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8766/', OLD=process.env.TC_OLD||'test_old.html', NEW=process.env.TC_NEW||'test.html';
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls,clock){ const ctx=await B.newContext({viewport:{width:1600,height:1200}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource|404/.test(m.text())) errs.push('console: '+m.text()); });
  if(clock) await p.clock.setFixedTime(new Date(clock));
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.ti&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100; },null,{timeout:90000});
  await p.waitForTimeout(2000); return {p,ctx,errs}; }
const grab=(o,book)=>o.p.evaluate(b=>{ const {st,buildMap}=window.__T, d=st.live.SPX, m=buildMap("gamma",null,d,b,null); return JSON.stringify(m?{g:m.grid,f:m.feats}:null); },book);
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  const E=JSON.parse(fs.readFileSync(process.env.WK_EXPECT||'wk_expect.json','utf8'));
  const A=await open(B,OLD), N=await open(B,NEW);
  for(const bk of ["0dte","week","all"]) ok(await grab(A,bk)===await grab(N,bk),'OFF: gamma map identical to the old page, book '+bk);
  ok(await A.p.$eval('#mapRead',e=>e.innerHTML)===await N.p.$eval('#mapRead',e=>e.innerHTML),'OFF: read-out identical');
  ok(!A.errs.length&&!N.errs.length,'OFF: 0 JS errors '+JSON.stringify(N.errs.concat(A.errs).slice(0,3)));
  const M=await open(B,NEW,{tg_v1:JSON.stringify({wk:true})});
  const P=await M.p.evaluate(()=>{ const T=window.__T, st=T.st, x=T.x, nb=st.ti.near_book.SPX, S=st.live.SPX.spot, bas={};
    for(const [k,v] of x('tgWkBasis')()) bas[k]=v.b;
    const bl={}; for(const r of nb.rows) bl[r.e+"|"+(r.set||"PM")+"|"+r.k]=x('tgWkSg08')(r,S);
    const rows=x('nearRows')(), fin={}, fb={}; for(const r of rows){ const k=r.e+"|"+r.set+"|"+r.k; fin[k]=r.sg; fb[k]=r.fb; }
    const day=x('wkDayISO')(), now=x('tgLiveMin')(); const g=x('greekColWeek')("gamma",rows,S,now,day);
    return {S,bas,bl,fin,fb,g,now,day,rows:rows.map(r=>[r.k,r.sg,r.cp,r.pp,r.exp_t,r.set,r.e,r.fb])}; });
  let mb=0; for(const k in E.basis) mb=Math.max(mb,Math.abs((P.bas[k]??1e9)-E.basis[k])); ok(Object.keys(P.bas).length===Object.keys(E.basis).length&&mb<1e-6,'WK: forward basis per expiry = Python (max diff '+mb.toExponential(2)+')');
  const key=k=>k.replace(/\|(\d+)\.0$/,'|$1');
  let ml=0,nl=0; for(const k in E.blend){ nl++; ml=Math.max(ml,Math.abs((P.bl[key(k)]??1e9)-E.blend[k])); } ok(nl>500&&ml<1e-6,`WK: 08:35 delta-weighted IV = Python on ${nl} contracts (max diff ${ml.toExponential(2)})`);
  let mf=0,nf=0; for(const k in E.final_live){ if(!(key(k) in P.fin)) continue; nf++; mf=Math.max(mf,Math.abs(P.fin[key(k)]-E.final_live[k])); } ok(nf>250&&mf<1e-9,`WK: live Week IV after 09:35 = Python on ${nf} contracts (max diff ${mf.toExponential(2)})`);
  let mfb=0; for(const k in E.fb){ if(!(key(k) in P.fb)) continue; mfb=Math.max(mfb,Math.abs(P.fb[key(k)]-E.fb[k])); } ok(mfb<1e-6,'WK: every Week row carries its forward basis');
  // independent gamma at the forward from the page's own final rows
  const PHI=x=>Math.exp(-x*x/2)/Math.sqrt(2*Math.PI); const S=P.S; let g=0;
  const nowI=Date.UTC(2026,10,19,0,0)/1+(P.now+300)*60000;
  for(const [k,sg,cp,pp,et,set,e,fb] of P.rows){ const pos=cp-pp; if(!pos||!(sg>0)) continue; const xi=Date.parse(et.length>16?et:et+":00-05:00"); const m=(xi-nowI)/60000; if(!(m>0)) continue;
    const ty=m/525600, sq=Math.sqrt(ty), F=S+(fb||0), d1=(Math.log(F/k)+0.5*sg*sg*ty)/(sg*sq); g+=pos*PHI(d1)/(F*sg*sq)*2.5; }
  ok(Math.abs(g-P.g)<=1e-9*Math.abs(g)+1e-9,`WK: Week gamma at spot = independent sum at the forward (${P.g.toFixed(3)} vs ${g.toFixed(3)})`);
  const fd=await M.p.evaluate(()=>{ const T=window.__T,x=T.x,S=T.st.live.SPX.spot,rows=x('nearRows')(),day=x('wkDayISO')(),now=x('tgLiveMin')(),W=x('greekColWeek'),out=[];
    for(const dS of [-40,0,40]){ const P=S+dS, h=0.01, D=p=>W("delta",rows,p,now,day); out.push([W("gamma",rows,P,now,day),(D(P+h)-D(P-h))/(2*h)*2.5]); } return out; });
  ok(fd.every(([g,f])=>Math.abs(g-f)<=1e-5*Math.max(1,Math.abs(g))),'WK step 2: Week gamma = finite difference of Week delta at the forward (3 prices) '+JSON.stringify(fd.map(([g,f])=>[+g.toFixed(4),+f.toFixed(4)])));
  ok(await grab(M,"0dte")===await grab(A,"0dte"),'WK: 0DTE map unchanged');
  ok(await grab(M,"week")!==await grab(A,"week"),'WK: Week map changed');
  ok(/TEST Week IV \(WK v1\):/.test(await M.p.$eval('#mapRead',e=>e.innerHTML)),'WK: read-out line');
  ok(!M.errs.length,'WK: 0 JS errors '+JSON.stringify(M.errs.slice(0,3)));
  // P3 + AB, clock fixed 1 min after the feed (fresh) and 30 min after (stale)
  const Q=await open(B,NEW,{tg_v1:JSON.stringify({pin:true,ab:true})},'2026-11-19T13:18:00-05:00');
  const q=await Q.p.evaluate(()=>{ const T=window.__T, x=T.x; return {pin:x('tgPinCalc')(), warn:x('tgWarn')(), ro:x('tgReadoutCfg')({},false), read:document.getElementById('mapRead').innerHTML}; });
  console.log('pin',JSON.stringify(q.pin),'warn',JSON.stringify(q.warn),'readout close',JSON.stringify(q.ro&&q.ro.close&&q.ro.close.pin));
  ok(q.pin&&["pin","nopin","edge","none"].includes(q.pin.kind),'P3: close pin computed ('+(q.pin&&q.pin.kind)+' '+(q.pin&&q.pin.lvl)+')');
  ok(q.ro&&q.ro.close&&q.ro.close.pin&&"kind" in q.ro.close.pin,'P3: read-out records close.pin for scoring');
  ok(/TEST Close pin \(cp-v0/.test(q.read),'P3: read-out line');
  ok(Array.isArray(q.warn)&&!q.warn.some(w=>/min old/.test(w))&&q.warn.some(w=>/call\/put IV/.test(w)),'AB: fresh feed not flagged; 08:35 call/put gap flagged (WK off)');
  ok(/DATA CHECK/.test(q.read),'AB: warning is the first read-out line');
  ok(!Q.errs.length,'P3+AB: 0 JS errors '+JSON.stringify(Q.errs.slice(0,3)));
  const R=await open(B,NEW,{tg_v1:JSON.stringify({ab:true,wk:true})},'2026-11-19T13:47:00-05:00');
  const w2=await R.p.evaluate(()=>window.__T.x('tgWarn')()); console.log('warn stale',JSON.stringify(w2));
  ok(w2.some(w=>/min old/.test(w))&&!w2.some(w=>/call\/put IV/.test(w)),'AB: stale feed flagged; gap warning gone with WK on');
  // grouped bar
  await R.p.click('button[data-grp="3"]'); const tg=await R.p.evaluate(()=>window.__T.x('TG'));
  ok(tg.sg===true&&tg.e2===true,'Groups: "Smile path" sets SG + E2');
  await R.p.click('button[data-grp="3"]'); const tg2=await R.p.evaluate(()=>window.__T.x('TG')); ok(tg2.sg===false&&tg2.e2===false,'Groups: click again clears them');
  ok(await R.p.$$eval('button[data-grp]',b=>b.length)===8,'Groups: 8 grouped switches');
  ok(!R.errs.length,'groups: 0 JS errors '+JSON.stringify(R.errs.slice(0,3)));
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
