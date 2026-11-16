import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2, ml_lab as ML, ml_run as MR, ind_lab as IL, growth_e as GE
CROSS = ['x_r15', 'x_r60', 'x_ropen', 'rel15', 'rel_open']
IL.FEATS = [f for f in IL.FEATS if f not in CROSS]
U = L2.N_USD; c1 = MR.SIDE(2.35)
nq, mq = ML.Sessions('nq'), ML.Sessions('mnqx')
Ta = IL.features(nq, None, 5, 5); Ta = Ta[Ta.date <= GE.JOIN].assign(src=0)
Tb = IL.features(mq, None, 5, 5); Tb = Tb[Tb.date > GE.JOIN].assign(src=1)
T = pd.concat([Ta, Tb], ignore_index=True); T = T[T.dec >= 15].reset_index(drop=True)
kk = T.k.values.copy(); T['k'] = np.where(T.src == 1, T.k + 10 ** 6, T.k)
p = GE.walk(T)
def run(TT, pp, lab):
    s, sides = ML.trade(TT, pp, 4 * c1, c1); m = s.index.year >= 2013; s = s[m]; sides = sides[m]
    d, h = s[s.index <= GE.JOIN], s[s.index > GE.JOIN]
    print(f"{lab:46s} NQ 2013-19 t {MR.tt(d):+5.2f} | MNQ 2019-26 ${h.mean() * U * 252:8,.0f}/yr t {MR.tt(h):+5.2f} | round trips/traded day {sides.mean() / 2:5.1f}")
    return s, sides
s, sides = run(T, p, 'as tested')
g, _ = ML.trade(T, p, 4 * c1, 0.0); g = g[g.index.year >= 2013]
print(f"   gross per round trip: {(g.mean() * U) / (sides.mean() / 2) / 2:.2f} pts (cost per round trip {2 * c1 * U / 2:.2f} pts)")
D = T.copy(); S = {0: nq, 1: mq}
for src in (0, 1):
    m = (D.src == src).values; k_ = kk[m]; p_ = D.dec.values[m]
    D.loc[m, 'entry'] = S[src].O[k_, np.minimum(p_ + 1, 389)]
    D.loc[m, 'exitp'] = S[src].C[k_, np.minimum(p_ - 1 + 5 + 1, 389)]
run(D, p, 'entry and exit 1 minute LATER (no bar bounce)')
rng = np.random.default_rng(7); q = p.copy()
for k, idx in T.groupby('k').indices.items(): q[idx] = rng.permutation(p[idx])
run(T, q, 'forecasts shuffled within each day (control)')
