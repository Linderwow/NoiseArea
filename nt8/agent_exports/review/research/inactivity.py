r"""INACTIVITY (INACTIVITY_PLAN.txt, pre-registered 2026-10-30): Bulenox's inactivity rule (a trade at least every 5 trading days, else
suspension + balance forfeited) on the funded 50K Master with the adopted cushion rule, and NoiseArea's activity fallback (after K
sessions without a trade the next session runs with the noise-band multiplier lowered to V until a trade happens).
    python inactivity.py   -> inactivity_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import intraday_worst as IW
import biglever as B
import sizes as S

P50 = S.P50; SEED = B.SEED_REPORT
KS = (3, 4); VS = (0.5, 0.25, 0.0); DS = (5, 6)
RULE = lambda cu: np.clip(np.floor(cu / 2500.0), 1, 4)          # adopted 2026-10-24 (MASTERSCALE)
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def series_v(vm):
    """intraday_worst.build's frame for band multiplier vm: r_close, r_min, nt per session (vm = 1 must equal intraday_worst.csv)."""
    from research_engine import WARMUP
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * 1 + 0.25) / p_ref; parts = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQ', lambda d: d > N.JOIN)):
        mk = N.market(nm); p, w, n = IW.day_close_and_worst(mk, vm=vm); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c,
                                   'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c, 'nt': n[sel]}, index=d[sel]))
    s = pd.concat(parts); s.index.name = 'date'; return s


def dollars(s):
    """sizes.base_series on a frame: 1-micro $ close, $ worst point, trades."""
    x = s.r_close.values * S.NOTIONAL; w = s.r_min.values * S.NOTIONAL; nt = s.nt.values.astype(float); cost = nt * S.COMM_EXTRA_RT
    return x - cost, w - cost, nt


def walk(nt, ntf, K):
    """The historical sessions in order with the fallback: which sessions are fallback sessions, trades taken, dry runs left."""
    dry = 0; fb = np.zeros(len(nt), bool); took = np.zeros(len(nt))
    for i in range(len(nt)):
        fb[i] = K is not None and dry >= K
        took[i] = ntf[i] if fb[i] else nt[i]
        dry = 0 if took[i] > 0 else dry + 1
    return fb, took


def runs(took):
    r = []; cur = 0
    for t in took:
        if t > 0:
            if cur: r.append(cur)
            cur = 0
        else: cur += 1
    if cur: r.append(cur)
    return np.array(r) if r else np.zeros(1, int)


