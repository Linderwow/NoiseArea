"""trendline_parity.py - the SwingTrendlines indicator's trade export (agent_exports\\SwingTrendlines_trades_<inst>_<period>_<tag>.csv)
against the research port trendline.run_scribd('SB') on NinjaTrader's minute store, trade for trade. PASS when at least 95 % of
the chart's trades (after a one-day warm-up) match the port's on entry minute and side, and at least 95 % of the matches agree on
the exit kind and on points within one tick. --selftest builds an export from the port itself (must PASS) and a tampered copy
(must FAIL)."""
import argparse
import os
import re
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import onemin as om
import trendline as tl

TICK = 0.25


def port_trades(k, strength, target_r):
    B = tl.make_bars(om.mins_nt(), k)
    rows = []
    for fb, xb, s, e, x, nm, nl, kind in tl.run_scribd(B, 'SB', N=strength, r_mult=target_r):
        rows.append((pd.Timestamp(B.ts[fb]) + pd.Timedelta(minutes=k), 'long' if s > 0 else 'short',
                     pd.Timestamp(B.ts[xb]) + pd.Timedelta(minutes=k), kind, s * (x - e)))
    return pd.DataFrame(rows, columns=['entry_time', 'side', 'exit_time', 'kind', 'points'])


def compare(chart, port):
    a = chart.entry_time.min() + pd.Timedelta(days=1)
    b = min(chart.exit_time.max(), port.entry_time.max())
    c = chart[(chart.entry_time >= a) & (chart.entry_time <= b)].copy()
    p = port[(port.entry_time >= a) & (port.entry_time <= b)].copy()
    m = c.merge(p, on=['entry_time', 'side'], how='left', suffixes=('', '_port'))
    found = m.kind_port.notna()
    same = found & (m.kind == m.kind_port) & ((m.points - m.points_port).abs() <= TICK + 1e-9)
    back = p.merge(c[['entry_time', 'side']], on=['entry_time', 'side'], how='left', indicator=True)
    r_found = found.mean() if len(m) else 0.0
    r_same = same.sum() / max(found.sum(), 1)
    r_back = (back['_merge'] == 'both').mean() if len(back) else 0.0
    ok = len(m) > 0 and r_found >= 0.95 and r_same >= 0.95 and r_back >= 0.95
    print(f"chart trades {len(m)} (from {a:%Y-%m-%d}), found in the port {r_found:.1%}, port trades found on the chart "
          f"{r_back:.1%}, same exit kind and points {r_same:.1%} -> {'PARITY PASS' if ok else 'PARITY FAIL'}")
    if not ok:
        print(m[~same].head(10).to_string())
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('csv', nargs='?')
    ap.add_argument('--strength', type=int, default=5)
    ap.add_argument('--target', type=float, default=2.0)
    ap.add_argument('--minutes', type=int, default=None)
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        port = port_trades(1, args.strength, args.target)
        w = port[(port.entry_time >= '2026-09-01') & (port.entry_time < '2026-10-01')]
        good = compare(w.copy(), port)
        bad = w.copy()
        bad.loc[bad.index[::8], 'points'] += 1.0
        bad = bad.drop(bad.index[1::12])
        broken = compare(bad, port)
        print('SELFTEST ' + ('OK' if good and not broken else 'FAILED'))
        sys.exit(0 if good and not broken else 1)
    chart = pd.read_csv(args.csv, parse_dates=['entry_time', 'exit_time'])
    k = args.minutes
    if k is None:
        mm = re.search(r'_(\d+)Minute_', os.path.basename(args.csv))
        k = int(mm.group(1)) if mm else 1
    sys.exit(0 if compare(chart, port_trades(k, args.strength, args.target)) else 1)


if __name__ == '__main__':
    main()
