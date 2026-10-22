r"""Market vs limit entry at the 10:00 decision on 491 MNQ entries 2019-2026 (LIMIT_PLAN.txt, pre-registered 2026-10-20).
    python limit_test.py      -> limit_output.txt, limit_entries.csv
Data: D:\databento_archive\mnq_trades_1000_windows_2019-2026\chunk_*.parquet (Databento trades, MNQ.v.0, 09:59-10:05 ET).
Rules as EXEC_PILOT_PLAN sections 3-4 (Part B tape rules), re-implemented here.
"""
import os, sys, glob
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd

DATA = r"D:\databento_archive\mnq_trades_1000_windows_2019-2026"
TICK = 0.25; NS = 1_000_000_000; MS = 1_000_000; LATS = (25, 75, 150); PRIMARY = 75; HOLD = 60; NBOOT = 10000; SEED = 20261020
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def load():
    w = pd.read_csv(os.path.join(HERE, 'limit_quote_windows.csv'), parse_dates=['start', 'end'])
    w = w[(w.start + pd.Timedelta(minutes=1)).dt.strftime('%H:%M') == '10:00'].reset_index(drop=True)
    ref = pd.read_csv(os.path.join(HERE, 's1_reference_trades_ntbars_v3.csv'), parse_dates=['entry_time', 'exit_time'])
    ref['fill_open'] = ref.entry_time - pd.Timedelta(minutes=5)
    w['fill_open'] = w.start + pd.Timedelta(minutes=1)
    w = w.merge(ref[['fill_open', 'dir', 'entry', 'points']], on='fill_open', how='left')
    tp = pd.concat([pd.read_parquet(f) for f in sorted(glob.glob(os.path.join(DATA, 'chunk_*.parquet')))], ignore_index=True)
    return w, tp


def T_ns(d):
    return pd.Timestamp(d).tz_localize('America/New_York').tz_convert('UTC').value


def evaluate(ts, px, sd, T, s):
    """ts int64 ns (sorted), px float, sd +1 buyer / -1 seller / 0 unknown. Returns dict of costs (ticks, + = worse than R)."""
    i0 = int(np.searchsorted(ts, T, 'left'))
    if i0 == 0 or i0 >= len(ts) or ts[i0] > T + 30 * NS: return None
    t0 = ts[i0]; C = px[i0 - 1]; R = px[i0]
    known = sd != 0; kts, kpx, ksd = ts[known], px[known], sd[known]

    def touch(t):
        k = int(np.searchsorted(kts, t, 'right')) - 1
        if k < 0: return np.nan, np.nan
        p, x = kpx[k], ksd[k]
        return (p - TICK, p) if x > 0 else (p, p + TICK)

    def mkt(t):
        b, a = touch(t); return a if s > 0 else b

    cost = lambda f: (f - R) * s / TICK
    o = dict(C=C, R=R, t0_ms=(t0 - T) / MS)
    for lat in LATS:
        t1 = t0 + lat * MS; m = mkt(t1); b1, a1 = touch(t1); L = C - s * TICK; tend = T + HOLD * NS
        marketable = (s > 0 and L >= a1) or (s < 0 and L <= b1)
        if marketable:
            fc = fo = (a1 if s > 0 else b1); thru = tch = True
        else:
            iw0 = int(np.searchsorted(ts, t1, 'right')); iw1 = int(np.searchsorted(ts, tend, 'left')); seg = px[iw0:iw1]
            if s > 0: thru = len(seg) > 0 and seg.min() <= L - TICK; tch = len(seg) > 0 and seg.min() <= L
            else: thru = len(seg) > 0 and seg.max() >= L + TICK; tch = len(seg) > 0 and seg.max() >= L
            conv = mkt(tend + lat * MS); fc = L if thru else conv; fo = L if tch else conv
        o[f'mkt_{lat}'] = cost(m); o[f'lim_{lat}'] = cost(fc); o[f'limopt_{lat}'] = cost(fo)
        o[f'filled_{lat}'] = bool(thru); o[f'marketable_{lat}'] = bool(marketable)
    return o


def boot(x):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; rng = np.random.default_rng(SEED)
    m = x[rng.integers(0, len(x), (NBOOT, len(x)))].mean(1); return x.mean(), np.percentile(m, 2.5), np.percentile(m, 97.5), len(x)


