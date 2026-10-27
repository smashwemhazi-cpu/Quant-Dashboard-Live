"""Pip-based scalp and long-side research on XAUUSD M1 bid bars (UTC). Pip = 0.10 price.

Candidates and the grid are fixed before the first run (see CANDIDATES / GRID). Judge only on:
  positive expectancy after costs in 2020-22 AND 2023-25 AND 2026, and on neighbouring grid cells.
"""
import sys, json, math
import numpy as np, pandas as pd

PIP = 0.10
COST = dict(spread=0.30, slip=0.50, comm=0.07)  # price units at 0.01 lot ($1 per 1.00 move)
DAY = dict(start_h=1, end_h=20, flat=(20, 45), fri_end_h=18, max_trades=5, max_losses=3)


def load(path):
    if path.endswith('.pkl'):
        return pd.read_pickle(path)
    df = pd.read_csv(path)
    df.index = pd.to_datetime(df.iloc[:, 0]); df = df.iloc[:, 1:5]; df.columns = ['o', 'h', 'l', 'c']
    return df


def bars(m1, tf):
    return m1.resample(tf, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(s, n):
    d = s.diff(); up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def closed_series(b, s, tf):
    """Value known at the bar's close time."""
    return pd.Series(s.values, index=b.index + pd.Timedelta(tf))


def htf_bias(m1, tf, n):
    h = bars(m1, tf); e = ema(h.c, n)
    return closed_series(h, np.sign(h.c - e), tf)


def signals(m1, name):
    """Return list of (signal_time, side, exit_time or None)."""
    if name in ('S1', 'S2', 'S3'):
        b = bars(m1, '5min'); t = b.index + pd.Timedelta('5min')
        bias = htf_bias(m1, '1h', 50).reindex(t, method='ffill').values
        c, o, h, l = b.c.values, b.o.values, b.h.values, b.l.values
        if name == 'S1':  # trend pullback rejection of EMA20
            e20, e50 = ema(b.c, 20).values, ema(b.c, 50).values
            lg = (bias > 0) & (e20 > e50) & (l <= e20) & (c > e20) & (c > o)
            sh = (bias < 0) & (e20 < e50) & (h >= e20) & (c < e20) & (c < o)
        elif name == 'S2':  # fresh 12-bar breakout
            hh = b.h.shift(1).rolling(12).max().values; ll = b.l.shift(1).rolling(12).min().values
            pc = b.c.shift(1).values
            lg = (bias > 0) & (c > hh) & (pc <= hh)
            sh = (bias < 0) & (c < ll) & (pc >= ll)
        else:  # RSI(2) dip in the trend
            r = rsi(b.c, 2).values
            lg = (bias > 0) & (r < 10)
            sh = (bias < 0) & (r > 90)
        out = [(t[i], 1, None) for i in np.where(lg)[0]] + [(t[i], -1, None) for i in np.where(sh)[0]]
        return sorted(out)
    if name == 'L1':  # long only: H4 uptrend, H1 RSI(2) < 10, exit on H1 close above EMA5
        b = bars(m1, '1h'); t = b.index + pd.Timedelta('1h')
        bias = htf_bias(m1, '4h', 50).reindex(t, method='ffill').values
        r = rsi(b.c, 2).values; e5 = ema(b.c, 5).values; c = b.c.values
        ex = np.where(c > e5)[0]
        out = []
        for i in np.where((bias > 0) & (r < 10))[0]:
            k = ex[np.searchsorted(ex, i + 1)] if np.searchsorted(ex, i + 1) < len(ex) else None
            out.append((t[i], 1, t[k] if k is not None else None))
        return out
    if name == 'L2':  # long only day trade: D1 close > SMA50 and yesterday a down day -> buy 07:00 UTC
        d = bars(m1, '1D'); sma = d.c.rolling(50).mean()
        ok = (d.c > sma) & (d.c < d.o)
        out = []
        for day in d.index[ok.values]:
            nxt = day + pd.Timedelta(days=1)
            while nxt.dayofweek >= 5: nxt += pd.Timedelta(days=1)
            out.append((nxt + pd.Timedelta(hours=7), 1, None))
        return out
    raise ValueError(name)


def simulate(m1, sigs, sl_pips, tp_pips, max_hold_min, overnight=False, equity=None, risk_pct=3.0):
    t = m1.index; tv = t.values; O, H, L, C = (m1[k].values for k in 'ohlc')
    th, tm, wd = t.hour.values, t.minute.values, t.dayofweek.values
    dates = t.normalize().values
    spr, slip, comm = COST['spread'], COST['slip'], COST['comm']
    sl, tp = sl_pips * PIP, (tp_pips * PIP if tp_pips else None)
    fh, fm = DAY['flat']; busy = 0; daycnt = {}; trades = []; eq = equity
    for st, side, ext in sigs:
        i = t.searchsorted(st)
        if i >= len(t) or i < busy: continue
        if not (DAY['start_h'] <= th[i] < DAY['end_h']) or (wd[i] == 4 and th[i] >= DAY['fri_end_h']): continue
        if (tv[i] - np.datetime64(st)) > np.timedelta64(10, 'm'): continue  # data gap
        dc = daycnt.setdefault(dates[i], [0, 0])
        if dc[0] >= DAY['max_trades'] or dc[1] >= DAY['max_losses']: continue
        lots = 0.01
        if equity is not None:
            lots = math.floor(eq * risk_pct / 100 / sl + 1e-9) / 100
            if lots < 0.01: continue
        fill = O[i] + (spr if side == 1 else 0)  # long buys the ask, short sells the bid
        stop = fill - side * sl; targ = fill + side * tp if tp else None
        end = (np.datetime64(st) + np.timedelta64(max_hold_min, 'm')) if max_hold_min else None
        ext64 = np.datetime64(ext) if ext is not None else None
        j = i; res = None
        while j < len(t):
            if not overnight and (dates[j] != dates[i] or th[j] > fh or (th[j] == fh and tm[j] >= fm)):
                px = O[j] if dates[j] == dates[i] else C[j - 1]
                res = side * (px - fill) - (spr if side == -1 else 0); break
            if overnight and wd[j] == 4 and th[j] >= 20:
                res = side * (O[j] - fill) - (spr if side == -1 else 0); break
            if (end is not None and tv[j] >= end) or (ext64 is not None and tv[j] >= ext64):
                res = side * (O[j] - fill) - (spr if side == -1 else 0); break
            bh, bl = (H[j], L[j]) if side == 1 else (H[j] + spr, L[j] + spr)  # long exits on bid, short on ask
            if (side == 1 and bl <= stop) or (side == -1 and bh >= stop):
                gap = (side == 1 and O[j] < stop) or (side == -1 and O[j] + spr > stop)
                px = (O[j] if side == 1 else O[j] + spr) if gap else stop
                res = side * (px - fill) - slip; break
            if targ is not None and ((side == 1 and bh >= targ) or (side == -1 and bl <= targ)):
                res = side * (targ - fill); break
            j += 1
        if res is None: break
        pips = (res - comm) / PIP
        pnl = (res - comm) * lots * 100
        if eq is not None: eq += pnl
        dc[0] += 1; dc[1] += pnl < 0
        trades.append((t[i], side, pips, pips / sl_pips, pnl, eq if eq is not None else 0.0))
        busy = j + 1
    return pd.DataFrame(trades, columns=['t', 'side', 'pips', 'R', 'pnl', 'equity'])


def stats(tr):
    if tr.empty: return dict(n=0)
    gp = tr.pnl[tr.pnl > 0].sum(); gl = -tr.pnl[tr.pnl < 0].sum()
    return dict(n=len(tr), win=round(100 * (tr.pnl > 0).mean(), 1), expR=round(tr.R.mean(), 3),
                pips=round(tr.pips.mean(), 1), PF=round(gp / gl, 2) if gl else 99, net=round(tr.pnl.sum(), 2))


def periods(tr):
    y = tr.t.dt.year
    return {'20-22': stats(tr[y <= 2022]), '23-25': stats(tr[(y >= 2023) & (y <= 2025)]), '26': stats(tr[y >= 2026])}
