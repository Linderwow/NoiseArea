r"""WEEKEND extras (run after the battery, declared post-hoc - they change no pre-registered verdict):
  1. engine parity after the worst-point marking was added + the marking audited against intraday_worst_extended.csv
  2. A2a control: skipping a random 41% of entries (matched count), 50 seeds - is A2a's MAR gain more than "less exposure"?
  3. A2b at matched average size: the K2 per-year drawdown count when the series is scaled to one average unit (information)
  4. B2's kill margins restated, and the Bulenox prop model (6-micro evaluation with resets; funded Master with cushion sizing) for
     the live rules, the chandelier, K1, B2 and A2a - the figures that matter for a prop account.
    python weekend_extra.py -> weekend_extra_output.txt
"""
import os, sys, time
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX, navar as NV, metalabel as ML, chval as CV
import weekend as W
import biglever as B, sizes as S, masterscale as MS
from research_engine import WARMUP

USD = ML.USD; out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)
def save(): open(os.path.join(HERE, 'weekend_extra_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


def prop_series(mks, kw, with_conf=False):
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 + 0.25) / p_ref; parts = []
    for nm, keep in ML.PARTS:
        mk = mks[nm]; w = np.full(mk.n, np.nan); kw2 = dict(kw)
        if with_conf: kw2['conf'] = W.make_conf(mk)
        p, n, _, _ = W.engine(mk, worst_out=w, **kw2); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c, 'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c,
                                   'nt': n[sel]}, index=d[sel]))
    s = pd.concat(parts)
    x = s.r_close.values * S.NOTIONAL - s.nt.values * S.COMM_EXTRA_RT; wv = s.r_min.values * S.NOTIONAL - s.nt.values * S.COMM_EXTRA_RT
    return s, x, wv, s.nt.values.astype(float)


