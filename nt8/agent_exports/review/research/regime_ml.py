r"""Learned market-state classifier ("Patrick's step 1 with machine learning"), per REGIME_ML_PLAN.txt (pre-registered 2026-10-20).
    python regime_ml.py discover    48 configurations fitted on FIT, scored on VALIDATE; the chosen one (no holdout figures)
    python regime_ml.py holdout     the single holdout run, oracle, audits
Needs regime_work\ from  python regime_test.py build  and  build_db.
"""
import os, sys, json
import numpy as np, pandas as pd
import regime_test as rt
from research_engine import Market

TAUS = (0.3, 0.4, 0.5, 0.6)
CONS_930 = ('C', 'D', 'E', 'G', 'H'); CONS_1000 = ('D', 'E', 'G')
F930 = ['ER5', 'ER10', 'ER20', 'BR560', 'BR520', 'ONR', 'GAPZ', 'GAPTR', 'PDE', 'PRR', 'EFF3', 'ONMOVE', 'MON', 'FRI']
F1000 = F930 + ['EFF30', 'R30Z', 'MOVEZ', 'VOL30Z']


# ------------------------------------------------------------------------------------------------ features and label
def intraday(name):
    """Per session of one file: first-30-minute efficiency, range, |move| in 10:00 noise units, volume."""
    mk = Market(rt.FILES[name], 1.0, name); sig = mk.sigma(14); rows = []
    for k, x in enumerate(mk.days):
        m = x.M <= 600; i10 = np.where(x.M == 600)[0]
        mv = abs(x.C[i10[0]] / x.O[0] - 1) / sig[k, 5] if len(i10) and sig[k, 5] > 0 else np.nan
        rows.append((x.eff, x.H[m].max() - x.L[m].min(), mv, x.V[m].sum()))
    return pd.DataFrame(rows, columns=['EFF30', 'R30', 'MOVEZ', 'VOL30'], index=mk.dates)


def feature_table(second='MNQ'):
    dates, R, NT, c, p_ref, px, P, full, _ = rt.joined(second)
    a, b = intraday('NQ'), intraday(second)
    intr = pd.concat([a[a.index <= rt.JOIN], b[b.index > rt.JOIN]])
    d = full.join(intr)
    O, H, L, C, seg = d.O, d.H, d.L, d.C, d.seg
    newseg = seg.ne(seg.shift()) & (d.index != d.index[0])
    dC = C.diff().mask(newseg); gap = (O - C.shift()).mask(newseg)
    body = (C - O).abs(); rng = H - L; eff = body / rng
    f = pd.DataFrame(index=d.index)
    for n in (5, 10, 20):
        f[f'ER{n}'] = dC.rolling(n, min_periods=1).sum().abs().shift(1) / dC.abs().rolling(n, min_periods=1).sum().shift(1)
    f['BR560'] = body.rolling(5).mean().shift(1) / body.rolling(60).mean().shift(1)
    f['BR520'] = body.rolling(5).mean().shift(1) / body.rolling(20).mean().shift(1)
    mrng = rng.rolling(14).mean().shift(1)
    f['ONR'] = d.ONR / mrng
    f['GAPZ'] = gap.abs() / gap.abs().rolling(14, min_periods=1).mean().shift(1)
    f['GAPTR'] = np.sign(gap) * np.sign(dC.rolling(10, min_periods=1).sum().shift(1))
    f['PDE'] = eff.shift(1); f['PRR'] = rng.shift(1) / mrng; f['EFF3'] = eff.rolling(3).mean().shift(1)
    f['ONMOVE'] = gap.abs() / mrng
    f['MON'] = (d.index.dayofweek == 0).astype(float); f['FRI'] = (d.index.dayofweek == 4).astype(float)
    f['EFF30'] = d.EFF30; f['R30Z'] = d.R30 / d.R30.rolling(14).mean().shift(1)
    f['MOVEZ'] = d.MOVEZ; f['VOL30Z'] = d.VOL30 / d.VOL30.rolling(14).mean().shift(1)
    f['EFFDAY'] = eff                                            # hindsight: label only
    return dates, R, NT, c, p_ref, f.reindex(dates)


# ------------------------------------------------------------------------------------------------ models (numpy)
class Prep:
    def __init__(self, X):
        self.med = np.nanmedian(X, axis=0); Xf = np.where(np.isnan(X), self.med, X)
        self.mu = Xf.mean(axis=0); self.sd = Xf.std(axis=0); self.sd[self.sd == 0] = 1.0
    def __call__(self, X):
        return (np.where(np.isnan(X), self.med, X) - self.mu) / self.sd


def sigmoid(z): return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))