def main():
    w, tp = load()
    sdmap = {'B': 1, 'A': -1}
    pr(f"LIMIT TEST (LIMIT_PLAN.txt). {len(w)} 10:00 entries, {len(tp):,} trades in {tp.win.nunique()} windows; reference matched "
       f"{int(w.dir.notna().sum())}; latencies {LATS} ms (primary {PRIMARY}), limit 1 tick better held {HOLD} s, conservative fills")
    rows = []
    for i, r in w.iterrows():
        g = tp[tp.win == i].sort_values('ts', kind='mergesort')       # stable: same-timestamp trades keep the exchange's order
        if not len(g) or not np.isfinite(r.dir): continue
        ts = pd.to_datetime(g.ts).values.astype('datetime64[ns]').astype(np.int64)
        e = evaluate(ts, g.price.values.astype(float), g.side.map(sdmap).fillna(0).values.astype(int), T_ns(r.fill_open), int(r.dir))
        if e is None: continue
        e.update(date=r.fill_open.normalize(), dir=int(r.dir), points=r.points, iid=int(g.iid.iloc[0]), ref_entry=r.entry, n_trades=len(g))
        rows.append(e)
    E = pd.DataFrame(rows); E.to_csv(os.path.join(HERE, 'limit_entries.csv'), index=False)
    pr(f"evaluated {len(E)} entries ({len(w) - len(E)} without a usable window)")
    for lat in LATS:
        mk = boot(E[f'mkt_{lat}']); sv = boot(E[f'mkt_{lat}'] - E[f'lim_{lat}']); so = boot(E[f'mkt_{lat}'] - E[f'limopt_{lat}'])
        pr(f"  {lat:3d} ms: market cost {mk[0]:+.2f} ticks/side [{mk[1]:+.2f}, {mk[2]:+.2f}] | limit saving conservative {sv[0]:+.2f} "
           f"[{sv[1]:+.2f}, {sv[2]:+.2f}], optimistic {so[0]:+.2f} | filled at the limit {E[f'filled_{lat}'].mean() * 100:.0f}%, "
           f"marketable at once {E[f'marketable_{lat}'].mean() * 100:.0f}%")
    p = PRIMARY; E['save'] = E[f'mkt_{p}'] - E[f'lim_{p}']
    h1 = E[E.date <= '2022-12-31'].save; h2 = E[E.date >= '2023-01-01'].save
    top = E.points >= E.points.quantile(0.8)
    sv = boot(E.save)
    pr(f"\nPRIMARY ({p} ms): saving {sv[0]:+.2f} [{sv[1]:+.2f}, {sv[2]:+.2f}] ticks/side; 2019-2022 {h1.mean():+.2f} (n {len(h1)}), "
       f"2023-2026 {h2.mean():+.2f} (n {len(h2)}); runners (top 20% by points) {E[top].save.mean():+.2f}, the rest {E[~top].save.mean():+.2f}; "
       f"missed limits cost {(-E[~E[f'filled_{p}'] & ~E[f'marketable_{p}']].save).mean():+.2f} ticks more than market on those entries")
    yrs = (E.date.max() - E.date.min()).days / 365.25; per_yr = len(E) / yrs
    pr(f"   value: {sv[0]:+.2f} ticks x $0.50 x {per_yr:.0f} 10:00 entries a year = ${sv[0] * 0.5 * per_yr:+,.0f} a year per micro")
    promising = sv[0] >= 0.5 and sv[1] > 0 and h1.mean() > 0 and h2.mean() > 0 and E[top].save.mean() >= -2
    mk = boot(E[f'mkt_{p}'])
    pr(f"   VERDICT: limit entry at 10:00 {'PROMISING' if promising else 'NOT PROMISING'} (bar: >= +0.5 with CI above 0, both halves +, runners >= -2)")
    pr(f"COST-MODEL CHECK: 10:00 market entries cost {mk[0]:+.2f} ticks/side [{mk[1]:+.2f}, {mk[2]:+.2f}] at {p} ms vs the backtest's +1.00 "
       f"({'ABOVE the live gate 1.5' if mk[0] > 1.5 else ('above 1 tick' if mk[0] > 1 else 'within 1 tick')}); by year: "
       + ' '.join(f"{y}:{v:+.2f}" for y, v in E.groupby(E.date.dt.year)[f'mkt_{p}'].mean().items()))
    pr(f"   R - C (the open vs the decision close, ticks): mean {((E.R - E.C) * E.dir / TICK).mean():+.2f}; first trade after 10:00:00 "
       f"arrives after {E.t0_ms.median():.0f} ms (median)")
    # checks
    off = (E.R - E.ref_entry); g = E.assign(off=off).groupby('iid').off
    near = (np.abs(off - g.transform(lambda x: x.round(2).mode().iloc[0])) <= TICK + 1e-9).mean()
    pr(f"\nCHECKS: R vs the reference entry price (NinjaTrader bars, back-adjusted): within 1 tick of each contract's modal offset on "
       f"{near * 100:.0f}% of entries; windows with trades on both sides of 10:00: {len(E)}/{len(w)}")
    c = evaluate(np.array([0, 10, 20, 30]) * MS + T_ns('2026-01-02 10:00'), np.array([100.0, 100.25, 100.5, 100.75]), np.array([1, 1, 1, 1]),
                 T_ns('2026-01-02 10:00') + 5 * MS, 1)
    pr(f"   synthetic buy into a rising tape: market cost {c['mkt_75']:+.2f} ticks (must be >= 0)")
    for _, x in E.sample(3, random_state=1).iterrows():
        pr(f"   hand-check {x.date.date()} dir {x.dir:+d}: C {x.C} R {x.R} market {x['mkt_75']:+.2f} limit {x['lim_75']:+.2f} filled {x['filled_75']}")
    with open(os.path.join(HERE, 'limit_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
