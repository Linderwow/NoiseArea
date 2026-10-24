r"""PREDICT (2026-10-24): one forecast from the audited engines - NoiseArea per micro, the Bulenox 6-micro evaluation, one funded
Master (cushion sizing), and the $1,000 wallet ladder to 5 funded accounts - at 1, 3, 6, 12 and 24 months (21-session months).
    python predict.py   -> predict_output.txt
Inputs: intraday_worst_extended.csv (16 years + the recorded sessions), MNQ 29,150, $0.61 a side; 21-session block bootstrap,
20,000 paths, seed 20261022. Scenarios: 16-year history; MNQ era only (2019-05 on, bootstrapped from those sessions); half the edge.
Engines: biglever.paths, firms.evaluation (audited vs biglever.evaluation), masterscale.master_dyn, fleet.Master / fleet.Evaluation
(audited vs master_dyn / biglever.evaluation). A projection of the history, not a promise.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import biglever as B, sizes as S, masterscale as MS, fleet as FL, firms as FM
import nav_lab as N

SEED = B.SEED_REPORT; CP = {'1 month': 21, '3 months': 63, '6 months': 126, '12 months': 252, '24 months': 504}
rule = lambda cu: np.clip(np.floor(cu / 2500), 1, 4)
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
pc = lambda v, q: float(np.percentile(v, q))


def ladder(X, W, NT, cap=5, wallet0=1000.0):
    """fleet.fleet's wallet ladder with the net cash and open Masters recorded at every checkpoint."""
    n = X.shape[0]; ms = [FL.Master(n) for _ in range(cap)]; ev = FL.Evaluation(n); pending = np.zeros(n, bool); waited = np.zeros(n, int)
    fees = np.zeros(n); pays = np.zeros(n); snap = {}
    afford = lambda fee: wallet0 + pays - fees >= fee
    for t in range(B.N_FUNDED):
        if pending.any():
            for m in ms:
                go = pending & ~m.alive & afford(FL.ACT_FEE)
                if go.any(): m.start(go); fees[go] += FL.ACT_FEE; pending &= ~go
            waited[pending] += 1; pending &= ~(pending & (waited > 5))
        waited[~pending] = 0
        n_open = sum(m.alive.astype(int) for m in ms); new = ~ev.active & ~pending & (n_open < cap) & afford(FL.EVAL_FEE)
        if new.any(): ev.start(new); fees[new] += FL.EVAL_FEE
        for m in ms: pays += m.step(X[:, t], W[:, t], NT[:, t])[0]
        hit, rs = ev.step(X[:, t], W[:, t], afford(B.RESET_FEE)); fees[rs] += B.RESET_FEE; pending |= hit
        if t + 1 in CP.values(): snap[t + 1] = (pays - fees, sum(m.alive.astype(int) for m in ms), pays.copy())
    return snap


def main():
    s = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    x_all, w_all, nt_all, _ = S.base_series('intraday_worst_extended.csv')
    mnq = (s.index > N.JOIN)
    pr(f"PREDICT: {len(s)} sessions {s.index[0].date()} .. {s.index[-1].date()} (MNQ era {int(mnq.sum())} from {s.index[mnq][0].date()}); "
       f"MNQ 29,150, $0.61 a side; {B.N_PATHS:,} paths, seed {SEED}")
    scen = {'16-year history': (x_all, w_all, nt_all), 'MNQ era only (2019-26)': (x_all[mnq], w_all[mnq], nt_all[mnq])}
    sh = 0.5 * x_all.mean(); scen['half the edge'] = (x_all - sh, w_all - sh, nt_all)
    for sc, (x, w, nt) in scen.items():
        pr(f"\n=================== {sc}: ${x.mean() * 252:,.0f}/yr a micro, daily sd ${x.std():,.0f} ===================")
        X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, SEED)
        cum = X.cumsum(1)
        pr("A. NOISEAREA, 1 micro (Sim101):            mean    median   10th pct   90th pct   P(loss)")
        for lab, t in CP.items():
            v = cum[:, t - 1]; pr(f"   after {lab:<10}                    ${v.mean():>7,.0f}  ${np.median(v):>7,.0f}  ${pc(v, 10):>8,.0f}  ${pc(v, 90):>8,.0f}   {np.mean(v < 0) * 100:5.1f}%")
        e12 = cum[:, :252]; dd12 = (np.maximum.accumulate(np.c_[np.zeros(len(e12)), e12], 1)[:, 1:] - e12).max(1)
        pr(f"   deepest drawdown within 12 months: median ${np.median(dd12):,.0f}, 90th pct ${pc(dd12, 90):,.0f}, 99th pct ${pc(dd12, 99):,.0f}; "
           f"losing months {np.mean(X[:, :252].reshape(len(X), 12, 21).sum(2) < 0) * 100:.0f}%")
        XE, WE, NTE = B.paths(252, x, w, nt, 1, SEED)
        ok1 = S.eval_one(x, w, nt, 6, False)['p']; okr = S.eval_one(x, w, nt, 6, True)['p']
        p, when, fees = FM.evaluation(XE, WE, NTE, 6, FM.FIRMS['Bulenox 50K Option 2']['ev'])
        pr(f"B. BULENOX 50K EVALUATION, 6 micros: one 30-day try passes {ok1 * 100:.0f}% (with resets {okr * 100:.0f}%); buying again after a failed "
           f"try: passed by 1 month {np.mean(p & (when < 21)) * 100:.0f}%, 2 months {np.mean(p & (when < 42)) * 100:.0f}%, 3 months {np.mean(p & (when < 63)) * 100:.0f}%, "
           f"6 months {np.mean(p & (when < 126)) * 100:.0f}%; fees until the pass median ${np.median(fees[p]):,.0f}, 90th pct ${pc(fees[p], 90):,.0f}")
        pay, ou, _ = MS.master_dyn(X, W, NT, S.P50, rule); cp_ = pay.cumsum(1)
        first = np.where(pay.sum(1) > 0, (pay > 0).argmax(1), 10 ** 6)
        pr("C. ONE FUNDED MASTER (cushion: 1 micro per $2,500, max 4):   payouts to you  mean   median  90th pct  first payout by then  still open")
        for lab, t in list(CP.items())[2:]:
            v = cp_[:, t - 1]; pr(f"   after {lab:<10}                                    ${v.mean():>6,.0f}  ${np.median(v):>6,.0f}  ${pc(v, 90):>7,.0f}      {np.mean(first < t) * 100:5.1f}%            {np.mean(ou >= t) * 100:5.1f}%")
        snap = ladder(X, W, NT)
        pr("D. LADDER TO 5 FUNDED ACCOUNTS ($1,000 budget, payouts buy the next evaluation):  net cash  mean   median  10th pct  90th pct  P(ahead)  funded accounts open (mean / P(>=3))")
        for lab, t in list(CP.items())[2:]:
            net, op, _ = snap[t]
            pr(f"   after {lab:<10}                                                       ${net.mean():>6,.0f}  ${np.median(net):>6,.0f}  ${pc(net, 10):>6,.0f}  ${pc(net, 90):>7,.0f}   "
               f"{np.mean(net > 0) * 100:5.1f}%     {op.mean():.2f} / {np.mean(op >= 3) * 100:.0f}%")
    with open(os.path.join(HERE, 'predict_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
