r"""Research-grade source strategies (Part A) and a self-improving automated rule search (Part B), per RULEMINE_PLAN.txt.
    python rulemine.py audit     deflated-Sharpe null simulation; rule-engine and Part A recomputation checks (no results)
    python rulemine.py run       Part A, then Part B rounds 1-4 (the holdout is touched once, at the end)
"""
import os, sys, json, math, itertools, ctypes
import numpy as np, pandas as pd
from scipy.stats import norm, skew, kurtosis
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
import regime_test as rt
from research_engine import Market

P_REF = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
C2 = 2 * 0.5 / P_REF                               # round-trip cost as a return (1 tick + $0.50 a side at today's price)
N_USD = 2 * P_REF
FOMC = {pd.Timestamp(s.strip()) for s in open(os.path.join(HERE, '..', '..', '..', 'bin', 'Custom', 'Strategies', 'fomc_dates.txt'))
        if s.strip() and not s.startswith('#')}
TS = [585, 600, 630, 660, 720, 780, 840, 900, 930]
XS = ['30', '60', '120', 'close']
DISC, VAL, HOLD = ('2010-07-21', '2019-05-05'), ('2019-06-01', '2022-12-31'), ('2023-01-01', '2026-10-14')


# ------------------------------------------------------------------------------------------------ deflated Sharpe
def dsr(sr, n_obs, sk, ku, n_trials, var_sr):
    """Deflated Sharpe ratio (Bailey & Lopez de Prado 2014); sr per observation, ku = Pearson kurtosis (normal = 3)."""
    g = 0.5772156649
    sr0 = math.sqrt(max(var_sr, 1e-18)) * ((1 - g) * norm.ppf(1 - 1 / n_trials) + g * norm.ppf(1 - 1 / (n_trials * math.e)))
    den = math.sqrt(max(1 - sk * sr + (ku - 1) / 4 * sr * sr, 1e-12))
    return float(norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / den)), sr0


def tstat(x): x = np.asarray(x, float); s = x.std(ddof=1); return x.mean() / (s / np.sqrt(len(x))) if s > 0 else 0.0


# ------------------------------------------------------------------------------------------------ data: per-day features
def file_days(name, keep):
    mk = Market(rt.FILES[name], 1.0, name); sig = mk.sigma(14)
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    rows = []
    for k, x in enumerate(mk.days):
        d = mk.dates[k]
        if not keep(d) or k < 30: continue
        M = x.M; idx = {int(m): i for i, m in enumerate(M)}
        px = raw.get(d, x.O[0])
        rows.append((d, k, x, idx, px))
    return mk, sig, rows


def build():
    feats, rets, meta = [], [], []
    for name, keep in (('NQ', lambda d: d <= rt.JOIN), ('MNQ', lambda d: d > rt.JOIN)):
        mk, sig, rows = file_days(name, keep)
        for d, k, x, idx, px in rows:
            O, H, L, C, vw = x.O, x.H, x.L, x.C, x.vw
            prev = mk.days[k - 1]; pc, ph, pl = prev.C[-1], prev.H.max(), prev.L.min()
            ppc = mk.days[k - 2].C[-1]; c5 = mk.days[k - 6].C[-1]
            o0 = O[0]
            day = dict(date=d, src=name, px=px, gap=(o0 - pc) / px, prevret=(pc - ppc) / px, ret5=(pc - c5) / px,
                       first30=((C[idx[600]] - o0) / px) if 600 in idx else np.nan, mon=float(d.weekday() == 0),
                       fri=float(d.weekday() == 4), fomc=float(d in FOMC))
            for T in TS:
                if T not in idx or T + 5 not in idx: continue
                i = idx[T]; s_ = sig[k, x.slot[i]]
                f = dict(ret_open=(C[i] / o0 - 1) / s_ if s_ > 0 else np.nan, vwap_side=np.sign(C[i] - vw[i]),
                         vwap_dist=(C[i] - vw[i]) / (C[i] * s_) if s_ > 0 else np.nan,
                         last30=((C[i] - C[idx[T - 30]]) / px) if (T - 30) in idx else np.nan,
                         pd_pos=(1.0 if C[i] > ph else (-1.0 if C[i] < pl else 0.0)), pc_side=np.sign(C[i] - pc))
                for nm, last in (('or15', 585), ('or30', 600), ('or60', 630)):
                    if T >= last and last in idx:
                        j = idx[last]; hi, lo = H[:j + 1].max(), L[:j + 1].min()
                        f[nm] = 1.0 if C[i] > hi else (-1.0 if C[i] < lo else 0.0)
                    else: f[nm] = np.nan
                e = O[idx[T + 5]]
                for X in XS:
                    xm = 955 if X == 'close' else T + int(X)
                    if xm > 955 or xm not in idx: continue
                    rets.append((len(meta), T, X, (C[idx[xm]] - e) / px))
                feats.append((len(meta), T, f))
            meta.append(day)
    daily = pd.DataFrame(meta)
    full_dates = pd.DatetimeIndex(daily.date)
    # regime-test daily features (ER10, VOLR, ONR) on the joined series
    dates, R, NT, c, p_ref, px, P, full, _ = rt.joined('MNQ')
    rf = rt.features(full)
    for col in ('ER10', 'VOLR', 'ONR'): daily[col] = rf[col].reindex(full_dates).values
    nD = len(daily); iT = {T: n for n, T in enumerate(TS)}; iX = {X: n for n, X in enumerate(XS)}
    RET = np.full((nD, len(TS), len(XS)), np.nan)
    for r, T, X, v in rets: RET[r, iT[T], iX[X]] = v
    tf = {}
    for r, T, f in feats:
        for kk, vv in f.items(): tf.setdefault(kk, np.full((nD, len(TS)), np.nan))[r, iT[T]] = vv
    return daily, RET, tf


