from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

BASE = Path(r"D:\stream_capture\DaCaptainTrader_")
LEDGER = BASE / "trades.csv"


def utc_to_et(t: datetime) -> datetime:
    y = t.year
    mar = datetime(y, 3, 8, 7)
    while mar.weekday() != 6:
        mar += timedelta(days=1)
    nov = datetime(y, 11, 1, 6)
    while nov.weekday() != 6:
        nov += timedelta(days=1)
    return t + timedelta(hours=-4 if mar <= t < nov else -5)


def session_of(t: datetime) -> date:
    return (t + timedelta(days=1)).date() if t.hour >= 18 else t.date()


def load_trades(since: str, until: str) -> list[dict]:
    out = []
    for r in csv.DictReader(open(LEDGER, encoding="utf-8")):
        if r["entry_ok"] not in ("yes", "fixed") or r["side"] not in ("long", "short") or not r["entry_bar"]:
            continue
        if not (since <= r["date"] <= until):
            continue
        t_in = datetime.strptime(r["date"] + " " + (r["entry_et"] or r["entry_bar"] + ":00"), "%Y-%m-%d %H:%M:%S")
        t_out = None
        if r["exit_et"]:
            t_out = datetime.strptime(r["date"] + " " + r["exit_et"], "%Y-%m-%d %H:%M:%S")
            if t_out < t_in - timedelta(minutes=5):
                t_out += timedelta(days=1)
        out.append({"date": r["date"], "session": session_of(t_in), "t_in": t_in, "t_out": t_out, "side": r["side"],
                    "usd": float(r["usd"]) if r["usd"] != "" else None, "pts": float(r["pts"]) if r["pts"] != "" else None,
                    "kind": r["exit_kind"], "video": r["video"], "copy": r.get("copy", "")})
    out.sort(key=lambda t: t["t_in"])
    return out


def coverage_windows() -> list[tuple[datetime, datetime]]:
    win = []
    for s in (BASE / "vod").glob("*/status.json"):
        try:
            d = json.load(open(s, encoding="utf-8"))
            start = d.get("stream", {}).get("start_utc", "")
            pos = d.get("video_pos") or 0
            if start and pos:
                t0 = utc_to_et(datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None))
                win.append((t0, t0 + timedelta(seconds=pos)))
        except Exception:
            pass
    for st in BASE.glob("20??-??-??/state.csv"):
        try:
            lines = st.read_text(encoding="utf-8").splitlines()
            if len(lines) > 2:
                t0 = utc_to_et(datetime.strptime(lines[1][:19], "%Y-%m-%d %H:%M:%S"))
                t1 = utc_to_et(datetime.strptime(lines[-1][:19], "%Y-%m-%d %H:%M:%S"))
                win.append((t0, t1))
        except Exception:
            pass
    return sorted(win)


