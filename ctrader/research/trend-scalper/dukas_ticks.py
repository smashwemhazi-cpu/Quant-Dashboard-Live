"""Download Dukascopy XAUUSD ticks one month at a time, convert to M1 BID OHLC + per-minute
median spread (pips, pip = 0.10), append to a pickle, delete the raw CSV, then run T1.

Usage:  python dukas_ticks.py fetch  <workdir> [2025-01 2026-10]
        python dukas_ticks.py m1     <workdir> <bid_m1.csv> <ask_m1.csv>   (Dukascopy M1 BID+ASK bars instead of ticks)
        python dukas_ticks.py check  <workdir>
        python dukas_ticks.py run    <workdir>
Needs network access to datafeed.dukascopy.com. Raw ticks and bars stay in <workdir>; never commit them.
"""
import sys, os, glob, shutil, subprocess
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scalp-and-long')); sys.path.insert(0, HERE)
PIP = 0.10


def to_m1(csv_path):
    t = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in t.columns}
    ts = pd.to_datetime(t[cols['timestamp']], unit='ms', utc=True).dt.tz_convert(None)
    bid, ask = t[cols['bidprice']].astype(float), t[cols['askprice']].astype(float)
    df = pd.DataFrame({'bid': bid.values, 'spr': ((ask - bid) / PIP).values}, index=ts)
    df = df[(df.bid > 0) & (df.spr >= 0)]
    g = df.resample('1min', label='left', closed='left')
    m = pd.DataFrame({'o': g.bid.first(), 'h': g.bid.max(), 'l': g.bid.min(), 'c': g.bid.last(),
                      'spread_pips': g.spr.median()}).dropna(subset=['o'])
    return m


def from_m1(work, bid_csv, ask_csv):
    """Dukascopy M1 BID and ASK bar CSVs (timestamp ms, open, high, low, close) -> same pickle as fetch.
    Per-minute spread = mean of the open and close ASK-BID differences (no tick median available).
    Stale minutes (O=H=L=C = previous close: Sundays, the 21:00 break, holidays) are filled bars with no ticks,
    which to_m1 would never produce, so they are dropped."""
    rd = lambda p: pd.read_csv(p).set_index('timestamp')
    b, a = rd(bid_csv), rd(ask_csv)
    j = b.join(a, lsuffix='_b', rsuffix='_a', how='inner')
    spr = ((j.open_a - j.open_b) + (j.close_a - j.close_b)) / 2 / PIP
    m = pd.DataFrame({'o': j.open_b, 'h': j.high_b, 'l': j.low_b, 'c': j.close_b, 'spread_pips': spr})
    m.index = pd.to_datetime(m.index, unit='ms', utc=True).tz_convert(None)
    m = m[(m.o > 0) & (m.spread_pips >= 0)].sort_index()
    m = m[~m.index.duplicated(keep='last')]
    stale = (m.o == m.h) & (m.h == m.l) & (m.l == m.c) & (m.o == m.c.shift())
    m = m[~stale]; print('stale flat minutes dropped', int(stale.sum()))
    os.makedirs(work, exist_ok=True); m.to_pickle(os.path.join(work, 'dukas_m1.pkl'))
    print('bid rows', len(b), 'ask rows', len(a), 'joined', len(j), 'kept', len(m))


def fetch(work, a='2025-01', b='2026-10', end='2026-10-29'):
    os.makedirs(work, exist_ok=True); out = os.path.join(work, 'dukas_m1.pkl'); gaps = []
    have = pd.read_pickle(out) if os.path.exists(out) else None
    for start in pd.period_range(a, b, freq='M'):
        f, t = str(start.start_time.date()), str(min((start + 1).start_time, pd.Timestamp(end)).date())
        if have is not None and len(have.loc[f:str(start.end_time.date())]) > 0:
            continue
        raw = os.path.join(work, 'raw', str(start)); ok = False
        for attempt in range(2):
            shutil.rmtree(raw, ignore_errors=True); os.makedirs(raw)
            r = subprocess.run(['npx', '-y', 'dukascopy-node', '-i', 'xauusd', '-from', f, '-to', t,
                                '-t', 'tick', '-f', 'csv', '-dir', raw], capture_output=True, text=True)
            files = glob.glob(os.path.join(raw, '*.csv'))
            if r.returncode == 0 and files:
                ok = True; break
            print(start, 'attempt', attempt + 1, 'failed:', (r.stderr or r.stdout)[-300:], flush=True)
        if not ok:
            gaps.append(str(start)); shutil.rmtree(raw, ignore_errors=True); continue
        m = pd.concat([to_m1(p) for p in files])
        have = m if have is None else pd.concat([have, m])
        have = have[~have.index.duplicated(keep='last')].sort_index()
        have.to_pickle(out); shutil.rmtree(raw, ignore_errors=True)
        print(start, 'rows', len(m), flush=True)
    print('gaps:', gaps or 'none')


def check(work):
    m = pd.read_pickle(os.path.join(work, 'dukas_m1.pkl'))
    print(m.groupby(m.index.to_period('M')).size().to_string())
    print('first', m.index[0], 'last', m.index[-1], 'low', m.l.min(), 'high', m.h.max())
    print(m.spread_pips.groupby(m.index.hour).quantile([.5, .9]).unstack().round(2).to_string())
    sess = m.between_time('07:00', '16:30').spread_pips.median()
    print('median spread 07:00-16:30 UTC (pips):', round(sess, 3))


def run(work):
    import scalp as S
    from trendscalp import t1_signals
    m = pd.read_pickle(os.path.join(work, 'dukas_m1.pkl'))
    sess_spread = m.between_time('07:00', '16:30').spread_pips.median() * PIP
    bars = m[['o', 'h', 'l', 'c']]
    variants = [(slp, rr, None) for slp in (60, 100) for rr in (1.5, 2.0)] + \
               [(100, rr, sess_spread) for rr in (1.5, 2.0)]
    base = dict(S.COST)
    for slp, rr, spr in variants:
        S.COST.update(base)
        if spr is not None:
            S.COST['spread'] = spr
        tr = S.simulate(bars, t1_signals(bars, slp), slp, slp * rr, None)
        tag = f'SL {slp} TP {slp*rr:.0f} spread {S.COST["spread"]:.2f}'
        print(tag, 'ALL', S.stats(tr), flush=True)
        for y, g in tr.groupby(tr.t.dt.year): print(tag, y, S.stats(g))
        t26 = tr[tr.t.dt.year == 2026]
        for mo, g in t26.groupby(t26.t.dt.month): print(tag, f'2026-{mo:02d}', S.stats(g))
        for s, g in tr.groupby('side'): print(tag, 'side', s, S.stats(g))
    S.COST.update(base)


if __name__ == '__main__':
    cmd, work = sys.argv[1], sys.argv[2]
    {'fetch': lambda: fetch(work, *sys.argv[3:5]), 'm1': lambda: from_m1(work, *sys.argv[3:5]), 'check': lambda: check(work), 'run': lambda: run(work)}[cmd]()
