r"""CHVAL (CHVAL_PLAN.txt, pre-registered 2026-10-24): full validation and improvement breakdown of the chandelier exit (CH).
    python chval.py   -> chval_output.txt
Engine: strat3.run's live rules + CH (close mode: exit at the next open after a 5-minute close 3 x ATR beyond the best close since
entry; stop mode: a resting stop at that level), plus random early exits (control C2) and per-trade / per-day logs. Audited first.
"""
import os, sys, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import history_extend as HX                      # registers nav_lab.FILES['MNQX']
import navar as NV
import metalabel as ML
import strat3 as S3
from research_engine import WARMUP

LOOK = 14; CHECKS = set(range(600, 931, 30)); M_CH = 3.0; USD = ML.USD; N_TRIALS = 15; P_SIG = 0.05 / N_TRIALS
FRESH = {'MNQDB': dict(a='2019-05-06', b='2026-12-31', cost=None), 'ES': dict(a='2010-07-01', b='2019-05-05', cost=0.25 + 2.50 / 50),
         'MES': dict(a='2019-05-06', b='2026-12-31', cost=0.25 + 0.50 / 5), 'MYM': dict(a='2019-05-06', b='2026-12-31', cost=1.0 + 0.50 / 0.5),
         'M2K': dict(a='2019-05-06', b='2026-12-31', cost=0.1 + 0.50 / 5)}
TICK = {'MNQDB': 0.25, 'ES': 0.25, 'MES': 0.25, 'MYM': 1.0, 'M2K': 0.1}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
def save(): open(os.path.join(HERE, 'chval_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


def run(mk, ch=None, atr=None, mode='close', hazard=None, seed=0, worst_out=None, log=None):
    """Live rules + CH / random exits. Returns (daily points, trades fam_V-style, days with a CH or random exit (bool array)).
    log (dict) receives 'trades' [(k, dir, entry, exit, reason, points)] and per-day 'first_exit_min', 'reentries'."""
    sig = mk.sigma(LOOK); pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n); hit = np.zeros(mk.n, bool)
    rng = np.random.default_rng(seed) if hazard is not None else None
    if log is not None: log.update(trades=[], first_exit_min={}, reentries={})
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, H, L, C, M, vw, nb = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb
        A = atr[k] if atr is not None else x.A
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        st = dict(d=0, ref=0.0, p=0.0, vol=0, worst=0.0, e1=0.0, i_in=0, ext=0.0, after=False, re=0)

        def close_trade(px, reason, i):
            d = st['d']; st['p'] += d * (px - st['ref']); st['vol'] += abs(d)
            if log is not None: log['trades'].append((k, d, st['e1'], px, reason, d * (px - st['e1'])))
            if reason in ('ch', 'rand'):
                hit[k] = True
                if log is not None and k not in log['first_exit_min']: log['first_exit_min'][k] = int(M[i])
                st['after'] = True
            st['d'] = 0; st['ref'] = px

        def mark(i):
            d = st['d']
            if d: st['worst'] = min(st['worst'], st['p'] + d * ((L[i] if d > 0 else H[i]) - st['ref']))

        for i in range(flat_i):
            c = C[i]; mark(i)
            if st['d'] != 0 and ch is not None and mode == 'stop' and i >= st['i_in']:
                d = st['d']; lvl = st['ext'] - ch * A[i - 1] if d > 0 else st['ext'] + ch * A[i - 1]
                if (d > 0 and L[i] <= lvl) or (d < 0 and H[i] >= lvl):
                    close_trade(min(O[i], lvl) if d > 0 else max(O[i], lvl), 'ch', i)
            d = st['d']
            if d != 0:
                st['ext'] = max(st['ext'], c) if d > 0 else min(st['ext'], c)
                if ch is not None and mode == 'close' and ((d > 0 and c < st['ext'] - ch * A[i]) or (d < 0 and c > st['ext'] + ch * A[i])):
                    close_trade(O[i + 1], 'ch', i); continue
                if hazard is not None and i >= st['i_in'] and rng.random() < hazard:
                    close_trade(O[i + 1], 'rand', i); continue
            if M[i] not in CHECKS or np.isnan(sg[i]): continue
            ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i]); d = st['d']
            if d == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif d == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if new != d:
                px = O[i + 1]
                if d != 0: close_trade(px, 'live', i)
                if new != 0:
                    st['vol'] += 1; st['d'] = new; st['ref'] = px; st['e1'] = px; st['ext'] = px; st['i_in'] = i + 1
                    if st['after']: st['re'] += 1
        if st['d'] != 0: mark(flat_i); close_trade(C[flat_i], 'close', flat_i)
        pnl[k] = st['p']; nt[k] = st['vol'] / 2
        if worst_out is not None: worst_out[k] = min(st['worst'], st['p'])
        if log is not None and st['re']: log['reentries'][k] = st['re']
    return pnl, nt, hit


