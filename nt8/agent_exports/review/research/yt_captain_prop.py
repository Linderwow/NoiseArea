from __future__ import annotations

import sys, time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_backtest as yb
yb.STORE = r"D:\dev\trading\E5n\data\market\nt_minute"
from yt_captain_st import hma_supertrend, simulate

PT, COMM, MLL = 20.0, 4.00, 2000.0


def day_tables(tr, df, sides=None):
    day = df["day"].to_numpy()[tr.exit_i.to_numpy()]
    pts = tr.pts.to_numpy() if sides is None else tr.pts.to_numpy() * sides
    usd = pts * PT - COMM
    t = pd.DataFrame({"day": day, "usd": usd})
    t["cum"] = t.groupby("day").usd.cumsum()
    g = t.groupby("day").agg(pnl=("usd", "sum"), low=("cum", "min"))
    days = pd.Index(sorted(df.day.unique()))
    g = g.reindex(days, fill_value=0.0)
    return g.pnl.to_numpy(), np.minimum(g.low.to_numpy(), 0.0), days


def evals(pnl, low, target, cap=120):
    n = len(pnl); res = []
    for s in range(n - cap):
        bal = 0.0; hi = 0.0; out = None
        for k in range(s, s + cap):
            mll = hi - MLL
            if bal + low[k] <= mll: out = ("fail", k - s + 1); break
            bal += pnl[k]
            if bal <= mll: out = ("fail", k - s + 1); break
            if bal >= target: out = ("pass", k - s + 1, k); break
            hi = max(hi, bal)
        res.append(out or ("open", cap))
    return res


def payout_counter(pnl, low, start, cap=250):
    bal = hi = 0.0; wins = 0
    for k in range(start + 1, min(len(pnl), start + 1 + cap)):
        if bal + low[k] <= hi - MLL: return False
        bal += pnl[k]
        if bal <= hi - MLL: return False
        if pnl[k] >= 200: wins += 1
        if wins >= 5: return True
        hi = max(hi, bal)
    return False


def summary(res, pnl, low):
    p = [r for r in res if r[0] == "pass"]; f = [r for r in res if r[0] == "fail"]
    pay = np.mean([payout_counter(pnl, low, r[2]) for r in p]) if p else 0.0
    return len(p) / len(res), np.median([r[1] for r in p]) if p else np.nan, np.median([r[1] for r in f]) if f else np.nan, pay


def main():
    t0 = time.time(); mnq = yb.load("MNQ", "2019-06-01")
    O, H, L, C = (mnq[c].to_numpy(float) for c in ("open", "high", "low", "close")); order = mnq["order"].to_numpy()
    fu, fd, trend = hma_supertrend(H, L, C, 14, 2.5, 14); rng = np.random.default_rng(17)
    print(f"MNQ minute store {mnq.day.iloc[0]} .. {mnq.day.iloc[-1]}, priced at 1 NQ ($20/pt); MLL ${MLL:,.0f} trailing the highest end-of-day balance")
    print(f"{'copy':<6} {'cost':<9} {'$/day':>7} | {'target':>7} {'pass':>6} {'days to pass':>12} {'days to fail':>12} {'5x$200 after pass':>17} | {'ZERO-EDGE pass':>14} {'5x$200':>7}")
    for T in (40, 100, 200):
        for slip, cost in ((yb.TICK if hasattr(yb, 'TICK') else 0.25, "live"), (0.0, "zero")):
            tr = simulate(O, H, L, C, order, fu, fd, trend, T, slip=slip)
            global COMM
            keep = COMM; COMM = 4.0 if cost == "live" else 0.0
            pnl, low, days = day_tables(tr, mnq)
            for target in (1500.0, 3000.0, 4500.0):
                pr, dp, df_, pay = summary(evals(pnl, low, target), pnl, low)
                zs = []
                for _ in range(20 if target == 3000.0 else 5):
                    sd = rng.choice([-1.0, 1.0], len(tr)); zp, zl, _ = day_tables(tr, mnq, sd); zs.append(summary(evals(zp, zl, target), zp, zl))
                print(f"T{T:<5} {cost:<9} {pnl.mean():>7.0f} | {target:>7,.0f} {pr:>6.0%} {dp:>12.0f} {df_:>12.0f} {pay:>17.0%} | {np.mean([z[0] for z in zs]):>14.0%} {np.mean([z[3] for z in zs]):>7.0%}", flush=True)
            COMM = keep
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
