r"""Bulenox-legal routes to more profit (BULENOX_ROUTES_PLAN.txt, pre-registered 2026-10-20).
    python bulenox_routes.py a     Part A: tests T1 (FOMC night leg 18:05 -> 09:30), T2 (FOMC morning 09:30 -> 13:55), T3 (C15 night
                                   leg) with gates G1-G9, plus the diagnostics
    python bulenox_routes.py b     Part B: Bulenox Master / Qualification account model for NoiseArea at 1 (and 2) micros
Times are New York; 1-minute bars stamped at their start; fills at a bar's open; costs from src2_lib.side_cost.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L

FOMC = sorted(pd.Timestamp(s.strip()) for s in open(os.path.join(HERE, '..', '..', '..', 'bin', 'Custom', 'Strategies', 'fomc_dates.txt'))
              if s.strip() and not s.startswith('#'))
MIN = np.timedelta64(1, 'm')


def press_conf_1230(d):
    """The eight 2011-2012 press-conference meetings, whose statement came at 12:30 ET (same list as src2_strats_c._press_conf)."""
    return (d.year == 2011 and d.month in (4, 6, 11)) or (d.year == 2012 and d.month in (1, 4, 6, 9, 12))


class Pan:
    """Sessions of market `a` (NQ/ES) up to the join, then market `b` (MNQ/MES); per session the Mins object and its 09:30/15:59 bars."""
    def __init__(self, a, b):
        self.A, self.B = L.Mins(a), L.Mins(b)
        sess = [(d, self.A) for d in self.A.dates if pd.Timestamp(L.DISC[0]) <= d <= L.JOIN] + [(d, self.B) for d in self.B.dates if d > L.JOIN]
        self.mk = [m for _, m in sess]
        self.daily = pd.DataFrame([dict(date=d, seg=0 if m is self.A else 1, i0=m.days[d][0], i1=m.days[d][1], aO=m.ao[m.days[d][0]],
                                        aC=m.ac[m.days[d][1]], px=m.o[m.days[d][0]]) for d, m in sess])
        self.dates = pd.DatetimeIndex(self.daily.date); self.k_of = {d: k for k, d in enumerate(self.dates)}

    def bar_at(self, k, day_offset, hhmm, until):
        """Index of the first 1-minute bar at or after date(k)+day_offset hh:mm and before hh:mm+until minutes (else None)."""
        m = self.mk[k]; d = np.datetime64(self.dates[k].date()) + np.timedelta64(day_offset, 'D')
        t0 = d.astype('datetime64[m]') + (hhmm[0] * 60 + hhmm[1]) * MIN; j = int(np.searchsorted(m.t, t0))
        return j if j < len(m.t) and m.t[j] < t0 + until * MIN else None

    def night_entry(self, k, hhmm=(18, 5)):
        """The night-leg entry bar for session k: first bar >= hh:mm (within 25 min) on the calendar evening before the session date."""
        j = self.bar_at(k, -1, hhmm, 25); return j if j is not None and j < self.daily.i0.iat[k] else None

    def ret(self, k, j_from, j_to):
        m = self.mk[k]; return (m.ao[j_to] - m.ao[j_from]) / self.daily.px.iat[k]

    def market(self):
        D = self.daily; r = np.r_[0.0, np.diff(D.aC.values) / D.px.values[1:]]; r[np.r_[False, D.seg.values[1:] != D.seg.values[:-1]]] = 0
        return pd.Series(r, index=self.dates)


def tstat(x):
    x = np.asarray(x, float); sd = x.std(ddof=1) if len(x) > 2 else 0.0; return x.mean() / (sd / np.sqrt(len(x))) if sd > 0 else 0.0


def to_series(P, trades):
    """trades: list of (k, gross) -> (gross, sides) daily series, 2 sides per trade, booked on session k."""
    g = np.zeros(len(P.dates)); s = np.zeros(len(P.dates))
    for k, r in trades: g[k] += r; s[k] += 2
    return pd.Series(g, index=P.dates), pd.Series(s, index=P.dates)


# ------------------------------------------------------------------------------------------------ the three routes
def t1(P, hhmm=(18, 5)):
    out = []
    for d in FOMC:
        k = P.k_of.get(d)
        if k is None: continue
        j = P.night_entry(k, hhmm)
        if j is not None: out.append((k, P.ret(k, j, P.daily.i0.iat[k])))
    return out


def t2(P, all_1225=False):
    out = []
    for d in FOMC:
        k = P.k_of.get(d)
        if k is None: continue
        hhmm = (12, 25) if (all_1225 or press_conf_1230(d)) else (13, 55)
        e = P.bar_at(k, 0, hhmm, 5)
        if e is not None: out.append((k, P.ret(k, P.daily.i0.iat[k], e)))
    return out


def c15_signal(P, n=200):
    """C15 as in src2_strats_c: joined adjusted close (second market shifted onto the first's last close) above its n-day average."""
    D = P.daily; seg = D.seg.values; sh = np.zeros(len(D))
    if (seg == 1).any() and (seg == 0).any():
        j = int(np.argmax(seg == 1)); sh[j:] = D.aC.values[j - 1] - D.aO.values[j]
    c = D.aC.values + sh; sma = pd.Series(c).rolling(n).mean().values
    return np.where(c > sma, 1.0, 0.0)


def t3(P, hhmm=(18, 5), sig=None):
    sig = c15_signal(P) if sig is None else sig; D = P.daily; out = []
    for k in range(len(D) - 1):
        if sig[k] and D.seg.iat[k] == D.seg.iat[k + 1]:
            j = P.night_entry(k + 1, hhmm)
            if j is not None: out.append((k + 1, P.ret(k + 1, j, D.i0.iat[k + 1])))
    return out


def c15_close_to_open(P):
    """Reproduction check: C15 as SOURCES2 ran it (16:00 close -> 09:30 open)."""
    sig = c15_signal(P); D = P.daily; out = []
    for k in range(len(D) - 1):
        if sig[k] and D.seg.iat[k] == D.seg.iat[k + 1]: out.append((k + 1, (D.aO.iat[k + 1] - D.aC.iat[k]) / D.px.iat[k + 1]))
    return out


# ------------------------------------------------------------------------------------------------ Part A
def era(s, a, b): return s[(s.index >= pd.Timestamp(a)) & (s.index <= pd.Timestamp(b))]


def maxdd(usd):
    eq = np.cumsum(usd); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())


