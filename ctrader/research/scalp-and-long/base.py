import sys; sys.path.insert(0,'.')
import pandas as pd, scalp as S
A=pd.read_pickle('m1.pkl'); B=S.load('../data/XAUUSD_M1_2026-03-26_to_2026-09-25.csv')
def daily(m, cond):
    d=S.bars(m,'1D'); sma=d.c.rolling(50).mean(); ok=cond(d,sma); out=[]
    for day in d.index[ok.values]:
        n=day+pd.Timedelta(days=1)
        while n.dayofweek>=5: n+=pd.Timedelta(days=1)
        out.append((n+pd.Timedelta(hours=7),1,None))
    return out
V={'L0 buy every day':lambda d,s:d.c>0,'L0b every day in uptrend':lambda d,s:d.c>s,'L2 uptrend+down day':lambda d,s:(d.c>s)&(d.c<d.o),'L2x uptrend+UP day':lambda d,s:(d.c>s)&(d.c>d.o)}
for k,f in V.items():
    for sl in (60,100):
        tr=pd.concat([S.simulate(m,daily(m,f),sl,None,None) for m in (A,B)])
        yr={y:(S.stats(g)['n'],S.stats(g)['expR']) for y,g in tr.groupby(tr.t.dt.year)}
        print(k,sl,S.stats(tr),yr)