# ------------------------------------------------------------------------------------------------ literals
NUM_T = ['ret_open', 'vwap_dist', 'last30']                                     # numeric, depend on T
CAT_T = {'vwap_side': (1, -1), 'pd_pos': (1, 0, -1), 'pc_side': (1, -1), 'or15': (1, 0, -1), 'or30': (1, 0, -1), 'or60': (1, 0, -1)}
NUM_D = ['gap', 'first30', 'prevret', 'ret5', 'ER10', 'VOLR', 'ONR']
FLAG_D = ['mon', 'fri', 'fomc']
CUTS = {'low33': (None, 1 / 3), 'mid33': (1 / 3, 2 / 3), 'high33': (2 / 3, None),
        'low25': (None, .25), 'high25': (.75, None), 'low50': (None, .5), 'high50': (.5, None)}


def literal_masks(daily, tf, disc_mask, cuts=('low33', 'mid33', 'high33')):
    """{name: bool array [days, nT]}; numeric cut points from discovery days only (per T for T-dependent measures)."""
    nD = len(daily); out = {}
    for f in NUM_T:
        v = tf[f]
        for cname in cuts:
            lo, hi = CUTS[cname]; m = np.zeros_like(v, dtype=bool)
            for t in range(len(TS)):
                ref = v[disc_mask, t]; ref = ref[~np.isnan(ref)]
                if len(ref) < 50: continue
                a = -np.inf if lo is None else np.quantile(ref, lo); b = np.inf if hi is None else np.quantile(ref, hi)
                col = v[:, t]; m[:, t] = (col >= a) & (col < b) & ~np.isnan(col)
            out[f'{f}:{cname}'] = m
    for f, levels in CAT_T.items():
        v = tf[f]
        for lv in levels: out[f'{f}={lv:+.0f}'] = (v == lv)
    for f in NUM_D:
        v = daily[f].values.astype(float); ref = v[disc_mask & ~np.isnan(v)]
        for cname in cuts:
            lo, hi = CUTS[cname]
            a = -np.inf if lo is None else np.quantile(ref, lo); b = np.inf if hi is None else np.quantile(ref, hi)
            m = (v >= a) & (v < b) & ~np.isnan(v); mm = np.repeat(m[:, None], len(TS), axis=1)
            if f == 'first30': mm[:, [i for i, T in enumerate(TS) if T < 600]] = False      # known only from 10:00
            out[f'{f}:{cname}'] = mm
    for f in FLAG_D:
        m = daily[f].values == 1; out[f'{f}'] = np.repeat(m[:, None], len(TS), axis=1)
    return out


def rule_series(RET, mask_dt, t, x, d):
    """Daily net return of a rule: d x RET[:, t, x] - cost on days where the mask holds and the return exists."""
    r = RET[:, t, x]; on = mask_dt[:, t] & ~np.isnan(r)
    return np.where(on, d * np.nan_to_num(r) - C2, 0.0), on


