const fs=require('fs'); eval(fs.readFileSync(process.argv[2]+'/charm_funcs.js','utf8')+';globalThis.charmFlow=charmFlow;globalThis.charmFlips=charmFlips;globalThis.stkIV=stkIV;globalThis.HZN=HZN;globalThis.EXP_MIN=EXP_MIN;');
const OI=JSON.parse(fs.readFileSync(process.argv[2]+'/oi_20261028.json','utf8')).SPX.oi;
const lines=fs.readFileSync(process.argv[2]+'/site/flow_20261028.ndjson','utf8').split('\n').filter(Boolean).map(l=>JSON.parse(l)).filter(j=>j.s==='SPX'&&j.t>='09:30'&&j.t<='15:59');
const mm=t=>{const [h,m]=t.split(':').map(Number);return h*60+m;};
const out=[];
for(const j of lines){ const S=j.x; let q=0; j.k.forEach((k,i)=>{ if(Math.abs(k-S)<Math.abs(j.k[q]-S)) q=i; });
  const atmC=j.ci[q], atmP=j.pi[q]; const atm=(atmC>0&&atmP>0)?(atmC+atmP)/2:(atmC||atmP||0.15);
  const rows=j.k.map((k,i)=>{ const o=OI[String(Number(k).toFixed(1))]||[0,0]; return {k:+k, cOI:o[0], pOI:o[1], cIV:j.ci[i], pIV:j.pi[i]}; });
  const rp=rows.map(r=>({k:r.k, net:(r.cOI||0)-(r.pOI||0), sig:stkIV(r,S,atm)})).filter(r=>r.net&&r.k>0);
  const now=mm(j.t); const v=charmFlow(S,rp,now,HZN); const fl=charmFlips(S,rp,now);
  out.push({t:j.t,S,es:+v.toFixed(1),usd_m:+(Math.abs(v)*50*S/1e6).toFixed(0),flips:fl.map(f=>+f.p.toFixed(1)), ftxt:fl.map(f=>f.txt)}); }
fs.writeFileSync(process.argv[2]+'/charm_clock.json',JSON.stringify(out));
const pick=['09:30','10:00','10:30','11:00','11:30','12:00','12:30','13:00','13:30','13:55','14:05','14:15','14:30','14:45','15:00','15:15','15:30','15:45','15:59'];
for(const o of out) if(pick.includes(o.t)) console.log(o.t, 'spot',o.S.toFixed(1),'next60',(o.es>0?'BUY ':'SELL ')+Math.abs(o.es).toFixed(0)+' ES (~$'+o.usd_m+'M)','flips',o.flips.join(' / '));
let ch=[]; for(let i=1;i<out.length;i++) if(Math.sign(out[i].es)!==Math.sign(out[i-1].es)) ch.push(out[i].t+(out[i].es>0?' sell→BUY':' buy→SELL'));
console.log('sign changes:',ch.join(' | ')||'none');
