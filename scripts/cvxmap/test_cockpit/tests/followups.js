// Main cockpit checks for the 2026-11-09 follow-ups: Orbs zoom / 3 profile modes / Charm / 4 bases, Flow bars off by
// default, Volume last-N-min windows, VIX map (bases, range, per-minute line, waiting message), Tenor live, Levels zoom,
// the top-bar SPX <-> SPY calculator. Same harness as main_options.js: a folder served on :8765 (TC_BASE) with prod.html
// (tests/instrument.py of cvx_cockpit.html) and the data files (+ tenor.json, confluence.json, live_1109.json = live.json
// with its date moved to 2026-11-09). Prints one line per check, exits 1 on any failure. Screenshots go to TC_OUT.
const {chromium}=require('playwright'); const path=require('path'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.TC_OUT||'./', SRV=process.env.TC_SRV||'./srv/';
let fails=0, n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,opts={}){ const ctx=await B.newContext({viewport:opts.vp||{width:1600,height:1150},deviceScaleFactor:opts.dsf||1}); const p=await ctx.newPage();
  const errs=[], req=[]; p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); });
  p.on('request',r=>req.push(r.url()));
  if(opts.missing) await p.route(/\/(wide_|vixmap_|volx|tenor|confluence)[^/]*\.json/,r=>r.fulfill({status:404,body:'missing'}));
  if(opts.live) await p.route('**/live.json*',r=>r.fulfill({path:path.join(SRV,opts.live),contentType:'application/json'}));
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(opts.ls||{}));
  await p.goto(BASE+'prod.html');
  if(opts.light) await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX; },null,{timeout:60000});   // no minute log for that day
  else await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  return {p,ctx,errs,req}; }
const X=(o,s)=>o.p.evaluate(s=>window.__T.x(s),s);
async function tab(o,t){ await o.p.click(`#pageTabs button[data-tab="${t}"]`); await o.p.waitForTimeout(400); }
// count pixels of a colour family in a horizontal band of a canvas (fx0..fx1 of its width)
async function count(o,id,x0,x1,kind){ return o.p.evaluate(([id,x0,x1,kind])=>{ const c=document.getElementById(id), g=c.getContext('2d'), w=c.width, h=c.height, d=g.getImageData(0,0,w,h).data; let k=0;
  for(let y=0;y<h;y+=2) for(let x=Math.floor(w*x0);x<Math.floor(w*x1);x+=2){ const i=(y*w+x)*4, r=d[i],G=d[i+1],b=d[i+2];
    if(kind==='green'&&G>180&&r<120&&b>110&&b<170) k++; else if(kind==='red'&&r>220&&G>95&&G<140&&b>90&&b<140) k++;
    else if(kind==='call'&&r<110&&G>160&&b>200) k++; else if(kind==='put'&&r>230&&G<130&&b>130) k++; } return k; },[id,x0,x1,kind]); }
