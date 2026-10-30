"""Trend-following pip scalper T1 (rules fixed before the first run).

  D1 filter : previous D1 close above SMA50 -> longs only, below -> shorts only.
  H1 filter : MicroLotBreakoutBot regime (EMA50, 5-bar slope > 0.3 ATR, close on the trend side) agrees with D1.
  M5 trigger: bar trades back to EMA20 and closes beyond it in the trend direction (EMA20 vs EMA50 aligned).
  Vol gate  : fixed SL pips must be 1.5-3.0 x M5 ATR(14) in pips, else skip (pips stay fixed, the gate decides when they fit).
  Sessions  : 07:00-16:30 UTC. Exit: SL / TP in pips, else 20:45 UTC.
"""
import sys, json
sys.path.insert(0, '.')
import numpy as np, pandas as pd
import scalp as S


def t1_signals(m1, sl_pips, gate=(1.5, 3.0)):
    b = S.bars(m1, '5min'); t = b.index + pd.Timedelta('5min')
    h = S.bars(m1, '1h'); e = S.ema(h.c, 50)
    pc = h.c.shift(); tr = np.maximum(h.h - h.l, np.maximum((h.h - pc).abs(), (h.l - pc).abs())); atr = tr.rolling(14).mean()
    sl = e - e.shift(5)
    reg = np.where(sl.abs() <= 0.3 * atr, 0, np.where((h.c > e) & (sl > 0), 1, np.where((h.c < e) & (sl < 0), -1, 0)))
    hreg = pd.Series(reg, index=h.index + pd.Timedelta('1h')).reindex(t, method='ffill').values
    d = S.bars(m1, '1D'); dd = np.sign(d.c - d.c.rolling(50).mean())
    dreg = pd.Series(dd.values, index=d.index + pd.Timedelta('1D')).reindex(t, method='ffill').values
    c, o, hi, lo = b.c.values, b.o.values, b.h.values, b.l.values
    e20, e50 = S.ema(b.c, 20).values, S.ema(b.c, 50).values
    pc5 = b.c.shift(); tr5 = np.maximum(b.h - b.l, np.maximum((b.h - pc5).abs(), (b.l - pc5).abs()))
    atrp = (tr5.rolling(14).mean() * 10).values
    hrs = t.hour + t.minute / 60.0
    sess = (hrs >= 7) & (hrs < 16.5)
    ok = sess & (sl_pips >= gate[0] * atrp) & (sl_pips <= gate[1] * atrp)
    lg = ok & (dreg > 0) & (hreg > 0) & (e20 > e50) & (lo <= e20) & (c > e20) & (c > o)
    sh = ok & (dreg < 0) & (hreg < 0) & (e20 < e50) & (hi >= e20) & (c < e20) & (c < o)
    return sorted([(t[i], 1, None) for i in np.where(lg)[0]] + [(t[i], -1, None) for i in np.where(sh)[0]])


if __name__ == '__main__':
    A = pd.read_pickle('m1.pkl'); B = S.load('../data/XAUUSD_M1_2026-03-26_to_2026-09-25.csv')
    for slp in (60, 100):
        for rr in (1.5, 2.0):
            parts = []
            for m in (A, B):
                parts.append(S.simulate(m, t1_signals(m, slp), slp, slp * rr, None))
            tr = pd.concat(parts)
            yrs = {y: (S.stats(g)['n'], S.stats(g)['expR'], S.stats(g)['PF']) for y, g in tr.groupby(tr.t.dt.year)}
            sides = {s: (S.stats(g)['n'], S.stats(g)['PF']) for s, g in tr.groupby('side')}
            print(f'SL {slp} TP {slp*rr:.0f}', S.stats(tr), yrs, 'sides', sides, flush=True)
