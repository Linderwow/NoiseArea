from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRASH = Path(r"D:\stream_capture\_to_delete\research_results")
VENV_PY = r"D:\dev\tooling\screenread\.venv\Scripts\python.exe"
STEPS = [
    ("FRAME PASS - re-read the P&L frames with the fixed OCR", "frame_pass.py", ["ocr"], VENV_PY),
    ("FRAME PASS - frame-derived P&L vs the tape", "frame_pass.py", ["validate"]),
    ("LEDGER", "stream_trades.py", []),
    ("MONEY 1 - HIS TRADES GRADED ON OUR TAPE (no OCR'd dollars) - all months", "yt_tape_grade.py", []),
    ("MONEY 1 - HIS TRADES GRADED ON OUR TAPE - current setup (since 2026-10-01)", "yt_tape_grade.py", ["--since", "2026-10-01"]),
    ("MONEY 2 - EVERY COPY FROM THE GRID'S REALIZED COLUMN (artefact-aware tracker) - all months", "yt_grid_pnl.py", []),
    ("MONEY 2 - EVERY COPY FROM THE GRID - current setup (since 2026-10-01)", "yt_grid_pnl.py", ["--since", "2026-10-01"]),
    ("MONEY CHECK - grid vs tape on the same copy and minutes", "yt_ledger_vs_grid.py", []),
    ("ENTRY RULE FIT", "yt_rule_fit.py", []),
    ("TAKEN vs LET-GO BREAKOUTS", "yt_rule_diag.py", ["8"]),
    ("IS HIS SELECTION WORTH ANYTHING? taken vs let-go under identical exits - all months", "yt_selector_value.py", ["8"]),
    ("IS HIS SELECTION WORTH ANYTHING? - current setup (since 2026-10-01)", "yt_selector_value.py", ["8", "--since", "2026-10-01"]),
    ("CHART FEATURES, CROSS-VALIDATED", "yt_feature_tree.py", []),
    ("ORDER FLOW AT THE SIGNAL BAR", "yt_orderflow.py", []),
    ("WHAT COPYING BY HAND COSTS (25-60 s behind him, on the tick tape)", "yt_copy_delay.py", []),
    ("STOP RULE", "yt_stop_fit.py", []),
    ("DAY RULES", "yt_day_rules.py", []),
    ("BACKTEST 2019-2026 + MES", "yt_backtest.py", []),
]


def run(script: str, args: list[str], timeout: int, python: str | None = None) -> tuple[str, float]:
    t0 = time.time()
    if not (HERE / script).exists():
        return f"(not built: {script})", 0.0
    cmd = [python, "-X", "utf8"] if python else [sys.executable, "-I", "-X", "utf8"]
    try:
        p = subprocess.run([*cmd, str(HERE / script), *args], cwd=HERE, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        out = p.stdout + (("\n[stderr]\n" + p.stderr[-4000:]) if p.returncode else "")
    except subprocess.TimeoutExpired:
        out = f"(timed out after {timeout // 60} min)"
    return out, time.time() - t0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-backtest", action="store_true")
    args = ap.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_path = HERE / f"YT_DEEP_RESEARCH_{stamp}.txt"
    sections = [f"DA CAPTAIN TRADER STREAM STRATEGY - DEEP RESEARCH RESULTS  {datetime.now():%Y-%m-%d %H:%M} ET\n"
                f"One run over every video read so far; every step's full report follows its header. Kills are results too.\n"]
    summary = []
    for step in STEPS:
        title, script, sargs = step[:3]
        python = step[3] if len(step) > 3 else None
        if args.skip_backtest and script == "yt_backtest.py":
            continue
        timeout = 7200 if script == "yt_backtest.py" else 1800
        out, secs = run(script, sargs, timeout, python)
        sections.append(f"\n\n{'=' * 100}\n{title}   ({script} {' '.join(sargs)}, {secs:.0f} s)\n{'=' * 100}\n{out.rstrip()}")
        last = [ln for ln in out.strip().splitlines() if ln.strip()]
        summary.append(f"{title}: " + (last[-1][:160] if last else "(no output)"))
        print(f"{title}: done in {secs:.0f} s", flush=True)
    sections.insert(1, "\nSUMMARY (last line of each step)\n" + "\n".join("  " + s for s in summary))
    out_path.write_text("\n".join(sections), encoding="utf-8")
    TRASH.mkdir(parents=True, exist_ok=True)
    moved = 0
    for old in HERE.glob("YT_*.txt"):
        if old != out_path:
            shutil.move(str(old), str(TRASH / old.name))
            moved += 1
    print(f"\nresults -> {out_path}  ({moved} older result files moved to {TRASH})")
    print("\n".join(summary))


if __name__ == "__main__":
    main()
