import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import ml_lab as ML
import ml_run as MR

OUT = os.path.join(HERE, 'GROWTH_RESULTS.txt')
BAR = 2.76
SPEC = {'mes': (5, 0.25, 0.61), 'mym': (0.5, 1.0, 0.61), 'm2k': (5, 0.1, 0.61), 'mgc': (10, 0.1, 0.76), 'mcl': (100, 0.01, 0.76),
        'm6e': (12500, 0.0001, 0.50), 'mbt': (0.1, 5.0, 2.76)}
TRIAL = {'mes': 'A1', 'mym': 'A2', 'm2k': 'A3', 'mgc': 'A4', 'mcl': 'A5', 'm6e': 'A6', 'mbt': 'A7'}
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def main():
    say(); say("=" * 124); say(f"G-A  growth_a.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  the 15-min model on every other micro (GROWTH_PLAN.txt); G1 bar t >= {BAR:.2f}")
    say("=" * 124)
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close
    cross = {'mes': ML.Sessions('mnqx'), 'other': ML.Sessions('mes')}
    surv = []; series = {}
    for nm, (pv, tick, comm) in SPEC.items():
        S = ML.Sessions(nm); X = cross['mes'] if nm == 'mes' else cross['other']
        last = float(S.C[-1, -1]); usd = last * pv
        c1 = (tick * pv + comm) / usd; c2 = (2 * tick * pv + comm) / usd
        T = ML.features(S, X, 15, 15); years = sorted(T.date.dt.year.unique()); y0 = years[0]; first = y0 + 2
        pred = ML.walk_forward(T, years, first, MR.model)
        s, sides = ML.trade(T, pred, 2 * 2 * c1, c1); s2, _ = ML.trade(T, pred, 2 * 2 * c1, c2); s0, _ = ML.trade(T, pred, 2 * 2 * c1, 0.0)
        keep = lambda x: x[x.index.year >= first]
        s, s2, s0 = keep(s), keep(s2), keep(s0)
        ok = np.isfinite(pred) & np.isfinite(T.y.values); ic = np.corrcoef(pred[ok], T.y.values[ok])[0, 1] if ok.sum() > 100 else float('nan')
        yrs = s.groupby(s.index.year).sum(); last2 = s[s.index.year >= max(years) - 1]; d5 = s.drop(s.sort_values(ascending=False).index[:5])
        maxlose = 1 if first >= 2023 else 2
        g = {'G1': MR.tt(s) >= BAR, 'G2': last2.mean() > 0 and MR.tt(last2) >= 1.5, 'G3': int((yrs < 0).sum()) <= maxlose,
             'G4': MR.tt(s2) >= 1.5, 'G5': MR.tt(d5) >= 2.0}
        d = s * usd; eq = d.cumsum(); dd = float((eq.cummax() - eq).max())
        series[nm] = d
        say(f"{TRIAL[nm]} {nm.upper():4s} test {first}-{max(years)} | {len(S.D)} sessions | $ per contract at {last:,.4g}: cost/side ${c1 * usd:.2f}")
        say(f"     ${d.mean() * 252:7,.0f}/yr t {MR.tt(s):+5.2f} | last 2 yrs t {MR.tt(last2):+5.2f} | losing years {int((yrs < 0).sum())}/{len(yrs)} | "
            f"2 ticks t {MR.tt(s2):+5.2f} | drop5 t {MR.tt(d5):+5.2f} | zero-cost t {MR.tt(s0):+5.2f} | IC {ic:+.4f} | max DD ${dd:,.0f} | "
            f"worst day ${d.min():,.0f} | corr X3 {s.reindex(x3.index).dropna().corr(x3.reindex(s.index).dropna()):+.2f} | {time.time() - T0:.0f} s")
        fails = [k for k, v in g.items() if not v]
        say(f"     VERDICT {TRIAL[nm]}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: surv.append(TRIAL[nm])
    pd.DataFrame(series).to_csv(os.path.join(HERE, 'regime_work', 'growth_a_daily.csv'))
    say(); say("G-A SURVIVORS: " + (", ".join(surv) if surv else "none")); say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"appended to {OUT}")
