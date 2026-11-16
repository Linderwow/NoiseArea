import os, sys, itertools
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L

LIVE = 2.35
P = None


def init():
    global P
    import src2_test as T
    T.load_batches(); T.P = L.Panel(only=None); P = T.P


def cost_pts(ticks): return ticks * 0.25 + 0.25


def bars_pd(mk, n):
    t = pd.DatetimeIndex(mk.t.astype('datetime64[m]'))
    df = pd.DataFrame({'o': mk.ao, 'h': mk.ah, 'l': mk.al, 'c': mk.ac, 'iid': mk.iid, 'i': np.arange(len(t))}, index=t)
    g = df.groupby([df.index.floor(f'{n}min'), df.iid])
    b = g.agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), first=('i', 'min'), last=('i', 'max'))
    b = b.reset_index().rename(columns={'level_0': 'start'}).sort_values('first').reset_index(drop=True)
    return b


def osc_indep(b, ema_n=21):
    c, h, l = b.c.values, b.h.values, b.l.values
    ema = np.empty(len(c)); a = 2 / (ema_n + 1); ema[0] = c[0]
    for i in range(1, len(c)): ema[i] = a * c[i] + (1 - a) * ema[i - 1]
    atr = np.empty(len(c)); atr[0] = h[0] - l[0]
    for i in range(1, len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])); atr[i] = atr[i - 1] + (tr - atr[i - 1]) / 14
    raw = (c - ema) / (3 * atr) * 100; o3 = np.empty(len(c)); a3 = 0.5; o3[0] = raw[0]
    for i in range(1, len(c)): o3[i] = a3 * raw[i] + (1 - a3) * o3[i - 1]
    return o3


def trades_market(mk, dates, bar=10, thr=100.0, win_end=660, ema_n=21, indep=True):
    key = (mk.name, bar, ema_n)
    if key not in _OSC:
        b = bars_pd(mk, bar); _OSC[key] = (b, osc_indep(b, ema_n))
    b, osc = _OSC[key]; first = b['first'].values; smin = (b.start.dt.hour * 60 + b.start.dt.minute).values
    out = []
    for d in dates:
        i0, i1 = mk.days[d]; a = np.searchsorted(first, i0); z = np.searchsorted(first, i1, side='right') - 1
        if a < 30 or z - a < 5 or first[a] != i0: continue
        pos = 0; e = 0.0; em = 0
        for q in range(a, z):
            x0, x1 = osc[q - 1], osc[q]; win = smin[q] + bar <= win_end
            up = x0 <= thr < x1; dn = x0 >= -thr > x1; new = pos
            if pos == 1 and dn: new = -1 if win else 0
            elif pos == -1 and up: new = 1 if win else 0
            elif pos == 0 and win: new = 1 if up else (-1 if dn else 0)
            if new != pos:
                px = mk.o[first[q + 1]]; mm = int(mk.m[first[q + 1]])
                if pos: out.append((d, pos, e, px, em, mm, mk.o[i0]))
                if new: e = px; em = mm
                pos = new
        if pos: out.append((d, pos, e, mk.c[i1], em, 960, mk.o[i0]))
    return pd.DataFrame(out, columns=['date', 'dr', 'e', 'x', 'em', 'xm', 'o930'])


_OSC = {}


def panel_trades(**kw):
    parts = []
    for mk in (P.nq, P.mnq):
        ds = [d for d, m in P.sess if m is mk]; parts.append(trades_market(mk, ds, **kw))
    return pd.concat(parts, ignore_index=True)


def daily(tr, ticks, index):
    r = (tr.dr * (tr.x - tr.e) - 2 * cost_pts(ticks)) / tr.o930
    s = r.groupby(tr.date).sum(); return s.reindex(index, fill_value=0.0)


def tstat(x): x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if x.std() > 0 else 0.0


def grid_cell(args):
    if P is None: init()
    thr, we, en, bar = args; tr = panel_trades(bar=bar, thr=thr, win_end=we, ema_n=en)
    return args, tstat(daily(tr, 1, P.dates).values), len(tr)


