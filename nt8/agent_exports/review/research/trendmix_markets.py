import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import regime_test as rt
from trendmix import fam_T, stats

out = ["TRENDMIX second sources (descriptive, same fixed rules; gross of costs except MNQDB joined at 2.35 ticks)"]
na, _ = N.series('MNQDB', fn=fam_T, ticks=2.35, mode='na'); t3, _ = N.series('MNQDB', fn=fam_T, ticks=2.35, mode='fill')
t5, _ = N.series('MNQDB', fn=fam_T, ticks=2.35, mode='holdfill')
for lab, x in (('NA', na), ('T3 FILL', t3), ('T5 HOLD+FILL', t5)):
    s = stats(x); p = '' if lab == 'NA' else f"P {rt.boot_sharpe_diff(x.index, x.values, na.values):.4f}"
    out.append(f"  NQ+MNQ Databento bars  {lab:13s} ${s['usd']:+7,.0f}/yr Sharpe {s['sh']:.2f}  2023-26 ${s['usd_h']:+7,.0f}  maxDD ${s['dd']:,.0f}  {p}")
for nm in ('ES', 'MES', 'MYM', 'M2K'):
    try:
        a = N.single(nm, fn=fam_T, mode='na'); b = N.single(nm, fn=fam_T, mode='fill'); c = N.single(nm, fn=fam_T, mode='d03')
    except Exception as e:
        out.append(f"  {nm}: unavailable ({e})"); continue
    sh = lambda g: g.mean() / g.std() * np.sqrt(252)
    hb = lambda g: sh(g[g.index >= '2023-01-01'])
    out.append(f"  {nm:4s} {a.index[0].date()}..{a.index[-1].date()}  NA Sharpe {sh(a.g):.2f} (23-26 {hb(a.g):.2f})  FILL {sh(b.g):.2f} (23-26 {hb(b.g):.2f})"
               f"  trend day alone {sh(c.g):.2f} (23-26 {hb(c.g):.2f})  P {rt.boot_sharpe_diff(a.index, b.g.values, a.g.values):.3f}")
txt = "\n".join(out); print(txt); open(os.path.join(HERE, 'trendmix_markets_output.txt'), 'w').write(txt + "\n")
