r"""FUNDED6 (2026-10-24): the user's question "what if I trade 6 contracts in the evaluation and then funded?" - evaluation at 6 micros,
funded Masters at a FIXED 6 micros, against the plan (funded cushion sizing: 1 micro per $2,500 of cushion, max 4) and fixed 2 / 3 / 4,
at the user's prices ($20 evaluation, $170 activation, no resets). A projection on prices20.multi (fleet.Master / fleet.Evaluation,
audited); 1 funded account and 5 (evaluations bought for every free slot the same day). Lost funded accounts are replaced (new $20
evaluations + $170), so the cost of blowing up is in the numbers.
    python funded6.py   -> funded6_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import biglever as B, sizes as S, masterscale as MS, fleet as FL, prices20 as P2
import nav_lab as N

SEED = B.SEED_REPORT
SIZING = {'cushion 1 per $2,500 (plan)': lambda cu: np.clip(np.floor(cu / 2500), 1, 4), 'fixed 2': lambda cu: np.full_like(cu, 2.0),
          'fixed 3': lambda cu: np.full_like(cu, 3.0), 'fixed 4': lambda cu: np.full_like(cu, 4.0), 'fixed 6': lambda cu: np.full_like(cu, 6.0)}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
pc = lambda v, q: float(np.percentile(v, q))


def main():
    s = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    x_all, w_all, nt_all, _ = S.base_series('intraday_worst_extended.csv'); mnq = s.index > N.JOIN; sh = 0.5 * x_all.mean()
    scen = {'16-year history': (x_all, w_all, nt_all), 'MNQ era only (2019-26)': (x_all[mnq], w_all[mnq], nt_all[mnq]),
            'half the edge': (x_all - sh, w_all - sh, nt_all)}
    pr("FUNDED6: evaluation 6 micros ($20, no resets), funded $170; funded sizing compared; 20,000 paths, seed 20261022; MNQ 29,150, $0.61 a side")
    real = FL.size_rule
    try:
        for sc, (x, w, nt) in scen.items():
            X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, SEED)
            pr(f"\n=================== {sc}: ${x.mean() * 252:,.0f}/yr a micro ===================")
            pr(" ONE funded account on its own (2 years): payouts mean / median / 90th | paid at all | still open after 1 / 2 yrs | lost before 1st payout")
            for nm, fn in SIZING.items():
                pay, ou, mic = MS.master_dyn(X, W, NT, S.P50, fn); tot = pay.sum(1); fp = S.first_true(pay > 0)
                pr(f"   {nm:<28} ${tot.mean():>6,.0f} / ${np.median(tot):>6,.0f} / ${pc(tot, 90):>7,.0f} | {np.mean(tot > 0) * 100:5.1f}% | "
                   f"{np.mean(ou > 251) * 100:5.1f}% / {np.mean(ou > 503) * 100:5.1f}% | {np.mean((ou < fp) & (ou < B.N_FUNDED)) * 100:5.1f}%")
            for cap in (1, 5):
                pr(f" {cap} funded account{'s' if cap > 1 else ''} slot{'s' if cap > 1 else ''}, lost accounts replaced (net after every fee): "
                   "when: NET mean / median / 10th / 90th | P(ahead) | out of pocket med / 90th | funded accounts activated")
                for nm, fn in SIZING.items():
                    FL.size_rule = fn
                    sn = P2.multi(X, W, NT, cap, 6, True)
                    row = []
                    for lab, t in (('12 mo', 252), ('24 mo', 504)):
                        d = sn[t]
                        row.append(f"{lab}: ${d['net'].mean():>7,.0f} / ${np.median(d['net']):>7,.0f} / ${pc(d['net'], 10):>7,.0f} / ${pc(d['net'], 90):>7,.0f} | "
                                   f"{np.mean(d['net'] > 0) * 100:4.1f}% | ${np.median(d['oop']):>5,.0f} / ${pc(d['oop'], 90):>5,.0f} | {d['act'].mean():5.2f}")
                    pr(f"   {nm:<28} " + '   ||   '.join(row))
                    FL.size_rule = real
    finally:
        FL.size_rule = real
    with open(os.path.join(HERE, 'funded6_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
