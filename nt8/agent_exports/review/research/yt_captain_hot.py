from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yt_backtest as yb
import yt_captain_st as cs

LOOKBACKS = (5, 10, 20)


def daily_net(df, days, T, pt_usd=2.0, comm=1.0):
    O, H, L, C = (df[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    fu, fd, trend = cs.hma_supertrend(H, L, C, 14, 2.5, 14)
    tr = cs.simulate(O, H, L, C, df["order"].to_numpy(), fu, fd, trend, T, slip=cs.TICK)
    day = df["day"].to_numpy()[tr.exit_i.to_numpy()]
    return pd.Series(tr.pts.to_numpy() * pt_usd - comm, index=day).groupby(level=0).sum().reindex(days, fill_value=0.0)


def hot(daily: pd.Series, n: int) -> pd.Series:
    on = daily.rolling(n, min_periods=n).sum().shift(1) > 0
    return daily.where(on.astype(bool), 0.0), on.astype(bool)


def stats(x: pd.Series):
    yrs = x.groupby(x.index.str[:4]).sum()
    return x.mean(), yb.tstat(x.to_numpy()), int((yrs < 0).sum()), len(yrs)


def main() -> None:
    t0 = time.time()
    mnq = yb.load("MNQ", None)
    days = pd.Index(sorted(mnq.day.unique()))
    print(f"MNQ minute store {len(mnq):,} bars {days[0]} .. {days[-1]}  ({len(days)} days)")
    prim, hold = days[days <= yb.PRIMARY_END], days[days > yb.PRIMARY_END]
    print(f"\n{'copy':<6} {'wk ac1':>6} | {'all days $/d':>12} {'t':>6} | " + " | ".join(
        f"hot{n:<2} $/d    t  -yrs  on%  hold $/d   t" for n in LOOKBACKS))
    res = []
    for T in cs.TARGETS:
        dn = daily_net(mnq, days, T)
        p = dn[prim]
        wk = p.groupby(pd.to_datetime(p.index).to_period("W-FRI")).sum()
        ac = float(np.corrcoef(wk.to_numpy()[1:], wk.to_numpy()[:-1])[0, 1])
        line = f"T{T:<5} {ac:>+6.2f} | {p.mean():>12.1f} {yb.tstat(p.to_numpy()):>6.2f} |"
        for n in LOOKBACKS:
            f, on = hot(dn, n)
            m, t, ny, yy = stats(f[prim])
            hm, ht, *_ = stats(f[hold])
            res.append((T, n, m, t, ny, yy, hm, ht))
            line += f" {m:>8.1f} {t:>5.2f} {ny:>2}/{yy} {on[prim].mean():>4.0%} {hm:>8.1f} {ht:>5.2f} |"
        print(line + f"  ({time.time() - t0:.0f} s)", flush=True)
    alive = [r for r in res if r[3] >= 2.0 and r[4] <= 1 and r[6] > 0]
    print("\nsurvivors (primary t >= 2, <= 1 losing year, holdout positive): "
          + (", ".join(f"T{r[0]} hot{r[1]} t {r[3]:.2f}" for r in alive) if alive else "none"))
    best = max(res, key=lambda r: r[3])
    print(f"best primary t: T{best[0]} hot{best[1]} ${best[2]:+.1f}/day t {best[3]:.2f}, holdout ${best[6]:+.1f}/day t {best[7]:.2f}")
    try:
        mes = yb.load("MES", None)
        md = pd.Index(sorted(mes.day.unique()))
        ratio = float(mes.close.median() / mnq.close.median())
        ticks = max(1, round(best[0] * ratio))
        f, _ = hot(daily_net(mes, md, ticks, pt_usd=5.0), best[1])
        m, t, *_ = stats(f[md[md <= yb.PRIMARY_END]])
        print(f"MES check of the best (target {ticks} ticks, hot{best[1]}): ${m:+.1f}/day t {t:.2f} - "
              f"{'agrees' if (m > 0) == (best[2] > 0) else 'DISAGREES'}")
    except Exception as e:
        print(f"MES check not run: {e}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