def stats(s, mask):
    x = s[mask]; sd = x.std(ddof=1)
    return (x.mean() / sd if sd > 0 else 0.0), len(x)


# ------------------------------------------------------------------------------------------------ Part A
def part_a(lines):
    import youtube_test as yt
    out = {}
    # A1 VWAP trend on MNQ 1-minute
    D = yt.Mins(); dates = [d for d in D.dates if pd.Timestamp('2019-06-01') <= d <= pd.Timestamp('2026-04-09')]
    a1 = {}
    for day in dates:
        i0, i1 = D.days[day]; o, c = D.o[i0:i1 + 1], D.c[i0:i1 + 1]
        tp = (D.h[i0:i1 + 1] + D.l[i0:i1 + 1] + c) / 3; v = D.v[i0:i1 + 1]; vw = np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1)
        n = len(c)
        if n < 30: continue
        sgn = np.sign(c - vw); pos = np.zeros(n)                                # pos[j] held from open j to open j+1
        cur = 0.0
        for j in range(2, n):
            s_ = sgn[j - 1] if j - 1 >= 1 else 0
            if s_ != 0: cur = s_
            pos[j] = cur
        pos[-1] = pos[-1]
        pnl = np.sum(pos[:-1] * (o[1:] - o[:-1])) + pos[-1] * (c[-1] - o[-1])
        sides = np.abs(np.diff(np.r_[0.0, pos])).sum() + abs(pos[-1])
        px = o[0]; a1[day] = pnl / px - sides * C2 / 2
    s = pd.Series(a1); out['A1'] = s
    # daily strategies A2..A5 on the joined NQ + MNQ RTH series
    dts, R, NT, c_, p_ref, px_, P, full, _ = rt.joined('MNQ')
    f = full.copy(); f = f[f.index >= pd.Timestamp('2010-07-21')]
    O, H, L, C, seg = (f[k].values.astype(float) for k in ('O', 'H', 'L', 'C', 'seg'))
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    px = raw.reindex(f.index).values; px = np.where(np.isnan(px), O, px)
    n = len(C); idx = f.index
    def rsi2(cl):
        d_ = np.diff(cl, prepend=cl[0]); up = pd.Series(np.clip(d_, 0, None)).ewm(alpha=1 / 2, adjust=False).mean()
        dn = pd.Series(np.clip(-d_, 0, None)).ewm(alpha=1 / 2, adjust=False).mean(); return (100 - 100 / (1 + up / dn.replace(0, np.nan))).values
    sma = lambda w: pd.Series(C).rolling(w).mean().values
    r2, s200, s5 = rsi2(C), sma(200), sma(5)
    def hold_rule(entry, exitf):
        r = np.zeros(n); pos = False
        for t in range(1, n):
            if seg[t] != seg[t - 1]: pos = False; continue                      # never across the NQ / MNQ join
            if pos: r[t] += (C[t] - C[t - 1]) / px[t]
            if pos and exitf(t): pos = False; r[t] -= C2 / 2
            elif not pos and entry(t): pos = True; r[t] -= C2 / 2
        return pd.Series(r, index=idx)
    out['A2'] = hold_rule(lambda t: r2[t] < 10 and C[t] > s200[t], lambda t: C[t] > s5[t])
    out['A3'] = hold_rule(lambda t: t >= 6 and C[t] <= C[t - 6:t + 1].min() and C[t] > s200[t], lambda t: t >= 6 and C[t] >= C[t - 6:t + 1].max())
    a4 = np.zeros(n)
    for t in range(1, n - 1):
        if seg[t + 1] != seg[t] or seg[t] != seg[t - 1]: continue
        if idx[t].weekday() == 0 and idx[t - 1].weekday() == 4 and C[t] < C[t - 1] and idx[t + 1].weekday() == 1:
            a4[t + 1] += (C[t + 1] - C[t]) / px[t + 1] - C2
    out['A4'] = pd.Series(a4, index=idx)
    a5 = np.zeros(n); absmv = np.abs(np.diff(C, prepend=np.nan)) / px
    for t in range(15, n - 1):
        if seg[t + 1] != seg[t] or seg[t] != seg[t - 1]: continue
        if (C[t] - C[t - 1]) / px[t] < -np.nanmean(absmv[t - 14:t]):
            a5[t + 1] += (O[t + 1] - C[t]) / px[t + 1] - C2
    out['A5'] = pd.Series(a5, index=idx)
    na = pd.Series(R[:, rt.A_DEF], index=dts)
    lines.append("=== PART A - research-grade source strategies (EDGE: full t >= 2.50, MNQ-only t >= 2.0, >= 60% years positive) ===")
    names = {'A1': 'VWAP trend (Zarattini & Aziz), MNQ 1-min 2019-2026', 'A2': 'Connors RSI(2)', 'A3': 'Connors Double 7s',
             'A4': 'Turnaround Tuesday', 'A5': 'overnight after a down day'}
    flags = {}
    for k_, s in out.items():
        s = s[(s.index >= pd.Timestamp('2010-07-21'))]
        mnq = s[s.index > rt.JOIN]; yr = s.groupby(s.index.year).sum()
        t_all, t_m = tstat(s.values), tstat(mnq.values); pos = (yr > 0).mean()
        common = na.index.intersection(s.index); a, b = na.reindex(common).values, s.reindex(common).values
        pb = rt.boot_sharpe_diff(common, a + b, a)
        edge = t_all >= 2.5 and t_m >= 2.0 and pos >= 0.6; add = rt.sharpe(a + b) > rt.sharpe(a) and pb < 0.01; flags[k_] = (edge, add)
        lines.append(f"   {k_} {names[k_]:44s} ${s.sum() * N_USD / (len(s) / 252):+7,.0f}/yr  t {t_all:+.2f}  MNQ-only t {t_m:+.2f}  years + {pos * 100:.0f}%"
                     f"  | NoiseArea + it Sharpe {rt.sharpe(a + b):+.2f} vs {rt.sharpe(a):+.2f} (P {pb:.3f})  -> EDGE {edge}, ADDITION {add}")
    return out, flags


