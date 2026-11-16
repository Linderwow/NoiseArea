from __future__ import annotations

import argparse
import glob
import math
import os
import time

import numpy as np
import pandas as pd

STORE = r"D:\nt_ticks\minute"
ET = "America/New_York"
TICK = 0.25
COMMISSION = 1.0
PT_USD = {"MNQ": 2.0, "MES": 5.0}
PRIMARY_END = "2024-12-31"


def load(sym: str, start: str | None) -> pd.DataFrame:
    files = sorted(glob.glob(os.path.join(STORE, sym, "*.parquet")), key=lambda p: (os.path.basename(p)[3:5], os.path.basename(p)[:2]))
    frames = []
    for order, f in enumerate(files):
        d = pd.read_parquet(f, columns=["time", "open", "high", "low", "close"])
        d["order"] = order
        frames.append(d)
    df = pd.concat(frames)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.sort_values(["time", "order"]).drop_duplicates("time", keep="last").reset_index(drop=True)
    if start:
        df = df[df.time >= pd.Timestamp(start, tz="UTC")].reset_index(drop=True)
    et = df.time.dt.tz_convert(ET)
    df["day"] = et.dt.strftime("%Y-%m-%d")
    df["hour"] = et.dt.hour
    return df


def breakout(close: np.ndarray, high: np.ndarray, low: np.ndarray, n: int, kind: str) -> np.ndarray:
    c = pd.Series(close)
    if kind == "closes":
        up, dn = c.shift(1).rolling(n).max().to_numpy(), c.shift(1).rolling(n).min().to_numpy()
    else:
        up, dn = pd.Series(high).shift(1).rolling(n).max().to_numpy(), pd.Series(low).shift(1).rolling(n).min().to_numpy()
    sig = np.zeros(len(close), dtype=np.int8)
    sig[close > up] = 1
    sig[close < dn] = -1
    return sig


