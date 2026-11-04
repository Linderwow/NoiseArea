r"""Da Captain Trader's trade ledger: one row per round trip of the Chart Trader account (his "200/1/-20" copy, 1 NQ),
built from the capture's events (D:\stream_capture\DaCaptainTrader_\<date>\events.jsonl) plus the trades seen by eye before
the capture started (manual_trades.csv next to the day folders).

Times: the capture stamps the PC clock when a frame was read; the stream runs ~22 s behind the market (yt_delay.py), so
market_et = recv_utc - LAG, in Eastern time. Entry fills are at a bar's open, so entry_bar = that minute.
Exit kind from the realized P&L step: +$1,000 = the 200-tick (50-pt) target; a negative step = a stop; exit price = entry +/- pts.

Usage: python stream_trades.py [--lag 22]  -> writes D:\stream_capture\DaCaptainTrader_\trades.csv and prints it.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(r"D:\stream_capture\DaCaptainTrader_")
POINT_USD = 20.0   # NQ
TARGET_PTS = 50.0  # 200 ticks: the copy the Chart Trader panel showed on 11-03/11-04
# the panel follows whichever account he has selected; its copy's target can be any of the eight (ticks 10..500)
TARGETS_PTS = (2.5, 5.0, 10.0, 15.0, 25.0, 50.0, 75.0, 125.0)


def exit_kind_of(pts: float) -> str:
    for t in TARGETS_PTS:
        if abs(pts - t) <= 0.3:
            return f"target{t:g}"
    return "stop" if pts < 0 else "other"


def to_et(utc_str: str, lag: float) -> datetime:
    t = datetime.strptime(utc_str[:23], "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc) - timedelta(seconds=lag)
    # Eastern offset: EST -5 (DST handled by the PC clock elsewhere; this project runs in EST/EDT by date)
    y = t.year
    # second Sunday of March .. first Sunday of November, 2 AM local
    mar = datetime(y, 3, 8, 7, tzinfo=timezone.utc)
    while mar.weekday() != 6:
        mar += timedelta(days=1)
    nov = datetime(y, 11, 1, 6, tzinfo=timezone.utc)
    while nov.weekday() != 6:
        nov += timedelta(days=1)
    off = -4 if mar <= t < nov else -5
    return (t + timedelta(hours=off)).replace(tzinfo=None)


def captured_trades(lag: float) -> list[dict]:
    trades = []
    for day_dir in sorted(BASE.glob("20??-??-??")) + sorted((BASE / "vod").glob("*")):
        ev_path = day_dir / "events.jsonl"
        if not ev_path.exists():
            continue
        open_trade = None
        last_realized = None  # the P&L line is only visible while flat; remember it across the trade
        first_event = True  # a position seen on the very first frame of a video was entered before it started
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            k = e.get("kind")
            if k in ("flat", "exit", "realized_change", "grid_change"):
                first_event = False
            if k == "flat" and e.get("realized") is not None:
                last_realized = e["realized"]
            elif k == "realized_change" and e.get("after") is not None:
                last_realized = e["after"]
            if k == "entry":
                avg = e.get("avg")
                if open_trade and open_trade["entry"] == avg:
                    if open_trade["side"] == "?" and e.get("side") in ("long", "short"):
                        open_trade["side"] = e["side"]
                    continue  # same trade re-read
                if open_trade and open_trade["entry"] != avg:
                    open_trade["note"] = "exit not seen"
                    trades.append(open_trade)
                side = e.get("side") or "?"
                if side == "?" and e.get("grid"):
                    sides = {r.get("side") for r in e["grid"] if r.get("params", "").startswith("200")}
                    side = sides.pop() if len(sides) == 1 and sides != {"flat"} else "?"
                t = to_et(e["recv_utc"], lag if day_dir.parent.name != "vod" else 0.0)
                video = day_dir.name if day_dir.parent.name == "vod" else "live"
                open_trade = {"date": t.strftime("%Y-%m-%d"), "entry_et": t.strftime("%H:%M:%S"),
                              "entry_bar": t.replace(second=0).strftime("%H:%M"), "side": side, "entry": avg,
                              "exit_et": "", "exit": "", "exit_kind": "", "pts": "", "usd": "", "source": "capture", "note": "",
                              "video": video, "first_event": first_event}
                first_event = False
            elif k == "exit" and open_trade:
                before, after = e.get("realized_before"), e.get("realized")
                if before is None:
                    before = last_realized
                if after is not None:
                    last_realized = after
                t = to_et(e["recv_utc"], lag if day_dir.parent.name != "vod" else 0.0)
                open_trade["exit_et"] = t.strftime("%H:%M:%S")
                if before is not None and after is not None:
                    usd = after - before
                    pts = usd / POINT_USD
                    sign = 1 if open_trade["side"] == "long" else -1 if open_trade["side"] == "short" else 0
                    open_trade["usd"] = round(usd, 2)
                    open_trade["pts"] = round(pts, 2)
                    open_trade["exit_kind"] = exit_kind_of(pts)
                    if sign and open_trade["entry"] is not None:
                        open_trade["exit"] = round(open_trade["entry"] + sign * pts, 2)
                else:
                    open_trade["note"] = "realized not read"
                trades.append(open_trade)
                open_trade = None
        if open_trade:
            open_trade["note"] = "still open at end of file"
            trades.append(open_trade)
    return trades


def manual_trades() -> list[dict]:
    p = BASE / "manual_trades.csv"
    if not p.exists():
        return []
    rows = []
    for r in csv.DictReader(open(p, encoding="utf-8")):
        r.setdefault("source", "screenshot")
        for k in ("entry", "exit"):
            r[k] = float(r[k]) if r.get(k) else ""
        if r["entry"] != "" and r["exit"] != "" and r.get("side") in ("long", "short"):
            sign = 1 if r["side"] == "long" else -1
            r["pts"] = round(sign * (r["exit"] - r["entry"]), 2)
            r["usd"] = round(r["pts"] * POINT_USD, 2)
            r["exit_kind"] = exit_kind_of(r["pts"])
        rows.append({"date": r["date"], "entry_et": r.get("entry_et", ""), "entry_bar": r.get("entry_bar", ""), "side": r["side"],
                     "entry": r["entry"], "exit_et": r.get("exit_et", ""), "exit": r["exit"], "exit_kind": r.get("exit_kind", ""),
                     "pts": r.get("pts", ""), "usd": r.get("usd", ""), "source": r["source"], "note": r.get("note", ""),
                     "video": "screenshot", "first_event": False})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lag", type=float, default=22.0)
    args = ap.parse_args()
    rows = manual_trades() + captured_trades(args.lag)
    seen = set()
    out = []
    for r in rows:  # manual first: the capture may have re-seen the same trade (same day, side, entry)
        key = (r["date"], r["side"], r["entry"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: (r["date"], r["entry_bar"] or r["entry_et"]))
    try:  # triangulate against our tape: repair one-digit OCR misreads, mark flats that follow a capture gap
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import ledger_check
        state_files = sorted(BASE.glob("20??-??-??/state.csv")) + sorted((BASE / "vod").glob("*/state.csv"))
        out = ledger_check.check_trades(out, ledger_check.capture_gaps(state_files))
    except Exception as e:  # noqa: BLE001
        print("tape check skipped:", e)
    cols = ["date", "entry_bar", "entry_et", "side", "entry", "exit_et", "exit", "exit_kind", "pts", "usd", "source", "note",
            "video", "entry_ok", "exit_ok", "check"]
    for r in out:
        r.pop("first_event", None)
    with open(BASE / "trades.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    print(f"{len(out)} trades -> {BASE / 'trades.csv'}")
    print(f"{'date':<10} {'bar':<5} {'side':<5} {'entry':>9} {'exit':>9} {'kind':<6} {'pts':>6} {'usd':>7}  source")
    for r in out:
        print(f"{r['date']:<10} {r['entry_bar']:<5} {r['side']:<5} {r['entry']!s:>9} {r['exit']!s:>9} {r['exit_kind']:<6} {r['pts']!s:>6} {r['usd']!s:>7}  {r['source']} {r['note']}")
    done = [r for r in out if r["usd"] != ""]
    if done:
        from collections import Counter
        kinds = Counter(r["exit_kind"] for r in done)
        print(f"\nclosed {len(done)}: net ${sum(float(r['usd']) for r in done):,.0f}; exits: "
              + ", ".join(f"{k} {n}" for k, n in sorted(kinds.items())))


if __name__ == "__main__":
    main()