class Logit:
    def fit(self, X, y, lam=1.0):
        self.prep = Prep(X); Z = np.c_[np.ones(len(X)), self.prep(X)]; w = np.zeros(Z.shape[1])
        reg = lam * np.eye(Z.shape[1]); reg[0, 0] = 0.0
        for _ in range(50):
            p = sigmoid(Z @ w); g = Z.T @ (p - y) + reg @ w; Hm = (Z * (p * (1 - p))[:, None]).T @ Z + reg
            step = np.linalg.solve(Hm, g); w -= step
            if np.abs(step).max() < 1e-10: break
        self.w = w; return self
    def predict(self, X): return sigmoid(np.c_[np.ones(len(X)), self.prep(X)] @ self.w)


class Stumps:
    def fit(self, X, y, rounds=150, lr=0.1, nthr=16):
        self.prep = Prep(X); Z = self.prep(X); p0 = np.clip(y.mean(), 1e-3, 1 - 1e-3); self.f0 = np.log(p0 / (1 - p0))
        thr = [np.unique(np.quantile(Z[:, j], np.arange(1, nthr + 1) / (nthr + 1))) for j in range(Z.shape[1])]
        F = np.full(len(y), self.f0); self.trees = []
        for _ in range(rounds):
            p = sigmoid(F); g = y - p; h = np.maximum(p * (1 - p), 1e-6); best = None
            for j in range(Z.shape[1]):
                for t in thr[j]:
                    L = Z[:, j] <= t; GL, HL = g[L].sum(), h[L].sum(); GR, HR = g[~L].sum(), h[~L].sum()
                    if HL < 1e-3 or HR < 1e-3: continue
                    gain = GL * GL / HL + GR * GR / HR
                    if best is None or gain > best[0]: best = (gain, j, t, GL / HL, GR / HR)
            _, j, t, vl, vr = best; self.trees.append((j, t, lr * vl, lr * vr))
            F += np.where(Z[:, j] <= t, lr * vl, lr * vr)
        return self
    def predict(self, X):
        Z = self.prep(X); F = np.full(len(Z), self.f0)
        for j, t, vl, vr in self.trees: F += np.where(Z[:, j] <= t, vl, vr)
        return sigmoid(F)


