r"""What separates the breakouts he took from the ones he let go? For one trigger rule (default: close breaks the prior
8 closes, in the armed direction), list every signal bar and every armed-direction false alarm with context: minutes since
the previous exit and how it ended, the breakout size beyond the channel, bar body, ATR, close position in range, hour,
distance from EMA(20/50) and from the previous exit price. Prints the two groups' distributions side by side."""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_rule_fit as fit  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8


def main() -> None:
    rows = fit.load_trades()
    signals, flats = fit.signal_and_flat(rows)
    # previous exit per day: (exit time, kind, side, exit price) for the "minutes since exit" context
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    taken, missed = [], []
    for day in sorted(set(signals) | set(flats)):
        bars = fit.day_bars(day)
        if bars is None:
            continue
        O, H, L, C = (bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
        labels = [t.strftime("%H:%M") for t in bars.index]
        idx = {lab: i for i, lab in enumerate(labels)}
        e20, e50 = fit.ema(C, 20), fit.ema(C, 50)
        tr = np.maximum(H[1:] - L[1:], np.maximum(abs(H[1:] - C[:-1]), abs(L[1:] - C[:-1])))
        A = np.concatenate([[tr[0]], fit.ema(tr, 14)])
        exits = sorted([(r["exit_et"], r["exit_kind"], r["side"], r.get("exit")) for r in by_day[day]
                        if r.get("exit_ok") == "yes" and r.get("exit_et")])

        def context(i, side):
            lab = labels[i]
            prev = [e for e in exits if e[0] < lab + ":00"]
            if prev:
                ex_t, ex_kind, ex_side, ex_px = prev[-1]
                mins = (datetime.strptime(lab, "%H:%M") - datetime.strptime(ex_t[:5], "%H:%M")).total_seconds() / 60
                ex_kind = "target" if ex_kind.startswith("target") else ex_kind
                dist_exit = (C[i] - float(ex_px)) * side if ex_px not in (None, "") else np.nan
            else:
                mins, ex_kind, dist_exit = np.nan, "-", np.nan
            chan = C[i - N:i].max() if side == 1 else C[i - N:i].min()
            return {"day": day, "bar": lab, "side": side, "mins_since_exit": mins, "prev_exit": ex_kind,
                    "beyond_channel": (C[i] - chan) * side, "body": (C[i] - O[i]) * side, "range": H[i] - L[i], "atr": A[i],
                    "pos_in_range": (C[i] - L[i]) / max(H[i] - L[i], 1e-9), "hour": int(lab[:2]),
                    "vs_ema20": (C[i] - e20[i]) * side, "vs_ema50": (C[i] - e50[i]) * side, "dist_from_exit": dist_exit}

        for lab, side in signals.get(day, {}).items():
            i = idx.get(lab)
            if i is None or i < N:
                continue
            fired = (C[i] > C[i - N:i].max() and side == 1) or (C[i] < C[i - N:i].min() and side == -1)
            prev_fired = i >= N + 1 and ((C[i - 1] > C[i - N - 1:i - 1].max() and side == 1) or (C[i - 1] < C[i - N - 1:i - 1].min() and side == -1))
            if fired or prev_fired:
                taken.append(context(i if fired else i - 1, side))
            else:
                taken.append({**context(i, side), "note": "rule did not fire"})
        for lab, armed in flats.get(day, {}).items():
            i = idx.get(lab)
            if i is None or i < N:
                continue
            if (armed == 1 and C[i] > C[i - N:i].max()) or (armed == -1 and C[i] < C[i - N:i].min()):
                missed.append(context(i, armed))

    def dist(name, key, fmt="{:.1f}"):
        a = np.array([t[key] for t in taken if key in t and not (isinstance(t[key], float) and np.isnan(t[key]))], dtype=float)
        b = np.array([t[key] for t in missed if key in t and not (isinstance(t[key], float) and np.isnan(t[key]))], dtype=float)
        if not len(a) or not len(b):
            return
        q = lambda x: " ".join(fmt.format(v) for v in np.percentile(x, [10, 25, 50, 75, 90]))
        print(f"{name:<28} taken n={len(a):<3} p10/25/50/75/90: {q(a)}")
        print(f"{'':<28} let go n={len(b):<3} p10/25/50/75/90: {q(b)}")

    print(f"trigger: close breaks prior {N} closes in the armed direction. taken={len(taken)} (incl. {sum(1 for t in taken if t.get('note'))} "
          f"where the rule did not fire), let go={len(missed)}\n")
    for name, key in (("minutes since last exit", "mins_since_exit"), ("pts beyond the channel", "beyond_channel"), ("bar body (pts, signed)", "body"),
                      ("bar range (pts)", "range"), ("ATR(14)", "atr"), ("close position in range", "pos_in_range"), ("hour (ET)", "hour"),
                      ("close vs EMA(20), signed", "vs_ema20"), ("close vs EMA(50), signed", "vs_ema50"), ("pts from last exit price, signed", "dist_from_exit")):
        dist(name, key)
    from collections import Counter
    print("\nprevious exit kind  taken:", dict(Counter(t["prev_exit"] for t in taken)), " let go:", dict(Counter(t["prev_exit"] for t in missed)))
    print("\nlet-go breakouts by minutes since exit (<=2, 3-5, 6-15, >15):",
          [sum(1 for t in missed if lo <= t["mins_since_exit"] <= hi) for lo, hi in ((0, 2), (3, 5), (6, 15), (16, 9999))])
    print("taken breakouts by minutes since exit (<=2, 3-5, 6-15, >15):   ",
          [sum(1 for t in taken if lo <= (t["mins_since_exit"] if not np.isnan(t["mins_since_exit"]) else -1) <= hi) for lo, hi in ((0, 2), (3, 5), (6, 15), (16, 9999))])


if __name__ == "__main__":
    main()