const near=(a,b,t)=>Math.abs(a-b)<=t;
(async()=>{ const B=await chromium.launch();
 // ---------------- 4. Flow bars OFF on a fresh profile; a saved ON is kept
 { const o=await open(B,{});
   ok(await o.p.$eval('#fbBtn',b=>b.getAttribute('aria-pressed'))==='false'&&await X(o,'st.fbOn===false'),'Flow bars: OFF on a fresh profile (button + state)');
   await o.p.click('#fbBtn'); await o.p.waitForTimeout(200); ok(await o.p.evaluate(()=>localStorage.getItem('cockpit.fbOn'))==='1','Flow bars: turning it on is saved');
   ok(o.errs.length===0,'fresh profile: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close();
   const o2=await open(B,{ls:{'cockpit.fbOn':'1'}}); ok(await o2.p.$eval('#fbBtn',b=>b.getAttribute('aria-pressed'))==='true'&&await X(o2,'st.fbOn===true'),'Flow bars: a saved ON choice is kept'); await o2.ctx.close(); }
 // ---------------- 1-3. Orbs: zoom, profile modes, charm, bases
 { const o=await open(B,{dsf:2,ls:{'cockpit.orOn':'1','cockpit.tab':'orbs','cockpit.orProf':'bull'}});
   await o.p.waitForFunction(()=>window.__T.x('!!(OR.P&&OR.P.bk.length)'),null,{timeout:30000}); await o.p.waitForTimeout(500);
   ok(await X(o,'OR.prof')==='bb','an old saved profile mode (bull) maps to Bull/Bear');
   ok(await o.p.$$eval('#orProfSeg button',bs=>bs.map(b=>b.textContent).join('|'))==='Calls/Puts|Bull/Bear|Net','Volume modes = Calls/Puts | Bull/Bear | Net');
   ok(await o.p.$$eval('#orMeasSeg button',bs=>bs.map(b=>b.textContent).join('|'))==='Gamma|Vanna|Charm|Both','Show = Gamma | Vanna | Charm | Both');
   ok(await o.p.$$eval('#orZoomSeg button[aria-pressed=true]',bs=>bs.map(b=>b.dataset.v).join())==='2','price zoom defaults to ±2%');
   const v2=await X(o,'(()=>{ const v=orView(), x=OR.P.bk[OR.i].s.x; return [v.lo/x,v.hi/x]; })()'); ok(near(v2[0],0.98,1e-9)&&near(v2[1],1.02,1e-9),'±2% = 2% either side of the current price');
   await o.p.click('#orZoomSeg button[data-v="1"]'); await o.p.waitForTimeout(250);
   const v1=await X(o,'(()=>{ const v=orView(), x=OR.P.bk[OR.i].s.x; return [v.lo/x,v.hi/x]; })()'); ok(near(v1[0],0.99,1e-9)&&near(v1[1],1.01,1e-9)&&await o.p.evaluate(()=>localStorage.getItem('cockpit.orZoom'))==='1','±1% button (and remembered)');
   await o.p.click('#orZoomSeg button[data-v="all"]'); await o.p.waitForTimeout(250);
   ok(await X(o,'(()=>{ const v=orView(); return v.lo<=OR.P.lo&&v.hi>=OR.P.hi; })()'),'All = every stored strike');
   await o.p.click('#orZoomSeg button[data-v="1"]'); await o.p.waitForTimeout(250);
   // wheel zoom on the main chart, then on the volume profile: all three panes share OR.view
   const mb=await o.p.locator('#orMainC').boundingBox(); await o.p.mouse.move(mb.x+mb.width*0.6,mb.y+mb.height*0.5);
   const s0=await X(o,'orView().hi-orView().lo'); await o.p.mouse.wheel(0,-300); await o.p.waitForTimeout(250); const s1=await X(o,'orView().hi-orView().lo');
   ok(s1<s0*0.95&&await X(o,'!!OR.view'),`wheel on the orbs zooms in (${s0.toFixed(1)} → ${s1.toFixed(1)} points)`);
   const pb=await o.p.locator('#orProfC').boundingBox(); await o.p.mouse.move(pb.x+pb.width*0.5,pb.y+pb.height*0.5); await o.p.mouse.wheel(0,300); await o.p.waitForTimeout(250);
   const s2=await X(o,'orView().hi-orView().lo'); ok(s2>s1*1.05,`wheel on the volume profile zooms out (${s1.toFixed(1)} → ${s2.toFixed(1)})`);
   const lb=await o.p.locator('#orLadC').boundingBox(); const c0=await X(o,'(orView().lo+orView().hi)/2');
   await o.p.mouse.move(lb.x+lb.width*0.5,lb.y+lb.height*0.4); await o.p.mouse.down(); await o.p.mouse.move(lb.x+lb.width*0.5,lb.y+lb.height*0.4+120,{steps:6}); await o.p.mouse.up(); await o.p.waitForTimeout(250);
   const c1=await X(o,'(orView().lo+orView().hi)/2'); ok(c1>c0+1,`drag down on the ladder pans up in price (${c0.toFixed(1)} → ${c1.toFixed(1)})`);
   const iBefore=await X(o,'OR.i'); await o.p.mouse.move(mb.x+mb.width*0.3,mb.y+mb.height*0.3); await o.p.mouse.down(); await o.p.mouse.move(mb.x+mb.width*0.3,mb.y+mb.height*0.3-80,{steps:5}); await o.p.mouse.up(); await o.p.waitForTimeout(250);
   ok(await X(o,'OR.i')===iBefore,'a drag on the orbs pans and does not jump the time');
   await o.p.mouse.dblclick(mb.x+mb.width*0.5,mb.y+mb.height*0.5); await o.p.waitForTimeout(250); ok(await X(o,'OR.view===null'),'double-click resets to the button range');
   await o.p.mouse.move(mb.x+mb.width*0.6,mb.y+mb.height*0.5); await o.p.mouse.wheel(0,-300); await o.p.waitForTimeout(200); await o.p.click('#orReset'); await o.p.waitForTimeout(200); ok(await X(o,'OR.view===null'),'Reset button');
   const rows=await X(o,'(()=>{ const y=orY(600); return OR.P.bk[OR.i].s.k.filter(k=>k>=y.lo&&k<=y.hi).length; })()'); ok(rows>=10&&rows<=40,`±1% shows ${rows} SPX strikes in all three panes`);
   // profile modes, sides
   await o.p.click('#orProfSeg button[data-v="cp"]'); await o.p.waitForTimeout(250);
   const cl=await count(o,'orProfC',0,0.5,'call'), cr=await count(o,'orProfC',0.5,1,'call'), pl=await count(o,'orProfC',0,0.5,'put'), pr=await count(o,'orProfC',0.5,1,'put');
   ok(cr>20&&pl>20&&cl<5&&pr<5,`Calls/Puts: calls right (${cr}, left ${cl}), puts left (${pl}, right ${pr})`);
   ok(await X(o,'(()=>{ const p=orProfile(OR.P.bk[OR.i].s); let b=0; p.k.forEach((_,i)=>{ if(p.c[i]+p.p[i]>p.c[b]+p.p[b]) b=i; }); return p.poc===p.k[b]; })()'),'Calls/Puts POC = busiest strike for calls + puts');
   await o.p.click('#orProfSeg button[data-v="bb"]'); await o.p.waitForTimeout(250);
   const gl=await count(o,'orProfC',0,0.5,'green'), gr=await count(o,'orProfC',0.5,1,'green'), rl=await count(o,'orProfC',0,0.5,'red'), rr=await count(o,'orProfC',0.5,1,'red');
   ok(gr>20&&rl>20&&gl<5&&rr<5,`Bull/Bear: bull green right (${gr}, left ${gl}), bear red left (${rl}, right ${rr})`);
   ok(await X(o,'(()=>{ const p=orProfile(OR.P.bk[OR.i].s); let b=0; p.k.forEach((_,i)=>{ if(p.bull[i]+p.bear[i]>p.bull[b]+p.bear[b]) b=i; }); return p.poc===p.k[b]; })()'),'Bull/Bear POC = busiest strike for bull + bear');
   await o.p.locator('#tabOrbs').screenshot({path:OUT+'orbs_zoom_profile.png'});
   await o.p.click('#orProfSeg button[data-v="net"]'); await o.p.waitForTimeout(250);
   const nl=await count(o,'orProfC',0,0.5,'red'), nr=await count(o,'orProfC',0.5,1,'green'), ng=await count(o,'orProfC',0,0.5,'green'), nx=await count(o,'orProfC',0.5,1,'red');
   ok(nl>5&&nr>5&&ng<5&&nx<5,`Net: red left (${nl}), green right (${nr}), never the other way (${ng}/${nx})`);
   // bases
   for(const [v,lab] of [['oi','OI'],['vol','Vol'],['oiv','OI + vol'],['ois','OI + signed']]){ await o.p.click(`#orBasisSeg button[data-v="${v}"]`); await o.p.waitForTimeout(200);
     const chk=await X(o,`(()=>{ const s=OR.P.bk[OR.i].s, A=f=>orArr(s,f), g=orG(s,"g"), ch=orG(s,"ch"), va=orG(s,"va");
       const E=(a,b,c)=>a.map((x,i)=>x+(b?b[i]:0));
       const want={oi:[A("g"),A("va"),A("ch")],vol:[A("gv"),A("vav"),A("chv")],oiv:[E(A("g"),A("gv")),E(A("va"),A("vav")),E(A("ch"),A("chv"))],ois:[E(A("g"),A("gt")),E(A("va"),A("vat")),E(A("ch"),A("cht"))]}["${v}"];
       const same=(a,b)=>a.length===b.length&&a.every((x,i)=>Math.abs(x-b[i])<1e-12);
       return same(g,want[0])&&same(va,want[1])&&same(ch,want[2])&&OR.basis==="${v}"&&g.some(x=>x!==0)&&ch.some(x=>x!==0); })()`);
     ok(chk,`basis ${lab}: gamma, vanna and charm = ${ {oi:'OI book',vol:'volume book',oiv:'OI + volume books',ois:'OI + signed-trade books'}[v] }`); }
   ok(await o.p.$$eval('#orBasisSeg button[aria-pressed=true]',bs=>bs.length)===1,'basis is single-select');
   await o.p.click('#orBasisSeg button[data-v="oi"]'); await o.p.waitForTimeout(150);
   // charm
   await o.p.click('#orMeasSeg button[data-v="ch"]'); await o.p.waitForTimeout(300);
   ok(await X(o,'OR.meas==="ch"&&orG(OR.P.bk[OR.i].s,"ch").some(v=>v!==0)'),'Charm view on, charm values present');
   const rd=await o.p.innerText('#orRead'); ok(/Charm/.test(rd)&&/per hour/.test(rd),'Charm read-out: '+rd.slice(rd.indexOf('Charm'),rd.indexOf('Charm')+110));
   ok(/charm/.test(await o.p.innerText('#orSub'))&&/Charm/.test(await o.p.innerText('#orLadT')),'Charm view labels the orbs and the ladder');
   const box=await o.p.locator('#orMainC').boundingBox(); await o.p.mouse.move(box.x+box.width*0.93,box.y+box.height*0.5); await o.p.waitForTimeout(200);
   ok(/charm .*\/h/.test(await o.p.$eval('#orTip',e=>e.innerText)),'hover shows charm per hour');
   await o.p.locator('#tabOrbs').screenshot({path:OUT+'orbs_charm.png'});
   ok(o.errs.length===0,'Orbs: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 5. Volume: last 5 / 15 / 30 min
 { const o=await open(B,{dsf:2,ls:{'cockpit.tab':'vol','cockpit.sideSrc':'convex'}}); await o.p.waitForTimeout(800);   // Side = Trade tag
   ok(await o.p.$$eval('#volWinSeg button[aria-pressed=true]',bs=>bs.map(b=>b.dataset.v).join())==='day'&&await o.p.$eval('#volWinLine',e=>e.hidden),'Volume: window switch defaults to Day, no sentence');
   await o.p.click('#volWinSeg button[data-v="15"]'); await o.p.waitForTimeout(600);
   const line=await o.p.innerText('#volWinLine'); ok(/^Last 15 min: most new activity at [\d,]+ (calls|puts) (bought|sold) \(\+[\d.]+k?\)/.test(line),'sentence: '+line.slice(0,140));
   ok(await o.p.innerText('#volDayH')==='Last 15 min','ladder title follows the window');
   const st15=await X(o,`(()=>{ const se=VOLGEO._se, bk=VOLGEO._book, m=volWinStats(se,bk,15), d=[...m.values()]; let best=null;
       for(const x of d) for(const f of ["cB","cS","pB","pS"]) if(!best||x[f]>best.n) best={k:x.k,f,n:x[f]};
       const tot=d.reduce((a,x)=>a+x.c+x.p,0), day=bk.rows.reduce((a,r)=>a+(r.cv||0)+(r.pv||0),0), tag=d.reduce((a,x)=>a+x.cB+x.cS+x.pB+x.pS,0);
       const j=(()=>{ const T=se.T, t=T[se.m-1]; for(let q=se.m-1;q>=0;q--) if(t-T[q]>=15) return q; return -1; })();
       let manual=0; for(const r of bk.rows){ const i=se.idx.get(r.k); if(i==null) continue; manual+=Math.max(0,(r.cv||0)-(se.cuC[i][j]||0))+Math.max(0,(r.pv||0)-(se.cuP[i][j]||0)); }
       return {best,tot,day,tag,manual}; })()`);
   ok(st15.tot>0&&st15.tot<st15.day&&Math.abs(st15.tot-st15.manual)<1e-6,`window = day counter now minus 15 min ago (${st15.tot} of ${st15.day} contracts; check ${st15.manual})`);
   ok(st15.tag>0&&st15.tag<=st15.tot+1e-6,`bought/sold split inside the window (${st15.tag} tagged)`);
   ok(line.includes(Number(st15.best.k).toLocaleString('en-US')),'sentence names the strike with the most new contracts in one bucket ('+st15.best.k+' '+st15.best.f+')');
   const t5=await X(o,'[...volWinStats(VOLGEO._se,VOLGEO._book,5).values()].reduce((a,x)=>a+x.c+x.p,0)'), t30=await X(o,'[...volWinStats(VOLGEO._se,VOLGEO._book,30).values()].reduce((a,x)=>a+x.c+x.p,0)');
   ok(t5>0&&t5<st15.tot&&st15.tot<t30,`windows nest: 5 min ${t5} < 15 min ${st15.tot} < 30 min ${t30}`);
   // side setting
   ok(/Side: Trade tag/.test(line),'sentence names the Side setting (Trade tag)');
   await o.p.evaluate(()=>{ window.__T.st.sideSrc="cockpit"; window.__T.x("render()"); }); await o.p.waitForTimeout(400);
   ok(/no buy\/sell sides|Side: Quote/.test(await o.p.innerText('#volWinLine')),'sentence follows the Side setting (Quote: '+(await o.p.innerText('#volWinLine')).slice(-60)+')');
   await o.p.evaluate(()=>{ window.__T.st.sideSrc="convex"; window.__T.x("render()"); }); await o.p.waitForTimeout(400);
   // hover
   const vb=await o.p.locator('#volDayC').boundingBox(); await o.p.mouse.move(vb.x+vb.width*0.5,vb.y+vb.height*0.5); await o.p.waitForTimeout(250);
   const tip=await o.p.$eval('#volTip',e=>e.hidden?'':e.innerText); ok(/Day/.test(tip)&&/Last 5 min/.test(tip)&&/Last 15 min/.test(tip)&&/Last 30 min/.test(tip),'ladder hover shows Day + last 5 / 15 / 30 min: '+tip.replace(/\n/g,' | ').slice(0,120));
   await o.p.mouse.move(5,5);
   const r1=await o.p.locator('#volWinLine').boundingBox(), r2=await o.p.locator('#volDayC').boundingBox();
   await o.p.screenshot({path:OUT+'volume_last15.png',clip:{x:r2.x-8,y:r1.y-60,width:r2.width+16,height:r2.y+r2.height-r1.y+68}});
   for(const v of ['5','30','day']){ await o.p.click(`#volWinSeg button[data-v="${v}"]`); await o.p.waitForTimeout(300); }
   ok(await o.p.$eval('#volWinLine',e=>e.hidden)&&await o.p.innerText('#volDayH')==='Today','back to Day');
   ok(o.errs.length===0,'Volume: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 7. Tenor live
 { const o=await open(B,{dsf:2,vp:{width:1600,height:2000},ls:{'cockpit.tnOn':'1','cockpit.tab':'tenor'}}); await o.p.waitForSelector('#tnLiveBox .tnlivehd',{timeout:15000}); await o.p.waitForTimeout(400);
   const hd=await o.p.innerText('#tnLiveBox'); ok(/LIVE \d\d:\d\d:\d\d/.test(hd)&&/from last night/.test(hd),'Tenor: live badge with the feed time: '+hd.split('\n')[0].slice(0,100));
   const m0=await o.p.$eval('#tnGamSvgLive',g=>[g.getAttribute('transform'),g.getAttribute('opacity')]); ok(m0[1]==='1'&&/translate\(/.test(m0[0]),'live marker drawn on the gamma chart '+m0[0]);
   const chk=await o.p.evaluate(()=>{ const T=window.__T, D=T.x('tnData'), G=D.gamma, x=T.st.live.SPX.spot, gr=G.grid, c=G.total.curve; let i=1; while(gr[i]<x) i++;
     const want=c[i-1]+(c[i]-c[i-1])*(x-gr[i-1])/(gr[i]-gr[i-1]); return {want,got:T.x('tnInterp(tnData.gamma.grid,tnData.gamma.total.curve,st.live.SPX.spot)'),x,zc:G.total.zero_cross}; });
   ok(Math.abs(chk.want-chk.got)<1e-9,`total gamma at live SPX ${chk.x} = interpolation of the nightly curve (${chk.got.toFixed(2)})`);
   const tb=await o.p.innerText('#tnLiveBox table'); ok(/Total/.test(tb)&&new RegExp(Math.round(chk.zc-chk.x).toLocaleString('en-US').replace('-','−')).test(tb),'live table: distance from live SPX to the total zero-cross ('+Math.round(chk.zc-chk.x)+')');
   ok(/as of last night/.test(await o.p.innerText('#tnLiveBox')),'vega shown as of last night (no ladder)');
   await o.p.evaluate(()=>{ const T=window.__T; T.st.live=JSON.parse(JSON.stringify(T.st.live)); T.st.live.SPX.spot-=180; T.st.live.ts=T.st.live.ts.replace(/T\d\d:\d\d:\d\d/,"T15:59:30"); T.x("render()"); });
   await o.p.waitForTimeout(300); const m1=await o.p.$eval('#tnGamSvgLive',g=>g.getAttribute('transform'));
   const px=s=>+s.match(/translate\(([\d.]+)/)[1]; ok(px(m1)<px(m0[0])-20,`marker moves when live spot changes (${px(m0[0])} → ${px(m1)})`);
   ok(/LIVE 15:59:30/.test(await o.p.innerText('#tnLiveBox')),'badge time follows the feed');
   await o.p.locator('#tnGam').screenshot({path:OUT+'tenor_live.png'});
   ok(o.errs.length===0,'Tenor: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 8. Levels zoom
 { const o=await open(B,{dsf:2,ls:{'cockpit.lvOn':'1','cockpit.tab':'levels'}}); await o.p.waitForSelector('#lvSvg',{timeout:15000}); await o.p.waitForTimeout(300);
   ok(await o.p.$$eval('#lvZoomSeg button[aria-pressed=true]',bs=>bs.map(b=>b.dataset.v).join())==='all','Levels: zoom defaults to All (the chart as before)');
   const full=await X(o,'LVZ.geo.hi-LVZ.geo.lo');
   await o.p.click('#lvZoomSeg button[data-v="2"]'); await o.p.waitForTimeout(300);
   const g2=await X(o,'[LVZ.geo.lo/lvData.spot,LVZ.geo.hi/lvData.spot]'); ok(near(g2[0],0.98,1e-9)&&near(g2[1],1.02,1e-9),'±2% around spot');
   const sv=await o.p.locator('#lvSvg').boundingBox(); await o.p.mouse.move(sv.x+sv.width*0.4,sv.y+sv.height*0.45); await o.p.mouse.wheel(0,-300); await o.p.waitForTimeout(300);
   const sp=await X(o,'LVZ.geo.hi-LVZ.geo.lo'); ok(sp<full&&await X(o,'!!LVZ.view'),`wheel zooms in (${full.toFixed(0)} → ${sp.toFixed(0)} pts)`);
   const c0=await X(o,'(LVZ.geo.lo+LVZ.geo.hi)/2'); await o.p.mouse.move(sv.x+sv.width*0.4,sv.y+sv.height*0.3); await o.p.mouse.down(); await o.p.mouse.move(sv.x+sv.width*0.4,sv.y+sv.height*0.3+100,{steps:6}); await o.p.mouse.up(); await o.p.waitForTimeout(300);
   ok(await X(o,'(LVZ.geo.lo+LVZ.geo.hi)/2')>c0+1,'drag pans');
   await o.p.click('#lvReset'); await o.p.waitForTimeout(300); ok(await X(o,'LVZ.view===null'),'Reset');
   // hover card on a band inside the zoomed range
   const hit=await o.p.evaluate(()=>{ const sv=document.getElementById('lvSvg').getBoundingClientRect(); const els=[...document.querySelectorAll('#lvSvg .lvzone')].map(e=>e.getBoundingClientRect()).filter(r=>r.top>sv.top+30&&r.bottom<sv.bottom-50&&r.height>0);
     return els.length?{x:els[0].left+els[0].width*0.3,y:els[0].top+els[0].height/2}:null; });
   if(hit){ await o.p.mouse.move(hit.x,hit.y); await o.p.waitForTimeout(250); ok(!(await o.p.$eval('#lvTip',e=>e.hidden))&&/views?/i.test(await o.p.innerText('#lvTip')),'hover card works when zoomed'); }
   else ok(false,'no band inside the ±2% range to hover');
   await o.p.locator('#lvBody').screenshot({path:OUT+'levels_zoom.png'});
   await o.p.mouse.dblclick(sv.x+sv.width*0.5,sv.y+sv.height*0.5); await o.p.waitForTimeout(250); ok(await X(o,'LVZ.view===null'),'double-click resets');
   ok(o.errs.length===0,'Levels: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 6. VIX map: bases, range, per-minute line, now marker, waiting message
 { const o=await open(B,{dsf:2,ls:{'cockpit.tab':'vix','cockpit.vxOpt':'{"vmap":true}'}}); await o.p.waitForFunction(()=>!!document.getElementById('vxmGC'),null,{timeout:15000}); await o.p.waitForTimeout(500);
   ok(await o.p.$$eval('#vxmBasisSeg button',bs=>bs.map(b=>b.textContent).join('|'))==='OI|Vol|OI + vol|OI + signed','VIX map: Positions from = OI | Vol | OI + vol | OI + signed');
   ok(await o.p.$$eval('#vxmRngSeg button[aria-pressed=true]',bs=>bs.map(b=>b.textContent).join())==='10–25'&&near(await X(o,'VXM.geo.G.lo'),9.875,1e-6)&&near(await X(o,'VXM.geo.G.hi'),25.125,1e-6),'range defaults to VIX 10–25 on both maps');
   ok(await X(o,'VXM.geo.V.hi')===await X(o,'VXM.geo.G.hi'),'gamma and vanna maps share the range');
   const px=await X(o,'VXM.J.px.length'), uniq=await X(o,'new Set(VXM.J.px.map(p=>p[1])).size'); ok(px>300&&uniq>50,`VIX line from every minute (${px} minutes, ${uniq} different values)`);
   // white line pixels at three times
   const onLine=await o.p.evaluate(()=>{ const T=window.__T, G=T.x('VXM.geo.G'), pth=T.x('VXM.J.px'), c=document.getElementById('vxmGC'), g=c.getContext('2d'), d=window.devicePixelRatio||1; let hits=0;
     for(const f of [0.2,0.5,0.8]){ const p=pth[Math.floor(f*(pth.length-1))], x=G.X(T.x(`tmin("${p[0]}")`)), y=G.Y(p[1]); let best=0;
       for(let dy=-3;dy<=3;dy++){ const q=g.getImageData(Math.round(x*d),Math.round((y+dy)*d),1,1).data; best=Math.max(best,Math.min(q[0],q[1],q[2])); } if(best>200) hits++; } return hits; });
   ok(onLine===3,`white VIX line drawn at the minute's VIX across the session (${onLine}/3 sampled minutes)`);
   const nowPx=await o.p.evaluate(()=>{ const T=window.__T, G=T.x('VXM.geo.G'), pth=T.x('VXM.J.px'), lp=pth[pth.length-1], c=document.getElementById('vxmGC'), g=c.getContext('2d'), d=window.devicePixelRatio||1;
     const q=g.getImageData(Math.round((G.padL+G.W+6)*d),Math.round(G.Y(lp[1])*d),1,1).data; return [q[0],q[1],q[2]]; });
   ok(Math.min(...nowPx)>200,'"now" marker at the right edge at the current VIX '+nowPx);
   // bases + sign
   const cell=async()=>X(o,'(()=>{ const c=VXM.J.cols[VXM.J.cols.length-1]; return [vxmVals(c,"g")[40],c.gfo[40],c.gfv[40],c.gft[40]]; })()');
   for(const [v,f] of [['oi',(a)=>a[1]],['vol',(a)=>a[2]],['oiv',(a)=>a[1]+a[2]],['ois',(a)=>a[1]+a[3]]]){ await o.p.click(`#vxmBasisSeg button[data-v="${v}"]`); await o.p.waitForTimeout(250); const a=await cell(); ok(Math.abs(a[0]-f(a))<1e-9,`basis ${v}: value = ${v==='ois'?'OI + signed trades':v}`); }
   await o.p.click('#vxmSignSeg button[data-v="naive"]'); await o.p.waitForTimeout(250); { const a=await cell(); ok(Math.abs(a[0]-(-a[1]+a[3]))<1e-9&&a[3]!==0,'SPX-style sign flips the OI part only; signed trades stay as measured'); }
   await o.p.click('#vxmSignSeg button[data-v="dealer"]'); await o.p.click('#vxmBasisSeg button[data-v="oi"]'); await o.p.waitForTimeout(250);
   ok(/open interest/.test(await o.p.innerText('#vxmRead')),'read-out names the basis');
   await o.p.locator('#vxMap').screenshot({path:OUT+'vix_map_range.png'});
   await o.p.locator('#vxMap').screenshot({path:OUT+'vix_map.png'});
   await o.p.click('#vxmRngSeg button[data-v="50"]'); await o.p.waitForTimeout(250); ok(near(await X(o,'VXM.geo.G.hi'),50.125,1e-6),'10–50');
   const lim25=await X(o,'(VXM.rng="25",VXM.key="",vxmRender(),VXM.geo.G.lim)'), lim50=await X(o,'(VXM.rng="50",VXM.key="",vxmRender(),VXM.geo.G.lim)'); ok(lim25!==lim50,`colour scale re-fits to the rows shown (${lim25.toFixed(1)} vs ${lim50.toFixed(1)})`);
   await o.p.click('#vxmRngSeg button[data-v="custom"]'); await o.p.waitForTimeout(250); await o.p.fill('#vxmCmin','12'); await o.p.dispatchEvent('#vxmCmin','change'); await o.p.fill('#vxmCmax','20'); await o.p.dispatchEvent('#vxmCmax','change'); await o.p.waitForTimeout(300);
   ok(near(await X(o,'VXM.geo.G.lo'),11.875,1e-6)&&near(await X(o,'VXM.geo.G.hi'),20.125,1e-6),'custom range 12–20');
   ok(await o.p.evaluate(()=>[localStorage.getItem('cockpit.vxmRng'),localStorage.getItem('cockpit.vxmCmin'),localStorage.getItem('cockpit.vxmCmax')].join())==='custom,12,20','range remembered');
   await o.p.click('#vxmRngSeg button[data-v="all"]'); await o.p.waitForTimeout(250); ok(near(await X(o,'VXM.geo.G.hi'),await X(o,'VXM.J.levels[VXM.J.levels.length-1]+0.125'),1e-6),'All = every row in the file');
   await o.p.click('#vxmRngSeg button[data-v="25"]'); await o.p.waitForTimeout(200);
   ok(o.errs.length===0,'VIX map: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 { const o=await open(B,{light:true,live:'live_1109.json',ls:{'cockpit.tab':'vix','cockpit.vxOpt':'{"vmap":true}'}}); await o.p.waitForFunction(()=>/Waiting for/.test((document.getElementById('vxMap')||{}).innerText||''),null,{timeout:15000}); await o.p.waitForTimeout(800);
   const t=await o.p.innerText('#vxMap'); ok(/Waiting for vixmap_20261109\.json/.test(t)&&/Showing 2026-11-06/.test(t)&&!!(await o.p.$('#vxmGC')),'today missing: says it waits for vixmap_20261109.json and shows the last stored day (2026-11-06)');
   ok(o.errs.length===0,'VIX map fallback: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- 9. top bar SPX <-> SPY calculator
 { const o=await open(B,{dsf:2,ls:{'cockpit.tab':'map'}}); await o.p.waitForTimeout(600);
   await o.p.fill('#xcIn','7500'); await o.p.waitForTimeout(300);
   const r=await o.p.evaluate(()=>{ const L=window.__T.st.live; return L.SPX.spot/L.SPY.spot; });
   const out=await o.p.innerText('#xcOut'); ok(new RegExp('SPX 7,500 = SPY '+(7500/r).toFixed(2).replace('.','\\.')).test(out)&&out.includes('ratio '+r.toFixed(3)),'7500 read as SPX → SPY '+(7500/r).toFixed(2)+' with the live ratio: '+out);
   const pop=await o.p.innerText('#xcPop'); ok(/SPX 7,500/.test(pop)&&/SPY \d{3}/.test(pop)&&/Dealer gamma/.test(pop)&&/Open interest/.test(pop),'details: nearest strikes on both, OI, volume, dealer gamma');
   ok((await o.p.$$('#xcPop button.xck')).length>=3,'SPX strike + two SPY strikes around the converted level');
   const g=await X(o,'xcGamma("SPX",7500)'), g2=await X(o,'(()=>{ const {book}=currentBook(null,"SPX"); const r=book.rows.find(x=>x.k===7500); const S=book.S, ty=Math.max(minsToClose(liveClock()),1)/525600; return gexAt(S,[r],ty); })()');
   ok(g!=null&&Math.abs(g-g2)<1e-9*Math.max(1,Math.abs(g)),'dealer gamma at the strike = the Map book (gexAt) for that one strike');
   ok(await o.p.evaluate(()=>localStorage.getItem('cockpit.xcIn'))==='7500','last input remembered');
   await o.p.fill('#xcIn','750'); await o.p.waitForTimeout(250); ok(/^SPY 750\.00 = SPX /.test(await o.p.innerText('#xcOut')),'750 read as SPY');
   await o.p.click('#xcMode button[data-v="SPX"]'); await o.p.waitForTimeout(250); ok(/^SPX 750 = SPY /.test(await o.p.innerText('#xcOut')),'SPX toggle forces it');
   await o.p.click('#xcMode button[data-v="auto"]'); await o.p.fill('#xcIn','7500'); await o.p.waitForTimeout(250);
   await o.p.hover('#xcIn'); await o.p.waitForTimeout(200);
   const hb=await o.p.locator('header').boundingBox(), pb=await o.p.locator('#xcPop').boundingBox();
   await o.p.screenshot({path:OUT+'topbar_calc.png',clip:{x:0,y:0,width:1600,height:Math.min(1150,pb.y+pb.height+12)}});
   const spyBtn=await o.p.$('#xcPop button.xck[data-sym="SPY"]'); const k=+(await spyBtn.getAttribute('data-k')); await spyBtn.click(); await o.p.waitForTimeout(400);
   ok(await X(o,'st.sym')==='SPY'&&await X(o,'st.sel')===k,'click a SPY strike: symbol SPY, strike '+k+' selected');
   ok(await o.p.evaluate(()=>document.documentElement.scrollWidth)<=1600,'header fits: no horizontal scroll at 1600 px');
   ok(o.errs.length===0,'calculator: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 // ---------------- every new option ON with its data files missing, and phone width
 { const o=await open(B,{missing:true,ls:{'cockpit.orOn':'1','cockpit.tnOn':'1','cockpit.lvOn':'1','cockpit.vxOpt':'{"vmap":true}','cockpit.volWin':'15','cockpit.xcIn':'7500','cockpit.orMeas':'ch','cockpit.orBasis':'ois'}});
   for(const t of ['orbs','vol','tenor','levels','vix','flow','map','help']){ await tab(o,t); await o.p.waitForTimeout(t==='vix'?2500:600); }
   ok(/Waiting for vixmap_\d{8}\.json/.test(await o.p.innerText('#vxMap'))&&/No earlier VIX map day/.test(await o.p.innerText('#vxMap')),'VIX map, no files at all: waiting message');
   ok(o.errs.length===0,'missing data files: 0 JS errors '+JSON.stringify(o.errs.slice(0,3))); await o.ctx.close(); }
 { const o=await open(B,{vp:{width:400,height:900},ls:{'cockpit.xcIn':'7500'}}); await o.p.waitForTimeout(500);
   ok(await o.p.evaluate(()=>document.documentElement.scrollWidth)<=400&&o.errs.length===0,'phone: calculator in the top bar, no sideways scroll, 0 errors'); await o.ctx.close(); }
 await B.close(); console.log(fails?`FAILED ${fails}/${n}`:`ALL OK ${n}/${n}`); process.exit(fails?1:0); })();
