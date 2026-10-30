r"""WEEKEND A3 (WEEKEND_PLAN_20261031.txt): order flow at the open as a day-type call, on NinjaTrader's own MNQ tick store
(D:\nt_ticks\MNQ\<contract>\<date>.parquet, every print with the aggressor side, 2025-10-27 .. 2026-10-29).
    python weekend_flow.py   -> weekend_flow_output.txt, weekend_flow_features.csv
Features from 09:30 to the call (09:45 reported, 10:00 primary): F1 signed volume imbalance; F2 large-print (>= 10 contracts)
imbalance; F3 trade intensity vs the median of the previous 14 sessions at that call; F4 lag-1 autocorrelation of 1-minute signed
volume; F5 absorption = |price change in ticks| per 1,000 contracts of net aggressor volume. Labels eff (rest-of-day efficiency from
the call bar >= the median) and meta (the live rules' day P&L > 0). Univariate AUC with a 99.5% bootstrap CI (Bonferroni over the
10 looks at 10:00); direction-agnostic: a feature passes when |AUC - 0.5| >= 0.10 AND the CI excludes 0.50. Also a 2-feature
(F1, F3) leave-one-session-out logistic AUC. 265 sessions: SE(AUC) ~ 0.035 (declared in the plan - low power).
"""
import os, sys, glob, time
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX
import weekend as W

