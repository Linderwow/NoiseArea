r"""REGIME3 (REGIME3_PLAN.txt, pre-registered 2026-10-26): Patrick's way part 3 - a later market-state call (10:30/11:00/12:00), a
chart-shape model and a meta-label, used as a GATE on NoiseArea (stand down at the call on days the call says "range").
    python regime3.py audit      A1-A3 (must pass before anything else is read)
    python regime3.py discover   72 configurations fitted on FIT, scored on VALIDATE; the chosen one (no holdout figures)
    python regime3.py holdout    the single holdout run, oracle, all configurations (information), A4
Needs regime_work\ (regime_test.py build / build_db) for the part-2 daily features. Writes regime3_output.txt and
regime_work\regime3_model.json only. Runs at below-normal priority on one core (NinjaTrader shares this PC).
"""
import os, sys, json, datetime as dt
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, regime_test as rt, regime_ml as ML
from research_engine import WARMUP

CALLS = (630, 660, 720); CALL_TXT = {630: '10:30', 660: '11:00', 720: '12:00'}
LABELS = ('eff', 'meta'); FSETS = ('tab', 'chart'); MODELS = ('logit', 'stumps', 'trees2'); TAUS = (0.4, 0.5)
CHECKS = set(range(600, 931, 30))
TAB_AT = ['EFFT', 'RTZ', 'MOVEZT', 'VOLTZ', 'POS', 'BANDZ', 'VWAPZ', 'NCHG', 'RNGPOS']
TAB_COLS = ML.F930 + TAB_AT
CHART_COLS = ([f'P{q}' for q in range(1, 13)] + ['EXT_WITH', 'EXT_AGAINST', 'GAP0'] +
              [f'{a}{j}' for j in range(1, 11) for a in ('BODY', 'UPW', 'DNW', 'GAP')])
CONFIGS = [(t, lb, fs, mo, tau) for t in CALLS for lb in LABELS for fs in FSETS for mo in MODELS for tau in TAUS]
OUT = os.path.join(HERE, 'regime3_output.txt'); MODEL_JSON = os.path.join(rt.WORK, 'regime3_model.json')
_log = []


def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); _log.append(s)


def below_normal():
    try:
        import ctypes; k = ctypes.windll.kernel32; k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------ one session
def day_pass(x, pc, sg):
    """The live rules (nav_lab.fam_V defaults) over one session. Returns gate-off points, round trips, and per call time
    {minute: (points, round trips, call bar index, |position| after NoiseArea's own decision there, changes so far)} when
    standing down at that call (an open position exits at the next bar's open, no new entries)."""
    O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
    hi, lo = max(O[0], pc), min(O[0], pc); flat_i = nb - 1 if M[-1] >= 960 else nb - 2
    s = 0.0; ref = 0.0; p = 0.0; vol = 0.0; nchg = 0; sd = {}
    for i in range(flat_i):
        if M[i] not in CHECKS or np.isnan(sg[i]): continue
        c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
        if s == 0: new = 1 if c > ub else (-1 if c < lb else 0)
        elif s == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
        else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
        if M[i] in CALLS:
            ps, vs = (p + s * (O[i + 1] - ref), vol + abs(s)) if s != 0 else (p, vol)
            sd[int(M[i])] = (ps, vs / 2, i, abs(new), nchg + int(new != s))
        if new != s:
            px = O[i + 1]; p += s * (px - ref); vol += abs(new - s); s = float(new); ref = px; nchg += 1
    if s != 0: p += s * (C[flat_i] - ref); vol += abs(s)
    return p, vol / 2, sd


def decisions_to(x, pc, sg, t):
    """NoiseArea's position after its own decision at the call bar t and its position changes so far - from bars <= t only."""
    O, C, M, vw = x.O, x.C, x.M, x.vw; hi, lo = max(O[0], pc), min(O[0], pc); s = 0; nchg = 0
    for i in range(len(M)):
        if M[i] > t: break
        if M[i] not in CHECKS or np.isnan(sg[i]): continue
        c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
        if s == 0: new = 1 if c > ub else (-1 if c < lb else 0)
        elif s == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
        else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
        if new != s: s = new; nchg += 1
    return s, nchg


