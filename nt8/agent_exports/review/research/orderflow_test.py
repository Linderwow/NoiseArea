r"""Order flow at NoiseArea's entries on NQ 2010-2019 (ORDERFLOW_PLAN.txt): rebuilds every NQ NoiseArea trade from the research
engine's state machine, computes the four pre-registered flow features from the purchased tick windows, and runs tests A (fit
2010-2014), B (test 2015-2019) and C (direction check on the owned MES / MNQ tapes).
    python orderflow_test.py trades      rebuild the trades and audit them against the engine's daily P&L
    python orderflow_test.py run         features + tests A, B, C -> orderflow_output.txt
"""
import os, sys, ctypes, glob
sys.dont_write_bytecode = True
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from scipy.stats import spearmanr, norm

TICK = 0.25
TICKS = r"D:\databento_archive\nq_trades_entry_windows_2010-2019"
FIT, TEST = ('2010-07-01', '2014-12-31'), ('2015-01-01', '2019-05-05')


def nq_trades():
    """Every NQ NoiseArea trade (entries and reversals) with its decision time, direction and net points (2 ticks)."""
    import regime_test as rt
    from research_engine import Market
    m = Market(rt.FILES['NQ'], 20.0, 'NQ'); st = m.state(); rows = []
    for x, s in zip(m.days, st):
        if not s.valid or pd.Timestamp(x.date) > pd.Timestamp('2019-05-03'): continue
        flat_i = x.nb - 1 if x.M[-1] >= 960 else x.nb - 2; mp = s.mpos; prev = 0; open_ = None
        for i in range(flat_i):
            p = int(mp[i])
            if p != prev:
                if open_ is not None:
                    d0, dr, e = open_; rows.append(dict(date=pd.Timestamp(x.date), decision=d0, dir=dr, entry=e, exit=x.O[i + 1], open930=x.O[0])); open_ = None
                if p != 0: open_ = (pd.Timestamp(x.date) + pd.Timedelta(minutes=int(x.M[i])), p, x.O[i + 1])
            prev = p
        if open_ is not None:
            d0, dr, e = open_; rows.append(dict(date=pd.Timestamp(x.date), decision=d0, dir=dr, entry=e, exit=x.C[flat_i], open930=x.O[0]))
    t = pd.DataFrame(rows); t['points'] = t.dir * (t.exit - t.entry) - 2 * TICK
    t['ret'] = t.points / t.open930
    return t


def audit_trades(t):
    """Daily sum of rebuilt trade P&L vs the engine's fam_A daily P&L (1 tick a side, no commission)."""
    import regime_test as rt
    from research_engine import Market, fam_A
    m = Market(rt.FILES['NQ'], 20.0, 'NQ'); pnl, nt = fam_A(m, slip=TICK, comm=0.0)
    eng = pd.Series(pnl / 20.0, index=m.dates).dropna(); mine = t.groupby('date').points.sum()
    c = eng.index.intersection(mine.index); d = (eng.reindex(c) - mine.reindex(c)).abs()
    return (f"rebuilt {len(t)} trades on {t.date.nunique()} days; engine trade count {int(nt[np.isin(m.dates, c)].sum())}; "
            f"max |daily points difference| {d.max():.4f}, days differing > 0.01 pt: {(d > 0.01).sum()}")


