from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_indicator_sweep as sw

BASE = Path(r"D:\stream_capture\DaCaptainTrader_")
LEDGER = BASE / "trades.csv"
OUT = BASE / "exit_paths.csv"
ET = ZoneInfo("America/New_York")
TICK = 0.25


def load_orders(vid: str) -> pd.DataFrame | None:
    p = BASE / "vod" / vid / "orders.csv"
    if not p.exists():
        return None
    o = pd.read_csv(p, dtype={"stp": str, "lmt": str})
    o["stp1"] = o.stp.map(lambda s: float(s) if isinstance(s, str) and ";" not in s and s else np.nan)
    o["lmt1"] = o.lmt.map(lambda s: float(s) if isinstance(s, str) and ";" not in s and s else np.nan)
    return o


def phase(o: pd.DataFrame, start: float) -> tuple[int | None, int, int]:
    s = o.dropna(subset=["stp1"])
    p, v = s.p.to_numpy(), s.stp1.to_numpy()
    votes = np.zeros(60)
    n = 0
    for i in range(1, len(p)):
        if v[i] != v[i - 1] and 0 < p[i] - p[i - 1] <= 8.0:
            n += 1
            u0, u1 = start + p[i - 1], start + p[i]
            for d in range(60):
                if np.floor((u1 + d) / 60) > np.floor((u0 + d) / 60):
                    votes[d] += 1
    if n < 5:
        return None, n, 0
    best = int(np.argmax(votes))
    run = [d for d in range(60) if votes[d] == votes[best]]
    return int(round(np.median(run))), n, int(votes[best])


def main() -> None:
    bars = sw.load_series("2026-02-15")
    bars["epoch"] = bars.time.astype("int64") // 10 ** 9
    open_at = dict(zip(bars.epoch - 60, bars.open))
    by_close = {int(e): i for i, e in enumerate(bars.epoch)}
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    by_vid = defaultdict(list)
    for r in rows:
        if r.get("entry_ok") in ("yes", "fixed") and r.get("side") in ("long", "short") and r.get("video") not in ("", "live"):
            by_vid[r["video"]].append(r)
    out_rows = []
    stats = Counter()
    for vid, trs in sorted(by_vid.items()):
        o = load_orders(vid)
        st_p = BASE / "vod" / vid / "status.json"
        if o is None or not st_p.exists():
            stats["no orders file"] += 1
            continue
        start = datetime.fromisoformat(json.load(open(st_p, encoding="utf-8"))["stream"]["start_utc"]).timestamp()
        d0, n_ch, votes = phase(o, start)
        if d0 is None:
            print(f"{vid}: too few stop changes ({n_ch}) to calibrate")
            stats["uncalibrated"] += 1
            continue
        ents = [json.loads(l) for l in open(BASE / "vod" / vid / "events.jsonl", encoding="utf-8")]
        ents = [e for e in ents if e.get("kind") == "entry" and e.get("avg")]
        best_k, best_m = 0, -1
        for k in range(-3, 4):
            d = d0 + 60 * k
            m = 0
            for e in ents:
                u = datetime.strptime(e["recv_utc"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() + d
                b = int(u // 60 * 60)
                op = open_at.get(b)
                if op is not None and abs(op - float(e["avg"])) <= 1.0:
                    m += 1
            if m > best_m:
                best_k, best_m = k, m
        d = d0 + 60 * best_k
        ok = len(ents) >= 3 and best_m >= 0.4 * len(ents) and votes >= 0.7 * n_ch
        print(f"{vid}: phase {d0:+d} s from {n_ch} stop changes ({votes} agree), minute shift {best_k:+d} "
              f"({best_m}/{len(ents)} entry fills = the bar's open){'' if ok else '  -> NOT CALIBRATED, skipped'}", flush=True)
        if not ok:
            stats["calibration failed"] += 1
            continue
        o["et"] = start + o.p + d
        for r in trs:
            ent = float(r["entry"])
            side = 1 if r["side"] == "long" else -1
            f_et = datetime.strptime(f"{r['date']} {r['entry_bar']}", "%Y-%m-%d %H:%M").replace(tzinfo=ET).timestamp()
            path = []
            for k in range(1, 400):
                c0 = f_et + 60 * k
                w = o[(o.et >= c0 + 2) & (o.et < c0 + 60)]
                if not len(w):
                    if k > 1:
                        break
                    continue
                stp = Counter(w.stp1.dropna().round(2)).most_common(1)
                lmt = Counter(w.lmt1.dropna().round(2)).most_common(1)
                if not stp and not lmt:
                    break
                lv = lmt[0][0] if lmt else np.nan
                first_l = next((q[3] for q in path if not np.isnan(q[3])), np.nan)
                if not np.isnan(lv) and not np.isnan(first_l) and lv != first_l:
                    break
                path.append((k, c0, stp[0][0] if stp else np.nan, lv, len(w)))
            if not path:
                stats["no bracket seen"] += 1
                continue
            first_t = next((p[3] for p in path if not np.isnan(p[3])), np.nan)
            if np.isnan(first_t):
                stats["no target seen"] += 1
                continue
            ticks = round(side * (first_t - ent) / TICK)
            if ticks <= 0 or abs(side * (first_t - ent) / TICK - ticks) > 1e-6:
                stats["target not on the entry's side"] += 1
                continue
            stats["kept"] += 1
            for k, c0, stp, lmt, nr in path:
                out_rows.append({"video": vid, "trade": datetime.fromtimestamp(f_et, ET).strftime("%Y-%m-%d %H:%M"),
                                 "side": r["side"], "entry": ent, "copy_ticks": ticks, "k": k,
                                 "bar_close_et": datetime.fromtimestamp(c0, ET).strftime("%Y-%m-%d %H:%M"),
                                 "stop": stp, "target": lmt, "n_reads": nr,
                                 "in_bars": int(c0) in by_close})
    pd.DataFrame(out_rows).to_csv(OUT, index=False)
    print(f"\n{stats}\nwritten {OUT} ({len(out_rows)} bar rows)")


if __name__ == "__main__":
    main()
