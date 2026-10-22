r"""The big lever: Bulenox account type and micros in the evaluation / after funding (BIGLEVER_PLAN.txt, pre-registered 2026-10-21,
amendment 1: intraday stops).
    python biglever.py          -> biglever_output.txt   (intraday worst points - the primary model)
    python biglever.py close    -> biglever_output_closeonly.txt   (daily closes only - the optimistic bound, first run)
Routes: QM = Qualification -> Master (bulenox_routes.master's rules), FT = Fast Track, MO = Momentum (qualification + Momentum
Master). NoiseArea 1 micro x m (intraday_worst.csv: r_close == nav_lab.series == scaling_daily r_today; r_min = the session's worst
point). Option 2 (end-of-day drawdown) rules only. Every simulator takes X (day closes) and W (day worst points); W = X gives the
close-only model exactly.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import bulenox_routes as BR
import src2_lib as L

ACCTS = ('25K', '50K', '100K', '150K')
QS = (1, 2, 3, 4, 5, 6, 8, 10); MS = (1, 2, 3, 4)
SEED_PICK, SEED_REPORT = 20261021, 20261022
N_PATHS, N_FUNDED, N_WIN = 20000, 504, 21
RESET_FEE, RESET_MIN_LEFT = 78.0, 5
# Qualification -> Master: BR.SIZES (dd, dll, reserve, cap, act, qual, target)
FT = {'25K': dict(fee=338, dd=1000, dll=None, goal1=1500, goal2=1000, cap13=1000, cap4=1250),
      '50K': dict(fee=488, dd=2000, dll=1200, goal1=3000, goal2=2000, cap13=2000, cap4=2500),
      '100K': dict(fee=648, dd=4000, dll=2500, goal1=6000, goal2=3000, cap13=2500, cap4=3000),
      '150K': dict(fee=788, dd=5500, dll=3300, goal1=9000, goal2=4500, cap13=3000, cap4=3500)}
MO = {'25K': dict(fee=94, dd=1000, dll=600, target=1500, qday=100, minbal=1500, minreq=500, caps=(1000, 1000, 1000, 1000), strv=1000, goal2=1000),
      '50K': dict(fee=143, dd=2000, dll=1200, target=3000, qday=150, minbal=3000, minreq=1000, caps=(1500, 2000, 2500, 3000), strv=2000, goal2=2000),
      '100K': dict(fee=248, dd=3000, dll=2500, target=6000, qday=200, minbal=4500, minreq=1000, caps=(2000, 2500, 3000, 4000), strv=3000, goal2=3500),
      '150K': dict(fee=358, dd=4500, dll=3300, target=9000, qday=250, minbal=6500, minreq=1000, caps=(2500, 3000, 4000, 5000), strv=4500, goal2=5000)}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def series():
    s = pd.read_csv(os.path.join(HERE, 'intraday_worst.csv'), index_col=0, parse_dates=True)
    d = pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0, parse_dates=True)
    assert len(s) == len(d) and float((s.r_close - d.r_today).abs().max()) < 1e-12, "intraday_worst.csv no longer matches scaling_daily"
    return (s.r_close * L.N_USD).values, (s.r_min * L.N_USD).values, s.nt.values.astype(float)


def day(x, w, dll):
    """A day's close and worst point after the daily loss limit (None / np.inf = none): touching -DLL ends the day at -DLL."""
    if dll is None: return x, w
    hit = w <= -dll
    return np.where(hit, -dll, x), np.maximum(w, -dll)


def evaluation(X, W, dd, dll, target, resets=False):
    """One 21-session window per path; end-of-day threshold trailing the closing high, breached intraday (balance before the day +
    the day's worst point); DLL as above. With resets, a breach while >= RESET_MIN_LEFT sessions remain restarts at 0 for $78.
    Returns (passed, resets used). The target counts on closes."""
    n_p, n_d = X.shape; bal = np.zeros(n_p); hi = np.zeros(n_p); alive = np.ones(n_p, bool); ok = np.zeros(n_p, bool); nres = np.zeros(n_p)
    for t in range(n_d):
        x, w = day(X[:, t], W[:, t], dll)
        br = alive & (bal + w < hi - dd)
        bal = np.where(alive & ~br, bal + x, bal)
        if resets:
            rs = br & ((n_d - 1 - t) >= RESET_MIN_LEFT); nres += rs; bal[rs] = 0.0; hi[rs] = 0.0; br &= ~rs
        alive &= ~br
        hi = np.where(alive, np.maximum(hi, bal), hi); hit = alive & (bal >= target); ok |= hit; alive &= ~hit
    return ok, nres