def at_call(x, pc, sg, t):
    """What the gate may know at the close of call bar t, from this session's bars <= t (None when it cannot act)."""
    idx = np.where(x.M == t)[0]
    if not len(idx) or np.isnan(sg[idx[0]]): return None
    i = int(idx[0]); O0 = x.O[0]; C, H, L, V = x.C[:i + 1], x.H[:i + 1], x.L[:i + 1], x.V[:i + 1]
    pos, nchg = decisions_to(x, pc, sg, t)
    c = C[-1]; sgi = sg[i]; hi, lo = max(O0, pc), min(O0, pc); ub, lb = hi * (1 + sgi), lo * (1 - sgi)
    path = np.abs(np.diff(np.r_[O0, C])).sum(); hh, ll = H.max(), L.min()
    raw = [abs(c - O0) / path if path > 0 else 0.0, hh - ll, abs(c / O0 - 1) / sgi, V.sum(), abs(pos),
           max(c - ub, lb - c) / (O0 * sgi), abs(c - x.vw[i]) / (O0 * sgi), nchg,
           abs((c - ll) / (hh - ll) - 0.5) if hh > ll else 0.0]                 # TAB_AT order (RT, VOLT made z-scores later)
    sgn = 1.0 if c >= O0 else -1.0
    q = np.clip(np.round(np.arange(1, 13) * (i + 1) / 12).astype(int) - 1, 0, i)
    up, dn = (hh / O0 - 1) / sgi, (ll / O0 - 1) / sgi
    shape = list(sgn * (C[q] / O0 - 1) / sgi) + ([up, dn] if sgn > 0 else [-dn, -up])
    return i, raw, sgn, shape


def cut(x, i):
    """A copy of the session holding only bars 0..i (for the causality audit)."""
    class D: pass
    y = D()
    for a in ('O', 'H', 'L', 'C', 'V', 'M', 'slot'): setattr(y, a, getattr(x, a)[:i + 1].copy())
    tp = (y.H + y.L + y.C) / 3; y.vw = np.cumsum(tp * y.V) / np.maximum(np.cumsum(y.V), 1); y.nb = i + 1
    return y


# ------------------------------------------------------------------------------------------------ per market, joined
def market_table(mk):
    sig = mk.sigma(14); n = mk.n; nc = len(CALLS)
    P = np.full(n, np.nan); NT = np.zeros(n); PS = np.full((n, nc), np.nan); NS = np.zeros((n, nc)); OK = np.zeros((n, nc), bool)
    TAB = np.full((n, nc, len(TAB_AT)), np.nan); CH = np.full((n, nc, len(CHART_COLS)), np.nan); YE = np.full((n, nc), np.nan)
    cand = np.array([(x.O[0], x.H.max(), x.L.min(), x.C[-1]) for x in mk.days])
    R14 = pd.Series(cand[:, 1] - cand[:, 2]).rolling(14).mean().shift(1).values
    for k, x in enumerate(mk.days):
        if k < 14 or np.isnan(mk.pc[k]): continue
        sg = sig[k, x.slot]; p, nt, sd = day_pass(x, mk.pc[k], sg); P[k], NT[k] = p, nt
        flat_i = x.nb - 1 if x.M[-1] >= 960 else x.nb - 2
        for j, t in enumerate(CALLS):
            a = at_call(x, mk.pc[k], sg, t)
            if a is None or t not in sd:
                PS[k, j], NS[k, j] = p, nt; continue
            ps, ns, i, pos_, nchg_ = sd[t]
            assert i == a[0] and pos_ == a[1][4] and nchg_ == a[1][7], "decisions_to disagrees with day_pass"
            PS[k, j], NS[k, j], OK[k, j] = ps, ns, True; TAB[k, j] = a[1]
            sgn, r14 = a[2], R14[k]
            daily = [sgn * (x.O[0] - mk.pc[k]) / r14]
            for jj in range(1, 11):
                o, h, l, c = cand[k - jj]; up, dn = (h - max(o, c)) / r14, (min(o, c) - l) / r14
                if sgn < 0: up, dn = dn, up
                daily += [sgn * (c - o) / r14, up, dn, sgn * (o - mk.pc[k - jj]) / r14 if np.isfinite(mk.pc[k - jj]) else np.nan]
            CH[k, j] = a[3] + daily
            cc = x.C[i:flat_i + 1]; ch = np.abs(np.diff(cc)).sum(); YE[k, j] = abs(cc[-1] - cc[0]) / ch if ch > 0 else 0.0
    for j in range(nc):                                  # range and volume so far as multiples of their 14-session mean then
        for col in (1, 3):
            v = pd.Series(TAB[:, j, col]); TAB[:, j, col] = (v / v.rolling(14, min_periods=10).mean().shift(1)).values
    return dict(P=P, NT=NT, PS=PS, NS=NS, OK=OK, TAB=TAB, CH=CH, YE=YE, R14=R14, cand=cand)


