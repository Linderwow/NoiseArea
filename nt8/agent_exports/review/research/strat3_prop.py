r"""STRAT3 diagnostic (2026-10-24, after the verdict): the chandelier exit's effect on the Bulenox 50K evaluation (6 micros, resets)
and the funded Master (cushion sizing), with each session's intraday worst point under the exit. Not a gate - CH was rejected by
STRAT3's significance bar; this shows what it would do to the prop account.
    python strat3_prop.py   -> strat3_prop_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX, metalabel as ML, strat3 as S3
import biglever as B, sizes as S, masterscale as MS
from research_engine import WARMUP

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def series(kw):
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 + 0.25) / p_ref; parts = []
    for nm, keep in ML.PARTS:
        mk = N.market(nm); w = np.full(mk.n, np.nan); p, n, _ = S3.run(mk, worst_out=w, **kw); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c, 'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c,
                                   'nt': n[sel]}, index=d[sel]))
    s = pd.concat(parts)
    x = s.r_close.values * S.NOTIONAL - s.nt.values * S.COMM_EXTRA_RT; w = s.r_min.values * S.NOTIONAL - s.nt.values * S.COMM_EXTRA_RT
    return s, x, w, s.nt.values.astype(float)


def main():
    ref = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    s0, x0, w0, n0 = series({})
    assert s0.index.equals(ref.index) and float((s0.r_close - ref.r_close).abs().max()) < 1e-12 and float((s0.r_min - ref.r_min).abs().max()) < 1e-12, "audit"
    pr("audit: strat3.run with no switch reproduces intraday_worst_extended.csv (close and worst) exactly")
    pr(" exits                 $/yr a micro  days touching -$1,100 at 6 micros   eval pass w/ resets (6 micros)  fees per funded  Master 2-yr payouts (cushion)  any payout  open after 1 yr")
    for name, kw in (('live rules', {}), ('CH 3 x ATR (pre-registered)', dict(ch=3.0)), ('CH 2 x ATR (plateau best)', dict(ch=2.0))):
        s, x, w, nt = series(kw) if kw else (s0, x0, w0, n0)
        e = S.eval_one(x, w, nt, 6, True)
        X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, B.SEED_REPORT)
        pay, ou, _ = MS.master_dyn(X, W, NT, S.P50, lambda cu: np.clip(np.floor(cu / 2500), 1, 4)); tot = pay.sum(1)
        pr(f" {name:<28} ${x.mean() * 252:>6,.0f}        {np.mean(w * 6 <= -1100) * 100:5.1f}%                          {e['p'] * 100:5.1f}%"
           f"                    ${e['fees']:>5,.0f}          ${tot.mean():>7,.0f}                {np.mean(tot > 0) * 100:5.1f}%      {np.mean(ou > 251) * 100:5.1f}%")
    with open(os.path.join(HERE, 'strat3_prop_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
