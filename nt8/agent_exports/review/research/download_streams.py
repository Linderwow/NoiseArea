r"""Download Da Captain Trader's past streams one by one, newest first, and verify each file before it is ingested.

Run in a normal terminal (your Python has yt-dlp):
    python download_streams.py                 # all streams >= 1 h, newest first, skips what is done; Ctrl+C stops between videos
    python download_streams.py --max 60        # stop after 60 new downloads
    python download_streams.py --list          # refresh the stream list from the channel first
    python download_streams.py --check D:\stream_capture\vods\<id>.mp4   # verify one file only

Per video: download the best video-only stream up to 720p (mp4, else webm) to D:\stream_capture\vods\<id>.<ext>, resuming
a .part; then verify it is whole: it opens, and frames read at 5 %, 50 %, 95 % and 99.5 % of YouTube's listed duration
(a truncated file fails the late seeks). Good -> <id>.ok (the watcher then reads it); bad -> <id>.bad, re-downloaded once.
Safety: one instance at a time (downloader.lock); a file another process is still writing (fresh .part) is skipped; stops
when D: has less than 30 GB free; already-read videos (<id>.done, or the file moved to _to_delete) are skipped.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

VODS = Path(r"D:\stream_capture\vods")
TO_DELETE = Path(r"D:\stream_capture\_to_delete")
VOD_OUT = Path(r"D:\stream_capture\DaCaptainTrader_\vod")
LIST_CACHE = VODS / "streams.json"
DIG_LIST = Path(r"D:\stream_capture\DaCaptainTrader_\_dig\streams.json")
CHANNEL = "https://www.youtube.com/@DaCaptainTrader_/streams"
# video-only, 720p or ABOVE, never below: the smallest height that is >= 720 (so 720 when offered, else 1080/1440...)
FORMAT = "bv*[height>=720][ext=mp4]/bv*[height>=720]"
SORT = "+res"  # ascending resolution within the >=720 set
EXTS = (".mp4", ".webm", ".mkv")
MIN_FREE_GB = 30


def stream_list(refresh: bool) -> list[dict]:
    src = None
    if not refresh:
        for p in (LIST_CACHE, DIG_LIST):
            if p.exists():
                src = p
                break
    if refresh or src is None:
        print("fetching the stream list from the channel ...")
        p = subprocess.run([sys.executable, "-X", "utf8", "-m", "yt_dlp", "-J", "--flat-playlist", "--no-warnings", CHANNEL],
                           capture_output=True, text=True, encoding="utf-8", timeout=600)
        if p.returncode != 0:
            sys.exit("list failed: " + (p.stderr or "")[-400:])
        VODS.mkdir(parents=True, exist_ok=True)
        LIST_CACHE.write_text(p.stdout, encoding="utf-8")
        src = LIST_CACHE
    d = json.loads(src.read_text(encoding="utf-8"))
    return [e for e in d.get("entries", []) if e.get("live_status") == "was_live"]


def expected_duration(vid: str) -> float | None:
    for e in stream_list(False):
        if e.get("id") == vid:
            return e.get("duration")
    return None


def verify(path: Path, expected_s: float | None) -> tuple[bool, str]:
    """Whole and readable: opens, and frames read at 5/50/95/99.5 % of the expected length (or of the file's own length)."""
    if not path.exists():
        return False, "file missing"
    size = path.stat().st_size
    if size < 20_000_000:
        return False, f"too small ({size} bytes)"
    try:
        import cv2
    except ImportError:
        return True, f"{size/1e9:.2f} GB (cv2 not available in this Python: size check only)"
    cap = cv2.VideoCapture(str(path), cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return False, "does not open"
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if height and height < 720:
        cap.release()
        return False, f"only {height}p: below 720p, not accepted"
    own = frames / fps if fps else 0
    ref = expected_s or own
    if not ref:
        cap.release()
        return False, "no duration known"
    unreadable, black = [], []
    for frac in (0.05, 0.5, 0.95, 0.995):
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, ref * frac - 2) * 1000)
        ok, img = cap.read()
        if not (ok and img is not None):
            unreadable.append(f"{frac:.3g}")
        elif img.mean() <= 2:
            black.append(f"{frac:.3g}")
    cap.release()
    length_ok = (not expected_s) or abs(own - expected_s) <= 0.03 * expected_s + 5
    if unreadable:
        return False, f"no readable frame at {', '.join(unreadable)} of {ref:.0f} s (file says {own:.0f} s); download incomplete?"
    if black and not length_ok:
        return False, f"black frame at {', '.join(black)} and file length {own:.0f} s vs YouTube {expected_s:.0f} s; download incomplete?"
    note = "" if length_ok else f"; NOTE file length {own:.0f} s vs YouTube {expected_s:.0f} s"
    if black:
        note += f"; black screen at {', '.join(black)} of the length (the stream itself went dark there)"
    return True, f"{size/1e9:.2f} GB, {own/3600:.2f} h at {fps:.0f} fps, frames readable at 5/50/95/99.5 % of {ref:.0f} s{note}"


