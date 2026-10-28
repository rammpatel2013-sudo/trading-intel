const { chromium } = require('playwright'); const fs=require('fs'); const BASIS=process.argv[3]||'oi';
(async()=>{ const b=await chromium.launch(); const ctx=await b.newContext({viewport:{width:1600,height:1000}}); await ctx.addInitScript(bs=>{ try{ localStorage.setItem('cockpit.basis',bs); }catch(e){} }, BASIS); const p=await ctx.newPage();
  await p.goto('http://127.0.0.1:8765/tos_cockpit_v2_replay.html',{waitUntil:'load'}); await p.waitForTimeout(5000);
  const out=await p.evaluate(()=>{ const c=window.__cockpit, st=c.st, d=st.live[st.sym], se=c.series(st.sym);
    const res={}; for(const book of ['0dte']){ const g=c.buildMap('gamma',se,d,book), v=c.buildMap('vanna',se,d,book), ch=c.buildMap('charm',se,d,book);
      const P=(m,f)=>m.prices[0]+f*m.dp; const cols=[];
      g.times.forEach((t,i)=>{ if(t<570||t>g.now) return;
        const gz=g.feats[i].z.map(f=>P(g,f)), vz=v.feats[i].z.map(f=>P(v,f)), cz=ch.feats[i].z.map(f=>P(ch,f));
        let gpk=null, gv=-1e99; g.feats[i].pk.forEach(k=>{ if(g.grid[i][k]>gv){ gv=g.grid[i][k]; gpk=g.prices[k]; } });
        cols.push({t,gz,vz,cz,gpk,gpks:g.feats[i].pk.map(k=>g.prices[k])}); });
      res[book]={cols,basis:st.basis,now:g.now}; }
    return res; });
  fs.writeFileSync(process.argv[2]+'/map_levels_20261028_'+BASIS+'.json',JSON.stringify(out)); console.log('cols',out['0dte'].cols.length,'basis',out['0dte'].basis, JSON.stringify(out['0dte'].cols[0]).slice(0,400)); await b.close(); })();
