import sys, numpy as np, pandas as pd, databento as db
NS = 1_000_000_000
OFFS = [('-10ms', -10_000_000), ('0', 0), ('+1ms', 1_000_000), ('+10ms', 10_000_000), ('+100ms', 100_000_000), ('+1s', NS)]
S = pd.read_csv(sys.argv[1]); src, d0, d1 = sys.argv[2], sys.argv[3], sys.argv[4]
S = S[(S.part == 'B') & (~S.roll) & (S.date >= d0) & (S.date <= d1)].reset_index(drop=True)
mode = S.groupby('iid').off.agg(lambda x: x.round(2).mode().iloc[0]); S['nt'] = S.refpx + S.iid.map(mode)
Ts = S['T'].astype('int64').to_numpy(); parts = []
for df in db.DBNStore.from_file(src).to_df(pretty_ts=False, map_symbols=False, count=2_000_000):
    t = df.ts_event.astype('int64').to_numpy(); near = np.zeros(len(df), bool)
    for T in Ts: near |= (t >= T - 10 * NS) & (t < T + 10 * NS)
    if near.any(): parts.append(pd.DataFrame({'ts': t[near], 'px': df.price.to_numpy(float)[near]}))
    if t.min() > Ts.max() + 20 * NS: break
tp = pd.concat(parts, ignore_index=True); tp = tp[tp.px > 0].sort_values('ts', kind='stable'); ts = tp.ts.to_numpy(); px = tp.px.to_numpy()
rows = []
for _, r in S.iterrows():
    T = int(r['T']); hh = r.T_et[11:16]
    slot = '10:00' if hh == '10:00' else ('close' if r.kind == 'flat' else 'other')
    row = dict(slot=slot)
    for lab, dlt in OFFS:
        if r.kind == 'flat': v = px[np.searchsorted(ts, T + dlt, 'left') - 1]
        else: v = px[np.searchsorted(ts, T + dlt, 'left')]
        row[lab] = bool(np.isclose(r.nt, v))
    w = px[(ts >= T - NS) & (ts < T + NS)]; row['any_1s'] = bool(np.isclose(w, r.nt).any())
    rows.append(row)
R = pd.DataFrame(rows)
print(f'{len(R)} sides, {d0}..{d1}: share of sides whose NinjaTrader price equals the boundary trade at T + offset')
print(R.groupby('slot')[[l for l, _ in OFFS] + ['any_1s']].mean().round(2).to_string())
print('counts per slot:', R.slot.value_counts().to_dict())