def master_inact(X, W, NT, XF, WF, NTF, p, rule, K=None, D=None):
    """masterscale.master_dyn (same order of events) plus: a session uses the fallback paths when the account's dry count at its start
    is >= K; the account closes (balance lost) when the dry count reaches D. K = D = None is master_dyn exactly (audited).
    Returns payouts, open_until, micros, closed-by-inactivity flags, fallback-session flags, n of the payout per payout-day."""
    n_p, n_d = X.shape; pay = np.zeros((n_p, n_d)); open_until = np.full(n_p, n_d); mic = np.zeros((n_p, n_d)); which = np.zeros((n_p, n_d), int)
    bal = np.zeros(n_p); hi = np.zeros(n_p); thr = np.full(n_p, -float(p['dd'])); locked = np.zeros(n_p, bool); alive = np.ones(n_p, bool)
    n_pay = np.zeros(n_p, int); days = np.zeros(n_p, int); ptot = np.zeros(n_p); pbest = np.zeros(n_p); wait = np.zeros(n_p, int)
    dry = np.zeros(n_p, int); inact = np.zeros(n_p, bool); fbd = np.zeros((n_p, n_d), bool); traded = np.zeros((n_p, n_d), bool)
    for t in range(n_d):
        fb = (dry >= K) if K is not None else np.zeros(n_p, bool)
        xt = np.where(fb, XF[:, t], X[:, t]); wt = np.where(fb, WF[:, t], W[:, t]); nt = np.where(fb, NTF[:, t], NT[:, t])
        q = rule(bal - thr); mic[:, t] = np.where(alive, q, 0)
        x, w = B.day(xt * q, wt * q, np.where(locked, np.inf, float(p['dll'])))
        x = np.where(alive, x, 0.0); w = np.where(alive, w, 0.0)
        fbd[:, t] = alive & fb; traded[:, t] = alive & (nt > 0)
        br = alive & (bal + w < thr); open_until[br] = t; alive &= ~br; bal += x
        hi = np.where(alive, np.maximum(hi, bal), hi)
        up = alive & ~locked; thr = np.where(up, np.maximum(thr, hi - p['dd']), thr)
        lk = up & (thr >= 100); thr[lk] = 100.0; locked |= lk
        ww = alive & (wait > 0); wait[ww] -= 1; act = alive & ~ww
        days += (act & (nt > 0)).astype(int); ptot = np.where(act, ptot + x, ptot); pbest = np.where(act, np.maximum(pbest, x), pbest)
        amt = np.minimum(np.where(n_pay < 3, float(p['cap']), 0.5 * bal - 1), bal - p['reserve'])
        el = act & (days >= 10) & (ptot > 0) & (pbest <= 0.4 * ptot) & (amt >= 1000)
        bal[el] -= amt[el]; pay[el, t] = 0.9 * amt[el]; n_pay[el] += 1; which[el, t] = n_pay[el]
        days[el] = 0; ptot[el] = 0.0; pbest[el] = 0.0; wait[el] = 3
        dry = np.where(alive, np.where(nt > 0, 0, dry + 1), dry)
        if D is not None:
            gone = alive & (dry >= D); open_until[gone] = t; inact |= gone; alive &= ~gone
    return pay, open_until, mic, inact, fbd, traded, which


def stats(pay, ou, mic, inact, fbd, traded, which):
    tot = pay.sum(1); first3 = (pay * ((which >= 1) & (which <= 3))).sum(1); on = mic > 0
    return dict(pay=tot.mean(), pay3=first3.mean(), any=np.mean(tot > 0), in1=np.mean(inact & (ou <= 251)), in2=np.mean(inact),
                open1=np.mean(ou > 251), avgq=float(mic[on].mean()), flip=(fbd & traded).sum() / max(traded.sum(), 1))