def features(t):
    files = sorted(glob.glob(os.path.join(TICKS, 'chunk_*.parquet')))
    x = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    x['t_et'] = pd.to_datetime(x.ts, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
    x = x[x.price > 0]; out = {}
    for d, g in x.groupby('decision_et'):
        pre = g[g.t_et <= d]; l15 = pre[pre.t_et > d - pd.Timedelta(minutes=15)]; l5 = pre[pre.t_et > d - pd.Timedelta(minutes=5)]
        f10 = pre[pre.t_et <= d - pd.Timedelta(minutes=5)]
        def imb(w, big=False):
            if big: w = w[w['size'] >= 10]
            b, s = w.loc[w.side == 'B', 'size'].sum(), w.loc[w.side == 'A', 'size'].sum()
            return (b - s) / (b + s + (1 if big else 0)) if (b + s) > 0 else np.nan
        out[pd.Timestamp(d)] = dict(ofi15=imb(l15), ofi5=imb(l5), big15=imb(l15, True),
                                    pace=len(l5) / (len(f10) / 2) if len(f10) > 0 else np.nan, n15=len(l15))
    f = pd.DataFrame(out).T
    t = t.merge(f, left_on='decision', right_index=True, how='left')
    for c in ('ofi15', 'ofi5', 'big15'): t[c] = t[c] * t.dir                       # align with the trade's direction
    return t


def boot_p(dates, a, b, n=10000, seed=20261020):
    """Month-block bootstrap P(Sharpe(a) <= Sharpe(b))."""
    sh = lambda r: r.mean() / r.std(ddof=1) if r.std(ddof=1) > 0 else 0.0
    mon = pd.DatetimeIndex(dates).to_period('M'); blocks = [np.where(mon == u)[0] for u in mon.unique()]; rng = np.random.default_rng(seed); w = 0
    for _ in range(n):
        idx = np.concatenate([blocks[k] for k in rng.integers(0, len(blocks), len(blocks))]); w += sh(a[idx]) <= sh(b[idx])
    return w / n


def run():
    t = nq_trades(); t = features(t); L = ["=== ORDER FLOW AT NOISEAREA'S NQ ENTRIES 2010-2019 (ORDERFLOW_PLAN.txt) ==="]
    cov = t.ofi15.notna().mean(); L.append(f"trades {len(t)}, with tick features {t.ofi15.notna().sum()} ({cov * 100:.1f}%)")
    feats = ['ofi15', 'ofi5', 'big15', 'pace']
    A = t[(t.decision >= FIT[0]) & (t.decision <= FIT[1])].dropna(subset=feats); B = t[(t.decision >= TEST[0]) & (t.decision <= TEST[1])].dropna(subset=feats)
    res = []
    for f in feats:
        r, p = spearmanr(A[f], A.points); res.append((f, r, p))
    res.sort(key=lambda z: z[2]); L.append(f"TEST A (fit {FIT[0]} .. {FIT[1]}, {len(A)} trades): Spearman with net points, Holm over 4")
    best = None
    for i, (f, r, p) in enumerate(res):
        ph = min(1.0, p * (4 - i)); ok = ph < 0.05 and (best is None) and all(min(1.0, z[2] * (4 - j)) < 0.05 for j, z in enumerate(res[:i]))
        L.append(f"   {f:6s} rho {r:+.3f}  p {p:.4f}  Holm p {ph:.4f}{'  <- chosen' if ok else ''}")
        if ok: best = (f, r)
    if best is None:
        L.append("   no feature passes Holm on the fit period -> no filter (stop, as pre-registered)")
    else:
        f, r = best; q = A[f].quantile([1 / 3, 2 / 3]).values
        terc = lambda v: np.where(v <= q[0], 0, np.where(v <= q[1], 1, 2))
        means = A.groupby(terc(A[f].values)).points.mean(); worst = int(means.idxmin())
        L.append(f"   rule fixed on 2010-2014: skip entries whose {f} is in tercile {worst} (cuts {q[0]:+.3f} / {q[1]:+.3f}); "
                 f"fit-period mean points by tercile {', '.join(f'{k}: {v:+.2f}' for k, v in means.items())}")
        rB, pB = spearmanr(B[f], B.points)
        allB = t[(t.date >= TEST[0]) & (t.date <= TEST[1])]
        keep = ~((allB[f].notna()) & (terc(allB[f].fillna(0).values) == worst))
        d_all = allB.groupby('date').ret.sum(); d_flt = allB[keep].groupby('date').ret.sum().reindex(d_all.index).fillna(0)
        import regime_test as rt
        days = pd.DatetimeIndex([x for x in rt.joined('MNQ')[0] if TEST[0] <= str(x.date()) <= TEST[1]])
        a = d_flt.reindex(days).fillna(0).values; b = d_all.reindex(days).fillna(0).values
        sh = lambda r: r.mean() / r.std(ddof=1) * np.sqrt(252)
        pb = boot_p(days, a, b)
        ok = (np.sign(rB) == np.sign(r)) and pB < 0.05 and sh(a) > sh(b) and pb < 0.05
        L.append(f"TEST B (test {TEST[0]} .. {TEST[1]}, {len(B)} trades): {f} rho {rB:+.3f} p {pB:.4f}; NoiseArea Sharpe with the rule {sh(a):.2f} vs "
                 f"{sh(b):.2f} without, bootstrap P {pb:.3f}; skipped {int((~keep).sum())} of {len(allB)} trades -> {'PASS' if ok else 'fail'}")
        L.append(f"   mean points in 2015-2019 by tercile: " + ", ".join(f"{k}: {v:+.2f} ({n})" for k, (v, n) in
                 B.groupby(terc(B[f].values)).points.agg(['mean', 'size']).iterrows()))
    if best is not None and best[0] in ('ofi15', 'ofi5'):                         # TEST C: direction check on the owned tapes
        import flow_pilot as FP
        tr = pd.read_csv(os.path.join(HERE, 's1_reference_trades_ntbars_v3.csv'), parse_dates=['entry_time'])
        tr['decision'] = tr.entry_time - pd.Timedelta(minutes=5); mins = 15 if best[0] == 'ofi15' else 5
        for name, fn in (('MES tape', FP.flow_mes), ('MNQ tape', FP.flow_mnq)):
            flow, gap = fn(); rows = []
            for r in tr.itertuples():
                d = r.decision
                if d - pd.Timedelta(minutes=mins) < flow.index.min() or d > flow.index.max(): continue
                if gap is not None and gap <= d < pd.Timestamp('2026-04-10'): continue
                w = flow[(flow.index > d - pd.Timedelta(minutes=mins)) & (flow.index <= d)]; tot = w.buy.sum() + w.sell.sum()
                if tot > 0: rows.append((r.dir * (w.buy.sum() - w.sell.sum()) / tot, r.points))
            if len(rows) >= 10:
                a_ = np.array(rows); rc, pc = spearmanr(a_[:, 0], a_[:, 1])
                L.append(f"TEST C ({name}, {len(rows)} MNQ trades): {best[0]} rho {rc:+.3f} (p {pc:.3f}) - same sign as the fit: {np.sign(rc) == np.sign(best[1])}")
    elif best is not None:
        L.append(f"TEST C: {best[0]} cannot be computed on the owned tapes (no per-print sizes / pace there) - not run")
    L.append("DESCRIPTIVE: mean net points by OFI15 tercile (whole 2010-2019): " + ", ".join(
        f"{k}: {v:+.2f}" for k, v in t.dropna(subset=['ofi15']).groupby(pd.qcut(t.dropna(subset=['ofi15']).ofi15, 3, labels=False)).points.mean().items()))
    txt = "\n".join(L); print(txt); open(os.path.join(HERE, 'orderflow_output.txt'), 'w').write(txt + "\n")
    t.to_csv(os.path.join(HERE, 'orderflow_trades_nq.csv'), index=False)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'trades':
        t = nq_trades(); print(audit_trades(t)); print(t.decision.dt.time.value_counts().sort_index().head(14).to_string())
    elif cmd == 'run': run()
    else: print(__doc__)
