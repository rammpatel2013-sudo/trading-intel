"""Seven locked tests (close-prediction-architecture §9, LOCKED 2026-10-27) scored on one session.
v1 conventions where the spec is silent are listed in CONV and printed with the result; they are
NOT tuned on outcomes. Levels = the cockpit page's own 0DTE map (buildMap, OI basis), per 2-min column."""
import json, math, sys
R=sys.argv[1]; BASIS=sys.argv[2] if len(sys.argv)>2 else 'oi'; DAY='2026-10-28'
CONV={
 'levels':"v2 page buildMap('gamma'/'vanna','0dte'), basis OI, column at/before the minute; gz = gamma zero nearest spot; vanna top/bottom only when the column has exactly 2 vanna zeros (as Density draws them); gamma green peak = largest gamma local max",
 'spot':"ToS v2 minute log x",
 'open':"a 09:30 entry fills at 09:31: the 09:30 log row and quotes are pre-open (row x = prior close; quotes unusable)",
 'fills':"Convex raw bid/ask at that minute (nearest earlier minute within 3 if missing); buy at ask, sell at bid, both ways; x100 per contract",
 'fly':"long fly, calls if centre >= spot else puts, strikes rounded to 5",
 'fly_exit':"profit target = midpoint of the 20-30% band (25%) on the debit; stop when the fly is worth <= 50% of the debit; else close at 15:30",
 'stretch':"|spot - gamma zero| >= 1 SD, SD = 0DTE ATM straddle / 0.7979 (the Density tab's 1-sigma)",
 'credit':"one iron condor per entry (both sides together); stop = buy back when it costs 2x the credit; target 50%; time exit 14:00",
 'debit':"entries 09:30, 10:00, 10:30 (before 11:00); long call = gamma green peak + 20 rounded up to 5; exit at 4x debit else 15:00",
 'oldfly':"short iron fly 25 wide on the gamma zero, credit >= $1,800 else no trade, buy back at <= $1,000, else 14:00",
}
L=json.load(open(R+'/map_levels_20261028_%s.json'%BASIS))['0dte']['cols']
Q=json.load(open(R+'/quotes_20261028.json'))['q']
F={}
for l in open(R+'/site/flow_20261028.ndjson'):
    j=json.loads(l)
    if j['s']=='SPX': F[j['t']]=j
