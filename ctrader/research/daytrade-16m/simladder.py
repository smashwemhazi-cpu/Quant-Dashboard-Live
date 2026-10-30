"""Generalised replica of the MicroLot ladder engine (shared by MicroLotBreakoutBot 16m and MicroLotAsianBot 16j)
for M1 bid/ask bars. Adds to sim16m.py: the flat-regime FADE ladder (target = middle band), the weekly loss cap,
and the equity-curve filter with paper (shadow) trades, all as in the C# code.

Profiles:
  asian16j : sessions 01:00-05:00 UTC, fade on, RR cap 2.5, risk 4%, weekly cap 4%, equity filter (10), commission $8/lot
  day16m   : sessions 08:00-11:00 + 14:00-16:30, fade off, RR cap 2.5, risk 3%, no weekly cap, no filter, commission $7/lot

Usage: python simladder.py <m1_bidask.pkl> <profile> [real|spread_price] [start_equity]
"""
import sys
import numpy as np, pandas as pd

BASE = dict(atr=14, bb=20, bbsd=2.0, ema=50, slope_bars=5, flat_slope=0.3, depth=1.0, stop_atr=0.75,
            min_stop_atr=1.0, orders=3, reward=2.0, max_rr=2.5, expiry_bars=3, floor_dist=0.8 / 0.2,
            max_spread_pips=6.0, spread_of_risk=0.15, hurdle=3.0, slip=0.30, be_r=1.0, max_setups=3,
            max_streak=2, daily_loss_pct=6.0, flat=20 * 60 + 30, fri_flat=19 * 60 + 30, eq_floor=0.0)
PROFILES = {
    'asian16j': dict(sess=((60, 300),), fade=True, risk_pct=4.0, weekly_pct=4.0, eq_filter=10, comm=0.08),
    'day16m': dict(sess=((480, 660), (840, 990)), fade=False, risk_pct=3.0, weekly_pct=0.0, eq_filter=0, comm=0.07),
}