def existing_file(vid: str) -> Path | None:
    for ext in EXTS:
        p = VODS / f"{vid}{ext}"
        if p.exists():
            return p
    return None


def other_ytdlp_running(vid: str) -> bool:
    """Is some other process (your terminal's yt-dlp) downloading this video right now?"""
    try:  # wmic no longer exists on Windows 11; CIM through PowerShell does
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"name='python.exe'\" | ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                             capture_output=True, text=True, timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    me = str(os.getpid())
    for line in out.splitlines():
        pid, _, cmd = line.partition("\t")
        if pid.strip() != me and "yt_dlp" in cmd and vid in cmd:
            return True
    return False


def in_progress_elsewhere(vid: str) -> bool:
    """A fresh .part / .ytdl AND a live yt-dlp on it = someone else is writing this video; a stale .part is just resumed."""
    for ext in EXTS:
        for suffix in (".part", ".ytdl"):
            p = VODS / f"{vid}{ext}{suffix}"
            if p.exists() and time.time() - p.stat().st_mtime < 180 and other_ytdlp_running(vid):
                return True
    return False


def done_already(vid: str) -> bool:
    if (VODS / f"{vid}.done").exists():
        return True
    if any((TO_DELETE / f"{vid}{ext}").exists() for ext in EXTS):
        return True
    st = VOD_OUT / vid / "status.json"
    if st.exists():
        try:
            return bool(json.loads(st.read_text(encoding="utf-8")).get("done"))
        except ValueError:
            return False
    return False


PROBE_CACHE = VODS / "probe.json"


def probe(vid: str) -> tuple[str, int | None]:
    """('ok', best height) | ('members', None) | ('unavailable', None) | ('ratelimit', None) | ('error', None).
    Cached in probe.json for 'ok' and 'members'/'unavailable' only; a rate-limit answer ("Sign in to confirm you're not a
    bot") is never cached and never turned into a skip: on 11-04 that answer marked 180 good streams as inaccessible."""
    cache = {}
    if PROBE_CACHE.exists():
        try:
            cache = json.loads(PROBE_CACHE.read_text(encoding="utf-8"))
        except ValueError:
            cache = {}
    if vid in cache and cache[vid] is not None:
        v = cache[vid]
        return ("ok", v) if isinstance(v, int) else (v, None)
    p = subprocess.run([sys.executable, "-X", "utf8", "-m", "yt_dlp", "-j", "--skip-download", "--no-warnings", "--",
                        f"https://www.youtube.com/watch?v={vid}"], capture_output=True, text=True, encoding="utf-8", timeout=120)
    err = (p.stderr or "")
    if p.returncode != 0 or not p.stdout.strip():
        low = err.lower()
        if "not a bot" in low or "sign in to confirm" in low or "429" in low or "too many requests" in low:
            return "ratelimit", None
        if "members-only" in low or "join this channel" in low:
            status = "members"
        elif "private video" in low or "video unavailable" in low or "has been removed" in low:
            status = "unavailable"
        else:
            return "error", None
        cache[vid] = status
        PROBE_CACHE.write_text(json.dumps(cache, indent=0), encoding="utf-8")
        return status, None
    info = json.loads(p.stdout.splitlines()[-1])
    h = max((f.get("height") or 0) for f in info.get("formats", []) if f.get("vcodec", "none") != "none") or 0
    cache[vid] = h
    PROBE_CACHE.write_text(json.dumps(cache, indent=0), encoding="utf-8")
    return "ok", h


def download(vid: str) -> bool:
    cmd = [sys.executable, "-X", "utf8", "-m", "yt_dlp", "-f", FORMAT, "-S", SORT, "--continue", "--no-warnings",
           "--retries", "10", "--fragment-retries", "10", "--retry-sleep", "5", "--sleep-requests", "1",
           "-o", str(VODS / "%(id)s.%(ext)s"), "--", f"https://www.youtube.com/watch?v={vid}"]
    rc = subprocess.run(cmd).returncode
    time.sleep(20)  # a pause between videos keeps YouTube's bot check quiet
    return rc == 0


def free_gb() -> float:
    return shutil.disk_usage("D:/").free / 1e9


