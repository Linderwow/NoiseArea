r"""METALABEL (METALABEL_PLAN.txt, pre-registered 2026-10-24): a trained filter on NoiseArea's own entries, walk-forward.
    python metalabel.py   -> metalabel_output.txt
The live rules (nav_lab.fam_V, no switches) bar by bar; at every entry or flip a model trained on earlier entries (regime_ml's numpy
Logit / Stumps, refit every January) may skip it (a skipped flip goes flat). Out of sample 2016-01 .. the newest recorded session.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import history_extend as HX                     # registers nav_lab.FILES['MNQX'] (the MNQ bars extended with the recorded sessions)
import navar as NV
from regime_ml import Logit, Stumps, auc
from research_engine import WARMUP, NS

LOOK = 14; CHECKS = set(range(600, 931, 30)); RT_PTS = 1.0         # 1 tick + $0.50 a side = 0.5 point a side
OOS_FROM = 2016; QS = (0.25, 0.50); MODELS = ('logit', 'stumps'); P_BAR = 0.05 / 4
USD = 58300.0                                                         # $ per micro at MNQ 29,150 (as SIZES)
FEATS = ['dir', 'tod', 'strength', 'volreg', 'gap', 'move', 'vwapd', 'flip', 'n_today', 'prev', 'relvol', 'vix', 'vts', 'dow', 'own10']
PARTS = (('NQ', lambda d: d <= N.JOIN), ('MNQX', lambda d: d > N.JOIN))
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def vix_prior(dates):
    """VIX and VIX / VIX3M at the last close before each date (CBOE files in event_calendars\\)."""
    def rd(f):
        s = pd.read_csv(os.path.join(HERE, 'event_calendars', f)); return pd.Series(s.CLOSE.values, index=pd.to_datetime(s.DATE, format='%m/%d/%Y')).sort_index()
    v, v3 = rd('vix_history_cboe_latest.csv'), rd('vix3m_history_cboe_latest.csv')
    prior = [d - pd.Timedelta(days=1) for d in dates]
    a = np.array([v.asof(t) for t in prior], float); b = np.array([v3.asof(t) for t in prior], float)
    return a, a / b


class Ctx:
    """Per-market arrays the features need, all known before the session (or at the decision bar)."""
    def __init__(self, mk, base_pnl):
        self.sig = mk.sigma(LOOK)
        self.sig_med = pd.DataFrame(self.sig).rolling(250, min_periods=60).median().shift(1).values
        cv = np.full((mk.n, NS), np.nan)
        for k, x in enumerate(mk.days): cv[k, x.slot] = np.cumsum(x.V)
        self.cv_avg = pd.DataFrame(cv).rolling(14, min_periods=7).mean().shift(1).values
        with np.errstate(all='ignore'):
            self.sgd = np.array([np.nanmax(r) if np.isfinite(r).any() else np.nan for r in self.sig])   # the day's full-session sigma
        self.own = pd.Series(np.nan_to_num(base_pnl)).rolling(10, min_periods=1).sum().shift(1).values
        self.vix, self.vts = vix_prior(mk.dates)


def features(mk, ctx, k, i, new, s, n_today, ub, lb):
    x = mk.days[k]; O0, c, sgi, sgd, pc = x.O[0], x.C[i], ctx.sig[k, x.slot[i]], ctx.sgd[k], mk.pc[k]
    ppc = mk.pc[k - 1] if k >= 1 else np.nan
    return np.array([new, x.M[i], ((c / ub - 1) if new > 0 else (1 - c / lb)) / sgi, sgi / ctx.sig_med[k, x.slot[i]],
                     new * (O0 / pc - 1) / sgd, new * (c / O0 - 1) / sgi, new * (c / x.vw[i] - 1) / sgi, float(s == -new), n_today,
                     new * (pc / ppc - 1) / sgd, x.V[:i + 1].sum() / ctx.cv_avg[k, x.slot[i]], ctx.vix[k], ctx.vts[k],
                     mk.dates[k].weekday(), ctx.own[k] / (O0 * sgd)], float)


def run(mk, ctx=None, gate=None, exit_fn=None):
    """fam_V's live rules (no switches); gate(k, features) False skips an entry or flip (a skipped flip goes flat); exit_fn(k, i, s)
    True closes the position at the next bar's open after bar i (LIT2026, 2026-10-24). Returns (daily points, trades as fam_V
    counts them, entries [(k, features, net points)])."""
    sig = mk.sigma(LOOK); pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n); rows = []
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        s = 0; ref = 0.0; p = 0.0; vol = 0; n_today = 0; entry = 0.0; f_open = None
        for i in range(flat_i):
            if exit_fn is not None and s != 0 and exit_fn(k, i, s):
                px = O[i + 1]
                if f_open is not None: rows.append((k, f_open, s * (px - entry) - RT_PTS))
                p += s * (px - ref); vol += abs(s); s = 0; ref = px; continue
            if M[i] not in CHECKS or np.isnan(sg[i]): continue
            c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
            if s == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif s == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            f = None
            if new != 0 and new != s and ctx is not None:
                f = features(mk, ctx, k, i, new, s, n_today, ub, lb)
                if gate is not None and not gate(k, f): new = 0
            if new != s:
                px = O[i + 1]
                if s != 0 and f_open is not None: rows.append((k, f_open, s * (px - entry) - RT_PTS))
                p += s * (px - ref); vol += abs(new - s); s = new; ref = px
                if new != 0: entry = px; f_open = f; n_today += 1
        if s != 0:
            if f_open is not None: rows.append((k, f_open, s * (C[flat_i] - entry) - RT_PTS))
            p += s * (C[flat_i] - ref); vol += abs(s)
        pnl[k] = p; nt[k] = vol / 2
    return pnl, nt, rows


def to_series(mk, pnl, nt, keep, ticks=1):
    """nav_lab.series' conversion for one market part: daily net return per session after the warm-up."""
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * ticks + 0.25) / p_ref; d = mk.dates; sel = (np.arange(len(d)) >= WARMUP) & keep(d)
    o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
    return pd.Series(np.nan_to_num(pnl[sel]) / px - nt[sel] * 2 * c, index=d[sel])


