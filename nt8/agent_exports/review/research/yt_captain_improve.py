from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import yt_backtest as yb
import yt_captain_st as cs

TICK = 0.25
ET = "America/New_York"


def resample(df: pd.DataFrame, k: int) -> pd.DataFrame:
    if k == 1:
        b = df[["time", "open", "high", "low", "close", "order"]].copy()
    else:
        g = [df.order, df.time.dt.ceil(f"{k}min")]
        b = df.groupby(g, sort=True).agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                          close=("close", "last")).reset_index()
        b.columns = ["order", "time", "open", "high", "low", "close"]
        b = b.sort_values(["time", "order"]).drop_duplicates("time", keep="last").reset_index(drop=True)
    et = b.time.dt.tz_convert(ET)
    b["day"] = et.dt.strftime("%Y-%m-%d")
    b["mins"] = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    return b


def simulate2(O, H, L, C, order, fu, fd, trend, T, slip, allow, flat):
    N = len(C)
    out = []
    pos = 0
    entry = tgt = 0.0
    fill_i = 0
    pending = 0
    for i in range(1, N):
        if order[i] != order[i - 1]:
            if pos:
                out.append((fill_i, i, pos, entry, O[i] - pos * slip, "roll"))
                pos = 0
            pending = 0
            continue
        if pending:
            if pos == -pending:
                out.append((fill_i, i, pos, entry, O[i] - pos * slip, "reverse"))
                pos = 0
            if pos == 0:
                pos = pending
                entry = O[i] + pos * slip
                tgt = entry + pos * T * TICK if T else 0.0
                fill_i = i
            pending = 0
        if pos and i >= fill_i + 1:
            stp = fu[i - 1] if pos > 0 else fd[i - 1]
            hit_s = (L[i] <= stp) if pos > 0 else (H[i] >= stp)
            hit_t = bool(T) and ((H[i] >= tgt + TICK) if pos > 0 else (L[i] <= tgt - TICK))
            if hit_s:
                px = (min(stp, O[i]) if pos > 0 else max(stp, O[i])) - pos * slip
                out.append((fill_i, i, pos, entry, px, "stop"))
                pos = 0
            elif hit_t:
                px = max(tgt, O[i]) if pos > 0 else min(tgt, O[i])
                out.append((fill_i, i, pos, entry, px, "target"))
                pos = 0
        if pos and flat[i]:
            out.append((fill_i, i, pos, entry, C[i] - pos * slip, "session"))
            pos = 0
            pending = 0
            continue
        if trend[i] != trend[i - 1] and allow[i] and (pos == 0 or pos == -trend[i]):
            pending = int(trend[i])
    tr = pd.DataFrame(out, columns=["fill_i", "exit_i", "side", "entry", "exit", "kind"])
    tr["pts"] = tr.side * (tr.exit - tr.entry)
    return tr


def daily(tr, b, days, pt_usd, comm):
    day = b["day"].to_numpy()[tr.exit_i.to_numpy()] if len(tr) else np.array([], dtype=object)
    return pd.Series(tr.pts.to_numpy() * pt_usd - comm, index=day).groupby(level=0).sum().reindex(days, fill_value=0.0)


