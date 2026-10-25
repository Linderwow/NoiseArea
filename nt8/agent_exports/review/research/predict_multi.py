r"""PREDICT_MULTI (2026-10-24): forecast for 1 to 5 Bulenox funded accounts at 6, 12 and 24 months - totals and per account.
    python predict_multi.py   -> predict_multi_output.txt
Setup = FLEET part A: one 6-micro evaluation at a time (resets with 5+ sessions left, a new purchase after a failed try), a pass
becomes a funded Master the next session while fewer than N are open, every Master on cushion sizing (1 micro per $2,500, max 4),
payouts as soon as allowed, fees paid when due. Every account trades the SAME NoiseArea sessions (one bootstrap path per run).
Inputs as predict.py (intraday_worst_extended.csv, MNQ 29,150, $0.61 a side, 20,000 paths, seed 20261022); engines fleet.Master /
fleet.Evaluation (audited against masterscale.master_dyn and biglever.evaluation). A projection of the history, not a promise.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import biglever as B, sizes as S, fleet as FL
import nav_lab as N

SEED = B.SEED_REPORT; CP = {'6 months': 126, '12 months': 252, '24 months': 504}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
pc = lambda v, q: float(np.percentile(v, q))


def multi(X, W, NT, cap):
    n = X.shape[0]; ms = [FL.Master(n) for _ in range(cap)]; ev = FL.Evaluation(n); pending = np.zeros(n, bool)
    fees = np.zeros(n); pays = np.zeros(n); cash_min = np.zeros(n); activated = np.zeros(n); acct_sessions = np.zeros(n); snap = {}
    for t in range(B.N_FUNDED):
        if pending.any():
            for m in ms:
                go = pending & ~m.alive
                if go.any(): m.start(go); fees[go] += FL.ACT_FEE; pending &= ~go; activated += go
        n_open = sum(m.alive.astype(int) for m in ms)
        new = ~ev.active & ~pending & (n_open < cap)
        if new.any(): ev.start(new); fees[new] += FL.EVAL_FEE
        for m in ms: pays += m.step(X[:, t], W[:, t], NT[:, t])[0]
        hit, rs = ev.step(X[:, t], W[:, t]); fees[rs] += B.RESET_FEE; pending |= hit
        open_now = sum(m.alive.astype(int) for m in ms); acct_sessions += open_now
        cash_min = np.minimum(cash_min, pays - fees)
        if t + 1 in CP.values(): snap[t + 1] = dict(pay=pays.copy(), fees=fees.copy(), net=pays - fees, oop=-cash_min.copy(), open=open_now.copy(),
                                                   act=activated.copy(), am=acct_sessions.copy() / 21)
    return snap


def main():
    s = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    x_all, w_all, nt_all, _ = S.base_series('intraday_worst_extended.csv'); mnq = s.index > N.JOIN
    sh = 0.5 * x_all.mean()
    scen = {'16-year history': (x_all, w_all, nt_all), 'MNQ era only (2019-26)': (x_all[mnq], w_all[mnq], nt_all[mnq]),
            'half the edge': (x_all - sh, w_all - sh, nt_all)}
    pr(f"PREDICT_MULTI: {len(s)} sessions to {s.index[-1].date()}; MNQ 29,150, $0.61 a side; {B.N_PATHS:,} paths, seed {SEED}; fees paid when due; "
       f"every account trades the same sessions")
    for sc, (x, w, nt) in scen.items():
        X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, SEED)
        pr(f"\n=================== {sc}: ${x.mean() * 252:,.0f}/yr a micro ===================")
        pr(" accounts  when        payouts mean / median / 90th     fees    NET mean   median    10th     90th   P(ahead)  P(net<-$1k)  "
           "out of pocket med/90th  open now  activated  payout per open account-month   NET per account slot")
        for cap in (1, 2, 3, 4, 5):
            sn = multi(X, W, NT, cap)
            for lab, t in CP.items():
                d = sn[t]; ppm = d['pay'].sum() / max(d['am'].sum(), 1e-9)
                pr(f"    {cap}      {lab:<10} ${d['pay'].mean():>7,.0f} / ${np.median(d['pay']):>6,.0f} / ${pc(d['pay'], 90):>7,.0f}  ${d['fees'].mean():>5,.0f}  "
                   f"${d['net'].mean():>7,.0f}  ${np.median(d['net']):>7,.0f}  ${pc(d['net'], 10):>7,.0f}  ${pc(d['net'], 90):>7,.0f}   {np.mean(d['net'] > 0) * 100:5.1f}%     "
                   f"{np.mean(d['net'] < -1000) * 100:5.1f}%      ${np.median(d['oop']):>5,.0f} / ${pc(d['oop'], 90):>5,.0f}      {d['open'].mean():4.2f}      "
                   f"{d['act'].mean():4.2f}            ${ppm:>5,.0f}                      ${d['net'].mean() / cap:>6,.0f}")
            if cap < 5: pr("")
    with open(os.path.join(HERE, 'predict_multi_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
