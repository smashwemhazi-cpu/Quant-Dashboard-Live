"""Python replica of MicroLotBreakoutBot build 16m (day-trade profile) for M1 bid/ask bars.

Mirrors the C# logic: H1 EMA50 regime (5-bar slope vs 0.3 x H1 ATR), M5 ATR(14) and Bollinger(20, 2),
pullback ladder (3 limit orders from the middle band to 1 ATR beyond, shared stop 0.75 ATR further),
min order stop max(1 ATR, $4 floor), shallow orders dropped to fit 3% of equity, target RewardR x the
shallowest order's risk, deep rungs dropped above reward:risk 2.5, spread caps (6 pips, 15% of stop),
cost hurdle 3x, unfilled ladder re-priced every M5 bar (orders expire after 3 bars or at session end),
breakeven at 1R per position, max 3 setups a day, stop after 2 losing setups in a row, daily loss 6%,
flat at 20:30 UTC (Friday 19:30), fixed 0.01 lot, equity floor $300.

Not modelled: DoubleTick stop trigger, news windows (unused), equity-curve and weekly filters (off in 16m).
Within one minute the stop is checked before the target (conservative).

Usage: python sim16m.py <m1_bidask.pkl> [spread_override_price] [start_equity]
The pickle needs columns o,h,l,c (bid) and ao,ah,al,ac (ask) with a UTC DatetimeIndex.
"""
import sys, math
import numpy as np, pandas as pd

P = dict(sess=((8 * 60, 11 * 60), (14 * 60, 16 * 60 + 30)), flat=20 * 60 + 30, fri_flat=19 * 60 + 30,
         atr=14, bb=20, bbsd=2.0, ema=50, slope_bars=5, flat_slope=0.3, depth=1.0, stop_atr=0.75,
         min_stop_atr=1.0, orders=3, reward=2.0, max_rr=2.5, expiry_bars=3, risk_pct=3.0,
         floor_dist=0.8 / 0.2, max_spread_pips=6.0, spread_of_risk=0.15, hurdle=3.0, comm=0.07,
         slip=0.30, be_r=1.0, max_setups=3, max_streak=2, daily_loss_pct=6.0, eq_floor=300.0, lot_mult=1.0)


