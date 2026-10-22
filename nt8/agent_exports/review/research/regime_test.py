r"""Regime-switching strategy catalog ("Patrick's way") vs NoiseArea, per REGIME_PLAN.txt (pre-registered 2026-10-20).

Run from the research folder, in this order (each stage prints its report; holdout figures appear only in the last one):
    python regime_test.py build       gross daily P&L of all catalog variants on the three files -> regime_work\*.npz, audit (1)
    python regime_test.py discover    classifiers, 24 configurations fitted on FIT and scored on VALIDATE, the chosen model
    python regime_test.py holdout     the single holdout run: model vs NoiseArea, decision rule, robustness, secondary holdout
regime_work\ is a cache (about 3 MB) and can be deleted; build recreates it (about 3 minutes).
Audits: regime_check.py (families, holdout); descriptive follow-up: regime_diag.py. All output: regime_output.txt.
"""
import os, sys, time, json, ctypes
import numpy as np, pandas as pd

try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)   # below normal: NinjaTrader is live
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
from research_engine import Market, FAMS, grid, WARMUP
import onfade
from sim import load

WORK = os.path.join(HERE, 'regime_work')
FILES = {'NQ': os.path.join(HERE, 'scaling_inputs', 'nq_2010-2019_5m.csv'),
         'MNQ': os.path.join(HERE, 'scaling_inputs', 'nt_analyzer_bars_to_2026-10-14.csv'),
         'MNQDB': os.path.join(HERE, 'mnq_db_5m_2019-2026.csv')}
JOIN = pd.Timestamp('2019-05-05')
FIT, VAL, DISC, HOLD = ('2010-07-21', '2014-12-31'), ('2015-01-01', '2019-05-05'), ('2010-07-21', '2019-05-05'), ('2019-05-06', '2026-10-14')
MIN_TR, T_MIN, SEED = 30, 1.0, 20261020
FAMILY_NAMES = {'A': 'noise-area momentum', 'B': 'opening-range breakout', 'C': 'overnight-range fade', 'D': 'double top/bottom',
                'E': 'band reversion', 'F': 'VWAP-pullback continuation', 'G': 'VWAP-stretch reversion', 'H': 'prior-day high/low',
                'I': 'opening gap'}


# ------------------------------------------------------------------------------------------------ new families G, H, I
def fam_G(mk, k=3.0, s=1.0, maxn=1, slip=0.0, comm=0.0):
    """VWAP-stretch mean reversion: 10:30-15:00, close > k ATR from VWAP -> fade at next open, stop s ATR beyond the close, target VWAP."""
    pv = mk.pv; pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for d, x in enumerate(mk.days):
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        pos = 0; pend = None; n_today = 0; p = 0.0; n_ = 0
        for i in range(nb):
            if pend is not None and pos == 0:
                dr, stp_, tv = pend; e = O[i] + slip * dr; pend = None
                if (e - stp_) * dr > 0 and (tv - e) * dr > 0:
                    pos = dr; entry = e; target = tv; stp = stp_; n_ += 1
            if pos != 0:
                ex = None
                if (pos > 0 and L[i] <= stp) or (pos < 0 and H[i] >= stp): ex = stp - slip * pos
                elif (pos > 0 and H[i] >= target) or (pos < 0 and L[i] <= target): ex = target
                elif M[i] >= 955: ex = C[i] - slip * pos
                if ex is not None: p += (ex - entry) * pos * pv - 2 * comm; pos = 0
            if pos != 0 or pend is not None or n_today >= maxn or not (630 <= M[i] <= 900) or A[i] <= 0: continue
            if C[i] > vw[i] + k * A[i]: pend = (-1, C[i] + s * A[i], vw[i]); n_today += 1
            elif C[i] < vw[i] - k * A[i]: pend = (1, C[i] - s * A[i], vw[i]); n_today += 1
        if pos != 0: p += (C[-1] - slip * pos - entry) * pos * pv - 2 * comm
        pnl[d] = p; nt[d] = n_
    return pnl, nt


