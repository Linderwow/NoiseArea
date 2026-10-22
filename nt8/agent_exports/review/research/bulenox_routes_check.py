r"""Independent checks of bulenox_routes.py (BULENOX_ROUTES_PLAN.txt).
    python bulenox_routes_check.py
 1. T1 re-implemented with pandas straight from the .npz caches (own session logic, raw prices within a contract) - per event match.
 2. The FOMC-eve diagnostic vs T1: which events differ and why.
 3. Part B's payout bookkeeping on hand-computable synthetic paths.
 4. Part B's qualification check vs longhist_bulenox.txt with the trailing threshold stopping at the start balance (that file's rule).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import bulenox_routes as B
import src2_lib as L

out = []
def pr(*a): print(*a); out.append(' '.join(str(x) for x in a))


def own_t1(name, lo, hi):
    z = np.load(os.path.join(HERE, 'regime_work', f'{name}_1m.npz'))
    t = pd.DatetimeIndex(z['t']); o = z['o']; iid = z['iid']; sh = z['shift']
    s = pd.DataFrame({'o': o, 'iid': iid, 'ao': o + sh}, index=t)
    res = {}
    for d in B.FOMC:
        if not (pd.Timestamp(lo) <= d <= pd.Timestamp(hi)): continue
        ev = s.loc[d - pd.Timedelta(days=1) + pd.Timedelta(hours=18, minutes=5): d - pd.Timedelta(days=1) + pd.Timedelta(hours=18, minutes=29)]
        ex = s.loc[d + pd.Timedelta(hours=9, minutes=30): d + pd.Timedelta(hours=9, minutes=30)]
        if ev.empty or ex.empty: continue
        e0, x0 = ev.iloc[0], ex.iloc[0]
        r = (x0.o - e0.o) / x0.o if e0.iid == x0.iid else (x0.ao - e0.ao) / x0.o
        res[d] = r
    return pd.Series(res)


def main():
    P = B.Pan('nq', 'mnqx')
    mine = pd.Series({P.dates[k]: r for k, r in B.t1(P)})
    own = pd.concat([own_t1('nq', L.DISC[0], '2019-05-05'), own_t1('mnqx', '2019-05-06', '2026-10-16')])
    both = mine.index.intersection(own.index); diff = (mine[both] - own[both]).abs()
    pr(f"1. T1: bulenox_routes {len(mine)} events, own pandas {len(own)}; common {len(both)}; max |diff| {diff.max() * 1e4:.4f} bp; "
       f"only in routes {sorted(d.date() for d in mine.index.difference(own.index))}; only in own {sorted(d.date() for d in own.index.difference(mine.index))}")
    pr(f"   own pandas, net of 2 x 1 tick: mean {(own - 2 * L.side_cost(1)).mean() * 1e4:+.1f} bp, event t {B.tstat(own - 2 * L.side_cost(1)):+.2f}")
    # 2. diagnostic sample vs T1
    diag = []
    for d in B.FOMC:
        k = P.k_of.get(d)
        if k is None or k == 0 or P.daily.seg.iat[k - 1] != P.daily.seg.iat[k]: continue
        if P.bar_at(k - 1, 0, (16, 55), 5) is None or P.night_entry(k) is None: continue
        diag.append(d)
    miss = mine.index.difference(pd.DatetimeIndex(diag))
    pr(f"2. T1 events not in the 16:00-split diagnostic: " + ', '.join(f"{d.date()} {mine[d] * 1e4:+.1f} bp" for d in miss))
    g = mine[mine.index.isin(diag)]; pr(f"   T1 on the diagnostic's {len(g)} nights, gross: mean {g.mean() * 1e4:+.1f} bp, t {B.tstat(g):+.2f}")
    # 3. synthetic payout paths
    p = B.SIZES['25K']
    X = np.full((1, 80), 100.0); NT = np.ones((1, 80)); pay, ou = B.master(X, NT, p)
    days = np.where(pay[0] > 0)[0]
    pr(f"3. +$100 every day, 25K: payouts on days {list(days + 1)} amounts {list(pay[0][days])}; expected first on day 26 ($2,600 = reserve"
       f" $1,600 + $1,000) paying $900; next after 3 wait days + 10 trading days + balance back to $2,600")
    X2 = X.copy(); X2[0, 5] = 1200.0; pay2, _ = B.master(X2, NT, p); d2 = np.where(pay2[0] > 0)[0]
    pr(f"   same with a +$1,200 day 6 (consistency: best day must be <= 40% of the period's P&L): first payout day {d2[0] + 1 if len(d2) else None}"
       f" (period P&L reaches $3,000 on day {int(np.ceil((3000 - 1200) / 100)) + 1}); balance check alone would allow day 15")
    X3 = np.full((1, 30), -600.0); _, ou3 = B.master(X3, np.ones((1, 30)), p)
    pr(f"   -$600 every day, 25K: daily loss limit caps each day at -$500 -> closed on day {ou3[0] + 1} (expected day 4: -$2,000 < -$1,500)")
    # 4. qualification with the trailing stop at the start balance (longhist's rule) vs Bulenox's qualification text (no stop)
    d = pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0, parse_dates=True)
    Xb, _ = B.boot_paths(504, 20000, (d.r_today * L.N_USD).values, d.nt.values.astype(float))
    res = []
    for nm, q in B.SIZES.items():
        n_p = Xb.shape[0]; bal = np.zeros(n_p); hi = np.zeros(n_p); alive = np.ones(n_p, bool); ok = np.zeros(n_p, bool)
        for t in range(504):
            bal = np.where(alive, bal + Xb[:, t], bal); thr = np.minimum(hi - q['dd'], 0.0); alive &= ~(bal < thr)
            hi = np.where(alive, np.maximum(hi, bal), hi); hit = alive & (bal >= q['target']); ok |= hit; alive &= ~hit
        res.append(f"{nm} {ok.mean() * 100:.0f}%")
    pr("4. qualification in 2 years, trailing stops at the start balance (longhist's rule): " + ' | '.join(res) + "  (longhist: 63/70/69/58%)")
    with open(os.path.join(HERE, 'bulenox_routes_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
