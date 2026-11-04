r"""Watch D:\stream_capture\vods: verify each completed download, read it with stream_capture.py --file, rebuild the ledger,
then move the .mp4 to D:\stream_capture\_to_delete (never deleted; Sashkin empties that folder).

Run under screenread's venv:  python -X utf8 vods_watch.py [--hours 24]   (log: D:\stream_capture\vods\watch.log)
A file is taken when it is not a .part, has <id>.ok (verified by download_streams.py) or has stopped growing for 90 s and
passes the same check here; <id>.bad files are left alone; <id>.done marks a file already read.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

VODS = Path(r"D:\stream_capture\vods")
TO_DELETE = Path(r"D:\stream_capture\_to_delete")
RESEARCH = Path(__file__).resolve().parent
SYSTEM_PYTHON = r"C:\Users\linde\AppData\Local\Programs\Python\Python312\python.exe"
LOG = VODS / "watch.log"
sys.path.insert(0, str(RESEARCH))
from download_streams import verify  # noqa: E402


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def expected_duration(vid: str) -> float | None:
    import json
    for p in (VODS / "streams.json", Path(r"D:\stream_capture\DaCaptainTrader_\_dig\streams.json")):
        if p.exists():
            try:
                for e in json.loads(p.read_text(encoding="utf-8")).get("entries", []):
                    if e.get("id") == vid:
                        return e.get("duration")
            except ValueError:
                pass
    return None


def process(mp4: Path) -> None:
    vid = mp4.stem
    ok_marker, bad_marker, done_marker = VODS / f"{vid}.ok", VODS / f"{vid}.bad", VODS / f"{vid}.done"
    if not ok_marker.exists():
        ok, why = verify(mp4, expected_duration(vid))
        (ok_marker if ok else bad_marker).write_text(why, encoding="utf-8")
        log(("verified " if ok else "BAD ") + mp4.name + ": " + why)
        if not ok:
            return
    log(f"reading {mp4.name} ({mp4.stat().st_size/1e9:.1f} GB)")
    t0 = time.time()
    r = subprocess.run([sys.executable, "-X", "utf8", str(RESEARCH / "stream_capture.py"), "--file", str(mp4), "--step", "5"],
                       capture_output=True, text=True, encoding="utf-8")
    tail = (r.stdout or "").strip().splitlines()[-2:]
    log(f"read {mp4.name} rc={r.returncode} in {(time.time()-t0)/60:.0f} min: " + " | ".join(tail))
    if r.returncode != 0:
        log("stderr: " + (r.stderr or "")[-500:])
        (VODS / f"{vid}.fail").write_text((r.stderr or "")[-2000:], encoding="utf-8")  # left for a look; not retried
        return
    done_marker.write_text(datetime.now().isoformat(), encoding="utf-8")
    TO_DELETE.mkdir(parents=True, exist_ok=True)
    for src in (mp4, mp4.with_suffix(mp4.suffix + ".json"), ok_marker):
        if src.exists():
            shutil.move(str(src), str(TO_DELETE / src.name))
    log(f"moved {mp4.name} to {TO_DELETE}")
    r2 = subprocess.run([SYSTEM_PYTHON, "-I", "-X", "utf8", str(RESEARCH / "stream_trades.py")], capture_output=True, text=True, encoding="utf-8")
    log("ledger: " + " | ".join((r2.stdout or "").strip().splitlines()[-1:]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    args = ap.parse_args()
    VODS.mkdir(parents=True, exist_ok=True)
    log(f"watcher start ({args.hours:g} h)")
    end = time.time() + args.hours * 3600
    sizes: dict[str, tuple[int, float]] = {}
    while time.time() < end:
        files = [p for ext in ("*.mp4", "*.webm", "*.mkv") for p in VODS.glob(ext)]
        for mp4 in sorted(files):
            vid = mp4.stem
            if any((VODS / f"{vid}{m}").exists() for m in (".done", ".bad", ".fail")) \
                    or (VODS / f"{mp4.name}.part").exists() or (VODS / f"{mp4.name}.ytdl").exists():
                continue
            if (VODS / f"{vid}.ok").exists():
                process(mp4)
                continue
            size, since = mp4.stat().st_size, time.time()
            prev = sizes.get(vid)
            if prev and prev[0] == size and since - prev[1] >= 90:
                process(mp4)
                sizes.pop(vid, None)
            elif not prev or prev[0] != size:
                sizes[vid] = (size, since)
        time.sleep(30)
    log("watcher end")


if __name__ == "__main__":
    main()
