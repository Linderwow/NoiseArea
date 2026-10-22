r"""MNQ-only research program per MNQ_PROGRAM_PLAN.txt (pre-registered 2026-10-20). Run from the research folder:
    python mnq_program.py run        every candidate on MNQ (NinjaTrader bars) and Databento MNQ -> printed report
    python mnq_program.py audit      the three audits in the plan
Needs regime_work\ (regime_test.py build / build_db and structure_test.py build).
"""
import os, sys, json
import numpy as np, pandas as pd
import regime_test as rt, structure_test as st, regime_ml as rml
from research_engine import Market, fam_A
sys.path.insert(0, os.path.dirname(rt.HERE))
from daily_review import early_closes, full_closures

NB_ = 'NoiseArea'
YEARS = [2022, 2023, 2024, 2025, 2026]
OOS = ('2022-01-01', '2026-10-14')
FOMC = sorted(pd.Timestamp(s.strip()) for s in open(os.path.join(rt.HERE, '..', '..', '..', 'bin', 'Custom', 'Strategies', 'fomc_dates.txt'))
              if s.strip() and not s.startswith('#'))


# ------------------------------------------------------------------------------------------------ MNQ-only data
class Data:
    def __init__(self, name):
        self.name = name
        z = np.load(os.path.join(rt.WORK, f'{name}.npz')); zs = np.load(os.path.join(rt.WORK, f'{name}_struct.npz'))
        d = pd.to_datetime(z['dates']); keep = (np.arange(len(d)) >= rt.WARMUP) & (d > rt.JOIN)
        self.dates = d[keep]
        daily = pd.read_csv(os.path.join(rt.WORK, f'{name}_daily.csv'), index_col=0, parse_dates=True)
        lv = pd.read_csv(os.path.join(rt.WORK, f'{name}_levels.csv'), index_col=0, parse_dates=True)
        raw = pd.read_csv(os.path.join(rt.HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
        px = raw.reindex(self.dates).values; self.px = np.where(np.isnan(px), daily.O.values[keep], px)
        self.p_ref = float(pd.read_csv(os.path.join(rt.HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
        self.c = 0.5 / self.p_ref; self.N = 2 * self.p_ref
        P = np.hstack([z['P'][keep], zs['P'][keep]]); self.NT = np.hstack([z['NT'][keep], zs['NT'][keep]])
        self.R = P / self.px[:, None] - self.NT * 2 * self.c
        full = daily[daily.index > rt.JOIN].assign(seg=1).join(lv)          # MNQ-only history (no NQ) for every state measure
        self.feat = rt.features(full).reindex(self.dates); self.st = st.states(full).reindex(self.dates)
        self.mk = Market(rt.FILES[name], 1.0, name); self.kidx = {dd: k for k, dd in enumerate(self.mk.dates)}
        self.na = self.R[:, rt.A_DEF]; self.nna = self.NT[:, rt.A_DEF].astype(float)

    def mask(self, a, b): return (self.dates >= pd.Timestamp(a)) & (self.dates <= pd.Timestamp(b))

    def windows(self):
        for y in YEARS:
            yield y, self.dates < pd.Timestamp(f'{y}-01-01'), self.mask(f'{y}-01-01', f'{y}-12-31')

    def gross_to_R(self, pts, nt):
        """Gross points per Market day -> net return per stats row."""
        idx = [self.kidx[d] for d in self.dates]
        return np.asarray(pts)[idx] / self.px - np.asarray(nt)[idx] * 2 * self.c, np.asarray(nt)[idx].astype(float)


# ------------------------------------------------------------------------------------------------ W group
def best_cols(D, fit, cols, min_tr=100):
    t, ntr = rt.tstats(D.R, D.NT, fit, cols); t = np.where(ntr >= min_tr, t, -np.inf)
    return [cols[k] for k in np.argsort(-t)], t[np.argsort(-t)]


def W1(D):
    r = np.zeros(len(D.dates)); n = np.zeros(len(D.dates)); log = []
    for y, fit, te in D.windows():
        order, ts = best_cols(D, fit, st.FULL_COLS); j = order[0]
        r[te] = D.R[te, j]; n[te] = D.NT[te, j]; log.append(f"{y}: {st.label_all(j)} (fit t {ts[0]:+.2f})")
    return r, n, log


def W2(D):
    r = np.zeros(len(D.dates)); n = np.zeros(len(D.dates)); log = []
    for y, fit, te in D.windows():
        order, ts = best_cols(D, fit, st.FULL_COLS); pick = []
        for j, t in zip(order, ts):
            if t < 2 or len(pick) == 5: break
            if all(st.FAM_ALL[j] != st.FAM_ALL[q] for q in pick): pick.append(j)
        for j in pick: r[te] += D.R[te, j] / 5; n[te] += D.NT[te, j]
        log.append(f"{y}: " + (", ".join(st.label_all(j) for j in pick) or "none (flat)"))
    return r, n, log


def cells_any(D, scheme, ref):
    return rt.cells(D.feat, scheme, ref)[0] if scheme.startswith('S') else st.cells(D.st, scheme, ref)


W3_CONFIGS = [(s, cat, K) for s in ['S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6'] for cat in ('defaults', 'full') for K in (1, 2)]


def W3(D):
    r = np.zeros(len(D.dates)); n = np.zeros(len(D.dates)); log = []
    for y, fit, te in D.windows():
        cut = pd.Timestamp(f'{y - 1}-01-01'); fa = fit & (D.dates < cut); fb = fit & (D.dates >= cut)
        best = None
        for cfg in W3_CONFIGS:
            s, cat, K = cfg; cid = cells_any(D, s, fa); sel = st.select(D.R, D.NT, cid, fa, st.cols_for(s, cat), K)
            rr, _ = rt.apply(D.R, D.NT, cid, sel); sh = rt.sharpe(rr[fb])
            if best is None or sh > best[0] + 1e-12: best = (sh, cfg)
        s, cat, K = best[1]; cid = cells_any(D, s, fit); sel = st.select(D.R, D.NT, cid, fit, st.cols_for(s, cat), K)
        rr, nn = rt.apply(D.R, D.NT, cid, sel); r[te] = rr[te]; n[te] = nn[te]
        log.append(f"{y}: {best[1]} (inner Sharpe {best[0]:.2f}); " + "; ".join(f"cell {k}: " + ("+".join(st.label_all(j).split(' ')[0] for j, _ in v) or '-') for k, v in sel.items()))
    return r, n, log


def W4(D):
    r = np.zeros(len(D.dates)); n = np.zeros(len(D.dates)); log = []
    for y, fit, te in D.windows():
        order, ts = best_cols(D, fit, [j for j in st.FULL_COLS if st.FAM_ALL[j] != 'A']); j = order[0]
        r[te] = D.R[te, j]; n[te] = D.NT[te, j]; log.append(f"{y}: {st.label_all(j)} (fit t {ts[0]:+.2f})")
    return r, n, log


def W5(D):
    r = np.zeros(len(D.dates)); n = np.zeros(len(D.dates)); log = []
    for y, fit, te in D.windows():
        t, _ = rt.tstats(D.R, D.NT, fit, st.DEF_COLS); pick = [j for j, tt in zip(st.DEF_COLS, t) if tt >= 1]
        if not pick: log.append(f"{y}: none"); continue
        w = np.array([1 / D.R[fit, j].std(ddof=1) for j in pick]); port_fit = (D.R[np.ix_(fit, pick)] * w).sum(axis=1)
        w = w * D.na[fit].std(ddof=1) / port_fit.std(ddof=1)
        r[te] = (D.R[np.ix_(te, pick)] * w).sum(axis=1); n[te] = (D.NT[np.ix_(te, pick)]).sum(axis=1)
        log.append(f"{y}: " + ", ".join(f"{st.FAM_ALL[j]} {wi:.2f}" for j, wi in zip(pick, w)))
    return r, n, log


# ------------------------------------------------------------------------------------------------ N group
def N12(D, direction):
    p, nt = fam_A(D.mk, direction=direction, slip=0.0, comm=0.0); return D.gross_to_R(np.nan_to_num(p), nt)


def N3(D):
    sig = D.mk.sigma(14)[:, 5]; s = np.array([sig[D.kidx[d]] for d in D.dates]); r = np.zeros(len(D.dates)); log = []
    for y, fit, te in D.windows():
        med = np.nanmedian(s[fit]); w = np.clip(med / s[te], 0.5, 2.0); w = np.where(np.isnan(w), 1.0, w)
        r[te] = w * D.na[te]; log.append(f"{y}: median sigma {med * 100:.3f}%, mean weight {w.mean():.2f}")
    return r, D.nna.copy(), log


def fam_A_meta(mk, allow=None, ctx=None):
    """fam_A published default with an entry callback: allow(k, i, new, pos, n_today, info) -> bool. Returns gross points, entries,
    and the trade log [(k, i, dir, entry, exit, info)] with exits filled in."""
    sig = mk.sigma(14); checks = set(range(600, 931, 30)); pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int); log = []
    for k, x in enumerate(mk.days):
        if k < 14 or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; cur = None
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        for i in range(flat_i):
            if M[i] not in checks or np.isnan(sg[i]): continue
            c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
            if pos == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif pos == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if new != 0 and new != pos and allow is not None:
                info = dict(slot=(M[i] - 600) // 30, excess=((c - ub) if new > 0 else (lb - c)) / (c * sg[i]),
                            vwd=(c - vw[i]) * new / (c * sg[i]), n_today=n_, rev=int(pos != 0))
                if not allow(k, i, new, pos, n_, info): new = 0
            if new != pos:
                px = O[i + 1]
                if pos != 0: p += (px - entry) * pos; cur[4] = px; log.append(cur)
                if new != 0:
                    entry = px; n_ += 1
                    cur = [k, i, new, px, None, dict(slot=(M[i] - 600) // 30, excess=((c - ub) if new > 0 else (lb - c)) / (c * sg[i]),
                                                       vwd=(c - vw[i]) * new / (c * sg[i]), n_today=n_ - 1, rev=int(pos != 0))]
                pos = new
        if pos != 0: p += (C[flat_i] - entry) * pos; cur[4] = C[flat_i]; log.append(cur)
        pnl[k] = p; nt[k] = n_
    return pnl, nt, log


META_DAY = ['ER10', 'VOLR', 'ONR', 'GAPZ', 'PDE']


def meta_X(D, k, new, info):
    d = D.mk.dates[k]; f = D.feat.loc[d] if d in D.feat.index else None; s_ = D.st.loc[d] if d in D.st.index else None
    x = D.mk.days[k]; gap = x.O[0] - D.mk.pc[k]
    row = [info['slot'], new, info['excess'], info['vwd'], info['n_today'], info['rev'], np.sign(gap) * new, x.eff,
           float(d.dayofweek == 0), float(d.dayofweek == 4), float(d in FOMC_SET)]
    row += [f[c] if f is not None else np.nan for c in META_DAY]
    row += [s_['T4'] if s_ is not None else np.nan, s_['T5'] if s_ is not None else np.nan]
    return np.array(row, float)


FOMC_SET = set(FOMC)


def N4(D, model='logit'):
    base_p, base_n, log = fam_A_meta(D.mk)
    X = np.array([meta_X(D, k, d_, info) for k, i, d_, e, xp, info in log])
    raw = pd.read_csv(os.path.join(rt.HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    pxk = np.array([raw.get(D.mk.dates[k], D.mk.days[k].O[0]) for k, *_ in log])
    y = np.array([(xp - e) * d_ / p - 2 * D.c > 0 for (k, i, d_, e, xp, info), p in zip(log, pxk)], float)
    tdates = np.array([D.mk.dates[k] for k, *_ in log])
    models = {}
    for yr in YEARS:
        tr = tdates < pd.Timestamp(f'{yr}-01-01')
        models[yr] = (rml.Logit() if model == 'logit' else rml.Stumps()).fit(X[tr], y[tr])
    kept = {'n': 0, 'skip': 0}
    def allow(k, i, new, pos, n_, info):
        yr = D.mk.dates[k].year
        if yr not in models: return True
        p = models[yr].predict(meta_X(D, k, new, info)[None, :])[0]
        kept['n' if p >= 0.5 else 'skip'] += 1
        return p >= 0.5
    pf, nf, _ = fam_A_meta(D.mk, allow)
    r, n = D.gross_to_R(np.nan_to_num(pf), nf)
    # skill of the model on the OOS trades of the unfiltered run
    aucs = []
    for yr in YEARS:
        te = np.array([d.year == yr for d in tdates])
        if te.sum() > 10: aucs.append(f"{yr} AUC {rml.auc(y[te], models[yr].predict(X[te])):.3f}")
    return r, n, [f"entries kept {kept['n']}, skipped {kept['skip']} (OOS years); " + ", ".join(aucs)]


# ------------------------------------------------------------------------------------------------ E group
def events(D):
    """E1..E5 daily returns per stats row (0 when not active), and trade counts."""
    mk = D.mk; out = {e: (np.zeros(len(D.dates)), np.zeros(len(D.dates))) for e in ('E1', 'E2', 'E3', 'E4', 'E5')}
    hol = set()
    for y in range(2019, 2028):
        hol |= set(early_closes(y)) | set(full_closures(y))
    for row, d in enumerate(D.dates):
        k = D.kidx[d]
        if k == 0: continue
        x, xp = mk.days[k], mk.days[k - 1]; px = D.px[row]
        def put(e, a, b):
            out[e][0][row] = (b - a) / px - 2 * D.c; out[e][1][row] = 1
        i580 = np.where(x.M == 580)[0]; i840 = np.where(x.M == 840)[0]; i955 = np.where(x.M == 955)[0]
        put('E5', xp.C[-1], x.O[0])
        if d in FOMC_SET:
            put('E1', xp.C[-1], x.O[0])
            if len(i580) and len(i840): put('E2', x.O[i580[0]], x.C[i840[0]])
            if len(i840): put('E3', xp.C[-1], x.C[i840[0]])
        nxt = d + pd.Timedelta(days=1)
        while nxt.weekday() >= 5: nxt += pd.Timedelta(days=1)
        if nxt.date() in hol and len(i580):
            put('E4', x.O[i580[0]], x.C[i955[0]] if len(i955) else x.C[-1])
    return out


# ------------------------------------------------------------------------------------------------ report
def score(D, name, r, n, oos, kind, k_group, add_to_na):
    hd = D.dates[oos]; na = D.na[oos]
    total = (na + r[oos]) if add_to_na else r[oos]; ntot = (D.nna[oos] + n[oos]) if add_to_na else n[oos]
    M_ = rt.metrics(total, ntot, hd, D.N); Mn = rt.metrics(na, D.nna[oos], hd, D.N)
    pb = rt.boot_sharpe_diff(hd, total, na); pm, _ = rt.prop_odds(hd, total * D.N); pn, _ = rt.prop_odds(hd, na * D.N)
    passed = M_['sharpe'] > Mn['sharpe'] and pb < 0.05 / k_group and (add_to_na or M_['t'] > 2)
    prop = pm >= pn + 0.10 and M_['usd_yr'] >= 0.6 * Mn['usd_yr']
    return M_, pb, pm, passed, prop


def run_source(name):
    D = Data(name); oos = D.mask(*OOS); hd = D.dates[oos]
    Mn = rt.metrics(D.na[oos], D.nna[oos], hd, D.N); pn, tn = rt.prop_odds(hd, D.na[oos] * D.N)
    print(f"\n######## {name}: OOS {OOS[0]} .. {OOS[1]} ({oos.sum()} sessions), per micro at today's notional ${D.N:,.0f}")
    print(f"   NoiseArea   {rt.fmt(Mn)}  50K odds {pn * 100:.0f}%")
    res = {}
    groups = [('replacement', 8, False, [('W1', W1), ('W2', W2), ('W3', W3), ('W5', W5),
                                          ('N1', lambda D: (*N12(D, 'long'), ['long-only'])), ('N2', lambda D: (*N12(D, 'short'), ['short-only'])),
                                          ('N3', N3), ('N4', lambda D: N4(D, 'logit'))]),
              ('addition', 6, True, [('W4', W4)] + [(e, None) for e in ('E1', 'E2', 'E3', 'E4', 'E5')])]
    ev = events(D)
    for gname, k_group, add, items in groups:
        print(f"\n   --- {gname}s (PASS: Sharpe above NoiseArea's, bootstrap P < {0.05 / k_group:.4f}" + ("" if add else ", t > 2") + ")")
        for cname, fn in items:
            if fn is None: r, n = ev[cname]; log = [f"{int(n[oos].sum())} OOS trades, {int(n.sum())} on all MNQ dates; all-MNQ mean "
                                                         f"{np.mean(r[n > 0]) * 1e4:+.1f} bp/trade, t {rt.tstat(r[n > 0]):+.2f}"]
            else: r, n, log = fn(D)
            M_, pb, pm, ok, prop = score(D, name, r, n, oos, gname, k_group, add)
            res[cname] = (r, n, ok, prop)
            print(f"   {cname} {'(NoiseArea +) ' if add else ''}{rt.fmt(M_)}  P {pb:.3f}  50K {pm * 100:.0f}%  -> PASS {ok}, PROP-USEFUL {prop}")
            for line in log: print(f"        {line}")
    print("\n   For information - N4 with boosted stumps instead of logistic:")
    r, n, log = N4(D, 'stumps'); M_, pb, pm, ok, prop = score(D, name, r, n, oos, 'replacement', 8, False)
    print(f"   N4s {rt.fmt(M_)}  P {pb:.3f}  50K {pm * 100:.0f}%\n        {log[0]}")
    return D, res


def robustness(D, res, oos):
    hd = D.dates[oos]
    for cname, (r, n, ok, prop) in res.items():
        if not (ok or prop): continue
        add = cname.startswith('E') or cname == 'W4'
        tot = (D.na + r) if add else r; ntot = (D.nna + n) if add else n
        ex2 = 0.25 / D.p_ref * 2; t2 = tot[oos] - ntot[oos] * ex2; na2 = D.na[oos] - D.nna[oos] * ex2
        yr = pd.Series(tot[oos] * D.N, index=hd).groupby(hd.year).sum(); s = np.sort(tot[oos])[::-1]
        print(f"   {cname}: at 2 ticks Sharpe {rt.sharpe(t2):+.2f} vs NoiseArea {rt.sharpe(na2):+.2f} (P {rt.boot_sharpe_diff(hd, t2, na2):.3f}); "
              f"by year " + " ".join(f"{y} {v:+,.0f}" for y, v in yr.items()) +
              f"; best 10% of days removed ${s[int(.1 * len(s)):].sum() * D.N / (len(s) / 252):+,.0f}/yr")


def nq_fomc_line():
    """Information only: E1 / E2 on NQ 2010-2019 (they were discovered on MNQ, so this is their out-of-sample record)."""
    mk = Market(rt.FILES['NQ'], 1.0, 'NQ'); raw = pd.read_csv(os.path.join(rt.HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(rt.HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0]); c = 0.5 / p_ref
    e1, e2 = [], []
    for k, d in enumerate(mk.dates):
        if k == 0 or d not in FOMC_SET: continue
        x, xp = mk.days[k], mk.days[k - 1]; px = raw.get(d, x.O[0])
        e1.append((x.O[0] - xp.C[-1]) / px - 2 * c)
        i580, i840 = np.where(x.M == 580)[0], np.where(x.M == 840)[0]
        if len(i580) and len(i840): e2.append((x.C[i840[0]] - x.O[i580[0]]) / px - 2 * c)
    for nm, v in (('E1 FOMC-eve night', e1), ('E2 pre-FOMC drift', e2)):
        v = np.array(v); print(f"   NQ 2010-2019 {nm}: n {len(v)}, mean {v.mean() * 1e4:+.1f} bp, t {rt.tstat(v):+.2f}, win {np.mean(v > 0) * 100:.0f}%")


def main():
    D, res = run_source('MNQ'); oos = D.mask(*OOS)
    print("\n   Robustness of anything that passed or is prop-useful (MNQ NinjaTrader bars):"); robustness(D, res, oos)
    print("\n######## Second source - the same procedure on Databento MNQ bars")
    D2, res2 = run_source('MNQDB')
    print("\n######## Information: the FOMC sleeves on NQ 2010-2019 (independent of the MNQ data they were found on)"); nq_fomc_line()
    passed = [c for c, v in res.items() if v[2]]; prop = [c for c, v in res.items() if v[3]]
    agree = [c for c in passed if res2.get(c, (0, 0, False))[2]]
    print(f"\nSUMMARY: PASS on MNQ {passed or 'none'}; PROP-USEFUL {prop or 'none'}; PASS on both sources {agree or 'none'}")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'run': main()
    else: print(__doc__)
