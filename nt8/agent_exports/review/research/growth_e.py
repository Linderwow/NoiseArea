import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import src2_lib as L2
import ml_lab as ML
import ml_run as MR
import ind_lab as IL

OUT = os.path.join(HERE, 'GROWTH_RESULTS.txt')
BAR = 2.76
JOIN = pd.Timestamp('2019-05-05')
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def walk(T, first=2013):
    pred = np.full(len(T), np.nan); yr = T.date.dt.year.values
    for y in sorted(set(yr)):
        if y < first: continue
        tr = (yr < y) & np.isfinite(T.y.values); te = yr == y
        if te.sum() == 0 or tr.sum() < 5000: continue
        m = MR.model(); m.fit(T.loc[tr, IL.FEATS].values, T.y.values[tr]); pred[te] = m.predict(T.loc[te, IL.FEATS].values)
    return pred


def fam(a, b, xa, xb, H):
    Ta = IL.features(a, xa, H, H); Ta = Ta[Ta.date <= JOIN]
    Tb = IL.features(b, xb, H, H); Tb = Tb[Tb.date > JOIN].assign(k=lambda d: d.k + 10 ** 6)
    T = pd.concat([Ta, Tb], ignore_index=True); T = T[T.dec >= 15].reset_index(drop=True)
    return T, walk(T)


def audit(S):
    T = IL.features(S, None, 15, 15); r = T[(T.dec == 120)].iloc[1500]; k, p_ = int(r.k), int(r.dec)
    nb, o, h, l, c, v = IL.tf_bars(S, 15); j = k * nb + p_ // 15 - 1
    hand_c = S.C[k, p_ - 1]; ok1 = abs(c[j] - hand_c) < 1e-9
    ind = IL.indicators(o, h, l, c, v); ok2 = abs(r.rsi15 - ind['rsi'][j]) < 1e-12
    say(f"audit: {pd.Timestamp(r.date):%Y-%m-%d} decision {570 + p_} min - the last completed 15-min bar closes at the decision minute's close "
        f"{ok1}; RSI(15) row == indicator series {ok2} -> {'PASS' if ok1 and ok2 else 'FAIL'}")
    return ok1 and ok2


def main():
    say(); say("=" * 124); say(f"G-E  growth_e.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  indicator-library model ({len(IL.FEATS)} features), NQ/MNQ + ES/MES; G1 bar t >= {BAR:.2f}")
    say("=" * 124)
    nq, mq, es, ms = (ML.Sessions(n) for n in ('nq', 'mnqx', 'es', 'mes'))
    if not audit(nq): say("audit FAILED - nothing graded"); return
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close * L2.N_USD
    U = L2.N_USD; c1 = MR.SIDE(2.35); thr = 4 * c1; surv = []; keep = {}
    for H in (5, 15, 30, 60, 120):
        name = f'E{H}'
        T, pred = fam(nq, mq, es, ms, H)
        s, sides = ML.trade(T, pred, thr, c1); s0, _ = ML.trade(T, pred, thr, 0.0)
        s = s[s.index.year >= 2013]; s0 = s0[s0.index.year >= 2013]
        disc, hold = s[s.index <= JOIN], s[s.index > JOIN]
        ok = np.isfinite(pred) & np.isfinite(T.y.values); ic = np.corrcoef(pred[ok], T.y.values[ok])[0, 1]
        Te, pe = fam(es, ms, nq, mq, H); se, _ = ML.trade(Te, pe, thr, c1); se = se[se.index.year >= 2013]
        es_m, mes_m = se[se.index <= JOIN].mean(), se[se.index > JOIN].mean()
        yrs = s.groupby(s.index.year).sum(); d5 = s.drop(s.sort_values(ascending=False).index[:5])
        dd_ = hold * U; eq = dd_.cumsum(); mdd = float((eq.cummax() - eq).max()); worst = float(dd_.min())
        g = {'G1': MR.tt(disc) >= BAR, 'G2': hold.mean() > 0 and MR.tt(hold) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2, 'G4': es_m > 0 and mes_m > 0,
             'G5': MR.tt(d5) >= 2.0}
        say(f"{name}: hold {H} min | OOS forecast correlation {ic:+.4f} | NQ 2013-19 ${disc.mean() * U * 252:7,.0f}/yr t {MR.tt(disc):+5.2f} | "
            f"MNQ 2019-26 ${hold.mean() * U * 252:7,.0f}/yr t {MR.tt(hold):+5.2f} | ES ${es_m * U * 252:+,.0f} MES ${mes_m * U * 252:+,.0f}/yr | "
            f"losing yrs {int((yrs < 0).sum())}/{len(yrs)} | drop5 t {MR.tt(d5):+.2f} | zero-cost t {MR.tt(s0):+.2f} | corr X3 {s.reindex(x3.index).fillna(0).corr(x3):+.2f}")
        say(f"     Bulenox fit (2019-26, 1 micro): max DD ${mdd:,.0f} (limit $2,500), worst day ${worst:,.0f} (limit -$1,100) -> "
            f"{'fits' if mdd < 2500 and worst > -1100 else 'does NOT fit'} | {time.time() - T0:.0f} s")
        fails = [k_ for k_, v in g.items() if not v]
        say(f"     VERDICT {name}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: surv.append(name)
        keep[name] = s * U
    pd.DataFrame(keep).to_csv(os.path.join(HERE, 'regime_work', 'growth_e_daily.csv'))
    say(); say("G-E SURVIVORS: " + (", ".join(surv) if surv else "none")); say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"appended to {OUT}")
