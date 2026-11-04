r"""Sample frames from a YouTube VOD (a past stream) and OCR them for anything that names or configures the strategy:
the Strategies tab's "Strategy" column, a Strategy Parameters / Properties dialog, the Control Center, chart indicator labels.

Usage (screenread venv):  python -X utf8 vod_frames.py VIDEO_ID [--every 600] [--start 0] [--end 0] [--out D:\stream_capture\DaCaptainTrader_\_dig]
Writes <out>\<id>\<hhmmss>.jpg + .txt for frames with hits (always keeps every 6th frame as a layout sample) and <id>_hits.txt.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import cv2

from screenread.api import ocr_pixels, ReadOptions
from screenread.geometry import Rect

SYSTEM_PYTHON = r"C:\Users\linde\AppData\Local\Programs\Python\Python312\python.exe"
KEYWORDS = {
    "strategy_col": r"\bStrategy\b", "strategies_tab": r"\bStrategies\b", "params_dialog": r"Strategy Parameters|NinjaScript Strategy|Properties",
    "ninjascript": r"NinjaScript", "setup_words": r"\bContracts\b|\bProfit target\b|\bStop loss\b|\bTrail\b|\bTicks\b|\bCalculate\b|\bOn bar close\b|\bOn each tick\b|\bOn price change\b",
    "dialog_buttons": r"\bApply\b|\bCancel\b|\bOK\b", "account_tab": r"\bAccounts\b|\bOrders\b|\bExecutions\b|\bPositions\b",
    "label_line": r"Trend lines\(|MAMA\(|Price line\(|Range counter\(", "atm": r"\bATM\b|ATM Strategy",
    "indicator_other": r"\bEMA\(|\bSMA\(|\bATR\(|\bRSI\(|\bVWAP\b|\bBollinger|\bKeltner|\bSupertrend|\bDonchian|\bHMA\(|\bADX\(",
}
USUAL = {"label_line", "account_tab"}


def resolve(video_id: str) -> tuple[str, float] | None:
    cmd = [SYSTEM_PYTHON, "-X", "utf8", "-m", "yt_dlp", "-j", "--no-warnings", f"https://www.youtube.com/watch?v={video_id}"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        print("resolve failed:", (p.stderr or "")[-300:], file=sys.stderr)
        return None
    info = json.loads(p.stdout.splitlines()[-1])
    fm = [f for f in info.get("formats", []) if f.get("vcodec", "none") != "none" and f.get("height")
          and (f.get("protocol") or "").startswith("http") and (f.get("ext") == "mp4")]
    pick = [f for f in fm if f.get("height") == 720] or [f for f in fm if (f.get("height") or 0) <= 1080]
    if not pick:
        fm = [f for f in info.get("formats", []) if f.get("vcodec", "none") != "none" and f.get("height")]
        pick = [f for f in fm if f.get("height") == 720] or fm
    if not pick:
        return None
    f = sorted(pick, key=lambda x: x.get("height") or 0)[-1]
    return f["url"], float(info.get("duration") or 0)


def ocr_text(img):
    H, W = img.shape[:2]
    lines, _, _, _ = ocr_pixels(img, Rect(left=0, top=0, width=W, height=H), ReadOptions(engine="windows", scale=2.0))
    return "\n".join(ln.text for ln in lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--every", type=float, default=600)
    ap.add_argument("--start", type=float, default=0)
    ap.add_argument("--end", type=float, default=0)
    ap.add_argument("--out", default=r"D:\stream_capture\DaCaptainTrader_\_dig")
    args = ap.parse_args()
    r = resolve(args.video_id)
    if not r:
        print("no stream url")
        return
    url, dur = r
    end = args.end or dur
    out = Path(args.out) / args.video_id
    out.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        print("open failed")
        return
    print(f"{args.video_id}: {dur/3600:.1f} h, sampling every {args.every:.0f} s from {args.start:.0f} to {end:.0f}")
    hits_out = open(Path(args.out) / f"{args.video_id}_hits.txt", "a", encoding="utf-8")
    t = args.start
    n = 0
    t0 = time.time()
    while t < end:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, img = cap.read()
        if not ok or img is None:
            t += args.every
            continue
        text = ocr_text(img)
        found = {k: sorted(set(m.group(0) for m in re.finditer(p, text))) for k, p in KEYWORDS.items()}
        found = {k: v for k, v in found.items() if v}
        interesting = set(found) - USUAL
        stamp = f"{int(t)//3600:02d}{(int(t)%3600)//60:02d}{int(t)%60:02d}"
        if interesting or n % 6 == 0:
            cv2.imwrite(str(out / f"{stamp}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            (out / f"{stamp}.txt").write_text(text, encoding="utf-8")
        if interesting:
            line = f"{args.video_id} {stamp} " + " | ".join(f"{k}: {', '.join(v)[:60]}" for k, v in found.items() if k in interesting)
            print("  HIT", line)
            hits_out.write(line + "\n")
            hits_out.flush()
        n += 1
        t += args.every
    cap.release()
    print(f"done: {n} frames in {time.time()-t0:.0f} s")


if __name__ == "__main__":
    main()
