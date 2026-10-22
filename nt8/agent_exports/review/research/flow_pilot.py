r"""Free order-flow pilot (FLOWPILOT_PLAN.txt): aligned buy/sell imbalance in the 15 minutes before each NoiseArea entry decision vs
the trade's outcome, on the tick tapes already owned (MES Sep 2025 - Jun 2026; MNQ Jan - Apr 2026).
    python flow_pilot.py
"""
import os, sys, ctypes
sys.dont_write_bytecode = True
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__))
import numpy as np, pandas as pd
from scipy.stats import spearmanr

ARCH = r"D:\databento_archive"


def flow_mes():
    f = pd.read_csv(os.path.join(HERE, 'regime_work', 'mes_flow5m_2025-09_2026-06.csv'), parse_dates=['time_et'])
    return f.set_index('time_et')[['buy_vol', 'sell_vol']].rename(columns={'buy_vol': 'buy', 'sell_vol': 'sell'}), None   # stamped at bar close


def flow_mnq():
    parts = []
    for fn in ('mnq_trades_2026-01-01_2026-04-16_features2m.csv', 'mnq_trades_2026-04-10_2026-04-17_features2m.csv'):
        f = pd.read_csv(os.path.join(ARCH, fn)); t = pd.to_datetime(f.ts_utc, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
        f.index = t + pd.Timedelta(minutes=2)                                     # 2-minute bars stamped at their START -> use the close time
        parts.append(f[['buy_vol', 'sell_vol']].rename(columns={'buy_vol': 'buy', 'sell_vol': 'sell'}))
    x = pd.concat(parts); x = x[~x.index.duplicated(keep='first')].sort_index()
    return x, pd.Timestamp('2026-04-09 11:40')                                      # the first tape stops here


def test(name, flow, cutoff_gap, tr):
    lo, hi = flow.index.min(), flow.index.max(); rows = []
    for r in tr.itertuples():
        d = r.decision
        if d - pd.Timedelta(minutes=15) < lo or d > hi: continue
        if cutoff_gap is not None and cutoff_gap <= d < pd.Timestamp('2026-04-10'): continue
        w = flow[(flow.index > d - pd.Timedelta(minutes=15)) & (flow.index <= d)]
        tot = w.buy.sum() + w.sell.sum()
        if len(w) == 0 or tot == 0: continue
        rows.append(dict(decision=d, dir=r.dir, points=r.points, ofi=r.dir * (w.buy.sum() - w.sell.sum()) / tot))
    df = pd.DataFrame(rows)
    if len(df) < 10: return f"  {name}: only {len(df)} trades covered", df
    rho, p = spearmanr(df.ofi, df.points); med = df.ofi.median()
    hi_, lo_ = df[df.ofi > med].points, df[df.ofi <= med].points
    line = (f"  {name}: {len(df)} trades ({df.decision.min():%Y-%m-%d} .. {df.decision.max():%Y-%m-%d}); Spearman rho {rho:+.3f} (p {p:.3f}); "
            f"OFI above median: mean {hi_.mean():+.1f} pts ({len(hi_)}), below: {lo_.mean():+.1f} pts ({len(lo_)}); all trades {df.points.mean():+.1f} pts")
    return line, df


def main():
    tr = pd.read_csv(os.path.join(HERE, 's1_reference_trades_ntbars_v3.csv'), parse_dates=['entry_time'])
    tr['decision'] = tr.entry_time - pd.Timedelta(minutes=5)
    out = ["=== FREE ORDER-FLOW PILOT (FLOWPILOT_PLAN.txt): aligned 15-minute buy/sell imbalance before NoiseArea entries vs trade outcome ==="]
    res = {}
    for name, fn in (('MES tape (main)', flow_mes), ('MNQ tape', flow_mnq)):
        flow, gap = fn(); line, df = test(name, flow, gap, tr); out.append(line); res[name] = df
    m, q = res.get('MES tape (main)'), res.get('MNQ tape')
    if m is not None and len(m) >= 10:
        rho, p = spearmanr(m.ofi, m.points); r2 = spearmanr(q.ofi, q.points)[0] if q is not None and len(q) >= 10 else 0.0
        ok = abs(rho) >= 0.15 and p < 0.10 and not (np.sign(r2) == -np.sign(rho) and r2 != 0)
        out.append(f"  PILOT RULE: {'WORTH BUYING the full NQ history for a proper test' if ok else 'no clear signal - the purchase is a long shot'}")
    txt = "\n".join(out); print(txt); open(os.path.join(HERE, 'flow_pilot_output.txt'), 'w').write(txt + "\n")


if __name__ == '__main__':
    main()
