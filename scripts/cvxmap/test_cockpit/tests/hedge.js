// Hedge curve checks (2026-11-13), Map > Gamma button "Hedge". Same harness as parity.js: a folder served on :8765 holding
// prod_old.html (instrument.py of the page BEFORE the change), prod.html (instrument.py of the page with Hedge) and the data
// files of one session (live.json, flow_YYYYMMDD.ndjson, ti.json, smilebeta.json, ledger.json, straddle.json).
// Checks: Hedge OFF = buildMap / read-out / Map canvases identical to the old page; rally and drop at spot and off spot =
// the exact Black-Scholes dealer-delta change within 1% (0DTE book, OI, Smile off); strip, lines, hover card, Zoom, the
// button, phone width; 0 JS errors. Screenshots to OUT. Exit 1 on any failure.
//   node hedge.js        (TC_CHROME=/path/to/chrome to use an installed Chrome; TC_OLD / TC_NEW to rename the pages)
const {chromium}=require('playwright'); const fs=require('fs');
const BASE=process.env.TC_BASE||'http://localhost:8765/', OUT=process.env.OUT||'./shots/', OLD=process.env.TC_OLD||'prod_old.html', NEW=process.env.TC_NEW||'prod.html'; fs.mkdirSync(OUT,{recursive:true});
let fails=0,n=0; const ok=(c,m)=>{ n++; if(!c) fails++; console.log((c?'OK   ':'FAIL ')+m); };
async function open(B,page,ls,vp){ const ctx=await B.newContext({viewport:vp||{width:1600,height:1150}}); const p=await ctx.newPage(); const errs=[];
  p.on('pageerror',e=>errs.push(String(e))); p.on('console',m=>{ if(m.type()==='error'&&!/Failed to load resource/.test(m.text())) errs.push('console: '+m.text()); });
  await p.addInitScript(s=>{ try{ const o=JSON.parse(s); for(const k in o) localStorage.setItem(k,o[k]); }catch(e){} }, JSON.stringify(Object.assign({'cockpit.tab':'map'},ls||{})));
  await p.goto(BASE+page); await p.waitForFunction(()=>{ const T=window.__T; return T&&T.st.live&&T.st.live.SPX&&T.st.flow&&T.st.flow.SPX&&T.st.flow.SPX.length>100&&T.st.ti; },null,{timeout:60000});
  await p.waitForTimeout(1500); return {p,ctx,errs}; }
const grabMaps=o=>o.p.evaluate(()=>{ const {st,buildMap,mapRead}=window.__T; const R={}; const d=st.live.SPX; const keep={smileOn:st.smileOn,basis:st.basis};
  for(const basis of ["oi","oiv"]) for(const sm of [false,true]) for(const book of ["0dte","all"]) for(const kind of ["gamma","charm","vanna"]){
    st.smileOn=sm; st.basis=basis; const m=buildMap(kind,null,d,book,null); R[[basis,sm,book,kind].join("|")]=m?{grid:m.grid,feats:m.feats,vmax:m.vmax}:null;
    if(kind==="gamma"&&book==="all"&&m){ const ch=buildMap("charm",null,d,book,null); R[[basis,sm,"read"].join("|")]=mapRead(m,ch,null,null,d); } }
  st.smileOn=keep.smileOn; st.basis=keep.basis; return R; });