def master_qm(X, W, NT, p):
    """bulenox_routes.master (Option 2) with intraday breaches: identical to it when W = X (checked in main)."""
    n_p, n_d = X.shape; pay = np.zeros((n_p, n_d)); open_until = np.full(n_p, n_d)
    bal = np.zeros(n_p); hi = np.zeros(n_p); thr = np.full(n_p, -float(p['dd'])); locked = np.zeros(n_p, bool); alive = np.ones(n_p, bool)
    n_pay = np.zeros(n_p, int); days = np.zeros(n_p, int); ptot = np.zeros(n_p); pbest = np.zeros(n_p); wait = np.zeros(n_p, int)
    for t in range(n_d):
        x, w = day(X[:, t], W[:, t], np.where(locked, np.inf, float(p['dll'])))
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
    return pay, open_until


def fast_track(X, W, NT, p):
    """Fast Track Option 2: end-of-day threshold trailing the closing high, locked for good at +$100, breached intraday; DLL for
    life (none on 25K); cycle goal (1st / later), consistency 20/25/30%, min $1,000, caps, payout may not reach the threshold;
    3 sessions' wait, the new cycle starting when it is over."""
    n_p, n_d = X.shape; pay = np.zeros((n_p, n_d)); open_until = np.full(n_p, n_d)
    bal = np.zeros(n_p); hi = np.zeros(n_p); thr = np.full(n_p, -float(p['dd'])); locked = np.zeros(n_p, bool); alive = np.ones(n_p, bool)
    n_pay = np.zeros(n_p, int); cs = np.zeros(n_p); best = np.zeros(n_p); wait = np.zeros(n_p, int)
    for t in range(n_d):
        x, w = day(X[:, t], W[:, t], p['dll'])
        x = np.where(alive, x, 0.0); w = np.where(alive, w, 0.0)
        br = alive & (bal + w < thr); open_until[br] = t; alive &= ~br; bal += x
        hi = np.where(alive, np.maximum(hi, bal), hi)
        up = alive & ~locked; thr = np.where(up, np.maximum(thr, hi - p['dd']), thr)
        lk = up & (thr >= 100); thr[lk] = 100.0; locked |= lk
        ww = alive & (wait > 0); wait[ww] -= 1
        st = ww & (wait == 0); cs[st] = bal[st]; best[st] = 0.0
        act = alive & ~ww; best = np.where(act, np.maximum(best, x), best)
        cp = bal - cs; goal = np.where(n_pay == 0, p['goal1'], p['goal2'])
        cons = np.where(n_pay == 0, 0.20, np.where(n_pay == 1, 0.25, 0.30))
        amt = np.minimum(np.where(n_pay < 3, float(p['cap13']), float(p['cap4'])), bal - thr - 0.01)
        el = act & (cp >= goal) & (best <= cons * cp) & (amt >= 1000)
        bal[el] -= amt[el]; pay[el, t] = 0.9 * amt[el]; n_pay[el] += 1; wait[el] = 3; best[el] = 0.0; cs[el] = bal[el]
    return pay, open_until


def momentum_master(X, W, NT, p, lock=True):
    """Momentum Master Option 2: end-of-day threshold (locks at +$100 unless lock=False), breached intraday, DLL for life; per cycle
    >= 5 qualifying days, best day <= 35% of the cycle's net profit, balance >= start + minbal, later cycles new profit >= goal2;
    amount <= cycle profit, <= cap, balance after >= start + STR, >= min request; 3 sessions' wait."""
    n_p, n_d = X.shape; pay = np.zeros((n_p, n_d)); open_until = np.full(n_p, n_d)
    bal = np.zeros(n_p); hi = np.zeros(n_p); thr = np.full(n_p, -float(p['dd'])); locked = np.zeros(n_p, bool); alive = np.ones(n_p, bool)
    n_pay = np.zeros(n_p, int); cs = np.zeros(n_p); best = np.zeros(n_p); qd = np.zeros(n_p, int); wait = np.zeros(n_p, int)
    caps = np.array(p['caps'], float)
    for t in range(n_d):
        x, w = day(X[:, t], W[:, t], p['dll'])
        x = np.where(alive, x, 0.0); w = np.where(alive, w, 0.0)
        br = alive & (bal + w < thr); open_until[br] = t; alive &= ~br; bal += x
        hi = np.where(alive, np.maximum(hi, bal), hi)
        up = alive & ~locked; thr = np.where(up, np.maximum(thr, hi - p['dd']), thr)
        if lock: lk = up & (thr >= 100); thr[lk] = 100.0; locked |= lk
        ww = alive & (wait > 0); wait[ww] -= 1
        st = ww & (wait == 0); cs[st] = bal[st]; best[st] = 0.0; qd[st] = 0
        act = alive & ~ww; best = np.where(act, np.maximum(best, x), best); qd += (act & (x >= p['qday'])).astype(int)
        cp = bal - cs
        amt = np.minimum.reduce([caps[np.minimum(n_pay, 3)], cp, bal - p['strv']])
        el = act & (qd >= 5) & (cp > 0) & (best <= 0.35 * cp) & (bal >= p['minbal']) & ((n_pay == 0) | (cp >= p['goal2'])) & (amt >= p['minreq'])
        bal[el] -= amt[el]; pay[el, t] = 0.9 * amt[el]; n_pay[el] += 1; wait[el] = 3; best[el] = 0.0; qd[el] = 0; cs[el] = bal[el]
    return pay, open_until


