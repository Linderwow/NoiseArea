import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import src2_lib as L
import src2_test as T
import src2_strats_d as SD

T.P = L.Panel(only=None)


def run(thr, minute):
    def f(k, mk, i0, i1, D):
        pc = SD.prior_close(k, D); b = T.at(mk, i0, i1, minute)
        if pc is None or b is None: return
        r = mk.o[b] / pc - 1
        if abs(r) > thr: return [(int(np.sign(r)), mk.c[b], mk.c[i1], 2, minute + 1)]
    g, n = T.intraday(f); net = g - n * L.side_cost(1); sc = L.scorecard(net)
    lo = net[g > 0].index; sh = net[(g != 0)]
    return sc, int((n > 0).sum())


out = ["D03 trend-day neighbourhood (descriptive only - not a selection; t full / discovery 2010-19 / MNQ 2019-26, $ per micro a year, 1 tick)"]
for minute in (810, 840, 870, 900):
    for thr in (0.01, 0.015, 0.02, 0.025, 0.03):
        sc, nd = run(thr, minute)
        out.append(f"  {minute // 60:02d}:{minute % 60:02d}  >{thr * 100:3.1f}%  days {nd:4d}  ${sc['usd_yr']:+7,.0f}/yr  t {sc['t']:+5.2f}  disc {sc['t_disc']:+5.2f}  MNQ {sc['t_mnq']:+5.2f}  hold {sc['t_hold']:+5.2f}  maxDD ${sc['maxdd']:,.0f}")
txt = "\n".join(out); print(txt); open(os.path.join(HERE, 'ghsweep_d03_plateau_output.txt'), 'w').write(txt + "\n")
