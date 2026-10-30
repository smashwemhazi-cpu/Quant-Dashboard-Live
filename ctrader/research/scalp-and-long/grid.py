import sys, json, itertools, multiprocessing as mp
import pandas as pd
sys.path.insert(0, '.')
import scalp as S
A = pd.read_pickle('m1.pkl'); B = S.load('../data/XAUUSD_M1_2026-03-26_to_2026-09-25.csv')
SIG = {}
def job(args):
    name, sl, rr, hold, on = args
    out = {}
    for lab, m in (('A', A), ('B', B)):
        key = (name, lab)
        sig = S.signals(m, name)
        tr = S.simulate(m, sig, sl, sl * rr if rr else None, hold, overnight=on)
        out[lab] = tr
    tr = pd.concat([out['A'], out['B']])
    return (name, sl, rr, hold), S.periods(tr), S.stats(tr)
cells = [(n, sl, rr, 120, False) for n in ('S1', 'S2', 'S3') for sl in (40, 60, 80, 100) for rr in (1, 1.5, 2, 3)]
cells += [('L1', sl, None, 2880, True) for sl in (60, 100)] + [('L1', sl, 2, 2880, True) for sl in (60, 100)]
cells += [('L2', sl, rr, None, False) for sl in (60, 100) for rr in (1, 2, None)]
if __name__ == '__main__':
    with mp.Pool(4) as p:
        for key, per, tot in p.imap(job, cells):
            pos = sum(1 for v in per.values() if v.get('n') and v['expR'] > 0)
            print(key, 'ALL', tot, '| pos periods', pos, '|', {k: (v.get('n'), v.get('expR'), v.get('PF')) for k, v in per.items()}, flush=True)