def simulate(df: pd.DataFrame, sig: np.ndarray, target: float, stop: float | str | None, reverse: bool, lunch_off: bool,
             pt_usd: float, cost: bool = True) -> pd.DataFrame:
    O, H, L, C = (df[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    order = df["order"].to_numpy()
    hour = df["hour"].to_numpy()
    day = df["day"].to_numpy()
    slip = TICK if cost else 0.0
    comm = COMMISSION if cost else 0.0
    trail = stop == "trail8"
    fixed = None if (trail or stop is None) else float(stop)
    trades = []
    pos = 0
    entry = tgt = 0.0
    stp = None
    entry_i = 0
    n = len(O)

    def place(d: int, px: float, i: int):
        nonlocal pos, entry, tgt, stp, entry_i
        pos, entry, entry_i = d, px, i
        tgt = entry + d * target
        stp = entry - d * fixed if fixed is not None else None

    for i in range(1, n):
        if pos:
            d = pos
            exit_px = None
            kind = ""
            if trail and i >= 9:
                stp = C[i - 8:i].min() if d > 0 else C[i - 8:i].max()
            if order[i] != order[i - 1]:
                exit_px, kind = O[i] - d * slip, "roll"
            elif reverse and sig[i - 1] == -d:
                exit_px, kind = O[i] - d * slip, "reverse"
            else:
                hit_stop = stp is not None and ((L[i] <= stp) if d > 0 else (H[i] >= stp))
                hit_tgt = (H[i] >= tgt + TICK) if d > 0 else (L[i] <= tgt - TICK)
                if hit_stop:
                    exit_px = (min(stp, O[i]) if d > 0 else max(stp, O[i])) - d * slip
                    kind = "trail" if trail else "stop"
                elif hit_tgt:
                    exit_px, kind = tgt, "target"
            if exit_px is not None:
                pts = d * (exit_px - entry)
                trades.append((day[i], pts, pts * pt_usd - comm, kind, i - entry_i))
                pos = 0
                if kind in ("reverse", "trail") and not (lunch_off and 12 <= hour[i] < 15):
                    place(-d, (O[i] - d * slip) if kind == "reverse" else exit_px, i)
                continue
        if not pos and sig[i - 1] != 0 and order[i] == order[i - 1]:
            if lunch_off and 12 <= hour[i] < 15:
                continue
            d = int(sig[i - 1])
            place(d, O[i] + d * slip, i)
    return pd.DataFrame(trades, columns=["day", "pts", "net", "kind", "bars"])


def tstat(x: np.ndarray) -> float:
    return float(x.mean() / x.std(ddof=1) * math.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else float("nan")


def grade(tr: pd.DataFrame, days: pd.Index, label: str) -> dict:
    daily = tr.groupby("day")["net"].sum().reindex(days, fill_value=0.0)
    years = daily.groupby(daily.index.str[:4]).sum()
    best5 = tr["net"].nlargest(5).sum() if len(tr) else 0.0
    return {"label": label, "trades": len(tr), "net": daily.sum(), "per_day": daily.mean(), "t": tstat(daily.to_numpy()),
            "neg_years": int((years < 0).sum()), "years": len(years), "drop5": daily.sum() - best5,
            "win": float((tr.pts > 0).mean()) if len(tr) else float("nan"), "kinds": tr.kind.value_counts().to_dict()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    mnq = load("MNQ", "2024-01-01" if args.quick else None)
    print(f"MNQ minute store: {len(mnq):,} bars {mnq.day.iloc[0]} .. {mnq.day.iloc[-1]}, {mnq.order.nunique()} contracts "
          f"(loaded in {time.time() - t0:.0f} s)")
    assert len(mnq) > (100_000 if args.quick else 1_500_000), "far fewer bars than the store should hold"
    prim = mnq[mnq.day <= PRIMARY_END]
    hold = mnq[mnq.day > PRIMARY_END]
    days_p, days_h = pd.Index(sorted(prim.day.unique())), pd.Index(sorted(hold.day.unique()))
    entries = {"N8 closes": ("closes", 8), "N10 highs/lows": ("hl", 10)}
    results = []
    trial = 0
    for ename, (kind, n) in entries.items():
        sig = breakout(mnq.close.to_numpy(), mnq.high.to_numpy(), mnq.low.to_numpy(), n, kind)
        fire = float((sig != 0).mean())
        assert 0.005 < fire < 0.5, f"{ename} fires on {fire:.1%} of bars - signal is broken"
        print(f"{ename}: fires on {fire:.1%} of bars")
        for target in (25.0, 50.0):
            for stop_name, stop, rev in (("stop 25", 25.0, False), ("reverse", None, True), ("trail8", "trail8", False)):
                for lunch in (False, True):
                    trial += 1
                    label = f"{ename} | T{target:g} | {stop_name}{' | no 12-15 ET' if lunch else ''}"
                    tr = simulate(mnq, sig, target, stop, rev, lunch, PT_USD["MNQ"])
                    gp = grade(tr[tr.day <= PRIMARY_END], days_p, label)
                    gh = grade(tr[tr.day > PRIMARY_END], days_h, label)
                    z = grade(simulate(mnq, sig, target, stop, rev, lunch, PT_USD["MNQ"], cost=False)[lambda d: d.day <= PRIMARY_END],
                              days_p, label)
                    results.append((trial, label, gp, gh, z, tr))
                    print(f"  trial {trial:>2} {label:<48} primary: {gp['trades']:>6} trades net ${gp['net']:>10,.0f} "
                          f"t {gp['t']:>5.2f} neg-years {gp['neg_years']}/{gp['years']} drop5 ${gp['drop5']:>10,.0f} | "
                          f"zero-cost t {z['t']:>5.2f} | holdout: {gh['trades']:>5} trades ${gh['net']:>9,.0f} t {gh['t']:>5.2f} "
                          f"| win {gp['win']:.0%} ({time.time() - t0:.0f} s)", flush=True)
    print(f"\n{trial} trials; the 5 % bar at this multiplicity is t ~3.1 on the primary window.")
    alive = [r for r in results if r[2]["t"] >= 2.0 and r[2]["neg_years"] <= 1 and r[2]["net"] > 0]
    if not alive:
        print("VERDICT: every trial dies on the primary window (t < 2.0, or more than one losing year, or negative net at cost).")
        best = sorted(results, key=lambda r: -r[2]["t"])[:2]
        print("best two by t, for the record: " + "; ".join(f"{r[1]} t {r[2]['t']:.2f}" for r in best))
    else:
        print("survivors of the primary-window kill criteria: " + "; ".join(f"{r[1]} t {r[2]['t']:.2f}" for r in alive))
    best = sorted(results, key=lambda r: -r[2]["t"])[:2]
    try:
        mes = load("MES", "2024-01-01" if args.quick else None)
        ratio = float(mes.close.median() / mnq.close.median())
        print(f"\nMES replication ({len(mes):,} bars, target/stop scaled x{ratio:.3f}):")
        for _, label, gp, gh, _, _ in best:
            parts = label.split(" | ")
            kind, n = entries[parts[0]]
            target = float(parts[1][1:]) * ratio
            stop = 25.0 * ratio if parts[2].startswith("stop") else ("trail8" if parts[2] == "trail8" else None)
            rev = parts[2] == "reverse"
            lunch = "no 12-15" in label
            sig = breakout(mes.close.to_numpy(), mes.high.to_numpy(), mes.low.to_numpy(), n, kind)
            tr = simulate(mes, sig, target, stop, rev, lunch, PT_USD["MES"])
            gm = grade(tr[tr.day <= PRIMARY_END], pd.Index(sorted(mes[mes.day <= PRIMARY_END].day.unique())), label)
            agree = "agrees" if (gm["net"] > 0) == (gp["net"] > 0) else "DISAGREES in sign (fatal)"
            print(f"  {label:<48} MES primary: {gm['trades']:>6} trades net ${gm['net']:>9,.0f} t {gm['t']:>5.2f} - {agree} "
                  f"with MNQ (${gp['net']:,.0f}, t {gp['t']:.2f})")
    except Exception as e:
        print(f"MES replication not run: {e}")
    print(f"\ndone in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