def paths(n_days, usd, usdm, nt, m, seed):
    """Closes and worst points on the SAME bootstrap blocks (boot_paths' draws depend only on the seed and the lengths)."""
    X, NT = BR.boot_paths(n_days, N_PATHS, usd * m, nt, seed=seed); W, _ = BR.boot_paths(n_days, N_PATHS, usdm * m, nt, seed=seed)
    return X, W, NT


def funded_stats(fn, usd, usdm, nt, p, m, seed, **kw):
    X, W, NT = paths(N_FUNDED, usd, usdm, nt, m, seed); pay, ou = fn(X, W, NT, p, **kw)
    return dict(pay=pay.sum(1).mean(), open1=np.mean(ou > 251), open2=np.mean(ou > 503), anyp=np.mean(pay.sum(1) > 0))


def eval_stats(usd, usdm, nt, q, seed, dd, dll, target, fee, resets=False):
    X, W, _ = paths(N_WIN, usd, usdm, nt, q, seed); ok, nres = evaluation(X, W, dd, dll, target, resets)
    pp = ok.mean(); cost = fee + RESET_FEE * nres.mean()
    return dict(p=pp, cost_try=cost, fees=cost / pp if pp > 0 else np.inf)


def route_tables(usd, usdm, nt, seed):
    E, F = {}, {}
    for a in ACCTS:
        s = BR.SIZES[a]
        for q in QS:
            E[('QM-R0', a, q)] = eval_stats(usd, usdm, nt, q, seed, s['dd'], s['dll'], s['target'], s['qual'])
            E[('QM-R1', a, q)] = eval_stats(usd, usdm, nt, q, seed, s['dd'], s['dll'], s['target'], s['qual'], resets=True)
            E[('MO', a, q)] = eval_stats(usd, usdm, nt, q, seed, MO[a]['dd'], MO[a]['dll'], MO[a]['target'], MO[a]['fee'])
        for m in MS:
            F[('QM', a, m)] = funded_stats(master_qm, usd, usdm, nt, s, m, seed)
            F[('FT', a, m)] = funded_stats(fast_track, usd, usdm, nt, FT[a], m, seed)
            F[('MO', a, m)] = funded_stats(momentum_master, usd, usdm, nt, MO[a], m, seed)
    return E, F


def value(E, F, route, a, q, m):
    """Expected 2-year payouts after funding minus every fee to get there (per account slot)."""
    if route == 'FT': return F[('FT', a, m)]['pay'] - FT[a]['fee']
    if route == 'MO': return F[('MO', a, m)]['pay'] - E[('MO', a, q)]['fees']
    return F[('QM', a, m)]['pay'] - E[(route, a, q)]['fees'] - BR.SIZES[a]['act']