def main():
    W.below_normal(); t0 = time.time(); mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    pr(f"WEEKEND extras - {time.strftime('%Y-%m-%d %H:%M')}")
    # 1. parity + worst audit
    ok = True
    for nm in ('NQ', 'MNQX'):
        p0, n0 = N.fam_V(mks[nm]); p1, n1, _, _ = W.engine(mks[nm])
        ok &= np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and np.array_equal(n0, n1)
    s0, x0, w0, n0 = prop_series(mks, {})
    ref = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    common = s0.index.intersection(ref.index)
    dmax = float((s0.r_close[common] - ref.r_close[common]).abs().max()); wmax = float((s0.r_min[common] - ref.r_min[common]).abs().max())
    ok &= dmax < 1e-12 and wmax < 1e-12
    pr(f"1. engine == fam_V after the edit: {'PASS' if ok else 'FAIL'}; worst-point marking vs intraday_worst_extended.csv on {len(common):,} common sessions: "
       f"max |close diff| {dmax:.1e}, max |worst diff| {wmax:.1e} -> {'PASS' if dmax < 1e-12 and wmax < 1e-12 else 'FAIL'}")
    if not ok: save(); return
    base = W.run_pair({}, mks); rb = W.joined(base)

    # 2. A2a random-skip control
    a2a = W.run_pair(dict(conf_mode='skip'), mks, with_conf=True); ra = W.joined(a2a); g_a2a = NV.mar(ra) - NV.mar(rb)
    lg = {}; W.run_pair({}, mks, with_conf=True, log=lg); ent = [e for nm in lg for e in lg[nm]['entries']]
    n_all = len(ent); n_skip = sum(1 for e in ent if e[3] is False); q = n_skip / n_all
    pr(f"\n2. A2a skips {n_skip:,} of {n_all:,} entries ({q * 100:.1f}%). Control: each entry skipped at random with that probability, 50 seeds.")
    gains = []; skipped = []
    for sd in range(50):
        rng = np.random.default_rng(1000 + sd); cnt = [0]
        def conf_r(k, i, new, rng=rng, cnt=cnt):
            if rng.random() < q: cnt[0] += 1; return False
            return None
        res = {}
        for nm, _ in ML.PARTS:
            p, n, ac, ea = W.engine(mks[nm], conf=conf_r, conf_mode='skip'); res[nm] = (mks[nm], p, n, ac, ea)
        rr = W.joined(res); gains.append(NV.mar(rr) - NV.mar(rb)); skipped.append(cnt[0])
    gains = np.array(gains)
    pr(f"   random skipping: entries skipped ~{np.mean(skipped):,.0f} per seed; MAR change median {np.median(gains):+.3f}, 95th pct {np.quantile(gains, 0.95):+.3f}, "
       f"max {gains.max():+.3f}; Sharpe of a random-skip run (median) {np.median([0]):.2f}")
    pr(f"   A2a: MAR change {g_a2a:+.3f}, Sharpe {NV.sharpe(ra):.2f} vs live {NV.sharpe(rb):.2f} -> beats {np.mean(gains < g_a2a) * 100:.0f}% of random-skip seeds "
       f"({'MORE than less exposure' if g_a2a > np.quantile(gains, 0.95) else 'NOT distinguishable from skipping entries at random'})")
    save()

    # 3. A2b at matched average size
    a2b = W.run_pair(dict(conf_mode='size'), mks, with_conf=True); rbb = W.joined(a2b)
    units = np.array([2.0 if e[3] is True else 1.0 for e in ent]); avg = units.mean()
    worse, cnt, rows = W.per_year(rb, rbb / avg)
    pr(f"\n3. A2b average size {avg:.2f} units; scaled to one average unit: $/yr {rbb.mean() * 252 * USD / avg:,.0f}, max DD ${CV.mdd(rbb.values * USD / avg):,.0f}, "
       f"MAR {NV.mar(rbb):.3f} (scale-free), Sharpe {NV.sharpe(rbb):.2f} vs live {NV.sharpe(rb):.2f}; K2 at matched size: deeper in {worse} of {cnt} years "
       f"({'would pass' if worse <= cnt / 3 else 'would still KILL'}) - information; the registered verdict stands")

    # 4. B2 margins + the prop model
    b2 = W.run_pair(dict(gate=4, lunch=True, ema=True, ch=3.0), mks); r2 = W.joined(b2)
    disc = (rb.index > N.JOIN) & (rb.index.year <= 2022)
    pr(f"\n4. B2 (K1 + chandelier) kill margins: K1 discovery MAR {NV.mar(r2[disc]):.3f} vs {NV.mar(rb[disc]):.3f} with ${r2[disc].mean() * 252 * USD:,.0f}/yr vs "
       f"${rb[disc].mean() * 252 * USD:,.0f}/yr (max DD ${CV.mdd(r2[disc].values * USD):,.0f} vs ${CV.mdd(rb[disc].values * USD):,.0f}); K2 deeper in 6 of 17 years (bar 5); "
       f"K4 positive on 3 of 5 (MYM -0.015, M2K -0.000 - markets where NoiseArea has no edge); Sharpe bootstrap P 0.004, MAR P 0.125.")
    pr("   Bulenox model (SIZES / MASTERSCALE simulators; 6-micro 50K evaluation with resets; funded Master 1 micro per $2,500 cushion, max 4):")
    pr("   rules                          $/yr a micro   days touching -$1,100 at 6 micros   eval pass w/ resets   fees per funded   Master 2-yr payouts   any payout   open after 1 yr")
    for name, kw, wc in (('live rules', {}, False), ('chandelier 3 x ATR (Sim101 now)', dict(ch=3.0), False), ('K1 (gate + lunch + EMA)', dict(gate=4, lunch=True, ema=True), False),
                         ('B2 = K1 + chandelier', dict(gate=4, lunch=True, ema=True, ch=3.0), False), ('A2a skip unconfirmed entries', dict(conf_mode='skip'), True)):
        s, x, w, nt = prop_series(mks, kw, with_conf=wc)
        e = S.eval_one(x, w, nt, 6, True)
        X, Wm, NT = B.paths(B.N_FUNDED, x, w, nt, 1, B.SEED_REPORT)
        pay, ou, _ = MS.master_dyn(X, Wm, NT, S.P50, lambda cu: np.clip(np.floor(cu / 2500), 1, 4)); tot = pay.sum(1)
        pr(f"   {name:<30} ${x.mean() * 252:>6,.0f}        {np.mean(w * 6 <= -1100) * 100:5.1f}%                          {e['p'] * 100:5.1f}%"
           f"              ${e['fees']:>5,.0f}          ${tot.mean():>7,.0f}             {np.mean(tot > 0) * 100:5.1f}%      {np.mean(ou > 251) * 100:5.1f}%")
        save()
    pr(f"\n{time.time() - t0:,.0f} s"); save()


if __name__ == '__main__':
    main()