def build(second='MNQ'):
    dates, _, _, _, p_ref, f = ML.feature_table(second)
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    c = 0.5 / p_ref; parts = []
    for nm, keep in (('NQ', lambda d: d <= rt.JOIN), (second, lambda d: d > rt.JOIN)):
        mk = N.market(nm); T = market_table(mk); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d); o = np.array([x.O[0] for x in mk.days])
        px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(dict(dates=d[sel], px=px, **{k: v[sel] for k, v in T.items() if k not in ('R14', 'cand')}))
    J = {k: (parts[0][k].append(parts[1][k]) if k == 'dates' else np.concatenate([parts[0][k], parts[1][k]])) for k in parts[0]}
    assert J['dates'].equals(dates), "joined dates differ from regime_ml.feature_table"
    px = J['px'][:, None]
    J['Rf'] = np.nan_to_num(J['P']) / J['px'] - J['NT'] * 2 * c
    J['Rs'] = np.where(np.isnan(J['PS']), np.nan_to_num(J['P'])[:, None], J['PS']) / px - J['NS'] * 2 * c
    J['F930'] = f[ML.F930].values.astype(float); J['p_ref'] = p_ref; J['c'] = c
    return J


def X_of(J, j, fs):
    return np.c_[J['F930'], J['TAB'][:, j]] if fs == 'tab' else J['CH'][:, j]


def labels(J, j, med):
    ye = np.where(J['OK'][:, j], (J['YE'][:, j] >= med).astype(float), np.nan)
    d = J['Rf'] - J['Rs'][:, j]                          # net value of continuing past the call
    ym = np.where(J['OK'][:, j] & (np.abs(d) > 1e-12), (d > 0).astype(float), np.nan)
    return {'eff': ye, 'meta': ym}


# ------------------------------------------------------------------------------------------------ models
class Trees2:
    """Boosted depth-2 trees, logistic loss, Newton leaves; with depth=1, min_leaf=0 identical to regime_ml.Stumps."""
    def fit(self, X, y, rounds=150, lr=0.1, nthr=16, depth=2, min_leaf=20):
        self.prep = ML.Prep(X); Z = self.prep(X); p0 = np.clip(y.mean(), 1e-3, 1 - 1e-3); self.f0 = np.log(p0 / (1 - p0))
        thr = [np.unique(np.quantile(Z[:, j], np.arange(1, nthr + 1) / (nthr + 1))) for j in range(Z.shape[1])]
        F = np.full(len(y), self.f0); self.trees = []; self.lr = lr
        for _ in range(rounds):
            p = ML.sigmoid(F); g = y - p; h = np.maximum(p * (1 - p), 1e-6)
            tr = self._grow(Z, g, h, np.ones(len(y), bool), thr, depth, min_leaf); self.trees.append(tr)
            F += lr * self._apply(tr, Z)
        return self

    def _grow(self, Z, g, h, nd, thr, depth, min_leaf):
        G, H = g[nd].sum(), h[nd].sum(); best = None
        if depth > 0:
            for j in range(Z.shape[1]):
                for t in thr[j]:
                    L = nd & (Z[:, j] <= t); R = nd & ~(Z[:, j] <= t)
                    GL, HL = g[L].sum(), h[L].sum(); GR, HR = g[R].sum(), h[R].sum()
                    if HL < 1e-3 or HR < 1e-3 or L.sum() < min_leaf or R.sum() < min_leaf: continue
                    gain = GL * GL / HL + GR * GR / HR
                    if best is None or gain > best[0]: best = (gain, j, t, L, R)
        if best is None: return ('leaf', G / H if H > 0 else 0.0)
        _, j, t, L, R = best
        return ('split', j, t, self._grow(Z, g, h, L, thr, depth - 1, min_leaf), self._grow(Z, g, h, R, thr, depth - 1, min_leaf))

    def _apply(self, tr, Z):
        if tr[0] == 'leaf': return np.full(len(Z), tr[1])
        _, j, t, a, b = tr; m = Z[:, j] <= t; return np.where(m, self._apply(a, Z), self._apply(b, Z))

    def predict(self, X):
        Z = self.prep(X); F = np.full(len(Z), self.f0)
        for tr in self.trees: F += self.lr * self._apply(tr, Z)
        return ML.sigmoid(F)