def main(close_only=False):
    usd, usdm, nt = series()
    if close_only: usdm = usd
    pr(f"BIGLEVER (BIGLEVER_PLAN.txt{', first run: DAILY CLOSES ONLY - optimistic' if close_only else ', amendment 1: intraday worst points'}). "
       f"NoiseArea 1 micro: {len(usd)} sessions, ${usd.mean():+.2f}/session, worst point median ${np.median(usdm):,.0f}; funded {N_PATHS:,} paths "
       f"x {N_FUNDED} sessions, evaluation {N_PATHS:,} windows x {N_WIN} sessions; sizes picked on seed {SEED_PICK}, reported on seed {SEED_REPORT}.")
    X, NT = BR.boot_paths(504, 20000, usd, nt)
    for a in ('25K', '50K'):
        p0, _ = BR.master(X, NT, BR.SIZES[a]); p1, _ = master_qm(X, X, NT, BR.SIZES[a])
        ok, _ = evaluation(X[:, :21], X[:, :21], BR.SIZES[a]['dd'], BR.SIZES[a]['dll'], BR.SIZES[a]['target'])
        pr(f"check {a} (seed 20261020): master_qm with W = X vs bulenox_routes.master max |payout diff| {np.abs(p1 - p0).max():.2e}; "
           f"evaluation vs bulenox_routes.qualify {ok.mean() * 100:.2f}% vs {BR.qualify(X, BR.SIZES[a]) * 100:.2f}%; "
           f"2-yr net of activation ${p0.sum(1).mean() - BR.SIZES[a]['act']:,.0f} (Part B: 25K $2,271, 50K $3,038)")
    EA, FA = route_tables(usd, usdm, nt, SEED_PICK); EB, FB = route_tables(usd, usdm, nt, SEED_REPORT)
    base = value(EB, FB, 'QM-R0', '25K', 1, 1)
    pr(f"\nPLAN OF RECORD (Qualification 25K at 1 micro, no resets -> Master at 1 micro), reporting seed: value ${base:,.0f} per account slot "
       f"(P(pass per 30-day try) {EB[('QM-R0', '25K', 1)]['p'] * 100:.1f}%, fees per funded account ${EB[('QM-R0', '25K', 1)]['fees']:,.0f} + activation $143, "
       f"2-year payouts ${FB[('QM', '25K', 1)]['pay']:,.0f})")
    pr("\nEVALUATION (reporting seed): P(pass in one 30-day try) / expected fees per funded account, by micros in the evaluation")
    for r in ('QM-R0', 'QM-R1', 'MO'):
        for a in ACCTS:
            pr(f"   {r:5s} {a:4s} " + ' | '.join(f"{q}: {EB[(r, a, q)]['p'] * 100:4.1f}% ${EB[(r, a, q)]['fees']:,.0f}" for q in QS))
    pr("\nFUNDED PHASE (reporting seed): expected 2-year payouts to you / open after 1 year / any payout in 2 years, by micros")
    for r in ('QM', 'FT', 'MO'):
        for a in ACCTS:
            pr(f"   {r:2s} {a:4s} " + ' | '.join(f"{m}: ${FB[(r, a, m)]['pay']:,.0f} / {FB[(r, a, m)]['open1'] * 100:.0f}% / {FB[(r, a, m)]['anyp'] * 100:.0f}%" for m in MS))
    pr("\nCOMBINED VALUE PER ACCOUNT SLOT (2-year payouts - all fees), sizes picked on the pick seed, value on the reporting seed")
    rows = []
    for r in ('QM-R0', 'QM-R1', 'FT', 'MO'):
        for a in ACCTS:
            qs = (1,) if r == 'FT' else QS
            q, m = max(((q, m) for q in qs for m in MS), key=lambda qm: value(EA, FA, r, a, *qm))
            v = value(EB, FB, r, a, q, m)
            nb = []
            for dq, dm in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                if r == 'FT' and dq: continue
                iq = qs.index(q) + dq; im = MS.index(m) + dm
                if 0 <= iq < len(qs) and 0 <= im < len(MS): nb.append(value(EB, FB, r, a, qs[iq], MS[im]))
            plateau = all(x >= 0.8 * v for x in nb) if v > 0 else False
            first = FT[a]['fee'] if r == 'FT' else EB[(r, a, q)]['cost_try']
            pfirst = 1.0 if r == 'FT' else EB[(r, a, q)]['p']
            f = FB[('FT' if r == 'FT' else ('MO' if r == 'MO' else 'QM'), a, m)]
            v1 = max(value(EB, FB, r, a, qq, 1) for qq in qs)
            better = v >= 1.5 * base and plateau
            rows.append((v, r, a, q, m, better))
            pr(f"   {r:5s} {a:4s} eval {q if r != 'FT' else '-'} micros, funded {m}: value ${v:,.0f} (best with 1 funded micro ${v1:,.0f}) | neighbours "
               f"{', '.join(f'${x:,.0f}' for x in nb)} -> plateau {'yes' if plateau else 'no'} | first try ${first:,.0f}, funded after it "
               f"{pfirst * 100:.0f}% | funded open 1y {f['open1'] * 100:.0f}%, any payout {f['anyp'] * 100:.0f}% | {'BETTER' if better else 'not better'} "
               f"(plan of record ${base:,.0f})")
    rows.sort(reverse=True)
    pr("\nRANKING (value per slot, reporting seed; up to 5 Master-level slots, identical trades so they fail together):")
    for v, r, a, q, m, better in rows[:8]:
        pr(f"   {r:5s} {a:4s} eval {q if r != 'FT' else '-'} / funded {m}: ${v:,.0f} per slot, x5 = ${5 * v:,.0f}{'  BETTER' if better else ''}")
    pr("\nSENSITIVITY - Momentum Master WITHOUT the +$100 lock (assumption check), reporting seed, 2-year payouts by funded micros:")
    for a in ACCTS:
        pr(f"   MO {a:4s} " + ' | '.join(f"{m}: ${funded_stats(momentum_master, usd, usdm, nt, MO[a], m, SEED_REPORT, lock=False)['pay']:,.0f}" for m in MS))
    name = 'biglever_output_closeonly.txt' if close_only else 'biglever_output.txt'
    with open(os.path.join(HERE, name), 'w', encoding='utf-8') as fh: fh.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main(close_only=sys.argv[1:2] == ['close'])
