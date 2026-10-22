"""Overnight-session noise-area momentum, per OVERNIGHT_PLAN.txt."""
import numpy as np, pandas as pd
from sim import load

TICK = 0.25


def sessions(path):
    df = load(path)
    df['cal'] = df.time.dt.date
    rth = df[df.in_rth.values]
    rth_close = rth.groupby('cal').close.last()                       # 16:00 close per calendar day (last RTH bar)
    rth_ok = rth.groupby('cal').etmin.agg(['first', 'size'])
    rth_ok = rth_ok[(rth_ok['first'] == 575) & (rth_ok['size'] >= 30)].index
    rth_close = rth_close.reindex(rth_ok)
    on = df[(df.etmin > 17 * 60) | (df.etmin <= 9 * 60 + 30)].copy()  # 18:05 .. 09:30, keyed by Globex trading day
    out = []
    for td, g in on.groupby('tday', sort=True):
        M = g.etmin.values.astype(int)
        if M[0] != 18 * 60 + 5 or len(g) < 150: continue
        prev_days = rth_close.index[rth_close.index < td]
        if len(prev_days) == 0: continue
        out.append(dict(tday=td, O=g.open.values, H=g.high.values, L=g.low.values, C=g.close.values, V=g.volume.values.astype(float),
                        M=M, prior_close=float(rth_close.loc[prev_days[-1]]), open=float(g.open.values[0])))
    return out


def run(S, pv=2.0, vm=1.0, look=14, step=30, first=19 * 60, last=9 * 60, slip=TICK, comm=0.5):
    """Daily P&L per overnight session. Check slots use minutes-since-18:00 so the overnight wrap is handled."""
    def key(m): return (m - 18 * 60) % (24 * 60)                       # 18:00 -> 0, 09:30 -> 930
    kfirst, klast = key(first), key(last)
    hist = []; rows = []
    for s in S:
        K = np.array([key(m) for m in s['M']]); C = s['C']; O = s['O']
        mv = {int(k): abs(c / s['open'] - 1) for k, c in zip(K, C)}
        pnl = 0.0; n = 0
        if len(hist) >= look:
            tp = (s['H'] + s['L'] + C) / 3; vw = np.cumsum(tp * s['V']) / np.maximum(np.cumsum(s['V']), 1)
            hi = max(s['open'], s['prior_close']); lo = min(s['open'], s['prior_close'])
            pos = 0; entry = 0.0; pending = None
            for i in range(len(K)):
                if pending is not None:
                    px = O[i]
                    if pos != 0: pnl += (px - slip * pos - entry) * pos * pv - comm
                    pos = pending
                    if pos != 0: entry = px + slip * pos; pnl -= comm; n += 1
                    pending = None
                if i == len(K) - 1:
                    if pos != 0: pnl += (C[i] - slip * pos - entry) * pos * pv - comm; pos = 0
                    break
                k = int(K[i])
                if k < kfirst or k > klast or (k - kfirst) % step != 0: continue
                vals = [h[k] for h in hist[-look:] if k in h]
                if len(vals) < max(1, (look + 1) // 2): continue
                sg = float(np.mean(vals)); ub = hi * (1 + vm * sg); lb = lo * (1 - vm * sg); p = C[i]
                if pos == 0:
                    if p > ub: pending = 1
                    elif p < lb: pending = -1
                elif pos == 1:
                    if p < lb: pending = -1
                    elif p < max(ub, vw[i]): pending = 0
                else:
                    if p > ub: pending = 1
                    elif p > min(lb, vw[i]): pending = 0
            rows.append(dict(date=s['tday'], pnl=pnl, trades=n))
        hist.append(mv)
    return pd.DataFrame(rows)


def stats(df, a, b):
    d = df[(pd.to_datetime(df.date) >= a) & (pd.to_datetime(df.date) <= b)]
    p = d.pnl.values
    if len(p) < 3: return dict(days=len(p), trades=0, net=0.0, t=np.nan, mdd=np.nan)
    eq = np.cumsum(p); mdd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    return dict(days=len(p), trades=int(d.trades.sum()), net=float(p.sum()), t=float(p.mean() / (p.std(ddof=1) / np.sqrt(len(p)))), mdd=float(mdd))


if __name__ == '__main__':
    res = {}
    for path, name in (('nt_analyzer_bars.csv', 'NinjaTrader MNQ'), ('mnq_db_5m.csv', 'Databento MNQ')):
        S = sessions(path); print(f"\n{name}: {len(S)} valid overnight sessions {S[0]['tday']} .. {S[-1]['tday']}")
        for lab, kw in (('BASE vm=1.0 step=30', dict()), ('vm=0.75', dict(vm=0.75)), ('vm=1.5', dict(vm=1.5)), ('step=60', dict(step=60))):
            df = run(S, **kw)
            d = stats(df, '2019-01-01', '2022-12-31'); v = stats(df, '2023-01-01', '2026-12-31'); f = stats(df, '2019-01-01', '2026-12-31')
            print(f"  {lab:20s} discovery 2019-22: net ${d['net']:8,.0f} t {d['t']:+.2f} trades {d['trades']:4d} | validation 2023-: net ${v['net']:8,.0f} t {v['t']:+.2f} trades {v['trades']:4d} | full: net ${f['net']:8,.0f} t {f['t']:+.2f} maxDD ${f['mdd']:,.0f}")
            if lab.startswith('BASE'): res[name] = (d, v, f)
        base = run(S); y = pd.to_datetime(base.date).dt.year; print("  base by year $:", base.groupby(y).pnl.sum().round(0).astype(int).to_dict())
    ok = all(res[n][0]['net'] > 0 and res[n][1]['net'] > 0 for n in res) and res['NinjaTrader MNQ'][2]['t'] > 2
    print("\nPASS" if ok else "\nFAIL", "(base case positive in discovery and validation on both datasets, full-period t > 2 on NinjaTrader bars)")
