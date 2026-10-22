r"""LIMIT_PLAN.txt follow-up: delayed market entry at 10:00 (T + 5 / 15 / 30 s), plus an independent re-computation of the immediate
market cost with pandas merge_asof (must match limit_test.py's limit_entries.csv).
    python limit_followup.py  -> limit_followup_output.txt
"""
import os, sys, glob
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import limit_test as LT

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
DELAYS = (5, 15, 30)


def main():
    w, tp = LT.load(); E = pd.read_csv(os.path.join(HERE, 'limit_entries.csv'), parse_dates=['date'])
    tp['t'] = pd.to_datetime(tp.ts); tp = tp.sort_values(['win', 't'], kind='mergesort')   # stable: keep the exchange's order
    tp['sd'] = tp.side.map({'B': 1, 'A': -1}).fillna(0).astype(int)
    rows = []
    for i, r in w.iterrows():
        g = tp[tp.win == i]
        T = pd.Timestamp(r.fill_open).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None)
        after = g[g.t >= T]; before = g[g.t < T]
        if not len(after) or not len(before): continue
        R = after.price.iloc[0]; t0 = after.t.iloc[0]; s = int(r.dir)
        known = g[g.sd != 0][['t', 'price', 'sd']]

        def fill_at(t):
            k = known[known.t <= t]
            if not len(k): return np.nan
            p, x = k.price.iloc[-1], k.sd.iloc[-1]
            bid, ask = (p - LT.TICK, p) if x > 0 else (p, p + LT.TICK)
            return ask if s > 0 else bid
        row = dict(date=pd.Timestamp(r.fill_open).normalize(), points=r.points,
                   imm=(fill_at(t0 + pd.Timedelta(milliseconds=75)) - R) * s / LT.TICK)
        for d in DELAYS:
            row[f'd{d}'] = (fill_at(T + pd.Timedelta(seconds=d, milliseconds=75)) - R) * s / LT.TICK
        rows.append(row)
    D = pd.DataFrame(rows)
    m = D.merge(E[['date', 'mkt_75']], on='date', how='inner')
    pr(f"Independent immediate market cost (pandas, own touch logic): mean {D.imm.mean():+.3f} ticks over {len(D)} entries; "
       f"limit_test.py {E.mkt_75.mean():+.3f}; per-entry max |difference| {(m.imm - m.mkt_75).abs().max():.6f} on {len(m)} matched")
    top = D.points >= D.points.quantile(0.8)
    for d in DELAYS:
        sv = D.imm - D[f'd{d}']; mu, lo, hi, n = LT.boot(sv)
        h1 = sv[D.date <= '2022-12-31'].mean(); h2 = sv[D.date >= '2023-01-01'].mean(); rn = sv[top].mean()
        ok = mu >= 0.5 and lo > 0 and h1 > 0 and h2 > 0 and rn >= -2
        pr(f"  delay {d:2d} s: delayed cost {D[f'd{d}'].mean():+.2f} ticks/side; saving vs immediate {mu:+.2f} [{lo:+.2f}, {hi:+.2f}]; "
           f"2019-2022 {h1:+.2f}, 2023-2026 {h2:+.2f}, runners {rn:+.2f} -> {'PROMISING' if ok else 'NOT PROMISING'}")
    with open(os.path.join(HERE, 'limit_followup_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