def fit_model(mo, X, y):
    return (ML.Logit() if mo == 'logit' else ML.Stumps() if mo == 'stumps' else Trees2()).fit(X, y)


def gate(J, j, p, tau):
    off = J['OK'][:, j] & (p < tau)
    return np.where(off, J['Rs'][:, j], J['Rf']), np.where(off, J['NS'][:, j], J['NT']), off


def simplicity(cfg):
    t, lb, fs, mo, tau = cfg
    return (CALLS.index(t), lb == 'meta', fs == 'chart', MODELS.index(mo), abs(tau - 0.5))


def name(cfg): t, lb, fs, mo, tau = cfg; return f"{CALL_TXT[t]} {lb:4s} {fs:5s} {mo:6s} tau {tau}"


def predictions(J, train, meds):
    """All 36 classifiers fitted on `train`; returns {(t, label, fset, model): p over every row}."""
    out = {}
    for j, t in enumerate(CALLS):
        Y = labels(J, j, meds[str(t)])
        for lb in LABELS:
            y = Y[lb]; m = train & np.isfinite(y)
            for fs in FSETS:
                X = X_of(J, j, fs)
                for mo in MODELS: out[(t, lb, fs, mo)] = fit_model(mo, X[m], y[m]).predict(X)
    return out


# ------------------------------------------------------------------------------------------------ commands
def audit():
    pr("A1 stand-down simulator, gate never acting, vs nav_lab.fam_V defaults:")
    worst = 0.0
    for nm in ('NQ', 'MNQ', 'MNQDB'):
        mk = N.market(nm); T = market_table(mk); p0, n0 = N.fam_V(mk)
        dp = np.abs(np.nan_to_num(T['P']) - np.nan_to_num(p0)).max(); dn = np.abs(T['NT'] - n0).max(); worst = max(worst, dp, dn)
        pr(f"   {nm:5s} {mk.n} sessions: max |points diff| {dp:.1e}, max |trades diff| {dn:.1e}; call bar usable "
           + ', '.join(f"{CALL_TXT[t]} {T['OK'][:, j].mean() * 100:.1f}%" for j, t in enumerate(CALLS)))
    J = build('MNQ'); ref, _ = N.series('MNQ', fn=N.fam_V)
    ds = np.abs(J['Rf'] - ref.values).max(); same = J['dates'].equals(ref.index)
    pr(f"   joined net series vs nav_lab.series('MNQ', fam_V): {len(ref)} sessions, dates identical {same}, max diff {ds:.1e}")
    a1 = worst < 1e-9 and ds < 1e-12 and same
    pr(f"   A1 {'PASSED' if a1 else 'FAILED'}")
    pr("A2 causality: features recomputed from a copy of the session cut at the call bar (200 random sessions per file):")
    a2 = True; rng = np.random.default_rng(rt.SEED)
    for nm in ('NQ', 'MNQ', 'MNQDB'):
        mk = N.market(nm); sig = mk.sigma(14); bad = 0; checked = 0
        cand = [(x.O[0], x.H.max(), x.L.min(), x.C[-1]) for x in mk.days]
        for k in rng.choice(np.arange(30, mk.n), 200, replace=False):
            x = mk.days[k]; sg = sig[k, x.slot]
            if np.isnan(mk.pc[k]): continue
            r14 = np.mean([cand[q][1] - cand[q][2] for q in range(k - 14, k)])
            r14_tab = pd.Series([c_[1] - c_[2] for c_ in cand]).rolling(14).mean().shift(1).values[k]
            if abs(r14 - r14_tab) > 1e-9: bad += 1
            for t in CALLS:
                a = at_call(x, mk.pc[k], sg, t)
                if a is None: continue
                b = at_call(cut(x, a[0]), mk.pc[k], sg[:a[0] + 1], t); checked += 1
                if not (a[0] == b[0] and np.allclose(a[1], b[1], rtol=0, atol=0) and a[2] == b[2] and np.allclose(a[3], b[3], rtol=0, atol=0)):
                    bad += 1
        pr(f"   {nm:5s}: {checked} session x call pairs compared, {bad} mismatches"); a2 &= bad == 0
    pr(f"   A2 {'PASSED' if a2 else 'FAILED'}")
    pr("A3 models:")
    fit = rt.between(J['dates'], rt.FIT); j = CALLS.index(660)
    med = float(np.nanmedian(np.where(J['OK'][:, j], J['YE'][:, j], np.nan)[fit])); y = labels(J, j, med)['eff']; m = fit & np.isfinite(y)
    X = X_of(J, j, 'chart')[m]; yy = y[m]
    ps = ML.Stumps().fit(X, yy).predict(X); pt = Trees2().fit(X, yy, depth=1, min_leaf=0).predict(X)
    d3 = np.abs(ps - pt).max(); pr(f"   trees2 at depth 1 vs stumps (11:00, chart, eff, FIT): max prediction diff {d3:.1e}")
    Xt = X_of(J, j, 'tab')[m]; lg = ML.Logit().fit(Xt, yy); Z = np.c_[np.ones(len(Xt)), lg.prep(Xt)]; w = np.zeros(Z.shape[1])
    for _ in range(400_000):
        gr = Z.T @ (ML.sigmoid(Z @ w) - yy) + np.r_[0, w[1:]]; w -= 0.5 * gr / len(Z)
        if np.abs(gr).max() < 1e-9: break
    dl = np.abs(lg.w - w).max(); pr(f"   logistic Newton vs gradient descent (11:00, tab, eff, FIT): max coefficient diff {dl:.1e}")
    a3 = d3 < 1e-9 and dl < 1e-4
    pr(f"   A3 {'PASSED' if a3 else 'FAILED'}")
    pr(f"AUDITS A1-A3: {'ALL PASSED' if a1 and a2 and a3 else 'FAILED - stop'}")


