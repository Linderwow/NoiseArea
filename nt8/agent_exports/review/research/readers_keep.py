from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime
from pathlib import Path

VODS = Path(r"D:\stream_capture\vods")
RESEARCH = Path(__file__).resolve().parent
VENV_PY = r"D:\dev\tooling\screenread\.venv\Scripts\python.exe"
LOG = VODS / "readers_keep.log"


def log(msg: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
    print(line, flush=True)
    LOG.open("a", encoding="utf-8").write(line + "\n")


def watchers() -> int:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*vods_watch.py*' -and "
                          "$_.ExecutablePath -like '*Python312*' }).Count"], capture_output=True, text=True).stdout.strip()
    return int(out or 0)


def work_left() -> bool:
    for ext in ("*.mp4", "*.webm", "*.mkv"):
        for p in VODS.glob(ext):
            if not any((VODS / f"{p.stem}{m}").exists() for m in (".done", ".bad", ".fail")):
                return True
    try:
        state = json.load(open(VODS / "downloader_status.json", encoding="utf-8")).get("state")
    except Exception:
        state = "?"
    return state not in ("finished", "stopped")


def start_reader(reverse: bool) -> None:
    cmd = [VENV_PY, "-X", "utf8", str(RESEARCH / "vods_watch.py"), "--hours", "24"] + (["--reverse"] if reverse else [])
    flags = 0x00000008 | 0x00000200
    p = subprocess.Popen(cmd, cwd=RESEARCH, creationflags=flags, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    log(f"started reader pid {p.pid}{' (reverse)' if reverse else ''}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readers", type=int, default=6)
    ap.add_argument("--days", type=float, default=7)
    args = ap.parse_args()
    end = time.time() + args.days * 86400
    log(f"keeper start: {args.readers} readers for up to {args.days:g} days")
    started = 0
    while time.time() < end:
        n = watchers()
        if not work_left():
            if n == 0:
                log("no work left and no reader running: keeper end")
                return
        elif n < args.readers:
            for i in range(args.readers - n):
                start_reader(reverse=bool((n + i) % 2))
                started += 1
                time.sleep(40)
        time.sleep(300)
    log(f"keeper end after {args.days:g} days ({started} readers started)")


if __name__ == "__main__":
    main()
