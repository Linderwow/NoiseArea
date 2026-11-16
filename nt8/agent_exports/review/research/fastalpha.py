import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import regime_test as rt
from trendmix import stats


def fam_F(mk, overlay=True, vm=1.0, look=14, step=30, first=600, last=930):
    sig = mk.sigma(look); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        pos = 0; tgt = 0; entry = 0.0; p = 0.0; n_ = 0
        for i in range(flat_i):
            if M[i] in checks and not np.isnan(sg[i]):
                c = C[i]; ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
                if pos == 0: tgt = 1 if c > ub else (-1 if c < lb else 0)
                elif pos == 1: tgt = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else: tgt = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                if not overlay or tgt == pos: go = True
                else: go = None
            if tgt != pos:
                if overlay:
                    want = tgt if tgt != 0 else -pos
                    if pos != 0 and tgt == -pos: want = tgt
                    go = (C[i] < O[i]) if want > 0 else (C[i] > O[i])
                    if tgt == 0: go = (C[i] > O[i]) if pos > 0 else (C[i] < O[i])
                else: go = True
                if go:
                    px = O[i + 1]
                    if pos != 0: p += (px - entry) * pos
                    if tgt != 0: entry = px; n_ += 1
                    pos = tgt
        if pos != 0: p += (C[flat_i] - entry) * pos
        pnl[k] = p; nt[k] = n_
    return pnl, nt


if __name__ == '__main__':
    base, _ = N.series('MNQ', ticks=1); f0, _ = N.series('MNQ', fn=fam_F, ticks=1, overlay=False)
    out = ["FASTALPHA (SOURCES2_PLAN.txt) - Concretum one-bar pullback execution overlay on NoiseArea, $ per micro, NoiseArea engine",
           f"parity overlay-off vs nav_lab: {len(f0)} sessions, max |diff| {(f0 - base).abs().max():.2e}"]
    for src in ('MNQ', 'MNQDB'):
        for ticks in (1, 2.35):
            a, na_n = N.series(src, fn=fam_F, ticks=ticks, overlay=False); b, nb_n = N.series(src, fn=fam_F, ticks=ticks, overlay=True)
            for lab, x, n in (('NoiseArea', a, na_n), ('+ pullback', b, nb_n)):
                s = stats(x); pp = '' if x is a else f"P {rt.boot_sharpe_diff(x.index, x.values, a.values):.4f}"
                out.append(f"  {src:5s} {ticks:4} tk  {lab:11s} ${s['usd']:+7,.0f}/yr Sharpe {s['sh']:.2f}  NQ t {s['t_nq']:+.2f} MNQ t {s['t_mnq']:+.2f}"
                           f"  maxDD ${s['dd']:,.0f}  2023-26 ${s['usd_h']:+7,.0f} Sh {s['sh_h']:.2f}  trades {int(n.sum())}  {pp}")
    txt = "\n".join(out); print(txt); open(os.path.join(HERE, 'fastalpha_output.txt'), 'w').write(txt + "\n")
