r"""WEEKEND A1 (WEEKEND_PLAN_20261031.txt): analog days - k-nearest-neighbour chart matching as a day-type call.
    python weekend_knn.py   -> weekend_knn_output.txt
Features at the call (10:00 or 10:30): the 5-minute closes since 09:30 in units of that slot's noise sigma; the previous two sessions'
shapes (closes at 10:00, 11:00 .. 16:00 relative to their own open, in their own sigma units); the overnight gap in sigma units.
Walk-forward: a day's neighbours are strictly earlier days (NQ 2010-2019 then MNQ 2019-2026 pooled; the features are scale-free).
Labels: eff = rest-of-day efficiency >= the FIT-period median; meta = the live rules' day P&L > 0 (no-trade days undefined).
Scored by AUC on FIT (2012-2014, info), VALIDATE (2015-01 .. 2019-05) and HOLDOUT (2019-05 .. 2026-10). Bar: >= 0.60 on both.
"""
import os, sys, time
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX
import weekend as W
import regime3 as R3

LOOK = 14; CALLS = (600, 630); KS = (25, 50, 100); PRIOR_SLOTS = [5, 17, 29, 41, 53, 65, 77]        # 10:00, 11:00 .. 16:00 closes
FIT = (pd.Timestamp('2010-07-21'), pd.Timestamp('2014-12-31')); VAL = (pd.Timestamp('2015-01-01'), pd.Timestamp('2019-05-05'))
HOLD = (pd.Timestamp('2019-05-06'), pd.Timestamp('2026-12-31')); MIN_PRIOR = 300
out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)
def save(): open(os.path.join(HERE, 'weekend_knn_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


def auc(score, y):
    m = np.isfinite(score) & np.isfinite(y)
    s, t = score[m], y[m]; pos, neg = s[t == 1], s[t == 0]
    if len(pos) == 0 or len(neg) == 0: return np.nan, 0
    r = pd.Series(s).rank().values; rp = r[t == 1].sum()
    return (rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)), int(m.sum())


def day_table(mk, keep):
    """One row per session: features per call, labels, base P&L, stand-down P&L per call."""
    sig = mk.sigma(LOOK); p_base, nt_base, _, _ = W.engine(mk); rows = []
    for k, x in enumerate(mk.days):
        if k < LOOK + 2 or np.isnan(mk.pc[k]) or not keep(mk.dates[k]): continue
        sg = sig[k, x.slot]
        if np.isnan(sg).any(): continue
        slot_of = {int(s): i for i, s in enumerate(x.slot)}
        row = dict(date=mk.dates[k], pnl=p_base[k], nt=nt_base[k])
        # prior two sessions' shapes (carry the last available slot forward on short sessions)
        prior = []
        for b in (1, 2):
            y = mk.days[k - b]; sgy = sig[k - b, y.slot]; z = (y.C / y.O[0] - 1) / np.where(sgy > 0, sgy, np.nan); so = {int(s): i for i, s in enumerate(y.slot)}
            last = 0.0
            for sl in PRIOR_SLOTS:
                if sl in so and np.isfinite(z[so[sl]]): last = z[so[sl]]
                prior.append(last)
        gap = (x.O[0] / mk.pc[k] - 1) / sg[-1] if sg[-1] > 0 else 0.0
        ok = True
        for T in CALLS:
            n_sl = (T - 575) // 5 + 1
            if any(s not in slot_of for s in range(n_sl)): ok = False; break
            idx = [slot_of[s] for s in range(n_sl)]; i_call = idx[-1]
            path = [(x.C[i] / x.O[0] - 1) / sg[i] for i in idx]
            row[f'F{T}'] = np.array(path + prior + [gap])
            hh = x.H[i_call + 1:]; ll = x.L[i_call + 1:]
            rng = hh.max() - ll.min() if len(hh) else 0.0
            row[f'eff{T}'] = abs(x.C[-1] - x.C[i_call]) / rng if rng > 0 else 0.0
            row[f'absz{T}'] = abs(path[-1])
        if not ok: continue
        _, _, sd = R3.day_pass(x, mk.pc[k], sg)
        for T in CALLS: row[f'sd{T}'] = sd.get(T, (p_base[k], nt_base[k]))[0]
        rows.append(row)
    return rows


def main():
    W.below_normal(); t0 = time.time()
    pr(f"WEEKEND A1 analog days (kNN) - {time.strftime('%Y-%m-%d %H:%M')}")
    rows = day_table(N.market('NQ'), lambda d: d <= N.JOIN) + day_table(N.market('MNQX'), lambda d: d > N.JOIN)
    df = pd.DataFrame(rows).sort_values('date').reset_index(drop=True); dates = df.date
    fit = (dates >= FIT[0]) & (dates <= FIT[1]); val = (dates >= VAL[0]) & (dates <= VAL[1]); hold = dates >= HOLD[0]
    pr(f"sessions {len(df):,} ({dates.iloc[0].date()} .. {dates.iloc[-1].date()}): FIT {fit.sum():,}, VALIDATE {val.sum():,}, HOLDOUT {hold.sum():,}")
    meta = np.where(df.nt > 0, (df.pnl > 0).astype(float), np.nan)
    pr(f"meta label defined on {np.isfinite(meta).mean() * 100:.0f}% of sessions (NoiseArea traded); positive share {np.nanmean(meta) * 100:.0f}%")
    results = []
    for T in CALLS:
        F = np.vstack(df[f'F{T}'].values); med = np.median(df.loc[fit, f'eff{T}']); eff = (df[f'eff{T}'] >= med).astype(float).values
        sdv = (F[fit.values].std(axis=0)); sdv[sdv == 0] = 1.0; Z = F / sdv
        pr(f"\ncall {T // 60}:{T % 60:02d}: {F.shape[1]} features; eff label median {med:.3f} (FIT), trend share FIT {eff[fit.values].mean() * 100:.0f}% / "
           f"VALIDATE {eff[val.values].mean() * 100:.0f}% / HOLDOUT {eff[hold.values].mean() * 100:.0f}%")
        labels = {'eff': eff, 'meta': meta}
        scores = {(K, lb): np.full(len(df), np.nan) for K in KS for lb in labels}
        for k in range(MIN_PRIOR, len(df)):
            d2 = ((Z[:k] - Z[k]) ** 2).sum(axis=1); order = np.argsort(d2)
            for lb, y in labels.items():
                yy = y[:k][order]; yy = yy[np.isfinite(yy)]
                for K in KS:
                    if len(yy) >= K: scores[(K, lb)][k] = yy[:K].mean()
        for lb, y in labels.items():
            for K in KS:
                sc = scores[(K, lb)]
                a_f, n_f = auc(sc[fit.values], y[fit.values]); a_v, n_v = auc(sc[val.values], y[val.values]); a_h, n_h = auc(sc[hold.values], y[hold.values])
                primary = (T == 630 and K == 50 and lb == 'eff')
                results.append((T, K, lb, a_f, a_v, a_h, n_v, n_h, primary))
                pr(f"   kNN K={K:<3} label {lb:<4}: AUC FIT(2012-14) {a_f:.3f}  VALIDATE {a_v:.3f} (n {n_v:,})  HOLDOUT {a_h:.3f} (n {n_h:,})"
                   + ("   <- PRIMARY" if primary else ""))
            # benchmark: the size of the move so far in sigma units (what NoiseArea already reacts to)
            a_v, _ = auc(df[f'absz{T}'].values[val.values], y[val.values]); a_h, _ = auc(df[f'absz{T}'].values[hold.values], y[hold.values])
            pr(f"   benchmark |move so far| / sigma, label {lb}: VALIDATE {a_v:.3f}  HOLDOUT {a_h:.3f}")
        save()
    prim = [r for r in results if r[8]][0]
    passed = prim[4] >= 0.60 and prim[5] >= 0.60
    pr(f"\nPRIMARY (10:30, K=50, eff): VALIDATE AUC {prim[4]:.3f}, HOLDOUT {prim[5]:.3f} -> {'PASS' if passed else 'DEAD (bar 0.60 on both)'}")
    best = max(results, key=lambda r: min(r[4], r[5]))
    pr(f"best of all {len(results)} looks by min(VALIDATE, HOLDOUT): call {best[0] // 60}:{best[0] % 60:02d} K={best[1]} {best[2]}: {best[4]:.3f} / {best[5]:.3f} (information; selection on both windows)")
    if passed:
        # stand-down gate at tau 0.5 on the holdout, 10:30 call
        T = 630; sc = None
        F = np.vstack(df[f'F{T}'].values); sdv = F[fit.values].std(axis=0); sdv[sdv == 0] = 1.0; Z = F / sdv
        med = np.median(df.loc[fit, f'eff{T}']); eff = (df[f'eff{T}'] >= med).astype(float).values; sc = np.full(len(df), np.nan)
        for k in range(MIN_PRIOR, len(df)):
            d2 = ((Z[:k] - Z[k]) ** 2).sum(axis=1); sc[k] = eff[:k][np.argsort(d2)[:50]].mean()
        gated = np.where(np.isfinite(sc) & (sc < 0.5), df[f'sd{T}'], df.pnl)
        for lab, m in (('VALIDATE', val.values), ('HOLDOUT', hold.values)):
            b = df.pnl.values[m]; g = gated[m]
            pr(f"   gate tau 0.5 {lab}: base {b.sum():,.0f} pts, gated {g.sum():,.0f} pts, stood down on {np.mean(sc[m] < 0.5) * 100:.0f}% of days")
    pr(f"\n{time.time() - t0:,.0f} s"); save()


if __name__ == '__main__':
    main()
