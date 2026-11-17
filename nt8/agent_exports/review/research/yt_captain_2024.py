from __future__ import annotations

import sys, time
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_backtest as yb
yb.STORE = r"D:\dev\trading\E5n\data\market\nt_minute"
import yt_indicator_sweep as sw
from yt_captain_st import grade

TICK = 0.25
TARGETS = (20, 40, 60, 80, 100)


def supertrend(H, L, C, p=10, mult=3.0):
    pc = np.concatenate([[C[0]], C[:-1]])
    atr = sw.wilder(np.maximum(H - L, np.maximum(np.abs(H - pc), np.abs(L - pc))), p)
    mid = (H + L) / 2; up, dn = mid - mult * atr, mid + mult * atr
    N = len(C); fu, fd = up.copy(), dn.copy(); trend = np.ones(N, np.int8)
    for i in range(1, N):
        fu[i] = max(up[i], fu[i - 1]) if C[i - 1] > fu[i - 1] else up[i]
        fd[i] = min(dn[i], fd[i - 1]) if C[i - 1] < fd[i - 1] else dn[i]
        if trend[i - 1] == -1 and C[i] > fd[i - 1]: trend[i] = 1
        elif trend[i - 1] == 1 and C[i] < fu[i - 1]: trend[i] = -1
        else: trend[i] = trend[i - 1]
    return fu, fd, trend


def simulate(O, H, L, C, order, fu, fd, trend, T, fixed, slip=TICK):
    N = len(C); out = []; pos = 0; entry = tgt = stp0 = 0.0; fill_i = 0; pending = 0
    for i in range(1, N):
        if order[i] != order[i - 1]:
            if pos: out.append((fill_i, i, pos, entry, O[i] - pos * slip, "roll")); pos = 0
            pending = 0; continue
        if pending:
            if pos == -pending: out.append((fill_i, i, pos, entry, O[i] - pos * slip, "reverse")); pos = 0
            if pos == 0:
                pos = pending; entry = O[i] + pos * slip; tgt = entry + pos * T * TICK; stp0 = entry - pos * T * TICK; fill_i = i
            pending = 0
        if pos and i >= fill_i + 1:
            stp = stp0 if fixed else (fu[i - 1] if pos > 0 else fd[i - 1])
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


def five(df):
    k = df.time.dt.floor("5min")
    g = df.groupby([df["order"], k], sort=True)
    b = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"), day=("day", "last")).reset_index()
    return b.rename(columns={"level_1": "time"}).sort_values(["time"]).reset_index(drop=True)


def run(df, label, pt_usd, comm, scale=1.0, only=None):
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close")); order = df["order"].to_numpy(); day = df["day"].to_numpy()
    prim = pd.Index(sorted(df.day[df.day <= yb.PRIMARY_END].unique())); hold = pd.Index(sorted(df.day[df.day > yb.PRIMARY_END].unique()))
    fu, fd, trend = supertrend(H, L, C); res = []
    for fixed in (True, False):
        for T in TARGETS:
            name = f"{label} {'A fixed' if fixed else 'B band '} T{T}"
            if only and name.split(' ', 1)[1] not in only: continue
            Ts = max(1, round(T * scale)); tr = simulate(O, H, L, C, order, fu, fd, trend, Ts, fixed)
            d = day[tr.exit_i.to_numpy()]; gp = grade(tr[d <= yb.PRIMARY_END], df, prim, pt_usd, comm); gh = grade(tr[d > yb.PRIMARY_END], df, hold, pt_usd, comm)
            z = simulate(O, H, L, C, order, fu, fd, trend, Ts, fixed, slip=0.0); zd = day[z.exit_i.to_numpy()]; gz = grade(z[zd <= yb.PRIMARY_END], df, prim, pt_usd, 0.0)
            res.append((name, gp, gh))
            print(f"{name:<22} {gp['per_day']:>6.1f} {gp['win']:>4.0%} {gp['pts']:>8.2f} {gp['net_day']:>8.1f} {gp['t_net']:>6.2f} {gz['t_gross']:>7.2f} "
                  f"{gp['neg_years']:>3}/{gp['years']:<3} {gp['drop5']:>9,.0f} | {gh['net_day']:>7.1f} {gh['t_net']:>6.2f}", flush=True)
    return res


def main():
    t0 = time.time(); mnq = yb.load("MNQ", None)
    print(f"MNQ minute store {len(mnq):,} bars {mnq.day.iloc[0]} .. {mnq.day.iloc[-1]}; SuperTrend 10 3; $1.00 a round trip + 1 tick a market fill")
    print(f"{'trial':<22} {'tr/day':>6} {'win':>4} {'pt/tr':>8} {'$/day':>8} {'t net':>6} {'t 0cost':>7} {'neg yrs':>7} {'drop5 $':>9} | holdout $/day t")
    res = run(mnq, "1m", 2.0, 1.0) + run(five(mnq), "5m", 2.0, 1.0)
    alive = [r for r in res if r[1]["t_net"] >= 3.0 and r[1]["neg_years"] <= 1 and r[2]["net_day"] > 0]
    print("\nsurvivors (primary t >= 3.0, <= 1 losing year, holdout positive): " + (", ".join(f"{r[0]} (t {r[1]['t_net']:.2f})" for r in alive) if alive else "NONE"))
    best = sorted(res, key=lambda r: -r[1]["t_net"])[:2]
    try:
        mes = yb.load("MES", None); ratio = float(mes.close.median() / mnq.close.median())
        for name, gp, _ in best:
            tf = name.split()[0]; m = mes if tf == "1m" else five(mes)
            print(f"MES check of {name} (MNQ t {gp['t_net']:.2f}):"); run(m, tf, 5.0, 1.0, ratio, only={name.split(' ', 1)[1]})
    except Exception as e:
        print(f"MES check not run: {e}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
