r"""PRICES20 (2026-10-24): the account plan re-run at the user's own Bulenox prices - evaluation $20, activation $170 (their statement).
    python prices20.py   -> prices20_output.txt
A projection, no gate. At $20 a new evaluation beats a $78 reset (and restarts the 30-day clock), so there are no resets: a breach or
an expired window -> a new evaluation the next session.
Part 1: evaluation micros 1..8 - pass rate, fees and value per funded slot (cushion-sized Master, 2 years); the best size (base) is
used in part 2. Part 2: 1..5 funded accounts at 6 / 12 / 24 months, fees paid when due, all accounts on the same sessions:
  (S) sequential - one evaluation at a time;
  (P) parallel - when slots are free and no evaluation runs, one evaluation per free slot starts the same day; they trade identically,
      so they pass or fail together and their Masters are clones.
Inputs and engines as predict_multi.py (firms.evaluation audited vs biglever.evaluation; fleet.Master vs masterscale.master_dyn).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import biglever as B, sizes as S, masterscale as MS, fleet as FL, firms as FM
import nav_lab as N

SEED = B.SEED_REPORT; EVAL, ACT = 20.0, 170.0; CP = {'6 months': 126, '12 months': 252, '24 months': 504}
EV = dict(price=EVAL, reset=None, window=21, reset_min_left=5, target=3000.0, dd=2500.0, lock=None, dll=1100.0, cons=None, min_days=0)
rule = lambda cu: np.clip(np.floor(cu / 2500), 1, 4)
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
pc = lambda v, q: float(np.percentile(v, q))


def multi(X, W, NT, cap, q, parallel):
    FL.Q_EVAL = q
    n = X.shape[0]; ms = [FL.Master(n) for _ in range(cap)]; ev = FL.Evaluation(n); pend = np.zeros(n, int); k_att = np.zeros(n, int)
    fees = np.zeros(n); pays = np.zeros(n); cash_min = np.zeros(n); acts = np.zeros(n); am = np.zeros(n); snap = {}
    for t in range(B.N_FUNDED):
        for m in ms:
            go = (pend > 0) & ~m.alive
            if go.any(): m.start(go); fees[go] += ACT; pend[go] -= 1; acts += go
        n_open = sum(m.alive.astype(int) for m in ms)
        new = ~ev.active & (pend == 0) & (n_open < cap)
        if new.any():
            k_att[new] = (cap - n_open[new]) if parallel else 1
            ev.start(new); fees[new] += EVAL * k_att[new]
        for m in ms: pays += m.step(X[:, t], W[:, t], NT[:, t])[0]
        hit, _ = ev.step(X[:, t], W[:, t], False); pend[hit] = k_att[hit]
        open_now = sum(m.alive.astype(int) for m in ms); am += open_now; cash_min = np.minimum(cash_min, pays - fees)
        if t + 1 in CP.values(): snap[t + 1] = dict(pay=pays.copy(), fees=fees.copy(), net=pays - fees, oop=-cash_min.copy(), open=open_now.copy(), act=acts.copy())
    FL.Q_EVAL = 6
    return snap


def main():
    s = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    x_all, w_all, nt_all, _ = S.base_series('intraday_worst_extended.csv'); mnq = s.index > N.JOIN; sh = 0.5 * x_all.mean()
    scen = {'16-year history': (x_all, w_all, nt_all), 'MNQ era only (2019-26)': (x_all[mnq], w_all[mnq], nt_all[mnq]),
            'half the edge': (x_all - sh, w_all - sh, nt_all)}
    pr(f"PRICES20: evaluation ${EVAL:.0f}, activation ${ACT:.0f} (the user's prices), no resets; {len(s)} sessions to {s.index[-1].date()}; "
       f"MNQ 29,150, $0.61 a side; {B.N_PATHS:,} paths, seed {SEED}")
    best_q = None
    for sc, (x, w, nt) in scen.items():
        XE, WE, NTE = B.paths(252, x, w, nt, 1, SEED); X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, SEED)
        pay = MS.master_dyn(X, W, NT, S.P50, rule)[0].sum(1).mean()
        pr(f"\n=================== {sc}: ${x.mean() * 252:,.0f}/yr a micro; funded Master (cushion) 2-yr payouts ${pay:,.0f} ===================")
        pr("PART 1 - evaluation micros: one 30-day try passes | passed within 1 / 3 months | evaluations bought per funded account | fees per funded "
           "| value per funded slot (payouts - fees - $170)")
        vals = {}
        for q in range(1, 9):
            ok1 = S.eval_one(x, w, nt, q, False)['p']; p, when, fees = FM.evaluation(XE, WE, NTE, q, EV)
            per = fees.mean() / max(p.mean(), 1e-9); vals[q] = pay - per - ACT
            pr(f"   {q} micros: {ok1 * 100:5.1f}% | {np.mean(p & (when < 21)) * 100:5.1f}% / {np.mean(p & (when < 63)) * 100:5.1f}% | {per / EVAL:5.1f} | ${per:>6,.0f} | ${vals[q]:>6,.0f}")
        if best_q is None: best_q = max(vals, key=vals.get); pr(f"   -> evaluation size used below (best value per slot, 16-year history): {best_q} micros")
        pr(f"PART 2 - 1..5 funded accounts, evaluations at {best_q} micros")
        pr(" policy      accts  when        payouts mean    fees    NET mean   median    10th      90th   P(ahead)  out of pocket med/90th  open now  activated")
        for parallel in (False, True):
            for cap in (1, 2, 3, 4, 5):
                if parallel and cap == 1: continue
                sn = multi(X, W, NT, cap, best_q, parallel)
                for lab, t in CP.items():
                    d = sn[t]
                    pr(f" {'parallel' if parallel else 'sequential':<10}  {cap}     {lab:<10}  ${d['pay'].mean():>8,.0f}  ${d['fees'].mean():>5,.0f}  ${d['net'].mean():>8,.0f}  "
                       f"${np.median(d['net']):>7,.0f}  ${pc(d['net'], 10):>7,.0f}  ${pc(d['net'], 90):>8,.0f}   {np.mean(d['net'] > 0) * 100:5.1f}%     "
                       f"${np.median(d['oop']):>5,.0f} / ${pc(d['oop'], 90):>5,.0f}      {d['open'].mean():4.2f}      {d['act'].mean():5.2f}")
        with open(os.path.join(HERE, 'prices20_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