def auc(y, p):
    order = np.argsort(p); r = np.empty(len(p)); r[order] = np.arange(1, len(p) + 1)
    pos = y == 1; n1, n0 = pos.sum(), (~pos).sum()
    return (r[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else np.nan


# ------------------------------------------------------------------------------------------------ policies
CONFIGS = ([(tm, mo, 'GATE', None, tau) for tm in ('0930', '1000') for mo in ('logit', 'stumps') for tau in TAUS] +
           [(tm, mo, 'SWITCH', cat, tau) for tm in ('0930', '1000') for mo in ('logit', 'stumps') for cat in ('defaults', 'full') for tau in TAUS])


def simplicity(cfg):
    tm, mo, pol, cat, tau = cfg
    return (tm == '1000', mo == 'stumps', pol == 'SWITCH', cat == 'full', abs(tau - 0.5))


def cons_cols(tm, cat):
    fams = CONS_930 if tm == '0930' else CONS_1000
    if cat == 'defaults': return [rt.DEF_IDX[list(rt.DEFAULTS).index(f)] for f in fams]
    return [j for j in range(rt.N_FULL) if rt.CAT[j][0] in fams and not (rt.CAT[j][0] == 'H' and rt.CAT[j][1]['mode'] != 'fade')]


def fit_predict(f, y, tm, mo, train, test):
    cols = F930 if tm == '0930' else F1000
    X = f[cols].values.astype(float); m = (Logit() if mo == 'logit' else Stumps()).fit(X[train], y[train])
    return m.predict(X), m


def policy(R, NT, p, cfg, sel_mask):
    """Daily return and trade count; SWITCH picks its consolidation variant on sel_mask & (p < tau)."""
    tm, mo, pol, cat, tau = cfg
    na, nna = R[:, rt.A_DEF], NT[:, rt.A_DEF].astype(float); on = p >= tau
    r = np.where(on, na, 0.0); n = np.where(on, nna, 0.0); pick = None
    if pol == 'SWITCH':
        cols = cons_cols(tm, cat); m = sel_mask & ~on
        if m.sum() >= 20:
            t, ntr = rt.tstats(R, NT, m, cols)
            ok = [k for k in np.argsort(-t) if ntr[k] >= rt.MIN_TR and t[k] >= rt.T_MIN]
            if ok: pick = cols[ok[0]]; r = r + np.where(~on, R[:, pick], 0.0); n = n + np.where(~on, NT[:, pick], 0.0)
    return r, n, pick


def name(cfg): tm, mo, pol, cat, tau = cfg; return f"{tm} {mo:6s} {pol:6s} {cat or '-':8s} tau {tau}"


def discover():
    dates, R, NT, c, p_ref, f = feature_table('MNQ')
    fit, val, disc = rt.between(dates, rt.FIT), rt.between(dates, rt.VAL), rt.between(dates, rt.DISC)
    med = np.nanmedian(f.EFFDAY.values[fit]); y = (f.EFFDAY.values >= med).astype(float)
    N = 2 * p_ref; na = R[:, rt.A_DEF]
    print(f"Label: trend day = RTH efficiency >= {med:.3f} (FIT median). Trend days: FIT {y[fit].mean() * 100:.0f}%, VALIDATE {y[val].mean() * 100:.0f}%")
    print(f"NoiseArea on VALIDATE: Sharpe {rt.sharpe(na[val]):.2f}, ${na[val].sum() * N / (val.sum() / 252):,.0f}/yr; "
          f"NoiseArea Sharpe on FIT trend days {rt.sharpe(na[fit & (y == 1)]):+.2f} vs consolidation days {rt.sharpe(na[fit & (y == 0)]):+.2f}")
    preds = {}
    for tm in ('0930', '1000'):
        for mo in ('logit', 'stumps'):
            p, _ = fit_predict(f, y, tm, mo, fit, val); preds[(tm, mo)] = p
            print(f"   classifier {tm} {mo:6s}: AUC FIT {auc(y[fit], p[fit]):.3f}, VALIDATE {auc(y[val], p[val]):.3f}")
    rows = []
    for cfg in CONFIGS:
        p = preds[cfg[:2]]; r, n, pick = policy(R, NT, p, cfg, fit)
        rows.append(dict(cfg=name(cfg), val_sharpe=rt.sharpe(r[val]), val_usd_yr=r[val].sum() * N / (val.sum() / 252),
                         days_on=(p[val] >= cfg[4]).mean() * 100, cons=rt.label(pick) if pick is not None else '-'))
    tab = pd.DataFrame(rows); print("\n" + tab.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    best = max(CONFIGS, key=lambda cfg: (round(tab.val_sharpe[CONFIGS.index(cfg)], 6), tuple(-float(v) for v in simplicity(cfg))))
    print(f"\nCHOSEN: {name(best)} (VALIDATE Sharpe {tab.val_sharpe[CONFIGS.index(best)]:.3f})")
    json.dump(dict(cfg=list(best), median=med), open(os.path.join(rt.WORK, 'ml_model.json'), 'w'))


def holdout():
    m = json.load(open(os.path.join(rt.WORK, 'ml_model.json'))); cfg = tuple(m['cfg']); med = m['median']
    dates, R, NT, c, p_ref, f = feature_table('MNQ')
    disc, hold = rt.between(dates, rt.DISC), rt.between(dates, rt.HOLD); N = 2 * p_ref
    y = (f.EFFDAY.values >= med).astype(float); hd = dates[hold]
    p, model = fit_predict(f, y, cfg[0], cfg[1], disc, hold)
    r, n, pick = policy(R, NT, p, cfg, disc)
    na, nna = R[:, rt.A_DEF], NT[:, rt.A_DEF].astype(float)
    print(f"=== HOLDOUT 2019-05-06 .. 2026-10-14 ({hold.sum()} sessions): {name(cfg)}; consolidation strategy: {rt.label(pick) if pick is not None else 'flat'}")
    print(f"   classifier AUC on the holdout {auc(y[hold], p[hold]):.3f} (discovery in-sample {auc(y[disc], p[disc]):.3f}); NoiseArea traded on {(p[hold] >= cfg[4]).mean() * 100:.0f}% of days")
    Mm, Mn = rt.metrics(r[hold], n[hold], hd, N), rt.metrics(na[hold], nna[hold], hd, N)
    print(f"   model      {rt.fmt(Mm)}\n   NoiseArea  {rt.fmt(Mn)}")
    pb = rt.boot_sharpe_diff(hd, r[hold], na[hold]); pm, tm_ = rt.prop_odds(hd, r[hold] * N); pn, tn = rt.prop_odds(hd, na[hold] * N)
    print(f"   bootstrap P(Sharpe model <= NoiseArea) = {pb:.3f}; 50K evaluation pass odds model {pm * 100:.0f}% vs NoiseArea {pn * 100:.0f}%")
    v_self = Mm['sharpe'] > Mn['sharpe'] and pb < 0.05 and Mm['t'] > 2
    v_prop = Mm['maxdd'] <= 0.6 * Mn['maxdd'] and Mm['usd_yr'] >= 0.6 * Mn['usd_yr'] and pm >= pn + 0.10
    print(f"   DECISION RULE: self-funded {v_self} | prop {v_prop}")
    extra = 0.25 / p_ref * 2; a2, b2 = r[hold] - n[hold] * extra, na[hold] - nna[hold] * extra
    print(f"   at 2 ticks/side: model {rt.fmt(rt.metrics(a2, n[hold], hd, N))}\n                    NoiseArea {rt.fmt(rt.metrics(b2, nna[hold], hd, N))}")
    yr = pd.Series(r[hold] * N, index=hd).groupby(hd.year).sum(); print("   by year, model: " + " ".join(f"{k} {v:+,.0f}" for k, v in yr.items()))
    print("\n   ORACLE (true hindsight label instead of the prediction):")
    for pol in ('GATE', 'SWITCH'):
        oc = (cfg[0], cfg[1], pol, cfg[3] or 'defaults', 0.5); ro, no_, pk = policy(R, NT, y, oc, disc)
        print(f"   {pol:6s} {rt.fmt(rt.metrics(ro[hold], no_[hold], hd, N))}" + (f"  ({rt.label(pk)})" if pk is not None else ""))
    print(f"   NoiseArea on true trend days: ${na[hold & (y == 1)].sum() * N / (hold.sum() / 252):+,.0f}/yr; on true consolidation days "
          f"${na[hold & (y == 0)].sum() * N / (hold.sum() / 252):+,.0f}/yr")
    print("\n   All 48 configurations on the holdout (information only; refitted on discovery):")
    preds = {(tm, mo): fit_predict(f, y, tm, mo, disc, hold)[0] for tm in ('0930', '1000') for mo in ('logit', 'stumps')}
    for k in preds: print(f"      classifier {k[0]} {k[1]:6s}: holdout AUC {auc(y[hold], preds[k][hold]):.3f}")
    rows = []
    for c_ in CONFIGS:
        rr, nn, pk = policy(R, NT, preds[c_[:2]], c_, disc); mm = rt.metrics(rr[hold], nn[hold], hd, N)
        rows.append(dict(cfg=name(c_), sharpe=mm['sharpe'], usd_yr=mm['usd_yr'], maxdd=mm['maxdd']))
    tab = pd.DataFrame(rows); print(tab.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"   configurations beating NoiseArea's holdout Sharpe {Mn['sharpe']:.2f}: {int((tab.sharpe > Mn['sharpe']).sum())} of 48")
    # secondary holdout (Databento MNQ)
    d2, R2, NT2, _, _, f2 = feature_table('MNQDB'); h2 = rt.between(d2, rt.HOLD)
    X2 = f2[F930 if cfg[0] == '0930' else F1000].values.astype(float); p2 = model.predict(X2)
    on = p2 >= cfg[4]; r2 = np.where(on, R2[:, rt.A_DEF], 0.0); n2 = np.where(on, NT2[:, rt.A_DEF], 0.0)
    if pick is not None: r2 = r2 + np.where(~on, R2[:, pick], 0.0); n2 = n2 + np.where(~on, NT2[:, pick], 0.0)
    M2, M2n = rt.metrics(r2[h2], n2[h2], d2[h2], N), rt.metrics(R2[h2, rt.A_DEF], NT2[h2, rt.A_DEF].astype(float), d2[h2], N)
    print(f"\n   Databento holdout: model {rt.fmt(M2)}\n                      NoiseArea {rt.fmt(M2n)}")
    # audits
    cols = F930 if cfg[0] == '0930' else F1000; X = f[cols].values.astype(float)
    lg = Logit().fit(X[disc], y[disc]); Z = np.c_[np.ones(disc.sum()), lg.prep(X[disc])]; w = np.zeros(Z.shape[1])
    for _ in range(200_000):
        gr = Z.T @ (sigmoid(Z @ w) - y[disc]) + np.r_[0, w[1:]]; w -= 0.5 * gr / len(Z)
        if np.abs(gr).max() < 1e-9: break
    print(f"\n   AUDIT: Newton vs gradient-descent logistic coefficients, max difference {np.abs(lg.w - w).max():.1e} -> {'PASSED' if np.abs(lg.w - w).max() < 1e-4 else 'FAILED'}")
    on_h = p[hold] >= cfg[4]
    rebuilt = np.where(on_h, na[hold], 0.0) + (np.where(~on_h, R[hold, pick], 0.0) if pick is not None else 0.0)
    print(f"   AUDIT: holdout series rebuilt from the saved predictions, max difference {np.abs(rebuilt - r[hold]).max():.1e} -> "
          f"{'PASSED' if np.abs(rebuilt - r[hold]).max() < 1e-12 else 'FAILED'}")
    verdict = 'DEAD ON THIS DATA'
    if v_self or v_prop: verdict = 'USABLE (' + ('self-funded' if v_self else 'prop') + ') - check the downgrade rules above'
    print(f"\nVERDICT (learned classifier): {verdict}")
    np.savez_compressed(os.path.join(rt.WORK, 'ml_holdout.npz'), p=p[hold], r=r[hold], y=y[hold])


if __name__ == '__main__':
    {'discover': discover, 'holdout': holdout}.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: print(__doc__))()
