r"""Audit and post-hoc diagnostics for earlyfire.py (EARLYFIRE_PLAN.txt). Report-only: nothing here changes the pre-registered verdict.
    python earlyfire_audit.py      (after earlyfire.py)  -> earlyfire_audit_output.txt
D1 spread profile from NinjaTrader's recorded quotes around 10:00 vs a calm control second (11:15), NT tick year.
D2 symmetric quote comparison: baseline AND early fills both from NinjaTrader's recorded quotes, vs R_nt, on the NT-year real 10:00
   entries (the plan's A1-2 used quotes for the early fill only). NT time = feed time (lags exchange time in bursts) - indicative.
D3 Part C sensitivity: early price mapped with each Databento contract's MODAL NT-minus-Databento offset instead of the per-day shift.
D4 hand checks: the 2026-02-02 flip P&L from raw ticks; the 2021-09-28 early 1 s fill from raw trades.
"""
import os, sys, glob
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd, pyarrow.parquet as pq
from earlyfire import read_nt, et_utc, nt_files, TICK, MS, S_NS, boot, DELTAS
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))

E = pd.read_csv(os.path.join(HERE, 'earlyfire_days.csv'), parse_dates=['date'])
B = pd.read_csv(os.path.join(HERE, 'earlyfire_ntyear.csv')); U = B[B.usable]
by = nt_files()

# D1 ------------------------------------------------------------------------------------------------------------------
win = {'09:59:00-09:59:10': ('09:59:00', '09:59:10'), '09:59:50-09:59:58': ('09:59:50', '09:59:58'), '09:59:58-10:00:00': ('09:59:58', '10:00:00'),
       '10:00:00-10:00:02': ('10:00:00', '10:00:02'), '10:00:10-10:00:20': ('10:00:10', '10:00:20'), '11:15:00-11:15:10 (control)': ('11:15:00', '11:15:10')}
acc = {k: [] for k in win}; qrows = []
for r in U.itertuples():
    f = dict(by[r.date])[r.con]
    ts, t = read_nt(f, et_utc(r.date, '09:59:00'), et_utc(r.date, '11:15:10'))
    sp = (t.ask.values - t.bid.values) / TICK
    for k, (a, b) in win.items():
        m = (ts >= et_utc(r.date, a).value) & (ts < et_utc(r.date, b).value); acc[k].append(sp[m])
    if r.pos_after != 0:                                                   # D2 material
        s = r.pos_after; Tn = et_utc(r.date, '10:00:00').value; px = t.price.values
        k0 = int(np.searchsorted(ts, Tn, 'left')); R = px[k0]; t0 = ts[k0]
        q = lambda t_: int(np.searchsorted(ts, t_, 'right')) - 1
        fill = lambda j: t.ask.values[j] if s > 0 else t.bid.values[j]
        o = dict(date=r.date, dir=s, R_nt=R, base_q=(fill(q(t0 + 75 * MS)) - R) * s / TICK)
        # same touch model as earlyfire, on NT prints (NT 'A' = traded at the ask = buyer)
        side = np.where(t.price.values == t.ask.values, 1, np.where(t.price.values == t.bid.values, -1, 0))
        def touch_fill(t_):
            j = q(t_)
            while j >= 0 and side[j] == 0: j -= 1
            p = px[j]; ask = p if side[j] > 0 else p + TICK; bid = p - TICK if side[j] > 0 else p
            return ask if s > 0 else bid
        o['base_touch'] = (touch_fill(t0 + 75 * MS) - R) * s / TICK
        for d in DELTAS:
            ta = Tn - d * MS + 75 * MS
            o[f'early_q_{d}'] = (fill(q(ta)) - R) * s / TICK; o[f'early_touch_{d}'] = (touch_fill(ta) - R) * s / TICK
        qrows.append(o)
pr("D1 SPREAD PROFILE (NinjaTrader recorded bid/ask at each print, all usable NT-year days; ticks):")
for k, v in acc.items():
    x = np.concatenate(v); pr(f"   {k:28s} prints {len(x):8,d} | 1 tick {np.mean(np.abs(x - 1) < 1e-9) * 100:5.1f}% | <=2 {np.mean(x <= 2 + 1e-9) * 100:5.1f}% | "
                              f"mean {x.mean():5.2f} | median {np.median(x):4.1f} | p90 {np.percentile(x, 90):5.1f}")

# D1b: independent lower bound from Databento trades - price gap between consecutive trades of opposite aggressor side
tp = pd.concat([pd.read_parquet(x) for x in sorted(glob.glob(r'D:\databento_archive\mnq_trades_1000_windows_2019-2026\chunk_*.parquet'))])
gaps = {'09:59:00-09:59:58': [], '09:59:58-10:00:00': []}; gy = {}
for wi, g in tp.groupby('win'):
    g = g.sort_values('ts', kind='mergesort'); day = str(g.start_et.iloc[0].date())
    Tn = et_utc(day, '10:00:00').tz_convert(None); ms = ((g.ts - Tn).dt.total_seconds() * 1000).values
    sd = g.side.map({'B': 1, 'A': -1}).fillna(0).values; px = g.price.values
    flip = (sd[1:] * sd[:-1] == -1); gap = np.abs(np.diff(px)) / TICK
    for k, (a, b) in {'09:59:00-09:59:58': (-60000, -2000), '09:59:58-10:00:00': (-2000, 0)}.items():
        m = flip & (ms[1:] >= a) & (ms[1:] < b); gaps[k].append(gap[m])
        if k.startswith('09:59:00'): gy.setdefault(day[:4], []).append(gap[m])
