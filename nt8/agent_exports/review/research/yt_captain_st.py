from __future__ import annotations

import csv
import math
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_backtest as yb
import yt_indicator_sweep as sw

TICK = 0.25
TARGETS = (10, 20, 30, 40, 50, 60, 80, 100, 200, 500, 1000)
LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")
PATHS = Path(r"D:\stream_capture\DaCaptainTrader_\exit_paths.csv")


def hma_supertrend(H, L, C, n=14, mult=2.5, p=14):
    pc = np.concatenate([[C[0]], C[:-1]])
    tr_ = np.maximum(H - L, np.maximum(np.abs(H - pc), np.abs(L - pc)))
    base = sw.hma(C, n)
    atr = sw.wilder(tr_, p)
    up, dn = base - mult * atr, base + mult * atr
    N = len(C)
    fu, fd = up.copy(), dn.copy()
    trend = np.ones(N, np.int8)
    for i in range(1, N):
        if trend[i - 1] == 1:
            fu[i] = up[i] if up[i] > fu[i - 1] else fu[i - 1]
            if C[i] < fu[i]:
                trend[i] = -1
                fd[i] = dn[i]
            else:
                trend[i] = 1
        else:
            fd[i] = dn[i] if dn[i] < fd[i - 1] else fd[i - 1]
            if C[i] > fd[i]:
                trend[i] = 1
                fu[i] = up[i]
            else:
                trend[i] = -1
    return fu, fd, trend


def simulate(O, H, L, C, order, fu, fd, trend, T, slip=TICK):
    N = len(C)
    out = []
    pos = 0
    entry = tgt = 0.0
    fill_i = sig_i = 0
    pending = 0
    for i in range(1, N):
        if order[i] != order[i - 1]:
            if pos:
                out.append((sig_i, fill_i, i, pos, entry, O[i] - pos * slip, "roll"))
                pos = 0
            pending = 0
            continue
        if pending:
            if pos == -pending:
                out.append((sig_i, fill_i, i, pos, entry, O[i] - pos * slip, "reverse"))
                pos = 0
            if pos == 0:
                pos = pending
                entry = O[i] + pos * slip
                tgt = entry + pos * T * TICK
                fill_i, sig_i = i, i - 1
            pending = 0
        if pos and i >= fill_i + 1:
            stp = fu[i - 1] if pos > 0 else fd[i - 1]
            hit_s = (L[i] <= stp) if pos > 0 else (H[i] >= stp)
            hit_t = (H[i] >= tgt + TICK) if pos > 0 else (L[i] <= tgt - TICK)
            if hit_s:
                px = (min(stp, O[i]) if pos > 0 else max(stp, O[i])) - pos * slip
                out.append((sig_i, fill_i, i, pos, entry, px, "stop"))
                pos = 0
            elif hit_t:
                px = max(tgt, O[i]) if pos > 0 else min(tgt, O[i])
                out.append((sig_i, fill_i, i, pos, entry, px, "target"))
                pos = 0
        if trend[i] != trend[i - 1] and (pos == 0 or pos == -trend[i]):
            pending = int(trend[i])
    tr = pd.DataFrame(out, columns=["sig_i", "fill_i", "exit_i", "side", "entry", "exit", "kind"])
    tr["pts"] = tr.side * (tr.exit - tr.entry)
    return tr


