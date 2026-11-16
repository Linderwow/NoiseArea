import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import src2_lib as L2

OUT = os.path.join(HERE, 'REVERSAL_RESULTS.txt')
LIVE = 2.35
TICK = 0.25
BAR = NormalDist().inv_cdf(1 - 0.05 / 5)
WIN = {'nq': ('2010-07-01', '2019-05-05'), 'mnqx': ('2019-05-06', '2026-10-15'), 'es': ('2010-07-01', '2019-05-05'), 'mes': ('2019-05-06', '2026-10-15')}
CHECKS = list(range(600, 901, 30))
TRIALS = {'R1': 'W33 Williams %R(5) < -90 -> next cash session long', 'R2': 'W28 Connors multiple days down -> next cash session long',
          'R3': 'IBS < 0.2 -> next cash session long', 'R4': 'stretch fade: 2 noise widths beyond the band -> fade to VWAP',
          'R5': 'stretch fade with a 5-minute turn bar'}
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def tt(x):
    x = np.asarray(x, float); s = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / (s / np.sqrt(len(x)))) if s > 0 else 0.0


class Market:
    def __init__(self, name):
        mk = L2.Mins(name); self.mk = mk; self.name = name
        rows = []; grid = []
        for d in mk.dates:
            i0, i1 = mk.days[d]
            if mk.m[i1] < 959: continue
            pos = mk.m[i0:i1 + 1] - 570; c = np.full(390, np.nan); idx = np.full(390, -1, np.int64)
            ok = (pos >= 0) & (pos < 390); c[pos[ok]] = mk.c[i0:i1 + 1][ok]; idx[pos[ok]] = np.arange(i0, i1 + 1)[ok]
            c = pd.Series(c).ffill().values
            rows.append(dict(date=d, i0=i0, i1=i1, iid=mk.iid[i0], O=mk.o[i0], H=mk.h[i0:i1 + 1].max(), L=mk.l[i0:i1 + 1].min(), C=mk.c[i1],
                             aO=mk.ao[i0], aH=mk.ah[i0:i1 + 1].max(), aL=mk.al[i0:i1 + 1].min(), aC=mk.ac[i1]))
            grid.append((c, idx))
        self.D = pd.DataFrame(rows); self.C1 = np.array([g[0] for g in grid]); self.IX = np.array([g[1] for g in grid])
        D = self.D
        mv = np.abs(self.C1 / D.O.values[:, None] - 1)
        self.sigma = pd.DataFrame(mv).shift(1).rolling(14, min_periods=14).mean().values


def next_day(M, sig):
    D = M.D; out = []; split = []
    for k in range(len(D) - 1):
        if not sig[k]: continue
        d0, d1 = D.date.iat[k], D.date.iat[k + 1]
        if (d1 - d0).days > 5: continue
        out.append((d1, (D.C.iat[k + 1] - D.O.iat[k + 1]) / D.O.iat[k + 1], 1))
        split.append(((D.aO.iat[k + 1] - D.aC.iat[k]) / D.C.iat[k], (D.C.iat[k + 1] - D.O.iat[k + 1]) / D.O.iat[k + 1]))
    return out, split


def signals(M):
    D = M.D; c, h, l = D.aC.values, D.aH.values, D.aL.values
    hh = pd.Series(h).rolling(5).max().values; ll = pd.Series(l).rolling(5).min().values
    with np.errstate(invalid='ignore', divide='ignore'):
        wr = (hh - c) / np.where(hh - ll > 0, hh - ll, np.nan) * -100
        s200 = pd.Series(c).rolling(200).mean().values; s5 = pd.Series(c).rolling(5).mean().values
        dn = pd.Series(np.r_[False, np.diff(c) < 0].astype(int)).rolling(5).sum().values
        ibs = (D.C - D.L) / (D.H - D.L).where(D.H > D.L)
    return {'R1': np.nan_to_num(wr, nan=0) < -90, 'R2': (c > s200) & (c < s5) & (dn >= 4), 'R3': (ibs < 0.2).values}


def vwap_series(mk, i0, i1):
    tp = (mk.h[i0:i1 + 1] + mk.l[i0:i1 + 1] + mk.c[i0:i1 + 1]) / 3; v = mk.v[i0:i1 + 1]
    return np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1)


def run_trade(mk, j, i0, i1, dr, e, stp, vw):
    for i in range(j, i1 + 1):
        if dr > 0 and mk.l[i] <= stp: return (stp if (i == j or mk.o[i] > stp) else mk.o[i]), i
        if dr < 0 and mk.h[i] >= stp: return (stp if (i == j or mk.o[i] < stp) else mk.o[i]), i
        if i > j:
            t = vw[i - 1 - i0]
            if dr > 0 and t > e and mk.h[i] >= t + TICK: return t, i
            if dr < 0 and t < e and mk.l[i] <= t - TICK: return t, i
    return mk.c[i1], i1


def first_index(idx_row, minute):
    p = minute - 570
    return int(idx_row[p]) if 0 <= p < 390 and idx_row[p] >= 0 else None


