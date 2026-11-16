from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")
TAPE = Path(r"D:\nt_recorder\MNQ_12-26")
MINUTE_STORE = Path(r"D:\nt_ticks\minute\MNQ")
TICK = 0.25
sys.path.insert(0, str(Path(__file__).resolve().parent))
from trendline_port import swings, swing_bar


def load_bars() -> pd.DataFrame:
    parts = []
    for p in sorted(MINUTE_STORE.glob("*.parquet")):
        m = pd.read_parquet(p)
        m["t"] = pd.to_datetime(m["time"], utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        parts.append(m[["t", "open", "high", "low", "close", "volume"]])
    store = (pd.concat(parts).sort_values(["t", "volume"]).drop_duplicates("t", keep="last")) if parts else pd.DataFrame()
    recs = []
    for p in sorted(TAPE.glob("*_bars1minute.csv")):
        try:
            d = pd.read_csv(p, usecols=["time_et", "open", "high", "low", "close", "volume"])
        except Exception:
            continue
        d["t"] = pd.to_datetime(d.time_et)
        recs.append(d[["t", "open", "high", "low", "close", "volume"]])
    rec = pd.concat(recs).drop_duplicates("t") if recs else pd.DataFrame()
    bars = pd.concat([x for x in (store, rec) if len(x)]).sort_values("t").drop_duplicates("t", keep="last").set_index("t")
    bars = bars.astype(float)
    prev_close = bars["close"].shift(1)
    tr = pd.concat([bars["high"] - bars["low"], (bars["high"] - prev_close).abs(), (bars["low"] - prev_close).abs()], axis=1).max(axis=1)
    bars["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return bars


def pos_at(bars: pd.DataFrame, t: datetime) -> int:
    i = bars.index.get_indexer([pd.Timestamp(t)], method="pad")[0]
    return int(i)


def load_stops(since: str, until: str, copy: str) -> tuple[list[dict], dict]:
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    n_all = len(rows)
    stops = []
    for r in rows:
        if r["entry_ok"] not in ("yes", "fixed") or r["exit_ok"] not in ("yes", "fixed") or r["exit_kind"] != "stop":
            continue
        if not (since <= r["date"] <= until) or (copy and r.get("copy", "") != copy):
            continue
        if r["side"] not in ("long", "short") or not r["entry_bar"] or not r["exit_et"]:
            continue
        d = datetime.strptime(r["date"], "%Y-%m-%d")
        ts = datetime.strptime(r["date"] + " " + r["entry_bar"], "%Y-%m-%d %H:%M")
        t_in = datetime.strptime(r["date"] + " " + r["entry_et"], "%Y-%m-%d %H:%M:%S")
        t_out = datetime.strptime(r["date"] + " " + r["exit_et"], "%Y-%m-%d %H:%M:%S")
        if t_out < t_in:
            t_out += timedelta(days=1)
        stops.append({"date": r["date"], "side": r["side"], "sign": 1 if r["side"] == "long" else -1,
                      "entry": float(r["entry"]), "exit": float(r["exit"]), "pts": float(r["pts"]), "copy": r.get("copy", ""),
                      "video": r["video"], "ts": ts, "t_in": t_in, "t_out": t_out, "exit_sec": t_out.second,
                      "minutes": (t_out - t_in).total_seconds() / 60, "dist": -float(r["pts"])})
    meta = {"rows": n_all, "stops": len(stops)}
    return stops, meta


def copy_p1(copy: str) -> int | None:
    if "/" not in copy:
        return None
    try:
        return int(copy.split("/")[0])
    except ValueError:
        return None


def score(residuals: list[float]) -> dict:
    a = np.abs(np.array(residuals, dtype=float))
    return {"n": len(a), "tick": float((a <= TICK + 1e-9).mean()) if len(a) else 0.0,
            "pts2": float((a <= 2.0 + 1e-9).mean()) if len(a) else 0.0, "med": float(np.median(a)) if len(a) else math.nan}


def level_residual(s: dict, level: float) -> float:
    return (level - s["exit"]) if s["sign"] == 1 else (s["exit"] - level)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--until", default="2099-12-31")
    ap.add_argument("--copy", default="", help="only stops where the panel showed this copy (e.g. 200/1/-2)")
    args = ap.parse_args()
    stops, meta = load_stops(args.since, args.until, args.copy)
    print(f"ledger {meta['rows']} rows -> {meta['stops']} tape-verified stop exits ({args.since}..{args.until}"
          f"{', copy ' + args.copy if args.copy else ''})")
    if not stops:
        return
    bars = load_bars()
    print(f"bars: {len(bars):,} 1-min MNQ bars {bars.index[0]:%Y-%m-%d} .. {bars.index[-1]:%Y-%m-%d}")

    dist = np.array([s["dist"] for s in stops])
    mins = np.array([s["minutes"] for s in stops])
    print(f"\nstop distance (pts): min {dist.min():.2f}  p10 {np.percentile(dist, 10):.2f}  median {np.median(dist):.2f}  "
          f"p90 {np.percentile(dist, 90):.2f}  max {dist.max():.2f}")
    print(f"time in trade (min): min {mins.min():.1f}  p10 {np.percentile(mins, 10):.1f}  median {np.median(mins):.1f}  "
          f"p90 {np.percentile(mins, 90):.1f}  max {mins.max():.1f}")
    by_copy = defaultdict(list)
    for s in stops:
        by_copy[s["copy"] or "(unknown)"].append(s["dist"])
    print("distance by copy: " + "; ".join(f"{c}: n={len(v)} median {np.median(v):.1f} [{min(v):.1f}..{max(v):.1f}]"
                                           for c, v in sorted(by_copy.items(), key=lambda kv: -len(kv[1]))))
    sec_in = Counter(min(s["t_in"].second // 10 * 10, 50) for s in stops)
    sec_out = Counter(min(s["exit_sec"] // 10 * 10, 50) for s in stops)
    print("seconds past the minute, entries: " + " ".join(f"{k:02d}s:{sec_in[k]}" for k in range(0, 60, 10)))
    print("seconds past the minute, stop exits: " + " ".join(f"{k:02d}s:{sec_out[k]}" for k in range(0, 60, 10))
          + "   (entries sit at a bar open; stops at the same offset = evaluated on bar close, spread out = resting stop order)")

    ctx = []
    for s in stops:
        i = pos_at(bars, s["ts"])
        if i < 25 or abs((bars.index[i] - s["ts"]).total_seconds()) > 180:
            continue
        j_exit = pos_at(bars, s["t_out"].replace(second=0) + timedelta(minutes=1))
        j_fill = i + 1
        sig = bars.iloc[i]
        c = dict(s)
        c.update(i=i, j_exit=j_exit, sig_low=sig["low"], sig_high=sig["high"], sig_range=sig["high"] - sig["low"], atr=sig["atr14"],
                 fill_open=float(bars.iloc[j_fill]["open"]) if j_fill < len(bars) else math.nan)
        for n in (2, 3, 5, 10, 20):
            w = bars.iloc[i - n + 1:i + 1]
            c[f"lo{n}"], c[f"hi{n}"] = float(w["low"].min()), float(w["high"].max())
        run = bars.iloc[j_fill:max(j_fill + 1, j_exit)]
        c["run_high"], c["run_low"] = float(run["high"].max()), float(run["low"].min())
        c["run_close_high"], c["run_close_low"] = float(run["close"].max()), float(run["close"].min())
        c["bars_in"] = max(1, j_exit - j_fill)
        for lab, j in (("x0", j_exit), ("x1", j_exit - 1)):
            for n in (8, 10):
                prior = bars.iloc[j - n:j]["close"]
                cl = float(bars.iloc[j]["close"])
                c[f"opp_{lab}_{n}"] = (cl < prior.min()) if s["sign"] == 1 else (cl > prior.max())
        ctx.append(c)
    print(f"\n{len(ctx)} stops with bars around them (signal bar within 3 min of a bar label)")
    fill_err = np.abs(np.array([c["fill_open"] - c["entry"] for c in ctx if not math.isnan(c["fill_open"])]))
    print(f"sanity: |ledger entry - next bar's open| median {np.median(fill_err):.2f} pts, within 2 pts {np.mean(fill_err <= 2):.0%} "
          f"(fills are at the next open; MNQ-NQ basis adds up to ~0.5)")

    swing_level = {}
    days = sorted({c["ts"].strftime("%Y-%m-%d") for c in ctx})
    for day in days:
        d0 = datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)
        blk = bars.loc[d0: datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)]
        if len(blk) < 20:
            continue
        sh, sl = swings(list(blk["high"]), list(blk["low"]), 5)
        offset = bars.index.get_indexer([blk.index[0]])[0]
        for c in ctx:
            if c["ts"].strftime("%Y-%m-%d") != day:
                continue
            t = c["i"] - offset
            if t < 11:
                continue
            series = sl if c["sign"] == 1 else sh
            ago = swing_bar(series, t, 5, 400)
            if ago != -1:
                swing_level[id(c)] = series[t - ago]

    results = []

    def add(name, param, pairs):
        res = [r for r in pairs if r is not None and not math.isnan(r)]
        if res:
            results.append((name, param, score(res), res))

    for cp, lst in by_copy.items():
        mode = Counter(round(d / TICK) * TICK for d in lst).most_common(1)[0][0]
        add(f"fixed distance, copy {cp}", f"{mode:.2f} pts", [mode - c["dist"] for c in ctx if (c["copy"] or "(unknown)") == cp])
    mode_all = Counter(round(d / TICK) * TICK for d in dist).most_common(1)[0][0]
    add("fixed distance, all", f"{mode_all:.2f} pts", [mode_all - c["dist"] for c in ctx])
    add("signal bar low/high", "", [level_residual(c, c["sig_low"] if c["sign"] == 1 else c["sig_high"]) for c in ctx])
    for n in (2, 3, 5, 10, 20):
        add(f"prior {n}-bar extreme", "", [level_residual(c, c[f"lo{n}"] if c["sign"] == 1 else c[f"hi{n}"]) for c in ctx])
    add("last NT Swing(5)", "", [level_residual(c, swing_level[id(c)]) for c in ctx if id(c) in swing_level])
    best = None
    for k in np.arange(0.5, 8.01, 0.25):
        res = [c["dist"] - k * c["atr"] for c in ctx if c["atr"] > 0]
        sc = score(res)
        if best is None or (sc["pts2"], sc["tick"]) > (best[1]["pts2"], best[1]["tick"]):
            best = (k, sc, res)
    add("k x ATR(14)", f"k={best[0]:.2f}", best[2])
    best = None
    for k in np.arange(0.5, 8.01, 0.25):
        res = [c["dist"] - k * c["sig_range"] for c in ctx if c["sig_range"] > 0]
        sc = score(res)
        if best is None or (sc["pts2"], sc["tick"]) > (best[1]["pts2"], best[1]["tick"]):
            best = (k, sc, res)
    add("k x signal-bar range", f"k={best[0]:.2f}", best[2])
    for n in (3, 4, 5, 6, 8, 10, 12, 15, 20):
        best_var = None
        for back in (0, 1):
            res = []
            for c in ctx:
                j = c["j_exit"] - back
                prior = bars.iloc[j - n:j]["close"]
                level = float(prior.min()) if c["sign"] == 1 else float(prior.max())
                res.append(level_residual(c, level))
            sc = score(res)
            if best_var is None or (sc["pts2"], sc["tick"]) > (best_var[1]["pts2"], best_var[1]["tick"]):
                best_var = (back, sc, res)
        add(f"lowest/highest close of prior {n} bars", f"{'exit bar' if best_var[0] == 0 else '1 bar earlier'}", best_var[2])
    for lab, hk, lk in (("trail from running high/low", "run_high", "run_low"), ("trail from running close", "run_close_high", "run_close_low")):
        ks = [(c[hk] - c["exit"]) if c["sign"] == 1 else (c["exit"] - c[lk]) for c in ctx]
        mode = Counter(round(k / TICK) * TICK for k in ks).most_common(1)[0][0]
        add(lab, f"k={mode:.2f} pts (modal)", [mode - k for k in ks])
    for lab, f in (("3rd param = p1/10 ticks", lambda p: p / 10 * TICK), ("3rd param = p1/10 points", lambda p: p / 10),
                   ("3rd param = p1 ticks", lambda p: p * TICK), ("3rd param = p1 points", lambda p: float(p))):
        add(lab, "", [f(copy_p1(c["copy"])) - c["dist"] for c in ctx if copy_p1(c["copy"])])
    nb = Counter(c["bars_in"] for c in ctx)
    mode_bars = nb.most_common(1)[0][0]
    results.append((f"time stop", f"{mode_bars} bars (modal)", {"n": len(ctx), "tick": nb[mode_bars] / len(ctx), "pts2": nb[mode_bars] / len(ctx),
                                                                 "med": float(np.median([c['bars_in'] for c in ctx]))}, []))

    print(f"\n{'rule':<34} {'param':<22} {'n':>4} {'<=1 tick':>8} {'<=2 pts':>8} {'median |res|':>12}")
    for name, param, sc, _ in sorted(results, key=lambda r: (-r[2]["pts2"], -r[2]["tick"])):
        print(f"{name:<34} {param:<22} {sc['n']:>4} {sc['tick']:>8.0%} {sc['pts2']:>8.0%} {sc['med']:>12.2f}")

    print("\nstop = the opposite breakout (a close beyond the prior N closes the other way, filled at the next open)?")
    print("  window = the bar holding the exit time and the two before it (video clocks are only good to a minute);")
    print("  'breakout+fill' = such a close AND the ledger's exit within 2 pts of the following bar's open; base = how often any")
    print("  bar of those days closes beyond its prior N closes in a given direction (chance level for one bar)")
    days = sorted({c["ts"].strftime("%Y-%m-%d") for c in ctx})
    day_bars = {d: bars.loc[d] for d in days}
    print(f"  {'N':>3} {'exit bar':>9} {'1 before':>9} {'2 before':>9} {'any of 3':>9} {'breakout+fill':>14} {'base/bar':>9}")
    flip_detail = {}
    for n in (3, 4, 5, 6, 8, 10, 12, 15, 20):
        hits = [0, 0, 0]
        anyhit = bf = 0
        for c in ctx:
            j = c["j_exit"]
            got = False
            for k in (0, 1, 2):
                jj = j - k
                prior = bars.iloc[jj - n:jj]["close"]
                cl = float(bars.iloc[jj]["close"])
                b = (cl < prior.min()) if c["sign"] == 1 else (cl > prior.max())
                hits[k] += b
                if b and not got:
                    got = True
                    nxt_open = float(bars.iloc[jj + 1]["open"]) if jj + 1 < len(bars) else math.nan
                    ok = abs(nxt_open - c["exit"]) <= 2.0
                    bf += ok
                    if n == 8:
                        flip_detail[id(c)] = ("breakout+fill" if ok else "breakout, fill elsewhere")
            anyhit += got
        base = []
        for db in day_bars.values():
            cl = pd.Series(db["close"].values)
            if len(cl) > n + 1:
                lo, hi = cl.rolling(n).min().shift(1), cl.rolling(n).max().shift(1)
                base.append(float(((cl < lo) | (cl > hi)).mean()) / 2)
        print(f"  {n:>3} {hits[0] / len(ctx):>9.0%} {hits[1] / len(ctx):>9.0%} {hits[2] / len(ctx):>9.0%} {anyhit / len(ctx):>9.0%} "
              f"{bf / len(ctx):>14.0%} {np.mean(base):>9.1%}")
    d_open, d_close, d_level, d_low = [], [], [], []
    for c in ctx:
        j = c["j_exit"]
        for k in (0, 1, 2):
            jj = j - k
            prior = bars.iloc[jj - 8:jj]["close"]
            cl = float(bars.iloc[jj]["close"])
            if (cl < prior.min()) if c["sign"] == 1 else (cl > prior.max()):
                nxt_open = float(bars.iloc[jj + 1]["open"])
                level = float(prior.min()) if c["sign"] == 1 else float(prior.max())
                bar_ext = float(bars.iloc[jj]["low"]) if c["sign"] == 1 else float(bars.iloc[jj]["high"])
                d_open.append(level_residual(c, nxt_open)); d_close.append(level_residual(c, cl))
                d_level.append(level_residual(c, level)); d_low.append(level_residual(c, bar_ext))
                break
    for lab, a in (("next bar's open", d_open), ("breakout bar's close", d_close), ("lowest/highest close of the prior 8", d_level),
                   ("breakout bar's low/high", d_low)):
        a = np.array(a)
        print(f"  exit vs {lab:<36} n={len(a)}: within 1 tick {np.mean(np.abs(a) <= 0.25):.0%}, within 2 pts {np.mean(np.abs(a) <= 2):.0%}, "
              f"median signed {np.median(a):+.2f} (+ = exit beyond it)")
    rest = [c for c in ctx if id(c) not in flip_detail]
    print(f"\n  stops that are NOT an opposite breakout (N=8) within the window: {len(rest)} of {len(ctx)}")
    print(f"  their distance: median {np.median([c['dist'] for c in rest]):.1f} pts [{min(c['dist'] for c in rest):.1f}..{max(c['dist'] for c in rest):.1f}], "
          f"time in trade median {np.median([c['minutes'] for c in rest]):.1f} min")
    print(f"  {'date':<10} {'bar':<5} {'side':<5} {'copy':<10} {'dist':>6} {'min':>6}  video")
    for c in sorted(rest, key=lambda c: (c["date"], c["ts"])):
        print(f"  {c['date']:<10} {c['ts']:%H:%M} {c['side']:<5} {c['copy'] or '-':<10} {c['dist']:>6.2f} {c['minutes']:>6.1f}  {c['video']}")
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    nxt = {}
    byvid = defaultdict(list)
    for r in rows:
        if r["entry_ok"] in ("yes", "fixed") and r["side"] in ("long", "short") and r["entry_bar"]:
            byvid[(r["video"], r["date"])].append(r)
    flips = within = 0
    for c in ctx:
        later = [r for r in byvid[(c["video"], c["date"])]
                 if datetime.strptime(r["date"] + " " + r["entry_bar"], "%Y-%m-%d %H:%M") >= c["t_out"].replace(second=0)]
        if not later:
            continue
        later.sort(key=lambda r: r["entry_bar"])
        r = later[0]
        gap = (datetime.strptime(r["date"] + " " + r["entry_bar"], "%Y-%m-%d %H:%M") - c["t_out"].replace(second=0)).total_seconds() / 60
        if gap <= 2:
            within += 1
            flips += (r["side"] != c["side"])
    print(f"  next ledger trade within 2 bars of the stop: {within}/{len(ctx)}; of those opposite side: {flips}")

    level_rules = [r for r in results if r[3]]
    name, param, sc, res = sorted(level_rules, key=lambda r: (-r[2]["pts2"], -r[2]["tick"]))[0]
    a = np.array(res)
    print(f"\nbest: {name} {param}: residual (predicted level - actual exit, + = slipped beyond) "
          f"p10 {np.percentile(a, 10):.2f}  p25 {np.percentile(a, 25):.2f}  median {np.median(a):.2f}  p75 {np.percentile(a, 75):.2f}  p90 {np.percentile(a, 90):.2f}")
    edges = [-100, -20, -10, -5, -2, -1, -0.25, 0.25, 1, 2, 5, 10, 20, 100]
    h = np.histogram(a, bins=edges)[0]
    print("  histogram: " + "  ".join(f"[{edges[i]:g},{edges[i + 1]:g}):{h[i]}" for i in range(len(h)) if h[i]))
    print("\nread: a real rule shows most stops within a tick or two of its level; spread-out residuals mean the stop is not "
          "that level. Fitted k/N are in-sample (discovery), to be validated on later videos before use.")


if __name__ == "__main__":
    main()