def discover():
    J = build('MNQ'); d = J['dates']; fit, val = rt.between(d, rt.FIT), rt.between(d, rt.VAL); Nusd = 2 * J['p_ref']
    meds = {str(t): float(np.nanmedian(np.where(J['OK'][:, j], J['YE'][:, j], np.nan)[fit])) for j, t in enumerate(CALLS)}
    na = J['Rf']
    pr(f"NoiseArea on VALIDATE: Sharpe {rt.sharpe(na[val]):.2f}, ${na[val].sum() * Nusd / (val.sum() / 252):,.0f}/yr")
    for j, t in enumerate(CALLS):
        Y = labels(J, j, meds[str(t)]); ok = J['OK'][:, j]
        pr(f"   call {CALL_TXT[t]}: rest-of-day efficiency median (FIT) {meds[str(t)]:.3f}; usable on {ok[fit].mean() * 100:.1f}% of FIT days; "
           f"NoiseArea in a position at the call {np.nanmean(J['TAB'][fit & ok, j, 4]) * 100:.0f}%; continuing pays on "
           f"{np.nanmean(Y['meta'][fit]) * 100:.0f}% of FIT days where it matters ({np.isfinite(Y['meta'][fit]).mean() * 100:.0f}% of days)")
    P = predictions(J, fit, meds)
    pr("\nClassifier skill (AUC, 0.5 = coin flip):")
    for (t, lb, fs, mo), p in P.items():
        y = labels(J, CALLS.index(t), meds[str(t)])[lb]
        pr(f"   {CALL_TXT[t]} {lb:4s} {fs:5s} {mo:6s}: FIT {ML.auc(y[fit & np.isfinite(y)], p[fit & np.isfinite(y)]):.3f}  "
           f"VALIDATE {ML.auc(y[val & np.isfinite(y)], p[val & np.isfinite(y)]):.3f}")
    rows = []
    for cfg in CONFIGS:
        j = CALLS.index(cfg[0]); r, n, off = gate(J, j, P[cfg[:4]], cfg[4])
        rows.append(dict(cfg=name(cfg), val_sharpe=rt.sharpe(r[val]), val_usd_yr=r[val].sum() * Nusd / (val.sum() / 252),
                         stood_down=off[val].mean() * 100))
    tab = pd.DataFrame(rows); pr("\n" + tab.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    best = max(CONFIGS, key=lambda c_: (round(tab.val_sharpe[CONFIGS.index(c_)], 6), tuple(-float(v) for v in simplicity(c_))))
    pr(f"\nCHOSEN: {name(best)} (VALIDATE Sharpe {tab.val_sharpe[CONFIGS.index(best)]:.3f} vs NoiseArea {rt.sharpe(na[val]):.3f})")
    json.dump(dict(cfg=list(best), medians=meds), open(MODEL_JSON, 'w'))


def holdout():
    m = json.load(open(MODEL_JSON)); cfg = tuple(m['cfg']); meds = m['medians']; t, lb, fs, mo, tau = cfg; j = CALLS.index(t)
    J = build('MNQ'); d = J['dates']; disc, hold = rt.between(d, rt.DISC), rt.between(d, rt.HOLD); hd = d[hold]; Nusd = 2 * J['p_ref']
    Y = labels(J, j, meds[str(t)]); y = Y[lb]; tr = disc & np.isfinite(y); X = X_of(J, j, fs)
    model = fit_model(mo, X[tr], y[tr]); p = model.predict(X); r, n, off = gate(J, j, p, tau)
    na, nna = J['Rf'], J['NT']
    pr(f"=== HOLDOUT 2019-05-06 .. 2026-10-14 ({hold.sum()} sessions): {name(cfg)}")
    yh = hold & np.isfinite(y)
    pr(f"   classifier AUC on the holdout {ML.auc(y[yh], p[yh]):.3f} (discovery in-sample {ML.auc(y[tr], p[tr]):.3f}); "
       f"NoiseArea stood down on {off[hold].mean() * 100:.0f}% of days")
    Mm, Mn = rt.metrics(r[hold], n[hold], hd, Nusd), rt.metrics(na[hold], nna[hold], hd, Nusd)
    pr(f"   model      {rt.fmt(Mm)}\n   NoiseArea  {rt.fmt(Mn)}")
    pb = rt.boot_sharpe_diff(hd, r[hold], na[hold]); pm, _ = rt.prop_odds(hd, r[hold] * Nusd); pn, _ = rt.prop_odds(hd, na[hold] * Nusd)
    pr(f"   bootstrap P(Sharpe model <= NoiseArea) = {pb:.3f}; 50K evaluation pass odds model {pm * 100:.0f}% vs NoiseArea {pn * 100:.0f}%")
    # secondary holdout: Databento MNQ bars, the same discovery-fitted model
    J2 = build('MNQDB'); h2 = rt.between(J2['dates'], rt.HOLD); p2 = model.predict(X_of(J2, j, fs)); r2, n2, _ = gate(J2, j, p2, tau)
    M2, M2n = rt.metrics(r2[h2], n2[h2], J2['dates'][h2], Nusd), rt.metrics(J2['Rf'][h2], J2['NT'][h2], J2['dates'][h2], Nusd)
    pr(f"   Databento holdout: model {rt.fmt(M2)}\n                      NoiseArea {rt.fmt(M2n)}")
    v_self = Mm['sharpe'] > Mn['sharpe'] and pb < 0.05 / 3 and Mm['t'] > 2 and M2['sharpe'] > M2n['sharpe']
    v_prop = (Mm['maxdd'] <= 0.6 * Mn['maxdd'] and Mm['usd_yr'] >= 0.6 * Mn['usd_yr'] and pm >= pn + 0.10 and M2['maxdd'] <= M2n['maxdd'])
    pr(f"   DECISION RULE: self-funded {v_self} | prop (drawdown) {v_prop}")
    extra = 0.25 / J['p_ref'] * 2
    pr(f"   at 2 ticks/side: model {rt.fmt(rt.metrics(r[hold] - n[hold] * extra, n[hold], hd, Nusd))}\n"
       f"                    NoiseArea {rt.fmt(rt.metrics(na[hold] - nna[hold] * extra, nna[hold], hd, Nusd))}")
    yr = pd.Series((r[hold] - na[hold]) * Nusd, index=hd).groupby(hd.year).sum()
    pr("   by year, model minus NoiseArea $: " + " ".join(f"{k} {v:+,.0f}" for k, v in yr.items()))
    pr("\n   ORACLE (true hindsight label instead of the prediction; the ceiling of a perfect later call):")
    for jj, tt in enumerate(CALLS):
        YY = labels(J, jj, meds[str(tt)]); ok = J['OK'][:, jj]
        for lab, offo in (('eff ', ok & (YY['eff'] == 0)), ('meta', ok & (YY['meta'] == 0))):
            ro = np.where(offo, J['Rs'][:, jj], na); no_ = np.where(offo, J['NS'][:, jj], nna)
            pr(f"   {CALL_TXT[tt]} {lab}: {rt.fmt(rt.metrics(ro[hold], no_[hold], hd, Nusd))}")
        pr(f"   {CALL_TXT[tt]}: NoiseArea in a position at the call on {np.nanmean(J['TAB'][hold & ok, jj, 4]) * 100:.0f}% of holdout days")
    pr("\n   All 72 configurations on the holdout (information only; classifiers refitted on discovery):")
    P = predictions(J, disc, meds); rows = []
    for (tt, lb_, fs_, mo_), pp in P.items():
        yy = labels(J, CALLS.index(tt), meds[str(tt)])[lb_]; mh = hold & np.isfinite(yy)
        pr(f"      {CALL_TXT[tt]} {lb_:4s} {fs_:5s} {mo_:6s}: holdout AUC {ML.auc(yy[mh], pp[mh]):.3f}")
    for c_ in CONFIGS:
        rr, nn, oo = gate(J, CALLS.index(c_[0]), P[c_[:4]], c_[4]); mm = rt.metrics(rr[hold], nn[hold], hd, Nusd)
        rows.append(dict(cfg=name(c_), sharpe=mm['sharpe'], usd_yr=mm['usd_yr'], maxdd=mm['maxdd'], stood_down=oo[hold].mean() * 100))
    tab = pd.DataFrame(rows); pr(tab.to_string(index=False, float_format=lambda v: f"{v:,.2f}"))
    pr(f"   configurations beating NoiseArea's holdout Sharpe {Mn['sharpe']:.2f}: {int((tab.sharpe > Mn['sharpe']).sum())} of 72; "
       f"with a lower max drawdown AND >= 60% of its $/yr: {int(((tab.maxdd < Mn['maxdd']) & (tab.usd_yr >= 0.6 * Mn['usd_yr'])).sum())} of 72")
    # A4: the chosen holdout series rebuilt from the saved predictions by separate arithmetic
    sv = os.path.join(rt.WORK, 'regime3_holdout.npz'); np.savez_compressed(sv, p=p[hold], ok=J['OK'][hold, j], rf=na[hold], rs=J['Rs'][hold, j], r=r[hold])
    z = np.load(sv); o = (z['ok'] & (z['p'] < tau)).astype(float); rebuilt = z['rs'] * o + z['rf'] * (1 - o)
    a4 = np.abs(rebuilt - z['r']).max(); pr(f"\n   AUDIT A4: holdout series rebuilt from the saved predictions, max diff {a4:.1e} -> {'PASSED' if a4 < 1e-12 else 'FAILED'}")
    verdict = 'DEAD ON THIS DATA'
    if v_self or v_prop: verdict = 'USABLE (' + ('self-funded' if v_self else 'prop') + ') - next: the full NAVAR validation gates, not live'
    pr(f"\nVERDICT (REGIME3): {verdict}")


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd not in ('audit', 'discover', 'holdout'): print(__doc__); sys.exit(0)
    below_normal(); t0 = dt.datetime.now()
    pr(f"\n=== regime3.py {cmd}  ({t0:%Y-%m-%d %H:%M}) ===")
    {'audit': audit, 'discover': discover, 'holdout': holdout}[cmd]()
    pr(f"(finished in {(dt.datetime.now() - t0).total_seconds() / 60:.1f} min)")
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write('\n'.join(_log) + '\n')
