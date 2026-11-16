import os, sys, types
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2
import ntline as N

R = []


def rec(n, ok, d=''):
    R.append(bool(ok)); print(('PASS  ' if ok else 'FAIL  ') + n + (f'  [{d}]' if d else ''))


def fake(o, h, l, c, sig, m0=600, tday=None, iid=None):
    n = len(o); D = types.SimpleNamespace()
    D.o, D.h, D.l, D.c = (np.array(x, float) for x in (o, h, l, c)); D.sig = np.array(sig, np.int8); D.agree = np.ones(n, bool)
    D.m = m0 + np.arange(n); D.tday = np.array(tday if tday is not None else ['2015-03-02'] * n, dtype='datetime64[D]'); D.iid = np.array(iid if iid is not None else [1] * n)
    D.segments = types.MethodType(N.Data.segments, D)
    return D


def main():
    r5 = 5.0 / L2.P_REF; p = 1000.0
    D = fake([p] * 6, [p, p, p, p, p * (1 + r5 * 1.2), p], [p] * 6, [p] * 6, [0, 0, 1, 0, 0, 0])
    T = N.simulate(D, 'BR', False, False, 5.0)
    rec('bracket long: signal at a close fills at the next open and exits at the target', len(T) == 1 and abs(T.ret.iat[0] - r5) < 1e-12, str(T.ret.tolist()))
    D = fake([p] * 6, [p, p, p, p, p * (1 + r5 * 1.2), p], [p, p, p, p, p * (1 - r5 * 1.2), p], [p] * 6, [0, 0, 1, 0, 0, 0])
    T = N.simulate(D, 'BR', False, False, 5.0)
    rec('bracket: both touched in one bar -> stop first', len(T) == 1 and abs(T.ret.iat[0] + r5) < 1e-12)
    D = fake([p] * 6, [p] * 6, [p, p, p, p * (1 - r5 * 1.5), p, p], [p] * 6, [0, 0, 1, 0, 0, 0])
    T = N.simulate(D, 'BR', False, False, 5.0)
    rec('bracket: the entry bar itself can stop out at the stop price', len(T) == 1 and abs(T.ret.iat[0] + r5) < 1e-12)
    D = fake([100, 101, 102, 103, 104, 105, 106, 107], [110] * 8, [90] * 8, [100, 101, 102, 103, 104, 105, 106, 108], [0, 1, 0, 0, -1, 0, 0, 0])
    T = N.simulate(D, 'SAR', False, False)
    rec('stop-and-reverse: long from the next open, reversed at the open after the opposite signal, last trade closed at the session end',
        len(T) == 2 and list(T.side) == [1, -1] and abs(T.ret.iat[0] - (105 - 102) / 102) < 1e-12 and abs(T.ret.iat[1] + (108 - 105) / 105) < 1e-12, str(T.round(5).values.tolist()))
    D = fake([100] * 6, [110] * 6, [90] * 6, [100] * 6, [0, 0, 1, 0, 0, 0], tday=['2015-03-02'] * 3 + ['2015-03-03'] * 3)
    T = N.simulate(D, 'SAR', False, False)
    rec('a signal on the last bar of a session is not traded the next day', len(T) == 0)
    D = fake([100] * 6, [110] * 6, [90] * 6, [100, 100, 100, 100, 100, 99], [0, 1, 0, 0, 0, 0], m0=956)
    T = N.simulate(D, 'SAR', False, True)
    rec('regular hours: the position is closed at the 15:59 bar close', len(T) == 1 and abs(T.ret.iat[0]) < 1e-12, str(T.values.tolist()))
    ok = all(R); print('ALL PASS' if ok else f'{R.count(False)} FAILED'); return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
