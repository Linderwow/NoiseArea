r"""Volatility-scaled micros in the Bulenox 50K evaluation (EVALVOL_PLAN.txt, pre-registered 2026-10-22).
    python evalvol.py   -> evalvol_output.txt
Same evaluation simulator as biglever.py; only the micros per day change.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import bulenox_routes as BR
import biglever as B
import nav_lab as N
import src2_lib as L

SEED = 20261022; P = BR.SIZES['50K']; SLOT_1600 = (960 - 575) // 5
ERAS = (('NQ 2010-19', '2010-01-01', '2019-05-05'), ('MNQ 2019-26', '2019-05-06', '2026-12-31'),
        ('last 24 months', '2024-10-14', '2026-12-31'), ('last 12 months', '2025-10-14', '2026-12-31'))
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def sigma_1600(dates):
    """The noise-area sigma at the 16:00 slot for each session (NQ up to the join, MNQ after), as the strategy computes it."""
    s = pd.Series(np.nan, index=dates)
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQ', lambda d: d > N.JOIN)):
        mk = N.market(nm); sg = mk.sigma(14)[:, SLOT_1600]
        ser = pd.Series(sg, index=mk.dates); sel = [d for d in dates if keep(d)]
        s.loc[sel] = ser.reindex(sel).values
    return s


def main():
    w = pd.read_csv(os.path.join(HERE, 'intraday_worst.csv'), index_col=0, parse_dates=True)
    usd, usdm = (w.r_close * L.N_USD).values, (w.r_min * L.N_USD).values; d = w.index
    s = sigma_1600(d); assert s.notna().all(), f"{int(s.isna().sum())} sessions without a sigma"
    s_ref = float(s[s.index <= N.JOIN].median())
    raw = 6 * s_ref / s.values
    rules = {'base 6': np.full(len(d), 6.0), 'E1 2..10': np.clip(np.round(raw), 2, 10), 'E2 2..6': np.clip(np.round(raw), 2, 6)}
    pr(f"EVALVOL (EVALVOL_PLAN.txt). {len(d)} sessions {d[0].date()} .. {d[-1].date()}; s_ref (NQ 2010-19 median) {s_ref * 100:.3f}%; "
       f"sigma now {s.iloc[-1] * 100:.3f}% -> E1 {int(rules['E1 2..10'][-1])}, E2 {int(rules['E2 2..6'][-1])} micros on the last session")
    res = {}
    for name, q in rules.items():
        X, _ = BR.boot_paths(B.N_WIN, B.N_PATHS, usd * q, np.ones(len(d)), seed=SEED); W, _ = BR.boot_paths(B.N_WIN, B.N_PATHS, usdm * q, np.ones(len(d)), seed=SEED)
        ok, nres = B.evaluation(X, W, P['dd'], P['dll'], P['target'], resets=True)
        fees = (P['qual'] + B.RESET_FEE * nres.mean()) / ok.mean()
        touch = float((W <= -P['dll']).sum(1).mean())
        eras = {}
        st_all = np.arange(len(d) - B.N_WIN + 1)
        for lab, a, b in ERAS:
            st = st_all[(d[st_all] >= pd.Timestamp(a)) & (d[st_all] <= pd.Timestamp(b))]; idx = st[:, None] + np.arange(B.N_WIN)
            o2, r2 = B.evaluation(usd[idx] * q[idx], usdm[idx] * q[idx], P['dd'], P['dll'], P['target'], resets=True)
            eras[lab] = (o2.mean(), (P['qual'] + B.RESET_FEE * r2.mean()) / o2.mean() if o2.mean() > 0 else np.inf)
        res[name] = (ok.mean(), fees, eras)
        pr(f"\n{name}: average {q.mean():.2f} micros (floor {np.mean(q == q.min()) * 100:.0f}% / cap {np.mean(q == q.max()) * 100:.0f}% of days); "
           f"bootstrap pass {ok.mean() * 100:.1f}% per try, resets {nres.mean():.2f}, fees per funded ${fees:,.0f}, DLL touches {touch:.1f} per window")
        pr("   real windows: " + ' | '.join(f"{lab} {p * 100:.1f}% ${f:,.0f}" for lab, (p, f) in eras.items()))
    b = res['base 6']
    for name in ('E1 2..10', 'E2 2..6'):
        p, f, e = res[name]
        g1 = f <= 0.9 * b[1]
        g2 = e['last 24 months'][0] >= b[2]['last 24 months'][0] and e['last 12 months'][0] >= b[2]['last 12 months'][0]
        g3 = not (e['NQ 2010-19'][0] < b[2]['NQ 2010-19'][0] and e['MNQ 2019-26'][0] < b[2]['MNQ 2019-26'][0])
        pr(f"\n{name}: (1) fees ${f:,.0f} vs ${b[1]:,.0f} ({(f / b[1] - 1) * 100:+.1f}%) {'PASS' if g1 else 'FAIL'} | (2) recent eras "
           f"{'PASS' if g2 else 'FAIL'} | (3) long eras {'PASS' if g3 else 'FAIL'} -> {'BETTER' if g1 and g2 and g3 else 'not better'}")
    with open(os.path.join(HERE, 'evalvol_output.txt'), 'w', encoding='utf-8') as fh: fh.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