def run(b, mult, T, session, htf, slip, days, pt_usd=2.0, comm=1.0, htf_bars=None):
    O, H, L, C = (b[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    fu, fd, trend = cs.hma_supertrend(H, L, C, 14, mult, 14)
    mins = b["mins"].to_numpy()
    if session == "us":
        allow = (mins >= 9 * 60 + 31) & (mins <= 15 * 60 + 30)
        flat = mins == 16 * 60
    else:
        allow = np.ones(len(C), bool)
        flat = np.zeros(len(C), bool)
    if htf:
        hb = htf_bars
        _u, _d, ht = cs.hma_supertrend(*(hb[c].to_numpy(float) for c in ("high", "low", "close")), 14, 2.5, 14)
        j = np.searchsorted(hb.time.to_numpy(), b.time.to_numpy(), side="right") - 1
        hdir = np.where(j >= 0, ht[np.clip(j, 0, None)], 0)
        allow = allow & (hdir == trend)
    tr = simulate2(O, H, L, C, b["order"].to_numpy(), fu, fd, trend, T, slip, allow, flat)
    return tr, daily(tr, b, days, pt_usd, comm if slip else 0.0)


def stats(x: pd.Series):
    yrs = x.groupby(x.index.str[:4]).sum()
    return dict(mean=x.mean(), t=yb.tstat(x.to_numpy()), neg=int((yrs < 0).sum()), yrs=len(yrs),
                drop5=x.sum() - x.nlargest(5).sum())


def main() -> None:
    t0 = time.time()
    mnq = yb.load("MNQ", None)
    days = pd.Index(sorted(mnq.day.unique()))
    prim, hold = days[days <= yb.PRIMARY_END], days[days > yb.PRIMARY_END]
    print(f"MNQ minute store {len(mnq):,} bars {days[0]} .. {days[-1]}; primary {len(prim)} days, holdout {len(hold)}")
    bars = {k: resample(mnq, k) for k in (1, 3, 5, 15, 30, 60)}
    trials = [(k, m, T, s, False) for k in (1, 3, 5, 15, 30, 60) for m in (2.5, 3.5) for T in (100, None) for s in ("24h", "us")]
    trials += [(k, 2.5, T, s, True) for k in (1, 5) for T in (100, None) for s in ("24h", "us")]
    hdr = (f"{'trial':<30} {'tr/day':>6} {'win':>4} {'gross pt/tr':>11} {'net $/day':>9} {'t':>6} {'t gross':>7} {'-yrs':>5} "
           f"{'drop5 $':>9} | {'hold $/d':>8} {'t':>6}")
    print("\none micro, $2/pt; cost $1.00 a round trip + 1 tick per market fill\n" + hdr, flush=True)
    res = []
    for k, m, T, s, h in trials:
        b = bars[k]
        tr, dn = run(b, m, T, s, h, TICK, days, htf_bars=bars[60])
        _z, dz = run(b, m, T, s, h, 0.0, days, htf_bars=bars[60])
        xd = b["day"].to_numpy()[tr.exit_i.to_numpy()]
        trp = tr[xd <= yb.PRIMARY_END]
        sp, sh, sz = stats(dn[prim]), stats(dn[hold]), stats(dz[prim])
        name = f"{k}m x{m} {'T' + str(T) if T else 'band'} {s}{' +60m' if h else ''}"
        res.append((name, (k, m, T, s, h), sp, sh, sz))
        print(f"{name:<30} {len(trp) / len(prim):>6.1f} {(trp.pts > 0).mean():>4.0%} {trp.pts.mean():>11.2f} {sp['mean']:>9.1f} "
              f"{sp['t']:>6.2f} {sz['t']:>7.2f} {sp['neg']:>2}/{sp['yrs']:<2} {sp['drop5']:>9,.0f} | {sh['mean']:>8.1f} {sh['t']:>6.2f}"
              f"   ({time.time() - t0:.0f} s)", flush=True)
    alive = [r for r in res if r[2]["t"] >= 2.0 and r[2]["neg"] <= 1 and r[3]["mean"] > 0]
    print("\nsurvivors of the kill criteria (primary t >= 2, <= 1 losing year, holdout positive): "
          + (", ".join(f"{r[0]} (t {r[2]['t']:.2f}, bar 3.2)" for r in alive) if alive else "none"))
    top = sorted(res, key=lambda r: -r[2]["t"])[:3]
    print("best three by primary t: " + "; ".join(f"{r[0]} t {r[2]['t']:.2f}, holdout ${r[3]['mean']:+.1f}/day t {r[3]['t']:.2f}"
                                                  for r in top))
    try:
        mes = yb.load("MES", None)
        mdays = pd.Index(sorted(mes.day.unique()))
        mprim = mdays[mdays <= yb.PRIMARY_END]
        ratio = float(mes.close.median() / mnq.close.median())
        mb = {k: resample(mes, k) for k in {r[1][0] for r in top} | {60}}
        for name, (k, m, T, s, h), sp, _sh, _sz in top:
            Tm = max(1, round(T * ratio)) if T else None
            _t, dm = run(mb[k], m, Tm, s, h, TICK, mdays, pt_usd=5.0, htf_bars=mb[60])
            st = stats(dm[mprim])
            print(f"MES check {name}: ${st['mean']:+.1f}/day t {st['t']:.2f} (MNQ t {sp['t']:.2f}) - "
                  f"{'agrees' if (st['mean'] > 0) == (sp['mean'] > 0) else 'DISAGREES'}")
    except Exception as e:
        print(f"MES check not run: {e}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
