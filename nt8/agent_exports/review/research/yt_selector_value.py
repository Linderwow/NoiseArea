from __future__ import annotations

import argparse
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_rule_fit as fit

HORIZONS = (5, 15, 30, 60)
BRACKETS = ((25.0, 25.0), (50.0, 25.0), (25.0, 15.0))


def bracket(O, H, L, i0, side, tgt, stp, max_bars=240):
    entry = O[i0]
    t_px, s_px = entry + side * tgt, entry - side * stp
    for j in range(i0, min(len(O), i0 + max_bars)):
        hit_s = L[j] <= s_px if side > 0 else H[j] >= s_px
        hit_t = H[j] >= t_px if side > 0 else L[j] <= t_px
        if hit_s:
            return -stp
        if hit_t:
            return tgt
    return float("nan")


def his_style(O, H, L, C, i0, side, n, tgt=50.0, max_bars=240):
    entry = O[i0]
    t_px = entry + side * tgt
    for j in range(i0, min(len(O) - 1, i0 + max_bars)):
        if (H[j] >= t_px) if side > 0 else (L[j] <= t_px):
            return tgt
        if j >= n and ((side > 0 and C[j] < C[j - n:j].min()) or (side < 0 and C[j] > C[j - n:j].max())):
            return side * (O[j + 1] - entry)
    return float("nan")


def outcomes(O, H, L, C, i, side, n) -> dict:
    i0 = i + 1
    if i0 >= len(O):
        return {}
    out = {}
    for h in HORIZONS:
        if i0 + h - 1 < len(C):
            out[f"+{h}m"] = side * (C[i0 + h - 1] - O[i0])
    for tg, st in BRACKETS:
        out[f"T{tg:g}/S{st:g}"] = bracket(O, H, L, i0, side, tg, st)
    out["T50 or reverse"] = his_style(O, H, L, C, i0, side, n)
    return out


def day_t(groups: dict[str, list[float]]) -> tuple[float, float, int]:
    finite = [[x for x in v if np.isfinite(x)] for v in groups.values()] if groups else []
    per_day = [float(np.mean(v)) for v in finite if v]
    allv = [x for v in finite for x in v]
    if len(per_day) < 3:
        return (float(np.mean(per_day)) if per_day else float("nan")), float("nan"), len(allv)
    m, sd = np.mean(per_day), np.std(per_day, ddof=1)
    return float(m), float(m / sd * math.sqrt(len(per_day))) if sd else float("nan"), len(allv)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=8)
    ap.add_argument("--since", default="")
    args = ap.parse_args()
    n = args.n
    rng = random.Random(20261104)
    rows = [r for r in fit.load_trades() if not args.since or r["date"] >= args.since]
    signals, flats = fit.signal_and_flat(rows)
    res = {g: defaultdict(lambda: defaultdict(list)) for g in ("taken", "let go", "control")}
    counts = defaultdict(int)
    for day in sorted(set(signals) | set(flats)):
        bars = fit.day_bars(day)
        if bars is None or len(bars) < 100:
            continue
        O, H, L, C = (bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
        idx = {t.strftime("%H:%M"): k for k, t in enumerate(bars.index)}

        def fired(i, side):
            return i >= n and ((side == 1 and C[i] > C[i - n:i].max()) or (side == -1 and C[i] < C[i - n:i].min()))

        for lab, side in signals.get(day, {}).items():
            i = idx.get(lab)
            if i is None or i < n + 1:
                continue
            k = i if fired(i, side) else (i - 1 if fired(i - 1, side) else None)
            if k is None:
                continue
            for key, v in outcomes(O, H, L, C, i, side, n).items():
                res["taken"][key][day].append(v)
            counts["taken"] += 1
        flat_bars = []
        for lab, armed in flats.get(day, {}).items():
            i = idx.get(lab)
            if i is None or i < n:
                continue
            flat_bars.append(i)
            if fired(i, armed):
                for key, v in outcomes(O, H, L, C, i, armed, n).items():
                    res["let go"][key][day].append(v)
                counts["let go"] += 1
        for i in rng.sample(flat_bars, min(len(flat_bars), 20)):
            for key, v in outcomes(O, H, L, C, i, rng.choice((1, -1)), n).items():
                res["control"][key][day].append(v)
            counts["control"] += 1
    print(f"SELECTOR VALUE - trigger: close beyond the prior {n} closes; days {len(set(signals) | set(flats))}"
          f"{' since ' + args.since if args.since else ''}; events: taken {counts['taken']}, let go {counts['let go']}, "
          f"control {counts['control']}")
    assert counts["taken"] >= 20 and counts["let go"] >= 20, "too few events - the sample is broken"
    keys = [f"+{h}m" for h in HORIZONS] + [f"T{t:g}/S{s:g}" for t, s in BRACKETS] + ["T50 or reverse"]
    print("every mean is the mean of per-day means (days are the unit the t is computed on); the difference is taken minus\n"
          "let go on the days that have both")
    print(f"\n{'outcome (gross pts)':<18} {'TAKEN mean':>10} {'t':>6} {'n':>5}   {'LET GO mean':>11} {'t':>6} {'n':>5}   "
          f"{'difference':>10}   {'CONTROL':>8} {'t':>6}")
    for key in keys:
        mt, tt, nt = day_t(res["taken"][key])
        ml, tl, nl = day_t(res["let go"][key])
        mc, tc, _ = day_t(res["control"][key])
        diffs = []
        for d in res["taken"][key]:
            a = [x for x in res["taken"][key][d] if np.isfinite(x)]
            b = [x for x in res["let go"][key].get(d, []) if np.isfinite(x)]
            if a and b:
                diffs.append(float(np.mean(a) - np.mean(b)))
        td = (np.mean(diffs) / np.std(diffs, ddof=1) * math.sqrt(len(diffs))) if len(diffs) > 2 and np.std(diffs, ddof=1) else float("nan")
        md = float(np.mean(diffs)) if diffs else float("nan")
        print(f"{key:<18} {mt:>10.2f} {tt:>6.2f} {nt:>5}   {ml:>11.2f} {tl:>6.2f} {nl:>5}   {md:>+10.2f} (t {td:>5.2f}, "
              f"{len(diffs)} days)   {mc:>8.2f} {tc:>6.2f}")
    print("\nread: a positive difference with |t| >= 2.5 (several outcomes tried) means his selection beats the trigger; ~0 means the\n"
          "selection adds nothing. CONTROL must be ~0 (random direction) or the test is broken.")


if __name__ == "__main__":
    main()