def fam_H(mk, mode='fade', buf=0.25, s=1.0, ex='vwap', slip=0.0, comm=0.0):
    """Prior-day RTH high/low. fade: trade through the level, close back inside by buf ATR -> fade, stop s ATR beyond the level.
    break: close beyond the level by buf ATR -> follow, stop s ATR back inside. Exit: VWAP (fade target / break close-through),
    2R target, or the close. 09:45-15:00, one trade per side per day."""
    pv = mk.pv; pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for d, x in enumerate(mk.days):
        if d == 0: continue
        pdh = mk.days[d - 1].H.max(); pdl = mk.days[d - 1].L.min()
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        pos = 0; pend = None; used = {1: False, -1: False}; p = 0.0; n_ = 0; exit_next = False
        for i in range(nb):
            if exit_next and pos != 0:
                p += (O[i] - slip * pos - entry) * pos * pv - 2 * comm; pos = 0; exit_next = False
            if pend is not None and pos == 0:
                dr, stp_, tv = pend; e = O[i] + slip * dr; pend = None; risk = (e - stp_) * dr
                tg = (tv if ex == 'vwap' and mode == 'fade' else (e + 2 * risk * dr if ex == '2R' else None))
                if risk > 0 and (tg is None or (tg - e) * dr > 0):
                    pos = dr; entry = e; target = tg; stp = stp_; n_ += 1
            if pos != 0:
                xp = None
                if (pos > 0 and L[i] <= stp) or (pos < 0 and H[i] >= stp): xp = stp - slip * pos
                elif target is not None and ((pos > 0 and H[i] >= target) or (pos < 0 and L[i] <= target)): xp = target
                elif M[i] >= 955: xp = C[i] - slip * pos
                if xp is not None: p += (xp - entry) * pos * pv - 2 * comm; pos = 0
                elif mode == 'break' and ex == 'vwap' and (C[i] - vw[i]) * pos < 0 and i < nb - 1: exit_next = True
            if pos != 0 or pend is not None or not (585 <= M[i] <= 900) or A[i] <= 0: continue
            a = A[i]
            if mode == 'fade':
                if not used[-1] and H[i] >= pdh and C[i] < pdh - buf * a: pend = (-1, pdh + s * a, vw[i]); used[-1] = True
                elif not used[1] and L[i] <= pdl and C[i] > pdl + buf * a: pend = (1, pdl - s * a, vw[i]); used[1] = True
            else:
                if not used[1] and C[i] > pdh + buf * a: pend = (1, pdh - s * a, None); used[1] = True
                elif not used[-1] and C[i] < pdl - buf * a: pend = (-1, pdl + s * a, None); used[-1] = True
        if pos != 0: p += (C[-1] - slip * pos - entry) * pos * pv - 2 * comm
        pnl[d] = p; nt[d] = n_
    return pnl, nt


def fam_I(mk, g=1.0, mode='fade', s=1.0, slip=0.0, comm=0.0):
    """Opening gap: |09:30 open - prior close| >= g x its 14-session mean -> at the 09:35 close fade it (target the prior close) or
    follow it (exit at the close); stop s ATR beyond the 09:35 bar's extreme."""
    pv = mk.pv; pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    gaps = np.array([abs(x.O[0] - mk.pc[d]) if not np.isnan(mk.pc[d]) else np.nan for d, x in enumerate(mk.days)])
    for d, x in enumerate(mk.days):
        if d < 15 or np.isnan(mk.pc[d]) or x.nb < 3: continue
        mg = np.nanmean(gaps[d - 14:d]); gap = x.O[0] - mk.pc[d]
        if not mg > 0 or abs(gap) < g * mg or gap == 0: continue
        O, H, L, C, M, A, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.nb
        dr = -int(np.sign(gap)) if mode == 'fade' else int(np.sign(gap))
        e = O[1] + slip * dr
        stp = H[0] + s * A[0] if dr < 0 else L[0] - s * A[0]
        tg = mk.pc[d] if mode == 'fade' else None
        if (e - stp) * dr <= 0 or (tg is not None and (tg - e) * dr <= 0): continue
        xp = None
        for i in range(1, nb):
            if (dr > 0 and L[i] <= stp) or (dr < 0 and H[i] >= stp): xp = stp - slip * dr; break
            if tg is not None and ((dr > 0 and H[i] >= tg) or (dr < 0 and L[i] <= tg)): xp = tg; break
            if M[i] >= 955: xp = C[i] - slip * dr; break
        if xp is None: xp = C[-1] - slip * dr
        pnl[d] = (xp - e) * dr * pv - 2 * comm; nt[d] = 1
    return pnl, nt


def fam_C_gross(mk, n_out=2, stop_atr=1.5, exit='close', on_ret='any'):
    v = onfade.V(n_out=n_out, stop_atr=stop_atr, exit=exit, on_ret=on_ret, max_stop_pts=1e9, max_stop_pct=0.6,
                 slip_ticks=0, comm_side=0.0, pv=1.0)
    tr = onfade.run(mk.onfade_days(), v)
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    if len(tr):
        idx = {d: k for k, d in enumerate(mk.dates.date)}
        for d, g in tr.groupby('tday'):
            k = idx.get(pd.Timestamp(d).date())
            if k is not None: pnl[k] = g.net.sum(); nt[k] = len(g)
    return pnl, nt


