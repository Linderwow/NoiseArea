r"""MASTERSCALE (MASTERSCALE_PLAN.txt, pre-registered 2026-10-24): micros in the funded Bulenox 50K Master earned from the cushion.
    python masterscale.py   -> masterscale_output.txt
SIZES's model (sizes.py, biglever.master_qm's rules unchanged) on intraday_worst_extended.csv; before each session micros =
clip(floor(cushion / C), 1, M), cushion = balance - threshold. Fixed 1/2/3 micros must reproduce master_qm exactly (audit).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import biglever as B
import sizes as S

P50 = S.P50; SEED = B.SEED_REPORT
RULES = [(c, m) for c in (1500, 2000, 2500) for m in (2, 4)]
FIXED = (1, 2, 3)
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def master_dyn(X, W, NT, p, rule, dll_life=False):
    """biglever.master_qm with the day's micros chosen before the session from the cushion (balance - threshold); X, W are 1-micro
    paths. dll_life: the $1,100 daily loss limit stays after the threshold locks (sensitivity). Returns (payouts, open_until,
    micros per path-day: 0 once closed)."""
    n_p, n_d = X.shape; pay = np.zeros((n_p, n_d)); open_until = np.full(n_p, n_d); mic = np.zeros((n_p, n_d))
    bal = np.zeros(n_p); hi = np.zeros(n_p); thr = np.full(n_p, -float(p['dd'])); locked = np.zeros(n_p, bool); alive = np.ones(n_p, bool)
    n_pay = np.zeros(n_p, int); days = np.zeros(n_p, int); ptot = np.zeros(n_p); pbest = np.zeros(n_p); wait = np.zeros(n_p, int)
    for t in range(n_d):
        q = rule(bal - thr); mic[:, t] = np.where(alive, q, 0)
        x, w = B.day(X[:, t] * q, W[:, t] * q, np.where(locked & (not dll_life), np.inf, float(p['dll'])))
        x = np.where(alive, x, 0.0); w = np.where(alive, w, 0.0)
        br = alive & (bal + w < thr); open_until[br] = t; alive &= ~br; bal += x
        hi = np.where(alive, np.maximum(hi, bal), hi)
        up = alive & ~locked; thr = np.where(up, np.maximum(thr, hi - p['dd']), thr)
        lk = up & (thr >= 100); thr[lk] = 100.0; locked |= lk
        ww = alive & (wait > 0); wait[ww] -= 1; act = alive & ~ww
        days += (act & (NT[:, t] > 0)).astype(int); ptot = np.where(act, ptot + x, ptot); pbest = np.where(act, np.maximum(pbest, x), pbest)
        amt = np.minimum(np.where(n_pay < 3, float(p['cap']), 0.5 * bal - 1), bal - p['reserve'])
        el = act & (days >= 10) & (ptot > 0) & (pbest <= 0.4 * ptot) & (amt >= 1000)
        bal[el] -= amt[el]; pay[el, t] = 0.9 * amt[el]; n_pay[el] += 1; days[el] = 0; ptot[el] = 0.0; pbest[el] = 0.0; wait[el] = 3
    return pay, open_until, mic


def stats(pay, ou, mic):
    tot = pay.sum(1); fp = S.first_true(pay > 0); on = mic > 0
    return dict(pay=tot.mean(), any=np.mean(tot > 0), open1=np.mean(ou > 251), open2=np.mean(ou > 503),
                lost=np.mean((ou < fp) & (ou < B.N_FUNDED)), avgq=float(mic[on].mean()), atmax=float(np.mean(mic[on] == mic.max())))


def main():
    x0, w0, nt, idx = S.base_series('intraday_worst_extended.csv')
    pr(f"MASTERSCALE (MASTERSCALE_PLAN.txt): {len(x0)} sessions {idx[0].date()} .. {idx[-1].date()} (extended history), 1 micro at MNQ "
       f"{S.PRICE:,.0f}, $0.61 a side; Master 50K over {B.N_FUNDED} sessions, {B.N_PATHS:,} paths, seed {SEED}")
    res = {}
    for sc in ('A base', 'B 2 ticks', 'C half edge', 'D no edge'):
        x, w = S.scenario(sc, x0, w0, nt)
        X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, SEED)
        if sc == 'A base':                                            # audit: fixed sizes == biglever.master_qm on paths(m)
            for k in FIXED:
                a = master_dyn(X, W, NT, P50, lambda c, k=k: np.full(c.shape, float(k)))
                Xk, Wk, NTk = B.paths(B.N_FUNDED, x, w, nt, k, SEED); b = B.master_qm(Xk, Wk, NTk, P50)
                assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]), f"audit failed at fixed {k}"
            pr(f"audit: fixed 1/2/3 micros reproduce biglever.master_qm exactly (payouts and closing days on all paths)")
        fees6 = S.eval_one(x, w, nt, 6, True)['fees'] + P50['act']
        pr(f"\n=== {sc}: ${x.mean() * 252:,.0f}/yr a micro; evaluation at 6 micros with resets + activation ${fees6:,.0f} per funded account ===")
        pr(" policy                  avg micros  any payout  expected payouts  open after 1 yr  open after 2 yrs  lost before 1st  value/slot (eval 6)")
        pols = [(f"fixed {k}", lambda c, k=k: np.full(c.shape, float(k))) for k in FIXED]
        pols += [(f"1 per ${c:,} cushion, max {m}", lambda cu, c=c, m=m: np.clip(np.floor(cu / c), 1, m)) for c, m in RULES]
        for name, rule in pols:
            f = stats(*master_dyn(X, W, NT, P50, rule)); res[(sc, name)] = f
            pr(f" {name:<26} {f['avgq']:5.2f}      {f['any'] * 100:5.1f}%       ${f['pay']:>7,.0f}          {f['open1'] * 100:5.1f}%"
               f"          {f['open2'] * 100:5.1f}%          {f['lost'] * 100:5.1f}%         ${f['pay'] - fees6:>7,.0f}")
    pr("\nDECISION (plan): eligible = vs fixed 1 in BOTH A and C: more expected payouts, any-payout not lower by > 2.0 points, open after 1 "
       "year not lower by > 5.0 points; adopt the eligible rule with the most expected payouts in C")
    elig = []
    for c, m in RULES:
        name = f"1 per ${c:,} cushion, max {m}"; ok = True; why = []
        for sc in ('A base', 'C half edge'):
            r, f1 = res[(sc, name)], res[(sc, 'fixed 1')]
            d_pay, d_any, d_open = r['pay'] - f1['pay'], (r['any'] - f1['any']) * 100, (r['open1'] - f1['open1']) * 100
            why.append(f"{sc[0]}: payouts {d_pay:+,.0f}, any {d_any:+.1f}, open1 {d_open:+.1f}")
            ok &= d_pay > 0 and d_any >= -2.0 and d_open >= -5.0
        pr(f"   {name:<26} {'ELIGIBLE' if ok else 'no      '}  " + ' | '.join(why))
        if ok: elig.append((res[('C half edge', name)]['pay'], name))
    if elig:
        best = max(elig)[1]; pr(f"-> ADOPT: {best} (most expected payouts at half the edge among the eligible)")
    else: pr("-> none eligible: the Master stays at a fixed 1 micro")
    # SENSITIVITY (added after the decision, 2026-10-24; not part of it): the $1,100 daily loss limit kept for life in the Master
    pr("\nSENSITIVITY (not part of the decision): Bulenox's $1,100 daily loss limit kept after the threshold locks")
    pr(" scenario       policy                        any payout  expected payouts  open after 1 yr")
    for sc in ('A base', 'C half edge'):
        x, w = S.scenario(sc, x0, w0, nt); X, W, NT = B.paths(B.N_FUNDED, x, w, nt, 1, SEED)
        for name, rule in (('fixed 1', lambda c: np.full(c.shape, 1.0)), ('1 per $2,500 cushion, max 4', lambda cu: np.clip(np.floor(cu / 2500), 1, 4)),
                           ('1 per $2,500 cushion, max 2', lambda cu: np.clip(np.floor(cu / 2500), 1, 2))):
            f = stats(*master_dyn(X, W, NT, P50, rule, dll_life=True))
            pr(f" {sc:<14} {name:<28}  {f['any'] * 100:5.1f}%       ${f['pay']:>7,.0f}          {f['open1'] * 100:5.1f}%")
    with open(os.path.join(HERE, 'masterscale_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
