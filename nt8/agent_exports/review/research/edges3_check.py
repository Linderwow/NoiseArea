r"""Independent checks of edges3.py (EDGES3_PLAN.txt).
    python edges3_check.py  -> edges3_check_output.txt
 1. A2 re-implemented with pandas straight from the caches and the EDGAR json (own next-session logic) - per event.
 2. C1's session mask rebuilt with pandas merge_asof (previous CBOE close strictly before the session) - identical sessions?
 3. B1 (ZN): 300 random (session, checkpoint) cells of the move grid and the NQ grid recomputed with pandas; the walk-forward beta
    and threshold for 5 sessions recomputed from the previous 250 sessions only.
"""
import os, sys, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import edges3 as E
import src2_lib as L
from bulenox_routes import Pan

out = []
def pr(*a): print(*a); out.append(' '.join(str(x) for x in a))


def frame(name):
    z = np.load(os.path.join(HERE, 'regime_work', f'{name}_1m.npz'))
    return pd.DataFrame({'o': z['o'], 'c': z['c'], 'ao': z['o'] + z['shift'], 'ac': z['c'] + z['shift']}, index=pd.DatetimeIndex(z['t']))


def main():
    import src2_test as T
    P = Pan('nq', 'mnqx'); na = T.noisearea()
    # 1. A2
    e = json.load(open(os.path.join(HERE, 'event_calendars', 'earnings_8k_202_20261020.json')))
    nq, mnq = frame('nq'), frame('mnqx'); J = pd.Timestamp('2019-05-05')
    sess = {}
    for name, df in (('nq', nq), ('mnqx', mnq)):
        rth = df.between_time('09:30', '15:59'); g = rth.groupby(rth.index.normalize())
        first, last, cnt = g.head(1), g.tail(1), g.size()
        for d in cnt.index:
            if (name == 'nq' and d > J) or (name == 'mnqx' and d <= J) or cnt[d] < 200: continue
            f = first[first.index.normalize() == d].iloc[0]
            if first[first.index.normalize() == d].index[0].time() != pd.Timestamp('09:30').time(): continue
            l = last[last.index.normalize() == d].iloc[0]; sess[d] = (f.ao, f.o, l.ac)
    days = pd.DatetimeIndex(sorted(sess)); cal = pd.DatetimeIndex(sorted(set(na.index) | set(days)))
    react = set()
    for c in E.PRIMARY:
        for f in e[c].split(','):
            i = cal.searchsorted(pd.Timestamp(f), side='right')
            if i < len(cal) and pd.Timestamp(L.DISC[0]) <= cal[i]: react.add(cal[i])
    own = {}
    for d in sorted(react):
        if d not in sess: continue
        i = days.get_loc(d)
        if i == 0 or (days[i - 1] <= J) != (d <= J): continue
        ao, o, ac = sess[d]; prev_ac = sess[days[i - 1]][2]; s = np.sign(ao - prev_ac)
        if s != 0: own[d] = s * (ac - ao) / o
    own = pd.Series(own); mine = pd.read_csv(os.path.join(HERE, 'edges3_a2_daily.csv'), index_col=0, parse_dates=True)
    mine_ev = (mine.net[mine.sides > 0] + 2 * L.side_cost(1))
    both = own.index.intersection(mine_ev.index)
    pr(f"1. A2: edges3 {len(mine_ev)} events, own pandas {len(own)}, common {len(both)}, max |diff| {(own[both] - mine_ev[both]).abs().max() * 1e4:.4f} bp; "
       f"only edges3 {[str(x.date()) for x in mine_ev.index.difference(own.index)][:5]}; only own {[str(x.date()) for x in own.index.difference(mine_ev.index)][:5]}")
    # 2. C1 mask
    v = pd.read_csv(os.path.join(HERE, 'vix_history_cboe.csv')); v['d'] = pd.to_datetime(v.DATE, format='%m/%d/%Y')
    v3 = pd.read_csv(os.path.join(HERE, 'event_calendars', 'vix3m_history_cboe_20261020.csv')); v3['d'] = pd.to_datetime(v3.DATE, format='%m/%d/%Y')
    r = v[['d', 'CLOSE']].merge(v3[['d', 'CLOSE']], on='d', suffixes=('', '3')); r['ratio'] = r.CLOSE / r.CLOSE3
    s = pd.DataFrame({'d': na.index}); s['q'] = s.d - pd.Timedelta(days=1)
    m = pd.merge_asof(s.sort_values('q'), r[['d', 'ratio']].rename(columns={'d': 'q'}).sort_values('q'), on='q', direction='backward')
    own_mask = set(m.d[m.ratio >= 1.0]); c1 = pd.read_csv(os.path.join(HERE, 'edges3_c1_daily.csv'), index_col=0, parse_dates=True)
    na_mask = set(c1.index[c1.net != 0]) | set(c1.index[(c1.net == 0) & (c1.index.isin(list(own_mask)))])
    mine_mask = set(c1.index[(c1.net != 0)])
    pr(f"2. C1: own merge_asof mask {len(own_mask)} sessions; edges3 sessions with a position {len(mine_mask)}, all inside the own mask: "
       f"{mine_mask <= own_mask}")
    # 3. B1 spot checks
    km = [k for k in range(len(P.dates)) if P.daily.seg.iat[k] == 1]; dates = [P.dates[k] for k in km]
    Y = E.nq_grid(P, km); R = E.mkt_grid('zn', dates); zn = frame('zn')
    rng = np.random.default_rng(7); cells = [(a, c) for a, c in zip(rng.integers(0, len(km), 2000), rng.integers(0, 12, 2000)) if np.isfinite(R[a, c]) and np.isfinite(Y[a, c])][:300]
    dr, dy = [], []
    for a, c in cells:
        d = dates[a]; h, mi = E.CHECKS[c]; t = d + pd.Timedelta(hours=h, minutes=mi)
        b1 = zn[zn.index < t].iloc[-1]; b0 = zn[zn.index < t - pd.Timedelta(minutes=30)].iloc[-1]
        dr.append(abs((b1.ac - b0.ac) / b1.c - R[a, c]))
        x = mnq.loc[t:t + pd.Timedelta(minutes=2)].iloc[0]
        if (h, mi) == (15, 30): y_ = (mnq.loc[d + pd.Timedelta(hours=15, minutes=59)].ac - x.ao) / mnq.loc[d + pd.Timedelta(hours=9, minutes=30)].o
        else:
            t2 = t + pd.Timedelta(minutes=30); y_ = (mnq.loc[t2:t2 + pd.Timedelta(minutes=2)].iloc[0].ao - x.ao) / mnq.loc[d + pd.Timedelta(hours=9, minutes=30)].o
        dy.append(abs(y_ - Y[a, c]))
    pr(f"3. B1 grids on {len(cells)} random cells: max |move diff| {max(dr) * 1e4:.5f} bp, max |NQ return diff| {max(dy) * 1e4:.5f} bp")
    pos, _ = E.leadlag(R, Y)
    for a in (300, 700, 1100, 1500, 1800):
        rr, yy = R[a - 250:a].ravel(), Y[a - 250:a].ravel(); ok = np.isfinite(rr) & np.isfinite(yy)
        beta = np.polyfit(rr[ok], yy[ok], 1)[0]; sd = rr[ok].std(ddof=1)
        exp = [np.sign(beta) * np.sign(R[a, c]) if np.isfinite(R[a, c]) and np.isfinite(Y[a, c]) and abs(R[a, c]) > sd else 0.0 for c in range(12)]
        pr(f"   session {dates[a].date()}: own beta {beta:+.4f}, sd {sd * 1e4:.2f} bp -> positions {'match' if np.allclose(exp, pos[a]) else 'DIFFER'}")
    with open(os.path.join(HERE, 'edges3_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
