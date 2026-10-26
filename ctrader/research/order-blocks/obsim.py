"""Order-block research backtest on M1 bid bars (UTC).

Rules are fixed in CFG before the first run (no tuning on results):
  structure TF M15, pivot strength 3, H1 EMA50 bias,
  bullish OB = last down-close M15 candle at/before the lowest low of the leg that closed above
  the last confirmed swing high (bearish mirrored), leg must contain a fair value gap.
  limit at OB proximal edge, stop beyond OB distal edge + 0.2 ATR, min stop 4.0, target 2R.
"""
import sys, json, math
import numpy as np, pandas as pd

CFG = dict(tf='15min', pivot=3, atr=14, ema=50, search=10, entry_depth=0.0, stop_buf_atr=0.2,
           min_stop=4.0, rr=2.0, valid_bars=32, spread=0.30, slip=0.50, comm=0.07,
           start_h=0, end_h=20, flat_hm=(20, 45), fri_end_h=18, max_trades=3, max_losses=2,
           equity=388.0, risk_pct=3.0, require_fvg=True, bias=True)


def load(path):
    if path.endswith('.pkl'):
        return pd.read_pickle(path)
    df = pd.read_csv(path)
    df.index = pd.to_datetime(df.iloc[:, 0]); df = df.iloc[:, 1:5]; df.columns = ['o', 'h', 'l', 'c']
    return df