def atr_days(mk, n):
    """ATR(n) on 5-minute bars, Wilder as sim.load (ATR 14), per kept session aligned with mk.days."""
    from sim import load
    df = load(mk.path); h, l, c = df.high.values, df.low.values, df.close.values
    tr = np.empty(len(df)); tr[0] = h[0] - l[0]; tr[1:] = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])])
    a = np.empty(len(df)); a[0] = tr[0]
    for i in range(1, len(df)): a[i] = ((n - 1) * a[i - 1] + tr[i]) / n if i >= n else tr[:i + 1].mean()
    df['a'] = a; df['date'] = df.time.dt.date; r = df[df.in_rth.values]
    by = {d: g.a.values for d, g in r.groupby('date', sort=True)}
    res = [by[x.date] for x in mk.days]
    assert all(len(v) == x.nb for v, x in zip(res, mk.days)), "ATR alignment"
    return res


def series_mnq(res, ticks=1):
    return ML.joined({nm: (res[nm][0], res[nm][1], res[nm][2]) for nm, _ in ML.PARTS}, ticks)


def series_other(mk, pnl, nt, spec, ticks=1):
    d = mk.dates; sel = (np.arange(len(d)) >= WARMUP) & (d >= pd.Timestamp(spec['a'])) & (d <= pd.Timestamp(spec['b']))
    if spec['cost'] is None: return ML.to_series(mk, pnl, nt, lambda x: (x >= pd.Timestamp(spec['a'])) & (x <= pd.Timestamp(spec['b'])), ticks)
    o = np.array([x.O[0] for x in mk.days]); tick = TICK[mk.name]; cost = spec['cost'] + (ticks - 1) * tick
    return pd.Series((np.nan_to_num(pnl[sel]) - nt[sel] * 2 * cost) / o[sel], index=d[sel])


def mdd(v):
    eq = np.cumsum(v); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())


def boot_sharpe(rv, rb, n=10000, seed=20261024):
    idx = rv.index; key = idx.year * 12 + idx.month; blocks = [np.where(key == k)[0] for k in np.unique(key)]
    rng = np.random.default_rng(seed); a, b = rv.values, rb.values; worse = 0
    for _ in range(n):
        ix = np.concatenate([blocks[j] for j in rng.integers(0, len(blocks), len(blocks))])
        worse += a[ix].mean() / a[ix].std() <= b[ix].mean() / b[ix].std()
    return worse / n


def compare(label, rv, rb, usd=USD, boot=True):
    dlt = (rv - rb) * usd; t = dlt.mean() / dlt.std() * np.sqrt(len(dlt)) if dlt.std() > 0 else 0.0
    mv, mb = NV.mar(rv), NV.mar(rb)
    s = (f" {label:<34} MAR {mv:6.3f} vs {mb:6.3f} ({mv - mb:+.3f}) | Sharpe {NV.sharpe(rv):5.2f} vs {NV.sharpe(rb):5.2f} | $/yr {rv.mean() * 252 * usd:>7,.0f} vs "
         f"{rb.mean() * 252 * usd:>7,.0f} (diff t {t:+.2f}) | max DD ${mdd(rv.values * usd):>6,.0f} vs ${mdd(rb.values * usd):>6,.0f}")
    if boot: s += f" | P(MAR) {NV.boot_mar(rv, rb):.3f} P(Sharpe) {boot_sharpe(rv, rb):.3f}"
    pr(s); return mv - mb, t


def drawdowns(v, idx, top=5):
    eq = np.cumsum(v); peak = np.maximum.accumulate(np.r_[0.0, eq])[1:]; dd = peak - eq; res = []; i = 0
    while i < len(v):
        if dd[i] > 0:
            j = i
            while j < len(v) and dd[j] > 0: j += 1
            t = i + int(np.argmax(dd[i:j])); res.append((dd[t], idx[i - 1].date() if i else idx[0].date(), idx[t].date(), idx[j - 1].date() if j < len(v) else None))
            i = j
        else: i += 1
    return sorted(res, reverse=True)[:top]