def main():
    pr(f"INACTIVITY (INACTIVITY_PLAN.txt): Bulenox's 5-trading-day inactivity rule and NoiseArea's activity fallback; Master 50K, "
       f"cushion rule (1 micro per $2,500, max 4), {B.N_FUNDED} sessions, {B.N_PATHS:,} paths, seed {SEED}")
    frames = {v: series_v(v) for v in (1.0,) + VS}
    ref = pd.read_csv(os.path.join(HERE, 'intraday_worst.csv'), index_col=0, parse_dates=True)
    d = (frames[1.0] - ref).abs().max().max()
    assert frames[1.0].index.equals(ref.index) and d < 1e-12, f"audit: V = 1 does not reproduce intraday_worst.csv ({d})"
    pr(f"audit: V = 1 reproduces intraday_worst.csv exactly ({len(ref)} sessions {ref.index[0].date()} .. {ref.index[-1].date()}, max diff {d:.1e})")
    idx = ref.index; yrs = (idx[-1] - idx[0]).days / 365.25
    x0, w0, nt0 = dollars(frames[1.0])

    pr("\n=== HISTORY (the 16-year series in order, 1 micro, today's cost) ===")
    r0 = runs(nt0)
    pr(f" no fallback: no-trade runs >= 5 sessions {np.sum(r0 >= 5)} ({np.sum(r0 >= 5) / yrs:.2f}/yr), >= 6 {np.sum(r0 >= 6)} "
       f"({np.sum(r0 >= 6) / yrs:.2f}/yr), longest {r0.max()}")
    pr(" K  V     fallback sessions/yr  of them trade  mean $/fallback session (t)  minus the base same day (t)  runs>=5 left  runs>=6 left  longest")
    hist = {}
    for K in KS:
        for V in VS:
            xf, wf, ntf = dollars(frames[V]); fb, took = walk(nt0, ntf, K); rr = runs(took)
            a = xf[fb]; dd = xf[fb] - x0[fb]
            ta = a.mean() / (a.std(ddof=1) / np.sqrt(len(a))); td = dd.mean() / (dd.std(ddof=1) / np.sqrt(len(dd)))
            hist[(K, V)] = dict(t=ta, n=fb.sum())
            pr(f" {K}  {V:<4}  {fb.sum() / yrs:6.2f}              {np.mean(ntf[fb] > 0) * 100:5.1f}%        ${a.mean():>7.2f} ({ta:+.2f})"
               f"                ${dd.mean():>7.2f} ({td:+.2f})            {np.sum(rr >= 5):3d}           {np.sum(rr >= 6):3d}        {rr.max()}")

    res = {}
    for sc in ('A base', 'C half edge'):
        mu = x0.mean(); cut = 0.0 if sc == 'A base' else 0.5 * mu
        X, W, NT = B.paths(B.N_FUNDED, x0 - cut, w0 - cut, nt0, 1, SEED)
        if sc == 'A base':                                            # audit: K = D = None == masterscale.master_dyn
            import masterscale as MS
            a = master_inact(X, W, NT, X, W, NT, P50, RULE); b = MS.master_dyn(X, W, NT, P50, RULE)
            assert np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1]) and np.array_equal(a[2], b[2]), "audit: master_inact != master_dyn"
            pr("\naudit: with no fallback and no inactivity rule the model reproduces masterscale.master_dyn exactly (payouts, closing days, micros)")
        pr(f"\n=== {sc}: ${(x0 - cut).mean() * 252:,.0f}/yr a micro ===")
        pr(" variant                          D  payouts 2 yrs  first 3 (Master)  any payout  closed by inactivity 1 yr / 2 yrs  open 1 yr  avg micros  fallback share of trade days")
        rows = [('old model (no inactivity rule)', None, None, None)]
        rows += [(f'no fallback', None, None, D) for D in DS]
        rows += [(f'fallback K={K} V={V}', K, V, D) for K in KS for V in VS for D in DS]
        for name, K, V, D in rows:
            if K is None: XF, WF, NTF = X, W, NT
            else:
                xf, wf, ntf = dollars(frames[V]); XF, WF, NTF = B.paths(B.N_FUNDED, xf - cut, wf - cut, ntf, 1, SEED)
            f = stats(*master_inact(X, W, NT, XF, WF, NTF, P50, RULE, K, D)); res[(sc, name, D)] = f
            pr(f" {name:<32} {D if D else '-'}  ${f['pay']:>7,.0f}       ${f['pay3']:>7,.0f}           {f['any'] * 100:5.1f}%     "
               f"{f['in1'] * 100:5.1f}% / {f['in2'] * 100:5.1f}%                {f['open1'] * 100:5.1f}%     {f['avgq']:4.2f}        {f['flip'] * 100:4.1f}%")

    pr("\nDECISION (plan): eligible = fallback sessions' mean $ t > -2.0 AND closed by inactivity within 2 years <= 2% at D = 5 (A and C);"
       " adopt the eligible variant with the most expected first-three payouts at half edge (C)")
    elig = []
    for K in KS:
        for V in VS:
            name = f'fallback K={K} V={V}'; h = hist[(K, V)]
            c5 = [res[(sc, name, 5)]['in2'] for sc in ('A base', 'C half edge')]
            ok = h['t'] > -2.0 and max(c5) <= 0.02
            pr(f"   {name:<22} {'ELIGIBLE' if ok else 'no      '}  t {h['t']:+.2f}, closed by inactivity (D=5) A {c5[0] * 100:.1f}% C {c5[1] * 100:.1f}%, "
               f"first-3 payouts C ${res[('C half edge', name, 5)]['pay3']:,.0f}")
            if ok: elig.append((res[('C half edge', name, 5)]['pay3'], name))
    if elig: pr(f"-> ADOPT: {max(elig)[1]} (most expected first-three payouts at half edge among the eligible)")
    else: pr("-> none eligible: no fallback; NoiseArea alone cannot keep a Master active")
    with open(os.path.join(HERE, 'inactivity_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
