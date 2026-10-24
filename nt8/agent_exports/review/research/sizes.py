r"""SIZES (SIZES_PLAN.txt, 2026-10-24): Bulenox 50K evaluation and funded Master at 1..8 micros, base + three stress scenarios.
    python sizes.py                -> sizes_output.txt
    python sizes.py --extended     -> sizes_output_extended.txt: the same on intraday_worst_extended.csv (the frozen history plus
                                      every session recorded since, rebuilt nightly by history_extend.py; 2026-10-24)
Uses biglever's simulators unchanged (evaluation, master_qm, paths) on intraday_worst.csv, re-priced at today's MNQ level and
Bulenox's $0.61 commission.
"""
import os, sys, argparse
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import biglever as B
import bulenox_routes as BR

PRICE = 29150.0; NOTIONAL = PRICE * 2.0
COMM_EXTRA_RT = 2 * (0.61 - 0.50)          # $ per round trip per micro over the series' $0.50 a side
TICK_SIDE = 0.25 * 2.0                      # one extra tick a side = $0.50 per side per micro
SIZES = range(1, 9); P50 = BR.SIZES['50K']; SEED = B.SEED_REPORT
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def base_series(name='intraday_worst.csv'):
    s = pd.read_csv(os.path.join(HERE, name), index_col=0, parse_dates=True)
    x = s.r_close.values * NOTIONAL; w = s.r_min.values * NOTIONAL; nt = s.nt.values.astype(float)
    cost = nt * COMM_EXTRA_RT
    return x - cost, w - cost, nt, s.index


def scenario(name, x, w, nt):
    if name == 'B 2 ticks':
        c = nt * 2 * TICK_SIDE; return x - c, w - c
    mu = x.mean()
    if name == 'C half edge': return x - 0.5 * mu, w - 0.5 * mu
    if name == 'D no edge': return x - mu, w - mu
    return x, w


def first_true(a):
    """Index of the first True per row (n_cols when none)."""
    has = a.any(1); idx = a.argmax(1); return np.where(has, idx, a.shape[1])


def eval_one(x, w, nt, q, resets):
    X, W, _ = B.paths(B.N_WIN, x, w, nt, q, SEED)
    ok, nres = B.evaluation(X, W, P50['dd'], P50['dll'], P50['target'], resets)
    # sessions to pass (re-simulated close-by-close for the median; only for passed paths)
    bal = np.cumsum(X, 1); passday = first_true(bal >= P50['target']) + 1
    cost = P50['qual'] + B.RESET_FEE * nres.mean()
    dll_touch = float(np.mean(W <= -P50['dll']))
    return dict(p=ok.mean(), fees=cost / ok.mean() if ok.mean() > 0 else np.inf, med=np.median(passday[ok]) if ok.any() else np.nan,
                dll=dll_touch, breach=None)


def eval_breach(x, w, nt, q):
    """One try without resets: share breached and share timed out (neither target nor breach in 21 sessions)."""
    X, W, _ = B.paths(B.N_WIN, x, w, nt, q, SEED); ok, _ = B.evaluation(X, W, P50['dd'], P50['dll'], P50['target'], False)
    # breach = not passed and the account died; replay the threshold check exactly as B.evaluation does
    n_p, n_d = X.shape; bal = np.zeros(n_p); hi = np.zeros(n_p); alive = np.ones(n_p, bool); dead = np.zeros(n_p, bool); done = np.zeros(n_p, bool)
    for t in range(n_d):
        xx, ww = B.day(X[:, t], W[:, t], P50['dll'])
        br = alive & ~done & (bal + ww < hi - P50['dd']); dead |= br; alive &= ~br
        bal = np.where(alive & ~done, bal + xx, bal); hi = np.where(alive, np.maximum(hi, bal), hi)
        done |= alive & (bal >= P50['target'])
    assert np.array_equal(done & ~dead, ok), "breach replay disagrees with biglever.evaluation"
    return float(np.mean(dead)), float(np.mean(~dead & ~ok))


