"""Rebuild live.json for a finished session so the v2 page replays it.
usage: python3 build_live.py <dir> <YYYY-MM-DD> <HH:MM ts>   (dir has site/live.json template, site/flow_*.ndjson, oi_*.json)"""
import json, sys
R, DAY, TS = sys.argv[1], sys.argv[2], sys.argv[3]
base=json.load(open(R+'/site/live.json')); OI=json.load(open(R+'/oi_%s.json'%DAY.replace('-','')))
F={}
for l in open(R+'/site/flow_%s.ndjson'%DAY.replace('-','')):
    try: j=json.loads(l)
    except Exception: continue
    if j.get('d')==DAY and j['t']<=TS: F.setdefault(j['s'],{})[j['t']]=j
live={'ts':'%sT%s:00'%(DAY,TS)}
for s in ('SPX','SPY','QQQ'):
    snaps=F[s]; last=snaps[max(snaps)]; first=snaps[min(t for t in snaps if t>='09:31')]
    idx={k:i for i,k in enumerate(last['k'])}; rows=[]
    for ks,(coi,poi) in sorted(OI[s]['oi'].items(), key=lambda a:float(a[0])):
        k=float(ks); k=int(k) if k==int(k) else k; i=idx.get(k)
        g=lambda f: (last[f][i] if (i is not None and last.get(f)) else 0) or 0
        rows.append({'k':k,'cOI':coi,'pOI':poi,'cV':g('cv'),'pV':g('pv'),'cIV':g('ci'),'pIV':g('pi'),'cM':g('cm'),'pM':g('pm'),'cL':g('cl'),'pL':g('pl'),'cB':g('cb'),'cA':g('ca'),'cZ':g('cz'),'pB':g('pb'),'pA':g('pa'),'pZ':g('pz'),'cD':g('cd'),'pD':g('pd')})
    fk=first['k']; q=min(range(len(fk)),key=lambda i:abs(fk[i]-first['x']))
    b=base[s]
    live[s]={'spot':last['x'],'open':first['x'],'pct':0,'iv':(first['ci'][q]+first['pi'][q])/2,'iv_open':0,'straddle':first['cm'][q]+first['pm'][q],'exp':DAY,'step':b.get('step',5 if s=='SPX' else 1),
             'rows':rows,'tl':[[t,snaps[t]['x']] for t in sorted(snaps) if t>='09:30'],'ser':[],'term':b.get('term',[]),'hv':b.get('hv',{})}
live['VIX']=base.get('VIX',{}); live['ES']=base.get('ES',{}); live['week']=base.get('week',{})
json.dump(live,open(R+'/site/live.json','w'))