def catalog():
    """[(family, params)]: the 684-variant grid of VARIANTS_PLAN.txt, then G, H, I, then the D default (defaults catalog only)."""
    cat = list(grid())
    for k in (2.0, 3.0, 4.0):
        for s in (1.0, 2.0):
            for mx in (1, 2): cat.append(('G', dict(k=k, s=s, maxn=mx)))
    for mode in ('fade', 'break'):
        for buf in (0.0, 0.25):
            for s in (1.0, 2.0):
                for ex in ('vwap', '2R', 'close'): cat.append(('H', dict(mode=mode, buf=buf, s=s, ex=ex)))
    for g in (0.5, 1.0, 1.5):
        for mode in ('fade', 'follow'):
            for s in (1.0, 2.0): cat.append(('I', dict(g=g, mode=mode, s=s)))
    cat.append(('D', dict(tol=0.15, pull=1.0, tgt='vwap', buf=0.25, state=True)))
    return cat


CAT = catalog()
N_FULL = len(CAT) - 1                       # 732: the "full" catalog; the last column is the D default
DEFAULTS = {'A': dict(vm=1.0, look=14, step=30, trail='paper', eff=None), 'B': dict(orm=5, R=10.0, stop='side', entry='candle'),
            'C': dict(n_out=2, stop_atr=1.5, exit='close', on_ret='any'), 'D': dict(tol=0.15, pull=1.0, tgt='vwap', buf=0.25, state=True),
            'E': dict(zone=0.5, stop='band', tgt='vwap', state=True), 'F': dict(kk=0.0, s_atr=0.5, exit='close', maxn=1),
            'G': dict(k=3.0, s=1.0, maxn=1), 'H': dict(mode='fade', buf=0.25, s=1.0, ex='vwap'), 'I': dict(g=1.0, mode='fade', s=1.0)}
DEF_IDX = [next(j for j, (f, p) in enumerate(CAT) if f == fam and p == prm) for fam, prm in DEFAULTS.items()]
A_DEF = DEF_IDX[0]
FAM_OF = np.array([f for f, _ in CAT])


def label(j):
    f, p = CAT[j]; return f + ' ' + ' '.join(f"{k}={v}" for k, v in p.items())


def run_variant(mk, j):
    f, p = CAT[j]
    if f == 'C': return fam_C_gross(mk, **p)
    fn = {'G': fam_G, 'H': fam_H, 'I': fam_I}.get(f) or FAMS[f]
    return fn(mk, **p, slip=0.0, comm=0.0)


# ------------------------------------------------------------------------------------------------ stage 1: build
def daily_stats(path, mk):
    """Per session: RTH O/H/L/C in points and the overnight (18:00-09:30) range."""
    df = load(path)
    eth = df[(~df.in_rth) & ((df.etmin <= 570) | (df.etmin > 1080))]
    onr = (eth.groupby('tday').high.max() - eth.groupby('tday').low.min())
    onr.index = pd.to_datetime(onr.index)
    return pd.DataFrame({'O': [x.O[0] for x in mk.days], 'H': [x.H.max() for x in mk.days], 'L': [x.L.min() for x in mk.days],
                         'C': [x.C[-1] for x in mk.days], 'ONR': onr.reindex(mk.dates).values}, index=mk.dates)


def build(name, cols=None):
    os.makedirs(WORK, exist_ok=True)
    t0 = time.time(); path = FILES[name]
    mk = Market(path, 1.0, name)
    cols = list(range(len(CAT))) if cols is None else cols
    P = np.zeros((mk.n, len(CAT))); NT = np.zeros((mk.n, len(CAT)), int); done = np.zeros(len(CAT), bool)
    for c, j in enumerate(cols):
        p, n = run_variant(mk, j); P[:, j] = np.nan_to_num(p); NT[:, j] = n; done[j] = True
        if c % 100 == 0: print(f"   {name}: {c}/{len(cols)} variants, {time.time() - t0:.0f} s", flush=True)
    ds = daily_stats(path, mk)
    np.savez_compressed(os.path.join(WORK, f'{name}.npz'), P=P, NT=NT, done=done, dates=mk.dates.values.astype('datetime64[D]'))
    ds.to_csv(os.path.join(WORK, f'{name}_daily.csv'))
    print(f"   {name}: {mk.n} sessions {mk.dates[0].date()}..{mk.dates[-1].date()}, {len(cols)} variants in {time.time() - t0:.0f} s", flush=True)


