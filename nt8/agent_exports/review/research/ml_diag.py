import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2, ml_lab as ML, ml_run as MR

nq, mq, es, ms = (ML.Sessions(n) for n in ('nq', 'mnqx', 'es', 'mes'))
T, pred = MR.family(nq, mq, es, ms, 15, 15)
U = L2.N_USD; out = []
for ticks in (1.0, 2.35, 3.5, 5.0):
    c1 = MR.SIDE(ticks); s, sides = ML.trade(T, pred, 2 * 2 * MR.SIDE(2.35), c1); m = s.index.year >= 2013; s = s[m]; sides = sides[m]
    out.append((ticks, s.mean() * U * 252, MR.tt(s), sides.mean()))
s, sides = ML.trade(T, pred, 2 * 2 * MR.SIDE(2.35), MR.SIDE(2.35)); m = s.index.year >= 2013; s = s[m] * U; sides = sides[m]
eq = s.cumsum(); dd = (eq.cummax() - eq).max()
yrs = s.groupby(s.index.year).sum()
print("DIAGNOSTICS AFTER THE VERDICT (L1, not gates): $ per micro at today's price")
for t, yr, t_, sd in out: print(f"   cost {t:4.2f} ticks/side: ${yr:7,.0f}/yr t {MR.tt(s) if False else t_:+.2f}  (avg {sd / 2:.1f} round trips a traded day)")
print(f"   traded days {len(s)} of {((T.date.dt.year >= 2013)).groupby(T.k).any().sum()} | max drawdown ${dd:,.0f} | worst day ${s.min():,.0f} | days below -$1,100 at 1 micro: {(s < -1100).sum()}")
print("   per year: " + "; ".join(f"{y} {v:+,.0f}" for y, v in yrs.items()))
r = s[s.index >= '2023-01-01']; print(f"   2023-26 only: ${r.mean() * 252:,.0f}/yr t {MR.tt(r):+.2f}")
