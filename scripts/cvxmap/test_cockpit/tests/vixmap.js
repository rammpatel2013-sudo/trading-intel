// VIX map Strike map (C1 + C2) checks (2026-11-19 night). Folder served on TC_BASE with test_old.html (instrument.py of the
// test page BEFORE this ship), test.html (instrument.py of the new test page) and vixmap_fixture.py's output
// (vixmap_20261106.json v3 + wide_index.json). Checks: button OFF = VIX map canvases, read-out and hover identical to the old
// test page; ON = strike rows drawn (canvases differ), role labels = rule rv-v0 recomputed here, hover card text, scale from
// the books, 0 JS errors. Exit 1 on fail.
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.OUT||'./shots/', OLD=process.env.TC_OLD||'test_old.html', NEW=process.env.TC_NEW||'test.html'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls){ const ctx=await B.newContext({viewport:{width:1600,height:1400}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource|404/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'vix','cockpit.vxOpt':'{"vmap":true}'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; if(!T) return false; const V=T.x('VXM'); return V&&V.J&&V.J.cols&&V.J.cols.length&&document.getElementById('vxmGC'); },null,{timeout:60000});
  await p.waitForTimeout(1200); return {p,ctx,errs}; }
const canv=o=>o.p.evaluate(()=>["vxmGC","vxmVC"].map(id=>{ const c=document.getElementById(id); return c?id+":"+c.width+"x"+c.height+":"+c.toDataURL().length+":"+c.toDataURL().slice(-300):id+":none"; }));
const hover=async(o,id,fx,fy)=>{ const b=await o.p.$eval('#'+id,c=>{ const r=c.getBoundingClientRect(); return [r.left,r.top,r.width,r.height]; }); await o.p.mouse.move(b[0]+b[2]*fx,b[1]+b[3]*fy); await o.p.waitForTimeout(200); return o.p.$eval('#vxmHov',e=>e.innerHTML); };
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  const A=await open(B,OLD), N=await open(B,NEW);
  ok(await A.p.evaluate(()=>window.__T.x('VXM').J.v)===3,'fixture: file v3 with books');
  ok(JSON.stringify(await canv(A))===JSON.stringify(await canv(N)),'OFF: VIX map canvases pixel-identical to the old test page');
  ok(await A.p.$eval('#vxmRead',e=>e.innerHTML)===await N.p.$eval('#vxmRead',e=>e.innerHTML),'OFF: read-out identical');
  ok(await hover(A,'vxmGC',0.4,0.5)===await hover(N,'vxmGC',0.4,0.5),'OFF: hover identical');
  ok(await N.p.$('#tgVxsBtn')!==null,'OFF: Strike map button present');
  ok(!A.errs.length&&!N.errs.length,'OFF: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  // ON by the button
  await N.p.click('#tgVxsBtn'); await N.p.waitForTimeout(800);
  ok(await N.p.evaluate(()=>localStorage.getItem('cockpit.tgVxs'))==='1','ON: button saves the toggle');
  ok(JSON.stringify(await canv(A))!==JSON.stringify(await canv(N)),'ON: canvases change (strike rows)');
  ok(await N.p.evaluate(()=>window.__T.x('VXM').geo.G.strike===true&&window.__T.x('VXM').geo.V.strike===true),'ON: both maps drawn by the strike drawer');
  const roles=await N.p.evaluate(()=>{ const T=window.__T, J=T.x('VXM').J, b=J.books[J.books.length-1], px=J.px[J.px.length-1][1];
    const R=T.x('tgVxsRoles')(b,px), v=T.x('tgVxsVals')(b,"g");
    // rv-v0 recomputed independently
    let bad=0; for(const r of R){ const x=v[r.i], y=b.y[r.i]; const want=x>0?(Math.abs(y-px)<0.1?"PIN":y<px?"FLOOR":"CEILING"):"MAGNET"; if(r.role!==want) bad++; if(r.role==="MAGNET"&&r.up!==(y>px)) bad++; }
    const sorted=R.every((r,i)=>!i||R[i-1].abs>=r.abs); return {n:R.length,bad,sorted,top:R.slice(0,10).map(r=>[r.rank,r.k,r.role,r.up,+r.pct.toFixed(3)])}; });
  console.log('roles', JSON.stringify(roles.top));
  ok(roles.n>10&&roles.bad===0&&roles.sorted&&roles.top[0][4]===1,'ON: roles rv-v0 (sign + side of VIX), ranked by size, #1 = 100%');
  const rd=await N.p.$eval('#vxmRead',e=>e.innerHTML); ok(/Book 12:45 \(live\)/.test(rd)&&/#1 /.test(rd),'ON: read-out lists the top roles of the newest book');
  const hv=await hover(N,'vxmGC',0.55,0.5); console.log('hover', hv.replace(/<[^>]+>/g,' ').slice(0,400));
  ok(/STRIKE ROLES · VIX options/.test(hv)&&/(rank #\d+ of \d+|No listed strike|no dealer position)/.test(hv),'ON: hover card');
  const hv2=await hover(N,'vxmVC',0.55,0.5); ok(/STRIKE ROLES/.test(hv2),'ON: hover card on the vanna map');
  // scale from the books; the basis / sign buttons still drive the strike map
  const sc=await N.p.evaluate(()=>{ const T=window.__T, V=T.x('VXM'); return [V.geo.G.lim, T.x('tgVxsLim')(V.J,"g",T.x('vxmRange')(V.J.levels))]; }); ok(Math.abs(sc[0]-sc[1])<1e-12,'ON: colour scale = 98th pct of the books');
  const c1=JSON.stringify(await canv(N)); await N.p.click('#vxmSignSeg button[data-v="naive"]'); await N.p.waitForTimeout(500);
  const flip=await N.p.evaluate(()=>{ const T=window.__T, J=T.x('VXM').J, b=J.books[J.books.length-1]; return T.x('tgVxsVals')(b,"g").slice(0,5).map(x=>x+0); });
  ok(JSON.stringify(await canv(N))!==c1,'ON: Dealer side switch redraws the strike map');
  await N.p.click('#vxmSignSeg button[data-v="dealer"]'); await N.p.waitForTimeout(500);
  const back=await N.p.evaluate(()=>{ const T=window.__T, J=T.x('VXM').J, b=J.books[J.books.length-1]; return T.x('tgVxsVals')(b,"g").slice(0,5); });
  ok(flip.every((x,i)=>Math.abs(x+back[i])<1e-12),'ON: SPX-style sign = exactly the negative on the OI basis');
  await N.p.screenshot({path:OUT+'vixmap_on.png',fullPage:false});
  await N.p.$eval('#vxMap',e=>e.scrollIntoView()); await N.p.waitForTimeout(300); await N.p.screenshot({path:OUT+'vixmap_on_map.png'});
  // ON with a v2 file (no books) = production drawing + a note
  await N.p.evaluate(()=>{ const V=window.__T.x('VXM'); delete V.J.books; V.key=""; window.__T.x('vxmRender()'); }); await N.p.waitForTimeout(400);
  ok(await N.p.evaluate(()=>window.__T.x('VXM').geo.G.strike!==true),'ON + v2 file: production drawing');
  ok(/no strike book/.test(await N.p.$eval('#vxmRead',e=>e.innerHTML)),'ON + v2 file: note in the read-out');
  // OFF again by the button = old canvases
  await N.p.evaluate(()=>{ window.__T.x('VXM').J=null; }); await N.p.reload();
  ok(!N.errs.length,'ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
  await B.close(); console.log(`${n-fails}/${n} passed`); process.exit(fails?1:0); })().catch(e=>{ console.error(e); process.exit(1); });
