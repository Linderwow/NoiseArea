r"""LIT2026 (LIT2026_PLAN.txt, pre-registered 2026-10-24): the two new ideas of the 2024-2026 literature scan as NoiseArea filters
(L1 month-end rebalancing side filter, L2 implied-move brake, L3 L2 + take profit) and the big-move-day decay check.
    python lit2026.py   -> lit2026_output.txt
Simulator: metalabel.run (== nav_lab.fam_V with no filter, audited here again) on the extended 16-year joined series.
"""
import os, sys, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import history_extend as HX
import navar as NV
import metalabel as ML

P_BAR = 0.05 / 3; USD = ML.USD
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def cboe(f):
    s = pd.read_csv(os.path.join(HERE, 'event_calendars', f))
    return pd.Series(s.CLOSE.values.astype(float), index=pd.to_datetime(s.DATE, format='%m/%d/%Y')).sort_index()


def prior(series, dates):
    """The value at the last observation before each date."""
    return np.array([series.asof(d - pd.Timedelta(days=1)) for d in dates], float)


def dgs10():
    s = pd.read_csv(os.path.join(HERE, 'event_calendars', 'dgs10_fred_20261021.csv'))
    v = pd.to_numeric(s.DGS10, errors='coerce'); return pd.Series(v.values, index=pd.to_datetime(s.observation_date)).dropna().sort_index()


def month_end_sign(mk, y10):
    """Per session: the sign of NQ's minus the bond's return from the previous month's last close to the prior close, on the last 5
    CME sessions of each month (0 elsewhere). Bond = -8 x the 10-year yield change (MECH S1)."""
    d = mk.dates; close = np.array([x.C[-1] for x in mk.days]); sgn = np.zeros(mk.n); last5 = {}
    for y, m in sorted(set(zip(d.year, d.month))):
        a = dt.date(y, m, 1); b = (pd.Timestamp(a) + pd.offsets.MonthEnd(0)).date()
        last5[(y, m)] = set(sorted(HX.cme_sessions(a, b))[-5:])
    mon = d.year * 12 + d.month
    for k, t in enumerate(d):
        if t.date() not in last5[(t.year, t.month)]: continue
        prev = np.flatnonzero(mon < t.year * 12 + t.month)
        if not len(prev) or np.isnan(mk.pc[k]): continue
        j = prev[-1]
        nq = mk.pc[k] / close[j] - 1
        bond = -8 * (y10.asof(t - pd.Timedelta(days=1)) - y10.asof(d[j])) / 100
        sgn[k] = np.sign(nq - bond)
    return sgn


def bar_index(x, minute): return int(np.searchsorted(x.M, minute))