def pct(a: list[float], q: float) -> float:
    return float(np.percentile(a, q)) if a else float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-01-01")
    ap.add_argument("--until", default="2099-12-31")
    args = ap.parse_args()
    trades = load_trades(args.since, args.until)
    wins = coverage_windows()
    by_day: dict[date, list[dict]] = defaultdict(list)
    for t in trades:
        by_day[t["session"]].append(t)
    days = sorted(by_day)
    print(f"{len(trades)} tape-verified entries on {len(days)} trading days ({days[0]} .. {days[-1]}), "
          f"{sum(t['usd'] is not None for t in trades)} with P&L; {len(wins)} recordings for coverage")

    cov = {}
    for d in days:
        s0, s1 = datetime.combine(d - timedelta(days=1), datetime.min.time()) + timedelta(hours=18), datetime.combine(d, datetime.min.time()) + timedelta(hours=17)
        parts = [(max(a, s0), min(b, s1)) for a, b in wins if b > s0 and a < s1]
        cov[d] = (min(p[0] for p in parts), max(p[1] for p in parts), sum((p[1] - p[0]).total_seconds() for p in parts) / 3600) if parts else (None, None, 0.0)
    covered_hours = sum(c[2] for c in cov.values())
    print(f"recorded time inside those sessions: {covered_hours:,.0f} h ({covered_hours / max(len(days), 1):.1f} h per day)")

    print("\n== daily profit stop / loss stop (days where at least 80 % of the trades have a P&L; the rest count as 0)")
    complete = {}
    miss = Counter()
    for d in days:
        ts = by_day[d]
        m = sum(t["usd"] is None for t in ts)
        miss[d] = m
        if m <= 0.2 * len(ts):
            complete[d] = ts
    print(f"usable days: {len(complete)} of {len(days)} (missing P&L per day: "
          + ", ".join(f"{d.strftime('%m-%d')}:{miss[d]}/{len(by_day[d])}" for d in days) + ")")
    print("  usable: " + ", ".join(f"{d} {sum(t['usd'] or 0 for t in ts):+,.0f} ({len(ts)} tr)" for d, ts in sorted(complete.items())))
    for label, sign in (("profit stop", 1), ("loss stop", -1)):
        print(f"\n  {label}: stop trading once the day's P&L reaches {'+' if sign > 0 else '-'}X")
        print(f"  {'X':>6} {'days reached':>12} {'obeyed':>7} {'violated (later trades)':<60}")
        for x in (500, 750, 1000, 1500, 2000, 2500, 3000, 4000, 5000):
            reached = obeyed = 0
            viol = []
            for d, ts in sorted(complete.items()):
                cum = 0.0
                for k, t in enumerate(ts):
                    cum += t["usd"] or 0.0
                    if sign * cum >= x:
                        reached += 1
                        later = ts[k + 1:]
                        if not later:
                            obeyed += 1
                        else:
                            viol.append(f"{d}@{cum:+,.0f} then {len(later)} more ({sum(t['usd'] or 0 for t in later):+,.0f})")
                        break
            print(f"  {sign * x:>+6} {reached:>12} {obeyed:>7} {'; '.join(viol)[:60]:<60}")
    print("\n  days that stopped early (recording ran >= 2 h after the last exit, no new trade):")
    print(f"  {'day':<10} {'last exit':>9} {'rec. end':>8} {'P&L then':>9} {'trades':>6}  note")
    early = 0
    for d in days:
        ts = by_day[d]
        last = max((t["t_out"] for t in ts if t["t_out"]), default=None)
        c0, c1, _ = cov[d]
        if last and c1 and (c1 - last) >= timedelta(hours=2):
            early += 1
            pnl = sum(t["usd"] for t in ts if t["usd"] is not None)
            inc = any(t["usd"] is None for t in ts)
            print(f"  {d} {last:%H:%M:%S} {c1:%H:%M} {pnl:>+9,.0f} {len(ts):>6}  {'P&L incomplete' if inc else ''}")
    print(f"  {early} of {len(days)} days")

    print("\n== trades per day")
    counts = Counter(len(by_day[d]) for d in days)
    print("  " + "  ".join(f"{n} tr: {counts[n]} d" for n in sorted(counts)))
    full = [d for d in days if cov[d][2] >= 12]
    print(f"  days with >= 12 h recorded: {len(full)}; their trades/day: " + ", ".join(f"{len(by_day[d])}" for d in full)
          + (f"  (max {max(len(by_day[d]) for d in full)})" if full else ""))

    print("\n== time of day (ET)")
    firsts = [min(t["t_in"] for t in by_day[d]) for d in days]
    lasts = [max(t["t_in"] for t in by_day[d]) for d in days]
    hm = lambda t: t.hour + t.minute / 60
    print(f"  first entry of the day: earliest {min(firsts):%H:%M}, median {datetime.min + timedelta(hours=float(np.median([hm(t) for t in firsts]))):%H:%M}")
    print(f"  last entry of the day:  latest {max(lasts, key=lambda t: hm(t)):%H:%M}, median {datetime.min + timedelta(hours=float(np.median([hm(t) for t in lasts]))):%H:%M}")
    hour_cov = Counter()
    for a, b in wins:
        t = a.replace(minute=0, second=0, microsecond=0)
        while t < b:
            t1 = min(t + timedelta(hours=1), b)
            hour_cov[t.hour] += (t1 - max(t, a)).total_seconds() / 3600
            t += timedelta(hours=1)
    hour_n = Counter(t["t_in"].hour for t in trades)
    print(f"  entries per recorded hour, by hour of day (ET):")
    line = []
    for h in list(range(18, 24)) + list(range(0, 18)):
        rate = hour_n[h] / hour_cov[h] if hour_cov[h] >= 2 else float("nan")
        line.append(f"{h:02d}:{rate:.2f}" if rate == rate else f"{h:02d}:-")
    print("  " + " ".join(line[:12]))
    print("  " + " ".join(line[12:]))
    blk = lambda h0, h1: (sum(hour_n[h] for h in range(h0, h1)), sum(hour_cov[h] for h in range(h0, h1)))
    for lab, (h0, h1) in (("18-00", (18, 24)), ("00-06", (0, 6)), ("06-09", (6, 9)), ("09-12", (9, 12)), ("12-15", (12, 15)), ("15-17", (15, 17))):
        n, h = blk(h0, h1)
        print(f"  {lab}: {n} entries in {h:.0f} recorded h = {n / h if h else 0:.2f}/h")

    print("\n== consecutive trades within a day")
    gaps = defaultdict(list)
    flips = Counter()
    same = []
    for d in days:
        ts = by_day[d]
        for a, b in zip(ts, ts[1:]):
            if a["t_out"] is None:
                continue
            g = (b["t_in"] - a["t_out"]).total_seconds() / 60
            k = a["kind"] if a["kind"].startswith(("stop", "target")) else ("other" if a["kind"] else "?")
            k = "target" if k.startswith("target") else k
            gaps[k].append(g)
            flips[(k, b["side"] != a["side"])] += 1
            if b["side"] == a["side"]:
                same.append((d, a, b, g))
    for k in ("stop", "target", "other"):
        g = gaps[k]
        if g:
            fl = flips[(k, True)]
            print(f"  after a {k:<6} n={len(g):>3}: next entry in min {min(g):.1f}, p10 {pct(g, 10):.1f}, median {pct(g, 50):.1f} min; "
                  f"within 2 min {np.mean(np.array(g) <= 2):.0%}; next trade opposite side {fl / (fl + flips[(k, False)]):.0%}")
    allpairs = sum(flips.values())
    print(f"  direction alternates on {sum(v for (k, f), v in flips.items() if f)} of {allpairs} consecutive pairs = "
          f"{sum(v for (k, f), v in flips.items() if f) / max(allpairs, 1):.0%}")
    print(f"  same-direction exceptions ({len(same)}): day, previous exit, gap, then the repeat")
    for d, a, b, g in same[:40]:
        print(f"    {d} {a['t_in']:%H:%M} {a['side']} -> {a['kind'] or 'exit not seen'} {a['pts'] if a['pts'] is not None else '':>7} | "
              f"gap {g:6.1f} min | {b['t_in']:%H:%M} {b['side']} again")
    if len(same) > 40:
        print(f"    ... {len(same) - 40} more")
    prev_kind = Counter(a["kind"].rstrip("0123456789.") or "none" for _, a, _, _ in same)
    print(f"  what preceded a repeat: {dict(prev_kind)}")

    print("\n== per month")
    print(f"  {'month':<8} {'days':>4} {'trades':>6} {'tr/day':>6} {'with P&L':>8} {'win%':>5} {'net $':>9} {'avg pts':>7} {'stops':>5} {'targets':>7}")
    bym = defaultdict(list)
    for t in trades:
        bym[t["session"].strftime("%Y-%m")].append(t)
    for m in sorted(bym):
        ts = bym[m]
        dd = {t["session"] for t in ts}
        pl = [t for t in ts if t["usd"] is not None]
        wr = np.mean([t["usd"] > 0 for t in pl]) if pl else float("nan")
        print(f"  {m:<8} {len(dd):>4} {len(ts):>6} {len(ts) / len(dd):>6.1f} {len(pl):>8} {wr:>5.0%} {sum(t['usd'] for t in pl):>9,.0f} "
              f"{np.mean([t['pts'] for t in pl]) if pl else float('nan'):>7.1f} {sum(t['kind'] == 'stop' for t in ts):>5} "
              f"{sum(t['kind'].startswith('target') for t in ts):>7}")


if __name__ == "__main__":
    main()
