from __future__ import annotations

import csv
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tape_seconds as tape

LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")
POINT_USD = 20.0
TARGETS_PTS = (2.5, 5.0, 10.0, 15.0, 25.0, 50.0, 75.0, 125.0, 250.0)
TOL = 0.5
BEFORE, AFTER = 20, 20
PROX = 10.0
DEFAULT_LAG = 16


def _t(date: str, hms: str) -> datetime | None:
    if not hms:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date} {hms}", fmt)
        except ValueError:
            pass
    return None


def copy_target(copy: str) -> float | None:
    m = re.match(r"\s*(\d{1,4})/1/", copy or "")
    return int(m.group(1)) / 4 if m else None


def _known(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("usd") not in ("", None) and r.get("exit") not in ("", None) and r.get("exit_et")
            and r.get("exit_ok") in ("yes", "fixed") and r.get("side") in ("long", "short") and r.get("exit_src", "panel") != "tape"]


def video_targets(rows: list[dict]) -> dict[str, float]:
    seen: dict[str, Counter] = defaultdict(Counter)
    for r in _known(rows):
        k = r.get("exit_kind", "")
        if k.startswith("target"):
            try:
                seen[r.get("video", "")][float(k[6:])] += 1
            except ValueError:
                pass
    return {v: c.most_common(1)[0][0] for v, c in seen.items()}


def video_lags(rows: list[dict]) -> dict[str, list[tuple[int, float]]]:
    out: dict[str, list[tuple[int, float]]] = defaultdict(list)
    for r in _known(rows):
        t = _t(r["date"], r["exit_et"])
        s = tape.seconds_around(t, 600, 600) if t else None
        if s is None or not len(s):
            continue
        p = float(r["exit"])
        hit = s[(s["lo"] <= p + TOL) & (s["hi"] >= p - TOL)]
        if len(hit):
            out[r.get("video", "")].append((id(r), min(((ts - t).total_seconds() for ts in hit.index), key=abs)))
    return out


def lag_for(video: str, lags: dict, exclude: int | None = None) -> float:
    vals = [o for rid, o in lags.get(video, []) if rid != exclude]
    return statistics.median(vals) if len(vals) >= 3 else DEFAULT_LAG


def _traded(price: float, t: datetime, before: int, after: int) -> bool | None:
    s = tape.seconds_around(t, before, after)
    if s is not None and len(s):
        return bool(((s["lo"] <= price + TOL) & (s["hi"] >= price - TOL)).any())
    bars = tape.minute_bars(t.strftime("%Y-%m-%d"))
    if bars is None or not len(bars):
        return None
    t0 = (t - timedelta(seconds=before)).replace(second=0)
    t1 = (t + timedelta(seconds=after)).replace(second=0) + timedelta(minutes=1)
    w = bars.loc[t0:t1]
    if not len(w):
        return None
    return bool(w["low"].min() - 0.25 <= price <= w["high"].max() + 0.25)


def flat_price(t: datetime, lag: float = DEFAULT_LAG) -> tuple[float, str] | None:
    s = tape.seconds_around(t + timedelta(seconds=lag), 10, 0)
    if s is not None and len(s) and s["last"].notna().any():
        return float(s["last"].dropna().iloc[-1]), f"last trade at flat +{lag:.0f} s"
    bars = tape.minute_bars(t.strftime("%Y-%m-%d"))
    if bars is None or not len(bars):
        return None
    label = (t + timedelta(seconds=10)).replace(second=0) + timedelta(minutes=1)
    if label not in bars.index:
        return None
    b = bars.loc[label]
    return float((b["high"] + b["low"]) / 2), "minute-bar midpoint (no tape that day)"


def derive(r: dict, vtargets: dict[str, float], lag: float = DEFAULT_LAG) -> tuple[float, str, str] | None:
    t = _t(r["date"], r.get("exit_et", ""))
    side = 1 if r.get("side") == "long" else -1 if r.get("side") == "short" else 0
    if t is None or not side or r.get("entry") in ("", None):
        return None
    entry = float(r["entry"])
    fp = flat_price(t, lag)
    if fp is None:
        return None
    target = vtargets.get(r.get("video", ""))
    how = "the video's target size"
    if target is None:
        target = copy_target(r.get("copy", ""))
        how = "the copy's parameters"
    hits = []
    for pts in ([target] if target else TARGETS_PTS):
        price = round(entry + side * pts, 2)
        hit = _traded(price, t, BEFORE, AFTER)
        if hit is None:
            return None
        if hit and abs(fp[0] - price) <= PROX:
            hits.append(pts)
    if target and hits:
        return round(entry + side * target, 2), f"target{target:g}", f"target from {how}"
    if not target and len(hits) == 1:
        return round(entry + side * hits[0], 2), f"target{hits[0]:g}", "target: the only size that traded"
    pts = side * (fp[0] - entry)
    return round(fp[0], 2), ("stop" if pts < 0 else "other"), fp[1]


