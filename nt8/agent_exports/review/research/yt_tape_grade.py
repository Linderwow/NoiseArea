from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exit_pass as xp

LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")
PT_USD = 2.0
COSTS = {"$2.00 RT (1 tick/side + $1)": 2.0, "$3.35 RT (2.35 ticks/side + $1)": 3.35}


def tstat(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return float("nan")
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / sd * math.sqrt(n) if sd > 0 else float("nan")


def boot(xs: list[float], seed: int = 7) -> tuple[float, float]:
    rng = random.Random(seed)
    b = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(5000))
    return b[250], b[4750]


def grade_rows(rows: list[dict], since: str = "") -> tuple[list[dict], int, int]:
    vt = xp.video_targets(rows)
    lags = xp.video_lags(rows)
    cand = [r for r in rows if r.get("entry_ok") in ("yes", "fixed") and r.get("side") in ("long", "short")
            and r.get("exit_et") and r.get("entry") not in ("", None) and (not since or r["date"] >= since)]
    graded, no_tape = [], 0
    for r in cand:
        d = xp.derive(r, vt, xp.lag_for(r.get("video", ""), lags))
        if d is None:
            no_tape += 1
            continue
        price, kind, how = d
        side = 1 if r["side"] == "long" else -1
        pts = side * (price - float(r["entry"]))
        panel_pts = float(r["pts"]) if r.get("pts") not in ("", None) and r.get("exit_src") in ("panel", "frame", "grid") else None
        graded.append({"date": r["date"], "hour": int(r["entry_et"][:2]) if r.get("entry_et") else -1, "pts": pts,
                       "kind": "target" if kind.startswith("target") else kind, "panel_pts": panel_pts,
                       "video": r.get("video", ""), "copy": r.get("copy") or "not identified", "row": r})
    return graded, len(cand), no_tape


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    graded, n_cand, no_tape = grade_rows(rows, args.since)
    scope = f"since {args.since}" if args.since else "all months"
    print(f"TAPE-ONLY GRADE, {scope}: {len(graded)} closed trades with a tape-verified entry and a tape-derived exit "
          f"({n_cand} candidates, {no_tape} with no tape at the exit), {len({g['date'] for g in graded})} days "
          f"({min(g['date'] for g in graded) if graded else '-'} .. {max(g['date'] for g in graded) if graded else '-'})")
    if len(graded) < 20:
        print("too few trades to grade")
        return
    pts = [g["pts"] for g in graded]
    wins = [p for p in pts if p > 0]
    losses = [p for p in pts if p <= 0]
    lo, hi = boot(pts)
    print(f"per trade: win rate {len(wins) / len(pts):.0%}, avg win {statistics.mean(wins):.1f} pts, avg loss "
          f"{statistics.mean(losses):.1f} pts, expectancy {statistics.mean(pts):+.2f} pts gross (90 % bootstrap {lo:+.2f} .. "
          f"{hi:+.2f}); our round-trip cost is 1.00 pt ($2.00 per micro)")
    by_day = defaultdict(list)
    for g in graded:
        by_day[g["date"]].append(g["pts"])
    days = sorted(by_day)
    print(f"\n{'cost':<32} {'net $':>8} {'$/day':>7} {'t(daily)':>8} {'90% boot $/day':>17} {'drop-5-best $':>13} {'pos days':>8}")
    for name, cost in [("zero cost (gross)", 0.0)] + list(COSTS.items()):
        daily = [sum(p * PT_USD - cost for p in by_day[d]) for d in days]
        per_trade = sorted(p * PT_USD - cost for p in pts)
        net = sum(daily)
        blo, bhi = boot(daily)
        print(f"{name:<32} {net:>8,.0f} {net / len(days):>7,.1f} {tstat(daily):>8.2f} {blo:>8,.1f} .. {bhi:>6,.1f} "
              f"{net - sum(per_trade[-5:]):>13,.0f} {sum(x > 0 for x in daily):>4}/{len(days)}")
    print(f"trades/day {len(pts) / len(days):.1f}; the multiplicity bar for this whole research is t ~3 (see the verdict)")
    for title, key in (("by month", lambda g: g["date"][:7]), ("by exit kind", lambda g: g["kind"]),
                       ("by session", lambda g: "RTH 09:30-16:00" if 9 <= g["hour"] < 16 else "overnight"),
                       ("by copy on the panel", lambda g: g["copy"])):
        groups = defaultdict(list)
        for g in graded:
            groups[key(g)].append(g["pts"])
        print(f"\n{title:<18} {'trades':>6} {'win%':>5} {'pts/trade':>9} {'net $ at $2 RT':>14} {'t':>6}")
        for k in sorted(groups):
            xs = groups[k]
            print(f"{k:<18} {len(xs):>6} {sum(x > 0 for x in xs) / len(xs):>5.0%} {statistics.mean(xs):>+9.2f} "
                  f"{sum(x * PT_USD - 2.0 for x in xs):>14,.0f} {tstat(xs):>6.2f}")
    both = [g for g in graded if g["panel_pts"] is not None]
    if both:
        diffs = [g["pts"] - g["panel_pts"] for g in both]
        print(f"\ncheck: on the {len(both)} trades whose P&L the panel/frame/grid also gave, tape - panel: median "
              f"{statistics.median(diffs):+.2f} pt, mean {statistics.mean(diffs):+.2f} pt, |diff| <= 2 pt "
              f"{sum(abs(x) <= 2 for x in diffs) / len(diffs):.0%}; panel expectancy on the same trades "
              f"{statistics.mean(g['panel_pts'] for g in both):+.2f} pt vs tape {statistics.mean(g['pts'] for g in both):+.2f} pt")
    print("\nread this with: the copy on the panel only (one of his copies at a time); trades the video did not show closing are\n"
          "not graded; the days are the days a video was read. No OCR'd dollar figure enters these numbers.")


if __name__ == "__main__":
    main()
