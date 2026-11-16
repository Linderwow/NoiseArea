import os, sys
from types import SimpleNamespace
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import msbtrade as mt

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got}, want {want}")
    if not ok: FAILS.append(name)


def mkB(script, n=60, start=600, iid=None):
    rows = [(100.0, 100.0, 100.0, 100.0)] * n
    for i, r in script.items(): rows[i] = r
    a = np.array(rows, float)
    return dict(o=a[:, 0], h=a[:, 1], l=a[:, 2], c=a[:, 3], iid=np.ones(n, int) if iid is None else iid,
                endm=np.arange(start, start + n), day=np.array([np.datetime64('2024-01-03')] * n))


def sigs(n, at, s, below=np.nan, above=np.nan):
    sig = np.zeros(n, np.int8); pt = np.full(n, np.nan); pb = np.full(n, np.nan)
    sig[at] = s; pb[at] = below; pt[at] = above
    return sig, pt, pb


def run(B, sg, k, session='rth'):
    log = []; tr, st = mt.simulate(B, *sg, k, session, log=log)
    return [(round(float(x[1]), 2), x[2], x[3], round(float(x[4]), 2), x[5]) for x in tr], st, log


tr, st, log = run(mkB({10: (100, 101, 99, 100), 11: (100, 110, 100, 109), 12: (109, 110.25, 108, 110)}), sigs(60, 9, 2, below=95), 2)
check('A long: target 110 touched at 11, traded through at 12', tr, [(10.0, 1, 1, 2.0, 'target')])
check('A entry at the next open, stop = pending low', [(e[0], e[1], float(e[3]), float(e[4]), float(e[5])) for e in log], [(10, 12, 100.0, 95.0, 110.0)])
tr, st, log = run(mkB({11: (100, 111, 94, 100)}), sigs(60, 9, 2, below=95), 2)
check('B stop and target in one bar: stop first', tr, [(-5.0, 0, 2, -1.0, 'stop')])
tr, st, log = run(mkB({11: (94, 94, 93, 93.5)}), sigs(60, 9, 2, below=95), 2)
check('C gap through the stop fills at the open', tr, [(-6.0, 0, 2, -1.2, 'stop')])
tr, st, log = run(mkB({}), sigs(60, 9, 2), 2)
check('D no pending opposite level: no trade, counted', (tr, st['no_level']), ([], 1))
tr, st, log = run(mkB({10: (94, 94, 94, 94)}), sigs(60, 9, 2, below=95), 2)
check('E entry beyond the stop: no trade, counted', (tr, st['bad_entry']), ([], 1))
tr, st, log = run(mkB({}, start=920), sigs(60, 9, 2, below=95), 2)
check('F RTH: out at the 16:00 close', (tr, int(log[0][1])), ([(0.0, 0, 2, 0.0, 'session')], 40))
iid = np.ones(60, int); iid[15:] = 2
tr, st, log = run(mkB({}, iid=iid), sigs(60, 9, 2, below=95), 2)
check('G contract change: out at the last bar of the old contract', (tr, int(log[0][1])), ([(0.0, 0, 2, 0.0, 'session')], 14))
tr, st, log = run(mkB({11: (100, 100, 94.75, 95)}), sigs(60, 9, -2, above=105), 1)
check('H short: target 95 traded through', tr, [(5.0, 1, 1, 1.0, 'target')])
tr, st, log = run(mkB({}, start=992), sigs(60, 9, 2, below=95), 2, 'legal')
check('I legal session: no signal after 16:30', (tr, st['signals']), ([], 0))
tr, st, log = run(mkB({}, start=970), sigs(60, 9, 2, below=95), 2, 'legal')
check('J legal session: out at the 16:55 close', int(log[0][1]), 1015 - 970)

t = np.array([np.datetime64('2024-01-03T09:30') + np.timedelta64(i, 'm') for i in range(10)])
v = np.arange(10, dtype=float)
mk = SimpleNamespace(t=t, ao=100 + v, ah=101 + v, al=99 + v, ac=100.5 + v, iid=np.ones(10, int))
B = mt.bars(mk, 5)
check('K 5-minute bars end 09:35 and 09:40', [int(x) for x in B['endm']], [575, 580])
check('K 5-minute OHLC', [list(map(float, (B['o'][i], B['h'][i], B['l'][i], B['c'][i]))) for i in range(2)],
      [[100.0, 105.0, 99.0, 104.5], [105.0, 110.0, 104.0, 109.5]])
check('K evening bars belong to the next trading day', str(mt.bars(SimpleNamespace(t=np.array([np.datetime64('2024-01-03T18:04')]), ao=v[:1], ah=v[:1], al=v[:1], ac=v[:1], iid=np.ones(1, int)), 1)['day'][0]), '2024-01-04')
print('ALL PASS' if not FAILS else f'{len(FAILS)} FAIL: {FAILS}')
sys.exit(1 if FAILS else 0)
