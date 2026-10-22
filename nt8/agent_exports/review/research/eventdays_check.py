r"""Independent checks of event_days.py (EVENTDAYS_PLAN.txt).
    python eventdays_check.py  -> eventdays_check_output.txt
 1. E1, E3, E4 re-implemented with pandas straight from the .npz caches (own session logic, raw prices within one contract) - per event.
 2. The MACRO release dates that have no session in the panel, and why.
 3. The auction close times used (13:00 vs early closes).
(The BLS dates were checked separately against bls.gov: identical checksums for CPI / PPI / NFP, see EVENTDAYS_RESULTS.txt.)
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import event_days as E
import src2_lib as L
from bulenox_routes import Pan

out = []
def pr(*a): print(*a); out.append(' '.join(str(x) for x in a))


def frame(name):
    z = np.load(os.path.join(HERE, 'regime_work', f'{name}_1m.npz'))
    return pd.DataFrame({'o': z['o'], 'c': z['c'], 'iid': z['iid'], 'ao': z['o'] + z['shift'], 'ac': z['c'] + z['shift']}, index=pd.DatetimeIndex(z['t']))


def own(df, dates, kind, auct=None):
    res = {}
    for d in dates:
        day = df.loc[d: d + pd.Timedelta(hours=23, minutes=59)]
        rth = day.between_time('09:30', '15:59')
        if rth.empty or rth.index[0] != d + pd.Timedelta(hours=9, minutes=30) or len(rth) < 200: continue
        last = rth.iloc[-1]; px = rth.iloc[0].o
        if kind == 'E1':
            w = day.between_time('08:25', '08:29')
            if w.empty: continue
            e = w.iloc[0]; res[d] = ((last.c - e.o) if e.iid == last.iid else (last.ac - e.ao)) / px
        elif kind == 'E3':
            w = day.between_time('08:30', '08:34')
            if w.empty: continue
            e = w.iloc[0]; o930 = rth.iloc[0]; s = np.sign(o930.ao - e.ao)
            if s != 0: res[d] = s * (last.ac - o930.ao) / px
        else:
            c = auct.get(d, 780) + 1; t0 = d + pd.Timedelta(minutes=c); w = day.loc[t0: t0 + pd.Timedelta(minutes=4)]
            if w.empty: continue
            e = w.iloc[0]; res[d] = ((last.c - e.o) if e.iid == last.iid else (last.ac - e.ao)) / px
    return pd.Series(res, dtype=float)


def main():
    fam, auct, ndx = E.calendars(); macro = sorted(fam['cpi'] | fam['ppi'] | fam['empsit']); P = Pan('nq', 'mnqx')
    kM, kA = E.kset(P, macro), E.kset(P, auct)
    nq, mnq = frame('nq'), frame('mnqx'); J = pd.Timestamp('2019-05-05'); lo = pd.Timestamp(L.DISC[0])
    for kind, fn, ks, dates in (('E1', E.e1, kM, macro), ('E3', E.e3, kM, macro), ('E4', lambda P_, k_: E.e4(P_, k_, auct), kA, sorted(auct))):
        mine = pd.Series({P.dates[k]: r for k, r in fn(P, ks)})
        dd = [d for d in dates if lo <= d <= P.dates[-1]]
        o = pd.concat([own(nq, [d for d in dd if d <= J], kind, auct), own(mnq, [d for d in dd if d > J], kind, auct)])
        both = mine.index.intersection(o.index); diff = (mine[both] - o[both]).abs()
        pr(f"1. {kind}: event_days {len(mine)}, own pandas {len(o)}, common {len(both)}, max |diff| {diff.max() * 1e4:.4f} bp; only in event_days "
           f"{[str(d.date()) for d in mine.index.difference(o.index)][:6]}; only in own {[str(d.date()) for d in o.index.difference(mine.index)][:6]}")
        pr(f"   own pandas mean {(o - 2 * L.side_cost(1)).mean() * 1e4:+.2f} bp net vs event_days {(mine - 2 * L.side_cost(1)).mean() * 1e4:+.2f}")
    miss = [d for d in macro if lo <= d <= P.dates[-1] and d not in P.k_of]
    pr(f"2. MACRO dates without a panel session ({len(miss)}): " + ', '.join(f"{d.date()} {d.day_name()[:3]}" for d in miss))
    a = pd.Series(auct); pr(f"3. auction close times: " + ', '.join(f"{v // 60:02d}:{v % 60:02d} x{n}" for v, n in a.value_counts().items())
                           + " | early ones: " + ', '.join(str(d.date()) for d, v in a.items() if v != 780))
    with open(os.path.join(HERE, 'eventdays_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
