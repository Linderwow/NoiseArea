"""
exec_pilot_verify.py - independent re-computation of exec_pilot.py's Part B numbers with a second, loop-based implementation of
EXEC_PILOT_PLAN.txt's rules (bisect on a plain sorted list, no shared code with exec_pilot.py).

    python exec_pilot_verify.py <exec_pilot_sides.csv> <trades .dbn.zst> <first date> <last date>

Run it on a file other than the one the pilot used where one exists (2026-04-15: mnq_trades_2026-04-15_2026-04-16.dbn.zst is a
separate download of the same session), and on the pilot's own main file for a stretch of sessions (reads only until the last window).
"""
import sys, bisect, numpy as np, pandas as pd, databento as db

TICK, NS, LAT = 0.25, 1_000_000_000, 250_000_000
sides, src, d0, d1 = pd.read_csv(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
S = sides[(sides.part == 'B') & (sides.date >= d0) & (sides.date <= d1)].reset_index(drop=True)
Ts = S['T'].astype('int64').to_numpy(); last_needed = Ts.max() + 160 * NS
parts = []
for df in db.DBNStore.from_file(src).to_df(pretty_ts=False, map_symbols=False, count=2_000_000):
    tev = df.ts_event.astype('int64').to_numpy()
    near = np.zeros(len(df), bool)
    for T in Ts: near |= (tev >= T - 130 * NS) & (tev < T + 160 * NS)       # only the windows: memory stays small
    if near.any(): parts.append(pd.DataFrame({'ts': tev[near], 'px': df.price.to_numpy(float)[near], 'side': df.side.astype(str).to_numpy()[near]}))
    if tev.min() > last_needed: break
df = pd.concat(parts, ignore_index=True); df = df[df.px > 0]
trades = sorted(zip(df.ts, range(len(df)), df.px, df.side))      # (exchange time, file order, price, aggressor)
ts = [t[0] for t in trades]
print(f'{src}: {len(trades):,} trades kept around {len(S)} sides ({S.date.min()} .. {S.date.max()})')


def touch(t):
    i = bisect.bisect_right(ts, t) - 1
    while trades[i][3] not in ('B', 'A'): i -= 1
    p = trades[i][2]
    return (p - TICK, p) if trades[i][3] == 'B' else (p, p + TICK)


checks = []
for _, r in S.iterrows():
    T, s = int(r['T']), int(r.s)
    i0 = bisect.bisect_left(ts, T); C = trades[i0 - 1][2]; R = C if r.kind == 'flat' else trades[i0][2]
    t1 = trades[i0][0] + LAT
    b, a = touch(t1); mt = a if s > 0 else b
    j = bisect.bisect_left(ts, t1)
    while trades[j][3] != ('B' if s > 0 else 'A'): j += 1
    sec = trades[i0][0] // NS * NS; k = bisect.bisect_left(ts, sec); ext = []
    while k < len(trades) and trades[k][0] < sec + NS: ext.append(trades[k][2]); k += 1
    got = {'R': R, 'C': C, 'mtouch_250': mt, 'mnext': trades[j][2], '1s_m1s': max(ext) if s > 0 else min(ext)}
    for kk in (0, 1):
        for H in (10, 30, 60):
            L = C - s * kk * TICK; tend = T + H * NS
            if (s > 0 and L >= a) or (s < 0 and L <= b):
                fc = fo = (a if s > 0 else b)
            else:
                seg = [trades[x][2] for x in range(bisect.bisect_right(ts, t1), bisect.bisect_left(ts, tend))]
                thru = any(p <= L - TICK for p in seg) if s > 0 else any(p >= L + TICK for p in seg)
                tch = any(p <= L for p in seg) if s > 0 else any(p >= L for p in seg)
                cb, ca = touch(tend + LAT); conv = ca if s > 0 else cb
                fc = L if thru else conv; fo = L if tch else conv
            got[f'c_{kk}_{H}'] = fc; got[f'o_{kk}_{H}'] = fo
    for key, v in got.items(): checks.append((r.date, r.kind, r.T_et, key, v, r[key], abs(v - r[key]) < 1e-9))
c = pd.DataFrame(checks, columns=['date', 'kind', 'T_et', 'field', 'verify', 'pilot', 'ok'])
print(f'verified {c.ok.sum()} of {len(c)} values ({len(S)} sides x {len(c) // max(len(S), 1)} fields)')
if (~c.ok).any(): print(c[~c.ok].to_string(index=False))
v = c[c.field == 'mtouch_250'].verify.to_numpy()
print(f'market cost (250 ms) on these sides: verify {((v - S.R.to_numpy()) * S.s.to_numpy() / TICK).mean():+.3f}, '
      f'pilot {((S.mtouch_250 - S.R) * S.s / TICK).mean():+.3f} ticks/side')
