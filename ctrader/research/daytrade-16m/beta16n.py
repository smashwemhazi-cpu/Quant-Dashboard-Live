"""Rolling start of the 16n live beta (simladder profile day16n): start $388 on <start>, run up to 5 months, apply
the pre-registered beta rules in trade order: floor $340 hit = FAIL; at 20 trades PF < 0.8 = STOP;
at 40 trades PF >= 1.2 and peak-to-trough drawdown <= 15% = KEEP, else RETIRE. Trades = positions (ladder fills).

Usage: python beta16n.py <m1_bidask.pkl> <real|spread_price> <YYYY-MM-DD>
Output: spread|start|verdict|last trade date|trades|net $
"""
import sys
import os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd, simladder as L
m = pd.read_pickle(sys.argv[1]); so = None if sys.argv[2] == 'real' else float(sys.argv[2]); start = pd.Timestamp(sys.argv[3])
tr = L.run(m.loc[start:start + pd.DateOffset(months=5)], 'day16n', so, 388.0)
r = tr[~tr.paper] if len(tr) else tr
def pf(g):
    gp = g.pnl[g.pnl > 0].sum(); gl = -g.pnl[g.pnl < 0].sum(); return gp / gl if gl else 99
verdict, n = 'incomplete (<40 trades in 5 months)', len(r)
eq = 388 + r.pnl.cumsum() if n else pd.Series(dtype=float)
floor_i = np.where(eq.values < 340)[0] if n else []
for k in range(1, n + 1):
    if len(floor_i) and floor_i[0] == k - 1: verdict = f'FAIL floor hit at trade {k}'; break
    if k == 20 and pf(r.iloc[:20]) < 0.8: verdict = f'STOP at 20 trades (PF {pf(r.iloc[:20]):.2f})'; break
    if k == 40:
        e = 388 + r.pnl.iloc[:40].cumsum(); peak = np.maximum.accumulate(np.r_[388, e.values])[1:]
        dd = ((peak - e.values) / peak).max(); p = pf(r.iloc[:40])
        verdict = ('KEEP' if p >= 1.2 and dd <= 0.15 else 'RETIRE') + f' at 40 trades (PF {p:.2f}, DD {dd:.0%})'; break
end = r.t.iloc[min(k, n) - 1].date() if n else '-'
print(f'{sys.argv[2]}|{start.date()}|{verdict}|{end}|{min(k, n) if n else 0}|{r.pnl.iloc[:k].sum() if n else 0:+.2f}', flush=True)