def regime_series(bid):
    h = bid.resample('1h', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    e = h.c.ewm(span=P['ema'], adjust=False).mean()
    pc = h.c.shift(); tr = np.maximum(h.h - h.l, np.maximum((h.h - pc).abs(), (h.l - pc).abs()))
    atr = tr.rolling(14).mean(); sl = e - e.shift(P['slope_bars'])
    r = np.where(sl.abs() <= P['flat_slope'] * atr, 0, np.where((h.c > e) & (sl > 0), 1, np.where((h.c < e) & (sl < 0), -1, 0)))
    r = pd.Series(r, index=h.index + pd.Timedelta('1h'))
    r[atr.isna().values] = np.nan
    return r


def run(m, spread_override=None, equity=388.0):
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
    reg = regime_series(bid).reindex(close_t, method='ffill').values
    sig = dict(zip(close_t, zip(atr5.values, mid.values, (mid + P['bbsd'] * sd).values, (mid - P['bbsd'] * sd).values, reg)))

    t = m.index; tv = t.values
    O, H, L, C = (m[k].values for k in 'ohlc'); AO, AH, AL, AC = (m['a' + k].values for k in 'ohlc')
    mins = t.hour.values * 60 + t.minute.values; dow = t.dayofweek.values; day = t.normalize().values
    in_sess = np.zeros(len(t), bool)
    for a, b in P['sess']:
        in_sess |= (mins >= a) & (mins < b)
    in_sess &= dow < 5
    flat_at = np.where(dow == 4, P['fri_flat'], P['flat'])
    is_m5_close = (t.minute.values % 5 == 0)
    sess_end = {}

    orders = []        # dict(side, lvl, stop, tp, exp, setup)
    pos = []           # dict(side, entry, stop, tp, risk, be, setup, t)
    setups = {}        # setup -> dict(day, net, closed, filled)
    trades = []; eq = equity; stopped = False
    daystat = {}

    def close_pos(p, px, why, j):
        nonlocal eq
        pnl = (p['side'] * (px - p['entry']) - P['comm']) * 100 * 0.01 * P['lot_mult']
        eq += pnl
        s = setups[p['setup']]; s['net'] += pnl; s['open'] -= 1
        if s['open'] == 0 and not s['orders_left']:
            s['closed'] = tv[j]
        trades.append(dict(t=p['t'], exit=t[j], side=p['side'], entry=p['entry'], px=px, why=why, pnl=pnl,
                           R=(pnl / (100 * 0.01 * P['lot_mult'])) / p['risk'], setup=p['setup'], equity=eq))

    for j in range(len(t)):
        if stopped:
            break
        bo, bh, bl, ao_, ah_, al_ = O[j], H[j], L[j], AO[j], AH[j], AL[j]
        # 1) flat time: close all, cancel orders
        if mins[j] >= flat_at[j] or dow[j] >= 5:
            for p in pos:
                close_pos(p, bo if p['side'] == 1 else ao_, 'flat', j)
            pos = []; orders = []
            for s in setups.values(): s['orders_left'] = False
            continue
        # 2) at each M5 close: re-price or place a ladder
        if is_m5_close[j] and tv[j] in sig:
            atr, md, top, bot, rg = sig[tv[j]]
            if not in_sess[j]:
                orders = []
            if orders and not pos:
                orders = []
            dk = day[j]; ds = daystat.setdefault(dk, dict(setups=0, pnl=0.0))
            if not pos and not orders and in_sess[j] and not (np.isnan(atr) or np.isnan(md) or np.isnan(rg)):
                today = [s for s in setups.values() if s['day'] == dk and s['filled']]
                done = sorted([s for s in today if s['closed'] is not None], key=lambda s: s['closed'], reverse=True)
                streak = 0
                for s in done:
                    if s['net'] < 0: streak += 1
                    else: break
                day_pnl = sum(s['net'] for s in done)
                ok = len(today) < P['max_setups'] and streak < P['max_streak'] and day_pnl > -eq * P['daily_loss_pct'] / 100
                if ok and rg != 0:
                    side = int(rg); spread = ao_ - bo
                    near = md; far = md - side * P['depth'] * atr
                    stop = far - P['stop_atr'] * atr if side == 1 else far + P['stop_atr'] * atr + spread
                    price = ao_ if side == 1 else bo
                    lv = []
                    for k in range(P['orders']):
                        f = k / (P['orders'] - 1)
                        lvl = near + (far - near) * f
                        if side == 1: lvl -= spread
                        valid = lvl < price if side == 1 else lvl > price
                        if valid and abs(lvl - stop) >= max(P['min_stop_atr'] * atr, P['floor_dist']):
                            lv.append(round(lvl, 2))
                    cap = eq * P['risk_pct'] / 100
                    while lv and sum(abs(l - stop) for l in lv) * P['lot_mult'] > cap:
                        lv.pop(0)
                    if lv:
                        sh = lv[0]; tp = sh + side * P['reward'] * abs(sh - stop)
                        lv = [l for l in lv if abs(tp - l) <= P['max_rr'] * abs(l - stop)]
                        if lv:
                            sh = lv[0]; risk0 = abs(sh - stop); tgt = abs(tp - sh)
                            sp_ok = spread / 0.1 <= P['max_spread_pips'] + 1e-6 and spread <= P['spread_of_risk'] * risk0
                            if sp_ok and tgt >= P['hurdle'] * (spread + P['comm']):
                                sid = tv[j]
                                if mins[j] < P['sess'][0][1]: se = P['sess'][0][1]
                                else: se = P['sess'][1][1]
                                exp = tv[j] + np.timedelta64(min(P['expiry_bars'] * 5, se - mins[j]), 'm')
                                setups[sid] = dict(day=dk, net=0.0, closed=None, filled=False, open=0, orders_left=True)
                                orders = [dict(side=side, lvl=l, stop=round(stop, 2), tp=round(tp, 2), exp=exp, setup=sid) for l in lv]
        # 3) fills on this minute
        keep = []
        for od in orders:
            if tv[j] >= od['exp']:
                continue
            s = od['side']
            hit = (al_ <= od['lvl']) if s == 1 else (bh >= od['lvl'])
            if hit:
                fill = min(od['lvl'], ao_) if s == 1 else max(od['lvl'], bo)
                setups[od['setup']]['filled'] = True; setups[od['setup']]['open'] += 1
                pos.append(dict(side=s, entry=fill, stop=od['stop'], tp=od['tp'], risk=abs(fill - od['stop']), be=False,
                                setup=od['setup'], t=t[j]))
            else:
                keep.append(od)
        orders = keep
        live = {o['setup'] for o in orders}
        for k, s in setups.items():
            if s['orders_left'] and k not in live:
                s['orders_left'] = False
                if s['open'] == 0 and s['filled'] and s['closed'] is None:
                    s['closed'] = tv[j]
        # 4) exits: stop first, then target, then breakeven for the next minute
        keep = []
        for p in pos:
            if p['side'] == 1:
                if bl <= p['stop']:
                    px = (bo if bo < p['stop'] else p['stop']) - P['slip']; close_pos(p, px, 'SL', j); continue
                if bh >= p['tp']:
                    close_pos(p, p['tp'], 'TP', j); continue
                if not p['be'] and bh - p['entry'] >= P['be_r'] * p['risk'] and p['entry'] > p['stop']:
                    p['stop'] = p['entry']; p['be'] = True
            else:
                if ah_ >= p['stop']:
                    px = (ao_ if ao_ > p['stop'] else p['stop']) + P['slip']; close_pos(p, px, 'SL', j); continue
                if al_ <= p['tp']:
                    close_pos(p, p['tp'], 'TP', j); continue
                if not p['be'] and p['entry'] - al_ >= P['be_r'] * p['risk'] and p['entry'] < p['stop']:
                    p['stop'] = p['entry']; p['be'] = True
            keep.append(p)
        pos = keep
        if eq < P['eq_floor']:
            stopped = True
    return pd.DataFrame(trades)


def report(tr, label):
    if tr.empty:
        print(label, 'no trades'); return
    def st(g):
        gp = g.pnl[g.pnl > 0].sum(); gl = -g.pnl[g.pnl < 0].sum()
        return f"n={len(g):4d} setups={g.setup.nunique():4d} win={100*(g.pnl>0).mean():4.1f}% PF={gp/gl if gl else 99:5.2f} net=${g.pnl.sum():8.2f}"
    e = tr.equity; pk = e.cummax()
    print('==', label, st(tr), '| maxDD $%.0f | end $%.0f | last trade %s' % ((pk - e).max(), e.iloc[-1], tr.exit.iloc[-1]))
    for y, g in tr.groupby(tr.t.dt.year): print('  ', y, st(g))
    for mo, g in tr[tr.t.dt.year == 2026].groupby(tr.t.dt.month): print('   2026-%02d' % mo, st(g))
    for s, g in tr.groupby('side'): print('   side', s, st(g))
    for w, g in tr.groupby('why'): print('   exit', w, st(g))


if __name__ == '__main__':
    m = pd.read_pickle(sys.argv[1])
    so = float(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] != 'real' else None
    eq = float(sys.argv[3]) if len(sys.argv) > 3 else 388.0
    tr = run(m, so, eq)
    report(tr, f"16m replica | spread {'Dukascopy real' if so is None else so} | start ${eq:.0f}")