def fill(rows: list[dict]) -> list[dict]:
    vt = video_targets(rows)
    lags = video_lags(rows)
    for r in rows:
        r.setdefault("exit_src", "")
        if r.get("usd") not in ("", None):
            if not r["exit_src"]:
                r["exit_src"] = "grid" if "P&L from the grid row" in r.get("note", "") else "panel"
            continue
        if not r.get("exit_et"):
            continue
        d = derive(r, vt, lag_for(r.get("video", ""), lags))
        if d is None:
            continue
        price, kind, how = d
        side = 1 if r["side"] == "long" else -1
        pts = round(side * (price - float(r["entry"])), 2)
        r["exit"], r["pts"], r["usd"], r["exit_kind"], r["exit_src"] = price, pts, round(pts * POINT_USD, 2), kind, "tape"
        r["note"] = "; ".join(x for x in (r.get("note", ""), f"exit from the tape: {how}") if x)
    return rows


def validate(rows: list[dict]) -> None:
    vt = video_targets(rows)
    lags = video_lags(rows)
    known = _known(rows)
    targets = [r for r in known if r["exit_kind"].startswith("target")]
    stops = [r for r in known if r["exit_kind"] in ("stop", "other")]
    print(f"validation on {len(known)} panel-read, tape-confirmed exits ({len(targets)} targets, {len(stops)} stops/other)")
    rep = miss = none = 0
    misses = []
    for r in targets:
        d = derive(r, vt, lag_for(r.get("video", ""), lags, id(r)))
        if d is None:
            none += 1
        elif d[1] == r["exit_kind"] and abs(d[0] - float(r["exit"])) <= 0.3:
            rep += 1
        else:
            miss += 1
            misses.append(f"{r['video']} {r['date']} {r['exit_et']}: panel {r['exit_kind']} @{float(r['exit']):.2f} -> {d[1]} @{d[0]:.2f}")
    print(f"targets reproduced (same size, same price within a tick): {rep}/{len(targets)}; missed {miss}, no data {none}")
    for m in misses[:6]:
        print("   " + m)
    false_t = [(r, d) for r in stops if (d := derive(r, vt, lag_for(r.get("video", ""), lags, id(r)))) and d[1].startswith("target")]
    close = sum(1 for r, d in false_t if abs(float(r["pts"]) - float(d[1][6:])) <= 2.5)
    print(f"stops/other the method calls a target: {len(false_t)}/{len(stops)}; of these {close} are within 2.5 pts of that "
          f"target anyway (his target fills land a few ticks off the exact size)")
    for group, sel in (("tape seconds", lambda t: tape.seconds_around(t, 16, 0) is not None),
                       ("minute bars", lambda t: tape.seconds_around(t, 16, 0) is None)):
        rows_g = [r for r in known if sel(_t(r["date"], r["exit_et"]))]
        errs, worst = [], []
        for r in rows_g:
            d = derive(r, vt, lag_for(r.get("video", ""), lags, id(r)))
            if d is None:
                continue
            e = abs(d[0] - float(r["exit"]))
            errs.append(e)
            worst.append((e, f"{r['video']} {r['date']} {r['exit_et']} {e:.0f} pt"))
        if not errs:
            continue
        n = len(errs)
        w = lambda x: sum(e <= x for e in errs) / n
        print(f"P&L error of the derived exit vs the panel's, {group} ({n} exits): within 1 pt {w(1):.0%}, 2 pt {w(2):.0%}, "
              f"5 pt {w(5):.0%}, 10 pt {w(10):.0%}; median {statistics.median(errs):.1f} pt, mean {statistics.mean(errs):.1f} pt")
        print("   worst: " + "; ".join(s for _, s in sorted(worst, reverse=True)[:3]))
    sgn = [(1 if r["side"] == "long" else -1) * (d[0] - float(r["exit"])) for r in known
           if (d := derive(r, vt, lag_for(r.get("video", ""), lags, id(r)))) is not None]
    if sgn:
        print(f"bias (derived - panel, in the trade's favour +): median {statistics.median(sgn):+.2f} pt, mean {statistics.mean(sgn):+.2f} pt")


def main() -> None:
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    validate(rows)
    todo = [r for r in rows if r.get("exit_et") and r.get("usd") in ("", None)]
    print(f"\nrows with an exit time and no P&L: {len(todo)}")
    fill(rows)
    print("exit_src after the pass:", dict(Counter(r.get("exit_src", "") for r in rows)))
    filled = [r for r in rows if r.get("exit_src") == "tape"]
    print(f"filled from the tape: {len(filled)} ({dict(Counter(r['exit_kind'] for r in filled))}); "
          f"still without P&L: {sum(1 for r in todo if r.get('usd') in ('', None))}")


if __name__ == "__main__":
    main()