# ------------------------------------------------------------------------------------------------ Part B
def part_b(lines, daily, RET, tf):
    dates = pd.DatetimeIndex(daily.date)
    disc = (dates >= DISC[0]) & (dates <= DISC[1]); val = (dates >= VAL[0]) & (dates <= VAL[1]); hold = (dates >= HOLD[0]) & (dates <= HOLD[1])
    lits = literal_masks(daily, tf, disc)
    lines.append(f"\n=== PART B - automated rule search: {len(lits)} literals, {len(TS)} decision times, {len(XS)} exits, 2 directions ===")
    lines.append(f"   days: discovery {disc.sum()} (NQ 2010-2019), validation {val.sum()} (MNQ 2019-2022), holdout {hold.sum()} (MNQ 2023-2026)")
    tried = []                                                                  # (name, sr_disc, n_obs, skew, kurt, t, trades, series spec)
    def evaluate(spec, mask):
        lit_names, ti, xi, d = spec
        m = np.ones((len(dates), len(TS)), bool)
        for ln in lit_names: m &= lit_pool[ln]
        s, on = rule_series(RET, m, ti, xi, d)
        x = s[mask]; sd = x.std(ddof=1); n_tr = int(on[mask].sum())
        sr = x.mean() / sd if sd > 0 else 0.0
        return sr, len(x), float(skew(x)) if sd > 0 else 0.0, float(kurtosis(x, fisher=False)) if sd > 0 else 3.0, n_tr, s
    lit_pool = dict(lits)
    # round 1: singles
    singles = []
    for ln in lits:
        for ti in range(len(TS)):
            for xi in range(len(XS)):
                if np.isnan(RET[:, ti, xi]).all(): continue
                for d in (1, -1):
                    sr, n, sk, ku, ntr, _ = evaluate(((ln,), ti, xi, d), disc)
                    if ntr < 100: continue
                    singles.append((((ln,), ti, xi, d), sr, n, sk, ku, ntr))
    # round 2: pairs grown from the 200 best singles
    best = sorted(singles, key=lambda z: -z[1])[:200]; pairs = []; seen = set()
    for (spec, sr, *_r) in best:
        (l1,), ti, xi, d = spec
        for l2 in lits:
            if l2 == l1 or l2.split(':')[0].split('=')[0] == l1.split(':')[0].split('=')[0]: continue
            key = (tuple(sorted((l1, l2))), ti, xi, d)
            if key in seen: continue
            seen.add(key); sr2, n2, sk2, ku2, ntr2, _ = evaluate((key[0], ti, xi, d), disc)
            if ntr2 < 100: continue
            pairs.append(((key[0], ti, xi, d), sr2, n2, sk2, ku2, ntr2))
    allr = singles + pairs; N = len(allr); var_sr = np.var([z[1] for z in allr], ddof=1)
    surv = []
    for (spec, sr, n, sk, ku, ntr) in allr:
        p, sr0 = dsr(sr, n, sk, ku, N, var_sr)
        if p > 0.95 and sr > 0: surv.append((spec, sr, p, ntr))
    lines.append(f"   rounds 1-2: {len(singles)} single-literal rules + {len(pairs)} pairs = {N} rules tried on discovery; deflated-Sharpe bar "
                 f"(N = {N}): expected best Sharpe by luck {dsr(0, 100, 0, 3, N, var_sr)[1] * np.sqrt(252):.2f} a year; survivors {len(surv)}")
    top_disc = sorted(allr, key=lambda z: -z[1])[:5]
    for (spec, sr, n, sk, ku, ntr) in top_disc:
        lines.append(f"      best on discovery: {fmt_spec(spec)}  Sharpe {sr * np.sqrt(252):+.2f}/yr, {ntr} trades, DSR {dsr(sr, n, sk, ku, N, var_sr)[0]:.3f}")
    # round 3: validation
    val_ok = []
    for (spec, sr, p, ntr) in surv:
        srv, nv, skv, kuv, ntrv, s = evaluate(spec, val)
        tv = srv * np.sqrt(nv)
        val_ok.append((spec, sr, tv, ntrv, 1 - norm.cdf(tv)))
    val_ok.sort(key=lambda z: z[4]); m_ = len(val_ok); passed = []
    for i, z in enumerate(val_ok):
        if z[4] <= 0.05 / (m_ - i) and z[2] >= 2.0: passed.append(z)
        else: break
    lines.append(f"   round 3 (validation MNQ 2019-2022, Holm over {m_}): {len(passed)} rule(s) pass")
    for z in val_ok[:8]:
        lines.append(f"      {fmt_spec(z[0])}: discovery Sharpe {z[1] * np.sqrt(252):+.2f}, validation t {z[2]:+.2f} ({z[3]} trades)")
    if not passed:
        lines.append("   -> nothing reaches the holdout; Part B stops here (the holdout stays untouched)")
        return []
    # round 4: self-improvement around each validated rule on discovery + validation
    lits_n = literal_masks(daily, tf, disc, cuts=tuple(CUTS)); lit_pool.update(lits_n)
    dv = disc | val; finals = []; n4 = 0; cand4 = []
    for z in passed:
        spec = z[0]; lns, ti, xi, d = spec
        neigh = set()
        for t2 in {max(0, ti - 1), ti, min(len(TS) - 1, ti + 1)}:
            for x2 in range(len(XS)):
                alts = []
                for ln in lns:
                    base = ln.split(':')[0]
                    alts.append([ln] + ([f"{base}:{c}" for c in CUTS if f"{base}:{c}" in lit_pool and f"{base}:{c}" != ln] if ':' in ln else []))
                for combo in itertools.product(*alts): neigh.add((tuple(combo), t2, x2, d))
        fam = []
        for sp in neigh:
            if np.isnan(RET[:, sp[1], sp[2]]).all(): continue
            sr, n, sk, ku, ntr, _ = evaluate(sp, dv)
            if ntr < 100: continue
            fam.append((sp, sr, n, sk, ku, ntr)); n4 += 1
        cand4.append(fam)
    N4 = N + n4; var4 = np.var([z[1] for fam in cand4 for z in fam] + [z[1] for z in allr], ddof=1)
    for fam in cand4:
        bestf = max(fam, key=lambda z: z[1]); p, _ = dsr(bestf[1], bestf[2], bestf[3], bestf[4], N4, var4)
        finals.append((bestf[0], bestf[1], p, bestf[5]))
    finals = sorted(finals, key=lambda z: -z[1])[:10]
    lines.append(f"   round 4 (self-improvement: {n4} neighbouring rules on discovery + validation, N = {N4}): {len(finals)} final rule(s)")
    # holdout, once
    k = len(finals); zbar = max(2.0, norm.ppf(1 - 0.05 / k))
    dts_, R_, *_ = rt.joined('MNQ'); na = pd.Series(R_[:, rt.A_DEF], index=dts_)
    res = []
    for (spec, sr, p, ntr) in finals:
        srh, nh, skh, kuh, ntrh, s = evaluate(spec, hold); th = srh * np.sqrt(nh)
        sh = pd.Series(s[hold], index=dates[hold]); _, on = rule_series(RET, np.ones((len(dates), len(TS)), bool) & np.all([lit_pool[l] for l in spec[0]], axis=0), spec[1], spec[2], spec[3])
        ex2 = sh - on[hold] * (0.25 / P_REF) * 2
        common = na.index.intersection(sh.index); a, b = na.reindex(common).values, sh.reindex(common).values
        pb = rt.boot_sharpe_diff(common, a + b, a)
        ok = th >= zbar and ex2.sum() > 0
        res.append((spec, ok, th, pb))
        lines.append(f"      {fmt_spec(spec)}: disc+val DSR {p:.3f} | HOLDOUT t {th:+.2f} (bar {zbar:.2f}), ${sh.sum() * N_USD / (len(sh) / 252):+,.0f}/yr, "
                     f"{ntrh} trades, at 2 ticks ${ex2.sum() * N_USD / (len(sh) / 252):+,.0f}/yr | NoiseArea + it P {pb:.3f} -> {'PASS' if ok else 'fail'}")
    return res


