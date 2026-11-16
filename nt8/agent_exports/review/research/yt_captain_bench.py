from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yt_backtest as yb
import yt_captain_st as cs


def base_trades(df, T, pt_usd=2.0, comm=1.0):
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    fu, fd, trend = cs.hma_supertrend(H, L, C, 14, 2.5, 14)
    tr = cs.simulate(O, H, L, C, df["order"].to_numpy(), fu, fd, trend, T, slip=cs.TICK)
    t_et = df.time.dt.tz_convert("America/New_York").dt.tz_localize(None).to_numpy()
    tr["usd"] = tr.pts * pt_usd - comm
    tr["day"] = df["day"].to_numpy()[tr.exit_i.to_numpy()]
    tr["session"] = pd.to_datetime(t_et[tr.fill_i.to_numpy()] + np.timedelta64(6, "h")).strftime("%Y-%m-%d")
    return tr


def bench(tr, K, release):
    keep = np.zeros(len(tr), bool)
    streak, benched, sess = 0, False, None
    for q, (u, s) in enumerate(zip(tr.usd.to_numpy(), tr.session.to_numpy())):
        if s != sess:
            sess = s
            if release == "session":
                benched, streak = False, 0
        if benched:
            if release == "paper" and u > 0:
                benched, streak = False, 0
            continue
        keep[q] = True
        streak = streak + 1 if u <= 0 else 0
        if streak >= K:
            benched = True
    return tr[keep]


def daily(tr, days):
    return tr.groupby("day").usd.sum().reindex(days, fill_value=0.0)


def stats(x):
    yrs = x.groupby(x.index.str[:4]).sum()
    return x.mean(), yb.tstat(x.to_numpy()), int((yrs < 0).sum()), len(yrs)


def main():
    t0 = time.time()
    mnq = yb.load("MNQ", None)
    days = pd.Index(sorted(mnq.day.unique()))
    prim, hold = days[days <= yb.PRIMARY_END], days[days > yb.PRIMARY_END]
    print(f"MNQ {days[0]} .. {days[-1]}; one micro, $1 + 1 tick per market fill\n")
    print(f"{'trial':<26} {'kept':>5} {'net $/day':>9} {'t':>6} {'-yrs':>5} | {'hold $/d':>8} {'t':>6} | always-on $/day")
    res = []
    for T in (10, 40, 100, 1000):
        tr = base_trades(mnq, T)
        win = (tr.usd > 0).to_numpy().astype(float)
        same = tr.session.to_numpy()[1:] == tr.session.to_numpy()[:-1]
        ac = np.corrcoef(win[1:][same], win[:-1][same])[0, 1]
        base = daily(tr, days)
        bm, bt, *_ = stats(base[prim])
        print(f"T{T}: within-session lag-1 correlation of win/loss {ac:+.3f} (benching needs it clearly > 0); always-on t {bt:.2f}")
        for K in (2, 3, 5):
            for rel in ("session", "paper"):
                b = bench(tr, K, rel)
                dn = daily(b, days)
                m, t, ny, yy = stats(dn[prim])
                hm, ht, *_ = stats(dn[hold])
                name = f"T{T} K{K} {rel}"
                res.append((name, m, t, ny, hm, ht))
                print(f"{name:<26} {len(b) / len(tr):>5.0%} {m:>9.1f} {t:>6.2f} {ny:>2}/{yy:<2} | {hm:>8.1f} {ht:>6.2f} | {bm:>8.1f}"
                      f"   ({time.time() - t0:.0f} s)", flush=True)
    alive = [r for r in res if r[2] >= 2.0 and r[3] <= 1 and r[4] > 0]
    print("\nsurvivors (primary t >= 2, <= 1 losing year, holdout positive): " + (", ".join(r[0] for r in alive) if alive else "none"))
    best = max(res, key=lambda r: r[2])
    print(f"best primary t: {best[0]} ${best[1]:+.1f}/day t {best[2]:.2f}; holdout ${best[4]:+.1f}/day t {best[5]:.2f}")


if __name__ == "__main__":
    main()
