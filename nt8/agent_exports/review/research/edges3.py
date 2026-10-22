r"""Free batch (EDGES3_PLAN.txt, pre-registered 2026-10-20): A1/A2 big-tech earnings days, B1-B4 cross-market lead-lag, C1 VIX term
structure - intraday only.
    python edges3.py      -> edges3_output.txt, edges3_<id>_daily.csv
Calendars in research\event_calendars\; 1-minute caches regime_work\{nq,mnqx,es,mes,zn,m6e,mgc,mcl}_1m.npz.
"""
import os, sys, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L
from bulenox_routes import Pan, tstat, to_series, era, maxdd
from event_days import close_ret

CAL = os.path.join(HERE, 'event_calendars'); MIN = np.timedelta64(1, 'm')
PRIMARY = ('AAPL', 'MSFT', 'AMZN', 'GOOGL')
CHECKS = [(h, m) for h in range(10, 16) for m in (0, 30)]            # 10:00 .. 15:30
B_DISC, B_HOLD = ('2019-05-06', '2022-12-31'), L.HOLD
out = []
def pr(*a): print(*a); out.append(' '.join(str(x) for x in a))


def judge(name, net, sides, ev, ctl_ev, g3_ok, na, mkt, extra3, disc, eras, lines):
    import regime_test as rt
    import src2_test as T
    usd = L.N_USD; t_full = tstat(net); t_disc = tstat(era(net, *disc)); m_hold = era(net, *L.HOLD).mean()
    best5 = net.sort_values().index[-5:]; t_d5 = tstat(net.drop(best5)); t_3tk = tstat(net - extra3)
    em = {k: era(net, a, b).mean() for k, (a, b) in eras.items()}
    ta, beta = T.alpha_t(net, mkt.reindex(net.index).fillna(0.0))
    welch = (ev.mean() - ctl_ev.mean()) / np.sqrt(ev.var(ddof=1) / len(ev) + ctl_ev.var(ddof=1) / len(ctl_ev))
    common = na.index.intersection(net.index); common = common[common >= net.index[0]]
    a, b = na.reindex(common).values, net.reindex(common).values
    p_add = rt.boot_sharpe_diff(common, a + b, a); dd_na = maxdd(a * usd); dd_c = maxdd((a + b) * usd)
    G = {'G1 t >= 2.70': t_full >= 2.7, 'G2 disc t >= 2.0 & holdout > 0': t_disc >= 2.0 and m_hold > 0, 'G3 second instrument': g3_ok,
         'G4 drop-5 t >= 2.0': t_d5 >= 2.0, 'G5 3-tick t >= 2.0': t_3tk >= 2.0, 'G6 eras > 0': all(v > 0 for v in em.values()),
         'G7 alpha t >= 2.0': ta >= 2.0, 'G7 Welch t >= 2.0': welch >= 2.0, 'G8 ADDITION P < 0.0071': p_add < 0.0071,
         'G9 maxDD <= 1.10x': dd_c <= 1.10 * dd_na}
    yr = net.groupby(net.index.year).sum(); ny = (sides > 0).groupby(sides.index.year).sum()
    pr(f"\n{name}: {len(ev)} events | net {ev.mean() * 1e4:+.2f} bp (${ev.mean() * usd:+,.0f}/micro) per event, event t {tstat(ev):+.2f}, "
       f"win {np.mean(ev > 0) * 100:.0f}% | ${net.sum() * usd / (len(net) / 252):+,.0f}/yr per micro over {net.index[0].date()} .. {net.index[-1].date()}")
    pr(f"   G1 t {t_full:+.2f} | G2 disc t {t_disc:+.2f}, holdout {m_hold * 1e4:+.3f} bp/day | G4 drop-5 t {t_d5:+.2f} | G5 3-tick t {t_3tk:+.2f} | "
       f"G6 " + ' '.join(f"{k} {v * 1e4:+.3f}" for k, v in em.items()) + " bp/day")
    pr(f"   G7 alpha t {ta:+.2f} (beta {beta:+.3f}); control n {len(ctl_ev)}, {ctl_ev.mean() * 1e4:+.2f} bp, t {tstat(ctl_ev):+.2f}; Welch t {welch:+.2f} | "
       f"G8 NoiseArea + it Sharpe {rt.sharpe(a + b):.3f} vs {rt.sharpe(a):.3f} ({len(common)} sessions), P {p_add:.4f} | "
       f"G9 maxDD ${dd_c:,.0f} vs ${dd_na:,.0f} ({dd_c / dd_na:.2f}x)")
    for ln in lines: pr('   ' + ln)
    pr("   per year ($/micro, event days): " + ' | '.join(f"{y} {v * usd:+,.0f}/{int(ny.get(y, 0))}" for y, v in yr.items()))
    ok = all(G.values()); pr("   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()))
    pr(f"   VERDICT {name.split()[0]}: {'PASSES - all gates' if ok else 'NOT ADOPTED'}")
    return ok


def extra_micro(name, na, nt, mask, mkt, lines_extra):
    """A second NoiseArea micro on the masked days: gates as E5 (EVENTDAYS)."""
    c1, c3 = L.side_cost(1), L.side_cost(3); traded = nt.reindex(na.index).fillna(0) > 0
    net = na * mask; s = nt.reindex(na.index).fillna(0) * 2 * mask
    ev = na[mask & traded].values; ctl = na[~mask & traded].values
    ex = lambda a_, b_: (na[mask & (na.index >= pd.Timestamp(a_)) & (na.index <= pd.Timestamp(b_))].mean()
                         - na[~mask & (na.index >= pd.Timestamp(a_)) & (na.index <= pd.Timestamp(b_))].mean())
    x1, x2 = ex(L.DISC[0], '2019-05-05'), ex('2019-05-06', '2026-10-16')
    lines = [f"G3 NoiseArea excess per day on these sessions: NQ 2010-2019 {x1 * 1e4:+.2f} bp, MNQ 2019-2026 {x2 * 1e4:+.2f} bp",
             f"(events = masked sessions on which NoiseArea traded: {len(ev)} of {int(mask.sum())})"] + lines_extra
    eras = {'disc': L.DISC, 'val': L.VAL, 'hold': L.HOLD}
    return judge(name, net, s, ev, ctl, x1 > 0 and x2 > 0, na, mkt, s * (c3 - c1), L.DISC, eras, lines), net, s


def calendar(P, na):
    return pd.DatetimeIndex(sorted(set(na.index) | set(P.dates)))


def reaction_dates(cal, filings):
    """First session strictly after each filing date (all PRIMARY companies release after the 16:00 close)."""
    out = set()
    for f in filings:
        i = cal.searchsorted(pd.Timestamp(f), side='right')
        if i < len(cal): out.add(cal[i])
    return out


def gap(P, k):
    D = P.daily
    if k == 0 or D.seg.iat[k - 1] != D.seg.iat[k]: return np.nan
    return (D.aO.iat[k] - D.aC.iat[k - 1]) / D.px.iat[k]


# ------------------------------------------------------------------------------------------------ B: lead-lag grid
def nq_grid(P, ks):
    """Y[k, c]: NQ return from the open of the checkpoint bar to the open 30 minutes later (the 16:00 close for 15:30); NaN if missing."""
    Y = np.full((len(ks), len(CHECKS)), np.nan)
    for a, k in enumerate(ks):
        if not (P.mk[k].m[P.daily.i1.iat[k]] == 959): continue
        for c, (h, m) in enumerate(CHECKS):
            j = P.bar_at(k, 0, (h, m), 3)
            if j is None: continue
            if (h, m) == (15, 30): Y[a, c] = close_ret(P, k, j)
            else:
                h2, m2 = (h, 30) if m == 0 else (h + 1, 0); e = P.bar_at(k, 0, (h2, m2), 3)
                if e is not None: Y[a, c] = P.ret(k, j, e)
    return Y


def mkt_grid(name, dates):
    """R[k, c]: market `name`'s move over the 30 minutes before each checkpoint (last trade before t vs last trade before t-30),
    on the back-adjusted series / the raw price; NaN when either price is older than 10 minutes."""
    M = L.Mins(name); R = np.full((len(dates), len(CHECKS)), np.nan)
    for a, d in enumerate(dates):
        d0 = np.datetime64(pd.Timestamp(d).date()).astype('datetime64[m]')
        for c, (h, m) in enumerate(CHECKS):
            t = d0 + (h * 60 + m) * MIN; j1 = int(np.searchsorted(M.t, t)) - 1; j0 = int(np.searchsorted(M.t, t - 30 * MIN)) - 1
            if j0 < 0 or j1 < 0 or M.t[j1] < t - 10 * MIN or M.t[j0] < t - 40 * MIN or j1 <= j0: continue
            R[a, c] = (M.ac[j1] - M.ac[j0]) / M.c[j1]
    return R


def leadlag(R, Y, look=250):
    """Walk-forward: beta and sd from the previous `look` sessions; trade when |r| > sd. Returns position grid (+1/-1/0) and the
    control grid (same sign rule where |r| <= sd)."""
    pos = np.zeros_like(Y); ctl = np.zeros_like(Y)
    for a in range(look, len(Y)):
        r, y = R[a - look:a].ravel(), Y[a - look:a].ravel(); ok = np.isfinite(r) & np.isfinite(y)
        if ok.sum() < 500: continue
        rr, yy = r[ok], y[ok]; beta = np.cov(rr, yy)[0, 1] / rr.var(ddof=1); sd = rr.std(ddof=1)
        for c in range(Y.shape[1]):
            x = R[a, c]
            if not np.isfinite(x) or not np.isfinite(Y[a, c]) or x == 0: continue
            if abs(x) > sd: pos[a, c] = np.sign(beta) * np.sign(x)
            else: ctl[a, c] = np.sign(beta) * np.sign(x)
    return pos, ctl


def main():
    import src2_test as T, regime_test as rt
    P = Pan('nq', 'mnqx'); Q = Pan('es', 'mes'); na = T.noisearea(); mkt = P.market(); usd = L.N_USD; c1, c3 = L.side_cost(1), L.side_cost(3)
    dts, Rr, NT, *_ = rt.joined('MNQ'); nt = pd.Series(NT[:, rt.A_DEF], index=dts)
    cal = calendar(P, na); verdicts = {}
    pr(f"EDGES3 (EDGES3_PLAN.txt). NQ/MNQ panel {len(P.dates)} sessions, ES/MES {len(Q.dates)}, NoiseArea series {len(na)} sessions "
       f"({na.index[0].date()} .. {na.index[-1].date()}). Cost {c1 * 1e4:.3f} bp a side; $ per micro = return x {usd:,.0f}. k = 7.")
    # ================================================================== A: earnings
    e = json.load(open(os.path.join(CAL, 'earnings_8k_202_20261020.json')))
    react = {c: reaction_dates(cal, e[c].split(',')) for c in ('AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'NVDA')}
    prim = set().union(*(react[c] for c in PRIMARY)); prim = {d for d in prim if pd.Timestamp(L.DISC[0]) <= d <= P.dates[-1]}
    kR = sorted(P.k_of[d] for d in prim if d in P.k_of); setR = set(kR)
    before = sorted(k - 1 for k in kR if k > 0 and (k - 1) not in setR)
    g_all = np.array([abs(gap(P, k)) for k in range(len(P.dates))]); other = np.array([k for k in range(len(P.dates)) if k not in setR])
    rat_r = np.nanmean(g_all[kR]) / np.nanmean(g_all[other]); rat_b = np.nanmean(g_all[before]) / np.nanmean(g_all[other])
    pr(f"\nA alignment check: {len(prim)} PRIMARY reaction sessions ({len(kR)} in the 1-minute panel); mean |overnight move| on them "
       f"{np.nanmean(g_all[kR]) * 1e4:.1f} bp vs {np.nanmean(g_all[other]) * 1e4:.1f} bp other sessions = {rat_r:.2f}x (need >= 1.30); "
       f"the session before: {rat_b:.2f}x (need <= 1.15)")
    if not (rat_r >= 1.30 and rat_b <= 1.15):
        pr("   ALIGNMENT FAILED - family A not run (plan)"); verdicts['A1'] = verdicts['A2'] = False
    else:
        assert 180 <= len(prim) <= 260, f"A: {len(prim)} reaction sessions outside 180-260"
        mask = pd.Series(na.index.isin(list(prim)), index=na.index)
        by = [f"NoiseArea per day on reaction sessions: " + ', '.join(f"{c} ${na[na.index.isin(list(react[c]))].mean() * usd:+.0f} "
              f"(n {int(na.index.isin(list(react[c])).sum())})" for c in ('AAPL', 'MSFT', 'AMZN', 'GOOGL', 'META', 'NVDA'))
              + f" | other days ${na[~mask].mean() * usd:+.0f}"]
        ok, net, s = extra_micro('A1 NoiseArea extra micro on mega-cap earnings reaction sessions', na, nt, mask, mkt, by)
        verdicts['A1'] = ok; net.rename('net').to_frame().assign(sides=s).to_csv(os.path.join(HERE, 'edges3_a1_daily.csv'))

        def a2(Px, ks):
            o = []
            for k in ks:
                gv = gap(Px, k)
                if np.isfinite(gv) and gv != 0: o.append((k, np.sign(gv) * close_ret(Px, k, Px.daily.i0.iat[k])))
            return o
        tr = a2(P, kR); assert 180 <= len(tr) <= 260, f"A2: {len(tr)}"
        g, s = to_series(P, tr); net = g - s * c1; ev = np.array([r for _, r in tr]) - 2 * c1
        ctl = np.array([r for _, r in a2(P, list(other))]) - 2 * c1
        qk = sorted(Q.k_of[d] for d in prim if d in Q.k_of); gq, sq = to_series(Q, a2(Q, qk)); netq = gq - sq * c1
        plus = prim | {d for c in ('META', 'NVDA') for d in react[c]}; kp = sorted(P.k_of[d] for d in plus if d in P.k_of)
        evp = np.array([r for _, r in a2(P, kp)]) - 2 * c1
        lines = [f"G3 ES/MES: n {int((sq > 0).sum())}, {netq[sq > 0].mean() * 1e4:+.2f} bp per event, t {tstat(netq):+.2f}",
                 f"diagnostic with META and NVDA added: n {len(evp)}, {evp.mean() * 1e4:+.2f} bp, t {tstat(evp):+.2f}"]
        verdicts['A2'] = judge('A2 earnings-reaction gap continuation 09:30 -> 16:00', net, s, ev, ctl, netq.mean() > 0, na, mkt, s * (c3 - c1),
                               L.DISC, {'disc': L.DISC, 'val': L.VAL, 'hold': L.HOLD}, lines)
        net.rename('net').to_frame().assign(sides=s).to_csv(os.path.join(HERE, 'edges3_a2_daily.csv'))
    # ================================================================== C: VIX term structure
    v = pd.read_csv(os.path.join(HERE, 'vix_history_cboe.csv')); v.index = pd.to_datetime(v.DATE, format='%m/%d/%Y')
    v3 = pd.read_csv(os.path.join(CAL, 'vix3m_history_cboe_20261020.csv')); v3.index = pd.to_datetime(v3.DATE, format='%m/%d/%Y')
    ratio = (v.CLOSE / v3.CLOSE).dropna()
    dv = v.CLOSE.diff(); ret = mkt[mkt != 0]
    c0 = np.corrcoef(ret.values, dv.reindex(ret.index).values)[0, 1] if True else 0
    both = pd.concat([ret.rename('r'), dv.rename('dv'), dv.shift(1).rename('dv1')], axis=1).dropna()
    c0, c_1 = both.r.corr(both.dv), both.r.corr(both.dv1)
    pr(f"\nC alignment check: corr(NQ session return, same-day VIX change) {c0:+.2f} (need < -0.30); with the previous day's change {c_1:+.2f} "
       f"(need within +-0.15)")
    if not (c0 < -0.30 and abs(c_1) <= 0.15):
        pr("   ALIGNMENT FAILED - family C not run (plan)"); verdicts['C1'] = False
    else:
        prev = pd.Series(ratio.reindex(ratio.index).values, index=ratio.index)
        sig = pd.Series([prev[prev.index < d].iloc[-1] if (prev.index < d).any() else np.nan for d in na.index], index=na.index)
        mask = sig >= 1.0; n = int(mask.sum()); assert 250 <= n <= 600, f"C1: {n} sessions"
        bk = pd.cut(sig, [0, 0.85, 0.95, 1.0, 9], right=False)
        lines = ["NoiseArea per day by previous-close VIX/VIX3M: " + ', '.join(f"{b} ${na[bk == b].mean() * usd:+.0f} (n {int((bk == b).sum())})"
                                                                         for b in bk.cat.categories)]
        ok, net, s = extra_micro('C1 NoiseArea extra micro after a backwardated VIX close (VIX/VIX3M >= 1.00)', na, nt, mask.fillna(False), mkt, lines)
        verdicts['C1'] = ok; net.rename('net').to_frame().assign(sides=s).to_csv(os.path.join(HERE, 'edges3_c1_daily.csv'))
    # ================================================================== B: cross-market lead-lag
    km = [k for k in range(len(P.dates)) if P.daily.seg.iat[k] == 1]; dates = [P.dates[k] for k in km]
    Y = nq_grid(P, km); qmap = [Q.k_of.get(d) for d in dates]
    Yq = np.full_like(Y, np.nan); okq = [a for a, k in enumerate(qmap) if k is not None]
    Yq[okq] = nq_grid(Q, [qmap[a] for a in okq])
    pr(f"\nB grid: {len(km)} MNQ-era sessions x {len(CHECKS)} checkpoints, NQ next-30-minute returns present {np.isfinite(Y).mean() * 100:.0f}%")
    for bid, name in (('B1', 'zn'), ('B2', 'm6e'), ('B3', 'mgc'), ('B4', 'mcl')):
        R = mkt_grid(name, dates); pos, ctlp = leadlag(R, Y)
        trades = [(km[a], pos[a, c] * Y[a, c]) for a in range(len(km)) for c in range(len(CHECKS)) if pos[a, c] != 0]
        assert len(trades) >= 500, f"{bid}: only {len(trades)} trades"
        g, s = to_series(P, trades); first = P.dates[min(k for k, _ in trades)]
        net = (g - s * c1)[g.index >= first]; s = s[s.index >= first]
        ev = np.array([r for _, r in trades]) - 2 * c1
        ctl = (ctlp * Y)[ctlp != 0] - 2 * c1
        tq = [(km[a], pos[a, c] * Yq[a, c]) for a in range(len(km)) for c in range(len(CHECKS)) if pos[a, c] != 0 and np.isfinite(Yq[a, c])]
        gq, sq = to_series(P, tq); netq = (gq - sq * c1)[gq.index >= first]
        lines = [f"G3 MES with the same signal: n {len(tq)}, {np.mean([r for _, r in tq]) * 1e4 - 2 * c1 * 1e4:+.2f} bp per trade, t {tstat(netq):+.2f}",
                 f"market {name}: {np.isfinite(R).mean() * 100:.0f}% of checkpoints priced; traded {len(trades)} of "
                 f"{int(np.isfinite(R[250:]).sum())} after warm-up; hit rate {np.mean(np.array([r for _, r in trades]) > 0) * 100:.0f}%; first trade {first.date()}"]
        verdicts[bid] = judge(f"{bid} lead-lag from {name.upper()} (walk-forward sign, |move| > 1 sd)", net, s, ev, ctl, netq.mean() > 0, na, mkt,
                              s * (c3 - c1), B_DISC, {'disc': B_DISC, 'hold': B_HOLD}, lines)
        net.rename('net').to_frame().assign(sides=s).to_csv(os.path.join(HERE, f'edges3_{bid.lower()}_daily.csv'))
    pr("\nSUMMARY: " + ', '.join(f"{k} {'PASS' if v else 'not adopted'}" for k, v in verdicts.items()))
    with open(os.path.join(HERE, 'edges3_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