def part_a():
    import src2_test as T, regime_test as rt
    P = Pan('nq', 'mnqx'); Q = Pan('es', 'mes'); usd = L.N_USD; c1, c3 = L.side_cost(1), L.side_cost(3)
    na = T.noisearea(); mkt = P.market(); lines = []
    pr = lambda *a: (print(*a), lines.append(' '.join(str(x) for x in a)))
    pr(f"BULENOX ROUTES - Part A (BULENOX_ROUTES_PLAN.txt). Sessions: {len(P.dates)} NQ/MNQ ({P.dates[0].date()} .. {P.dates[-1].date()}), "
       f"{len(Q.dates)} ES/MES. P_REF {L.P_REF:,.2f}, $ per micro = return x {usd:,.0f}; 1 tick + $0.50 a side = {c1 * 1e4:.3f} bp")
    # reproduction check of the machinery against SOURCES2's C15 (t 3.96 there)
    g, s = to_series(P, c15_close_to_open(P)); pr(f"check: C15 16:00 -> 09:30 reproduced: n {int((s > 0).sum())}, net t {tstat(g - s * c1):+.2f} (SOURCES2: 3.96)")
    expect = {'T1': (120, 140), 'T2': (125, 135), 'T3': (3100, 3400)}
    verdicts = {}
    for name, fn in (('T1', t1), ('T2', t2), ('T3', t3)):
        tr = fn(P); n = len(tr); lo, hi = expect[name]
        assert lo <= n <= hi, f"{name}: {n} events outside the expected {lo}-{hi}"
        g, s = to_series(P, tr); net = g - s * c1; net3 = g - s * c3; ev = np.array([r for _, r in tr]) - 2 * c1
        tq = fn(Q); gq, sq = to_series(Q, tq); netq = gq - sq * c1
        t_full = tstat(net); t_disc = tstat(era(net, *L.DISC)); m_hold = era(net, *L.HOLD).mean()
        best5 = net.sort_values().index[-5:]; t_d5 = tstat(net.drop(best5)); t_3tk = tstat(net3)
        eras = {k: era(net, a, b).mean() for k, (a, b) in (('disc', L.DISC), ('val', L.VAL), ('hold', L.HOLD))}
        ta, beta = T.alpha_t(net, mkt)
        common = na.index.intersection(net.index); a, b = na.reindex(common).values, net.reindex(common).values
        p_add = rt.boot_sharpe_diff(common, a + b, a); dd_na = maxdd(a * usd); dd_c = maxdd((a + b) * usd)
        yr = net.groupby(net.index.year).sum(); full_yrs = yr[(yr.index >= 2011) & (yr.index <= 2025)]; neg_yrs = int((full_yrs < 0).sum())
        G = {'G1 t >= 2.50': t_full >= 2.5, 'G2 disc t >= 2.0 & holdout > 0': t_disc >= 2.0 and m_hold > 0, 'G3 ES/MES mean > 0': netq.mean() > 0,
             'G4 drop-5 t >= 2.0': t_d5 >= 2.0, 'G5 3-tick t >= 2.0': t_3tk >= 2.0, 'G6 eras > 0': all(v > 0 for v in eras.values())}
        if name == 'T3': G['G6 neg years <= 1'] = neg_yrs <= 1
        G['G7 alpha t >= 2.0'] = ta >= 2.0
        ctl_t = welch = None
        if name in ('T1', 'T2'):
            fset = {P.k_of[d] for d in FOMC if d in P.k_of}; ctl = []
            for k in range(len(P.dates)):
                if k in fset: continue
                if name == 'T1':
                    j = P.night_entry(k)
                    if j is not None: ctl.append(P.ret(k, j, P.daily.i0.iat[k]) - 2 * c1)
                else:
                    e = P.bar_at(k, 0, (13, 55), 5)
                    if e is not None: ctl.append(P.ret(k, P.daily.i0.iat[k], e) - 2 * c1)
            ctl = np.array(ctl); ctl_t = tstat(ctl)
            welch = (ev.mean() - ctl.mean()) / np.sqrt(ev.var(ddof=1) / len(ev) + ctl.var(ddof=1) / len(ctl))
            G['G7 Welch event-vs-control t >= 2.0'] = welch >= 2.0
            if name == 'T2': G['G7 control |t| < 2'] = abs(ctl_t) < 2
        G['G8 ADDITION P < 0.0167'] = p_add < 0.0167; G['G9 maxDD <= 1.10 x NoiseArea'] = dd_c <= 1.10 * dd_na
        ok = all(G.values()); verdicts[name] = ok
        pr(f"\n{name}: {n} events | per event net {ev.mean() * 1e4:+.1f} bp (${ev.mean() * usd:+,.0f}/micro), event-level t {tstat(ev):+.2f}, "
           f"win {np.mean(ev > 0) * 100:.0f}% | ${net.sum() * usd / (len(net) / 252):+,.0f}/yr per micro")
        pr(f"   G1 full t {t_full:+.2f} | G2 disc t {t_disc:+.2f}, holdout mean {m_hold * 1e4:+.2f} bp/day | G3 ES/MES n {len(tq)}, "
           f"mean {netq[sq > 0].mean() * 1e4:+.1f} bp/event, t {tstat(netq):+.2f} | G4 drop-5 t {t_d5:+.2f} | G5 3-tick t {t_3tk:+.2f}")
        pr(f"   G6 era means (bp per event): " + ', '.join(f"{k} {era(net, *rng)[era(s, *rng) > 0].mean() * 1e4:+.1f} (n {int((era(s, *rng) > 0).sum())})"
                                                     for k, rng in (('disc', L.DISC), ('val', L.VAL), ('hold', L.HOLD)))
           + (f" | negative full years 2011-2025: {neg_yrs}" if name == 'T3' else ''))
        pr(f"   G7 alpha t {ta:+.2f} (beta {beta:+.3f})" + (f" | control same window non-FOMC: t {ctl_t:+.2f}, Welch t {welch:+.2f}" if ctl_t is not None else ''))
        pr(f"   G8 NoiseArea + {name} Sharpe {rt.sharpe(a + b):.3f} vs {rt.sharpe(a):.3f}, P {p_add:.4f} | G9 maxDD ${dd_c:,.0f} vs NoiseArea ${dd_na:,.0f} "
           f"({dd_c / dd_na:.2f}x) | 2-tick t {tstat(g - s * L.side_cost(2)):+.2f}")
        pr("   per year (net $ per micro / events): " + ' | '.join(f"{y} {v * usd:+,.0f}/{int(n_)}" for (y, v), n_ in
                                                                    zip(yr.items(), (s > 0).groupby(s.index.year).sum())))
        pr(f"   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()))
        pr(f"   VERDICT {name}: {'PASSES - all gates' if ok else 'NOT ADOPTED'}")
    # ---------------------------------------------------------------- diagnostics (no decision)
    pr("\nDIAGNOSTICS (no decision)")
    seg = {'16:00->16:55': [], '16:55->18:05': [], '18:05->09:30': [], '16:00->09:30': []}
    for d in FOMC:
        k = P.k_of.get(d)
        if k is None or k == 0 or P.daily.seg.iat[k - 1] != P.daily.seg.iat[k]: continue
        m = P.mk[k]; i1p = P.daily.i1.iat[k - 1]; j55 = P.bar_at(k - 1, 0, (16, 55), 5); j18 = P.night_entry(k); i0 = P.daily.i0.iat[k]
        if j55 is None or j18 is None: continue
        px = P.daily.px.iat[k]; c16 = m.ac[i1p]
        seg['16:00->16:55'].append((m.ao[j55] - c16) / px); seg['16:55->18:05'].append((m.ao[j18] - m.ao[j55]) / px)
        seg['18:05->09:30'].append((m.ao[i0] - m.ao[j18]) / px); seg['16:00->09:30'].append((m.ao[i0] - c16) / px)
    for k_, v in seg.items(): v = np.array(v); pr(f"   FOMC eve gross {k_}: n {len(v)}, mean {v.mean() * 1e4:+.1f} bp, t {tstat(v):+.2f}")
    for hh in ((18, 30), (19, 0), (20, 0)):
        tr = t1(P, hh); ev = np.array([r for _, r in tr]) - 2 * c1; pr(f"   T1 entry {hh[0]}:{hh[1]:02d}: n {len(ev)}, net {ev.mean() * 1e4:+.1f} bp, t {tstat(ev):+.2f}")
    tr = t2(P, all_1225=True); ev = np.array([r for _, r in tr]) - 2 * c1; pr(f"   T2 ending 12:25 every day: n {len(ev)}, net {ev.mean() * 1e4:+.1f} bp, t {tstat(ev):+.2f}")
    allk = [(k, P.ret(k, j, P.daily.i0.iat[k])) for k in range(len(P.dates)) for j in [P.night_entry(k)] if j is not None]
    g, s = to_series(P, allk); net = g - s * c1
    pr(f"   every night 18:05->09:30 (no filter): n {len(allk)}, net t {tstat(net):+.2f}, ${net.sum() * usd / (len(net) / 252):+,.0f}/yr per micro, "
       f"maxDD ${maxdd(net.values * usd):,.0f}")
    for name, fn in (('T1', t1), ('T2', t2), ('T3', t3)):
        g, s = to_series(P, fn(P)); (g - s * c1).rename('net').to_frame().assign(sides=s).to_csv(os.path.join(HERE, f'bulenox_{name.lower()}_daily.csv'))
    with open(os.path.join(HERE, 'bulenox_routes_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')
    return verdicts


# ------------------------------------------------------------------------------------------------ Part B
SIZES = {'25K': dict(dd=1500, dll=500, reserve=1600, cap=1000, act=143, qual=145, target=1500),
         '50K': dict(dd=2500, dll=1100, reserve=2600, cap=1500, act=148, qual=175, target=3000),
         '100K': dict(dd=3000, dll=2200, reserve=3100, cap=1750, act=248, qual=215, target=6000),
         '150K': dict(dd=4500, dll=3300, reserve=4600, cap=2000, act=498, qual=325, target=9000)}


def boot_paths(n_days, n_paths, x, nt, block=21, seed=20261020):
    rng = np.random.default_rng(seed); nb = -(-n_days // block); starts = rng.integers(0, len(x) - block, (n_paths, nb))
    idx = (starts[:, :, None] + np.arange(block)).reshape(n_paths, -1)[:, :n_days]
    return x[idx], nt[idx]


def master(X, NT, p, eod=True):
    """One Master account per path (vectorised over paths). X: daily $ P&L (paths x days), NT: trades that day.
    Order each day: P&L (daily loss limit before the lock) -> close below the threshold closes the account -> threshold trails the
    closing high and locks at start + $100 -> payout bookkeeping (10 trading days, 40% consistency, reserve, caps; 3 sessions'
    wait after a request, which do not count toward the next period). Returns payouts to the trader per day and the closing day."""
    n_p, n_d = X.shape; pay = np.zeros((n_p, n_d)); open_until = np.full(n_p, n_d)
    bal = np.zeros(n_p); hi = np.zeros(n_p); thr = np.full(n_p, -float(p['dd'])); locked = np.zeros(n_p, bool); alive = np.ones(n_p, bool)
    n_pay = np.zeros(n_p, int); days = np.zeros(n_p, int); ptot = np.zeros(n_p); pbest = np.zeros(n_p); wait = np.zeros(n_p, int)
    for t in range(n_d):
        x = X[:, t].copy()
        if eod: x = np.where(locked, x, np.maximum(x, -p['dll']))
        x = np.where(alive, x, 0.0); bal += x
        br = alive & (bal < thr); open_until[br] = t; alive &= ~br
        hi = np.where(alive, np.maximum(hi, bal), hi)
        up = alive & ~locked; thr = np.where(up, np.maximum(thr, hi - p['dd']), thr)
        lk = up & (thr >= 100); thr[lk] = 100.0; locked |= lk
        w = alive & (wait > 0); wait[w] -= 1; act = alive & ~w
        days += (act & (NT[:, t] > 0)).astype(int); ptot = np.where(act, ptot + x, ptot); pbest = np.where(act, np.maximum(pbest, x), pbest)
        amt = np.minimum(np.where(n_pay < 3, float(p['cap']), 0.5 * bal - 1), bal - p['reserve'])
        el = act & (days >= 10) & (ptot > 0) & (pbest <= 0.4 * ptot) & (amt >= 1000)
        bal[el] -= amt[el]; pay[el, t] = 0.9 * amt[el]; n_pay[el] += 1; days[el] = 0; ptot[el] = 0.0; pbest[el] = 0.0; wait[el] = 3
    return pay, open_until


def qualify(X, p, n_days=21, trailing=False):
    """P(reaching the target within n_days sessions without touching the drawdown); EOD on closes (Option 2 with DLL) or trailing
    on daily closes (Option 1 approximation, optimistic: the real trailing follows intraday highs)."""
    n_p = X.shape[0]; bal = np.zeros(n_p); hi = np.zeros(n_p); alive = np.ones(n_p, bool); ok = np.zeros(n_p, bool)
    for t in range(n_days):
        x = X[:, t] if trailing else np.maximum(X[:, t], -p['dll'])
        bal = np.where(alive, bal + x, bal); alive &= ~(bal < hi - p['dd'])
        hi = np.where(alive, np.maximum(hi, bal), hi); hit = alive & (bal >= p['target']); ok |= hit; alive &= ~hit
    return ok.mean()


def part_b(extra=None):
    d = pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0, parse_dates=True)
    usd = (d.r_today * L.N_USD).values; nt = d.nt.values.astype(float)
    if extra is not None:
        e = extra.reindex(d.index).fillna(0.0).values * L.N_USD; usd = usd + e; nt = nt + (e != 0)
    lines = []; pr = lambda *a: (print(*a), lines.append(' '.join(str(x) for x in a)))
    pr(f"BULENOX ACCOUNT MODEL - Part B. NoiseArea 1 micro, 16 years at today's cost (scaling_daily.csv r_today x {L.N_USD:,.0f}), "
       f"{len(usd)} sessions, mean ${usd.mean():+.2f}/session, {'with ' + extra.name if extra is not None else 'NoiseArea only'}. "
       f"Bootstrap 21-session blocks, 20,000 paths x 504 sessions, seed 20261020.")
    for mult in (1, 2):
        X, NT = boot_paths(504, 20000, usd * mult, nt)
        if mult == 1 and extra is None:
            pr("check vs longhist_bulenox.txt (16 years, no time limit, 2 years, end-of-day trailing): " + ' | '.join(
                f"{nm} {qualify(X, p, 504, trailing=True) * 100:.0f}%" for nm, p in SIZES.items()) + "  (there: 63% / 70% / 69% / 58%)")
        pr(f"\n--- {mult} micro{'s' if mult > 1 else ''}{' (information only - sizing waits for 60 clean live sessions)' if mult > 1 else ''} ---")
        pr("size  | MASTER Option 2 (EOD): open 1y / 2y | E[payout to you] y1 / y2 | P(any payout in 2y) | first payout median | "
           "net of activation, 2y | 5 accounts, 2y | QUALIFICATION within 21 sessions: Opt2 EOD / Opt1 (optimistic)")
        for nm, p in SIZES.items():
            pay, ou = master(X, NT, p); y1 = pay[:, :252].sum(1).mean(); y2 = pay[:, 252:].sum(1).mean(); anyp = (pay.sum(1) > 0).mean()
            first = np.array([np.argmax(r > 0) for r in pay if (r > 0).any()]); q2 = qualify(X, p); q1 = qualify(X, p, trailing=True)
            pr(f"{nm:5s} | {np.mean(ou > 251) * 100:4.0f}% / {np.mean(ou > 503) * 100:4.0f}% | ${y1:6,.0f} / ${y2:6,.0f} | {anyp * 100:4.0f}% | "
               f"{(np.median(first) / 21 if len(first) else float('nan')):4.1f} mo | ${y1 + y2 - p['act']:6,.0f} | ${5 * (y1 + y2 - p['act']):7,.0f} | "
               f"{q2 * 100:4.1f}% / {q1 * 100:4.1f}% (fee ${p['qual']} -> ${p['qual'] / max(q2, 1e-9):,.0f} per pass on Option 2)")
    tag = '' if extra is None else '_' + extra.name
    with open(os.path.join(HERE, f'bulenox_account_model{tag}.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    arg = sys.argv[1:2]
    if arg == ['a']: part_a()
    elif arg == ['b']:
        ex = None
        if len(sys.argv) > 2:
            ex = pd.read_csv(os.path.join(HERE, f'bulenox_{sys.argv[2].lower()}_daily.csv'), index_col=0, parse_dates=True).net.rename(sys.argv[2])
        part_b(ex)
    else: print(__doc__)
