/* TEST COCKPIT bar (2026-11-08): toggles, ALL OFF, link back to production, live stats, "My read" notes,
   and the 15-minute read-outs (proof test + per-toggle forward scoring). Everything posts to the TEST server only. */
(function(){
const bar=document.getElementById("tgBar"); if(!bar) return;
const prodURL=location.protocol+"//"+location.hostname+":8899/cvx_cockpit.html";
function post(path,obj){ try{ fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(obj)}).catch(()=>{}); }catch(e){} }
function ctx(){ const d=st.live&&st.live[st.sym]; return {live_ts:st.live&&st.live.ts||null,sym:st.sym,spot:d&&d.spot||null,smile:!!st.smileOn,basis:st.basis,book:st.mapBook||"all",toggles:Object.assign({},TG),page:"test-v1"}; }
function clearCaches(){ try{ _mapCaches.clear(); }catch(e){} try{ WK_CACHE.clear(); }catch(e){} }
function setTG(id,v){ TG[id]=v; tgApply(); tgSave(); clearCaches(); post("note",Object.assign({type:"toggle",id,value:v},ctx())); render(); draw(); }
function allOff(){ for(const [id] of TG_DEF) TG[id]=id in TG_SEL?"":false; tgApply(); tgSave(); clearCaches(); post("note",Object.assign({type:"all_off"},ctx())); render(); draw(); }
function btn(id,lb,tid,ver,help){ const v=TG[id]; const on=!!v; const sel=TG_SEL[id];
  const val=sel?(v?(v.slice(0,2)+":"+v.slice(2)):"off"):(on?"ON":"off");
  return `<button class="tgb${on?" on":""}" data-tg="${id}" title="${help.replace(/"/g,"&quot;")}"><span class="tid">${tid}</span> ${lb} <b>${val}</b>${ver==="v0"?' <span class="v0">v0</span>':""}</button>`; }
function stats(){ const p=[]; const on=tgOn();
  if(TG.b3) p.push(`B3 dropped ${TG_STATS.rej} quote side${TG_STATS.rej===1?"":"s"} now`);
  if(TG.b6) p.push(`B6 filled ${TG_STATS.fill} strike${TG_STATS.fill===1?"":"s"}`);
  if(TG.b5) p.push(`B5 Week IV live ${TG_STATS.wkLive} / fallback ${TG_STATS.wkFb}`);
  if(TG.b4){ const bc=tgBookCheck(); if(bc) p.push((bc.bad?"⚠ ":"")+"B4a "+bc.txt+(bc.bad?" — CHECK THE MORNING BOOK":" — OK")); }
  if(TG.a5){ const e=tgEarly(); p.push(e?`A5 early close ${tlabel(e)}${tgEarlySim()?" (SIMULATED by the address)":""}: 0DTE clock + map end then`:"A5 on: today is a normal close (no change)"); }
  if((TG.e2||TG.d1)&&!st.smileOn) p.push("E2/D1 act only with Smile ON");
  if(TG.c2&&!SB) p.push("C2 waits for smilebeta.json");
  return p.join(" · "); }
function draw(){ const on=tgOn();
  bar.innerHTML=`<div class="tg1"><b class="tgt">TEST COCKPIT</b> <span>${on.length?"ON: "+on.join(", "):"all toggles off = production maths"}</span>
    <button class="tgall" id="tgAll">ALL OFF = production</button> <a class="tgprod" href="${prodURL}">Open production page (8899)</a>
    <span class="tgnote"><input id="tgRead" type="text" maxlength="400" placeholder="My read now (saved with the time and toggles)"><button id="tgReadBtn">Save read</button><span id="tgReadOk"></span></span></div>
    <div class="tg2">${TG_DEF.map(x=>btn(...x)).join("")}</div><div class="tg3" id="tgStats">${stats()}</div>`;
  bar.querySelectorAll("[data-tg]").forEach(b=>b.addEventListener("click",()=>{ const id=b.dataset.tg; const sel=TG_SEL[id];
    if(sel){ const i=sel.indexOf(TG[id]); setTG(id,sel[(i+1)%sel.length]); } else setTG(id,!TG[id]); }));
  document.getElementById("tgAll").addEventListener("click",allOff);
  const save=()=>{ const el=document.getElementById("tgRead"); const t=(el.value||"").trim(); if(!t) return; post("note",Object.assign({type:"read",text:t},ctx())); el.value="";
    const ok=document.getElementById("tgReadOk"); const c=liveClock(); ok.textContent=" saved "+tlabel(c.h*60+c.m); };
  document.getElementById("tgReadBtn").addEventListener("click",save);
  document.getElementById("tgRead").addEventListener("keydown",e=>{ if(e.key==="Enter") save(); e.stopPropagation(); }); }
draw(); setInterval(()=>{ const s=document.getElementById("tgStats"); if(s){ const h=stats(); if(s.innerHTML!==h) s.innerHTML=h; } },3000);

/* SCORING v1 read-outs (tracker §0.6): one per 15-minute slot 09:45–15:45 ET while this page is open. Each config is
   computed in its own task (the page stays responsive) and the record is posted once, append-only, to the test server. */
const done=new Set(); let busy=false;
async function readout(){ if(busy||!st.live||!st.live.SPX) return; const c=liveClock(), m=c.h*60+c.m;
  const close=tgSessionClose(); if(m<MAP_OPEN+15||m>Math.min(close,16*60)-15+3) return; const slot=Math.floor(m/15)*15; if(m-slot>3) return;
  const key=c.date+" "+tlabel(slot); if(done.has(key)) return; done.add(key); busy=true;
  try{ const rec=tgReadoutMeta(); if(!rec) return; rec.slot=tlabel(slot); rec.cfgs={}; const t0=performance.now();
    for(const [name,cfg] of TG_CFGS) for(const sm of [false,true]){ await new Promise(r=>setTimeout(r,0)); rec.cfgs[name+(sm?"|S":"|P")]=tgReadoutCfg(cfg,sm); }
    rec.ms=Math.round(performance.now()-t0); rec.current_toggles=Object.assign({},TG); rec.user_smile=!!st.smileOn; post("readout",rec); }
  finally{ busy=false; } }
setInterval(readout,20000); setTimeout(readout,5000);
})();
