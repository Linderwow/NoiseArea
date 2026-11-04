r"""Supervise the stream downloads + reads: watch D:\stream_capture\vods, exit with a report whenever something needs a decision,
or every --summary-hours with a status line. Meant to run in the background (one notification per exit), then be re-armed.

Exits (and prints why) on: a new .bad or .fail marker; the downloader stalled (lock present, no .part growth and no new .ok
for 15 min); the downloader finished (lock gone after being seen); the watcher silent for 3 h while verified files wait;
D: under 40 GB free; or the periodic summary.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

VODS = Path(r"D:\stream_capture\vods")
TO_DELETE = Path(r"D:\stream_capture\_to_delete")
LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")
LOG = VODS / "watch.log"


def snapshot() -> dict:
    files = {p.name: p.stat().st_size for p in VODS.iterdir() if p.is_file()}
    parts = {n: s for n, s in files.items() if n.endswith(".part")}
    marks = {m: sorted(n[: -len(m)] for n in files if n.endswith(m)) for m in (".ok", ".done", ".bad", ".fail")}
    vids = [n for n in files if n.endswith((".mp4", ".webm", ".mkv"))]
    moved = len([p for p in TO_DELETE.glob("*") if p.suffix in (".mp4", ".webm", ".mkv")]) if TO_DELETE.exists() else 0
    log_tail = LOG.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-3:] if LOG.exists() else []
    log_mtime = LOG.stat().st_mtime if LOG.exists() else 0
    trades = sum(1 for _ in open(LEDGER, encoding="utf-8")) - 1 if LEDGER.exists() else 0
    dl_state = {}
    try:
        dl_state = json.loads((VODS / "downloader_status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    return {"t": time.time(), "lock": (VODS / "downloader.lock").exists(), "parts": parts, "marks": marks, "vids": vids,
            "moved": moved, "free_gb": shutil.disk_usage("D:/").free / 1e9, "log_tail": log_tail, "log_mtime": log_mtime,
            "trades": trades, "dl_state": dl_state}


def report(why: str, s: dict) -> None:
    print(f"{datetime.now():%H:%M} SUPERVISOR: {why}")
    ds = s.get("dl_state") or {}
    print(f"  downloader {'running' if s['lock'] else 'not running'}"
          + (f" ({ds.get('state')} {ds.get('video', '')} since {ds.get('at', '')})" if ds else "") + "; in progress: "
          + (", ".join(f"{n} {sz/1e9:.2f} GB" for n, sz in s["parts"].items()) or "none"))
    print(f"  verified waiting/reading: {len(s['marks']['.ok'])}  read+moved: {s['moved']}  bad: {s['marks']['.bad']}  "
          f"fail: {s['marks']['.fail']}  free {s['free_gb']:.0f} GB  ledger trades: {s['trades']}")
    for line in s["log_tail"]:
        print("  log: " + line[:160])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary-hours", type=float, default=3)
    ap.add_argument("--max-hours", type=float, default=24)
    args = ap.parse_args()
    start = time.time()
    prev = snapshot()
    seen_lock = prev["lock"]
    last_progress = time.time()
    last_summary = time.time()
    while time.time() - start < args.max_hours * 3600:
        time.sleep(60)
        s = snapshot()
        new_bad = sorted(set(s["marks"][".bad"]) - set(prev["marks"][".bad"]))
        new_fail = sorted(set(s["marks"][".fail"]) - set(prev["marks"][".fail"]))
        if new_bad or new_fail:
            for v in new_bad:
                print(f"  BAD {v}: {(VODS / (v + '.bad')).read_text(encoding='utf-8', errors='replace')[:200]}")
            for v in new_fail:
                print(f"  FAIL {v}: {(VODS / (v + '.fail')).read_text(encoding='utf-8', errors='replace')[-300:]}")
            report("a file failed verification or reading", s)
            return
        grew = any(s["parts"].get(n, 0) > prev["parts"].get(n, 0) for n in s["parts"]) or \
            len(s["marks"][".ok"]) > len(prev["marks"][".ok"]) or s["moved"] > prev["moved"] or set(s["parts"]) != set(prev["parts"])
        if grew:
            last_progress = time.time()
        if s["lock"]:
            seen_lock = True
            blocked = (s.get("dl_state") or {}).get("state") == "blocked"
            if blocked:
                last_progress = time.time()  # waiting out YouTube's bot check is not a stall
            elif time.time() - last_progress > 15 * 60:
                report("downloader running but nothing moved for 15 min", s)
                return
        elif seen_lock:
            report("downloader finished or stopped", s)
            return
        if s["free_gb"] < 40:
            report(f"only {s['free_gb']:.0f} GB free on D:", s)
            return
        if s["marks"][".ok"] and time.time() - s["log_mtime"] > 3 * 3600:
            report("watcher silent for 3 h while verified files wait", s)
            return
        if time.time() - last_summary >= args.summary_hours * 3600:
            report("periodic summary", s)
            return
        prev = s
    report("supervisor time limit reached", snapshot())


if __name__ == "__main__":
    main()
