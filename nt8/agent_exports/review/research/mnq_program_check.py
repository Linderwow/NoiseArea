r"""Audits for MNQ_PROGRAM_PLAN.txt, written separately from mnq_program.py.
    python mnq_program_check.py
(a) W1's yearly choice re-derived by a plain loop for 2023 and 2025; (b) the meta-label simulator with no filter reproduces
NoiseArea (fam_A) day for day; (c) the event sleeves E1-E5 recomputed from the raw bar table with pandas.
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
import mnq_program as mp, structure_test as st, regime_test as rt
from research_engine import fam_A
from sim import load

D = mp.Data('MNQ'); ok = True
# (a)
_, _, log = mp.W1(D)
for y in (2023, 2025):
    fit = D.dates < pd.Timestamp(f'{y}-01-01'); best = None
    for j in st.FULL_COLS:
        x = D.R[fit, j]; n_tr = D.NT[fit, j].sum()
        if n_tr < 100: continue
        sd = x.std(ddof=1)
        if sd == 0: continue
        t = x.mean() / sd * np.sqrt(len(x))
        if best is None or t > best[0]: best = (t, j)
    got = [l for l in log if l.startswith(str(y))][0]; same = st.label_all(best[1]) in got; ok &= same
    print(f"(a) {y}: loop picks {st.label_all(best[1])} (t {best[0]:+.2f}); mnq_program: {got} -> {'same' if same else 'DIFFERENT'}")
# (b)
p0, n0 = fam_A(D.mk, slip=0.0, comm=0.0); p1, n1, _ = mp.fam_A_meta(D.mk)
d = np.nanmax(np.abs(np.nan_to_num(p0) - np.nan_to_num(p1))); same = d < 1e-9 and (n0 == n1).all(); ok &= same
print(f"(b) meta simulator without a filter vs fam_A: max |difference| {d:.1e}, entries equal {(n0 == n1).all()} -> {'same' if same else 'DIFFERENT'}")
# (c)
df = load(rt.FILES['MNQ']); df['date'] = df.time.dt.normalize()
rth = df[df.in_rth]; g = rth.groupby('date')
day = pd.DataFrame({'open': g.open.first(), 'close': g.close.last(), 'first_m': g.etmin.first(), 'n': g.size()})
day = day[(day.first_m == 575) & (day.n >= 30)]
o580 = rth[rth.etmin == 580].set_index('date').open; c840 = rth[rth.etmin == 840].set_index('date').close; c955 = rth[rth.etmin == 955].set_index('date').close
prev_close = day.close.shift(1)
ev = mp.events(D); rows = {dd: i for i, dd in enumerate(D.dates)}; worst = 0.0
hol = set()
for y in range(2019, 2028): hol |= set(mp.early_closes(y)) | set(mp.full_closures(y))
for dd in D.dates:
    i = rows[dd]; px = D.px[i]; c2 = 2 * D.c
    exp = {'E5': (day.open[dd] - prev_close[dd]) / px - c2}
    if dd in mp.FOMC_SET:
        exp['E1'] = exp['E5']
        if dd in o580.index and dd in c840.index: exp['E2'] = (c840[dd] - o580[dd]) / px - c2
        if dd in c840.index: exp['E3'] = (c840[dd] - prev_close[dd]) / px - c2
    nxt = dd + pd.Timedelta(days=1)
    while nxt.weekday() >= 5: nxt += pd.Timedelta(days=1)
    if nxt.date() in hol and dd in o580.index: exp['E4'] = ((c955[dd] if dd in c955.index else day.close[dd]) - o580[dd]) / px - c2
    for e in ('E1', 'E2', 'E3', 'E4', 'E5'):
        got = ev[e][0][i] if ev[e][1][i] else None; want = exp.get(e)
        if (got is None) != (want is None): worst = np.inf; print(f"   {dd.date()} {e}: present {got is not None} vs {want is not None}")
        elif got is not None: worst = max(worst, abs(got - want))
same = worst < 1e-12; ok &= same
print(f"(c) event sleeves vs the raw bar table: max |difference| {worst:.1e} -> {'same' if same else 'DIFFERENT'}")
print(f"AUDITS {'PASSED' if ok else 'FAILED'}")