def load_work(name):
    z = np.load(os.path.join(WORK, f'{name}.npz'))
    return pd.to_datetime(z['dates']), z['P'], z['NT'], z['done'], pd.read_csv(os.path.join(WORK, f'{name}_daily.csv'), index_col=0, parse_dates=True)


def joined(second='MNQ'):
    """Joined series NQ (<= 2019-05-05) + second file (>= 2019-05-06), statistics rows only (warm-up removed), as in scaling.py.
    Returns dates, net-return matrix R (today's costs), trade counts, the per-side cost c, and the full daily table for features."""
    d1, P1, N1, _, s1 = load_work('NQ'); d2, P2, N2, done2, s2 = load_work(second)
    k1 = (np.arange(len(d1)) >= WARMUP) & (d1 <= JOIN); k2 = (np.arange(len(d2)) >= WARMUP) & (d2 > JOIN)
    dates = d1[k1].append(d2[k2]); P = np.vstack([P1[k1], P2[k2]]); NT = np.vstack([N1[k1], N2[k2]])
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    o = np.r_[s1.O.values[k1], s2.O.values[k2]]
    px = raw.reindex(dates).values; px = np.where(np.isnan(px), o, px)
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = 0.5 / p_ref
    R = P / px[:, None] - NT * 2 * c
    full = pd.concat([s1[s1.index <= JOIN].assign(seg=0), s2[s2.index > JOIN].assign(seg=1)])
    return dates, R, NT, c, p_ref, px, P, full, done2


def audit():
    dates, R, NT, c, p_ref, px, P, full, _ = joined('MNQ')
    sd = pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0, parse_dates=True)
    mine = pd.Series(R[:, A_DEF], index=dates)
    common = sd.index.intersection(mine.index)
    diff = np.abs(mine.reindex(common).values - sd.r_today.reindex(common).values)
    print(f"\n=== AUDIT 1: NoiseArea default through this pipeline vs scaling_inputs\\scaling_daily.csv ===")
    print(f"   sessions: mine {len(mine)}, scaling {len(sd)}, common {len(common)}; max |difference| {diff.max():.2e}; "
          f"trades {int(NT[:, A_DEF].sum())} vs {int(sd.nt.sum())}  ->  {'PASSED' if diff.max() < 1e-9 and len(common) == len(sd) == len(mine) else 'FAILED'}")
    return diff.max() < 1e-9


# ------------------------------------------------------------------------------------------------ classifiers and selection
def features(full):
    O, H, L, C, ONR = (full[k].values.astype(float) for k in ('O', 'H', 'L', 'C', 'ONR'))
    seg = full.seg.values; T = len(full)
    dC = np.r_[np.nan, np.diff(C)]; dC[1:][seg[1:] != seg[:-1]] = np.nan
    gap = np.abs(O - np.r_[np.nan, C[:-1]]); gap[1:][seg[1:] != seg[:-1]] = np.nan
    body = np.abs(C - O); rng = H - L
    f = pd.DataFrame(index=full.index, columns=['ER10', 'VOLR', 'ONR', 'GAPZ', 'PDE'], dtype=float)
    for t in range(T):
        if t >= 11:
            w = dC[t - 10:t]; den = np.nansum(np.abs(w)); f.iat[t, 0] = abs(np.nansum(w)) / den if den > 0 else np.nan
        if t >= 60: f.iat[t, 1] = body[t - 5:t].mean() / body[t - 60:t].mean()
        if t >= 14:
            f.iat[t, 2] = ONR[t] / rng[t - 14:t].mean() if not np.isnan(ONR[t]) else np.nan
            mg = np.nanmean(gap[t - 14:t]); f.iat[t, 3] = gap[t] / mg if mg > 0 else np.nan
        if t >= 1 and rng[t - 1] > 0: f.iat[t, 4] = body[t - 1] / rng[t - 1]
    return f


SCHEMES = {'S0': [], 'S1': ['ER10'], 'S2': ['VOLR'], 'S3': ['ER10', 'VOLR'], 'S4': ['ONR', 'GAPZ'], 'S5': ['PDE']}


