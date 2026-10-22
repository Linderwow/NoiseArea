r"""Market-mechanics effects not tested before (MECH_PLAN.txt, pre-registered 2026-10-21).
    python mech.py      -> mech_output.txt, mech_<id>_daily.csv
T1 third-Friday price spike short (primary); S1 month-end rebalancing, S2 expiration pinning, S3 round-number crosses, S4 weekend
crypto, S5 intraday periodicity (secondary). Intraday only.
"""
import os, sys, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import numpy as np, pandas as pd
import src2_lib as L
import nav_lab as N
from bulenox_routes import tstat, maxdd

CAL = os.path.join(HERE, 'event_calendars'); JOIN = pd.Timestamp('2019-05-05')
C1, C3 = L.side_cost(1), L.side_cost(3); USD = L.N_USD
MIN = np.timedelta64(1, 'm')
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


# ------------------------------------------------------------------------------------------------ price access
class Src:
    """Session prices from a Databento 1-minute cache (raw o/c; a* back-adjusted) by date."""
    def __init__(self, name): self.M = L.Mins(name); self.dates = set(self.M.days)

    def has(self, d): return pd.Timestamp(d) in self.M.days

    def at(self, d, hhmm, field='o'):
        M = self.M; i0, i1 = M.days[pd.Timestamp(d)]
        if hhmm == 'close': return M.c[i1] if field in ('o', 'c') else M.ac[i1]
        t = np.datetime64(pd.Timestamp(d).date()).astype('datetime64[m]') + (hhmm[0] * 60 + hhmm[1]) * MIN
        j = int(np.searchsorted(M.t, t))
        if j > i1 or M.t[j] > t + 2 * MIN: return np.nan
        return {'o': M.o, 'c': M.c, 'ao': M.ao, 'ac': M.ac}[field][j]

    def open930(self, d): i0, _ = self.M.days[pd.Timestamp(d)]; return self.M.o[i0]

    def full(self, d): _, i1 = self.M.days[pd.Timestamp(d)]; return self.M.m[i1] == 959


class NTSrc:
    """MNQ NinjaTrader 5-minute bars (back-adjusted; stamped at the bar END), with the raw 09:30 open for scaling."""
    def __init__(self):
        from research_engine import Market
        self.mk = N.market('MNQ'); self.days = {pd.Timestamp(x.date): x for x in self.mk.days}
        raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open; self.raw = raw

    def has(self, d): return pd.Timestamp(d) in self.days

    def px(self, d):
        v = self.raw.get(pd.Timestamp(d)); return float(v) if v is not None and np.isfinite(v) else self.days[pd.Timestamp(d)].O[0]

    def at(self, d, hhmm, field='o'):
        x = self.days[pd.Timestamp(d)]
        if hhmm == 'close': return x.C[-1] if x.M[-1] >= 955 else np.nan
        m = hhmm[0] * 60 + hhmm[1]
        if field == 'o':                                       # price at hh:mm = open of the bar that starts at hh:mm (stamped +5)
            k = np.where(x.M == m + 5)[0]; return x.O[k[0]] if len(k) else np.nan
        k = np.where(x.M == m)[0]; return x.C[k[0]] if len(k) else np.nan

    def open930(self, d): return self.days[pd.Timestamp(d)].O[0]


def third_fridays():
    import daily_review as dr
    out = {}
    for y in range(2010, 2027):
        closed = {pd.Timestamp(d) for d in dr.full_closures(y)}
        for m in range(1, 13):
            d = dt.date(y, m, 1); d += dt.timedelta(days=(4 - d.weekday()) % 7); d += dt.timedelta(days=14); d = pd.Timestamp(d)
            out[d - pd.Timedelta(days=1) if d in closed else d] = m in (3, 6, 9, 12)
    return out                                                  # date -> quarterly?