def main():
    vxn = cboe('vxn_history_cboe_20261024.csv'); y10 = dgs10()
    mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    pr(f"LIT2026 (LIT2026_PLAN.txt): NQ {mks['NQ'].dates[0].date()} .. {N.JOIN.date()} + MNQ to {mks['MNQX'].dates[-1].date()} (extended); "
       f"1 tick + $0.50 a side; $ per micro at MNQ 29,150; VXN {vxn.index[0].date()} .. {vxn.index[-1].date()}")
    base, ctxs, aux = {}, {}, {}
    for nm, _ in ML.PARTS:
        mk = mks[nm]; p0, n0 = N.fam_V(mk); ctx = ML.Ctx(mk, p0); p1, n1, _ = ML.run(mk, ctx)
        assert np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and np.array_equal(n0, n1), f"audit failed on {nm}"
        base[nm] = (mk, p1, n1); ctxs[nm] = ctx
        v = prior(vxn, mk.dates)
        aux[nm] = dict(sgn=month_end_sign(mk, y10), m=v / 1600, on=(v < 20) | (v >= 30))
    pr("audit: metalabel.run with no filter reproduces nav_lab.fam_V exactly on both markets")
    for nm, _ in ML.PARTS:
        a = aux[nm]; pr(f"   {nm}: month-end window sessions {int((a['sgn'] != 0).sum())} (stocks ahead {int((a['sgn'] > 0).sum())}), "
                        f"implied-move regime on (VXN < 20 or >= 30) {np.nanmean(a['on']) * 100:.0f}% of sessions")

    def l1(nm):
        mk, a = mks[nm], aux[nm]
        return dict(gate=lambda k, f: not (a['sgn'][k] != 0 and np.sign(f[0]) == a['sgn'][k]))

    def l2(nm, tp=False):
        mk, a = mks[nm], aux[nm]
        def beyond(k, c, d): pc = mk.pc[k]; return (d > 0 and c > pc * (1 + a['m'][k])) or (d < 0 and c < pc * (1 - a['m'][k]))
        g = lambda k, f: not (a['on'][k] and beyond(k, mk.days[k].C[bar_index(mk.days[k], f[1])], f[0]))
        e = (lambda k, i, s: bool(a['on'][k]) and beyond(k, mk.days[k].C[i], s)) if tp else None
        return dict(gate=g, exit_fn=e)

    VARS = {'L1 month-end flow side filter': l1, 'L2 implied-move brake': l2, 'L3 brake + take profit': lambda nm: l2(nm, True)}
    rb, rb2 = ML.joined(base), ML.joined(base, 2)
    def summ(r):
        eq = np.cumsum(r.values * USD); return r.mean() * 252 * USD, float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    a_, d_ = summ(rb)
    pr(f"\nlive rules {rb.index[0].date()} .. {rb.index[-1].date()}: ${a_:,.0f}/yr, max drawdown ${d_:,.0f}, MAR {NV.mar(rb):.3f}")
    pr(" variant                          $/yr    max DD   MAR    days changed  G1 P(boot)   G2 NQ / MNQ MAR (live)          G3 2 ticks (live)   G4 drop-5   2010-17 MAR (live)   verdict")
    pre = rb.index.year <= 2017
    for name, mkvar in VARS.items():
        res = {}
        for nm, _ in ML.PARTS:
            kw = mkvar(nm); p, n, _ = ML.run(mks[nm], ctxs[nm], kw['gate'], kw.get('exit_fn')); res[nm] = (mks[nm], p, n)
        rv, rv2 = ML.joined(res), ML.joined(res, 2)
        a, d = summ(rv); P = NV.boot_mar(rv, rb); changed = int((np.abs(rv - rb) > 1e-12).sum())
        seg = [(NV.mar(rv[sl]), NV.mar(rb[sl])) for sl in (rv.index <= N.JOIN, rv.index > N.JOIN)]
        drop5 = NV.mar(rv.drop(rv.nlargest(5).index))
        g1 = NV.mar(rv) > NV.mar(rb) and P < P_BAR; g2 = all(v > b for v, b in seg); g3 = NV.mar(rv2) > NV.mar(rb2); g4 = drop5 > NV.mar(rb)
        pr(f" {name:<30} {a:>8,.0f}  {d:>7,.0f}  {NV.mar(rv):5.3f}   {changed:5d}       {P:6.4f} {'ok' if g1 else 'no'}   "
           f"{seg[0][0]:.3f} ({seg[0][1]:.3f}) / {seg[1][0]:.3f} ({seg[1][1]:.3f}) {'ok' if g2 else 'no'}   {NV.mar(rv2):.3f} ({NV.mar(rb2):.3f}) {'ok' if g3 else 'no'}   "
           f"{drop5:.3f} {'ok' if g4 else 'no'}   {NV.mar(rv[pre]):.3f} ({NV.mar(rb[pre]):.3f})        {'FORWARD-TEST CANDIDATE' if g1 and g2 and g3 and g4 else 'rejected'}")
    # monitoring: NoiseArea on sessions that moved more than one daily sigma (Kurth, Eisler, Rej & Bouchaud 2026)
    big = []
    for nm, keep in ML.PARTS:
        mk, ctx = mks[nm], ctxs[nm]; sel = (np.arange(mk.n) >= ML.WARMUP) & keep(mk.dates)
        mv = np.array([abs(x.C[-1] / x.O[0] - 1) for x in mk.days]) > ctx.sgd
        big.append(pd.Series(mv[sel], index=mk.dates[sel]))
    big = pd.concat(big).reindex(rb.index).fillna(False).astype(bool)
    yr = pd.DataFrame({'big': (rb * USD)[big].groupby(rb.index[big].year).sum(), 'other': (rb * USD)[~big].groupby(rb.index[~big].year).sum(),
                       'n_big': big.groupby(rb.index.year).sum(), 'n': big.groupby(rb.index.year).size()}).fillna(0)
    pr("\nMONITOR - NoiseArea per year on sessions moving more than one daily sigma (big) and on the rest, $ per micro:")
    pr("   year   big days   $ on big days   $ on the rest   $ per big day")
    for y, r in yr.iterrows(): pr(f"   {y}   {int(r.n_big):4d}/{int(r.n):3d}   {r.big:>12,.0f}   {r.other:>12,.0f}   {r.big / max(r.n_big, 1):>10,.0f}")
    for a, b in ((2010, 2025), (2019, 2025)):
        z = yr.loc[a:b]; per = z.big / z.n_big.clip(lower=1); x = z.index.values.astype(float)
        bta = np.polyfit(x, per.values, 1)[0]; resid = per.values - np.polyval(np.polyfit(x, per.values, 1), x)
        se = np.sqrt(resid.var(ddof=2) / ((x - x.mean()) ** 2).sum())
        pr(f"   trend of $ per big day {a}-{b}: {bta:+,.1f} $/yr (t {bta / se:+.2f}); mean ${per.mean():,.0f} per big day")
    with open(os.path.join(HERE, 'lit2026_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