def main():
    init()
    import src2_test as T
    out = ["PHASEVAL (PHASEVAL_PLAN.txt) - E03 Phase Oscillator validation; $ per micro at today's price; NQ 2010-19 + MNQ 2019-26"]
    g, n = T.run('E03'); ref = g - n * L.side_cost(1)
    tr = panel_trades(); ind = daily(tr, 1, P.dates)
    gross_ind = (tr.dr * (tr.x - tr.e) / tr.o930).groupby(tr.date).sum().reindex(P.dates, fill_value=0.0)
    days_ref = set(P.dates[(g != 0).values]); days_ind = set(tr.date)
    diff = (gross_ind - g).abs(); bad = int((diff > 1e-9).sum())
    out.append(f"K1 independent re-implementation: {len(tr)} trades on {len(days_ind)} days vs harness {int(n.sum() / 2)} trades on {len(days_ref)} days;"
               f" days differing in P&L {bad} ({100 * bad / max(len(days_ref | days_ind), 1):.2f}%); totals $ {ind.sum() * L.N_USD:,.0f} vs {ref.sum() * L.N_USD:,.0f}")
    out.append("   look-ahead audit: signal = oscillator of the CLOSED 10-min bar q vs bar q-1; orders at the open of bar q+1; exits at the next"
               " bar's open or the 15:59 close; EMA/ATR use only bars up to q (recursive)")
    live = daily(tr, LIVE, P.dates); sc = L.scorecard(live)
    h = live[live.index >= '2023-01-01']; yr = live.groupby(live.index.year).sum() * L.N_USD
    out.append(f"K3 live cost {LIVE} ticks: ${sc['usd_yr']:+,.0f}/yr t {sc['t']:+.2f} (NQ {sc['t_disc']:+.2f}, MNQ {sc['t_mnq']:+.2f}), maxDD ${sc['maxdd']:,.0f}")
    out.append(f"K2 2023-01..2026-10 alone: ${h.sum() * L.N_USD / (len(h) / 252):+,.0f}/yr t {tstat(h.values):+.2f}")
    out.append(f"K6 years positive {int((yr > 0).sum())}/{len(yr)}: " + ", ".join(f"{y} {v:+,.0f}" for y, v in yr.items()))
    usd = (tr.dr * (tr.x - tr.e) - 2 * cost_pts(LIVE)) * 2.0 * (L.P_REF / tr.o930)
    out.append(f"   trades {len(tr)} ({len(tr) / 16.3:.0f}/yr), win {100 * (usd > 0).mean():.1f}%, avg ${usd.mean():+.1f}, PF {usd[usd > 0].sum() / -usd[usd < 0].sum():.2f},"
               f" median hold {int((tr.xm - tr.em).median())} min, entries by hour {tr.em.floordiv(60).value_counts().sort_index().to_dict()}")
    for lab, sel in (('long only', tr.dr > 0), ('short only', tr.dr < 0)):
        s = daily(tr[sel], LIVE, P.dates); out.append(f"K5 NQ+MNQ {lab}: ${s.sum() * L.N_USD / (len(s) / 252):+,.0f}/yr t {tstat(s.values):+.2f}, trades {int(sel.sum())}")
    for nm in ('es', 'mes'):
        mk = L.Mins(nm); t2 = trades_market(mk, mk.dates)
        s = ((t2.dr * (t2.x - t2.e) - 2 * cost_pts(LIVE)) / t2.o930).groupby(t2.date).sum().reindex(pd.DatetimeIndex(mk.dates), fill_value=0.0)
        out.append(f"K5 {nm.upper()} {mk.dates[0].date()}..{mk.dates[-1].date()}: t {tstat(s.values):+.2f}, trades {len(t2)}, years + "
                   f"{int((s.groupby(s.index.year).sum() > 0).sum())}/{s.index.year.nunique()}")
    from concurrent.futures import ProcessPoolExecutor
    cells = list(itertools.product((80.0, 100.0, 120.0), (630, 660, 690), (15, 21, 30), (5, 10, 15)))
    with ProcessPoolExecutor(4) as ex: res = list(ex.map(grid_cell, cells))
    good = sum(t > 2 for _, t, _ in res)
    out.append(f"K4 neighbourhood: {good}/{len(res)} cells with t > 2 at 1 tick ({100 * good / len(res):.0f}%); t range {min(t for _, t, _ in res):+.2f} .. {max(t for _, t, _ in res):+.2f}")
    for bar in (5, 10, 15):
        row = [f"{t:+.1f}" for (a, t, _) in res if a[3] == bar]
        out.append(f"   bar {bar:2d} min, t by (thr x window x EMA): " + " ".join(row))
    import nav_lab as N, regime_test as rt
    na, _ = N.series('MNQ', ticks=LIVE); e = live.reindex(na.index, fill_value=0.0)
    from trendmix import stats
    s0, s1 = stats(na), stats(na + e)
    out.append(f"MERGE additive 2nd micro at {LIVE} ticks: NoiseArea ${s0['usd']:+,.0f}/yr Sharpe {s0['sh']:.2f} DD ${s0['dd']:,.0f} 2023-26 ${s0['usd_h']:+,.0f}"
               f" | + E03 ${s1['usd']:+,.0f}/yr Sharpe {s1['sh']:.2f} DD ${s1['dd']:,.0f} 2023-26 ${s1['usd_h']:+,.0f} | corr {np.corrcoef(na.values, e.values)[0, 1]:+.2f}"
               f" P {rt.boot_sharpe_diff(na.index, (na + e).values, na.values):.4f}")
    txt = "\n".join(out); print(txt); open(os.path.join(HERE, 'phaseval_output.txt'), 'w').write(txt + "\n")


if __name__ == '__main__':
    main()
