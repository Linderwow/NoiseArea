from __future__ import annotations

import sys, time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_backtest as yb
yb.STORE = r"D:\dev\trading\E5n\data\market\nt_minute"
from yt_captain_st import grade, hma_supertrend
from yt_captain_2024 import five, TICK


def simulate(O, H, L, C, order, trend, T, S, slip=TICK):
    N = len(C); out = []; pos = 0; entry = tgt = stp = 0.0; fill_i = 0; pending = 0
    for i in range(1, N):
        if order[i] != order[i - 1]:
            if pos: out.append((fill_i, i, pos, entry, O[i] - pos * slip, "roll")); pos = 0
            pending = 0; continue
        if pending:
            if pos == -pending: out.append((fill_i, i, pos, entry, O[i] - pos * slip, "reverse")); pos = 0
            if pos == 0:
                pos = pending; entry = O[i] + pos * slip; tgt = entry + pos * T * TICK; stp = entry - pos * S * TICK; fill_i = i
            pending = 0
        if pos and i >= fill_i + 1:
            hit_s = (L[i] <= stp) if pos > 0 else (H[i] >= stp)
            hit_t = (H[i] >= tgt + TICK) if pos > 0 else (L[i] <= tgt - TICK)
            if hit_s:
                out.append((fill_i, i, pos, entry, (min(stp, O[i]) if pos > 0 else max(stp, O[i])) - pos * slip, "stop")); pos = 0
            elif hit_t:
                out.append((fill_i, i, pos, entry, max(tgt, O[i]) if pos > 0 else min(tgt, O[i]), "target")); pos = 0
        if trend[i] != trend[i - 1] and (pos == 0 or pos == -trend[i]): pending = int(trend[i])
    tr = pd.DataFrame(out, columns=["fill_i", "exit_i", "side", "entry", "exit", "kind"])
    tr["pts"] = tr.side * (tr.exit - tr.entry)
    return tr


def run(df, label, pt_usd, comm, trials, scale=1.0):
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close")); order = df["order"].to_numpy(); day = df["day"].to_numpy()
    prim = pd.Index(sorted(df.day[df.day <= yb.PRIMARY_END].unique())); hold = pd.Index(sorted(df.day[df.day > yb.PRIMARY_END].unique()))
    _, _, trend = hma_supertrend(H, L, C, 14, 2.5, 14); res = []
    for T, S in trials:
        Ts, Ss = max(1, round(T * scale)), max(1, round(S * scale)); name = f"{label} T{T}/S{S}"
        tr = simulate(O, H, L, C, order, trend, Ts, Ss); d = day[tr.exit_i.to_numpy()]
        gp = grade(tr[d <= yb.PRIMARY_END], df, prim, pt_usd, comm); gh = grade(tr[d > yb.PRIMARY_END], df, hold, pt_usd, comm)
        z = simulate(O, H, L, C, order, trend, Ts, Ss, slip=0.0); zd = day[z.exit_i.to_numpy()]; gz = grade(z[zd <= yb.PRIMARY_END], df, prim, pt_usd, 0.0)
        res.append((name, gp, gh))
        print(f"{name:<18} {gp['per_day']:>6.1f} {gp['win']:>4.0%} {gp['pts']:>8.2f} {gp['net_day']:>8.1f} {gp['t_net']:>6.2f} {gz['t_gross']:>7.2f} "
              f"{gp['neg_years']:>3}/{gp['years']:<3} {gp['drop5']:>9,.0f} | {gh['net_day']:>7.1f} {gh['t_net']:>6.2f}", flush=True)
    return res


def main():
    t0 = time.time(); mnq = yb.load("MNQ", None)
    print(f"MNQ minute store {len(mnq):,} bars {mnq.day.iloc[0]} .. {mnq.day.iloc[-1]}; HMA SuperTrend flips, symmetric fixed bracket; $1.00 a round trip + 1 tick a market fill")
    print(f"{'trial':<18} {'tr/day':>6} {'win':>4} {'pt/tr':>8} {'$/day':>8} {'t net':>6} {'t 0cost':>7} {'neg yrs':>7} {'drop5 $':>9} | holdout $/day t")
    res = run(mnq, "1m", 2.0, 1.0, [(20, 20), (40, 40), (60, 60), (80, 80), (100, 100), (1000, 100)]) + run(five(mnq), "5m", 2.0, 1.0, [(1000, 100)])
    alive = [r for r in res if r[1]["t_net"] >= 3.0 and r[1]["neg_years"] <= 1 and r[2]["net_day"] > 0]
    print("\nsurvivors (primary t >= 3.0, <= 1 losing year, holdout positive): " + (", ".join(f"{r[0]} (t {r[1]['t_net']:.2f})" for r in alive) if alive else "NONE"))
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
