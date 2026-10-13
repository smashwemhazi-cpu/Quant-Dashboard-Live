"""Compare range-fade results across two cTrader events.json exports (by side, half-year and hour).
Usage: python3 compare_runs.py runA_events.json runB_events.json"""
import json, sys, datetime as dt, statistics as st
def load(path):
    with open(path) as f: ev=json.load(f)
    filled=set(e['positionId'] for e in ev if e['event']=='Stop Limit Order Filled')
    P={}
    for e in ev:
        pid=e['positionId']
        if pid is None: continue
        p=P.setdefault(pid,{})
        if e['event']=='Create Position':
            p.update(t=dt.datetime.fromtimestamp(e['time']/1000,dt.timezone.utc), side=e['type'], eng='BO' if pid in filled else 'RF')
        elif e['closePrice'] is not None:
            p['g']=e['grossProfit']
    return [p for p in P.values() if 'g' in p and 't' in p]
def s(name,xs):
    if not xs: print(f"{name:34s} n=0"); return
    g=[x['g'] for x in xs]; w=sum(v for v in g if v>0); l=-sum(v for v in g if v<=0)
    print(f"{name:34s} n={len(xs):3d} net={sum(g):7.2f} win%={100*sum(v>0 for v in g)/len(g):4.0f} PF={w/l if l else float('nan'):4.2f}")
cut=dt.datetime(2026,6,1,tzinfo=dt.timezone.utc)
for label,path in [("run1 (capped)",sys.argv[1]),("run3 (structural)",sys.argv[2])]:
    T=load(path); print("==",label)
    rf=[x for x in T if x['eng']=='RF']
    for half,sel in [("Jan-May",lambda x:x['t']<cut),("Jun-Oct",lambda x:x['t']>=cut)]:
        s(f"RF Sell {half}",[x for x in rf if x['side']=='Sell' and sel(x)])
        s(f"RF Buy  {half}",[x for x in rf if x['side']=='Buy' and sel(x)])
    s("12-13h UTC (all engines)",[x for x in T if x['t'].hour in (12,13)])
    s("07h UTC (all engines)",[x for x in T if x['t'].hour==7])
    s("RF excl 07h & 12-13h",[x for x in rf if x['t'].hour not in (7,12,13)])