def run(m1, cfg):
    tf = m1.resample(cfg['tf'], label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    h1 = m1['c'].resample('1h', label='left', closed='left').last().dropna()
    ema = h1.ewm(span=cfg['ema'], adjust=False).mean()
    # H1 bias known at time t = last H1 bar fully closed before t
    h1_close_time = h1.index + pd.Timedelta(hours=1)
    bias_series = pd.Series(np.sign(h1.values - ema.values), index=h1_close_time)
    O, H, L, C = (tf[k].values for k in 'ohlc')
    tr = np.maximum(H - L, np.maximum(abs(H - np.r_[C[0], C[:-1]]), abs(L - np.r_[C[0], C[:-1]])))
    ATR = pd.Series(tr).rolling(cfg['atr']).mean().values
    close_time = tf.index + pd.Timedelta(cfg['tf'])
    bias = bias_series.reindex(close_time, method='ffill').values

    m1t = m1.index.values; mh = m1['h'].values; ml = m1['l'].values; mc = m1['c'].values
    p = cfg['pivot']; n = len(tf)
    sh = None; sl_ = None  # (price, idx, broken)
    setups = []
    for i in range(p * 2 + cfg['atr'], n):
        j = i - p  # pivot candidate confirmed at close of bar i
        if H[j] > H[j - p:j].max() and H[j] > H[j + 1:i + 1].max():
            sh = [H[j], j, False]
        if L[j] < L[j - p:j].min() and L[j] < L[j + 1:i + 1].min():
            sl_ = [L[j], j, False]
        a = ATR[i]
        if not np.isfinite(a) or a <= 0:
            continue
        for side in (1, -1):
            piv = sh if side == 1 else sl_
            if piv is None or piv[2]:
                continue
            if not ((side == 1 and C[i] > piv[0]) or (side == -1 and C[i] < piv[0])):
                continue
            piv[2] = True
            lo = max(piv[1] - cfg['search'], 0)
            seg = range(lo, i + 1)
            m = min(seg, key=lambda k: L[k]) if side == 1 else max(seg, key=lambda k: H[k])
            k = m
            while k >= lo and not ((side == 1 and C[k] < O[k]) or (side == -1 and C[k] > O[k])):
                k -= 1
            if k < lo:
                continue
            if cfg['require_fvg']:
                ok = any((side == 1 and L[q] > H[q - 2]) or (side == -1 and H[q] < L[q - 2]) for q in range(max(k + 2, 2), i + 1))
                if not ok:
                    continue
            if cfg['bias'] and not (bias[i] == side):
                continue
            top, bot = H[k], L[k]
            if side == 1:
                entry = top - cfg['entry_depth'] * (top - bot)
                stop = bot - cfg['stop_buf_atr'] * a - cfg['spread']
            else:
                entry = bot + cfg['entry_depth'] * (top - bot)
                stop = top + cfg['stop_buf_atr'] * a + cfg['spread']
            dist = abs(entry - stop)
            if dist < cfg['min_stop']:
                stop = entry - side * cfg['min_stop']; dist = cfg['min_stop']
            if (side == 1 and C[i] <= entry) or (side == -1 and C[i] >= entry):
                continue  # already back inside the block at signal time: skip, no chasing
            setups.append(dict(t=close_time[i], side=side, entry=entry, stop=stop, dist=dist,
                               tp=entry + side * cfg['rr'] * dist, inval=bot if side == 1 else top,
                               expire=close_time[min(i + cfg['valid_bars'], n - 1)], atr=a))
    return simulate(setups, m1, cfg), len(setups)


def simulate(setups, m1, cfg):
    t = m1.index; th = t.hour.values; tm = t.minute.values; wd = t.dayofweek.values
    H = m1['h'].values; L = m1['l'].values; C = m1['c'].values
    idx = t.searchsorted(pd.DatetimeIndex([s['t'] for s in setups]))
    eq = cfg['equity']; trades = []; busy_until = 0
    day_count = {}; spr, slip, comm = cfg['spread'], cfg['slip'], cfg['comm']
    flat_h, flat_m = cfg['flat_hm']
    for s, i0 in zip(setups, idx):
        if i0 < busy_until or i0 >= len(t):
            continue
        h0 = th[i0]
        if not (cfg['start_h'] <= h0 < cfg['end_h']) or (wd[i0] == 4 and h0 >= cfg['fri_end_h']):
            continue
        day = t[i0].date(); dc = day_count.setdefault(day, [0, 0])
        if dc[0] >= cfg['max_trades'] or dc[1] >= cfg['max_losses']:
            continue
        cap = eq * cfg['risk_pct'] / 100.0
        lots = math.floor(cap / s['dist'] + 1e-9) / 100.0  # $1 per 1.00 price per 0.01 lot
        if cfg.get('fixed_lots'):
            lots = cfg['fixed_lots'] if s['dist'] <= cfg.get('max_stop', 1e9) else 0
        if lots < 0.01:
            continue
        side, entry, stop, tp = s['side'], s['entry'], s['stop'], s['tp']
        exp = t.searchsorted(s['expire'])
        fill = None; i = i0
        while i < min(exp, len(t)):
            if th[i] > flat_h or (th[i] == flat_h and tm[i] >= flat_m) or t[i].date() != day:
                break
            # cancel if target reached before fill or block invalidated (bid basis)
            if (side == 1 and H[i] >= tp) or (side == -1 and L[i] + spr <= tp):
                break
            if side == 1 and L[i] + spr <= entry:
                fill = i; break
            if side == -1 and H[i] >= entry:
                fill = i; break
            if (side == 1 and C[i] < s['inval']) or (side == -1 and C[i] > s['inval']):
                break
            i += 1
        if fill is None:
            continue
        # walk the trade; fill bar: stop can hit, target cannot (conservative)
        res = None; i = fill
        while i < len(t):
            new_day = t[i].date() != day
            if new_day or th[i] > flat_h or (th[i] == flat_h and tm[i] >= flat_m):
                px = C[i - 1] if new_day else C[i]
                res = side * (px - entry) - (spr if side == -1 else 0); break  # market close: buy exits on bid, sell exits on ask
            if side == 1:
                if L[i] <= stop: res = (stop - slip) - entry; break
                if i > fill and H[i] >= tp: res = tp - entry; break
            else:
                if H[i] + spr >= stop: res = entry - (stop + slip); break
                if i > fill and L[i] + spr <= tp: res = entry - tp; break
            i += 1
        if res is None:
            break
        pnl = (res - comm) * lots * 100
        eq += pnl; dc[0] += 1
        if pnl < 0: dc[1] += 1
        trades.append(dict(t=t[fill], side=side, R=(res - comm) / s['dist'], pnl=pnl, lots=lots, dist=s['dist'], equity=eq))
        busy_until = i + 1
    return pd.DataFrame(trades)


def report(tr, label):
    if tr.empty:
        print(label, 'no trades'); return
    def st(g):
        gp = g.pnl[g.pnl > 0].sum(); gl = -g.pnl[g.pnl < 0].sum()
        return f"n={len(g):4d} win={100*(g.R>0).mean():4.1f}% expR={g.R.mean():+.3f} PF={gp/gl if gl else 99:.2f} net=${g.pnl.sum():8.2f}"
    print('==', label, st(tr))
    e = tr.equity; pk = e.cummax(); print('   maxDD $%.0f (%.0f%%)  end equity $%.0f  avg stop %.2f  avg lots %.3f' % ((pk - e).max(), 100 * ((pk - e) / pk).max(), e.iloc[-1], tr.dist.mean(), tr.lots.mean()))
    for y, g in tr.groupby(tr.t.dt.year): print('   ', y, st(g))
    for sd, g in tr.groupby('side'): print('    side', sd, st(g))


if __name__ == '__main__':
    m1 = load(sys.argv[1])
    over = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    cfg = dict(CFG, **over)
    tr, ns = run(m1, cfg)
    report(tr, f"{sys.argv[1].split('/')[-1]} {over} setups={ns}")
    if len(sys.argv) > 3: tr.to_csv(sys.argv[3], index=False)
