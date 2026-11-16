import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
from sklearn.ensemble import HistGradientBoostingRegressor
import src2_lib as L2
import ml_lab as ML

OUT = os.path.join(HERE, 'EDGEHUNT_RESULTS.txt')
BAR = NormalDist().inv_cdf(1 - 0.05 / 6)
SIDE = lambda ticks: (0.5 * ticks + 0.61) / L2.N_USD
JOIN = pd.Timestamp('2019-05-05')
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def tt(x):
    x = np.asarray(x, float); s = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / (s / np.sqrt(len(x)))) if s > 0 else 0.0


def model(): return HistGradientBoostingRegressor(learning_rate=0.05, max_iter=300, max_leaf_nodes=15, min_samples_leaf=300,
                                                  l2_regularization=1.0, early_stopping=False, random_state=0)


def family(a, b, xa, xb, step, H):
    Ta = ML.features(a, xa, step, H); Ta = Ta[Ta.date <= JOIN]
    Tb = ML.features(b, xb, step, H); Tb = Tb[Tb.date > JOIN]; Tb = Tb.assign(k=Tb.k + 10 ** 6)
    T = pd.concat([Ta, Tb], ignore_index=True)
    years = sorted(T.date.dt.year.unique())
    pred = ML.walk_forward(T, years, 2013, model)
    return T, pred


def audit():
    S = ML.Sessions('nq'); T = ML.features(S, None, 15, 15); first = T[T.dec == 15].iloc[100]
    first_nan = bool(np.isnan(first.r15)) and bool(np.isnan(first.r60))
    r = T[T.dec >= 120].iloc[5000]; k = int(r.k); p = int(r.dec) - 1
    hand_r15 = np.log(S.C[k, p] / S.C[k, p - 15]); hand_y = np.log(S.C[k, p + 15] / S.O[k, p + 1])
    ok = abs(r.r15 - hand_r15) < 1e-12 and abs(r.y - hand_y) < 1e-12 and r.entry == S.O[k, p + 1] and first_nan
    say(f"audit: {pd.Timestamp(r.date):%Y-%m-%d} decision {570 + int(r.dec)} min - r15 and the target recomputed by hand {ok}; every feature uses "
        f"minutes <= the decision, the target starts at the next minute's open -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    say(); say("=" * 120); say(f"PART L  ml_run.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  self-retraining model (HistGradientBoosting, refit every January, expanding)")
    say(f"cost 2.35 ticks + $0.61 a side at today's MNQ price; trade when |forecast| > 2 x round trip; G1 bar t >= {BAR:.2f}"); say("=" * 120)
    if not audit(): say("audit FAILED - nothing graded"); return
    nq, mq, es, ms = (ML.Sessions(n) for n in ('nq', 'mnqx', 'es', 'mes'))
    say(f"sessions: NQ {len(nq.D)}, MNQ {len(mq.D)}, ES {len(es.D)}, MES {len(ms.D)}; {time.time() - T0:.0f} s")
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close * L2.N_USD
    U = L2.N_USD; c1 = SIDE(2.35); thr = 2 * 2 * c1; surv = []
    for name, step, H in (('L1', 15, 15), ('L2', 60, 60)):
        T, pred = family(nq, mq, es, ms, step, H)
        s, _ = ML.trade(T, pred, thr, c1); s0, _ = ML.trade(T, pred, thr, 0.0)
        s = s[s.index.year >= 2013]; s0 = s0[s0.index.year >= 2013]
        disc, hold = s[s.index <= JOIN], s[s.index > JOIN]
        ok_ = np.isfinite(pred) & np.isfinite(T.y.values)
        ic = np.corrcoef(pred[ok_], T.y.values[ok_])[0, 1]
        hit = np.mean(np.sign(pred[ok_]) == np.sign(T.y.values[ok_]))
        Te, pe = family(es, ms, nq, mq, step, H); se, _ = ML.trade(Te, pe, thr, c1); se = se[se.index.year >= 2013]
        es_m, mes_m = se[se.index <= JOIN].mean(), se[se.index > JOIN].mean()
        yrs = s.groupby(s.index.year).sum(); d5 = s.drop(s.sort_values(ascending=False).index[:5])
        g = {'G1': tt(disc) >= BAR, 'G2': hold.mean() > 0 and tt(hold) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2, 'G4': es_m > 0 and mes_m > 0, 'G5': tt(d5) >= 2.0}
        say(f"{name} horizon {H} min, decisions every {step} min: forecast-vs-outcome correlation {ic:+.4f}, direction right {hit:.1%} (out of sample, all years)")
        say(f"   NQ 2013-19 ${disc.sum() * U / max(len(disc), 1) * 252:7,.0f}/yr t {tt(disc):+5.2f} | MNQ 2019-26 ${hold.sum() * U / max(len(hold), 1) * 252:7,.0f}/yr t {tt(hold):+5.2f} | "
            f"ES ${es_m * U * 252:+,.0f}/yr MES ${mes_m * U * 252:+,.0f}/yr | losing years {int((yrs < 0).sum())}/{len(yrs)} | drop5 t {tt(d5):+.2f} | "
            f"zero-cost t {tt(s0):+.2f} | corr X3 {s.reindex(x3.index).fillna(0).corr(x3):+.2f} | {time.time() - T0:.0f} s")
        fails = [k for k, v in g.items() if not v]
        say(f"   VERDICT {name}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: surv.append(name)
    say(); say("PART L SURVIVORS: " + (", ".join(surv) if surv else "none")); say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"appended to {OUT}")