def main():
    mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    pr(f"CHVAL (CHVAL_PLAN.txt): CH = exit at the next open after a 5-minute close {M_CH:g} x ATR(14) beyond the best close since entry; "
       f"NQ {mks['NQ'].dates[0].date()} .. {N.JOIN.date()} + MNQ to {mks['MNQX'].dates[-1].date()} (extended); trials in the CH family {N_TRIALS}, "
       f"significance bar P < {P_SIG:.4f}")
    # ---- audits / silent-bug checks (C1)
    base, chr_, logs, hits = {}, {}, {}, {}
    for nm, _ in ML.PARTS:
        mk = mks[nm]; p0, n0 = N.fam_V(mk); p1, n1, h1 = run(mk)
        assert np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and np.array_equal(n0, n1) and not h1.any(), f"audit live {nm}"
        p2, n2, _ = S3.run(mk, ch=M_CH); lg = {}; p3, n3, h3 = run(mk, ch=M_CH, log=lg)
        assert np.array_equal(np.nan_to_num(p2, nan=-9e9), np.nan_to_num(p3, nan=-9e9)) and np.array_equal(n2, n3), f"audit CH vs strat3 {nm}"
        p4, n4, h4 = run(mk, ch=1000.0)
        assert np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p4, nan=-9e9)) and not h4.any(), f"C1 unreachable multiplier {nm}"
        same = ~h3 & ~np.isnan(p0)
        assert np.array_equal(p3[same], p0[same]) and np.array_equal(n3[same], n0[same]), f"C1 days without a CH exit differ {nm}"
        base[nm] = (mk, p1, n1); chr_[nm] = (mk, p3, n3); logs[nm] = lg; hits[nm] = h3
    pr("audits passed: engine == nav_lab.fam_V (live rules) and == strat3.run (CH) exactly; C1: 1,000 x ATR == live rules; every session "
       "without a CH exit has exactly the live rules' P&L and trades")
    rb, rc = series_mnq(base), series_mnq(chr_)
    assert rb.index.equals(rc.index)
    hit_s = pd.concat([pd.Series(hits[nm][(np.arange(mks[nm].n) >= WARMUP) & keep(mks[nm].dates)], index=mks[nm].dates[(np.arange(mks[nm].n) >= WARMUP) & keep(mks[nm].dates)])
                       for nm, keep in ML.PARTS]).reindex(rb.index).fillna(False).astype(bool)
    pr(f"sessions {len(rb):,}; sessions with a CH exit {int(hit_s.sum()):,} ({hit_s.mean() * 100:.1f}%); CH exits {sum(sum(1 for t in logs[nm]['trades'] if t[4] == 'ch') for nm in logs):,}; "
       f"trades live {ML.trades(base):,.0f} vs CH {ML.trades(chr_):,.0f}")
    ex_y = pd.Series(1, index=rb.index[hit_s]).groupby(rb.index[hit_s].year).sum()
    pr("   sessions with a CH exit per year: " + ', '.join(f"{y} {n}" for y, n in ex_y.items()))
    assert (ex_y.reindex(range(2011, 2026)).fillna(0) >= 5).all(), "sample count: a full year with fewer than 5 CH sessions"
    save()

    pr("\n1. WINDOWS (NinjaTrader bars; NOT a holdout - STRAT3 saw the whole period)")
    W = {'NQ 2010-07 .. 2019-05': (rb.index <= N.JOIN), 'MNQ discovery 2019-05 .. 2022': (rb.index > N.JOIN) & (rb.index.year <= 2022),
         'MNQ validation 2023 .. 2026-10': rb.index.year >= 2023, 'whole period': np.ones(len(rb), bool)}
    res_w = {lab: compare(lab, rc[m], rb[m]) for lab, m in W.items()}
    k1 = res_w['MNQ discovery 2019-05 .. 2022'][0] >= 0 and res_w['MNQ discovery 2019-05 .. 2022'][1] >= -1.0
    pr(f"   K1 discovery MNQ 2019-2022: MAR change {res_w['MNQ discovery 2019-05 .. 2022'][0]:+.3f}, diff t {res_w['MNQ discovery 2019-05 .. 2022'][1]:+.2f} -> {'pass' if k1 else 'KILL'}")
    save()

    pr("\n2. PER YEAR ($ per micro at MNQ 29,150)")
    pr("   year   live P&L   CH P&L    diff    live max DD   CH max DD   live MAR   CH MAR   CH sessions")
    worse = cnt = 0
    for y in sorted(set(rb.index.year)):
        a, b = rb[rb.index.year == y] * USD, rc[rc.index.year == y] * USD; da, db = mdd(a.values), mdd(b.values)
        if da >= 500: cnt += 1; worse += db > da + 1e-9
        pr(f"   {y}   {a.sum():>8,.0f}  {b.sum():>8,.0f}  {b.sum() - a.sum():>+7,.0f}    {da:>8,.0f}     {db:>8,.0f}    {a.sum() / da if da else float('nan'):6.2f}   {b.sum() / db if db else float('nan'):6.2f}     {int(hit_s[rb.index.year == y].sum())}")
    k2 = worse <= cnt / 3
    pr(f"   K2 years with a drawdown >= $500: {cnt}; CH deeper in {worse} -> {'pass' if k2 else 'KILL'}")
    save()

    pr("\n3. COSTS (whole period): ticks a side 0 / 1 / 2 / 3")
    k3 = True
    for tk in (0, 1, 2, 3):
        b_, c_ = series_mnq(base, tk), series_mnq(chr_, tk)
        pr(f"   {tk} tick(s): live MAR {NV.mar(b_):.3f} ${b_.mean() * 252 * USD:,.0f}/yr | CH MAR {NV.mar(c_):.3f} ${c_.mean() * 252 * USD:,.0f}/yr")
        if tk == 2: k3 = NV.mar(c_) >= NV.mar(b_)
    pr(f"   K3 at 2 ticks -> {'pass' if k3 else 'KILL'}")
    save()

    pr("\n4. UNSEEN DATA - second source and second instruments (the real out-of-sample)")
    signs = []
    for nm, spec in FRESH.items():
        try:
            mk = N.market(nm)
        except Exception as e:
            pr(f"   {nm}: not available ({e})"); continue
        p0, n0, _ = run(mk); p1, n1, _ = run(mk, ch=M_CH)
        sb, sc = series_other(mk, p0, n0, spec), series_other(mk, p1, n1, spec)
        dm, _ = compare(f"{nm} {sb.index[0].date()} .. {sb.index[-1].date()}", sc, sb, usd=1.0, boot=True); signs.append(dm > 0)
        sb2, sc2 = series_other(mk, p0, n0, spec, 2), series_other(mk, p1, n1, spec, 2)
        pr(f"      at 2 ticks: MAR {NV.mar(sc2):.3f} vs {NV.mar(sb2):.3f}")
    k4 = sum(not s for s in signs) < 2
    pr(f"   K4 MAR change positive on {sum(signs)} of {len(signs)} unseen series -> {'pass' if k4 else 'KILL'}")
    save()

    pr("\n5. CONTROL C2 - random early exits, same number as CH, 50 seeds")
    n_ch = sum(sum(1 for t in logs[nm]['trades'] if t[4] == 'ch') for nm in logs)
    tot_bars = sum(sum(max(0, x.nb - 1) for x in mks[nm].days) for nm, _ in ML.PARTS)
    def count_rand(hz, seed=0):
        res = {}; n = 0
        for nm, _ in ML.PARTS:
            lg = {}; p, nt_, _ = run(mks[nm], hazard=hz, seed=seed, log=lg); res[nm] = (mks[nm], p, nt_); n += sum(1 for t in lg['trades'] if t[4] == 'rand')
        return res, n
    hz = n_ch / (0.35 * tot_bars)
    for _ in range(6):                                            # calibrate the hazard to CH's number of early exits
        _, n = count_rand(hz); hz *= n_ch / max(n, 1)
    gains = []
    for sd in range(50):
        res, n = count_rand(hz, sd + 1); gains.append(NV.mar(series_mnq(res)) - NV.mar(rb))
    g_ch = NV.mar(rc) - NV.mar(rb); q95 = float(np.quantile(gains, 0.95))
    pr(f"   CH exits {n_ch:,}; random-exit hazard {hz:.5f} per bar (exits per seed ~{n:,}); MAR change of random exits: median {np.median(gains):+.3f}, "
       f"95th pct {q95:+.3f}, max {max(gains):+.3f}; CH {g_ch:+.3f} (beats {np.mean(np.array(gains) < g_ch) * 100:.0f}% of seeds)")
    k5 = g_ch > q95
    pr(f"   K5 (C2) -> {'pass' if k5 else 'KILL'}")
    save()

    pr("\n6. SELECTION - the whole family (multiple x ATR length), no chosen point")
    atrs = {n: {nm: atr_days(mks[nm], n) for nm, _ in ML.PARTS} for n in (7, 21)}
    fam = []
    pr("   multiple:      " + ''.join(f"{m:>8.1f}" for m in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0)))
    for n_atr in (7, 14, 21):
        row = []
        for m in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
            res = {nm: (mks[nm],) + run(mks[nm], ch=m, atr=(atrs[n_atr][nm] if n_atr != 14 else None))[:2] for nm, _ in ML.PARTS}
            dm = NV.mar(series_mnq(res)) - NV.mar(rb); row.append(dm); fam.append(dm)
        pr(f"   ATR({n_atr:2d}) MAR chg " + ''.join(f"{v:>+8.3f}" for v in row))
    k6 = np.mean(fam) > 0
    pr(f"   K6 family average MAR change {np.mean(fam):+.3f} (positive in {sum(v > 0 for v in fam)} of {len(fam)}) -> {'pass' if k6 else 'KILL'}")
    save()

    pr("\n7. EXECUTION - a resting stop order at the chandelier level")
    st_res = {nm: (mks[nm],) + run(mks[nm], ch=M_CH, mode='stop')[:2] for nm, _ in ML.PARTS}
    dm, _ = compare("stop order vs live rules", series_mnq(st_res), rb)
    k7 = dm > 0
    pr(f"   K7 -> {'pass' if k7 else 'KILL'}")
    save()

    pr("\n8. DROP-5")
    rc5 = rc.drop(rc.nlargest(5).index); k8 = NV.mar(rc5) > NV.mar(rb)
    pr(f"   CH without its 5 best days MAR {NV.mar(rc5):.3f} vs live {NV.mar(rb):.3f} -> {'pass' if k8 else 'KILL'}")
    save()

    # ---- breakdown
    pr("\n9. BREAKDOWN (whole period, $ per micro)")
    D = (rc - rb) * USD; dh = D[hit_s]
    pr(f"   sessions with a CH exit {int(hit_s.sum()):,}: total change {D.sum():+,.0f} ({D.sum() / (len(D) / 252):+,.0f}/yr); saved giveback on {int((dh > 0).sum())} "
       f"(+{dh[dh > 0].sum():,.0f}), lost upside / extra cost on {int((dh < 0).sum())} ({dh[dh < 0].sum():,.0f}), unchanged {int((dh == 0).sum())}; "
       f"sessions without a CH exit: change exactly {D[~hit_s].abs().sum():.0f}")
    dirs = {}; times = {}; rex = 0
    for nm, keep in ML.PARTS:
        mk = mks[nm]
        for (k, d, e, x_, r, pts) in logs[nm]['trades']:
            if r != 'ch' or not ((k >= WARMUP) and keep(pd.DatetimeIndex([mk.dates[k]]))[0]): continue
            day = mk.dates[k]; dirs.setdefault('long' if d > 0 else 'short', []).append(day)
        for k, m in logs[nm]['first_exit_min'].items():
            if k >= WARMUP and keep(pd.DatetimeIndex([mk.dates[k]]))[0]: times.setdefault(f"{m // 60:02d}:00", []).append(mk.dates[k])
        rex += sum(v for k, v in logs[nm]['reentries'].items() if k >= WARMUP)
    for lab, days in dirs.items():
        ds = sorted(set(days)); pr(f"   CH exits of {lab} trades: {len(days):,} on {len(ds):,} sessions, change on those sessions {D.reindex(ds).sum():+,.0f}")
    pr("   first CH exit by hour: " + '; '.join(f"{h} {len(v)} sessions {D.reindex(sorted(set(v))).sum():+,.0f}" for h, v in sorted(times.items())))
    pr(f"   new entries later on a CH-exit day (re-entries): {rex:,}")
    big = []
    for nm, keep in ML.PARTS:
        mk = mks[nm]; sg = mk.sigma(LOOK)
        with np.errstate(all='ignore'): sgd = np.array([np.nanmax(r) if np.isfinite(r).any() else np.nan for r in sg])
        mv = np.array([abs(x.C[-1] / x.O[0] - 1) for x in mk.days]) > sgd; sel = (np.arange(mk.n) >= WARMUP) & keep(mk.dates)
        big.append(pd.Series(mv[sel], index=mk.dates[sel]))
    big = pd.concat(big).reindex(rb.index).fillna(False).astype(bool)
    pr(f"   big-move sessions (> 1 daily sigma) {int(big.sum()):,}: live {rb[big].sum() * USD:+,.0f}, CH {rc[big].sum() * USD:+,.0f} ({D[big].sum():+,.0f}); "
       f"other sessions: live {rb[~big].sum() * USD:+,.0f}, CH {rc[~big].sum() * USD:+,.0f} ({D[~big].sum():+,.0f})")
    import lit2026 as LT
    vix = LT.cboe('vix_history_cboe_latest.csv'); vp = pd.Series(LT.prior(vix, rb.index), index=rb.index)
    terc = pd.qcut(vp, 3, labels=['low VIX', 'mid VIX', 'high VIX'])
    pr("   by VIX tercile (prior close): " + '; '.join(f"{t}: live {rb[terc == t].sum() * USD:+,.0f} CH {rc[terc == t].sum() * USD:+,.0f} "
                                                      f"(MAR {NV.mar(rc[terc == t]):.2f} vs {NV.mar(rb[terc == t]):.2f})" for t in ['low VIX', 'mid VIX', 'high VIX']))
    cv = lambda s: float(np.sort(s.values * USD)[:max(1, int(len(s) * 0.05))].mean())
    pr(f"   tail: worst day live {rb.min() * USD:,.0f} vs CH {rc.min() * USD:,.0f}; 5% CVaR live {cv(rb):,.0f} vs CH {cv(rc):,.0f}")
    wl, wc = rb.nsmallest(10), rc.nsmallest(10)
    pr("   10 worst days live: " + ', '.join(f"{d:%Y-%m-%d} {v * USD:,.0f} (CH {rc[d] * USD:,.0f})" for d, v in wl.items()))
    pr("   10 worst days CH:   " + ', '.join(f"{d:%Y-%m-%d} {v * USD:,.0f} (live {rb[d] * USD:,.0f})" for d, v in wc.items()))
    for lab, s in (('live', rb), ('CH', rc)):
        pr(f"   5 deepest drawdowns {lab}: " + '; '.join(f"${a:,.0f} {b} -> {c} (recovered {e})" for a, b, c, e in drawdowns(s.values * USD, s.index)))
    save()

    pr("\n10. BULENOX (6-micro evaluation with resets, cushion-sized Master, 5-account fleet), full edge and half edge (both shifted by half the live mean)")
    import strat3_prop as SP, sizes as S, biglever as B, masterscale as MS, fleet as FL
    s0, x0, w0, n0 = SP.series({}); s1, x1, w1, n1 = SP.series(dict(ch=M_CH))
    shift = 0.5 * x0.mean()
    for lab, sh in (('full edge', 0.0), ('half edge', shift)):
        for nm_, (x, w, nt_) in (('live rules', (x0, w0, n0)), ('CH', (x1, w1, n1))):
            x_, w_ = x - sh, w - sh; e = S.eval_one(x_, w_, nt_, 6, True)
            X, Wp, NTp = B.paths(B.N_FUNDED, x_, w_, nt_, 1, B.SEED_REPORT)
            pay, ou, _ = MS.master_dyn(X, Wp, NTp, S.P50, lambda cu: np.clip(np.floor(cu / 2500), 1, 4)); tot = pay.sum(1)
            f = FL.fleet(X, Wp, NTp, 5)
            pr(f"   {lab:<9} {nm_:<10}: eval pass {e['p'] * 100:5.1f}% fees/funded ${e['fees']:,.0f} | Master payouts ${tot.mean():,.0f} paid {np.mean(tot > 0) * 100:.1f}% "
               f"open 1 yr {np.mean(ou > 251) * 100:.1f}% | value/slot ${tot.mean() - e['fees'] - S.P50['act']:,.0f} | fleet of 5 net ${f['net'].mean():,.0f} "
               f"(P>0 {np.mean(f['net'] > 0) * 100:.0f}%)")
    save()

    kills = {'K1': k1, 'K2': k2, 'K3': k3, 'K4': k4, 'K5': k5, 'K6': k6, 'K7': k7, 'K8': k8}
    p_full = NV.boot_mar(rc, rb)
    pr(f"\nVERDICT: kills {', '.join(k for k, v in kills.items() if not v) or 'none'}; full-period bootstrap P(MAR) {p_full:.3f} vs bar {P_SIG:.4f} -> "
       + ('DEAD' if not all(kills.values()) else ('VALIDATED' if p_full < P_SIG else 'SURVIVES THE KILLS, NOT SIGNIFICANT: stays in the paper forward test')))
    save()


if __name__ == '__main__':
    main()
