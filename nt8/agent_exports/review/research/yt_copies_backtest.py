from __future__ import annotations

import argparse
import math
import os
import time

import numpy as np
import pandas as pd

import yt_backtest as yb

TARGETS = (10, 20, 30, 40, 50, 60, 80, 100, 200, 500, 1000)
TICK = 0.25
LOOKBACK = 8
COMMISSION = 1.00


def signal(close: np.ndarray, n: int = LOOKBACK) -> np.ndarray:
    return yb.breakout(close, close, close, n, "closes")


def run(df: pd.DataFrame, sig: np.ndarray, target_ticks: int, opposite_only: bool = True) -> pd.DataFrame:
    O, H, L = (df[c].to_numpy(dtype=float) for c in ("open", "high", "low"))
    order = df["order"].to_numpy()
    tgt_pts = target_ticks * TICK
    out = []
    pos, last, entry, tgt, ei = 0, 0, 0.0, 0.0, 0

    def close_at(i: int, px: float, kind: str) -> None:
        out.append((ei, i, pos, entry, px, kind))

    for i in range(1, len(O)):
        s = int(sig[i - 1])
        if order[i] != order[i - 1]:
            if pos:
                close_at(i, O[i], "roll")
                pos = 0
            continue
        if pos and s == -pos:
            close_at(i, O[i], "reverse")
            pos, last, entry, ei = s, s, O[i], i
            tgt = entry + s * tgt_pts
        elif not pos and s != 0 and (not opposite_only or s != last):
            pos, last, entry, ei = s, s, O[i], i
            tgt = entry + s * tgt_pts
        if pos > 0 and H[i] >= tgt + TICK:
            close_at(i, max(tgt, O[i]), "target")
            pos = 0
        elif pos < 0 and L[i] <= tgt - TICK:
            close_at(i, min(tgt, O[i]), "target")
            pos = 0
    tr = pd.DataFrame(out, columns=["ei", "xi", "side", "entry", "exit", "kind"])
    tr["pts"] = tr.side * (tr.exit - tr.entry)
    market_fills = 1 + (tr.kind != "target").astype(int)
    tr["cost_pts"] = market_fills * TICK + COMMISSION / yb.PT_USD["MNQ"]
    return tr


def grade(tr: pd.DataFrame, df: pd.DataFrame, days: pd.Index, pt_usd: float) -> dict:
    day = df["day"].to_numpy()[tr.xi.to_numpy()] if len(tr) else np.array([], dtype=object)
    g = pd.Series(tr.pts.to_numpy() * pt_usd, index=day).groupby(level=0).sum().reindex(days, fill_value=0.0)
    n = pd.Series((tr.pts - tr.cost_pts).to_numpy() * pt_usd, index=day).groupby(level=0).sum().reindex(days, fill_value=0.0)
    years = n.groupby(n.index.str[:4]).sum()
    return {"trades": len(tr), "per_day": len(tr) / max(1, len(days)), "win": float((tr.pts > 0).mean()) if len(tr) else math.nan,
            "gpt": float(tr.pts.mean()) if len(tr) else math.nan, "cpt": float(tr.cost_pts.mean()) if len(tr) else math.nan,
            "gross_day": g.mean(), "t_gross": yb.tstat(g.to_numpy()), "net_day": n.mean(), "t_net": yb.tstat(n.to_numpy()),
            "net": n.sum(), "neg_years": int((years < 0).sum()), "years": len(years),
            "drop5": n.sum() - n.nlargest(5).sum()}