def take_lock() -> Path | None:
    lock = VODS / "downloader.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
            if str(pid) in out:
                print(f"another download_streams.py is running (pid {pid}); run only one at a time")
                return None
        except ValueError:
            pass
    lock.write_text(str(os.getpid()))
    return lock


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="stop after this many new downloads (0 = no limit)")
    ap.add_argument("--list", action="store_true", help="refresh the stream list from the channel")
    ap.add_argument("--min-hours", type=float, default=1.0)
    ap.add_argument("--min-height", type=int, default=720, help="skip streams whose best quality is below this (480p text is unreadable)")
    ap.add_argument("--check", default="", help="verify one file and exit")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    VODS.mkdir(parents=True, exist_ok=True)
    if args.check:
        p = Path(args.check)
        ok, why = verify(p, expected_duration(p.stem[-11:]))
        (p.with_name(p.stem + (".ok" if ok else ".bad"))).write_text(why, encoding="utf-8")
        print(("OK   " if ok else "BAD  ") + p.name + ": " + why)
        sys.exit(0 if ok else 1)
    lock = take_lock()
    if lock is None:
        sys.exit(1)
    try:
        ents = [e for e in stream_list(args.list) if (e.get("duration") or 0) >= args.min_hours * 3600]
        print(f"{len(ents)} streams of {args.min_hours:g} h or more, newest first; {free_gb():.0f} GB free on D:")
        n = 0
        deferred: list[dict] = []
        queue = list(ents)
        while queue:
            e = queue.pop(0)
            if not queue and deferred:  # second pass over the ones another process was writing earlier
                queue, deferred = [d for d in deferred if not in_progress_elsewhere(d["id"])], []
            vid, title, dur = e["id"], e.get("title", ""), e.get("duration") or 0
            if done_already(vid) or (VODS / f"{vid}.ok").exists() or (VODS / f"{vid}.skip").exists():
                continue
            if in_progress_elsewhere(vid):
                print(f"--- {vid} is being downloaded by another process right now; will come back to it at the end")
                deferred.append(e)
                continue
            if args.max and n >= args.max:
                break
            if free_gb() < MIN_FREE_GB:
                print(f"\nstopping: only {free_gb():.0f} GB free on D: (need {MIN_FREE_GB}); empty {TO_DELETE} and run again")
                break
            if existing_file(vid) is None:
                status, h = probe(vid)
                while status in ("ratelimit", "error"):
                    wait = 600 if status == "ratelimit" else 60
                    print(f"--- YouTube {'is rate-limiting this PC (\"not a bot\" check)' if status == 'ratelimit' else 'probe error'}; "
                          f"waiting {wait // 60} min before trying {vid} again (nothing is skipped because of this)")
                    time.sleep(wait)
                    status, h = probe(vid)
                if status == "members":
                    (VODS / f"{vid}.skip").write_text("members-only", encoding="utf-8")
                    print(f"--- {vid}  {title[-22:]}  members-only; skipped (not counted)")
                    continue
                if status == "unavailable":
                    (VODS / f"{vid}.skip").write_text("private or removed", encoding="utf-8")
                    print(f"--- {vid}  {title[-22:]}  private or removed; skipped (not counted)")
                    continue
                if h < args.min_height:
                    (VODS / f"{vid}.skip").write_text(f"best quality {h}p (< {args.min_height}p)", encoding="utf-8")
                    print(f"--- {vid}  {title[-22:]}  only {h}p on YouTube; skipped (not counted)")
                    continue
                time.sleep(3)  # be gentle: the probe + download pair per stream is what tripped the bot check
            print(f"\n=== {vid}  {title[-22:]}  {dur/3600:.1f} h   ({n + 1}{'/' + str(args.max) if args.max else ''})")
            if args.dry_run:
                n += 1
                continue
            verdict = None
            for attempt in (1, 2):
                f = existing_file(vid)
                if f is None or (VODS / f"{vid}.bad").exists():
                    if (VODS / f"{vid}.bad").exists():
                        (VODS / f"{vid}.bad").unlink()
                        if f:
                            f.unlink(missing_ok=True)
                    if not download(vid):
                        print(f"download failed (attempt {attempt})")
                        time.sleep(10)
                        continue
                    f = existing_file(vid)
                    if f is None:
                        print("yt-dlp reported success but no file is there")
                        continue
                ok, why = verify(f, dur)
                if not ok:  # a just-written file can fail its seeks for a few seconds (index not flushed yet): look again
                    time.sleep(30)
                    ok, why = verify(f, dur)
                (VODS / f"{vid}.{'ok' if ok else 'bad'}").write_text(why, encoding="utf-8")
                verdict = ("OK   " if ok else "BAD  ") + f.name + ": " + why
                print(verdict)
                if ok:
                    break
            if verdict is None:
                print(f"giving up on {vid} for this run")
            n += 1
        print(f"\n{n} handled this run. The watcher reads each verified file and moves it to {TO_DELETE} when done.")
    except KeyboardInterrupt:
        print("\nstopped by you; a half-done .part resumes next time")
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    main()
