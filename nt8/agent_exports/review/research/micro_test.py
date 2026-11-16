import os, sys, glob, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist

SRC = os.path.join(HERE, 'regime_work', 'micro')
OUT = os.path.join(HERE, 'EDGEHUNT_RESULTS.txt')
SPLIT = pd.Timestamp('2026-07-01')
COMM = 0.61
BAR = NormalDist().inv_cdf(1 - 0.05 / 6)
DEC = np.arange(300, 22801, 10)
LINES = []


def say(s=''):
    print(s, flush=True); LINES.append(s)


def tt(x):
    x = np.asarray(x, float); s = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / (s / np.sqrt(len(x)))) if s > 0 else 0.0


def load():
    fs = sorted(glob.glob(os.path.join(SRC, '*.npz'))); dates = [pd.Timestamp(os.path.basename(f)[:10]) for f in fs]
    A = {k: [] for k in ('mid', 'bid', 'ask', 'cs')}
    for f in fs:
        z = np.load(f); A['mid'].append(z['mid'].astype(np.float64)); A['bid'].append(z['bid'].astype(np.float64)); A['ask'].append(z['ask'].astype(np.float64))
        A['cs'].append(np.cumsum(z['bv'].astype(np.float64) - z['sv'].astype(np.float64)))
    return pd.DatetimeIndex(dates), {k: np.array(v) for k, v in A.items()}


def sigs(A):
    m, cs = A['mid'], A['cs']; d = DEC
    f = lambda a, w: a[:, d] - a[:, d - w]
    return {'sv10': f(cs, 10), 'sv30': f(cs, 30), 'sv60': f(cs, 60), 'sv120': f(cs, 120), 'sv300': f(cs, 300),
            'r10': f(m, 10), 'r60': f(m, 60), 'r300': f(m, 300)}


def simulate(A, dirs, hold, extra=0.0):
    bid, ask, mid = A['bid'], A['ask'], A['mid']; nd = dirs.shape[0]; per_day = np.zeros(nd); n = np.zeros(nd, int); gross = []; paid = []
    for k in range(nd):
        busy = -1
        for j in np.nonzero(dirs[k])[0]:
            s = DEC[j]
            if s <= busy: continue
            e, x = s + 1, s + 1 + hold
            if x >= 23400: continue
            b0, a0, b1, a1 = bid[k, e], ask[k, e], bid[k, x], ask[k, x]
            if not (np.isfinite(b0) and np.isfinite(a0) and np.isfinite(b1) and np.isfinite(a1)) or a0 - b0 > 5 or a1 - b1 > 5: continue
            dr = dirs[k, j]
            pts = (b1 - a0) if dr > 0 else (b0 - a1)
            pts -= COMM + 2 * extra
            per_day[k] += pts * 2.0; n[k] += 1; busy = x
            gross.append(dr * (mid[k, x] - mid[k, e])); paid.append(dr * (mid[k, x] - mid[k, e]) - pts)
    return per_day, n, np.array(gross), np.array(paid)


def ols_forecast(S, A, disc_mask):
    names = ['sv10', 'sv30', 'sv60', 'sv120', 'sv300', 'r10', 'r60', 'r300']
    X = np.stack([S[n_] for n_ in names], -1); nd, nj, p = X.shape
    X = np.concatenate([np.ones((nd, nj, 1)), X], -1)
    y = A['mid'][:, np.minimum(DEC + 60, 23399)] - A['mid'][:, DEC]
    XtX = np.einsum('dji,djk->dik', X, X); Xty = np.einsum('dji,dj->di', X, y)
    fc = np.full((nd, nj), np.nan)
    for k in range(20, nd):
        b = np.linalg.lstsq(XtX[k - 20:k].sum(0) + 1e-6 * np.eye(p + 1), Xty[k - 20:k].sum(0), rcond=None)[0]
        fc[k] = X[k] @ b
    return fc