def stretch(M, turn):
    mk, D = M.mk, M.D; out = []
    for k in range(15, len(D)):
        if D.iid.iat[k - 1] != D.iid.iat[k] or (D.date.iat[k] - D.date.iat[k - 1]).days > 5: continue
        O, pc = D.O.iat[k], D.C.iat[k - 1]; lo_a, hi_a = min(O, pc), max(O, pc)
        i0, i1 = D.i0.iat[k], D.i1.iat[k]; vw = vwap_series(mk, i0, i1); used = {1: False, -1: False}; busy = -1
        for chk in CHECKS:
            p = chk - 571; sg = M.sigma[k, p]; c = M.C1[k, p]
            if np.isnan(sg): continue
            dr = 1 if c < lo_a * (1 - 2 * sg) else (-1 if c > hi_a * (1 + 2 * sg) else 0)
            if not dr or used[dr]: continue
            j = first_index(M.IX[k], chk)
            if j is None or j <= busy: continue
            if turn:
                j = None
                for b in range(6):
                    s0 = chk + 5 * b; a0 = first_index(M.IX[k], s0); a1 = first_index(M.IX[k], s0 + 4)
                    p0 = first_index(M.IX[k], s0 - 5); p1 = first_index(M.IX[k], s0 - 1); nx = first_index(M.IX[k], s0 + 5)
                    if None in (a0, a1, p0, p1, nx): continue
                    cl = mk.c[a1]; ph, pl = mk.h[p0:p1 + 1].max(), mk.l[p0:p1 + 1].min()
                    if (dr > 0 and cl > ph) or (dr < 0 and cl < pl): j = nx; break
                if j is None or j <= busy: continue
            used[dr] = True; e = mk.o[j]; stp = e * (1 - dr * sg)
            x, ix = run_trade(mk, j, i0, i1, dr, e, stp, vw); busy = ix
            out.append((D.date.iat[k], dr * (x - e) / O, 1))
    return out


def daily(trades, ticks, lo, hi):
    c = 2 * L2.side_cost(ticks) if ticks else 0.0
    df = pd.DataFrame(trades, columns=['d', 'g', 'n'])
    df = df[(df.d >= pd.Timestamp(lo)) & (df.d <= pd.Timestamp(hi))]
    net = df.g.values - c * df.n.values
    return pd.Series(net, index=pd.DatetimeIndex(df.d)).groupby(level=0).sum(), net


def main():
    say(f"reversal.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (REVERSAL_PLAN.txt)  cost {LIVE} ticks + $0.50 a side; G1 bar t >= {BAR:.2f}")
    M = {}
    for nm in WIN:
        M[nm] = Market(nm); D = M[nm].D
        say(f"{nm}: {len(D):,} full cash sessions {D.date.min():%Y-%m-%d}..{D.date.max():%Y-%m-%d}; sigma at 10:00 median "
            f"{np.nanmedian(M[nm].sigma[:, 29]) * 100:.3f}% of the open")
    k = 500; Dq = M['nq'].D; s = M['nq'].sigma[k, 29]
    say(f"audit sigma: NQ {Dq.date.iat[k]:%Y-%m-%d} 10:00 = {s * 100:.4f}% vs hand mean of the 14 prior |10:00 close/open - 1| "
        f"{np.mean(np.abs(M['nq'].C1[k - 14:k, 29] / Dq.O.values[k - 14:k] - 1)) * 100:.4f}%")
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close * L2.N_USD
    U = L2.N_USD; survivors = []
    say(); say("=" * 124)
    for r, desc in TRIALS.items():
        T = {}; sp = {}
        for nm in WIN:
            if r in ('R1', 'R2', 'R3'): T[nm], sp[nm] = next_day(M[nm], signals(M[nm])[r])
            else: T[nm] = stretch(M[nm], r == 'R5')
        s = {nm: daily(T[nm], LIVE, *WIN[nm]) for nm in WIN}; g0 = {nm: daily(T[nm], 0, *WIN[nm]) for nm in ('nq', 'mnqx')}
        nq, mq = s['nq'][0], s['mnqx'][0]; both = pd.concat([nq, mq]); yrs = both.groupby(both.index.year).sum()
        d5 = both.drop(both.sort_values(ascending=False).index[:5]) if len(both) > 5 else both
        es_m = s['es'][1].mean() if len(s['es'][1]) else float('nan'); mes_m = s['mes'][1].mean() if len(s['mes'][1]) else float('nan')
        allnet = np.r_[s['nq'][1], s['mnqx'][1]]
        g = {'G1': tt(nq) >= BAR, 'G2': mq.mean() > 0 and tt(mq) >= 2.0, 'G3': int((yrs < 0).sum()) <= 3, 'G4': es_m > 0 and mes_m > 0, 'G5': tt(d5) >= 2.0}
        corr = both.reindex(x3.index).fillna(0).corr(x3)
        say(f"{r} {desc}")
        say(f"   NQ 2010-19 ${nq.sum() * U / 8.85:7,.0f}/yr t {tt(nq):+5.2f} | MNQ 2019-26 ${mq.sum() * U / 7.45:7,.0f}/yr t {tt(mq):+5.2f} | "
            f"ES ${es_m * U:+6.2f} MES ${mes_m * U:+6.2f}/trade | {len(allnet) / 16.3:4.0f} trades/yr, win {np.mean(allnet > 0):.0%} | "
            f"losing years {int((yrs < 0).sum())}/{len(yrs)} | drop5 t {tt(d5):+5.2f} | 0-cost t {tt(g0['nq'][0]):+5.2f}/{tt(g0['mnqx'][0]):+5.2f} | corr X3 {corr:+.2f}")
        if r in ('R1', 'R2', 'R3'):
            sl = np.array(sp['nq'] + sp['mnqx'])
            say(f"   split after the signal (NQ+MNQ, gross $/signal): overnight close -> 09:30 ${sl[:, 0].mean() * U:+6.2f} (t {tt(sl[:, 0]):+.2f}) | "
                f"next cash session ${sl[:, 1].mean() * U:+6.2f} (t {tt(sl[:, 1]):+.2f})")
        fails = [k_ for k_, v in g.items() if not v]
        say(f"   VERDICT {r}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(r)
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
