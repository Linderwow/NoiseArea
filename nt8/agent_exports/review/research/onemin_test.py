import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np, pandas as pd
import src2_lib as L2
import onemin as om

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got}, want {want}")
    if not ok: FAILS.append(name)


def market(rows):
    mk = L2.Mins.__new__(L2.Mins); mk.name = 'synthetic'
    mk.t = np.array([np.datetime64(r[0].replace(' ', 'T'), 'm') for r in rows])
    mk.o, mk.h, mk.l, mk.c = (np.array([r[k] for r in rows], float) for k in (1, 2, 3, 4))
    mk.v = np.ones(len(rows)); mk.iid = np.ones(len(rows), np.int64); mk.shift = np.zeros(len(rows))
    return om.finish(mk)


def calm(day, start=0, n=390, base=100.0):
    out = []; prev = base
    for i in range(start, start + n):
        c = base + 0.25 * (i % 2); t = pd.Timestamp(day) + pd.Timedelta(minutes=570 + i)
        out.append((t.strftime('%Y-%m-%d %H:%M'), prev, max(prev, c), min(prev, c), c)); prev = c
    return out


def session(day, script, flat_after, flat_px):
    rows = calm(day, 0, 99)
    for k, (o, h, l, c) in enumerate(script):
        t = pd.Timestamp(day) + pd.Timedelta(minutes=570 + 99 + k); rows.append((t.strftime('%Y-%m-%d %H:%M'), o, h, l, c))
    for i in range(99 + len(script), 390):
        t = pd.Timestamp(day) + pd.Timedelta(minutes=570 + i); rows.append((t.strftime('%Y-%m-%d %H:%M'), flat_px, flat_px, flat_px, flat_px))
    return rows


SPIKE = [(100.0, 100.0, 99.5, 99.5), (99.5, 99.5, 98.5, 98.5)]
B = SPIKE + [(98.5, 98.75, 98.25, 98.5), (98.5, 98.5, 98.0, 98.25), (98.25, 99.25, 98.25, 99.0), (99.0, 99.5, 99.0, 99.25)]
C = SPIKE + [(98.5, 98.75, 98.25, 98.5), (98.5, 98.5, 98.0, 98.25), (98.25, 99.75, 96.25, 98.0)]
D = SPIKE + [(98.5, 98.75, 98.25, 98.5), (98.5, 98.75, 98.25, 98.5), (98.5, 98.75, 98.25, 98.5), (98.5, 98.5, 98.0, 98.5)]
E = SPIKE + [(98.5, 98.75, 98.25, 98.5), (98.5, 98.5, 98.0, 98.5)]

rows = calm('2024-01-02') + session('2024-01-03', B, 0, 99.25) + calm('2024-01-04') + session('2024-01-05', C, 0, 98.0) + \
       calm('2024-01-08') + session('2024-01-09', D, 0, 98.5) + calm('2024-01-10') + session('2024-01-11', E, 0, 98.5)
mk = market(rows)
log = []; res = om.run_R(mk, lambda d: True, z=3, tgt_frac=0.5, tstop=10, log=log)
by = {d.strftime('%m-%d'): [(fj, xj, s, e, x, k) for (dd, fj, xj, s, e, x, k) in log if dd == d] for d in mk.dates}
check('calm days: no trade', [by[k] for k in ('01-02', '01-04', '01-08', '01-10')], [[], [], [], []])
check('B trade-through fill at 102, limit target at 104', by['01-03'], [(102, 104, 1, 98.25, 99.25, 'limit')])
check('B sides: 2 limit, 0 market', res[pd.Timestamp('2024-01-03')][1:3], (2, 0))
check('C stop before target on the same bar', by['01-05'], [(102, 103, 1, 98.25, 96.5, 'market')])
check('D touch only for 3 bars, then expired', by['01-09'], [])
E_DAY = pd.Timestamp('2024-01-11')
log2 = []; om.run_R(mk, lambda d: True, z=3, tgt_frac=0, tstop=5, log=log2)
by2 = [(fj, xj, s, e, x, k) for (dd, fj, xj, s, e, x, k) in log2 if dd == E_DAY]
check('E time stop 5 bars after the fill, market', by2, [(102, 107, 1, 98.25, 98.5, 'market')])
check('E gross return = 0.25 / raw open', round(om.run_R(mk, lambda d: d == E_DAY, z=3, tgt_frac=0, tstop=5)[E_DAY][0] * 100.0, 6), 0.25)

log3 = []; om.run_S(mk, lambda d: True, z=3, tstop=5, log=log3)
by3 = {d.strftime('%m-%d'): [(fj, xj, s, e, x) for (dd, fj, xj, s, e, x, k) in log3 if dd == d] for d in mk.dates}
check('S on B: short at the next open, out at the 106 close', by3['01-03'], [(101, 106, -1, 98.5, 99.25)])
check('S on C: high 99.75 stays under the 100.25 stop, out at the 106 close', by3['01-05'], [(101, 106, -1, 98.5, 98.0)])


def night(day, script):
    rows = []; prev = 100.0
    for i in range(180, 570):
        m = i; t = pd.Timestamp(day) + pd.Timedelta(minutes=m)
        if m < 195: o, h, l, c = 100.0, 100.5, 99.5, 100.0
        else: o, h, l, c = script.get(m, (prev, prev, prev, prev))
        rows.append((t.strftime('%Y-%m-%d %H:%M'), o, h, l, c)); prev = c
    rows += [(r[0], 101.0, 101.0, 101.0, 101.0) for r in calm(day)]
    return rows


S1 = {240: (100.0, 101.0, 100.0, 101.0), 241: (101.0, 101.0, 101.0, 101.0), 300: (101.0, 101.0, 99.25, 99.25)}
S2 = {240: (100.0, 101.0, 100.0, 101.0), 241: (101.0, 101.0, 101.0, 101.0), 568: (101.0, 102.0, 101.0, 102.0)}
mg = market(night('2024-02-05', S1) + night('2024-02-06', S2))
rg = om.run_G(mg, lambda d: True, 'L')
check('G1 stop at the other side of the range', tuple(round(v, 6) for v in rg[pd.Timestamp('2024-02-05')]), (round(-1.5 / 101.0, 6), 0, 2, 1, 0))
check('G1 no stop: out at the 09:29 close', tuple(round(v, 6) for v in rg[pd.Timestamp('2024-02-06')]), (round(1.0 / 101.0, 6), 0, 2, 1, 1))
print('ALL PASS' if not FAILS else f'{len(FAILS)} FAIL: {FAILS}')
sys.exit(1 if FAILS else 0)