def cells(feat, scheme, ref_mask):
    """Cell id per day (-1 = a feature missing); cut points from the days in ref_mask only."""
    cols = SCHEMES[scheme]
    if not cols: return np.zeros(len(feat), int), {}
    cid = np.zeros(len(feat), int); cuts = {}; ok = np.ones(len(feat), bool)
    for col in cols:
        v = feat[col].values.astype(float); ref = v[ref_mask & ~np.isnan(v)]
        q = [np.median(ref)] if col == 'GAPZ' else list(np.percentile(ref, [100 / 3, 200 / 3]))
        cuts[col] = q; ok &= ~np.isnan(v)
        cid = cid * (len(q) + 1) + np.digitize(np.nan_to_num(v), q)
    cid[~ok] = -1
    return cid, cuts


def tstats(R, NT, mask, cols):
    r = R[np.ix_(mask, cols)]; n = mask.sum()
    sd = r.std(axis=0, ddof=1); t = np.where(sd > 0, r.mean(axis=0) / (sd / np.sqrt(n)), -np.inf)
    return t, NT[np.ix_(mask, cols)].sum(axis=0)


def select(R, NT, cid, fit_mask, catalog_cols, K, exclude_fam=None):
    """{cell: [(column, weight)]}: per cell the best variant(s) by t-stat on the fit days, >= MIN_TR trades, t >= T_MIN."""
    cols = np.array([j for j in catalog_cols if exclude_fam is None or FAM_OF[j] != exclude_fam])
    out = {}
    for c in sorted(set(cid[cid >= 0])):
        m = fit_mask & (cid == c)
        if m.sum() < 20: out[c] = []; continue
        t, ntr = tstats(R, NT, m, cols)
        order = [k for k in np.argsort(-t) if ntr[k] >= MIN_TR and t[k] >= T_MIN]
        pick = []
        for k in order:
            if len(pick) == K: break
            if all(FAM_OF[cols[k]] != FAM_OF[j] for j in pick): pick.append(cols[k])
        out[c] = [(int(j), 1.0 / len(pick)) for j in pick]
    return out


def apply(R, NT, cid, sel):
    r = np.zeros(len(cid)); n = np.zeros(len(cid))
    for c, lst in sel.items():
        m = cid == c
        for j, w in lst: r[m] += w * R[m, j]; n[m] += NT[m, j]
    return r, n


def sharpe(r): return r.mean() / r.std(ddof=1) * np.sqrt(252) if r.std(ddof=1) > 0 else 0.0
def tstat(r): return r.mean() / (r.std(ddof=1) / np.sqrt(len(r))) if r.std(ddof=1) > 0 else 0.0


def between(dates, a): return (dates >= pd.Timestamp(a[0])) & (dates <= pd.Timestamp(a[1]))


CONFIGS = [(s, cat, K) for s in SCHEMES for cat in ('defaults', 'full') for K in (1, 2)]


def cat_cols(cat): return list(range(N_FULL)) if cat == 'full' else DEF_IDX


def complexity(cfg): return (len(SCHEMES[cfg[0]]), cfg[1] == 'full', cfg[2])