def joined(res, ticks=1):
    return pd.concat([to_series(res[nm][0], res[nm][1], res[nm][2], keep, ticks) for nm, keep in PARTS])


def trades(res):
    """Round trips on the out-of-sample sessions of the joined series (fam_V's count)."""
    return sum(float(n[(np.arange(len(mk.dates)) >= WARMUP) & keep(mk.dates) & (mk.dates.year >= OOS_FROM)].sum())
               for (nm, keep), (mk, _, n) in zip(PARTS, (res[nm] for nm, _ in PARTS)))


def main():
    mks = {nm: N.market(nm) for nm, _ in PARTS}
    pr(f"METALABEL (METALABEL_PLAN.txt): NQ {mks['NQ'].dates[0].date()} .. {N.JOIN.date()} + MNQ to {mks['MNQX'].dates[-1].date()} "
       f"(extended), out of sample {OOS_FROM}-01 on; 1 tick + $0.50 a side; $ per micro at MNQ 29,150")
    # audit: the simulator with no filter == nav_lab.fam_V
    base, ctxs, E = {}, {}, []
    for nm, keep in PARTS:
        mk = mks[nm]; p0, n0 = N.fam_V(mk); ctx = Ctx(mk, p0); p1, n1, rows = run(mk, ctx)
        assert np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and np.array_equal(n0, n1), f"audit failed on {nm}"
        base[nm] = (mk, p1, n1); ctxs[nm] = ctx
        E += [(mk.dates[k], f, net) for k, f, net in rows if k >= WARMUP and keep(pd.DatetimeIndex([mk.dates[k]]))[0]]
    pr(f"audit: the simulator with no filter reproduces nav_lab.fam_V exactly on both markets (daily points and trades)")
    dates = pd.DatetimeIndex([e[0] for e in E]); X = np.array([e[1] for e in E]); y = np.array([e[2] > 0 for e in E], float)
    pr(f"entries: {len(E):,} ({dates[0].date()} .. {dates[-1].date()}), winners {y.mean() * 100:.1f}%; out of sample {int((dates.year >= OOS_FROM).sum()):,}")
    # walk-forward fits
    fits = {}
    for Y in range(OOS_FROM, dates.year.max() + 1):
        tr = dates.year < Y
        for mo in MODELS:
            m = (Logit() if mo == 'logit' else Stumps()).fit(X[tr], y[tr]); ptr = m.predict(X[tr])
            fits[(mo, Y)] = (m, {q: float(np.quantile(ptr, q)) for q in QS})
    oos = dates.year >= OOS_FROM
    for mo in MODELS:
        p = np.array([fits[(mo, d.year)][0].predict(X[j:j + 1])[0] if oos[j] else np.nan for j, d in enumerate(dates)])
        pr(f"   {mo}: out-of-sample AUC on the live rules' entries {auc(y[oos], p[oos]):.3f} (0.5 = no skill); in-sample (last fit) "
           f"{auc(y[dates.year < dates.year.max()], fits[(mo, dates.year.max())][0].predict(X[dates.year < dates.year.max()])):.3f}")
    rb = joined(base); rb = rb[rb.index.year >= OOS_FROM]; rb2 = joined(base, 2); rb2 = rb2[rb2.index.year >= OOS_FROM]
    def summary(r):
        eq = np.cumsum(r.values * USD); return r.mean() * 252 * USD, float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    a, d = summary(rb)
    pr(f"\nlive rules, out of sample: ${a:,.0f}/yr, max drawdown ${d:,.0f}, MAR {NV.mar(rb):.3f}, trades {trades(base):,.0f}")
    pr(" variant              $/yr    max DD    MAR    trades   G1 P(boot)  G2 NQ / MNQ MAR (live)          G3 2 ticks (live)   G4 drop-5   verdict")
    years = sorted(set(rb.index.year)); per_year = {'live rules': rb.groupby(rb.index.year).sum() * USD}
    for mo in MODELS:
        for q in QS:
            res = {}
            for nm, keep in PARTS:
                mk = mks[nm]
                def g(k, f, mk=mk, mo=mo, q=q):
                    Y = mk.dates[k].year
                    if Y < OOS_FROM: return True
                    m, thr = fits[(mo, Y)]; return m.predict(f[None, :])[0] >= thr[q]
                p, n, _ = run(mk, ctxs[nm], g); res[nm] = (mk, p, n)
            rv = joined(res); rv = rv[rv.index.year >= OOS_FROM]; rv2 = joined(res, 2); rv2 = rv2[rv2.index.year >= OOS_FROM]
            a, d = summary(rv); P = NV.boot_mar(rv, rb)
            seg = [(NV.mar(rv[sl]), NV.mar(rb[sl])) for sl in (rv.index <= N.JOIN, rv.index > N.JOIN)]
            drop5 = NV.mar(rv.drop(rv.nlargest(5).index))
            g1 = NV.mar(rv) > NV.mar(rb) and P < P_BAR; g2 = all(v > b for v, b in seg); g3 = NV.mar(rv2) > NV.mar(rb2); g4 = drop5 > NV.mar(rb)
            ntr = trades(res)
            name = f"{mo} skip {int(q * 100)}%"
            pr(f" {name:<18} {a:>8,.0f}  {d:>7,.0f}  {NV.mar(rv):6.3f}  {ntr:6,.0f}   {P:6.4f} {'ok' if g1 else 'no'}   "
               f"{seg[0][0]:.3f} ({seg[0][1]:.3f}) / {seg[1][0]:.3f} ({seg[1][1]:.3f}) {'ok' if g2 else 'no'}   "
               f"{NV.mar(rv2):.3f} ({NV.mar(rb2):.3f}) {'ok' if g3 else 'no'}   {drop5:.3f} {'ok' if g4 else 'no'}   "
               f"{'SIM CANDIDATE' if g1 and g2 and g3 and g4 else 'rejected'}")
            per_year[name] = rv.groupby(rv.index.year).sum() * USD
    pr("\nper year, $ per micro (out of sample):")
    pr("   " + 'year'.ljust(20) + ''.join(f"{y:>8}" for y in years))
    for nm, s in per_year.items(): pr("   " + nm.ljust(20) + ''.join(f"{s.get(y, 0):>8,.0f}" for y in years))
    with open(os.path.join(HERE, 'metalabel_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