def grade(name, desc, dates, A, dirs, hold):
    pd_, n, g, paid = simulate(A, dirs, hold); pd2, _, _, _ = simulate(A, dirs, hold, extra=0.25)
    s = pd.Series(pd_, index=dates); s2 = pd.Series(pd2, index=dates); disc, hold_ = s[s.index < SPLIT], s[s.index >= SPLIT]
    mo = s.groupby(s.index.to_period('M')).sum(); d5 = s.drop(s.sort_values(ascending=False).index[:5])
    gt = {'G1': tt(disc) >= BAR, 'G2': hold_.mean() > 0 and tt(hold_) >= 2.0, 'G3': (mo > 0).mean() >= 0.6, 'G4': s2.sum() > 0, 'G5': tt(d5) >= 2.0}
    say(f"{name} {desc}")
    say(f"   {int(n.sum()):,} trades ({n.mean():.0f}/day) | gross mid-to-mid {g.mean():+.3f} pt/trade (t {tt(g):+.2f}) | cost paid {paid.mean():.3f} pt/trade "
        f"(spread + slippage {paid.mean() - COMM:.3f} + commission {COMM}) | net ${s.sum() / len(s):+.1f}/day per micro")
    say(f"   discovery ${disc.mean():+.1f}/day t {tt(disc):+.2f} | holdout ${hold_.mean():+.1f}/day t {tt(hold_):+.2f} | months + {(mo > 0).mean():.0%} | "
        f"+1 tick/side ${s2.sum() / len(s2):+.1f}/day | drop5 t {tt(d5):+.2f}")
    fails = [k for k, v in gt.items() if not v]
    say(f"   VERDICT {name}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
    return not fails


def audit(dates, A):
    k = 100; s = DEC[500]; hold = 60
    dirs = np.zeros((len(dates), len(DEC)), int); dirs[k, 500] = 1
    pd_, n, g, paid = simulate(A, dirs, hold)
    hand = (A['bid'][k, s + 1 + hold] - A['ask'][k, s + 1] - COMM) * 2
    ok = n.sum() == 1 and abs(pd_[k] - hand) < 1e-9 and abs(g[0] - (A['mid'][k, s + 1 + hold] - A['mid'][k, s + 1])) < 1e-9
    dirs[k, 501] = -1; pd_, n, _, _ = simulate(A, dirs, hold)
    ok2 = n.sum() == 1
    say(f"audit: one long on {dates[k]:%Y-%m-%d} at {9 + (34200 + s) // 3600 - 9:d}h - ask in at s+1, bid out at s+61, $0.61 a side "
        f"= ${hand:+.2f} by hand -> engine ${pd_[k] if n.sum() else float('nan'):+.2f}; a signal while in the trade is skipped: {ok2} -> {'PASS' if ok and ok2 else 'FAIL'}")
    return ok and ok2


def main():
    t0 = time.time()
    dates, A = load(); disc = dates < SPLIT
    say(f"micro_test.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (EDGEHUNT_PLAN.txt part M)  {len(dates)} sessions {dates[0]:%Y-%m-%d}..{dates[-1]:%Y-%m-%d}, "
        f"discovery {int(disc.sum())} / holdout {int((~disc).sum())}; decisions every 10 s; G1 bar t >= {BAR:.2f}")
    if not audit(dates, A): say("audit FAILED - nothing graded"); return
    S = sigs(A)
    q = {k: (np.nanquantile(S[k][disc], 0.05), np.nanquantile(S[k][disc], 0.95)) for k in ('sv60', 'sv300', 'r60')}
    say("thresholds (discovery 5% / 95%): " + "; ".join(f"{k} {a:+.1f} / {b:+.1f}" for k, (a, b) in q.items()))
    say("=" * 120)
    surv = []
    rules = [('M1', 'flow continuation, 60 s signed volume, hold 60 s', np.where(S['sv60'] > q['sv60'][1], 1, np.where(S['sv60'] < q['sv60'][0], -1, 0)), 60),
             ('M2', 'flow continuation, 5 min signed volume, hold 5 min', np.where(S['sv300'] > q['sv300'][1], 1, np.where(S['sv300'] < q['sv300'][0], -1, 0)), 300),
             ('M3', 'short-term reversal of the 60 s move, hold 120 s', np.where(S['r60'] > q['r60'][1], -1, np.where(S['r60'] < q['r60'][0], 1, 0)), 120)]
    fc = ols_forecast(S, A, disc)
    cost = (A['ask'][:, DEC] - A['bid'][:, DEC]) + COMM
    with np.errstate(invalid='ignore'):
        m4 = np.where(fc > cost, 1, np.where(fc < -cost, -1, 0))
    rules.append(('M4', 'rolling 20-session OLS forecast of the next 60 s, trade when |forecast| > spread + commission, hold 60 s', m4, 60))
    for name, desc, dirs, hold in rules:
        if grade(name, desc, dates, A, np.nan_to_num(dirs).astype(int), hold): surv.append(name)
    say(); say("PART M SURVIVORS: " + (", ".join(surv) if surv else "none")); say(f"run {time.time() - t0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