def mm(t): h,m=t.split(':'); return int(h)*60+int(m)
def hh(m): return '%02d:%02d'%(m//60,m%60)
col={c['t']:c for c in L}
def lv(m):
    t=m-(m%2)
    while t>=570 and t not in col: t-=2
    c=col.get(t); S=F.get(hh(m),{}).get('x')
    if not c or S is None: return None
    gz=min(c['gz'],key=lambda z:abs(z-S)) if c['gz'] else None
    vt=vb=None
    if len(c['vz'])==2: vt,vb=max(c['vz']),min(c['vz'])
    return {'S':S,'gz':gz,'vt':vt,'vb':vb,'gpk':c['gpk']}
def quote(m,k,cp):
    for q in range(4):
        t=hh(m-q)
        if t in Q and str(int(k)) in Q[t]:
            cb,ca,pb,pa=Q[t][str(int(k))]
            return (cb,ca) if cp=='C' else (pb,pa)
    return None
def r5(x): return 5*round(x/5)
def sd_at(m):
    j=F.get(hh(m)); 
    if not j: return None
    q=min(range(len(j['k'])),key=lambda i:abs(j['k'][i]-j['x'])); st=(j['cm'][q] or 0)+(j['pm'][q] or 0)
    return st/0.7979 if st>0 else None
# legs: list of (qty, strike, 'C'/'P'); qty>0 long
def price(m,legs,open_side):
    """cost to OPEN (open_side=True) or value received to CLOSE (False), conservative."""
    tot=0
    for q,k,cp in legs:
        bq=quote(m,k,cp)
        if not bq or bq[1] is None or bq[0] is None: return None
        b,a=bq
        if open_side: tot+= q*(a if q>0 else b)
        else: tot+= q*(b if q>0 else a)
    return tot
def run_trade(name,m0,legs,exit_rule,t_exit):
    c0=price(m0,legs,True)
    if c0 is None: return None
    for m in range(m0+1,t_exit+1):
        v=price(m,legs,False)
        if v is None: continue
        r=exit_rule(c0,v)
        if r: return {'test':name,'in':hh(m0),'out':hh(m),'why':r,'open':round(c0,2),'close':round(v,2),'pl':round((v-c0)*100,0)}
    m=t_exit
    while m>m0 and price(m,legs,False) is None: m-=1
    v=price(m,legs,False)
    return {'test':name,'in':hh(m0),'out':hh(m),'why':'time','open':round(c0,2),'close':round(v,2),'pl':round((v-c0)*100,0)}
trades=[]
def fly_legs(K,w,S):
    cp='C' if K>=S else 'P'
    return [(1,K-w,cp),(-2,K,cp),(1,K+w,cp)]
fly_exit=lambda c0,v: 'target +25%' if v>=c0*1.25 else ('stop -50%' if v<=c0*0.5 else None)
# tests 1-4: transition-touch trigger
m=600; cool=-1
while m<=780:
    x=lv(m)
    if x and m>=cool:
        trans=[z for z in (x['gz'],x['vt'],x['vb']) if z is not None]
        if any(abs(x['S']-z)<=5 for z in trans):
            w=25 if m<660 else 20
            for name,K in (('1 Fly @ vanna top',x['vt']),('2 Fly @ gamma zero',x['gz']),('3 Fly @ vanna bottom',x['vb'])):
                if K is None: continue
                t=run_trade(name,m,fly_legs(r5(K),w,x['S']),fly_exit,930)
                if t: t.update(level=round(K,1),spot=x['S']); trades.append(t)
            sd=sd_at(m)
            if x['gz'] is not None and sd and abs(x['S']-x['gz'])>=sd:
                t=run_trade('4 Fly @ gamma zero +1SD',m,fly_legs(r5(x['gz']),w,x['S']),fly_exit,930)
                if t: t.update(level=round(x['gz'],1),spot=x['S']); trades.append(t)
            trades.append({'test':'_trigger','in':hh(m),'spot':x['S'],'near':[round(z,1) for z in trans if abs(x['S']-z)<=5],'sd':sd and round(sd,1),'gz':x['gz'] and round(x['gz'],1)})
            cool=m+60
    m+=1
# test 5
for m in (571,600,630,660,690):
    x=lv(m)
    if not x or x['vt'] is None or x['vb'] is None: trades.append({'test':'5 Credit spread','in':hh(m),'why':'no vanna rails'}); continue
    sc=5*math.ceil((x['vt']+20)/5); sp=5*math.floor((x['vb']-20)/5)
    legs=[(-1,sc,'C'),(1,sc+25,'C'),(-1,sp,'P'),(1,sp-25,'P')]
    c0=price(m,legs,True)
    if c0 is None: continue
    credit=-c0
    def ex(c0_,v,credit=credit):
        cost=-v
        return 'target 50%' if cost<=0.5*credit else ('stop 2x credit' if cost>=2*credit else None)
    t=run_trade('5 Credit spread',m,legs,ex,840); t.update(level='%s/%s'%(round(x['vt'],1),round(x['vb'],1)),spot=x['S'],strikes='%d/%d C, %d/%d P'%(sc,sc+25,sp,sp-25),credit=round(credit,2)); trades.append(t)
# test 6
for m in (571,600,630):
    x=lv(m)
    if not x or x['gpk'] is None: continue
    kl=5*math.ceil((x['gpk']+20)/5); legs=[(1,kl,'C'),(-1,kl+25,'C')]
    t=run_trade('6 Debit 1:5',m,legs,lambda c0,v: 'target 4x' if v>=4*c0 else None,900)
    if t: t.update(level=x['gpk'],spot=x['S'],strikes='%d/%d C'%(kl,kl+25)); trades.append(t)
# test 7
for m in [571]+list(range(600,781,30)):
    x=lv(m)
    if not x or x['gz'] is None: continue
    K=r5(x['gz']); legs=[(-1,K,'C'),(-1,K,'P'),(1,K+25,'C'),(1,K-25,'P')]
    c0=price(m,legs,True)
    if c0 is None: continue
    credit=-c0
    if credit<18.0: trades.append({'test':'7 Old fly','in':hh(m),'why':'credit %.2f < 18.00 - no trade'%credit,'level':round(x['gz'],1),'spot':x['S']}); continue
    t=run_trade('7 Old fly',m,legs,lambda c0,v: 'bought back <= $1,000' if -v<=10.0 else None,840); t.update(level=round(x['gz'],1),spot=x['S'],credit=round(credit,2)); trades.append(t)
json.dump({'conv':CONV,'basis':BASIS,'trades':trades},open(R+'/seven_tests_20261028_%s.json'%BASIS,'w'),indent=1)
print('BASIS',BASIS)
# summary
def risk(t):
    if t['test'].startswith(('1','2','3','4','6')): return t['open']*100
    if t['test'].startswith('5'): return (25-t['credit'])*100
    if t['test'].startswith('7'): return (25-t['credit'])*100
names=['1 Fly @ vanna top','2 Fly @ gamma zero','3 Fly @ vanna bottom','4 Fly @ gamma zero +1SD','5 Credit spread','6 Debit 1:5','7 Old fly']
tot_n=tot_r=tot_p=0
print('%-26s %3s %9s %9s %8s %6s'%('test','n','risk','P/L','avg','wins'))
for n in names:
    ts=[t for t in trades if t['test']==n and 'pl' in t]
    rk=sum(risk(t) for t in ts); pl=sum(t['pl'] for t in ts); w=sum(1 for t in ts if t['pl']>0)
    tot_n+=len(ts); tot_r+=rk; tot_p+=pl
    print('%-26s %3d %9.0f %9.0f %8s %6s'%(n,len(ts),rk,pl,('%.0f'%(pl/len(ts))) if ts else '-', '%d/%d'%(w,len(ts)) if ts else '-'))
print('%-26s %3d %9.0f %9.0f   %% of $50k: %+.2f%%'%('TOTAL',tot_n,tot_r,tot_p,tot_p/50000*100))
for t in trades:
    if t['test']=='_trigger' or 'pl' in t: print({k:t[k] for k in t if k in ('test','in','out','why','open','close','pl','level','spot','strikes','near','sd','gz')})