def regime_series(bid, P):
    h = bid.resample('1h', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    e = h.c.ewm(span=P['ema'], adjust=False).mean()
    pc = h.c.shift(); tr = np.maximum(h.h - h.l, np.maximum((h.h - pc).abs(), (h.l - pc).abs()))
    atr = tr.rolling(14).mean(); sl = e - e.shift(P['slope_bars'])
    r = np.where(sl.abs() <= P['flat_slope'] * atr, 0, np.where((h.c > e) & (sl > 0), 1, np.where((h.c < e) & (sl < 0), -1, 0))).astype(float)
    r[atr.isna().values] = np.nan
    return pd.Series(r, index=h.index + pd.Timedelta('1h'))


def run(m, profile, spread_override=None, equity=388.0, **over):
    P = dict(BASE, **PROFILES[profile], **over)
    m = m.copy()
    if spread_override is not None:
        for k in 'ohlc':
            m['a' + k] = m[k] + spread_override
    bid = m[['o', 'h', 'l', 'c']]
    b5 = bid.resample('5min', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    pc = b5.c.shift(); tr = np.maximum(b5.h - b5.l, np.maximum((b5.h - pc).abs(), (b5.l - pc).abs()))
    atr5 = tr.rolling(P['atr']).mean()
    mid = b5.c.rolling(P['bb']).mean(); sd = b5.c.rolling(P['bb']).std(ddof=0)
    close_t = b5.index + pd.Timedelta('5min')
    reg = regime_series(bid, P).reindex(close_t, method='ffill').values
    sig = dict(zip(close_t.values, zip(atr5.values, mid.values, (mid + P['bbsd'] * sd).values,
                                       (mid - P['bbsd'] * sd).values, reg, b5.c.values)))

    t = m.index; tv = t.values
    O, H, L = m.o.values, m.h.values, m.l.values
    AO, AH, AL = m.ao.values, m.ah.values, m.al.values
    mins = t.hour.values * 60 + t.minute.values; dow = t.dayofweek.values; day = t.normalize().values
    week = (t.normalize() - pd.to_timedelta(t.dayofweek, unit='D')).values
    in_sess = np.zeros(len(t), bool)
    for a, b in P['sess']:
        in_sess |= (mins >= a) & (mins < b)
    in_sess &= dow < 5
    flat_at = np.where(dow == 4, P['fri_flat'], P['flat'])

    orders, pos, setups, trades = [], [], {}, []
    eq = equity; shadow = [0.0]; sh_eq = 0.0
    real_closes = []  # (time, day, week, net)

    def filter_allows():
        if not P['eq_filter']:
            return True
        n = min(P['eq_filter'], len(shadow))
        return sh_eq >= np.mean(shadow[-n:]) - 1e-9

    def close_pos(p, px, why, j):
        nonlocal eq, sh_eq
        net = (p['side'] * (px - p['entry']) - P['comm']) * 1.0  # 0.01 lot: $1 per 1.00 move
        s = setups[p['setup']]; s['net'] += net; s['open'] -= 1
        if s['open'] == 0 and not s['orders_left']:
            s['closed'] = tv[j]
        sh_eq += net; shadow.append(sh_eq)
        if len(shadow) > 500: shadow.pop(0)
        if not p['paper']:
            eq += net; real_closes.append((tv[j], day[j], week[j], net))
        trades.append(dict(t=p['t'], exit=t[j], side=p['side'], kind=p['kind'], paper=p['paper'], why=why,
                           pnl=net, R=net / p['risk'], setup=p['setup'], equity=eq))

    for j in range(len(t)):
        bo, bh, bl, ao_, ah_, al_ = O[j], H[j], L[j], AO[j], AH[j], AL[j]
        if mins[j] >= flat_at[j]:
            for p in pos:
                close_pos(p, bo if p['side'] == 1 else ao_, 'flat', j)
            pos = []; orders = []
            for s in setups.values(): s['orders_left'] = False
            continue
        if tv[j] in sig and mins[j] % 5 == 0:
            atr, md, top, bot, rg, c5 = sig[tv[j]]
            if not in_sess[j] or (orders and not pos):
                orders = []
            if not pos and not orders and in_sess[j] and not (np.isnan(atr) or np.isnan(md) or np.isnan(rg)):
                dk = day[j]
                today = [s for s in setups.values() if s['day'] == dk and s['filled']]
                done = sorted([s for s in today if s['closed'] is not None], key=lambda s: s['closed'], reverse=True)
                streak = 0
                for s in done:
                    if s['net'] < 0: streak += 1
                    else: break
                day_pnl = sum(n for (_, d, _, n) in real_closes if d == dk)
                wk_pnl = sum(n for (_, _, w, n) in real_closes if w == week[j])
                ok = len(today) < P['max_setups'] and streak < P['max_streak'] and day_pnl > -eq * P['daily_loss_pct'] / 100
                if P['weekly_pct'] > 0 and wk_pnl <= -(eq - wk_pnl) * P['weekly_pct'] / 100:
                    ok = False
                if ok and (rg != 0 or P['fade']):
                    spread = ao_ - bo
                    if rg == 0:
                        fade = True; side = -1 if (top - c5) < (c5 - bot) else 1
                        near = top if side == -1 else bot
                        far = near + P['depth'] * atr if side == -1 else near - P['depth'] * atr
                    else:
                        fade = False; side = int(rg); near = md; far = md - side * P['depth'] * atr
                    stop = far - P['stop_atr'] * atr if side == 1 else far + P['stop_atr'] * atr + spread
                    price = ao_ if side == 1 else bo
                    lv = []
                    for k in range(P['orders']):
                        lvl = near + (far - near) * (k / (P['orders'] - 1))
                        if side == 1: lvl -= spread
                        if (lvl < price if side == 1 else lvl > price) and abs(lvl - stop) >= max(P['min_stop_atr'] * atr, P['floor_dist']):
                            lv.append(round(lvl, 2))
                    cap = eq * P['risk_pct'] / 100
                    while lv and sum(abs(l - stop) for l in lv) > cap:
                        lv.pop(0)
                    if lv:
                        sh = lv[0]
                        tp = md if fade else sh + side * P['reward'] * abs(sh - stop)
                        if side * (tp - sh) > 0:
                            lv = [l for l in lv if abs(tp - l) <= P['max_rr'] * abs(l - stop)]
                            if lv:
                                sh = lv[0]; risk0 = abs(sh - stop); tgt = abs(tp - sh)
                                if (spread / 0.1 <= P['max_spread_pips'] + 1e-6 and spread <= P['spread_of_risk'] * risk0
                                        and tgt >= P['hurdle'] * (spread + P['comm'])):
                                    se = next(b for a, b in P['sess'] if a <= mins[j] < b)
                                    exp = tv[j] + np.timedelta64(min(P['expiry_bars'] * 5, se - mins[j]), 'm')
                                    paper = not filter_allows()
                                    setups[tv[j]] = dict(day=dk, net=0.0, closed=None, filled=False, open=0, orders_left=True)
                                    orders = [dict(side=side, lvl=l, stop=round(stop, 2), tp=round(tp, 2), exp=exp,
                                                   setup=tv[j], paper=paper, kind='FADE' if fade else 'PULLBACK') for l in lv]
        keep = []
        for od in orders:
            if tv[j] >= od['exp']:
                continue
            s = od['side']
            if (al_ <= od['lvl']) if s == 1 else (bh >= od['lvl']):
                fill = min(od['lvl'], ao_) if s == 1 else max(od['lvl'], bo)
                st = setups[od['setup']]; st['filled'] = True; st['open'] += 1
                pos.append(dict(side=s, entry=fill, stop=od['stop'], tp=od['tp'], risk=max(abs(fill - od['stop']), 1e-6),
                                be=False, setup=od['setup'], t=t[j], paper=od['paper'], kind=od['kind']))
            else:
                keep.append(od)
        orders = keep
        live = {o['setup'] for o in orders}
        for k, s in setups.items():
            if s['orders_left'] and k not in live:
                s['orders_left'] = False
                if s['open'] == 0 and s['filled'] and s['closed'] is None:
                    s['closed'] = tv[j]
        keep = []
        for p in pos:
            slip = 0.0 if p['paper'] else P['slip']
            if p['side'] == 1:
                if bl <= p['stop']:
                    close_pos(p, (bo if bo < p['stop'] else p['stop']) - slip, 'SL', j); continue
                if bh >= p['tp']:
                    close_pos(p, p['tp'], 'TP', j); continue
                if not p['be'] and bh - p['entry'] >= P['be_r'] * p['risk'] and p['entry'] > p['stop']:
                    p['stop'] = p['entry']; p['be'] = True
            else:
                if ah_ >= p['stop']:
                    close_pos(p, (ao_ if ao_ > p['stop'] else p['stop']) + slip, 'SL', j); continue
                if al_ <= p['tp']:
                    close_pos(p, p['tp'], 'TP', j); continue
                if not p['be'] and p['entry'] - al_ >= P['be_r'] * p['risk'] and p['entry'] < p['stop']:
                    p['stop'] = p['entry']; p['be'] = True
            keep.append(p)
        pos = keep
        if P['eq_floor'] and eq < P['eq_floor']:
            break
    return pd.DataFrame(trades)


def stats(g):
    gp = g.pnl[g.pnl > 0].sum(); gl = -g.pnl[g.pnl < 0].sum()
    return dict(n=len(g), win=round(100 * (g.pnl > 0).mean(), 1), PF=round(gp / gl, 2) if gl else 99, net=round(g.pnl.sum(), 2))


def report(tr, label):
    real = tr[~tr.paper]
    print('==', label, 'REAL', stats(real), '| paper', stats(tr[tr.paper]) if tr.paper.any() else '-')
    e = real.equity; print('   maxDD $%.0f end $%.0f' % ((e.cummax() - e).max(), e.iloc[-1]))
    for y, g in real.groupby(real.t.dt.year): print('  ', y, stats(g))
    r26 = real[real.t.dt.year == 2026]
    print('   2026 months', {mo: (s['n'], s['net']) for mo, g in r26.groupby(r26.t.dt.month) for s in [stats(g)]})
    for k, g in real.groupby('kind'): print('   ', k, stats(g))
    for s, g in real.groupby('side'): print('    side', s, stats(g))
    w = real[(real.t >= '2026-06-03') & (real.t < '2026-10-17')]
    print('   window 03 Jun-16 Oct 2026 (cTrader 16j tick run: 81 trades, PF 1.49, +$111.85 at $200):', stats(w))


if __name__ == '__main__':
    m = pd.read_pickle(sys.argv[1]); prof = sys.argv[2]
    so = None if len(sys.argv) < 4 or sys.argv[3] == 'real' else float(sys.argv[3])
    eq = float(sys.argv[4]) if len(sys.argv) > 4 else 388.0
    report(run(m, prof, so, eq), f'{prof} spread={"real" if so is None else so} start ${eq:.0f}')