def match(since: str = "2026-10-01") -> None:
    d = sw.load_series("2026-02-15")
    O, H, L, C = (d[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    order = np.zeros(len(C), int)
    key = {(a, b): i for i, (a, b) in enumerate(zip(d.day, d.lab))}
    rows = [r for r in csv.DictReader(open(LEDGER, encoding="utf-8")) if r["date"] >= since and r["entry_ok"] in ("yes", "fixed")
            and r["side"] in ("long", "short") and r["entry_bar"] and (r["date"], r["entry_bar"]) in key]
    copies = {}
    if PATHS.exists():
        p = pd.read_csv(PATHS)
        for (vid, trd), g in p.groupby(["video", "trade"]):
            copies[(trd[:10], trd[11:16])] = int(g.copy_ticks.iloc[0])
    print(f"his tape-verified entries since {since}: {len(rows)}; panel copy known for {sum(1 for r in rows if (r['date'], r['entry_bar']) in copies)}")
    for p_ in (13, 14):
        fu, fd, trend = hma_supertrend(H, L, C, 14, 2.5, p_)
        flips = np.where(trend[1:] != trend[:-1])[0] + 1
        sides = trend[flips]
        ex = pm1 = 0
        for r in rows:
            i = key[(r["date"], r["entry_bar"])]
            s = 1 if r["side"] == "long" else -1
            j = np.searchsorted(flips, i)
            near = [flips[q] - i for q in (j - 1, j) if 0 <= q < len(flips) and sides[q] == s]
            if 0 in near:
                ex += 1
            if any(abs(v) <= 1 for v in near):
                pm1 += 1
        print(f"\nATR({p_}): his entries on our flip bar {ex}/{len(rows)} = {ex / len(rows):.0%}, within one bar {pm1 / len(rows):.0%}")
        agree = Counter()
        diffs = []
        by_t = defaultdict(list)
        for r in rows:
            T = copies.get((r["date"], r["entry_bar"]))
            if T:
                by_t[T].append(r)
        for T, rs in by_t.items():
            sim = simulate(O, H, L, C, order, fu, fd, trend, T, slip=0.0)
            by_sig = {int(s): q for s, q in zip(sim.sig_i, sim.itertuples())}
            for r in rs:
                i = key[(r["date"], r["entry_bar"])]
                q = by_sig.get(i) or by_sig.get(i - 1) or by_sig.get(i + 1)
                if q is None:
                    agree["no simulated entry within a bar"] += 1
                    continue
                his_kind = r["exit_kind"] or ""
                his_k = "target" if his_kind.startswith("target") else ("stop" if his_kind == "stop" else "other/unknown")
                agree[f"his {his_k} / ours {q.kind}"] += 1
                if r["exit"] and r["exit_ok"] in ("yes", "fixed"):
                    s = 1 if r["side"] == "long" else -1
                    diffs.append(s * (float(r["exit"]) - float(r["entry"])) - q.pts)
        print(f"  exits on the panel copy (simulated with that copy's target, no costs): {dict(agree)}")
        if diffs:
            dd = np.array(diffs)
            print(f"  points per trade, his (tape-verified exit) minus ours: median {np.median(dd):+.2f}, within 2 pts {np.mean(np.abs(dd) <= 2):.0%}, "
                  f"n {len(dd)}")


def grade(tr, df, days, pt_usd, comm):
    day = df["day"].to_numpy()[tr.exit_i.to_numpy()] if len(tr) else np.array([], dtype=object)
    gross = pd.Series(tr.pts.to_numpy() * pt_usd, index=day).groupby(level=0).sum().reindex(days, fill_value=0.0)
    net = pd.Series(tr.pts.to_numpy() * pt_usd - comm, index=day).groupby(level=0).sum().reindex(days, fill_value=0.0)
    yrs = net.groupby(net.index.str[:4]).sum()
    return {"n": len(tr), "per_day": len(tr) / max(1, len(days)), "win": float((tr.pts > 0).mean()) if len(tr) else math.nan,
            "pts": float(tr.pts.mean()) if len(tr) else math.nan, "gross_day": gross.mean(), "t_gross": yb.tstat(gross.to_numpy()),
            "net_day": net.mean(), "t_net": yb.tstat(net.to_numpy()), "neg_years": int((yrs < 0).sum()), "years": len(yrs),
            "drop5": net.sum() - net.nlargest(5).sum(), "kinds": tr.kind.value_counts().to_dict()}


def backtest() -> None:
    t0 = time.time()
    mnq = yb.load("MNQ", None)
    O, H, L, C = (mnq[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    order = mnq["order"].to_numpy()
    print(f"MNQ minute store {len(mnq):,} bars {mnq.day.iloc[0]} .. {mnq.day.iloc[-1]}")
    prim = pd.Index(sorted(mnq.day[mnq.day <= yb.PRIMARY_END].unique()))
    hold = pd.Index(sorted(mnq.day[mnq.day > yb.PRIMARY_END].unique()))
    era = pd.Index(sorted(mnq.day[mnq.day >= "2026-03-18"].unique()))
    hdr = (f"{'trial':<26} {'tr/day':>6} {'win':>4} {'gross pt/tr':>11} {'net $/day':>9} {'t net':>6} {'t gross':>7} {'neg yrs':>7} "
           f"{'drop5 $':>9} | holdout net $/day t | stream era net $/day t")
    print("\nMNQ, one micro per copy, $2/pt; cost = $1.00 a round trip + 1 tick per market fill\n" + hdr)
    results = []
    for p_ in (14, 13):
        fu, fd, trend = hma_supertrend(H, L, C, 14, 2.5, p_)
        print(f"  (ATR {p_}: band flips on {(trend[1:] != trend[:-1]).mean():.2%} of bars, {time.time() - t0:.0f} s)", flush=True)
        for T in TARGETS:
            tr = simulate(O, H, L, C, order, fu, fd, trend, T, slip=TICK)
            day = mnq["day"].to_numpy()[tr.exit_i.to_numpy()]
            g_p = grade(tr[day <= yb.PRIMARY_END], mnq, prim, 2.0, 1.0)
            g_h = grade(tr[day > yb.PRIMARY_END], mnq, hold, 2.0, 1.0)
            g_e = grade(tr[day >= "2026-03-18"], mnq, era, 2.0, 1.0)
            z = simulate(O, H, L, C, order, fu, fd, trend, T, slip=0.0)
            zd = mnq["day"].to_numpy()[z.exit_i.to_numpy()]
            g_z = grade(z[zd <= yb.PRIMARY_END], mnq, prim, 2.0, 0.0)
            name = f"T{T} ATR{p_}"
            results.append((name, g_p, g_h, g_e, g_z))
            print(f"{name:<26} {g_p['per_day']:>6.1f} {g_p['win']:>4.0%} {g_p['pts']:>11.2f} {g_p['net_day']:>9.1f} {g_p['t_net']:>6.2f} "
                  f"{g_z['t_gross']:>7.2f} {g_p['neg_years']:>3}/{g_p['years']:<3} {g_p['drop5']:>9,.0f} | {g_h['net_day']:>7.1f} {g_h['t_net']:>5.2f} | "
                  f"{g_e['net_day']:>7.1f} {g_e['t_net']:>5.2f}   ({time.time() - t0:.0f} s)", flush=True)
    alive = [r for r in results if r[1]["t_net"] >= 2.0 and r[1]["neg_years"] <= 1 and r[2]["net_day"] > 0]
    print("\nsurvivors of the kill criteria (primary t >= 2 at cost, <= 1 losing year, holdout positive): "
          + (", ".join(f"{r[0]} (t {r[1]['t_net']:.2f})" for r in alive) if alive else "none"))
    best = sorted(results, key=lambda r: -r[1]["t_net"])[:2]
    try:
        mes = yb.load("MES", None)
        Om, Hm, Lm, Cm = (mes[c].to_numpy(float) for c in ("open", "high", "low", "close"))
        ratio = float(mes.close.median() / mnq.close.median())
        mdays = pd.Index(sorted(mes.day[mes.day <= yb.PRIMARY_END].unique()))
        for name, g_p, *_ in best:
            T = int(name.split()[0][1:]); p_ = int(name.split()[1][3:])
            fu, fd, trend = hma_supertrend(Hm, Lm, Cm, 14, 2.5, p_)
            ticks = max(1, round(T * ratio))
            tr = simulate(Om, Hm, Lm, Cm, mes["order"].to_numpy(), fu, fd, trend, ticks, slip=TICK)
            tr = tr[mes["day"].to_numpy()[tr.exit_i.to_numpy()] <= yb.PRIMARY_END]
            gm = grade(tr, mes, mdays, 5.0, 1.0)
            print(f"MES check {name} (target {ticks} ticks): net ${gm['net_day']:+.1f}/day t {gm['t_net']:.2f} (MNQ t {g_p['t_net']:.2f}) - "
                  f"{'agrees' if (gm['net_day'] > 0) == (g_p['net_day'] > 0) else 'DISAGREES'}")
    except Exception as e:
        print(f"MES check not run: {e}")
    print(f"done in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "match"
    if mode == "match":
        match(sys.argv[2] if len(sys.argv) > 2 else "2026-10-01")
    elif mode == "backtest":
        backtest()
