import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L
import src2_test as T
import src2_strats_d as SD

T.P = L.Panel(only=None)
COST = 2 * (0.5 * 0.5 + 0.62)


def trades(fn, **kw):
    rows = []
    for k, (d, mk) in enumerate(T.P.sess):
        i0, i1 = mk.days[d]
        for t in (fn(k, mk, i0, i1, T.P.daily, **kw) or []):
            rows.append(dict(date=d, dr=t[0], pts=t[0] * (t[2] - t[1])))
    return pd.DataFrame(rows)


def report(name, df, a, b):
    x = df[(df.date >= a) & (df.date <= b)]
    usd = x.pts * 2.0 - COST
    pf = usd[usd > 0].sum() / -usd[usd < 0].sum() if (usd < 0).any() else np.inf
    eq = usd.cumsum(); dd = (eq.cummax() - eq).max()
    yrs = x.groupby(x.date.dt.year).pts.sum() * 2.0
    return (f"  {name:24s} {a[:7]}..{b[:7]}  trades {len(x):4d}  win {100 * (usd > 0).mean():4.1f}%  PF {pf:4.2f}  net ${usd.sum():+9,.0f}"
            f"  maxDD ${dd:7,.0f}  years+ {(yrs > 0).sum()}/{len(yrs)}")


out = ["Strategy B reproduction (1 MNQ, $0.62 commission + 0.5 tick slippage a side, 1-minute exits)"]
for name, kw in (("as published (look-ahead)", dict(look=True)), ("look-ahead removed", dict())):
    df = trades(SD.strategy_b, **kw)
    out += [report(name, df, '2023-01-01', '2026-05-31'), report(name, df, '2010-07-01', '2022-12-31')]
txt = "\n".join(out); print(txt)
open(os.path.join(HERE, 'ghsweep_repro_output.txt'), 'w').write(txt + "\n")
