r"""Two risk controls from the institutional research (RISKCTL_PLAN.txt, pre-registered 2026-10-22).
    python riskctl.py   -> riskctl_output.txt
A: disaster stop per micro on the 16-year series; B: room-scaled micros in the Bulenox 50K evaluation.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import bulenox_routes as BR
import biglever as B
import src2_lib as L

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
P = BR.SIZES['50K']; SEED = 20261022
ERAS = (('NQ 2010-19', '2010-01-01', '2019-05-05'), ('MNQ 2019-26', '2019-05-06', '2026-12-31'),
        ('last 24 months', '2024-10-14', '2026-12-31'), ('last 12 months', '2025-10-14', '2026-12-31'))


def mdd(x):
    eq = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())


def eval_room(X, W, dd, dll, target, rule, resets=True):
    """biglever.evaluation with the day's micros chosen from the room before the session: X, W are 1-micro paths."""
    n_p, n_d = X.shape; bal = np.zeros(n_p); hi = np.zeros(n_p); alive = np.ones(n_p, bool); ok = np.zeros(n_p, bool); nres = np.zeros(n_p)
    for t in range(n_d):
        room = bal - (hi - dd); q = rule(room)
        x, w = B.day(X[:, t] * q, W[:, t] * q, dll)
        br = alive & (bal + w < hi - dd)
        bal = np.where(alive & ~br, bal + x, bal)
        if resets:
            rs = br & ((n_d - 1 - t) >= B.RESET_MIN_LEFT); nres += rs; bal[rs] = 0.0; hi[rs] = 0.0; br &= ~rs
        alive &= ~br
        hi = np.where(alive, np.maximum(hi, bal), hi); hit = alive & (bal >= target); ok |= hit; alive &= ~hit
    return ok, nres


def main():
    w = pd.read_csv(os.path.join(HERE, 'intraday_worst.csv'), index_col=0, parse_dates=True)
    usd, usdm = (w.r_close * L.N_USD).values, (w.r_min * L.N_USD).values; d = w.index; yrs = len(d) / 252
    base_yr = usd.mean() * 252
    pr(f"RISKCTL (RISKCTL_PLAN.txt). {len(d)} sessions; base ${base_yr:,.0f}/yr per micro, max DD ${mdd(usd):,.0f}, worst day ${usd.min():,.0f}, "
       f"worst intraday point ${usdm.min():,.0f}")
    pr("\nA. DISASTER STOP per micro (day ends at -L when its worst point reaches -L)")
    rec = None
    for Lv in (400, 600, 800, 1000):
        hit = usdm <= -Lv; x = np.where(hit, -Lv, usd)
        cost = base_yr - x.mean() * 252
        pr(f"   L ${Lv:>5}: triggers {hit.sum()} ({hit.sum() / yrs:.2f}/yr), ${x.mean() * 252:,.0f}/yr (cost ${cost:+,.0f}/yr = {cost / base_yr * 100:+.1f}%), "
           f"max DD ${mdd(x):,.0f}, worst day ${x.min():,.0f}")
        if rec is None and cost <= 0.01 * base_yr and hit.sum() / yrs <= 1.0: rec = Lv
    pr(f"   -> {'RECOMMENDED: a broker-held stop at $' + str(rec) + ' per micro' if rec else 'no level meets both conditions'}")
    pr("\nB. ROOM-SCALED EVALUATION MICROS (50K, resets)")
    rules = {'base 6': lambda room: np.full(room.shape, 6.0),
             'B1 6*room/2500, 2..6': lambda room: np.clip(np.round(6 * room / P['dd']), 2, 6),
             'B2 6*room/2500, 1..6': lambda room: np.clip(np.round(6 * room / P['dd']), 1, 6)}
    X, _ = BR.boot_paths(B.N_WIN, B.N_PATHS, usd, np.ones(len(d)), seed=SEED); W, _ = BR.boot_paths(B.N_WIN, B.N_PATHS, usdm, np.ones(len(d)), seed=SEED)
    st_all = np.arange(len(d) - B.N_WIN + 1); res = {}
    for name, rule in rules.items():
        ok, nres = eval_room(X, W, P['dd'], P['dll'], P['target'], rule)
        fees = (P['qual'] + B.RESET_FEE * nres.mean()) / ok.mean()
        eras = {}
        for lab, a, b in ERAS:
            st = st_all[(d[st_all] >= pd.Timestamp(a)) & (d[st_all] <= pd.Timestamp(b))]; idx = st[:, None] + np.arange(B.N_WIN)
            o2, r2 = eval_room(usd[idx], usdm[idx], P['dd'], P['dll'], P['target'], rule)
            eras[lab] = o2.mean()
        res[name] = (ok.mean(), fees, eras)
        pr(f"   {name}: bootstrap pass {ok.mean() * 100:.1f}% per try, resets {nres.mean():.2f}, fees per funded ${fees:,.0f} | " +
           ' | '.join(f"{k} {v * 100:.1f}%" for k, v in eras.items()))
    b = res['base 6']
    for name in list(rules)[1:]:
        p, f, e = res[name]
        g1 = f <= 0.9 * b[1]; g2 = e['last 24 months'] >= b[2]['last 24 months'] and e['last 12 months'] >= b[2]['last 12 months']
        g3 = not (e['NQ 2010-19'] < b[2]['NQ 2010-19'] and e['MNQ 2019-26'] < b[2]['MNQ 2019-26'])
        pr(f"   {name}: fees {(f / b[1] - 1) * 100:+.1f}% {'PASS' if g1 else 'FAIL'} | recent {'PASS' if g2 else 'FAIL'} | long {'PASS' if g3 else 'FAIL'} "
           f"-> {'BETTER' if g1 and g2 and g3 else 'not better'}")
    with open(os.path.join(HERE, 'riskctl_output.txt'), 'w', encoding='utf-8') as fh: fh.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