def discover():
    dates, R, NT, c, p_ref, px, P, full, _ = joined('MNQ')
    if not audit(): print("Audit 1 failed: stopping before any regime figure, per REGIME_PLAN.txt."); sys.exit(1)
    feat = features(full).reindex(dates)
    fit, val, disc = between(dates, FIT), between(dates, VAL), between(dates, DISC)
    N = 2 * p_ref
    print(f"\nSessions: discovery {disc.sum()} (FIT {fit.sum()}, VALIDATE {val.sum()}); notional/micro ${N:,.0f}; cost per side {c * 1e4:.3f} bp")
    na = R[:, A_DEF]
    print(f"NoiseArea default: FIT Sharpe {sharpe(na[fit]):.2f}, VALIDATE {sharpe(na[val]):.2f}, discovery {sharpe(na[disc]):.2f}")
    print("\nEach family's default variant on the discovery period (net, today's costs):")
    for fam, j in zip(DEFAULTS, DEF_IDX):
        r = R[disc, j]; print(f"   {fam} {FAMILY_NAMES[fam]:28s} Sharpe {sharpe(r):+.2f}  ${r.sum() * N / (disc.sum() / 252):+8,.0f}/yr  trades {int(NT[disc, j].sum()):5d}")
    print("\nBest full-grid variant per family on the discovery period (t-stat of daily net):")
    t, ntr = tstats(R, NT, disc, list(range(N_FULL)))
    for fam in FAMILY_NAMES:
        idx = [j for j in range(N_FULL) if FAM_OF[j] == fam and ntr[j] >= MIN_TR]
        if idx:
            b = max(idx, key=lambda j: t[j]); print(f"   {fam} t {t[b]:+.2f}  {label(b)}")
    print("\n=== 24 configurations: fitted on FIT (2010-07..2014), scored on VALIDATE (2015..2019-05) ===")
    rows = []
    for cfg in CONFIGS:
        s, cat, K = cfg
        cid, _ = cells(feat, s, fit)
        sel = select(R, NT, cid, fit, cat_cols(cat), K)
        rv, nv = apply(R, NT, cid, sel); rows.append(dict(scheme=s, catalog=cat, K=K, fit_sharpe=sharpe(rv[fit]), val_sharpe=sharpe(rv[val]),
                                                          val_usd_yr=rv[val].sum() * N / (val.sum() / 252), val_trades=int(nv[val].sum()),
                                                          cells_trading=sum(1 for v in sel.values() if v)))
    tab = pd.DataFrame(rows)
    print(tab.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    best = max(CONFIGS, key=lambda cfg: (round(tab[(tab.scheme == cfg[0]) & (tab.catalog == cfg[1]) & (tab.K == cfg[2])].val_sharpe.iloc[0], 6),
                                         tuple(-v for v in complexity(cfg))))
    print(f"\nNoiseArea default on VALIDATE: Sharpe {sharpe(na[val]):.2f}, ${na[val].sum() * N / (val.sum() / 252):,.0f}/yr")
    print(f"CHOSEN MODEL (best VALIDATE Sharpe): scheme {best[0]} {SCHEMES[best[0]] or '(no regime)'}, catalog {best[1]}, K {best[2]}")
    cid, cuts = cells(feat, best[0], disc)
    sel = select(R, NT, cid, disc, cat_cols(best[1]), best[2])
    print(f"Refitted on the whole discovery period; cut points {json.dumps({k: [round(x, 4) for x in v] for k, v in cuts.items()})}")
    for cc, lst in sel.items():
        print(f"   cell {cc}: " + ("; ".join(f"{label(j)} (w {w:.2f})" for j, w in lst) if lst else "no trade"))
    json.dump(dict(model=list(best), cuts=cuts, sel={str(k): v for k, v in sel.items()}), open(os.path.join(WORK, 'model.json'), 'w'), indent=1)
    tab.to_csv(os.path.join(WORK, 'configs_discovery.csv'), index=False)


# ------------------------------------------------------------------------------------------------ holdout
def month_blocks(dates, *series):
    key = dates.year * 12 + dates.month
    return [np.where(key == k)[0] for k in np.unique(key)]


def boot_sharpe_diff(dates, a, b, n=10_000, seed=SEED):
    blocks = month_blocks(dates); rng = np.random.default_rng(seed); out = np.empty(n)
    for i in range(n):
        idx = np.concatenate([blocks[k] for k in rng.integers(0, len(blocks), len(blocks))])
        out[i] = sharpe(a[idx]) - sharpe(b[idx])
    return np.mean(out <= 0)


def prop_odds(dates, usd, n=4000, months=24, seed=SEED):
    """50K evaluation: +$3,000 target, $2,500 end-of-day trailing drawdown that stops trailing at the starting balance."""
    blocks = month_blocks(dates); rng = np.random.default_rng(seed); win = 0; t_pass = []
    for _ in range(n):
        seq = np.concatenate([usd[blocks[k]] for k in rng.integers(0, len(blocks), months)])
        bal = 0.0; hi = 0.0; res = None
        for t, v in enumerate(seq):
            bal += v; hi = max(hi, bal); floor = min(hi - 2500.0, 0.0)
            if bal >= 3000.0: res = t; break
            if bal <= floor: break
        if res is not None: win += 1; t_pass.append(res)
    return win / n, (np.median(t_pass) / 21 if t_pass else np.nan)


def metrics(r, n, dates, N):
    usd = r * N; eq = np.cumsum(usd); dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    traded = n > 0; gaps = []; g = 0
    for x in traded:
        g = 0 if x else g + 1; gaps.append(g)
    yrs = len(r) / 252
    return dict(usd_yr=usd.sum() / yrs, sharpe=sharpe(r), t=tstat(r), maxdd=dd, trades_yr=n.sum() / yrs, pct_days=traded.mean() * 100,
                longest_gap=max(gaps) if gaps else 0)


def fmt(m): return (f"${m['usd_yr']:>7,.0f}/yr  Sharpe {m['sharpe']:+.2f}  t {m['t']:+.2f}  max DD ${m['maxdd']:>7,.0f}  "
                    f"trades/yr {m['trades_yr']:5.0f}  days traded {m['pct_days']:4.1f}%  longest gap {m['longest_gap']:3d} sessions")


def holdout():
    mdl = json.load(open(os.path.join(WORK, 'model.json')))
    scheme, cat, K = mdl['model']
    dates, R, NT, c, p_ref, px, P, full, _ = joined('MNQ')
    feat = features(full).reindex(dates)
    disc, hold = between(dates, DISC), between(dates, HOLD); N = 2 * p_ref
    cid, _ = cells(feat, scheme, disc); sel = {int(k): [(int(j), w) for j, w in v] for k, v in mdl['sel'].items()}
    rm, nm = apply(R, NT, cid, sel); na, nna = R[:, A_DEF], NT[:, A_DEF].astype(float)
    hd = dates[hold]
    print(f"=== HOLDOUT 2019-05-06 .. 2026-10-14 ({hold.sum()} sessions, NinjaTrader MNQ bars), today's costs, per micro ===")
    print(f"MODEL {scheme} / {cat} / K={K}:")
    for cc, lst in sel.items():
        days = int(((cid == cc) & hold).sum()); print(f"   cell {cc} ({days} holdout days): " + ("; ".join(label(j) for j, _ in lst) or "no trade"))
    M_m, M_na = metrics(rm[hold], nm[hold], hd, N), metrics(na[hold], nna[hold], hd, N)
    print(f"   model      {fmt(M_m)}\n   NoiseArea  {fmt(M_na)}")
    p_self = boot_sharpe_diff(hd, rm[hold], na[hold])
    # overlay: NoiseArea always + per cell the best non-A variant (t >= T_MIN on discovery)
    ov_sel = select(R, NT, cid, disc, cat_cols(cat), 1, exclude_fam='A')
    ro, no = apply(R, NT, cid, ov_sel); ro = ro + na; no = no + nna
    M_ov = metrics(ro[hold], no[hold], hd, N); p_ov = boot_sharpe_diff(hd, ro[hold], na[hold])
    print(f"   overlay    {fmt(M_ov)}   (NoiseArea + " + "; ".join(f"cell {k}: {label(v[0][0]) if v else '-'}" for k, v in ov_sel.items()) + ")")
    # oracle: same scheme/catalog/K, cells selected on the holdout itself
    or_sel = select(R, NT, cid, hold, cat_cols(cat), K); ror, nor = apply(R, NT, cid, or_sel)
    M_or = metrics(ror[hold], nor[hold], hd, N)
    print(f"   ORACLE     {fmt(M_or)}   (hindsight: cells chosen on the holdout itself)")
    cid3, _ = cells(feat, 'S3', disc); o3 = select(R, NT, cid3, hold, list(range(N_FULL)), 1); r3, n3 = apply(R, NT, cid3, o3)
    print(f"   ORACLE S3 full K1 {fmt(metrics(r3[hold], n3[hold], hd, N))}")
    pm, tm = prop_odds(hd, rm[hold] * N); pn, tn = prop_odds(hd, na[hold] * N)
    print(f"\n50K evaluation pass odds (1 micro, 24-month cap): model {pm * 100:.0f}% (median {tm:.1f} months), NoiseArea {pn * 100:.0f}% (median {tn:.1f} months)")
    print(f"Bootstrap P(Sharpe model - NoiseArea <= 0) = {p_self:.3f};  overlay vs NoiseArea P = {p_ov:.3f}")
    v_self = M_m['sharpe'] > M_na['sharpe'] and p_self < 0.05 and M_m['t'] > 2
    v_prop = M_m['maxdd'] <= 0.6 * M_na['maxdd'] and M_m['usd_yr'] >= 0.6 * M_na['usd_yr'] and pm >= pn + 0.10
    v_ov = M_ov['sharpe'] > M_na['sharpe'] and p_ov < 0.05
    print(f"\nDECISION RULE: self-funded {v_self} | prop {v_prop} | overlay {v_ov}")

    print("\n=== Robustness of the model (and NoiseArea) ===")
    for ticks in (1, 2, 4):
        extra = (ticks - 1) * 0.25 / p_ref * 2
        a, b = rm[hold] - nm[hold] * extra, na[hold] - nna[hold] * extra
        print(f"   {ticks} tick(s)/side: model {fmt(metrics(a, nm[hold], hd, N))}\n                    NoiseArea {fmt(metrics(b, nna[hold], hd, N))}")
    extra2 = 0.25 / p_ref * 2; a2, b2 = rm[hold] - nm[hold] * extra2, na[hold] - nna[hold] * extra2
    p2 = boot_sharpe_diff(hd, a2, b2); m2, n2 = metrics(a2, nm[hold], hd, N), metrics(b2, nna[hold], hd, N)
    pm2, _ = prop_odds(hd, a2 * N); pn2, _ = prop_odds(hd, b2 * N)
    v_self2 = m2['sharpe'] > n2['sharpe'] and p2 < 0.05 and m2['t'] > 2
    v_prop2 = m2['maxdd'] <= 0.6 * n2['maxdd'] and m2['usd_yr'] >= 0.6 * n2['usd_yr'] and pm2 >= pn2 + 0.10
    for lab, x in (('model', rm[hold]), ('NoiseArea', na[hold])):
        s = np.sort(x)[::-1]; nn = len(s)
        print(f"   {lab:9s} best 10% of days removed: ${s[int(.1 * nn):].sum() * N / (nn / 252):+,.0f}/yr (t {tstat(s[int(.1 * nn):]):+.2f}); "
              f"best 15% removed: ${s[int(.15 * nn):].sum() * N / (nn / 252):+,.0f}/yr")
    yr = pd.Series(rm[hold] * N, index=hd).groupby(hd.year).sum(); yn = pd.Series(na[hold] * N, index=hd).groupby(hd.year).sum()
    print("   by year, model:     " + " ".join(f"{y} {v:+,.0f}" for y, v in yr.items()))
    print("   by year, NoiseArea: " + " ".join(f"{y} {v:+,.0f}" for y, v in yn.items()))
    neg_years = int((yr < 0).sum())
    rng = np.random.default_rng(SEED); dds = []
    for _ in range(5000):
        e = np.cumsum(rng.permutation(rm[hold] * N)); dds.append((np.maximum.accumulate(np.r_[0, e]) - np.r_[0, e]).max())
    print(f"   Monte Carlo (5,000 reorderings of the model's days): max DD median ${np.median(dds):,.0f}, 95th ${np.percentile(dds, 95):,.0f}")

    print("\n=== All 24 configurations on the holdout (information only; refitted on discovery) ===")
    rows = []
    for cfg in CONFIGS:
        s, ct, k = cfg; ci, _ = cells(feat, s, disc); se = select(R, NT, ci, disc, cat_cols(ct), k); rr, nn_ = apply(R, NT, ci, se)
        m = metrics(rr[hold], nn_[hold], hd, N); rows.append(dict(scheme=s, catalog=ct, K=k, sharpe=m['sharpe'], usd_yr=m['usd_yr'], maxdd=m['maxdd'], t=m['t']))
    print(pd.DataFrame(rows).to_string(index=False, float_format=lambda x: f"{x:,.2f}"))

    print("\n=== Secondary holdout: Databento MNQ bars, same model ===")
    d2, R2, NT2, c2, _, _, _, full2, done2 = joined('MNQDB')
    need = sorted({j for v in sel.values() for j, _ in v} | {A_DEF})
    if not all(done2[j] for j in need): print("   (variants missing in the MNQDB build)")
    feat2 = features(full2).reindex(d2); cid2, _ = cells(feat2, scheme, between(d2, DISC))
    h2 = between(d2, HOLD); r2, n2_ = apply(R2, NT2, cid2, sel)
    M2m, M2n = metrics(r2[h2], n2_[h2], d2[h2], N), metrics(R2[h2, A_DEF], NT2[h2, A_DEF].astype(float), d2[h2], N)
    print(f"   model      {fmt(M2m)}\n   NoiseArea  {fmt(M2n)}")

    verdict = 'DEAD ON THIS DATA'
    if v_self or v_prop or v_ov:
        verdict = ' + '.join(x for x, v in (('USABLE (self-funded)', v_self), ('USABLE (prop)', v_prop), ('USABLE AS OVERLAY', v_ov)) if v)
        if not ((v_self and v_self2) or (v_prop and v_prop2) or v_ov) or neg_years > 2 or M2m['sharpe'] <= M2n['sharpe']:
            verdict = 'PROMISING, NOT PROVEN (' + verdict + ' failed a robustness check)'
    print(f"\nVERDICT: {verdict}")
    np.savez_compressed(os.path.join(WORK, 'holdout_series.npz'), dates=hd.values.astype('datetime64[D]'), model=rm[hold], model_n=nm[hold],
                        noisearea=na[hold], overlay=ro[hold], cid=cid[hold])


if __name__ == '__main__':
    stage = sys.argv[1] if len(sys.argv) > 1 else ''
    if stage == 'build':
        for nm_ in ('NQ', 'MNQ'): build(nm_)
        audit()
    elif stage == 'build_db':
        build('MNQDB')
    elif stage == 'discover': discover()
    elif stage == 'holdout': holdout()
    else: print(__doc__)
