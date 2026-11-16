from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

FF = r"C:\Users\me\AppData\Roaming\Python\Python312\site-packages\imageio_ffmpeg\binaries\ffmpeg.exe"
VODS = Path(r"D:\stream_capture\_to_delete")
BASE = Path(r"D:\stream_capture\DaCaptainTrader_\vod")
AX_X0, AX_X1, AX_Y0, AX_Y1 = 918, 975, 0, 500
W, H = AX_X1 - AX_X0, AX_Y1 - AX_Y0
PRICE = re.compile(r"(\d{5})[.,:](\d{2})")

_ocr = None


def ocr_box(img: np.ndarray) -> float | None:
    global _ocr
    import cv2
    if _ocr is None:
        os.environ.setdefault("OMP_NUM_THREADS", "2")
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    if g.mean() > 127:
        pass
    else:
        g = 255 - g
    g = cv2.copyMakeBorder(g, 8, 8, 8, 8, cv2.BORDER_REPLICATE)
    res, _ = _ocr(cv2.cvtColor(g, cv2.COLOR_GRAY2BGR), use_det=False, use_cls=False, use_rec=True)
    if not res:
        return None
    txt = "".join(str(r[0]) for r in res).replace(" ", "")
    m = PRICE.search(txt)
    if not m:
        return None
    v = float(f"{m.group(1)}.{m.group(2)}")
    return v if abs(v * 4 - round(v * 4)) < 1e-6 else None


def boxes(ax: np.ndarray, kind: str) -> list[tuple[int, int]]:
    b, g, r = (ax[:, :, i].astype(int) for i in range(3))
    if kind == "stp":
        m = (r > 215) & (g > 140) & (g < 215) & (b > 150) & (b < 225) & (r - g > 25)
    else:
        m = (r < 110) & (g > 190) & (b > 190)
    rows = m.sum(axis=1) >= 0.45 * ax.shape[1]
    spans, start = [], None
    for y, on in enumerate(list(rows) + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            if y - start >= 8:
                spans.append((start, y))
            start = None
    return spans


def _text_mask(crop: np.ndarray) -> np.ndarray:
    import cv2
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    return g < 110


def run_video(vid: str) -> str:
    import av
    src = VODS / f"{vid}.mp4"
    out_dir = BASE / vid
    if not src.exists() or not out_dir.exists():
        return f"{vid}: no video or no read folder"
    out = out_dir / "orders.csv"
    tmp = out_dir / "orders.csv.tmp"
    c = av.open(str(src))
    s = c.streams.video[0]
    s.codec_context.skip_frame = "NONKEY"
    s.thread_type = "AUTO"
    prev: dict[str, list[tuple[np.ndarray, float | None]]] = {"stp": [], "lmt": []}
    n = rows_out = ocr_calls = 0
    t0 = time.time()
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["p", "stp", "lmt"])
        for f in c.decode(s):
            n += 1
            p = float(f.pts * s.time_base)
            img = f.to_ndarray(format="bgr24")
            if img.shape[0] != 720 or img.shape[1] != 1280:
                continue
            ax = img[AX_Y0:AX_Y1, AX_X0:AX_X1]
            found = {}
            for kind in ("stp", "lmt"):
                vals, cur = [], []
                for y0, y1 in boxes(ax, kind):
                    crop = ax[max(0, y0 - 1):min(H, y1 + 1)]
                    mask = _text_mask(crop)
                    val, reused = None, False
                    for pm, pv in prev[kind]:
                        if pm.shape == mask.shape and (pm ^ mask).sum() <= max(3, 0.02 * mask.sum()):
                            val, reused = pv, True
                            break
                    if not reused:
                        val = ocr_box(crop)
                        ocr_calls += 1
                    cur.append((mask, val))
                    if val is not None:
                        vals.append(val)
                prev[kind] = cur
                found[kind] = vals
            if found["stp"] or found["lmt"]:
                w.writerow([f"{p:.2f}", ";".join(f"{v:.2f}" for v in found["stp"]), ";".join(f"{v:.2f}" for v in found["lmt"])])
                rows_out += 1
    c.close()
    os.replace(tmp, out)
    return f"{vid}: {n} keyframes, {rows_out} with orders, {ocr_calls} OCR calls, {time.time() - t0:.0f} s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="*")
    ap.add_argument("--procs", type=int, default=6)
    a = ap.parse_args()
    vids = a.videos or sorted(p.name for p in BASE.iterdir() if p.is_dir() and (VODS / f"{p.name}.mp4").exists())
    print(f"{len(vids)} videos, {a.procs} processes", flush=True)
    with ProcessPoolExecutor(a.procs) as ex:
        futs = {ex.submit(run_video, v): v for v in vids}
        for f in as_completed(futs):
            try:
                print(f.result(), flush=True)
            except Exception as e:
                print(f"{futs[f]}: FAILED {e!r}", flush=True)


if __name__ == "__main__":
    main()
