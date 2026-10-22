"""Discovery sweep: every variant in VARIANTS_PLAN on MNQ, ranked on 2019-2022 only. Saves daily P&L for later validation."""
import pickle, time, sys
import numpy as np, pandas as pd
from research_engine import Market, FAMS, grid, label, stats, FAM_NAMES

mk = Market('mnq_db_5m.csv', 2.0, 'MNQ')
G = grid(); rows = []; daily = {}
t0 = time.time()
for j, (fam, prm) in enumerate(G):
    pnl, nt = FAMS[fam](mk, **prm)
    daily[j] = (pnl, nt)
    s = stats(mk, pnl, nt, '2019-01-01', '2022-12-31')
    rows.append(dict(id=j, fam=fam, label=label(fam, prm), **s))
    if j % 50 == 0: print(f'{j}/{len(G)} {time.time()-t0:.0f}s', flush=True)
df = pd.DataFrame(rows)
df['eligible'] = df.trades >= 100
df['rank'] = df.where(df.eligible).t.rank(ascending=False)
df = df.sort_values('t', ascending=False)
df.to_csv('discovery_results.csv', index=False)
pickle.dump(daily, open('discovery_daily.pkl', 'wb'))
print(f'\n{len(df)} variants, {df.eligible.sum()} with >=100 trades, {time.time()-t0:.0f}s')
print('\nTop 20 on MNQ 2019-2022 (t-stat of daily P&L):')
print(df[df.eligible].head(20)[['rank', 'label', 'trades', 'net', 't', 'mdd', 'pf']].to_string(index=False, float_format=lambda x: f'{x:,.2f}'))
print('\nBest per family:')
for f in 'ABCDEF':
    e = df[(df.fam == f) & df.eligible]
    if len(e): r = e.iloc[0]; print(f"  {FAM_NAMES[f]:30s} #{int(r['rank']):3d}  {r.label:55s} trades {r.trades:5d} net {r.net:9,.0f} t {r.t:+.2f}")
    print(f"      family: {len(df[df.fam==f])} variants, {int((df[df.fam==f].t>0).sum())} with positive t, median t {df[df.fam==f].t.median():+.2f}")
