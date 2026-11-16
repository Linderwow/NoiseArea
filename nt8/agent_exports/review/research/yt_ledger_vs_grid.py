from __future__ import annotations

import csv
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stream_trades as stt
import yt_grid_pnl as gp
import yt_tape_grade as tg

B = Path(r"D:\stream_capture\DaCaptainTrader_")


def utc_of(r: dict) -> datetime:
    t = datetime.strptime(f"{r['date']} {r['exit_et']}", "%Y-%m-%d %H:%M:%S")
    return t + timedelta(hours=5 if t >= datetime(2026, 11, 1, 2) else 4)


def main() -> None:
    rows = list(csv.DictReader(open(B / "trades.csv", encoding="utf-8")))
    graded, _, _ = tg.grade_rows(rows)
    tape = gp.Tape()
    print(f"{'video':<12} {'copy':<11} {'grid $':>8} {'tape $':>8} {'panel $':>8} {'tape-grid':>9} {'trades':>6}")
    tot = {"grid": 0.0, "tape": 0.0, "panel": 0.0}
    diffs = []
    n = 0
    for f in sorted((B / "vod").iterdir()):
        if not (f / "events.jsonl").exists():
            continue
        el = stt._elect_row(f / "state.csv")
        if el.get("row") is None:
            continue
        res = gp.video_rows(f, tape)
        if res is None:
            continue
        grid, _, modal, _ = res
        if el["rows"] != modal or el["row"] not in grid or grid[el["row"]][3] is None:
            continue
        info = grid[el["row"]][3]
        t0, t1 = info["t0"], info["t1"]
        inside = [g for g in graded if g["video"] == f.name and t0 <= utc_of(g["row"]) <= t1]
        if not inside:
            continue
        g_usd = info["change"]
        t_usd = sum(g["pts"] for g in inside) * gp.PT_USD_NQ
        p_usd = sum(g["panel_pts"] for g in inside if g["panel_pts"] is not None) * gp.PT_USD_NQ
        tot["grid"] += g_usd
        tot["tape"] += t_usd
        tot["panel"] += p_usd
        diffs.append(t_usd - g_usd)
        n += 1
        print(f"{f.name:<12} {el['copy']:<11} {g_usd:>8,.0f} {t_usd:>8,.0f} {p_usd:>8,.0f} {t_usd - g_usd:>9,.0f} {len(inside):>6}")
    if n:
        print(f"\n{n} videos where the panel's copy is known: grid ${tot['grid']:,.0f}, tape ${tot['tape']:,.0f}, panel "
              f"${tot['panel']:,.0f} (1 NQ); tape - grid: total ${tot['tape'] - tot['grid']:+,.0f}, median per video "
              f"${statistics.median(diffs):+,.0f}, median |diff| ${statistics.median(abs(d) for d in diffs):,.0f}")
        print("-> the verdict's money numbers: the tape grade (yt_tape_grade.py) for the copy on the panel, the grid tracker\n"
              "   (yt_grid_pnl.py) for all copies; this table says how far the two agree where both see the same trades.")
    else:
        print("no video where the panel's copy could be identified and graded")


if __name__ == "__main__":
    main()
