r"""Independent re-computation of RULEMINE Part A with plain loops (the plan's third audit): A1 VWAP trend straight from the 1-minute
cache (own day grouping, running VWAP, explicit position state), A2-A5 as trade lists over the daily RTH table, each compared day by
day with rulemine.part_a.
    python rulemine_check.py
"""
import os, sys, math
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import rulemine as rm                                      # sets BelowNormal priority
import regime_test as rt


def a1_loop():
    z = np.load(os.path.join(HERE, 'regime_work', 'mnq_1m.npz'))
    t = pd.DatetimeIndex(z['t'])
    df = pd.DataFrame({'d': t.normalize(), 'm': t.hour * 60 + t.minute, 'o': z['o'], 'h': z['h'], 'l': z['l'], 'c': z['c'], 'v': z['v'],
                       'iid': z['iid']})
    df = df[(df.m >= 570) & (df.m < 960) & (df.d >= '2019-06-01') & (df.d <= '2026-04-09')]
    out = {}
    for d, g in df.groupby('d', sort=True):
        if g.m.iloc[0] != 570 or len(g) < 201 or g.iid.iloc[0] != g.iid.iloc[-1]: continue
        O, H, L, C, V = (g[k].tolist() for k in 'ohlcv')
        n = len(C); pv = vv = 0.0; sig = []
        for j in range(n):                                 # side of each minute's close vs the session VWAP up to that minute
            pv += (H[j] + L[j] + C[j]) / 3 * V[j]; vv += V[j]; vw = pv / max(vv, 1.0)
            sig.append(1 if C[j] > vw else (-1 if C[j] < vw else 0))
        pos = prev = 0; pnl = 0.0; sides = 0
        for j in range(n):                                 # position over minute j, set from minute j-1's close (first from 09:31's)
            if j >= 2 and sig[j - 1] != 0: pos = sig[j - 1]
            sides += abs(pos - prev); prev = pos
            pnl += pos * ((O[j + 1] if j + 1 < n else C[j]) - O[j])
        sides += abs(prev)
        out[d] = pnl / O[0] - sides * rm.C2 / 2
    return pd.Series(out)


def daily_loops():
    _, _, _, _, _, _, _, full, _ = rt.joined('MNQ')
    f = full[full.index >= pd.Timestamp('2010-07-21')]
    idx = list(f.index); O, C, seg = f.O.tolist(), f.C.tolist(), f.seg.tolist(); n = len(C)
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    px = [raw.get(d, np.nan) for d in idx]; px = [O[i] if (p is None or math.isnan(p)) else p for i, p in enumerate(px)]
    half = rm.C2 / 2
    # RSI(2), Wilder smoothing started at zero, and simple averages, by hand
    au = ad = 0.0; rsi = []
    for i in range(n):
        ch = C[i] - C[i - 1] if i else 0.0
        au = au / 2 + max(ch, 0) / 2; ad = ad / 2 + max(-ch, 0) / 2
        rsi.append(100 - 100 / (1 + au / ad) if ad > 0 else float('nan'))
    sma = lambda w: [sum(C[i - w + 1:i + 1]) / w if i >= w - 1 else float('nan') for i in range(n)]
    s200, s5 = sma(200), sma(5)

    def trades(entry, exitf):
        r = [0.0] * n; i = 1
        while i < n:
            if seg[i] != seg[i - 1] or not entry(i): i += 1; continue
            r[i] -= half; k = i + 1                        # in at the close of i; walk to the exit close
            while k < n:
                if seg[k] != seg[k - 1]: break             # dropped at the NQ / MNQ join (no exit cost, as in rulemine)
                r[k] += (C[k] - C[k - 1]) / px[k]
                if exitf(k): r[k] -= half; break
                k += 1
            i = k + 1                                      # no re-entry on the exit day
        return pd.Series(r, index=idx)

    out = {'A2': trades(lambda t: rsi[t] < 10 and C[t] > s200[t], lambda t: C[t] > s5[t]),
           'A3': trades(lambda t: t >= 6 and C[t] == min(C[t - 6:t + 1]) and C[t] > s200[t], lambda t: t >= 6 and C[t] == max(C[t - 6:t + 1]))}
    a4 = [0.0] * n; a5 = [0.0] * n
    for t in range(1, n - 1):
        if not (seg[t - 1] == seg[t] == seg[t + 1]): continue
        if idx[t].weekday() == 0 and idx[t - 1].weekday() == 4 and idx[t + 1].weekday() == 1 and C[t] < C[t - 1]:
            a4[t + 1] += (C[t + 1] - C[t]) / px[t + 1] - rm.C2
        if t >= 15:
            avg = sum(abs(C[k] - C[k - 1]) / px[k] for k in range(t - 14, t)) / 14
            if (C[t] - C[t - 1]) / px[t] < -avg: a5[t + 1] += (O[t + 1] - C[t]) / px[t + 1] - rm.C2
    out['A4'] = pd.Series(a4, index=idx); out['A5'] = pd.Series(a5, index=idx)
    return out


def main():
    ref, _ = rm.part_a([])
    mine = {'A1': a1_loop(), **daily_loops()}
    ok = True
    print("=== RULEMINE Part A re-computed with plain loops vs rulemine.part_a ===")
    for k in ('A1', 'A2', 'A3', 'A4', 'A5'):
        a, b = ref[k], mine[k]; common = a.index.intersection(b.index)
        d = np.abs(a.reindex(common).values - b.reindex(common).values).max()
        same = len(common) == len(a) == len(b) and d < 1e-12; ok &= same
        usd = lambda s: s.sum() * rm.N_USD / (len(s) / 252)
        print(f"   {k}: days {len(a)} vs {len(b)}, max |daily difference| {d:.1e}, ${usd(a):+,.0f}/yr vs ${usd(b):+,.0f}/yr  -> {'MATCH' if same else 'DIFFERENT'}")
    print(f"RESULT: {'all five match' if ok else 'MISMATCH - see above'}")


if __name__ == '__main__':
    main()
