r"""DAYCALL BAR (DAYCALL_BAR_PLAN.txt, 2026-10-26): how accurate a day-type call must be before REGIME3's stand-down gate beats
NoiseArea. Information only.
    python regime3_bar.py    -> regime3_bar_output.txt
"""
import os, sys, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import regime3 as R3                                    # sets one BLAS thread before numpy loads
import numpy as np, pandas as pd, json
from math import erf, sqrt
import regime_test as rt

AUCS = (0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.00); DRAWS = 500; SEED = 20261026
OUT = os.path.join(HERE, 'regime3_bar_output.txt')


def phi_inv(p):
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if 0.5 * (1 + erf(mid / sqrt(2))) < p: lo = mid
        else: hi = mid
    return (lo + hi) / 2


def maxdd(usd):
    eq = np.cumsum(usd, axis=-1); z = np.zeros(eq.shape[:-1] + (1,))
    e0 = np.concatenate([z, eq], axis=-1); return (np.maximum.accumulate(e0, axis=-1) - e0).max(axis=-1)


def run(J, per, label_name, j, rng, Nusd, na_sh, na_usd, na_dd):
    meds = json.load(open(R3.MODEL_JSON))['medians']
    y = R3.labels(J, j, meds[str(R3.CALLS[j])])[label_name][per]
    ok = J['OK'][per, j] & np.isfinite(y); yl = np.nan_to_num(y)
    rf, rs = J['Rf'][per], J['Rs'][per, j]; n = per.sum(); yrs = n / 252; rows = []
    for a in AUCS:
        if a >= 1.0:
            off = (ok & (yl == 0))[None, :]; right = np.array([1.0])
        else:
            d = sqrt(2) * phi_inv(a); sc = d * yl[None, :] + rng.standard_normal((DRAWS, n))
            call1 = sc >= d / 2; off = ok[None, :] & ~call1
            right = ((call1 == (yl[None, :] == 1)) & ok[None, :]).sum(1) / ok.sum()
        r = np.where(off, rs[None, :], rf[None, :]); usd = r * Nusd
        sh = r.mean(1) / r.std(1, ddof=1) * np.sqrt(252); yr_usd = usd.sum(1) / yrs; dd = maxdd(usd)
        prop = (dd <= 0.6 * na_dd) & (yr_usd >= 0.6 * na_usd)
        rows.append(dict(auc=a, right=np.median(right) * 100, usd_yr=np.median(yr_usd), sharpe=np.median(sh), maxdd=np.median(dd),
                         beat=np.mean(sh > na_sh) * 100, prop=np.mean(prop) * 100, stood=np.median(off.mean(1)) * 100))
    return pd.DataFrame(rows)


def main():
    R3.below_normal(); log = [f"=== regime3_bar.py ({dt.datetime.now():%Y-%m-%d %H:%M}) ==="]
    J = R3.build('MNQ'); d = J['dates']; Nusd = 2 * J['p_ref']; rng = np.random.default_rng(SEED)
    for pname, per in (('HOLDOUT 2019-05-06 .. 2026-10-14', rt.between(d, rt.HOLD)), ('WHOLE 2010-07 .. 2026-10-14', np.ones(len(d), bool))):
        na = J['Rf'][per]; na_usd_d = na * Nusd; na_sh = rt.sharpe(na); na_usd = na_usd_d.sum() / (per.sum() / 252); na_dd = maxdd(na_usd_d)
        log.append(f"\n##### {pname}: NoiseArea ${na_usd:,.0f}/yr, Sharpe {na_sh:.2f}, max DD ${na_dd:,.0f} (per micro, reference notional)")
        for j, t in enumerate(R3.CALLS):
            for lb in R3.LABELS:
                tab = run(J, per, lb, j, rng, Nusd, na_sh, na_usd, na_dd)
                b1 = tab[tab.beat >= 50].auc.min(); b2 = tab[tab.prop >= 50].auc.min()
                log.append(f"\n{R3.CALL_TXT[t]} call, label {lb}: bar to beat NoiseArea's Sharpe = AUC {b1:.2f}; bar for the prop rule = AUC {b2:.2f}")
                log.append(tab.to_string(index=False, formatters={'auc': '{:.2f}'.format, 'right': '{:.1f}%'.format, 'usd_yr': '${:,.0f}'.format,
                                                                 'sharpe': '{:.2f}'.format, 'maxdd': '${:,.0f}'.format, 'beat': '{:.0f}%'.format,
                                                                 'prop': '{:.0f}%'.format, 'stood': '{:.0f}%'.format}))
    txt = '\n'.join(log); print(txt)
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write(txt + '\n')


if __name__ == '__main__':
    main()
