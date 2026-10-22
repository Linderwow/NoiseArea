r"""Event-day batch (EVENTDAYS_PLAN.txt, pre-registered 2026-10-20): E1-E5 on CPI / PPI / NFP / Treasury-auction days, intraday only,
plus the Nasdaq-100 rebalance diagnostic.
    python event_days.py      -> eventdays_output.txt, eventdays_e1..e5_daily.csv
Calendars from research\event_calendars\ (BLS archive dates, Treasury fiscaldata auctions). Machinery from bulenox_routes.py.
"""
import os, sys, json, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L
import bulenox_routes as B
from bulenox_routes import Pan, tstat, to_series, era, maxdd

CAL = os.path.join(HERE, 'event_calendars')
out = []
def pr(*a): print(*a); out.append(' '.join(str(x) for x in a))


def calendars():
    bls = json.load(open(os.path.join(CAL, 'bls_release_dates_20261020.json')))
    fam = {k: {pd.Timestamp(d) for d in bls[k]} for k in ('cpi', 'ppi', 'empsit')}
    a = pd.DataFrame(json.load(open(os.path.join(CAL, 'treasury_auctions_10y30y_20261020.json')))['data'])
    a = a[a.inflation_index_security == 'No']
    tm = lambda s: dt.datetime.strptime(s.strip(), '%I:%M %p'); auct = {}
    for r in a.itertuples():
        t = tm(r.closing_time_comp); d = pd.Timestamp(r.auction_date); mins = t.hour * 60 + t.minute
        auct[d] = min(auct.get(d, 10 ** 6), mins)
    ndx = {pd.Timestamp(B_third_friday(y, m)) for y in range(2010, 2027) for m in (3, 6, 9, 12)} | {pd.Timestamp('2011-04-29'), pd.Timestamp('2023-07-21')}
    return fam, auct, ndx


def B_third_friday(y, m):
    d = dt.date(y, m, 1); d += dt.timedelta(days=(4 - d.weekday()) % 7); return d + dt.timedelta(days=14)


def close_ret(P, k, j):
    """From bar j's open to the session's last RTH close."""
    m = P.mk[k]; return (m.ac[P.daily.i1.iat[k]] - m.ao[j]) / P.daily.px.iat[k]


def full_session(P, k): return P.mk[k].m[P.daily.i1.iat[k]] == 959


# ------------------------------------------------------------------------------------------------ rules: fn(P, ks) -> [(k, gross)]
def e1(P, ks):
    o = []
    for k in ks:
        j = P.bar_at(k, 0, (8, 25), 5)
        if j is not None and j < P.daily.i0.iat[k]: o.append((k, close_ret(P, k, j)))
    return o


def e2(P, ks):
    o = []; D = P.daily
    for k in ks:
        if k == 0 or D.seg.iat[k - 1] != D.seg.iat[k] or not full_session(P, k): continue
        j10 = P.bar_at(k, 0, (10, 0), 5); j1530 = P.bar_at(k, 0, (15, 30), 5)
        if j10 is None or j1530 is None: continue
        m = P.mk[k]; s = np.sign(m.ao[j10] - m.ac[D.i1.iat[k - 1]])
        if s != 0: o.append((k, s * close_ret(P, k, j1530)))
    return o


def e3(P, ks):
    o = []
    for k in ks:
        j = P.bar_at(k, 0, (8, 30), 5); i0 = P.daily.i0.iat[k]
        if j is None or j >= i0: continue
        s = np.sign(P.mk[k].ao[i0] - P.mk[k].ao[j])
        if s != 0: o.append((k, s * close_ret(P, k, i0)))
    return o