def master_one(x, w, nt, m):
    X, W, NT = B.paths(B.N_FUNDED, x, w, nt, m, SEED)
    pay, ou = B.master_qm(X, W, NT, P50); tot = pay.sum(1)
    fp = first_true(pay > 0)
    return dict(any=np.mean(tot > 0), pay=tot.mean(), open1=np.mean(ou > 251), open2=np.mean(ou > 503),
                med_first=np.median(fp[tot > 0]) + 1 if (tot > 0).any() else np.nan, p_lost_before_pay=np.mean((ou < fp) & (ou < B.N_FUNDED)))


def main(extended=False):
    x0, w0, nt, idx = base_series('intraday_worst_extended.csv' if extended else 'intraday_worst.csv')
    pr(f"SIZES (SIZES_PLAN.txt){' on the EXTENDED history (history_extend.py)' if extended else ''}: {len(x0)} sessions {idx[0].date()} .. {idx[-1].date()}, 1 micro at MNQ {PRICE:,.0f} (${NOTIONAL:,.0f} notional): "
       f"${x0.mean() * 252:,.0f}/yr, worst session ${x0.min():,.0f}, sessions with an intraday dip below -$1,100 at 1 micro {np.mean(w0 <= -1100) * 100:.2f}%")
    res = {}
    for sc in ('A base', 'B 2 ticks', 'C half edge', 'D no edge'):
        x, w = scenario(sc, x0, w0, nt)
        pr(f"\n=== {sc}: ${x.mean() * 252:,.0f}/yr a micro ===")
        pr("EVALUATION 50K (one 30-day / 21-session try; R1 = reset for $78 after a breach with >= 5 sessions left)")
        pr(" micros  pass 1 try  breached  neither   pass w/ resets  median sessions to pass  fees per funded acct  sessions touching DLL")
        for q in SIZES:
            e0 = eval_one(x, w, nt, q, False); e1 = eval_one(x, w, nt, q, True); br, nei = eval_breach(x, w, nt, q)
            res[(sc, 'E', q)] = (e0, e1, br, nei)
            pr(f"   {q}      {e0['p'] * 100:5.1f}%    {br * 100:5.1f}%   {nei * 100:5.1f}%       {e1['p'] * 100:5.1f}%              {e1['med']:4.0f}"
               f"                ${e1['fees']:>7,.0f}             {e0['dll'] * 100:5.1f}%")
        pr("FUNDED MASTER 50K over 2 years (504 sessions), trader's 90% share")
        pr(" micros  any payout  expected payouts  open after 1 yr  open after 2 yrs  lost before 1st payout  median sessions to 1st payout")
        for m in SIZES:
            f = master_one(x, w, nt, m); res[(sc, 'M', m)] = f
            pr(f"   {m}       {f['any'] * 100:5.1f}%       ${f['pay']:>7,.0f}          {f['open1'] * 100:5.1f}%          {f['open2'] * 100:5.1f}%"
               f"             {f['p_lost_before_pay'] * 100:5.1f}%                   {f['med_first']:4.0f}")
    pr("\nVALUE PER ACCOUNT SLOT (base scenario A, then D no edge): 2-year Master payouts - fees (evaluation with resets + $148 activation)")
    for sc in ('A base', 'C half edge', 'D no edge'):
        best = []
        for q in SIZES:
            fees = res[(sc, 'E', q)][1]['fees'] + P50['act']
            for m in SIZES:
                best.append((res[(sc, 'M', m)]['pay'] - fees, q, m))
        best.sort(reverse=True)
        pr(f"   {sc}: best " + '; '.join(f"eval {q} -> Master {m}: ${v:,.0f}" for v, q, m in best[:4]))
        for q, m in ((6, 1), (6, 2), (8, 1), (1, 1)):
            v = res[(sc, 'M', m)]['pay'] - res[(sc, 'E', q)][1]['fees'] - P50['act']
            pr(f"      eval {q} -> Master {m}: ${v:,.0f}")
    with open(os.path.join(HERE, 'sizes_output_extended.txt' if extended else 'sizes_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--extended', action='store_true')
    main(ap.parse_args().extended)
