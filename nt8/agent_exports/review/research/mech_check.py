r"""Independent check of mech.py's T1 (third-Friday spike short), written separately (MECH_PLAN.txt).
    python mech_check.py  -> mech_check_output.txt
 1. Third-Friday dates rebuilt with pandas' WOM-3FRI calendar and the NYSE Good Friday dates; holiday shifts listed.
 2. Every T1 event recomputed from the raw files: NQ from the .npz cache with pandas, MNQ from the NinjaTrader 5-minute CSV.
 3. Which events carry the result: the 5 best and 5 worst, and the mean without them.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import numpy as np, pandas as pd
import mech as M
import src2_lib as L

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def main():
    tf = M.third_fridays()
    own = pd.date_range('2010-01-01', '2026-12-31', freq='WOM-3FRI')
    gf = {pd.Timestamp(d) for d in ('2010-04-02', '2011-04-22', '2012-04-06', '2013-03-29', '2014-04-18', '2015-04-03', '2016-03-25', '2017-04-14',
                                     '2018-03-30', '2019-04-19', '2020-04-10', '2021-04-02', '2022-04-15', '2023-04-07', '2024-03-29', '2025-04-18', '2026-04-03')}
    shifted = sorted(d for d in own if d in gf)
    mine = sorted(tf); own_adj = sorted((d - pd.Timedelta(days=1)) if d in gf else d for d in own)
    pr(f"1. third Fridays: mech {len(mine)}, pandas WOM-3FRI {len(own_adj)}, identical: {mine == own_adj}; Good-Friday shifts to Thursday: "
       f"{[str(d.date()) for d in shifted]}")
    # 2. recompute
    z = np.load(os.path.join(HERE, 'regime_work', 'nq_1m.npz')); nq = pd.DataFrame({'o': z['o'], 'c': z['c']}, index=pd.DatetimeIndex(z['t']))
    nt = pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'nt_analyzer_bars_to_2026-10-14.csv'), parse_dates=['time']).set_index('time')
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    ref = pd.read_csv(os.path.join(HERE, 'mech_t1_daily.csv'), index_col=0, parse_dates=True).net
    rows = {}
    for d in sorted(tf):
        if d < pd.Timestamp('2010-07-01') or d > pd.Timestamp('2026-10-14'): continue
        if d <= M.JOIN:
            a = nq.loc[d + pd.Timedelta(hours=9, minutes=30): d + pd.Timedelta(hours=9, minutes=30)]
            b = nq.loc[d + pd.Timedelta(hours=12): d + pd.Timedelta(hours=12, minutes=2)]
            if a.empty or b.empty: continue
            r = -(b.o.iloc[0] - a.o.iloc[0]) / a.o.iloc[0]
        else:
            a = nt.loc[d + pd.Timedelta(hours=9, minutes=35): d + pd.Timedelta(hours=9, minutes=35)]
            b = nt.loc[d + pd.Timedelta(hours=12): d + pd.Timedelta(hours=12)]
            if a.empty or b.empty: continue
            px = raw.get(d, a.open.iloc[0]); r = -(b.close.iloc[0] - a.open.iloc[0]) / px
        rows[d] = r - 2 * M.C1
    own_s = pd.Series(rows); both = own_s.index.intersection(ref[ref != 0].index)
    pr(f"2. T1 events recomputed: {len(own_s)} (mech {int((ref != 0).sum())}), common {len(both)}, max |difference| "
       f"{(own_s[both] - ref[both]).abs().max() * 1e4:.4f} bp; mean {own_s.mean() * 1e4:+.2f} bp, event t {M.tstat(own_s.values):+.2f}")
    s = own_s.sort_values()
    pr(f"3. best 5: " + ', '.join(f"{d.date()} {v * 1e4:+.0f} bp" for d, v in s.tail(5).items()) +
       f" | worst 5: " + ', '.join(f"{d.date()} {v * 1e4:+.0f} bp" for d, v in s.head(5).items()))
    pr(f"   mean without the best 5 {s.iloc[:-5].mean() * 1e4:+.2f} bp (t {M.tstat(s.iloc[:-5].values):+.2f}); median {s.median() * 1e4:+.2f} bp")
    with open(os.path.join(HERE, 'mech_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