def trials() -> None:
    t0 = time.time()
    mnq = yb.load("MNQ", None)
    print(f"MNQ minute store: {len(mnq):,} bars {mnq.day.iloc[0]} .. {mnq.day.iloc[-1]}, {mnq.order.nunique()} contracts")
    sig = signal(mnq.close.to_numpy())
    print(f"8-close breakout fires on {(sig != 0).mean():.1%} of bars")
    prim_days = pd.Index(sorted(mnq.day[mnq.day <= yb.PRIMARY_END].unique()))
    hold_days = pd.Index(sorted(mnq.day[mnq.day > yb.PRIMARY_END].unique()))
    hdr = (f"{'copy':<11} {'trades/day':>10} {'win':>5} {'gross pts/tr':>12} {'cost pts/tr':>11} {'gross $/day':>11} {'t gross':>7} "
           f"{'net $/day':>9} {'t net':>6} {'neg yrs':>7} {'drop5 $':>9} | holdout: {'gross pts/tr':>12} {'net $/day':>9} {'t net':>6}")
    print("\nMNQ, one micro per copy ($2/pt); primary 2019-05 .. 2024-12, holdout 2025-01 .. today\n" + hdr)
    results = {}
    for T in TARGETS:
        tr = run(mnq, sig, T)
        day = mnq["day"].to_numpy()[tr.xi.to_numpy()]
        p = grade(tr[day <= yb.PRIMARY_END], mnq, prim_days, yb.PT_USD["MNQ"])
        h = grade(tr[day > yb.PRIMARY_END], mnq, hold_days, yb.PT_USD["MNQ"])
        results[T] = (p, h, tr)
        print(f"{f'{T}/1/-':<11} {p['per_day']:>10.1f} {p['win']:>5.0%} {p['gpt']:>12.2f} {p['cpt']:>11.2f} {p['gross_day']:>11.1f} "
              f"{p['t_gross']:>7.2f} {p['net_day']:>9.1f} {p['t_net']:>6.2f} {p['neg_years']:>3}/{p['years']:<3} {p['drop5']:>9,.0f} | "
              f"{'':9}{h['gpt']:>12.2f} {h['net_day']:>9.1f} {h['t_net']:>6.2f}   ({time.time() - t0:.0f} s)", flush=True)
    alive = [T for T, (p, h, _) in results.items() if p["t_net"] >= 3 and p["neg_years"] <= 1 and h["net_day"] > 0]
    print("\nPASS (net t >= 3, <= 1 losing year, holdout positive): " + (", ".join(f"{T} ticks" for T in alive) if alive else "none"))
    era = pd.Index(sorted(mnq.day[mnq.day >= "2026-03-18"].unique()))
    print("\nstream era 2026-03-18 .. today (the months his videos cover), gross $ per day at one micro / t:")
    print("  " + "  ".join(f"{T}: {grade(r[2][mnq['day'].to_numpy()[r[2].xi.to_numpy()] >= '2026-03-18'], mnq, era, 2.0)['gross_day']:+.1f}"
                           f"/{grade(r[2][mnq['day'].to_numpy()[r[2].xi.to_numpy()] >= '2026-03-18'], mnq, era, 2.0)['t_gross']:.2f}"
                           for T, r in results.items()))
    print("\nwithout the opposite-only filter (primary, net $/day / t): " + "  ".join(
        f"{T}: {grade((lambda tr: tr[mnq['day'].to_numpy()[tr.xi.to_numpy()] <= yb.PRIMARY_END])(run(mnq, sig, T, False)), mnq, prim_days, 2.0)['net_day']:+.1f}"
        for T in (10, 40, 100, 1000)))
    try:
        mes = yb.load("MES", None)
        msig = signal(mes.close.to_numpy())
        ratio = float(mes.close.median() / mnq.close.median())
        mdays = pd.Index(sorted(mes.day[mes.day <= yb.PRIMARY_END].unique()))
        best = sorted(results, key=lambda T: -results[T][0]["t_net"])[:2]
        for T in best:
            ticks = max(1, round(T * ratio))
            tr = run(mes, msig, ticks)
            tr = tr[mes["day"].to_numpy()[tr.xi.to_numpy()] <= yb.PRIMARY_END]
            m = grade(tr, mes, mdays, yb.PT_USD["MES"])
            print(f"MES check of the best by t ({T} ticks on MNQ = {ticks} ticks on MES): gross {m['gpt']:+.2f} pts a trade, "
                  f"net ${m['net_day']:+.1f}/day t {m['t_net']:.2f} (MNQ net t {results[T][0]['t_net']:.2f})")
    except Exception as e:
        print(f"MES check not run: {e}")
    print(f"\ndone in {time.time() - t0:.0f} s")


def window(start: str, end: str, contract: str, target: int) -> None:
    path = os.path.join(yb.STORE, "MNQ", f"{contract}.parquet")
    d = pd.read_parquet(path, columns=["time", "open", "high", "low", "close"])
    d["time"] = pd.to_datetime(d["time"], utc=True)
    d = d.sort_values("time").reset_index(drop=True)
    d["order"] = 0
    et = d.time.dt.tz_convert(yb.ET)
    d["day"] = et.dt.strftime("%Y-%m-%d")
    d = d[(d.day >= start) & (d.day <= end)].reset_index(drop=True)
    sig = signal(d.close.to_numpy())
    tr = run(d, sig, target)
    et = d.time.dt.tz_convert(yb.ET)
    print(f"MNQ {contract}, {start} .. {end} (ET days), {len(d):,} one-minute bars, target {target} ticks, no costs:")
    print(f"  {len(tr)} trades, {(tr.pts > 0).mean():.0%} winners, {tr.pts.sum():+.2f} pts = ${tr.pts.sum() * 2:+,.2f} at one micro; "
          f"exits: {tr.kind.value_counts().to_dict()}")
    print("  first 10 trades (entry bar = the bar whose open filled it; NinjaTrader stamps bars by their CLOSE, one minute later):")
    for r in tr.head(10).itertuples():
        print(f"    {et[r.ei]:%Y-%m-%d %H:%M} {'long ' if r.side > 0 else 'short'} {r.entry:.2f} -> {et[r.xi]:%H:%M} {r.exit:.2f} "
              f"{r.kind:<7} {r.pts:+.2f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", nargs=2, metavar=("START", "END"))
    ap.add_argument("--contract", default="12-26")
    ap.add_argument("--target", type=int, default=40)
    a = ap.parse_args()
    if a.window:
        window(a.window[0], a.window[1], a.contract, a.target)
    else:
        trials()


if __name__ == "__main__":
    main()