def fmt_spec(spec):
    lns, ti, xi, d = spec
    return f"{'long' if d > 0 else 'short'} at {TS[ti] // 60:02d}:{TS[ti] % 60:02d} -> {XS[xi]}{'' if XS[xi] == 'close' else ' min'} if " + " & ".join(lns)


# ------------------------------------------------------------------------------------------------ audits
def audit():
    rng = np.random.default_rng(1)
    hits = 0; B = 400; N = 1000; T = 1500
    for _ in range(B):
        X = rng.standard_normal((N, T)) * 0.01; srs = X.mean(1) / X.std(1, ddof=1); j = int(np.argmax(srs))
        p, _ = dsr(srs[j], T, float(skew(X[j])), float(kurtosis(X[j], fisher=False)), N, np.var(srs, ddof=1)); hits += p > 0.95
    print(f"AUDIT deflated Sharpe under pure noise: best of {N} random rules clears 0.95 in {hits / B * 100:.1f}% of {B} batches (target about 5%)")
    daily, RET, tf = build(); dates = pd.DatetimeIndex(daily.date); disc = (dates >= DISC[0]) & (dates <= DISC[1])
    lits = literal_masks(daily, tf, disc); names = list(lits)
    # recompute 5 random single-literal rules from the bars with a plain loop
    bad = 0
    mkq = {nm: Market(rt.FILES[nm], 1.0, nm) for nm in ('NQ', 'MNQ')}
    for _ in range(5):
        ln = names[rng.integers(len(names))]; ti = int(rng.integers(len(TS))); xi = int(rng.integers(len(XS))); d = int(rng.choice([1, -1]))
        s, on = rule_series(RET, lits[ln], ti, xi, d)
        for r in rng.choice(np.where(on)[0], min(20, on.sum()), replace=False) if on.sum() else []:
            row = daily.iloc[r]; mk = mkq[row.src]; k = list(mk.dates).index(row.date); x = mk.days[k]
            M = list(x.M); T_ = TS[ti]; xm = 955 if XS[xi] == 'close' else T_ + int(XS[xi])
            want = d * (x.C[M.index(xm)] - x.O[M.index(T_ + 5)]) / row.px - C2
            if abs(want - s[r]) > 1e-12: bad += 1
    print(f"AUDIT rule engine vs a plain loop on 100 random rule-days: {bad} differences")
    print(f"literals {len(lits)}; days {len(daily)}")


def main():
    lines = []
    a, flags = part_a(lines)
    daily, RET, tf = build()
    res = part_b(lines, daily, RET, tf)
    lines.append(f"\nSUMMARY: Part A EDGE {[k for k, v in flags.items() if v[0]] or 'none'}, ADDITION {[k for k, v in flags.items() if v[1]] or 'none'}; "
                 f"Part B holdout PASS {[fmt_spec(s) for s, ok, *_ in res if ok] or 'none'}")
    print("\n".join(lines))


if __name__ == '__main__':
    {'audit': audit, 'run': main}.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: print(__doc__))()