def e4(P, ks, auct_min):
    o = []
    for k in ks:
        c = auct_min.get(P.dates[k], 780) + 1; j = P.bar_at(k, 0, (c // 60, c % 60), 5)
        if j is not None and j < P.daily.i1.iat[k]: o.append((k, close_ret(P, k, j)))
    return o


def e4_mirror(P, ks, auct_min):
    o = []
    for k in ks:
        c = auct_min.get(P.dates[k], 780); j = P.bar_at(k, 0, (c // 60, c % 60), 5)
        if j is not None and j > P.daily.i0.iat[k]: o.append((k, -P.ret(k, P.daily.i0.iat[k], j)))
    return o


def kset(P, dates): return sorted(P.k_of[d] for d in dates if d in P.k_of)


# ------------------------------------------------------------------------------------------------ gates
def judge(name, net, sides, ev, ctl_ev, netq, na, mkt, extra_cost3, lines_fn):
    import src2_test as T, regime_test as rt
    usd = L.N_USD; t_full = tstat(net); t_disc = tstat(era(net, *L.DISC)); m_hold = era(net, *L.HOLD).mean()
    best5 = net.sort_values().index[-5:]; t_d5 = tstat(net.drop(best5)); t_3tk = tstat(net - extra_cost3)
    eras = {k: era(net, a, b).mean() for k, (a, b) in (('disc', L.DISC), ('val', L.VAL), ('hold', L.HOLD))}
    ta, beta = T.alpha_t(net, mkt)
    welch = (ev.mean() - ctl_ev.mean()) / np.sqrt(ev.var(ddof=1) / len(ev) + ctl_ev.var(ddof=1) / len(ctl_ev))
    common = na.index.intersection(net.index); a, b = na.reindex(common).values, net.reindex(common).values
    p_add = rt.boot_sharpe_diff(common, a + b, a); dd_na = maxdd(a * usd); dd_c = maxdd((a + b) * usd)
    G = {'G1 t >= 2.60': t_full >= 2.6, 'G2 disc t >= 2.0 & holdout > 0': t_disc >= 2.0 and m_hold > 0, 'G3 second instrument': netq,
         'G4 drop-5 t >= 2.0': t_d5 >= 2.0, 'G5 3-tick t >= 2.0': t_3tk >= 2.0, 'G6 eras > 0': all(v > 0 for v in eras.values()),
         'G7 alpha t >= 2.0': ta >= 2.0, 'G7 Welch t >= 2.0': welch >= 2.0, 'G8 ADDITION P < 0.01': p_add < 0.01,
         'G9 maxDD <= 1.10x': dd_c <= 1.10 * dd_na}
    yr = net.groupby(net.index.year).sum(); ny = (sides > 0).groupby(sides.index.year).sum()
    pr(f"\n{name}: {len(ev)} events | net {ev.mean() * 1e4:+.2f} bp (${ev.mean() * usd:+,.0f}/micro) per event, event t {tstat(ev):+.2f}, "
       f"win {np.mean(ev > 0) * 100:.0f}% | ${net.sum() * usd / (len(net) / 252):+,.0f}/yr per micro")
    pr(f"   G1 t {t_full:+.2f} | G2 disc t {t_disc:+.2f}, holdout {m_hold * 1e4:+.3f} bp/day | G4 drop-5 t {t_d5:+.2f} | G5 3-tick t {t_3tk:+.2f} | "
       f"G6 eras (bp/day) disc {eras['disc'] * 1e4:+.3f} val {eras['val'] * 1e4:+.3f} hold {eras['hold'] * 1e4:+.3f}")
    pr(f"   G7 alpha t {ta:+.2f} (beta {beta:+.3f}); control (same rule, other days) n {len(ctl_ev)}, {ctl_ev.mean() * 1e4:+.2f} bp, t {tstat(ctl_ev):+.2f}; "
       f"Welch t {welch:+.2f} | G8 NoiseArea + it Sharpe {rt.sharpe(a + b):.3f} vs {rt.sharpe(a):.3f}, P {p_add:.4f} | "
       f"G9 maxDD ${dd_c:,.0f} vs ${dd_na:,.0f} ({dd_c / dd_na:.2f}x)")
    for ln in lines_fn(): pr('   ' + ln)
    pr("   per year ($/micro, events): " + ' | '.join(f"{y} {v * usd:+,.0f}/{int(ny.get(y, 0))}" for y, v in yr.items()))
    ok = all(G.values()); pr("   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()))
    pr(f"   VERDICT {name.split()[0]}: {'PASSES - all gates' if ok else 'NOT ADOPTED'}")
    return ok


def main():
    import src2_test as T, regime_test as rt
    fam, auct, ndx = calendars(); P = Pan('nq', 'mnqx'); Q = Pan('es', 'mes'); c1, c3 = L.side_cost(1), L.side_cost(3); usd = L.N_USD
    na = T.noisearea(); mkt = P.market()
    macro = fam['cpi'] | fam['ppi'] | fam['empsit']
    pr(f"EVENT-DAY BATCH (EVENTDAYS_PLAN.txt). Calendars: CPI {len(fam['cpi'])}, PPI {len(fam['ppi'])}, NFP {len(fam['empsit'])} release dates 2010-2026 "
       f"(MACRO union {len(macro)}); auctions {len(auct)} days; NDX rebalance days {len(ndx)}. Sessions {len(P.dates)} NQ/MNQ, {len(Q.dates)} ES/MES. "
       f"Cost 1 tick + $0.50 a side = {c1 * 1e4:.3f} bp; $ per micro = return x {usd:,.0f}")
    kM, kA, kN = kset(P, macro), kset(P, auct), kset(P, ndx); allk = set(range(len(P.dates)))
    qM, qA = kset(Q, macro), kset(Q, auct)
    pr(f"   in the panel: MACRO {len(kM)} sessions, AUCT {len(kA)}, NDX {len(kN)}; MACRO sessions missing from the panel (holiday or "
       f"half-day releases): {len([d for d in macro if pd.Timestamp(L.DISC[0]) <= d <= P.dates[-1] and d not in P.k_of])}")
    expect = {'E1': (540, 600), 'E2': (530, 600), 'E3': (540, 600), 'E4': (360, 410)}
    rules = {'E1': (lambda Px, ks: e1(Px, ks), kM, qM), 'E2': (lambda Px, ks: e2(Px, ks), kM, qM), 'E3': (lambda Px, ks: e3(Px, ks), kM, qM),
             'E4': (lambda Px, ks: e4(Px, ks, auct), kA, qA)}
    titles = {'E1': 'E1 announcement-day long 08:25 -> 16:00 (MACRO)', 'E2': 'E2 news-day momentum: sign(prev close -> 10:00) held 15:30 -> 16:00 (MACRO)',
              'E3': 'E3 release-reaction continuation: sign(08:30 -> 09:30) held 09:30 -> 16:00 (MACRO)', 'E4': 'E4 post-auction long, auction close + 1 min -> 16:00 (AUCT)'}
    verdicts = {}
    for name, (fn, ks, qks) in rules.items():
        tr = fn(P, ks); lo, hi = expect[name]; assert lo <= len(tr) <= hi, f"{name}: {len(tr)} events outside {lo}-{hi}"
        g, s = to_series(P, tr); net = g - s * c1; ev = np.array([r for _, r in tr]) - 2 * c1
        ctl = fn(P, sorted(allk - set(ks))); ctl_ev = np.array([r for _, r in ctl]) - 2 * c1
        gq, sq = to_series(Q, fn(Q, qks)); netq = (gq - sq * c1)
        fams = {}
        if name != 'E4':
            for f_, lab in (('cpi', 'CPI'), ('ppi', 'PPI'), ('empsit', 'NFP')):
                x = np.array([r for k, r in tr if P.dates[k] in fam[f_]]) - 2 * c1; fams[lab] = x
        lines = lambda netq=netq, sq=sq, fams=fams: ([f"G3 ES/MES: n {int((sq > 0).sum())}, {netq[sq > 0].mean() * 1e4:+.2f} bp per event, t {tstat(netq):+.2f}"] +
                                                    ([' | '.join(f"{lab} n {len(x)} {x.mean() * 1e4:+.2f} bp t {tstat(x):+.2f}" for lab, x in fams.items())] if fams else []))
        verdicts[name] = judge(titles[name], net, s, ev, ctl_ev, netq.mean() > 0, na, mkt, s * (c3 - c1), lines)
        net.rename('net').to_frame().assign(sides=s).to_csv(os.path.join(HERE, f'eventdays_{name.lower()}_daily.csv'))
    # E5 - NoiseArea's extra micro on MACRO days
    dts, R, NT, c, *_ = rt.joined('MNQ'); nt = pd.Series(NT[:, rt.A_DEF], index=dts)
    mask = pd.Series(na.index.isin([P.dates[k] for k in kM]), index=na.index)
    net5 = na * mask; s5 = (nt.reindex(na.index).fillna(0) * 2) * mask
    ev5 = na[mask & (nt.reindex(na.index).fillna(0) > 0)].values; ctl5 = na[~mask & (nt.reindex(na.index).fillna(0) > 0)].values
    ex = lambda a_, b_: na[mask & (na.index >= pd.Timestamp(a_)) & (na.index <= pd.Timestamp(b_))].mean() - na[~mask & (na.index >= pd.Timestamp(a_)) & (na.index <= pd.Timestamp(b_))].mean()
    x_nq, x_mnq = ex(L.DISC[0], '2019-05-05'), ex('2019-05-06', '2026-10-16')
    fams5 = {lab: na[na.index.isin(list(fam[f_])) & (nt.reindex(na.index).fillna(0) > 0)].values for f_, lab in (('cpi', 'CPI'), ('ppi', 'PPI'), ('empsit', 'NFP'))}
    lines5 = lambda: [f"G3 NoiseArea event-day excess per day: NQ 2010-2019 {x_nq * 1e4:+.2f} bp, MNQ 2019-2026 {x_mnq * 1e4:+.2f} bp",
                      ' | '.join(f"{lab} n {len(x)} {x.mean() * 1e4:+.2f} bp t {tstat(x):+.2f}" for lab, x in fams5.items()),
                      f"(events = MACRO days on which NoiseArea traded: {len(ev5)} of {int(mask.sum())})"]
    # NoiseArea's series includes the quarterly expiry days that the Databento continuous files lack (KB: no RTH session there):
    # the market return is 0 on those ~4 days a year for the alpha regression only
    verdicts['E5'] = judge('E5 NoiseArea extra micro on MACRO days', net5, s5, ev5, ctl5, x_nq > 0 and x_mnq > 0, na,
                           mkt.reindex(na.index).fillna(0.0), s5 * (c3 - c1), lines5)
    net5.rename('net').to_frame().assign(sides=s5).to_csv(os.path.join(HERE, 'eventdays_e5_daily.csv'))
    # ---------------------------------------------------------------- diagnostics
    pr("\nDIAGNOSTICS (no decision)")
    traded = nt.reindex(na.index).fillna(0) > 0
    for lab, ds in (('CPI', fam['cpi']), ('PPI', fam['ppi']), ('NFP', fam['empsit']), ('AUCT', set(auct)), ('NDX', ndx)):
        m_ = na.index.isin(list(ds)); pr(f"   NoiseArea on {lab} days: n {int(m_.sum())}, ${na[m_].mean() * usd:+.1f}/day vs ${na[~m_].mean() * usd:+.1f} other days")
    mir = e4_mirror(P, kA, auct); x = np.array([r for _, r in mir]) - 2 * c1
    pr(f"   E4 mirror (short 09:30 -> auction close): n {len(x)}, {x.mean() * 1e4:+.2f} bp, t {tstat(x):+.2f}")
    lh = []
    for k in kN:
        j = P.bar_at(k, 0, (15, 0), 5)
        if j is not None and full_session(P, k): lh.append(close_ret(P, k, j))
    lh = np.array(lh); pr(f"   NDX rebalance days, 15:00 -> 16:00 gross: n {len(lh)}, {lh.mean() * 1e4:+.2f} bp, t {tstat(lh):+.2f}"
                          f" - the Databento continuous files have no RTH session on quarterly expiry days (= these third Fridays), so only"
                          f" {len(kN)} of {len(ndx)} are in the 1-minute panel; NoiseArea's line above uses its own series")
    pr(f"\nSUMMARY: " + ', '.join(f"{k} {'PASS' if v else 'not adopted'}" for k, v in verdicts.items()))
    with open(os.path.join(HERE, 'eventdays_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
