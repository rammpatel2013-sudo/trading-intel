/* TEST BQ (2026-11-20, hedge-math round 2 item 1 - claude/hedge-math-review-round2-20261119.md) - MORNING-BOOK CLEAN
   + IV PROVENANCE. Toggle "Book clean" (BQ v0), OFF by default; off = production maths. Scored with the other configs.
   The 1-7 DTE book is the 08:35 pull (ti.json near_book; vendor IV per side). Pre-open, calls and puts of the same
   strike were 18-55 vol pts apart on some expiries. Per book row:
     valid side  = IV in 3%..150%
     observed     both sides valid and within 10 vol pts -> the out-of-the-money side (production's choice)
     conflict     both valid, > 10 pts apart -> the side closer to the expiry's observed neighbours (else OTM side)
     paired-side  only one side valid -> that side (same strike and forward -> same IV under parity)
     interpolated neither side valid -> linear in strike from the nearest resolved strikes of the same expiry
                  (at most 3 strikes away on each side; one-sided -> unavailable)
     unavailable  nothing usable -> the row is left OUT of the map (never guessed)
     stale        (flag on top) the live Week chain has this contract and differs by > 10 vol pts
   No rejected value ever reaches the map. Counts go to the read-out ("Book quality") and TG_STATS.bq. */
const TG_BQ={key:null,nb:null,m:null,stats:null}, BQ_LO=0.03, BQ_HI=1.5, BQ_GAP=0.10, BQ_REACH=3;
function tgBqValid(x){ return x>=BQ_LO&&x<=BQ_HI; }
function tgBqBuild(nb,S){ const key=(st.ti&&st.ti.asof||"")+"|"+st.sym+"|"+Math.round(S); if(TG_BQ.key===key&&TG_BQ.nb===nb) return TG_BQ;   // rows are cached by object: a reloaded ti.json is a new book
  const m=new Map(), st0={observed:0,conflict:0,paired:0,interpolated:0,unavailable:0,stale:0,rows:0};
  const byE=new Map(); for(const r of nb.rows){ const id=r.e+"|"+(r.set||"PM"); if(!byE.has(id)) byE.set(id,[]); byE.get(id).push(r); }
  for(const rows of byE.values()){ rows.sort((a,b)=>a.k-b.k); const res=rows.map(r=>{ const c=+r.cIV||0, p=+r.pIV||0, cv=tgBqValid(c), pv=tgBqValid(p), otm=r.k>=S?c:p, oth=r.k>=S?p:c;
        if(cv&&pv) return Math.abs(c-p)<=BQ_GAP?{sg:otm,prov:"observed"}:{sg:null,prov:"conflict",c,p};
        if(cv||pv){ const v=cv?c:p; return {sg:v,prov:(r.k>=S?cv:pv)?"observed":"paired"}; }
        return {sg:null,prov:"none"}; });
    const obsIdx=res.map((x,i)=>x.prov==="observed"?i:-1).filter(i=>i>=0);
    const near=(i)=>{ let a=null,b=null; for(const j of obsIdx){ if(j<i) a=j; else if(j>i){ b=j; break; } } if(a!=null&&b!=null){ const ra=rows[a],rb=rows[b]; return res[a].sg+(res[b].sg-res[a].sg)*(rows[i].k-ra.k)/((rb.k-ra.k)||1); } return a!=null?res[a].sg:b!=null?res[b].sg:null; };
    res.forEach((x,i)=>{ if(x.prov==="conflict"){ const ref=near(i); x.sg=ref==null?(rows[i].k>=S?x.c:x.p):(Math.abs(x.c-ref)<=Math.abs(x.p-ref)?x.c:x.p); } });
    res.forEach((x,i)=>{ if(x.prov!=="none") return; let a=i-1,b=i+1; while(a>=0&&!(res[a].sg>0&&res[a].prov!=="interpolated")) a--; while(b<res.length&&!(res[b].sg>0&&res[b].prov!=="interpolated")) b++;
      if(a>=0&&b<res.length&&i-a<=BQ_REACH&&b-i<=BQ_REACH){ x.sg=res[a].sg+(res[b].sg-res[a].sg)*(rows[i].k-rows[a].k)/((rows[b].k-rows[a].k)||1); x.prov="interpolated"; } else x.prov="unavailable"; });
    res.forEach((x,i)=>{ const r=rows[i]; st0.rows++; st0[x.prov]++; if(x.sg>0&&Math.abs(r.k-S)<=100){ const lv=tgWeekLiveSg(r,S); if(lv>0&&Math.abs(lv-x.sg)>BQ_GAP){ x.stale=true; st0.stale++; } } m.set(r, x); }); }
  TG_BQ.key=key; TG_BQ.nb=nb; TG_BQ.m=m; TG_BQ.stats=st0; TG_STATS.bq=st0; return TG_BQ; }
function tgBqSg(r,S){ const nb=st.ti&&st.ti.near_book&&st.ti.near_book[st.sym]; if(!nb||!nb.rows) return otmIV(r.k,S,r.cIV||0,r.pIV||0);
  const x=tgBqBuild(nb,S).m.get(r); return x&&x.sg>0&&x.prov!=="unavailable"?x.sg:0; }
function tgBqLine(){ if(!TG.bq) return ""; const nb=st.ti&&st.ti.near_book&&st.ti.near_book[st.sym], S=st.live&&st.live[st.sym]&&st.live[st.sym].spot||0;
  if(!nb||!nb.rows||!(S>0)) return `<div class="bl"><b>TEST Book quality:</b> no 08:35 book.</div>`;
  const s=tgBqBuild(nb,S).stats, pc=v=>Math.round(100*v/Math.max(1,s.rows))+"%";
  return `<div class="bl"><b>TEST Book quality (08:35 book, ${s.rows} strikes):</b> ${pc(s.observed)} observed · ${pc(s.conflict)} call/put conflict resolved · ${pc(s.paired)} other side · ${pc(s.interpolated)} filled · <b>${pc(s.unavailable)} left out</b>${s.stale?` · ${s.stale} near-spot strikes > 10 vol pts off the live chain (stale)`:""}.</div>`; }