# ------------------------------------------------------------------------------------------------ gates
def judge(name, ev, ctl, q_ev, na, mkt, cal, tbar, p_bar, hold, eras, lines):
    """ev / q_ev / ctl: DataFrames with date, gross, sides (q_ev = second instrument). Gates as MECH_PLAN."""
    import regime_test as rt, src2_test as T
    def daily(e, ticks=1):
        s = pd.Series(0.0, index=cal)
        if len(e):
            g = (e.gross - e.sides * (C1 if ticks == 1 else C3)).groupby(e.date).sum(); s.loc[g.index.intersection(cal)] += g.reindex(g.index.intersection(cal))
        return s
    net = daily(ev); net3 = daily(ev, 3); nq = daily(q_ev) if len(q_ev) else pd.Series(0.0, index=cal)
    e_net = (ev.gross - ev.sides * C1).values; c_net = (ctl.gross - ctl.sides * C1).values if len(ctl) else np.array([0.0])
    t_full = tstat(net); h = net[(net.index >= hold[0]) & (net.index <= hold[1])]
    best5 = net.sort_values().index[-5:]; t_d5 = tstat(net.drop(best5)); t3 = tstat(net3)
    em = {k: net[(net.index >= a) & (net.index <= b)][lambda s: s != 0].mean() if (net[(net.index >= a) & (net.index <= b)] != 0).any() else 0.0
          for k, (a, b) in eras.items()}
    ta, beta = T.alpha_t(net, mkt.reindex(net.index).fillna(0.0))
    welch = (e_net.mean() - c_net.mean()) / np.sqrt(e_net.var(ddof=1) / len(e_net) + c_net.var(ddof=1) / max(len(c_net), 2))
    common = na.index.intersection(net.index); a, b = na.reindex(common).values, net.reindex(common).values
    p_add = rt.boot_sharpe_diff(common, a + b, a); dd_na, dd_c = maxdd(a * USD), maxdd((a + b) * USD)
    G = {f'G1 t >= {tbar}': t_full >= tbar, 'G2 holdout': (h.mean() > 0 and (tstat(h) >= 1.0 if name.startswith('T1') else True)),
         'G3 second instrument': nq.mean() > 0, 'G4 drop-5 t >= 2.0': t_d5 >= 2.0, 'G5 3-tick t >= 2.0': t3 >= 2.0,
         'G6 eras > 0': all(v > 0 for v in em.values()), 'G7 Welch >= 2.0 & alpha >= 2.0': welch >= 2.0 and ta >= 2.0,
         f'G8 ADDITION P < {p_bar} & DD <= 1.10x': p_add < p_bar and dd_c <= 1.10 * dd_na}
    yr = net.groupby(net.index.year).sum()
    pr(f"\n{name}: {len(ev)} events | net {e_net.mean() * 1e4:+.2f} bp (${e_net.mean() * USD:+,.0f}/micro) per event, event t {tstat(e_net):+.2f}, "
       f"win {np.mean(e_net > 0) * 100:.0f}% | ${net.sum() * USD / (len(net) / 252):+,.0f}/yr per micro")
    pr(f"   G1 t {t_full:+.2f} | G2 holdout {hold[0]}.. mean {h[h != 0].mean() * 1e4 if (h != 0).any() else 0:+.2f} bp/event, t {tstat(h):+.2f} | "
       f"G3 ES/MES n {len(q_ev)} {((q_ev.gross - q_ev.sides * C1).mean() * 1e4 if len(q_ev) else 0):+.2f} bp, t {tstat(nq):+.2f} | "
       f"G4 drop-5 t {t_d5:+.2f} | G5 3-tick t {t3:+.2f} | G6 " + ' '.join(f"{k} {v * 1e4:+.2f}" for k, v in em.items()) + " bp/event")
    pr(f"   G7 control n {len(ctl)} {c_net.mean() * 1e4:+.2f} bp, Welch t {welch:+.2f}, alpha t {ta:+.2f} (beta {beta:+.3f}) | "
       f"G8 NoiseArea + it Sharpe {rt.sharpe(a + b):.3f} vs {rt.sharpe(a):.3f}, P {p_add:.4f}, maxDD ${dd_c:,.0f} vs ${dd_na:,.0f}")
    for ln in lines: pr('   ' + ln)
    pr("   per year $/micro: " + ' | '.join(f"{y} {v * USD:+,.0f}" for y, v in yr.items() if v != 0))
    ok = all(G.values()); pr("   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()) + f" -> {'PASS' if ok else 'not adopted'}")
    net.rename('net').to_csv(os.path.join(HERE, f"mech_{name.split()[0].lower()}_daily.csv"))
    return ok


def frame(rows): return pd.DataFrame(rows, columns=['date', 'gross', 'sides', 'tag']) if rows else pd.DataFrame(columns=['date', 'gross', 'sides', 'tag'])


# ------------------------------------------------------------------------------------------------ rules
def t1_rows(dates, nqs, mnq, exit_at=(12, 0)):
    """Short 09:30 -> exit (or close). Returns rows; tag = (quarterly?, overnight move)."""
    rows = []
    for d, quarterly in dates.items():
        src = nqs if d <= JOIN else mnq
        if not src.has(d): continue
        o = src.open930(d); x = src.at(d, 'close') if exit_at == 'close' else src.at(d, exit_at, 'o')
        if not np.isfinite(x): continue
        px = src.px(d) if isinstance(src, NTSrc) else o
        rows.append((d, -(x - o) / px, 2, quarterly))
    return rows


def overnight(d, nqs, mnq, cal):
    src = nqs if d <= JOIN else mnq; i = cal.searchsorted(d) - 1
    if i < 0 or not src.has(d): return np.nan
    p = cal[i]
    if not src.has(p): return np.nan
    c = src.at(p, 'close'); o = src.open930(d); return (o - c) / o


def main():
    import src2_test as T
    from bulenox_routes import Pan
    na = T.noisearea(); cal = na.index; P = Pan('nq', 'mnqx'); mkt = P.market()
    nqs, es, mes, mnqx, mnq = Src('nq'), Src('es'), Src('mes'), Src('mnqx'), NTSrc()
    tf = third_fridays(); tf = {d: q for d, q in tf.items() if pd.Timestamp(L.DISC[0]) <= d <= pd.Timestamp('2026-10-14')}
    nq_ok = {d: q for d, q in tf.items() if (d <= JOIN and nqs.has(d)) or (d > JOIN and mnq.has(d))}
    pr(f"MECH (MECH_PLAN.txt). Third Fridays {len(tf)} (quarterly {sum(tf.values())}); with data: {len(nq_ok)} "
       f"(NQ era {sum(1 for d in nq_ok if d <= JOIN)}, MNQ era {sum(1 for d in nq_ok if d > JOIN)}). Cost {C1 * 1e4:.3f} bp a side.")
    verdict = {}
    # ================================================================== T1
    ev = frame(t1_rows(nq_ok, nqs, mnq)); assert 140 <= len(ev) <= 175, f"T1: {len(ev)} events"
    q = frame(t1_rows(tf, es, mes))
    fridays = [d for d in cal if d.weekday() == 4 and d not in tf and d >= pd.Timestamp(L.DISC[0])]
    ctl = frame(t1_rows({d: False for d in fridays}, nqs, mnq))
    ev16 = frame(t1_rows(nq_ok, nqs, mnq, 'close'))
    on = pd.Series({d: overnight(d, nqs, mnq, cal) for d in ev.date})
    e_net = ev.set_index('date').gross - 2 * C1
    lines = [f"cover at 16:00 instead: {(ev16.gross - 2 * C1).mean() * 1e4:+.2f} bp, t {tstat(ev16.gross - 2 * C1):+.2f}",
             f"serial months {e_net[~ev.set_index('date').tag.astype(bool)].mean() * 1e4:+.2f} bp (n {int((~ev.tag.astype(bool)).sum())}), quarterly "
             f"{e_net[ev.set_index('date').tag.astype(bool)].mean() * 1e4:+.2f} bp (n {int(ev.tag.astype(bool).sum())})",
             f"after an up night {e_net[on.reindex(e_net.index) > 0].mean() * 1e4:+.2f} bp (n {int((on > 0).sum())}), after a down night "
             f"{e_net[on.reindex(e_net.index) < 0].mean() * 1e4:+.2f} bp (n {int((on < 0).sum())}); mean night {on.mean() * 1e4:+.2f} bp",
             f"2022-2026 (after the paper's sample): {e_net[e_net.index >= '2022-01-01'].mean() * 1e4:+.2f} bp, n {int((e_net.index >= '2022-01-01').sum())}, "
             f"t {tstat(e_net[e_net.index >= '2022-01-01']):+.2f}"]
    verdict['T1'] = judge('T1 third-Friday spike short 09:30 -> 12:00', ev, ctl, q, na, mkt, cal, 2.0, 0.05, ('2022-01-01', '2026-10-16'),
                          {'NQ 2010-2019': ('2010-07-01', '2019-05-05'), 'MNQ 2019-2026': ('2019-05-06', '2026-10-16')}, lines)
    # ================================================================== S1 month-end rebalancing
    y = pd.read_csv(os.path.join(CAL, 'dgs10_fred_20261021.csv'), index_col=0, parse_dates=True).iloc[:, 0]
    y = pd.to_numeric(y, errors='coerce').ffill()
    def closes(src_a, src_b):
        s = {}
        for d in cal:
            src = src_a if d <= JOIN else src_b
            if src.has(d): s[d] = src.at(d, 'close', 'ac') if isinstance(src, Src) else src.at(d, 'close')
        return pd.Series(s).dropna()
    def s1_rows(src_a, src_b, last5=True):
        cl = closes(src_a, src_b); rows = []
        months = pd.Series(cal, index=cal).groupby([cal.year, cal.month])
        lastn = set(d for _, g in months for d in g.values[-5:])
        for d in cal:
            if (d in lastn) != last5 or d < pd.Timestamp(L.DISC[0]): continue
            src = src_a if d <= JOIN else src_b
            if not src.has(d): continue
            prev = cl[cl.index < d]
            if not len(prev): continue
            pdte = prev.index[-1]; ref = prev[prev.index < pd.Timestamp(d.year, d.month, 1)]
            if not len(ref) or (ref.index[-1] <= JOIN) != (d <= JOIN): continue
            eq = prev.iloc[-1] / ref.iloc[-1] - 1
            yb = y[y.index <= pdte]; yr0 = y[y.index <= ref.index[-1]]
            bond = -8 * (yb.iloc[-1] - yr0.iloc[-1]) / 100
            pos = -np.sign(eq - bond)
            if pos == 0: continue
            o = src.open930(d); c = src.at(d, 'close'); px = src.px(d) if isinstance(src, NTSrc) else o
            if np.isfinite(c): rows.append((d, pos * (c - o) / px, 2, pos))
        return rows
    ev = frame(s1_rows(nqs, mnq)); q = frame(s1_rows(es, mes)); ctl = frame(s1_rows(nqs, mnq, last5=False))
    verdict['S1'] = judge('S1 month-end rebalancing, last 5 sessions, 09:30 -> 16:00', ev, ctl, q, na, mkt, cal, 2.6, 0.01,
                          ('2023-01-01', '2026-10-16'), {'NQ 2010-2019': ('2010-07-01', '2019-05-05'), 'MNQ 2019-2026': ('2019-05-06', '2026-10-16')},
                          [f"long signals {int((ev.tag > 0).sum())}, short {int((ev.tag < 0).sum())}"])
    # ================================================================== S2 pinning (serial 3rd Fridays, raw prices)
    def s2_rows(src_a, src_b, grid, band, days):
        rows = []
        for d in days:
            src = src_a if d <= JOIN else src_b
            if not src.has(d) or not src.full(d): continue
            p = src.at(d, (15, 30), 'o'); c = src.at(d, 'close')
            if not np.isfinite(p) or not np.isfinite(c): continue
            K = round(p / grid) * grid
            if abs(p - K) > band or p == K: continue
            pos = np.sign(K - p); rows.append((d, pos * (c - p) / src.open930(d), 2, K))
        return rows
    serial = [d for d, qq in tf.items() if not qq]
    ev = frame(s2_rows(nqs, mnqx, 100, 25, serial)); q = frame(s2_rows(es, mes, 25, 6.25, serial))
    ctl = frame(s2_rows(nqs, mnqx, 100, 25, fridays))
    verdict['S2'] = judge('S2 expiration pinning 15:30 -> 16:00 (serial 3rd Fridays)', ev, ctl, q, na, mkt, cal, 2.6, 0.01,
                          ('2023-01-01', '2026-10-16'), {'NQ 2010-2019': ('2010-07-01', '2019-05-05'), 'MNQ 2019-2026': ('2019-05-06', '2026-10-16')}, [])
    # ================================================================== S3 round-number crosses (raw 1-minute)
    def s3_rows(src_a, src_b, grid, offset=0.0):
        rows = []
        for d in cal:
            if d < pd.Timestamp(L.DISC[0]): continue
            src = src_a if d <= JOIN else src_b
            if not src.has(d): continue
            M = src.M; i0, i1 = M.days[d]; o930 = M.o[i0]; c = M.c; o = M.o; m = M.m
            g = 0.0; n = 0; i = i0 + 5
            while i < i1:
                if m[i] > 930: break
                a, b = np.floor((c[i - 1] - offset) / grid), np.floor((c[i] - offset) / grid)
                if a != b and i + 1 <= i1:
                    dr_ = np.sign(c[i] - c[i - 1]); e = i + 16
                    xp = o[e] if e <= i1 else c[i1]
                    g += dr_ * (xp - o[i + 1]) / o930; n += 1; i = min(e, i1); continue
                i += 1
            if n: rows.append((d, g, 2 * n, n))
        return rows
    ev = frame(s3_rows(nqs, mnqx, 100)); q = frame(s3_rows(es, mes, 25)); ctl = frame(s3_rows(nqs, mnqx, 100, offset=37.0))
    verdict['S3'] = judge('S3 round-number cross follow, 15 minutes', ev, ctl, q, na, mkt, cal, 2.6, 0.01,
                          ('2023-01-01', '2026-10-16'), {'NQ 2010-2019': ('2010-07-01', '2019-05-05'), 'MNQ 2019-2026': ('2019-05-06', '2026-10-16')},
                          [f"trades {int(ev.tag.sum())}, per day traded {ev.tag.mean():.1f}; control = levels shifted by 37 points"])
    # ================================================================== S4 weekend crypto
    b = pd.read_csv(os.path.join(CAL, 'btc_usd_hourly_coinbase.csv'), parse_dates=['time_utc']).set_index('time_utc')
    b.index = b.index.tz_convert('America/New_York').tz_localize(None)
    def s4_rows(src_a, src_b, neg=True):
        rows = []
        for fri in pd.date_range('2015-07-24', '2026-10-09', freq='W-FRI'):
            t_f = fri + pd.Timedelta(hours=15); t_s = fri + pd.Timedelta(days=2, hours=18)
            if t_f not in b.index or t_s not in b.index: continue
            r = b.open.get(t_s) / b.close.get(t_f) - 1
            if (r < 0) != neg: continue
            i = cal.searchsorted(fri + pd.Timedelta(days=2), side='right')
            if i >= len(cal): continue
            d = cal[i]; src = src_a if d <= JOIN else src_b
            if not src.has(d): continue
            o = src.open930(d); c = src.at(d, 'close'); px = src.px(d) if isinstance(src, NTSrc) else o
            if np.isfinite(c): rows.append((d, -(c - o) / px, 2, r))
        return rows
    ev = frame(s4_rows(nqs, mnq)); q = frame(s4_rows(es, mes)); ctl = frame(s4_rows(nqs, mnq, neg=False))
    verdict['S4'] = judge('S4 weekend crypto down -> short next session', ev, ctl, q, na, mkt, cal, 2.6, 0.01,
                          ('2023-01-01', '2026-10-16'), {'NQ 2015-2019': ('2015-07-01', '2019-05-05'), 'MNQ 2019-2026': ('2019-05-06', '2026-10-16')},
                          ["control = the same short after UP weekends"])
    # ================================================================== S5 periodicity
    def s5_rows(src_a, src_b, shift=0):
        bounds = [(9, 30)] + [(h, m) for h in range(10, 16) for m in (0, 30)]
        R = {}
        for d in cal:
            if d < pd.Timestamp(L.DISC[0]): continue
            src = src_a if d <= JOIN else src_b
            if not src.has(d) or not src.full(d): continue
            p = [src.at(d, hm, 'o') for hm in bounds] + [src.at(d, 'close')]
            if not np.all(np.isfinite(p)): continue
            R[d] = (np.diff(p) / p[0], d <= JOIN)
        ds = sorted(R); rows = []
        for k in range(5, len(ds)):
            d = ds[k]; prev = [ds[k - l] for l in range(1, 6)]
            if any(R[x][1] != R[d][1] for x in prev): continue
            mu = np.mean([R[x][0] for x in prev], axis=0); pos = np.sign(np.roll(mu, shift))
            sides = abs(pos[0]) + np.abs(np.diff(pos)).sum() + abs(pos[-1])
            rows.append((d, float((pos * R[d][0]).sum()), float(sides), 0))
        return rows
    ev = frame(s5_rows(nqs, mnqx)); q = frame(s5_rows(es, mes)); ctl = frame(s5_rows(nqs, mnqx, shift=1))
    verdict['S5'] = judge('S5 same-slot periodicity (5-day lookback)', ev, ctl, q, na, mkt, cal, 2.6, 0.01,
                          ('2023-01-01', '2026-10-16'), {'NQ 2010-2019': ('2010-07-01', '2019-05-05'), 'MNQ 2019-2026': ('2019-05-06', '2026-10-16')},
                          [f"sides per day {ev.sides.mean():.1f}; control = signal taken from the neighbouring slot"])
    pr("\nSUMMARY: " + ', '.join(f"{k} {'PASS' if v else 'not adopted'}" for k, v in verdict.items()))
    with open(os.path.join(HERE, 'mech_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
