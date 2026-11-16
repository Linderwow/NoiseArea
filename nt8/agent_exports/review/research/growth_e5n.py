import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2, ml_lab as ML, ml_run as MR, ind_lab as IL, growth_e as GE
import biglever as BL, bulenox_routes as BR

CROSS = ['x_r15', 'x_r60', 'x_ropen', 'rel15', 'rel_open']
IL.FEATS = [f for f in IL.FEATS if f not in CROSS]
BAR = 2.77
OUT = os.path.join(HERE, 'GROWTH_RESULTS.txt'); LINES = []


def say(s=''):
    print(s, flush=True); LINES.append(s)


def fam(a, b):
    Ta = IL.features(a, None, 5, 5); Ta = Ta[Ta.date <= GE.JOIN]
    Tb = IL.features(b, None, 5, 5); Tb = Tb[Tb.date > GE.JOIN].assign(k=lambda d: d.k + 10 ** 6)
    T = pd.concat([Ta, Tb], ignore_index=True); T = T[T.dec >= 15].reset_index(drop=True); return T, GE.walk(T)


def main():
    say(); say(f"E5n (amendment 2, trial 18, bar t >= {BAR}): E5 without the cross-index features, {len(IL.FEATS)} features")
    U = L2.N_USD; c1 = MR.SIDE(2.35); c2 = MR.SIDE(4.7)
    T, p = fam(ML.Sessions('nq'), ML.Sessions('mnqx'))
    s, _ = ML.trade(T, p, 4 * c1, c1); s2, _ = ML.trade(T, p, 4 * c1, c2); s0, _ = ML.trade(T, p, 4 * c1, 0.0)
    s, s2, s0 = (x[x.index.year >= 2013] for x in (s, s2, s0))
    Te, pe = fam(ML.Sessions('es'), ML.Sessions('mes')); se, _ = ML.trade(Te, pe, 4 * c1, c1); se = se[se.index.year >= 2013]
    d, h = s[s.index <= GE.JOIN], s[s.index > GE.JOIN]; yrs = s.groupby(s.index.year).sum(); d5 = s.drop(s.sort_values(ascending=False).index[:5])
    es_m, mes_m = se[se.index <= GE.JOIN].mean(), se[se.index > GE.JOIN].mean()
    hu = h * U; eq = hu.cumsum(); mdd = float((eq.cummax() - eq).max())
    g = {'G1': MR.tt(d) >= BAR, 'G2': h.mean() > 0 and MR.tt(h) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2, 'G4': es_m > 0 and mes_m > 0, 'G5': MR.tt(d5) >= 2.0}
    full = s * U; sh = full.mean() / full.std() * np.sqrt(252); eqf = full.cumsum(); ddf = float((eqf.cummax() - eqf).max())
    say(f"   NQ 2013-19 ${d.mean() * U * 252:,.0f}/yr t {MR.tt(d):+.2f} | MNQ 2019-26 ${h.mean() * U * 252:,.0f}/yr t {MR.tt(h):+.2f} | ES ${es_m * U * 252:+,.0f} "
          f"(t {MR.tt(se[se.index <= GE.JOIN]):+.2f}) MES ${mes_m * U * 252:+,.0f}/yr (t {MR.tt(se[se.index > GE.JOIN]):+.2f}) | losing yrs {int((yrs < 0).sum())}/{len(yrs)} | "
          f"drop5 t {MR.tt(d5):+.2f} | 2 ticks a side more t {MR.tt(s2):+.2f} | zero-cost t {MR.tt(s0):+.2f}")
    say(f"   2013-26 ${full.mean() * 252:,.0f}/yr Sharpe {sh:.2f} max DD ${ddf:,.0f} | 2023-26 t {MR.tt(s[s.index >= '2023-01-01']):+.2f} | per year: "
        + "; ".join(f"{y} {v * U:+,.0f}" for y, v in yrs.items()))
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close * U
    b = pd.concat([full, x3], axis=1, join='inner').fillna(0); b.columns = ['m', 'x']; cm = b.m / b.m.std() + b.x / b.x.std()
    say(f"   with NoiseArea X3 at equal risk: Sharpe {cm.mean() / cm.std() * np.sqrt(252):.2f} vs X3 {b.x.mean() / b.x.std() * np.sqrt(252):.2f} (corr {b.m.corr(b.x):+.2f})")
    a = BR.SIZES['50K']; u = hu.values; nt = (u != 0).astype(float)
    e = BL.eval_stats(u, u, nt, 1, BL.SEED_REPORT, a['dd'], a['dll'], a['target'], a['qual'], resets=True)
    f = BL.funded_stats(BL.master_qm, u, u, nt, a, 1, BL.SEED_REPORT)
    say(f"   Bulenox 50K 2019-26, 1 micro (close marks, optimistic): max DD ${mdd:,.0f}, worst day ${hu.min():,.0f}; evaluation pass {e['p'] * 100:.1f}% per try; "
        f"Master 2-yr payouts ${f['pay']:,.0f}, open after 1 yr {f['open1'] * 100:.0f}%")
    fails = [k for k, v in g.items() if not v]
    say(f"   VERDICT E5n: {'PASSES THE GATES - forward-test candidate only (chosen after G-E)' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
    pd.DataFrame({'E5n': full}).to_csv(os.path.join(HERE, 'regime_work', 'growth_e5n_daily.csv'))


if __name__ == '__main__':
    main()
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