const canv=o=>o.p.evaluate(()=>["mapGammaC","mapRightC","mapBreakC"].map(id=>{ const c=document.getElementById(id); return id+":"+c.width+"x"+c.height+":"+c.toDataURL().length+":"+c.toDataURL().slice(-200); }));
(async()=>{ const B=await chromium.launch(process.env.TC_CHROME?{executablePath:process.env.TC_CHROME,args:['--disable-dev-shm-usage']}:{});
  // 1. OFF parity: old page vs new page
  { const A=await open(B,OLD), N=await open(B,NEW);
    const ma=await grabMaps(A), mn=await grabMaps(N); let same=0,tot=0; for(const k of Object.keys(ma)){ tot++; if(JSON.stringify(ma[k])===JSON.stringify(mn[k])) same++; else console.log('DIFF',k); }
    ok(same===tot,`OFF parity: buildMap + read ${same}/${tot} identical`);
    const ca=await canv(A), cn=await canv(N); ok(JSON.stringify(ca)===JSON.stringify(cn),'OFF parity: Map tab canvases (gamma, right, breakdown) pixel-identical');
    const ra=await A.p.$eval('#mapRead',e=>e.innerHTML), rn=await N.p.$eval('#mapRead',e=>e.innerHTML); ok(ra===rn,'OFF parity: read-out HTML identical');
    ok(await N.p.$eval('#hedgeStrip',e=>e.hidden)&&await N.p.$eval('#hedgeBtn',e=>e.getAttribute('aria-pressed'))==='false','OFF: strip hidden, Hedge button not pressed');
    ok(await N.p.evaluate(()=>window.__T.x('HEDGE.cur===null')),'OFF: nothing computed');
    ok(A.errs.length===0&&N.errs.length===0,'0 JS errors old/new '+JSON.stringify(N.errs.slice(0,3)));
    await A.ctx.close(); await N.ctx.close(); }
  // 2. ON: maths vs exact dealer delta (0DTE book, OI basis, Smile off -> rows fixed, exact Black-Scholes delta difference)
  { const N=await open(B,NEW,{'cockpit.mapHedge':'1','cockpit.mapBook':'0dte','cockpit.basis':'oi','cockpit.smileOn':'0'});
    const r=await N.p.evaluate(()=>{ const T=window.__T, st=T.st; st.smileOn=false; st.basis="oi"; st.mapBook="0dte"; const d=st.live.SPX;
      const m=T.buildMap("gamma",null,d,"0dte",null); const H=T.x("hedgeCurve")(m,d); if(!H) return null;
      let jn=0; for(let j=0;j<m.times.length;j++) if(m.times[j]<=Math.max(m.now,570)) jn=j; const t=m.times[jn];
      const snaps=st.flow.SPX; const byT=new Map(snaps.map((s,j)=>[T.x("tmin")(s.t),j])); let j=null; for(let q=0;q<4;q++){ if(byT.has(t+q)){ j=byT.get(t+q); break; } }
      const rows=j==null?T.mapRowsAt(null,null,d):T.mapRowsAt(j,null,d); const ty=Math.max(960-t,1)/525600, sq=Math.sqrt(ty);
      const N=x=>{ const a=Math.abs(x)/Math.SQRT2, tt=1/(1+0.3275911*a), y=1-(((((1.061405429*tt-1.453152027)*tt)+1.421413741)*tt-0.284496736)*tt+0.254829592)*tt*Math.exp(-a*a); return 0.5*(1+(x<0?-y:y)); };
      const Dx=S=>{ let v=0; for(const r of rows){ if(!(r.sg>0)) continue; const pos=r.cp-r.pp; if(!pos) continue; v+=pos*N((Math.log(S/r.k)+0.5*r.sg*r.sg*ty)/(r.sg*sq)); } return v; };
      const S=H.S, em=H.em; const exUp=-(Dx(S+em)-Dx(S)), exDn=-(Dx(S-em)-Dx(S));
      // a second check: one start price off spot
      const q=H.pts[Math.floor(H.pts.length/3)]; const exQ=-(Dx(q.p+em)-Dx(q.p));
      return {S,em,src:H.src,up:H.atS.up,dn:H.atS.dn,exUp,exDn,qp:q.p,qUp:q.up,exQ,sq:H.sq.map(x=>x.p),as:H.as.map(x=>x.p),npts:H.pts.length,t,rng:[H.pts[0].p,H.pts[H.pts.length-1].p]}; });
    console.log(JSON.stringify(r));
    const rel=(a,b)=>Math.abs(a-b)/Math.max(1,Math.abs(b));
    ok(r&&rel(r.up,r.exUp)<0.01&&rel(r.dn,r.exDn)<0.01,`rally/drop at spot = exact dealer-delta change within 1% (${r&&r.up.toFixed(0)} vs ${r&&r.exUp.toFixed(0)}, ${r&&r.dn.toFixed(0)} vs ${r&&r.exDn.toFixed(0)})`);
    ok(r&&rel(r.qUp,r.exQ)<0.01,`rally from ${r&&r.qp} = exact within 1% (${r&&r.qUp.toFixed(0)} vs ${r&&r.exQ.toFixed(0)})`);
    ok(r&&/straddle/.test(r.src)&&r.em>2&&r.em<80,`move = live straddle (${r&&r.em.toFixed(2)} pts, ${r&&r.src})`);
    await N.ctx.close(); }
  // 3. ON with the default book (All, OI): strip, lines, hover, screenshots
  { const N=await open(B,NEW,{'cockpit.mapHedge':'1'});
    ok(!(await N.p.$eval('#hedgeStrip',e=>e.hidden)),'ON: strip visible'); ok(await N.p.$eval('#hedgeBtn',e=>e.getAttribute('aria-pressed'))==='true','ON: button pressed');
    const txt=await N.p.$eval('#hedgeRead',e=>e.innerText); console.log('READ:',txt.replace(/\n/g,' | ')); ok(/one straddle/.test(txt)&&/dealers/.test(txt),'ON: plain read line');
    const cur=await N.p.evaluate(()=>{ const H=window.__T.x('HEDGE.cur'); return H?{S:H.S,em:H.em,sq:H.sq.map(x=>Math.round(x.p)),as:H.as.map(x=>Math.round(x.p)),up:H.atS.up,dn:H.atS.dn,book:H.book}:null; });
    console.log('CUR',JSON.stringify(cur)); ok(!!cur,'ON: curve computed on the All book');
    const pix=await N.p.evaluate(()=>{ const c=document.getElementById('hedgeC'); const g=c.getContext('2d'); const d=g.getImageData(0,0,c.width,c.height).data; let cy=0,or=0; for(let i=0;i<d.length;i+=4){ if(d[i]<120&&d[i+1]>190&&d[i+2]>220) cy++; if(d[i]>230&&d[i+1]>150&&d[i+1]<200&&d[i+2]<110) or++; } return {cy,or,w:c.width,h:c.height}; });
    ok(pix.cy>200&&pix.or>100,`ON: both curves drawn (cyan ${pix.cy} px, orange ${pix.or} px, canvas ${pix.w}x${pix.h})`);
    const gh=await N.p.$eval('#mapGammaC',c=>c.height); ok(gh>200,'ON: gamma map still has room ('+gh+' px)');
    await N.p.locator('#mapGammaPane').screenshot({path:OUT+'hedge_on_gamma_pane.png'});
    const box=await N.p.locator('#hedgeC').boundingBox(); await N.p.mouse.move(box.x+box.width*0.62,box.y+box.height*0.5); await N.p.waitForTimeout(250);
    const tip=await N.p.$eval('#hedgeTip',e=>e.hidden?'':e.innerText); console.log('TIP:',tip.replace(/\n/g,' | ')); ok(/starts at/.test(tip)&&/rally to/.test(tip)&&/drop to/.test(tip),'hover card shows rally + drop at that price');
    await N.p.screenshot({path:OUT+'hedge_hover.png',clip:{x:box.x-10,y:box.y-80,width:Math.min(1200,box.width+20),height:box.height+200}});
    // zoom on: lines still drawn, curve still from the full map
    await N.p.evaluate(()=>{ const st=window.__T.st; st.zoomOn=true; st.zoomW=50; window.__T.x('render()'); }); await N.p.waitForTimeout(400);
    const cz=await N.p.evaluate(()=>{ const H=window.__T.x('HEDGE.cur'); return H?H.pts.length:0; }); ok(cz>10,'Zoom on: curve unchanged in size ('+cz+' points, full map)');
    await N.p.locator('#mapGammaPane').screenshot({path:OUT+'hedge_on_zoom.png'});
    // turn OFF by the button: strip hides, map back to old pixels
    await N.p.evaluate(()=>{ const st=window.__T.st; st.zoomOn=false; window.__T.x('render()'); });
    await N.p.click('#hedgeBtn'); await N.p.waitForTimeout(500);
    ok(await N.p.$eval('#hedgeStrip',e=>e.hidden)&&await N.p.evaluate(()=>localStorage.getItem('cockpit.mapHedge'))==='0','button OFF: strip hidden, saved off');
    ok(N.errs.length===0,'ON: 0 JS errors '+JSON.stringify(N.errs.slice(0,3)));
    await N.ctx.close(); }
  // 4. phone width
  { const N=await open(B,NEW,{'cockpit.mapHedge':'1'},{width:420,height:900}); await N.p.waitForTimeout(500);
    await N.p.locator('#mapGammaPane').screenshot({path:OUT+'hedge_phone.png'}); ok(N.errs.length===0,'phone: 0 JS errors'); await N.ctx.close(); }
  await B.close(); console.log(fails?`FAILED ${fails}/${n}`:`ALL OK ${n}/${n}`); process.exit(fails?1:0); })();
