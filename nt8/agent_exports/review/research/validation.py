"""Validation: finalists from discovery_results.csv run ONCE on V1 MNQ 2023-25, V2 MES 2023-26, V3 NT MNQ 2026."""
import pickle, numpy as np, pandas as pd
from research_engine import Market, FAMS, grid, label, stats, FAM_NAMES

df = pd.read_csv('discovery_results.csv').sort_values('t', ascending=False)
G = grid(); daily = pickle.load(open('discovery_daily.pkl', 'rb'))
elig = df[df.eligible]
final = list(elig.head(15).id)
for f in 'ABCDEF':
    e = elig[elig.fam == f]
    if len(e) and e.iloc[0].id not in final: final.append(int(e.iloc[0].id))
base = [i for i, (f, p) in enumerate(G) if f == 'A' and p == dict(vm=1.0, look=14, step=30, trail='paper', eff=None)][0]
print('published base case id', base, 'discovery rank', int(df[df.id == base]['rank'].iloc[0]), 'of', len(elig))
if base not in final: final.append(base)      # reported for comparison; not a finalist unless ranked

mk_mnq = Market('mnq_db_5m.csv', 2.0, 'MNQ')
mk_mes = Market('mes_db_5m.csv', 5.0, 'MES', cap=0.4)
mk_nt = Market('bars_new.csv', 2.0, 'NT-MNQ')
print('NT 2026 valid days:', int(((mk_nt.dates >= '2026-01-01')).sum()))

rows = []
for i in final:
    fam, prm = G[i]
    d = stats(mk_mnq, *daily[i], '2019-01-01', '2022-12-31')
    v1 = stats(mk_mnq, *daily[i], '2023-01-01', '2025-12-31')
    v2 = stats(mk_mes, *FAMS[fam](mk_mes, **prm), '2023-01-01', '2026-12-31')
    v3 = stats(mk_nt, *FAMS[fam](mk_nt, **prm), '2026-01-01', '2026-10-14')
    rows.append(dict(id=i, rank=int(df[df.id == i]['rank'].iloc[0]), label=label(fam, prm), disc_t=d['t'], disc_net=d['net'],
                     v1_net=v1['net'], v1_t=v1['t'], v1_tr=v1['trades'], v2_net=v2['net'], v2_t=v2['t'], v2_tr=v2['trades'],
                     v3_net=v3['net'], v3_tr=v3['trades'], v1_mdd=v1['mdd'],
                     PASS=(v1['net'] > 0) and (v2['net'] > 0) and (v3['net'] > 0)))
res = pd.DataFrame(rows).sort_values('rank')
res.to_csv('validation_results.csv', index=False)
pd.set_option('display.width', 250)
print(res[['rank', 'label', 'disc_t', 'v1_net', 'v1_t', 'v2_net', 'v2_t', 'v3_net', 'v3_tr', 'PASS']].to_string(index=False, float_format=lambda x: f'{x:,.2f}'))
p = res[res.PASS]
print('\nPASSERS:', len(p), 'of', len(res))
if len(p):
    b = p.iloc[0]; print('BEST (highest discovery rank among passers):', b.label, '| disc rank', b['rank'])
