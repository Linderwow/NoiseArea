r"""Scan YouTube for live futures-trading streams and report which show a trading platform the capture can read.

Search (innertube, "Live" filter) -> up to N live streams -> for each: resolve the HLS stream with yt-dlp (system
Python), grab a frame after ~3 s, OCR the whole frame (Windows OCR), match platform keywords, save the frame.
Run under screenread's venv:  pythonw/python -X utf8 stream_scan.py [--max 18] [--out D:\stream_capture\_scan]
Writes <out>\<date>\<videoId>.jpg + .txt (OCR text) and SCAN_<date>.md (the table), prints the table.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import cv2

from screenread.api import ocr_pixels, ReadOptions
from screenread.geometry import Rect

SYSTEM_PYTHON = r"C:\Users\linde\AppData\Local\Programs\Python\Python312\python.exe"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
QUERIES = ["live futures trading", "NQ futures live", "ES futures live trading", "MNQ live trading",
           "nasdaq futures live stream", "topstep live trading", "apex trader funding live", "day trading futures live",
           "futures scalping live", "ninjatrader live", "tradovate live trading", "topstepx live", "funded futures live"]
PLATFORMS = {
    "NinjaTrader": ["ninjatrader", "chart trader", "buy mkt", "sell mkt", "buy ask", "sell bid", "superdom", "range counter",
                    "ninjascript", "strategies", "acct. position"],
    "Tradovate": ["tradovate", "flatten", "buy mkt", "sell mkt", "reverse", "tradovate.com"],
    "TopstepX": ["topstepx", "topstep x", "flatten all", "projectx"],
    "TradingView": ["tradingview", "trading panel", "paper trading", "order panel", "pine"],
    "Sierra Chart": ["sierra chart", "trade window", "sierrachart"],
    "Quantower": ["quantower"],
    "Bookmap": ["bookmap"],
    "thinkorswim": ["thinkorswim", "active trader", "tos"],
    "ATAS": ["atas"],
    "MotiveWave": ["motivewave"],
    "Jigsaw": ["jigsaw", "daytradr"],
    "Rithmic R|Trader": ["rithmic", "r | trader", "r|trader"],
    "MetaTrader": ["metatrader", "mt5", "mt4"],
    "Interactive Brokers": ["interactive brokers", "tws", "ibkr"],
}
PLATFORMS.update({
    "Exness (CFD)": ["exness"], "Lightspeed": ["lightspeed"], "TradeZella": ["tradezella"],
    "NinjaTrader SuperDOM": ["superdom", "ninjatrader"],
})
NT_CHART_TRADER = ["flat", "entry", "rev", "close", "buy mkt", "sell mkt", "instrument", "tif"]
NT_GRID = ["instrument", "data series", "position", "acct. position", "avg. price", "unrealized", "realized"]
MARKETS = {  # what is being traded, from tickers on screen
    "futures": [r"\b(NQ|ES|MNQ|MES|GC|MGC|CL|ZN|ZB|RTY|YM|M2K|SI)[ZHMU]?\d{0,2}\b", r"\bNQ1!|\bES1!|\bGC1!", r"E-mini", r"micro", r"nasdaq 100"],
    "forex / CFD": [r"XAU\s*/?\s*USD", r"\bUSD\s*/?\s*JPY\b", r"\bEUR\s*/?\s*USD\b", r"\bGBP", r"exness", r"\bBTC\s*/?\s*USD"],
    "stocks": [r"\bSPY\b", r"\bQQQ\b", r"\bAMD\b", r"\bNVDA\b", r"\bTSLA\b", r"gappers", r"premarket high", r"\bNASDAQ\b.*\bAMEX\b"],
}


def market_of(text: str) -> str:
    hits = {m: sum(1 for p in pats if re.search(p, text, re.I)) for m, pats in MARKETS.items()}
    m, n = max(hits.items(), key=lambda kv: kv[1])
    return m if n else "?"


def http_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def search_live(q: str) -> list[dict]:
    """Live streams for a query (YouTube's Live filter + the LIVE badge as a second check)."""
    body = {"context": {"client": {"clientName": "WEB", "clientVersion": "2.20261019.01.00", "hl": "en", "gl": "US"}},
            "query": q, "params": "EgJAAQ%3D%3D"}
    out = []
    try:
        j = http_json("https://www.youtube.com/youtubei/v1/search?prettyPrint=false", body)
    except Exception as e:  # noqa: BLE001
        print(f"search failed for {q!r}: {e}", file=sys.stderr)
        return out

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                v = o["videoRenderer"]
                blob = json.dumps(v)
                live = '"style": "LIVE"' in blob or '"label": "LIVE"' in blob or "watching" in blob
                if live:
                    vc = v.get("viewCountText", {})
                    vc = vc.get("simpleText") or "".join(r.get("text", "") for r in vc.get("runs", []))
                    out.append(dict(id=v["videoId"], title="".join(r["text"] for r in v["title"].get("runs", [])),
                                    channel=(v.get("ownerText", {}).get("runs") or [{}])[0].get("text", ""),
                                    watching=int(re.sub(r"[^0-9]", "", vc) or 0), query=q))
                return
            for x in o.values():
                walk(x)
        elif isinstance(o, list):
            for x in o:
                walk(x)
    walk(j)
    return out


def resolve(video_id: str) -> dict | None:
    cmd = [SYSTEM_PYTHON, "-X", "utf8", "-m", "yt_dlp", "-j", "--skip-download", "--no-warnings",
           f"https://www.youtube.com/watch?v={video_id}"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return None
    if p.returncode != 0 or not p.stdout.strip():
        return None
    info = json.loads(p.stdout.splitlines()[-1])
    if not info.get("is_live"):
        return None
    fm = [f for f in info.get("formats", []) if f.get("vcodec", "none") != "none" and f.get("height")
          and "m3u8" in (f.get("protocol") or "")]
    # the sharpest picture up to 1080p for the one-off frame (small panel text reads better than at 720p)
    pick = ([f for f in fm if f.get("height") == 1080 and "avc" in (f.get("vcodec") or "")]
            or [f for f in fm if f.get("height") == 720 and "avc" in (f.get("vcodec") or "")]
            or sorted((f for f in fm if (f.get("height") or 0) <= 1080), key=lambda f: f.get("height") or 0))
    if not pick:
        return None
    f = pick[-1]
    best = max((x.get("height") or 0) for x in fm)
    return {"url": f["url"], "height": f.get("height"), "best_height": best, "handle": info.get("uploader_id") or "",
            "channel_url": info.get("channel_url") or info.get("uploader_url") or "", "viewers": info.get("concurrent_view_count")}


def grab_frame(url: str, seconds: float = 3.0):
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        return None
    t0 = time.time()
    last = None
    n = 0
    while time.time() - t0 < seconds:
        ok, fr = cap.read()
        if not ok:
            break
        n += 1
        last = fr
    cap.release()
    return last


def ocr_text(img) -> str:
    H, W = img.shape[:2]
    lines, _, _, _ = ocr_pixels(img, Rect(left=0, top=0, width=W, height=H), ReadOptions(engine="windows", scale=2.0))
    return "\n".join(ln.text for ln in lines)


def classify(text: str) -> tuple[str, int, list[str], str]:
    low = text.lower()
    scores = {}
    hits = {}
    for name, words in PLATFORMS.items():
        h = [w for w in words if w in low]
        scores[name] = len(h)
        hits[name] = h
    name, score = max(scores.items(), key=lambda kv: kv[1])
    if score == 0:
        return "none seen", 0, [], "no"
    if name == "NinjaTrader":
        ct = sum(1 for w in NT_CHART_TRADER if w in low)
        grid = sum(1 for w in NT_GRID if w in low)
        if ct >= 5 or grid >= 4:
            readable = "YES (Chart Trader)" if ct >= 5 else "YES (Strategies grid)"
            if ct >= 5 and grid >= 4:
                readable = "YES (Chart Trader + grid)"
        else:
            readable = "partial (chart only)"
    else:
        readable = "needs a reader"
    return name, score, hits[name], readable


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=18)
    ap.add_argument("--out", default=r"D:\stream_capture\_scan")
    ap.add_argument("--skip", default="1YoLvEUq5jA", help="video ids already captured, comma-separated")
    args = ap.parse_args()
    day = datetime.now().strftime("%Y-%m-%d")
    out = Path(args.out) / day
    out.mkdir(parents=True, exist_ok=True)
    found = {}
    for q in QUERIES:
        for s in search_live(q):
            found.setdefault(s["id"], s)
        time.sleep(0.5)
    skip = set(args.skip.split(","))
    streams = sorted(found.values(), key=lambda s: -s["watching"])
    print(f"{len(found)} live streams found across {len(QUERIES)} queries; checking up to {args.max}\n")
    rows = []
    for s in streams[: args.max + len(skip)]:
        if s["id"] in skip:
            rows.append({**s, "platform": "NinjaTrader", "readable": "YES (being captured)", "height": 720, "hits": [], "note": "already captured"})
            continue
        info = resolve(s["id"])
        if not info:
            rows.append({**s, "platform": "-", "readable": "not live / no HLS", "height": None, "hits": [], "note": ""})
            print(f"  {s['id']} {s['channel'][:28]:<28} not live or no video stream")
            continue
        img = grab_frame(info["url"])
        if img is None:
            rows.append({**s, "platform": "-", "readable": "no frame", "height": info["height"], "hits": [], "note": ""})
            print(f"  {s['id']} {s['channel'][:28]:<28} no frame")
            continue
        cv2.imwrite(str(out / f"{s['id']}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
        text = ocr_text(img)
        (out / f"{s['id']}.txt").write_text(text, encoding="utf-8")
        platform, score, hits, readable = classify(text)
        market = market_of(text)
        rows.append({**s, **info, "platform": platform, "market": market, "readable": readable, "hits": hits, "note": ""})
        print(f"  {s['id']} {s['channel'][:28]:<28} {s['watching']:>5} watching  {info['best_height']}p  {market:<12} {platform:<18} {readable:<24} {hits}")
    stamp = datetime.now().strftime("%H%M")
    md = [f"# Live futures streams scan {day} {datetime.now().strftime('%H:%M')} ET", "",
          "| channel | handle | watching | best res | market | platform | readable | evidence | title |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['channel']} | {r.get('handle', '')} | {r['watching']} | {r.get('best_height') or ''} | {r.get('market', '')} | "
                  f"{r['platform']} | {r['readable']} | {', '.join(r['hits'][:5])} | {r['title'][:70].replace('|', '/')} |")
    (out / f"SCAN_{day}_{stamp}.md").write_text("\n".join(md), encoding="utf-8")
    (out / f"scan_{day}_{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n" + "\n".join(md))


if __name__ == "__main__":
    main()