pr("   Databento check (all 491 entry days): |price change| at a change of aggressor side (a lower bound on the spread), ticks:")
for k, v in gaps.items():
    x = np.concatenate(v); pr(f"      {k}: n {len(x):,} | 1 tick {np.mean(x == 1) * 100:.1f}% | <=2 {np.mean(x <= 2) * 100:.1f}% | median {np.median(x):.1f} | mean {x.mean():.2f}")
pr("      09:59:00-09:59:58 median / mean by year: " + ' | '.join(f"{y} {np.median(np.concatenate(v)):.0f} / {np.concatenate(v).mean():.2f}" for y, v in sorted(gy.items())))

# D2 ------------------------------------------------------------------------------------------------------------------
Q = pd.DataFrame(qrows)
pr(f"\nD2 SYMMETRIC QUOTE COMPARISON on the {len(Q)} NT-year real 10:00 entries (NT time; ticks/side vs R_nt, + = worse):")
pr(f"   baseline (first NT print at/after T + 75 ms): quotes {Q.base_q.mean():+.2f} | touch model on NT prints {Q.base_touch.mean():+.2f}")
for d in DELTAS:
    sq = boot(Q.base_q - Q[f'early_q_{d}']); st = boot(Q.base_touch - Q[f'early_touch_{d}'])
    pr(f"   early {d:4d} ms: quotes {Q[f'early_q_{d}'].mean():+.2f} | touch model {Q[f'early_touch_{d}'].mean():+.2f} || SAVING with quotes on both sides "
       f"{sq[0]:+.2f} [{sq[1]:+.2f}, {sq[2]:+.2f}] vs touch model on both sides {st[0]:+.2f} [{st[1]:+.2f}, {st[2]:+.2f}]")
Q.to_csv(os.path.join(HERE, 'earlyfire_audit_quotes.csv'), index=False)

# D3 ------------------------------------------------------------------------------------------------------------------
E['nt_minus_db'] = E['shift']
modal = E.groupby('iid').nt_minus_db.agg(lambda x: x.round(2).mode().iloc[0])
E['moff'] = E.iid.map(modal); E['off_dev_ticks'] = (E.nt_minus_db - E.moff) / TICK
band = E.C + E['shift'] - E.margin_ticks * TICK * E.dir           # the crossed band on the NT scale (C_nt - margin x s)
pr(f"\nD3 PART C SENSITIVITY: NT 10:00 close minus Databento last trade before T equals its contract's modal offset on "
   f"{(E.off_dev_ticks.abs() < 1e-9).mean() * 100:.1f}% of the 491 days (within 1 tick {(E.off_dev_ticks.abs() <= 1 + 1e-9).mean() * 100:.1f}%)")
from earlyfire import engine_day, PV
from research_engine import Market
mk = Market(os.path.join(HERE, 'nt_analyzer_bars.csv'), PV, 'NT'); sig = mk.sigma(14); kix = {pd.Timestamp(x.date): k for k, x in enumerate(mk.days)}
yrs = (E.date.max() - E.date.min()).days / 365.25
for d in DELTAS:
    cm = E[f'Cp_{d}'] + E.moff; agree = ((cm - band) * E.dir > 0)
    cons = []
    for r in E[~agree].itertuples():
        k = kix[r.date]; _, _, inf = engine_day(mk, k, sig); c10, ub, lb, _ = inf
        e = 1 if cm[r.Index] > ub else (-1 if cm[r.Index] < lb else 0)
        cons.append(engine_day(mk, k, sig, force10=e)[0] - engine_day(mk, k, sig)[0])
    cons = np.array(cons)
    pr(f"   delta {d:4d} ms: missed with the modal-offset mapping {int((~agree).sum())} of 491 ({(~agree).mean() * 100:.2f}%) vs the registered "
       f"per-day mapping {int((E[f'c_early_{d}'] != E.dir).sum())}; days: {sorted(E[~agree].date.dt.strftime('%Y-%m-%d'))}; P&L consequence sum "
       f"${cons.sum():+,.0f} (${cons.sum() / yrs:+,.0f}/yr, harm-only ${cons[cons < 0].sum() / yrs:+,.0f}/yr)")

# D4 ------------------------------------------------------------------------------------------------------------------
pr("\nD4 HAND CHECKS")
d = '2026-02-02'; f = dict(by[d])['03-26']
ts, t = read_nt(f, et_utc(d, '09:59:00'), et_utc(d, '16:00:01'), cols=('time', 'price', 'sub_ms', 'artifact')); px = t.price.values
o10 = px[np.searchsorted(ts, et_utc(d, '10:00:00').value)]; o1030 = px[np.searchsorted(ts, et_utc(d, '10:30:00').value)]
cl = px[np.searchsorted(ts, et_utc(d, '16:00:00').value) - 1]
pr(f"   {d}: 10:00 bar open {o10}, 10:30 bar open {o1030}, 16:00 close {cl}; real long from 10:00: ({cl} - 0.25 - ({o10} + 0.25)) x 2 - 1 = "
   f"{((cl - TICK) - (o10 + TICK)) * 2 - 1:+.2f}; early long from 10:30: {((cl - TICK) - (o1030 + TICK)) * 2 - 1:+.2f}")
w = E[E.date == '2021-09-28'].iloc[0]
g = tp[tp.win == w.win].sort_values('ts', kind='mergesort'); Tn = et_utc('2021-09-28', '10:00:00').tz_convert(None)
g['ms'] = (g.ts - Tn).dt.total_seconds() * 1000
pr("   2021-09-28 trades from -2500 to -900 ms: " + str([(round(a, 1), b, c) for a, b, c in g[(g.ms > -2500) & (g.ms < -900)][['ms', 'price', 'side']].itertuples(index=False)]))
with open(os.path.join(HERE, 'earlyfire_audit_output.txt'), 'w', encoding='utf-8') as fh: fh.write('\n'.join(out) + '\n')
