import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import src2_lib as L2
import reversal as R

OUT = os.path.join(HERE, 'REVERSAL_RESULTS.txt')
BAR = NormalDist().inv_cdf(1 - 0.05 / 13)
STOP_PTS = 30.0
TRIALS = {'R6': (False, 100), 'R7': (False, 200), 'R8': (False, 300), 'R9': (True, 100), 'R10': (True, 200), 'R11': (True, 300), 'R12': (False, 50), 'R13': (True, 50)}
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def bracket(mk, j, i1, dr, stp, tgt):
    for i in range(j, i1 + 1):
        if dr > 0 and mk.l[i] <= stp: return (stp if (i == j or mk.o[i] > stp) else mk.o[i]), i, 's'
        if dr < 0 and mk.h[i] >= stp: return (stp if (i == j or mk.o[i] < stp) else mk.o[i]), i, 's'
        if (dr > 0 and mk.h[i] >= tgt + R.TICK) or (dr < 0 and mk.l[i] <= tgt - R.TICK): return tgt, i, 't'
    return mk.c[i1], i1, 'c'


def fade(M, turn, tgt_pts):
    mk, D = M.mk, M.D; out = []
    for k in range(15, len(D)):
        if D.iid.iat[k - 1] != D.iid.iat[k] or (D.date.iat[k] - D.date.iat[k - 1]).days > 5: continue
        O, pc = D.O.iat[k], D.C.iat[k - 1]; lo_a, hi_a = min(O, pc), max(O, pc)
        i1 = D.i1.iat[k]; used = {1: False, -1: False}; busy = -1
        for chk in R.CHECKS:
            p = chk - 571; sg = M.sigma[k, p]; c = M.C1[k, p]
            if np.isnan(sg): continue
            dr = 1 if c < lo_a * (1 - 2 * sg) else (-1 if c > hi_a * (1 + 2 * sg) else 0)
            if not dr or used[dr]: continue
            j = R.first_index(M.IX[k], chk)
            if j is None or j <= busy: continue
            if turn:
                j = None
                for b in range(6):
                    s0 = chk + 5 * b; a1 = R.first_index(M.IX[k], s0 + 4)
                    p0 = R.first_index(M.IX[k], s0 - 5); p1 = R.first_index(M.IX[k], s0 - 1); nx = R.first_index(M.IX[k], s0 + 5)
                    if None in (a1, p0, p1, nx): continue
                    cl = mk.c[a1]; ph, pl = mk.h[p0:p1 + 1].max(), mk.l[p0:p1 + 1].min()
                    if (dr > 0 and cl > ph) or (dr < 0 and cl < pl): j = nx; break
                if j is None or j <= busy: continue
            used[dr] = True; e = mk.o[j]; S = STOP_PTS / L2.P_REF * e; T = tgt_pts / L2.P_REF * e
            x, ix, how = bracket(mk, j, i1, dr, e - dr * S, e + dr * T); busy = ix
            out.append((D.date.iat[k], dr * (x - e) / O, 1, how))
    return out


def main():
    say(f"reversal2.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (REVERSAL_PLAN.txt amendment A1)  stop {STOP_PTS:.0f} pt at today's price, "
        f"cost {R.LIVE} ticks + $0.50 a side; G1 bar t >= {BAR:.2f}")
    M = {nm: R.Market(nm) for nm in R.WIN}
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close * L2.N_USD
    U = L2.N_USD; survivors = []
    say("=" * 124)
    for r, (turn, tp) in TRIALS.items():
        T = {nm: fade(M[nm], turn, tp) for nm in R.WIN}
        s = {nm: R.daily([t[:3] for t in T[nm]], R.LIVE, *R.WIN[nm]) for nm in R.WIN}
        g0 = {nm: R.daily([t[:3] for t in T[nm]], 0, *R.WIN[nm]) for nm in ('nq', 'mnqx')}
        nq, mq = s['nq'][0], s['mnqx'][0]; both = pd.concat([nq, mq]); yrs = both.groupby(both.index.year).sum()
        d5 = both.drop(both.sort_values(ascending=False).index[:5]) if len(both) > 5 else both
        es_m = s['es'][1].mean() if len(s['es'][1]) else float('nan'); mes_m = s['mes'][1].mean() if len(s['mes'][1]) else float('nan')
        hows = [t[3] for nm in ('nq', 'mnqx') for t in T[nm]]; n = len(hows)
        g = {'G1': R.tt(nq) >= BAR, 'G2': mq.mean() > 0 and R.tt(mq) >= 2.0, 'G3': int((yrs < 0).sum()) <= 3, 'G4': es_m > 0 and mes_m > 0,
             'G5': R.tt(d5) >= 2.0}
        say(f"{r} {'turn-bar' if turn else 'at-check'} entry, stop 30 / target {tp}: NQ 2010-19 ${nq.sum() * U / 8.85:7,.0f}/yr t {R.tt(nq):+5.2f} | "
            f"MNQ 2019-26 ${mq.sum() * U / 7.45:7,.0f}/yr t {R.tt(mq):+5.2f} | ES ${es_m * U:+6.2f} MES ${mes_m * U:+6.2f}/trade | "
            f"{n / 16.3:3.0f} trades/yr | losing years {int((yrs < 0).sum())}/{len(yrs)} | drop5 t {R.tt(d5):+5.2f} | 0-cost t {R.tt(g0['nq'][0]):+5.2f}/{R.tt(g0['mnqx'][0]):+5.2f}")
        say(f"     target hit {hows.count('t') / n:.1%} (breakeven {30 / (30 + tp):.1%}), stopped {hows.count('s') / n:.1%}, out at 16:00 {hows.count('c') / n:.1%} | "
            f"corr X3 {both.reindex(x3.index).fillna(0).corr(x3):+.2f}")
        fails = [k_ for k_, v in g.items() if not v]
        say(f"     VERDICT {r}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(r)
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\nAMENDMENT A1 RESULTS\n" + "\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
