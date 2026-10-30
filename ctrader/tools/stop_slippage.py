"""Stop-loss slippage breakdown for cTrader events.json exports: total, top-5 outliers, and
what net P/L would have been if stops had filled at their price.  Usage: python3 stop_slippage.py run1.json [run2.json ...]"""
import json,sys,datetime as dt
for path in sys.argv[1:]:
    with open(path) as f: ev=json.load(f)
    P={}
    for e in ev:
        pid=e['positionId']
        if pid is None: continue
        p=P.setdefault(pid,{})
        if e['event']=='Create Position': p.update(t=dt.datetime.fromtimestamp(e['time']/1000,dt.timezone.utc),side=e['type'],sl0=e['sl'],entry=e['entryPrice'])
        elif e['event']=='Position Modified (S/L)': p['be']=True
        elif e['closePrice'] is not None: p.update(ex=e['event'],close=e['closePrice'],sl=e['sl'],g=e['grossProfit'])
    T=[p for p in P.values() if 'g' in p]
    S=[]
    for p in T:
        if p['ex']=='Stop Loss Hit':
            s=(p['sl']-p['close']) if p['side']=='Buy' else (p['close']-p['sl'])
            S.append((s,p['t'],p))
    S.sort(key=lambda x:-x[0]); tot=sum(x[0] for x in S); net=sum(p['g'] for p in T)
    top=sum(x[0] for x in S[:5])
    print(f"{path.split('/')[-1][:8]}: trades={len(T)} net={net:.2f} slippage total={tot:.2f}; top5={top:.2f} ({100*top/tot:.0f}%); median={sorted(x[0] for x in S)[len(S)//2]:.2f}")
    print("   top5:", [(round(s,2), t.strftime('%a %d %b %H:%M')) for s,t,_ in S[:5]])
    print(f"   net if top5 slippages avoided: {net+top:.2f}; net if every stop filled exactly: {net+tot:.2f}")
