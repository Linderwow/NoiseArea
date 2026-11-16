from __future__ import annotations

import csv
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tape_seconds as tape

LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")
DELAYS = (25, 35, 45, 60)


def price_at(t: datetime) -> float | None:
    s = tape.seconds_around(t, 5, 5)
    if s is None or not len(s):
        return None
    s = s[s.index <= t] if len(s[s.index <= t]) else s
    v = s["last"].dropna()
    return float(v.iloc[-1]) if len(v) else None


def main() -> None:
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    use = [r for r in rows if r["date"] >= "2026-10-22" and r.get("entry_ok") in ("yes", "fixed") and r.get("side") in ("long", "short")
           and r.get("entry_et")]
    cost = {d: [] for d in DELAYS}
    cost_entry = {d: [] for d in DELAYS}
    for r in use:
        side = 1 if r["side"] == "long" else -1
        t_in = datetime.strptime(f"{r['date']} {r['entry_bar']}:00", "%Y-%m-%d %H:%M:%S")
        entry = float(r["entry"])
        t_out = datetime.strptime(f"{r['date']} {r['exit_et']}", "%Y-%m-%d %H:%M:%S") if r.get("exit_et") and r.get("exit") else None
        exit_px = float(r["exit"]) if t_out else None
        for d in DELAYS:
            p_in = price_at(t_in + timedelta(seconds=d))
            if p_in is None:
                continue
            c_in = side * (p_in - entry)
            cost_entry[d].append(c_in)
            if t_out is not None:
                p_out = price_at(t_out + timedelta(seconds=d))
                if p_out is not None:
                    cost[d].append(c_in - side * (p_out - exit_px))
    print(f"COPY DELAY - his tape-verified trades since 2026-10-22: {len(use)} entries")
    print(f"{'delay':>6} {'entry: mean lost':>16} {'median':>7} {'t':>6} {'n':>4}   {'round trip: mean lost':>21} {'sd':>6} {'t':>6} {'n':>4}  {'$ per micro':>11}")
    for d in DELAYS:
        a, b = np.array(cost_entry[d]), np.array(cost[d])
        ta = a.mean() / a.std(ddof=1) * math.sqrt(len(a)) if len(a) > 2 else float("nan")
        tb = b.mean() / b.std(ddof=1) * math.sqrt(len(b)) if len(b) > 2 else float("nan")
        print(f"{d:>5}s {a.mean():>16.2f} {np.median(a):>7.2f} {ta:>6.2f} {len(a):>4}   {b.mean():>21.2f} {b.std(ddof=1):>6.2f} "
              f"{tb:>6.2f} {len(b):>4}  {b.mean() * 2:>11.2f}")
    print("\nread: points the copier gives up per trade vs his fills (positive = worse). sd is the extra noise a copier carries\n"
          "per trade; $ per micro = mean points x $2.")


if __name__ == "__main__":
    main()
