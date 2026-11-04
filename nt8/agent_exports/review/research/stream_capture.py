r"""Record the trades shown on a YouTube live stream of a NinjaTrader screen (Da Captain Trader, 24/5 NQ).

Runs for days unattended. Resolves the channel's current live stream (https://www.youtube.com/@<handle>/live, the
video id changes every stream), pulls the 720p HLS stream with yt-dlp (system Python) + OpenCV, and reads three areas
of every frame with Windows OCR (screenread's engine, so run this under screenread's venv):
  - the Chart Trader panel: "Flat" or "<qty> <avg price> <unrealized>", the qty box colour (green long / red short),
    and the realized P&L line below it ("$6,025.00") - the primary trade log (one account);
  - the Strategies grid: per row Parameters / Position / Acct. position / Avg. price / Unrealized / Realized
    (the other copies of the strategy);
  - the chart's order labels (EntryLong 1 @ 28596.50 ...) - raw text only, noisy at 720p.
Areas are found by OCR anchors ("Rev", "Close", "Instrument", "Position", ...) once a minute, so a moved window is
followed; a screen without them (ad, BRB, another layout) logs "no layout" and emits nothing.

Output (D:\stream_capture\<handle>\<ET date>\):
  events.jsonl     one line per state change (entry / exit / grid row change), after 2 identical reads 1 s apart
  frames\*.png     the frame of every event (+ one an hour) for verification
  state.csv        the parsed panel + grid state every 10 s
  chart_labels.txt every distinct order label seen on the chart, first-seen time
  delay.csv        once a minute: wall clock minus the HLS playlist's PROGRAM-DATE-TIME (how far behind live we read)
D:\stream_capture\status.json is rewritten every 30 s (watch it); capture.log is the log.
Times are UTC in files (recv_utc = PC clock when the frame was read; the stream itself is ~22 s behind the market).

Usage: pythonw stream_capture.py [--handle @DaCaptainTrader_] [--seconds N (test run)] [--out D:\stream_capture]
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import logging.handlers
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cv2
import numpy as np

from screenread.api import ocr_pixels, ReadOptions
from screenread.geometry import Rect

SYSTEM_PYTHON = r"C:\Users\linde\AppData\Local\Programs\Python\Python312\python.exe"  # has yt_dlp (user site)
try:
    ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001  (no tzdata in this venv; the PC's clock is Eastern)
    ET = None
log = logging.getLogger("capture")


# ----------------------------------------------------------------------------- stream
def resolve_live(handle: str) -> dict | None:
    """The channel's current live stream: {id, title, url (720p h264 HLS), height, format}; None when not live."""
    cmd = [SYSTEM_PYTHON, "-X", "utf8", "-m", "yt_dlp", "-j", "--skip-download", "--no-warnings",
           f"https://www.youtube.com/{handle}/live"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=90,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        log.warning("yt_dlp timed out")
        return None
    if p.returncode != 0 or not p.stdout.strip():
        msg = (p.stderr or "").strip().splitlines()
        log.info("not live / resolve failed: %s", msg[-1] if msg else "no output")
        return None
    info = json.loads(p.stdout.splitlines()[-1])
    if not info.get("is_live"):
        log.info("stream %s is not live", info.get("id"))
        return None
    fm = [f for f in info.get("formats", []) if f.get("vcodec", "none") != "none" and f.get("height")
          and "m3u8" in (f.get("protocol") or "")]
    pick = ([f for f in fm if f.get("height") == 720 and "avc" in (f.get("vcodec") or "")]
            or [f for f in fm if f.get("height") == 720] or sorted(fm, key=lambda f: f.get("height") or 0))
    if not pick:
        log.warning("no HLS video format for %s", info.get("id"))
        return None
    f = pick[-1]
    return {"id": info["id"], "title": info.get("title", ""), "url": f["url"], "height": f.get("height"),
            "format": f.get("format_id")}


def playlist_delay(url: str) -> float | None:
    """Seconds between now and the newest segment's PROGRAM-DATE-TIME in the media playlist."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
            text = r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        log.debug("playlist fetch failed: %s", e)
        return None
    # one PROGRAM-DATE-TIME may stand at the top of the window; later segments carry only EXTINF durations
    edge = None
    for line in text.splitlines():
        if line.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            edge = datetime.fromisoformat(line.split(":", 1)[1].strip().replace("Z", "+00:00"))
        elif line.startswith("#EXTINF:") and edge is not None:
            try:
                edge += timedelta(seconds=float(line[8:].split(",")[0]))
            except ValueError:
                pass
    if edge is None:
        return None
    return (datetime.now(timezone.utc) - edge).total_seconds()


# ----------------------------------------------------------------------------- OCR helpers
def rect(x: int, y: int, w: int, h: int) -> Rect:
    return Rect(left=int(x), top=int(y), width=int(w), height=int(h))


def ocr(img: np.ndarray, x: int, y: int, w: int, h: int, scale: float = 2.0, engine: str = "windows"):
    """OCR of a crop (Windows OCR, or RapidOCR for tiny cells); lines with boxes in frame coordinates."""
    H, W = img.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = min(w, W - x), min(h, H - y)
    if w < 8 or h < 8:
        return []
    lines, _, _, _ = ocr_pixels(img[y:y + h, x:x + w], rect(x, y, w, h), ReadOptions(engine=engine, scale=scale))
    return lines


def words_of(lines):
    for ln in lines:
        if ln.words:
            for wd in ln.words:
                yield wd.text, wd.box
        else:
            yield ln.text, ln.box


def find_word(lines, pattern: str, exclude_line: str | None = None):
    """Leftmost word (else first line) whose text matches the regex; returns its box.
    exclude_line: skip words whose line matches this regex (e.g. the "position" of "Acct. position")."""
    rx = re.compile(pattern, re.I)
    ex = re.compile(exclude_line, re.I) if exclude_line else None
    found = []
    for ln in lines:
        if ex and ex.search(ln.text):
            continue
        for text, box in words_of([ln]):
            if rx.fullmatch(text.strip()):
                found.append(box)
    if found:
        return min(found, key=lambda b: b.left)
    for ln in lines:
        if rx.search(ln.text) and not (ex and ex.search(ln.text)):
            return ln.box
    return None


# ----------------------------------------------------------------------------- layout
@dataclass
class Layout:
    panel: tuple[int, int, int, int] | None = None   # Chart Trader's position area: between Rev/Close and Instrument
    #   flat:        "Flat | Entry" row, then the realized P&L ("$6,025.00")
    #   in position: "<qty> | <avg price>" row (qty box green = long, red = short), then the unrealized ("($50.00)")
    grid: tuple[int, int, int, int] | None = None    # the Strategies grid incl. header
    grid_cols: dict[str, tuple[int, int]] = field(default_factory=dict)  # column name -> (x0, x1)
    chart: tuple[int, int, int, int] | None = None
    found_at: float = 0.0

    def ok(self) -> bool:
        return self.panel is not None


GRID_HEADERS = [("instrument", r"Instrument"), ("series", r"Data\s*series"), ("params", r"Paramet\w*"),
                ("position", r"Position"), ("acct", r"Acct\.?\s*position"), ("avg", r"Avg\.?\s*price"),
                ("unrealized", r"Unrealized"), ("realized", r"Realized"), ("enabled", r"Enabled")]


def find_layout(img: np.ndarray) -> Layout:
    H, W = img.shape[:2]
    lay = Layout(found_at=time.time())
    lines = ocr(img, 0, 0, W, H, scale=2.0)
    rev, close, instr = find_word(lines, r"Rev"), find_word(lines, r"Close"), find_word(lines, r"Instrument")
    # Chart Trader: "Rev" and "Close" on one row, "Instrument" below; the position row sits between them.
    if rev and close and instr and abs(rev.top - close.top) < 12 and instr.top > close.bottom:
        x0 = rev.left - 10
        x1 = close.right + 10
        y0 = close.bottom + 4
        y1 = instr.top - 4
        if y1 - y0 > 20:
            lay.panel = (x0, y0, x1 - x0, y1 - y0)
    # Strategies grid: header words on one row
    heads = {}
    for key, pat in GRID_HEADERS:
        b = find_word(lines, pat, exclude_line=r"Acct" if key == "position" else None)
        if b is not None:
            heads[key] = b
    if "position" in heads and len(heads) >= 5:
        hy = int(np.median([b.top for b in heads.values()]))
        heads = {k: b for k, b in heads.items() if abs(b.top - hy) < 12}
        if "acct" in heads and "position" in heads and heads["acct"].left <= heads["position"].left:
            del heads["acct"]  # the "Position" word found was the one inside "Acct. position"; keep the layout sane
        order = sorted(heads.items(), key=lambda kv: kv[1].left)
        cols = {}
        for i, (k, b) in enumerate(order):
            x0 = b.left - 6 if i == 0 else (order[i - 1][1].right + b.left) // 2
            x1 = (b.right + order[i + 1][1].left) // 2 if i + 1 < len(order) else min(W, b.right + 120)
            cols[k] = (x0, x1)
        gx0 = order[0][1].left - 8
        gx1 = max(x1 for _, x1 in cols.values())
        gy0 = hy - 4
        lay.grid = (gx0, gy0, gx1 - gx0, H - gy0)
        lay.grid_cols = cols
        # the chart is everything above the grid header and left of the panel
        chart_right = (rev.left - 10) if rev else W
        lay.chart = (0, 0, chart_right, max(40, gy0 - 40))
    elif rev:
        lay.chart = (0, 0, rev.left - 10, int(H * 0.65))
    return lay


# ----------------------------------------------------------------------------- parsing
MONEY = re.compile(r"\(?-?\$?\s*([\d,]+\.\d{2})\)?")
PRICE = re.compile(r"(?<!\d)(\d{5})[.,]?(\d{2})(?!\d)")  # 28391.25, also OCR's "2839125" without the point


def money(text: str) -> float | None:
    m = MONEY.search(text.replace(" ", ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -v if "(" in text or text.strip().startswith("-") else v


def side_from_colour(img: np.ndarray, box: tuple[int, int, int, int]) -> tuple[str, tuple[int, int, int]]:
    """Mean colour of the qty box (left 35 % of the upper row of the position area): green = long, red = short."""
    x, y, w, h = box
    crop = img[y + 4:y + int(h * 0.5), x + 4:x + int(w * 0.35)]
    if crop.size == 0:
        return "?", (0, 0, 0)
    b, g, r = [int(v) for v in crop.reshape(-1, 3).mean(axis=0)]
    if g > 110 and g > r + 40 and g > b + 40:
        return "long", (r, g, b)
    if r > 110 and r > g + 40 and r > b + 40:
        return "short", (r, g, b)
    return "?", (r, g, b)


def parse_panel(img: np.ndarray, lay: Layout) -> dict:
    """{'state': 'flat'|'pos'|'unknown', 'side', 'qty', 'avg', 'unreal', 'realized', 'raw'}"""
    out = {"state": "unknown", "side": None, "qty": None, "avg": None, "unreal": None, "realized": None, "raw": ""}
    if not lay.panel:
        return out
    x, y, w, h = lay.panel
    lines = ocr(img, x, y, w, h, scale=2.0)
    raw = " | ".join(ln.text for ln in lines)
    out["raw"] = raw
    if re.search(r"\bFlat\b", raw, re.I):
        out["state"] = "flat"
        out["realized"] = money(raw)  # the P&L line under "Flat | Entry"
    else:
        compact = raw.replace(" ", "")
        pm = PRICE.search(compact)
        qm = re.search(r"(?<![\d.$,])(\d{1,2})(?![\d.,])", raw)
        if pm:
            out["state"] = "pos"
            out["avg"] = float(f"{pm.group(1)}.{pm.group(2)}")
            out["qty"] = int(qm.group(1)) if qm else None
            out["unreal"] = money(compact[pm.end():])  # the unrealized line under the qty / price row
            out["side"], out["rgb"] = side_from_colour(img, lay.panel)
    return out


def parse_grid(img: np.ndarray, lay: Layout) -> list[dict]:
    cols = lay.grid_cols
    if not lay.grid or "position" not in cols or not ("instrument" in cols or "series" in cols):
        return []
    x, y, w, h = lay.grid
    lines = ocr(img, x, y, w, h, scale=3.0)
    # rows: words in the instrument column that look like an instrument ("NQ DEC26"), or - when OCR missed the
    # "Instrument" header - in the data-series column ("1 Minute"); row y = their centre
    if "instrument" in cols:
        ix0, ix1 = cols["instrument"]
        row_rx = re.compile(r"[A-Z]{1,3}\s*[A-Z]{3}\d{2}|DEC|MAR|JUN|SEP", re.I)
    else:
        ix0, ix1 = cols["series"]
        row_rx = re.compile(r"Minute|Min\b|Tick|Range|Second|Day|Renko", re.I)
    rows = []
    for text, box in words_of(lines):
        cx = box.left + box.width / 2
        if ix0 <= cx < ix1 and row_rx.search(text) and box.top > y + 12:
            cy = box.top + box.height / 2
            if all(abs(cy - r["cy"]) > 7 for r in rows):
                rows.append({"cy": cy, "h": box.height, "instrument": text.strip()})
    rows.sort(key=lambda r: r["cy"])
    for r in rows:
        for k in cols:
            r.setdefault(k, "")
    for text, box in words_of(lines):
        cx = box.left + box.width / 2
        cy = box.top + box.height / 2
        row = min(rows, key=lambda r: abs(r["cy"] - cy), default=None)
        if row is None or abs(row["cy"] - cy) > max(6, row["h"] * 0.6):
            continue
        for k, (x0, x1) in cols.items():
            if x0 <= cx < x1 and k != "instrument":
                row[k] = (row[k] + " " + text.strip()).strip()
    # second pass: RapidOCR on the Position / Acct. position strip - Windows OCR reads the small "1 S" cells as
    # "-" or nothing (2026-11-04 frame 12:50:13), RapidOCR reads them ("1S")
    if rows:
        px0 = cols["position"][0]
        px1 = cols["acct"][1] if "acct" in cols else cols["position"][1]
        try:
            plines = ocr(img, px0, y + 12, px1 - px0, h - 12, scale=3.0, engine="rapid")
        except Exception as e:  # noqa: BLE001
            log.debug("rapid pass failed: %s", e)
            plines = []
        for text, box in words_of(plines):
            cx = box.left + box.width / 2
            cy = box.top + box.height / 2
            row = min(rows, key=lambda r: abs(r["cy"] - cy))
            if abs(row["cy"] - cy) > max(6, row["h"] * 0.6):
                continue
            k = "position" if cx < cols["position"][1] else "acct"
            cell = text.strip().upper().replace("I", "1").replace("|", "1").replace(" ", "")
            if re.fullmatch(r"\d+[LS]|-", cell):
                row[k] = cell
    out = []
    for r in rows:
        pos = r.get("position", "").replace(" ", "").upper().replace("I", "1").replace("|", "1")
        side = None
        if re.fullmatch(r"\d+L", pos):
            side = "long"
        elif re.fullmatch(r"\d+S", pos):
            side = "short"
        elif pos in ("-", "—", "_", "."):
            side = "flat"
        elif pos == "":
            # OCR sometimes drops the small "1 S" / "1 L" cell; a quoted average price still means a position
            side = "?" if PRICE.search(r.get("avg", "").replace(" ", "")) else "flat"
            if side == "?":
                pos = "1?"
        qty_m = re.match(r"(\d+)", pos)
        out.append({"params": r.get("params", ""), "position": r.get("position", ""), "side": side or "?",
                    "qty": int(qty_m.group(1)) if qty_m and side in ("long", "short") else 0,
                    "acct": r.get("acct", ""), "avg": r.get("avg", ""), "unrealized": r.get("unrealized", ""),
                    "realized": r.get("realized", ""), "y": int(r["cy"])})
    return out


LABEL = re.compile(r"(Entry|Stop|Target)\s*(Long|Short)|\d\s*@\s*\d{4,5}[.,]?\d{0,2}", re.I)


def parse_chart_labels(img: np.ndarray, lay: Layout) -> list[str]:
    if not lay.chart:
        return []
    x, y, w, h = lay.chart
    lines = ocr(img, x, y, w, h, scale=3.0)
    return [ln.text.strip() for ln in lines if LABEL.search(ln.text)]


# ----------------------------------------------------------------------------- recorder
class Recorder:
    def __init__(self, out: Path, handle: str):
        self.base = out / handle.lstrip("@")
        self.status_path = out / "status.json"
        self.handle = handle
        self.day = None
        self.events = self.state = self.labels = self.delay = None
        self.seen_labels: set[str] = set()
        self.counts = {"frames": 0, "events": 0, "ocr_ms": 0.0}
        self.last_panel: dict | None = None
        self.pending_panel: dict | None = None
        self.last_grid_sig: str | None = None
        self.pending_grid_sig: str | None = None
        self.last_hour_frame = 0.0
        self.last_state_row = 0.0
        self.stream: dict | None = None
        self.lock = threading.Lock()

    def folder(self) -> Path:
        d = (datetime.now(ET) if ET else datetime.now()).strftime("%Y-%m-%d")
        if d != self.day:
            self.day = d
            p = self.base / d
            (p / "frames").mkdir(parents=True, exist_ok=True)
            for f in (self.events, self.state, self.labels, self.delay):
                if f:
                    f.close()
            self.events = open(p / "events.jsonl", "a", encoding="utf-8")
            new = not (p / "state.csv").exists() or (p / "state.csv").stat().st_size == 0
            self.state = open(p / "state.csv", "a", encoding="utf-8", newline="")
            if new:
                self.state.write("recv_utc,state,side,qty,avg,unreal,realized,grid_positions,raw\n")
            self.labels = open(p / "chart_labels.txt", "a", encoding="utf-8")
            self.delay = open(p / "delay.csv", "a", encoding="utf-8")
            self.seen_labels = set()
            if (p / "chart_labels.txt").exists():
                for line in (p / "chart_labels.txt").read_text(encoding="utf-8").splitlines():
                    if "\t" in line:
                        self.seen_labels.add(line.split("\t", 1)[1])
        return self.base / self.day

    @staticmethod
    def now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def save_frame(self, img: np.ndarray, tag: str) -> str:
        name = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + f"_{tag}.png"
        cv2.imwrite(str(self.folder() / "frames" / name), img, [cv2.IMWRITE_PNG_COMPRESSION, 6])
        return name

    def event(self, img: np.ndarray, kind: str, **data):
        self.folder()
        rec = {"recv_utc": self.now(), "kind": kind, "stream": (self.stream or {}).get("id"), **data}
        rec["frame"] = self.save_frame(img, kind)
        self.events.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.events.flush()
        self.counts["events"] += 1
        log.info("EVENT %s %s", kind, {k: v for k, v in data.items() if k not in ("grid", "raw")})

    def on_frame(self, img: np.ndarray, lay: Layout, panel: dict, grid: list[dict] | None, labels: list[str] | None):
        self.folder()
        self.counts["frames"] += 1
        t = time.time()
        # --- panel state machine (2 identical reads in a row before an event)
        sig = (panel["state"], panel.get("side"), panel.get("qty"), panel.get("avg"))
        if panel["state"] != "unknown":
            if self.pending_panel and (self.pending_panel["sig"] == sig):
                if self.last_panel is None or self.last_panel["sig"] != sig:
                    prev = self.last_panel
                    if panel["state"] == "pos" and (prev is None or prev["sig"][0] != "pos" or prev["sig"][1:] != sig[1:]):
                        self.event(img, "entry", side=panel.get("side"), qty=panel.get("qty"), avg=panel.get("avg"),
                                   unreal=panel.get("unreal"), realized=panel.get("realized"), rgb=panel.get("rgb"),
                                   prev=prev["sig"] if prev else None, raw=panel["raw"], grid=grid)
                    elif panel["state"] == "flat" and prev is not None and prev["sig"][0] == "pos":
                        self.event(img, "exit", prev_side=prev["sig"][1], prev_qty=prev["sig"][2], prev_avg=prev["sig"][3],
                                   realized_before=prev.get("realized"), realized=panel.get("realized"),
                                   raw=panel["raw"], grid=grid)
                    elif panel["state"] == "flat" and prev is None:
                        self.event(img, "flat", realized=panel.get("realized"), raw=panel["raw"], grid=grid)
                    self.last_panel = {"sig": sig, "realized": panel.get("realized")}
                elif panel.get("realized") is not None and self.last_panel.get("realized") not in (None, panel["realized"]):
                    self.event(img, "realized_change", before=self.last_panel["realized"], after=panel["realized"],
                               raw=panel.get("raw_pnl", ""), grid=grid)
                    self.last_panel["realized"] = panel["realized"]
            self.pending_panel = {"sig": sig}
        # --- grid: any row's Position column changes
        if grid is not None:
            # position side + qty per row only: the params / avg price text flickers under OCR and is kept in the rows
            gsig = ";".join(f"{r['side']}{r['qty']}" for r in grid)
            if grid and self.pending_grid_sig == gsig and gsig != self.last_grid_sig:
                if self.last_grid_sig is not None:
                    self.event(img, "grid_change", rows=grid, before=self.last_grid_sig)
                self.last_grid_sig = gsig
            self.pending_grid_sig = gsig
        # --- chart labels, first seen
        if labels:
            for lb in labels:
                if lb not in self.seen_labels:
                    self.seen_labels.add(lb)
                    self.labels.write(f"{self.now()}\t{lb}\n")
            self.labels.flush()
        # --- periodic state row / hourly frame
        if t - self.last_state_row >= 10:
            self.last_state_row = t
            gp = " ".join(f"{r['params']}={r['side']}{r['qty']}" for r in (grid or []))
            w = csv.writer(self.state)
            w.writerow([self.now(), panel["state"], panel.get("side") or "", panel.get("qty") or "", panel.get("avg") or "",
                        panel.get("unreal") if panel.get("unreal") is not None else "",
                        panel.get("realized") if panel.get("realized") is not None else "", gp, panel["raw"][:120]])
            self.state.flush()
        if t - self.last_hour_frame >= 3600:
            self.last_hour_frame = t
            self.save_frame(img, "hourly")

    def write_delay(self, seconds: float | None):
        self.folder()
        self.delay.write(f"{self.now()},{'' if seconds is None else round(seconds, 1)}\n")
        self.delay.flush()

    def status(self, **extra):
        stream = {k: v for k, v in (self.stream or {}).items() if k != "url"} or None  # the url carries the PC's IP
        rec = {"written_utc": self.now(), "pid": os.getpid(), "handle": self.handle, "stream": stream,
               "counts": self.counts, "panel": self.last_panel, "day_folder": str(self.folder()), **extra}
        tmp = self.status_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.status_path)


# ----------------------------------------------------------------------------- main loop
def capture(stream: dict, rec: Recorder, deadline: float | None) -> str:
    """Read the stream until it stops. Returns why."""
    cap = cv2.VideoCapture(stream["url"], cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return "open failed"
    rec.stream = stream
    lay = Layout()
    last_ok = time.time()
    last_panel_t = last_grid_t = last_chart_t = last_status_t = last_delay_t = 0.0
    fails = 0
    while True:
        if deadline and time.time() > deadline:
            cap.release()
            return "deadline"
        ok = cap.grab()
        if not ok:
            fails += 1
            if time.time() - last_ok > 45:
                cap.release()
                return "no frames for 45 s"
            time.sleep(0.2)
            continue
        fails = 0
        last_ok = time.time()
        t = last_ok
        if t - last_panel_t < 1.0:
            continue  # process one frame a second; grab() keeps the decoder at the live edge
        ok, img = cap.retrieve()
        if not ok or img is None:
            continue
        last_panel_t = t
        try:
            if not lay.ok() or t - lay.found_at > 60:
                new = find_layout(img)
                if new.ok():
                    if not lay.ok():
                        log.info("layout found: panel %s grid %s cols %s", new.panel, new.grid, list(new.grid_cols))
                    lay = new
                else:
                    lay = Layout(found_at=t)
                    if int(t) % 60 < 2:
                        log.info("no layout on screen")
            if not lay.ok():
                if t - last_status_t > 30:
                    last_status_t = t
                    rec.status(layout=False)
                continue
            t0 = time.perf_counter()
            panel = parse_panel(img, lay)
            grid = labels = None
            if t - last_grid_t >= 2:
                last_grid_t = t
                grid = parse_grid(img, lay)
            if t - last_chart_t >= 10:
                last_chart_t = t
                labels = parse_chart_labels(img, lay)
            rec.counts["ocr_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            rec.on_frame(img, lay, panel, grid, labels)
        except Exception:  # noqa: BLE001
            log.exception("frame processing failed")
        if t - last_delay_t >= 60:
            last_delay_t = t
            threading.Thread(target=lambda: rec.write_delay(playlist_delay(stream["url"])), daemon=True).start()
        if t - last_status_t >= 30:
            last_status_t = t
            rec.status(layout=True, panel_now=panel.get("state"), ocr_ms=rec.counts["ocr_ms"])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--handle", default="@DaCaptainTrader_")
    ap.add_argument("--out", default=r"D:\stream_capture")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (test run)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(out / "capture.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[handler] + ([logging.StreamHandler(sys.stdout)] if args.seconds else []))
    lock = out / "capture.lock"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip())
            if pid != os.getpid() and subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
                                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.find(str(pid)) >= 0:
                log.error("another capture is running (pid %s); exiting", pid)
                return
        except ValueError:
            pass
    lock.write_text(str(os.getpid()))
    rec = Recorder(out, args.handle)
    deadline = time.time() + args.seconds if args.seconds else None
    log.info("capture start handle=%s out=%s", args.handle, out)
    backoff = 30
    try:
        while True:
            if deadline and time.time() > deadline:
                break
            stream = resolve_live(args.handle)
            if not stream:
                rec.stream = None
                rec.status(layout=False, waiting="not live")
                time.sleep(min(120, max(10, (deadline - time.time()) if deadline else 120)))
                continue
            log.info("live: %s %s (%sp %s)", stream["id"], stream["title"][:60], stream["height"], stream["format"])
            why = capture(stream, rec, deadline)
            log.info("capture ended: %s", why)
            if why == "deadline":
                break
            time.sleep(5 if why != "open failed" else backoff)
    finally:
        rec.status(layout=False, stopped=True)
        try:
            lock.unlink()
        except OSError:
            pass
        log.info("capture stop")


if __name__ == "__main__":
    main()