TICKS = r'D:\nt_ticks\MNQ'; CALLS = (585, 600); LARGE = 10; TICK = 0.25
out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)
def save(): open(os.path.join(HERE, 'weekend_flow_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


def session_features(path):
    df = pd.read_parquet(path, columns=['time', 'price', 'volume', 'side'])
    et = df.time.dt.tz_convert('America/New_York'); m = (et.dt.hour * 60 + et.dt.minute).values
    sec = m * 60 + et.dt.second.values
    w = (m >= 570) & (m < 600)
    if w.sum() < 1000: return None
    px = df.price.values[w]; v = df.volume.values[w].astype(float); a = (df.side.values[w] == 'A'); mm = m[w]; ss = sec[w]
    sgn = np.where(a, 1.0, -1.0) * v
    o930 = px[0]; feats = {}
    for T in CALLS:
        q = mm < T
        if q.sum() < 100: return None
        tot = v[q].sum(); net = sgn[q].sum()
        big = v[q] >= LARGE; tot_b = v[q][big].sum(); net_b = sgn[q][big].sum()
        minute = mm[q]; sv = pd.Series(sgn[q]).groupby(minute).sum().reindex(range(570, T), fill_value=0.0).values
        ac = np.corrcoef(sv[:-1], sv[1:])[0, 1] if sv.std() > 0 else 0.0
        dp = (px[q][-1] - o930) / TICK
        feats[T] = dict(F1=net / tot, F2=(net_b / tot_b if tot_b > 0 else 0.0), F3raw=q.sum() / ((T - 570)), F4=ac,
                        F5=abs(dp) / max(abs(net), 100.0) * 1000.0, vol=tot, prints=int(q.sum()), dp_ticks=dp)
    return feats


def auc_ci(score, y, n_boot=2000, seed=20261031):
    m = np.isfinite(score) & np.isfinite(y); s, t = score[m], y[m]
    def _auc(s, t):
        pos, neg = (t == 1).sum(), (t == 0).sum()
        if pos == 0 or neg == 0: return np.nan
        r = pd.Series(s).rank().values; return (r[t == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)
    a = _auc(s, t); rng = np.random.default_rng(seed); bs = []
    for _ in range(n_boot):
        ix = rng.integers(0, len(s), len(s)); bs.append(_auc(s[ix], t[ix]))
    bs = np.array([b for b in bs if np.isfinite(b)])
    return a, float(np.quantile(bs, 0.0025)), float(np.quantile(bs, 0.9975)), int(m.sum())


def loo_logit_auc(X, y):
    """Leave-one-out logistic regression (Newton, L2 1e-3), AUC of the held-out scores."""
    m = np.isfinite(X).all(axis=1) & np.isfinite(y); X, y = X[m], y[m]; n = len(y); sc = np.full(n, np.nan)
    for i in range(n):
        tr = np.ones(n, bool); tr[i] = False
        Xt = X[tr]; mu, sd = Xt.mean(0), Xt.std(0); sd[sd == 0] = 1; Zt = np.c_[np.ones(tr.sum()), (Xt - mu) / sd]; yt = y[tr]
        b = np.zeros(Zt.shape[1])
        for _ in range(25):
            p = 1 / (1 + np.exp(-Zt @ b)); g = Zt.T @ (p - yt) + 1e-3 * b; H = (Zt * (p * (1 - p))[:, None]).T @ Zt + 1e-3 * np.eye(len(b))
            b -= np.linalg.solve(H, g)
        sc[i] = np.r_[1.0, (X[i] - mu) / sd] @ b
    a, lo, hi, nn = auc_ci(sc, y); return a, lo, hi, nn


def main():
    W.below_normal(); t0 = time.time()
    pr(f"WEEKEND A3 order flow at the open - {time.strftime('%Y-%m-%d %H:%M')}")
    files = {}
    for f in sorted(glob.glob(os.path.join(TICKS, '*', '*.parquet'))):
        d = os.path.basename(f)[:-8]; files.setdefault(d, []).append(f)
    rows = []
    for d, fl in sorted(files.items()):
        best = None
        for f in fl:                                              # two contracts on roll days: keep the one with more opening volume
            ft = session_features(f)
            if ft is None: continue
            if best is None or ft[600]['vol'] > best[600]['vol']: best = ft
        if best is None: continue
        r = {'date': pd.Timestamp(d)}
        for T in CALLS:
            for kk, vv in best[T].items(): r[f'{kk}_{T}'] = vv
        rows.append(r)
    F = pd.DataFrame(rows).set_index('date').sort_index()
    for T in CALLS:
        med = F[f'F3raw_{T}'].rolling(14, min_periods=7).median().shift(1); F[f'F3_{T}'] = F[f'F3raw_{T}'] / med
    pr(f"sessions with opening tape: {len(F)} ({F.index[0].date()} .. {F.index[-1].date()}); opening (09:30-10:00) volume median {F['vol_600'].median():,.0f}")
    # labels from the strategy's own bars (MNQX)
    mk = N.market('MNQX'); sig = mk.sigma(14); p_base, nt_base, _, _ = W.engine(mk); lab = {}
    for k, x in enumerate(mk.days):
        if k < 14 or np.isnan(mk.pc[k]): continue
        slot_of = {int(s): i for i, s in enumerate(x.slot)}; r = {}
        for T in CALLS:
            sl = (T - 575) // 5
            if sl not in slot_of: continue
            i = slot_of[sl]; hh = x.H[i + 1:]; ll = x.L[i + 1:]; rng = hh.max() - ll.min() if len(hh) else 0.0
            r[f'eff_{T}'] = abs(x.C[-1] - x.C[i]) / rng if rng > 0 else np.nan
        r['pnl'] = p_base[k]; r['nt'] = nt_base[k]; lab[mk.dates[k]] = r
    L = pd.DataFrame(lab).T; L.index = pd.to_datetime(L.index)
    D = F.join(L, how='inner'); D.to_csv(os.path.join(HERE, 'weekend_flow_features.csv'))
    pr(f"sessions with tape AND bars: {len(D)}; NoiseArea traded on {(D.nt > 0).mean() * 100:.0f}% of them")
    for T in CALLS:
        y_eff = (D[f'eff_{T}'] >= D[f'eff_{T}'].median()).astype(float).values
        y_meta = np.where(D.nt > 0, (D.pnl > 0).astype(float), np.nan)
        pr(f"\ncall {T // 60}:{T % 60:02d}{'  (PRIMARY)' if T == 600 else '  (reported)'}: eff median {D[f'eff_{T}'].median():.3f}; meta defined on {np.isfinite(y_meta).sum()} sessions")
        pr(f"   feature                              label   AUC    99.5% CI          n     verdict")
        for fn, desc in (('F1', 'signed volume imbalance'), ('F2', 'large-print imbalance'), ('F3', 'trade intensity vs 14 sessions'),
                         ('F4', 'imbalance persistence (lag-1 ac)'), ('F5', 'absorption |ticks| per 1k net')):
            x = D[f'{fn}_{T}'].values.astype(float)
            for lb, y in (('eff', y_eff), ('meta', y_meta)):
                a, lo, hi, n = auc_ci(x, y)
                ok = abs(a - 0.5) >= 0.10 and (lo > 0.5 or hi < 0.5)
                pr(f"   {fn} {desc:<33} {lb:<5}  {a:.3f}  [{lo:.3f}, {hi:.3f}]   {n:>4}   {'PASS' if ok else '-'}")
            # the direction-aware version for the imbalance features: does the sign of the flow match the sign of the day's move?
            if fn in ('F1', 'F2'):
                move = np.sign(D.pnl.values) * 0 + np.sign((D[f'dp_ticks_{T}']).values)      # placeholder for alignment
        # signed version: imbalance sign vs rest-of-day direction (does the open's flow point the way?)
        for fn in ('F1', 'F2'):
            x = D[f'{fn}_{T}'].values.astype(float)
            # rest-of-day direction from the strategy's bars: close vs the call-bar close
            dirs = []
            for d_ in D.index:
                k = mk.dates.get_loc(d_); xk = mk.days[k]; slot_of = {int(s): i for i, s in enumerate(xk.slot)}; i = slot_of[(T - 575) // 5]
                dirs.append(np.sign(xk.C[-1] - xk.C[i]))
            dirs = np.array(dirs); y_dir = np.where(dirs != 0, (dirs > 0).astype(float), np.nan)
            a, lo, hi, n = auc_ci(x, y_dir)
            pr(f"   {fn} as a DIRECTION call (rest of day up?): AUC {a:.3f} [{lo:.3f}, {hi:.3f}] n {n} (information)")
        X = D[[f'F1_{T}', f'F3_{T}']].values.astype(float)
        for lb, y in (('eff', y_eff), ('meta', y_meta)):
            a, lo, hi, n = loo_logit_auc(X, y)
            pr(f"   2-feature logistic (F1 + F3), leave-one-out, label {lb}: AUC {a:.3f} [{lo:.3f}, {hi:.3f}] n {n}")
        # Gao-style volume terciles (from the scout's reading list, information only): NoiseArea's P&L by opening-volume tercile
        q = pd.qcut(D[f'vol_{T}'], 3, labels=['low', 'mid', 'high'])
        pr("   NoiseArea day P&L (points) by opening-volume tercile (information): " + ', '.join(f"{t}: {D.pnl[q == t].mean():+.1f} pts/day (n {int((q == t).sum())})" for t in ('low', 'mid', 'high')))
        save()
    pr(f"\n{time.time() - t0:,.0f} s"); save()


if __name__ == '__main__':
    main()
