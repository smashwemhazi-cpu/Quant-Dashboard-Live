"""Analyze a cTrader backtest events.json export: per-engine/side/hour/month stats,
stop slippage, out-of-sample split and t-stat.  Usage: python3 analyze_events.py events.json [YYYY-MM-DD split]"""
import json, sys, datetime as dt, collections, statistics as st
with open(sys.argv[1]) as f:
    ev = json.load(f)
filled_from_order = set(e['positionId'] for e in ev if e['event']=='Stop Limit Order Filled')
pos = {}
for e in ev:
    pid = e['positionId']
    if pid is None: continue
    p = pos.setdefault(pid, {'id':pid})
    if e['event']=='Create Position':
        p.update(t0=dt.datetime.fromtimestamp(e['time']/1000, dt.timezone.utc).replace(tzinfo=None), side=e['type'], entry=e['entryPrice'], sl0=e['sl'],
                 engine='BO' if pid in filled_from_order else 'RF')
    elif e['event']=='Position Modified (S/L)':
        p['be']=True
    elif e['event'] in ('Stop Loss Hit','Take Profit Hit','Position closed','Position Closed') or e['closePrice'] is not None and e['event']!='Create Position':
        p.update(exit=e['event'], close=e['closePrice'], gross=e['grossProfit'], sl_at_exit=e['sl'], t1=dt.datetime.fromtimestamp(e['time']/1000, dt.timezone.utc).replace(tzinfo=None))
T=[p for p in pos.values() if 'gross' in p and 'entry' in p]
for p in T:
    risk=abs(p['entry']-p['sl0']); p['risk']=risk
    p['R']=p['gross']/risk if risk else 0
    p['mins']=(p['t1']-p['t0']).total_seconds()/60
    if p['exit']=='Stop Loss Hit':
        slip = (p['sl_at_exit']-p['close']) if p['side']=='Buy' else (p['close']-p['sl_at_exit'])
        p['slip']=slip
    p['kind']= 'TP' if p['exit']=='Take Profit Hit' else ('BE-scratch' if p.get('be') and p['exit']=='Stop Loss Hit' else ('SL' if p['exit']=='Stop Loss Hit' else p['exit']))
print("trades parsed:",len(T), "events:", collections.Counter(e['event'] for e in ev))
def summ(name, xs):
    if not xs: return
    g=[x['gross'] for x in xs]; w=[x for x in g if x>0]; l=[x for x in g if x<=0]
    print(f"{name:22s} n={len(xs):3d} net={sum(g):7.2f} win%={100*len(w)/len(xs):5.1f} avgW={st.mean(w) if w else 0:5.2f} avgL={st.mean(l) if l else 0:6.2f} PF={(sum(w)/-sum(l)) if l and sum(l) else float('nan'):4.2f} avgR={st.mean(x['R'] for x in xs):5.2f} avgRisk$={st.mean(x['risk'] for x in xs):4.2f} medHold={st.median(x['mins'] for x in xs):5.0f}m")
summ("ALL",T)
for k in ['BO','RF']: summ("engine "+k,[x for x in T if x['engine']==k])
for k in ['Buy','Sell']: summ("side "+k,[x for x in T if x['side']==k])
for e in ['BO','RF']:
  for k in ['Buy','Sell']: summ(f"{e} {k}",[x for x in T if x['engine']==e and x['side']==k])
print("exit kinds:",collections.Counter(x['kind'] for x in T))
for k in ['TP','SL','BE-scratch']: summ("exit "+k,[x for x in T if x['kind']==k])
sl=[x['slip'] for x in T if x['kind']=='SL']
print(f"SL slippage beyond stop (price): mean={st.mean(sl):.2f} median={st.median(sl):.2f} max={max(sl):.2f}; as % of risk: mean={100*st.mean(x['slip']/x['risk'] for x in T if x['kind']=='SL'):.0f}%")
sc=[x for x in T if x['kind']=='BE-scratch']; print("BE scratches gross:", round(sum(x['gross'] for x in sc),2))
print("hour UTC buckets:")
hb=collections.defaultdict(list)
for x in T: hb[x['t0'].hour].append(x)
for h in sorted(hb): summ(f"  {h:02d}h",hb[h])
mb=collections.defaultdict(list)
for x in T: mb[x['t0'].strftime('%Y-%m')].append(x)
print("monthly:")
for m in sorted(mb): summ("  "+m,mb[m])
print("hold-time of SL losers (min):", sorted(round(x['mins']) for x in T if x['kind']=='SL')[:40])
# would-be outcome if no breakeven: count BE trades that later hit TP vs scratched
print("TP winners avg R:", round(st.mean(x['R'] for x in T if x['kind']=='TP'),2))
print("\n=== out-of-sample check (split at 2026-06-01) ===")
cut=dt.datetime.fromisoformat(sys.argv[2]) if len(sys.argv)>2 else dt.datetime(2026,6,1)
good=lambda x: x['t0'].hour in (9,10)
for name,sel in [("09-10h",good),("other hours",lambda x: not good(x)),("Sell",lambda x:x['side']=='Sell'),("Buy",lambda x:x['side']=='Buy')]:
    summ("IS  "+name,[x for x in T if x['t0']<cut and sel(x)])
    summ("OOS "+name,[x for x in T if x['t0']>=cut and sel(x)])
import math
g=[x['gross'] for x in T]; m=st.mean(g); s=st.stdev(g)
print(f"\nper-trade mean={m:.3f} sd={s:.2f} t-stat={m/(s/math.sqrt(len(g))):.2f}  (|t|<2 => no evidence of edge either way)")
# cost decomposition
slipcost=sum(x['slip'] for x in T if x['kind']=='SL')
print(f"stop slippage total cost ≈ ${slipcost:.2f} (1 oz => $1 per 1.00) vs gross result ${sum(g):.2f}")
# win rate needed
W=[x['gross'] for x in T if x['gross']>0]; L=[-x['gross'] for x in T if x['gross']<=0]
b=st.mean(W)/st.mean(L); print(f"payoff ratio={b:.2f} -> breakeven win rate={100/(1+b):.1f}% vs actual {100*len(W)/len(T):.1f}%")
# max losing streak
ms=c=0
for x in sorted(T,key=lambda x:x['t0']):
    c = c+1 if x['gross']<=0 else 0; ms=max(ms,c)
print("max consecutive non-winning trades:",ms)
