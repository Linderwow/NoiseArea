from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path(r"D:\stream_capture\DaCaptainTrader_")
CACHE = "panel_reread.json"
VENV_PY = r"D:\dev\tooling\screenread\.venv\Scripts\python.exe"
KINDS = ("exit", "flat", "realized_change")


def folders() -> list[Path]:
    return sorted(BASE.glob("20??-??-??")) + sorted(p for p in (BASE / "vod").glob("*") if p.is_dir())


def rereads(folder: Path) -> dict[str, float | None]:
    p = folder / CACHE
    if not p.exists():
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    return {k: v.get("realized") for k, v in d.items() if isinstance(v, dict)}


def frames_of(folder: Path) -> list[tuple[str, str, float | None]]:
    ev = folder / "events.jsonl"
    out = []
    if not ev.exists():
        return out
    for line in ev.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        k = e.get("kind")
        if k in KINDS and e.get("frame"):
            out.append((e["frame"], k, e.get("after") if k == "realized_change" else e.get("realized")))
    return out


def ocr_folder(folder: Path, force: bool = False) -> tuple[int, int, int]:
    import cv2
    sys.argv, argv = ["x"], sys.argv
    import stream_capture as sc
    sys.argv = argv
    p = folder / CACHE
    cache = {}
    if p.exists() and not force:
        try:
            cache = json.load(open(p, encoding="utf-8"))
        except Exception:
            cache = {}
    todo = [(f, k) for f, k, _ in frames_of(folder) if f not in cache]
    for frame, kind in todo:
        img = cv2.imread(str(folder / "frames" / frame))
        if img is None:
            cache[frame] = {"realized": None, "raw": "", "state": "missing", "kind": kind}
            continue
        lay = sc.find_layout(img)
        res = sc.parse_panel(img, lay) if lay.ok() else {"state": "nolayout", "realized": None, "raw": ""}
        cache[frame] = {"realized": res.get("realized"), "raw": res.get("raw", ""), "state": res.get("state"), "kind": kind}
    if todo:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
        tmp.replace(p)
    return len(cache), len(todo), sum(v.get("realized") is not None for v in cache.values())


def stage_ocr(force: bool = False) -> None:
    import logging
    logging.getLogger().setLevel(logging.ERROR)
    known = new = read = 0
    for folder in folders():
        k, n, r = ocr_folder(folder, force)
        known += k
        new += n
        read += r
        if n:
            print(f"  {folder.name:<12} frames {k:>4}  re-read now {n:>4}  with a value {r:>4}", flush=True)
    print(f"OCR pass: {known} frames in the caches, {new} read this run, {read} carry a value")
    agree = differ = gained = still = 0
    worst = []
    for folder in folders():
        rr = rereads(folder)
        for frame, kind, live in frames_of(folder):
            if frame not in rr:
                continue
            v = rr[frame]
            if live is not None:
                if v is not None and abs(v - live) < 0.01:
                    agree += 1
                else:
                    differ += 1
                    worst.append((folder.name, frame, live, v))
            elif v is not None:
                gained += 1
            else:
                still += 1
    print(f"check (a): on frames the live parse HAD read, the re-read agrees on {agree} and differs on {differ}; "
          f"frames it had NOT read: {gained} now have a value, {still} still none")
    for name, frame, live, v in worst[:10]:
        print(f"    differs: {name} {frame} live {live} re-read {v}")
    if still:
        why = Counter()
        for folder in folders():
            p = folder / CACHE
            if p.exists():
                for v in json.load(open(p, encoding="utf-8")).values():
                    if v.get("realized") is None:
                        why[v.get("state") or "?"] += 1
        print("    still unread, by panel state: " + ", ".join(f"{k} {n}" for k, n in why.most_common()))


def stage_validate() -> None:
    sys.path.insert(0, str(HERE))
    import exit_pass
    import stream_trades
    rows = stream_trades.build(22.0)
    dropped = Counter()
    for r in rows:
        if "not this trade's" in r.get("note", ""):
            dropped["frame" if "frame P&L" in r["note"] else "panel"] += 1
    print("realized-line P&Ls whose exit price never traded (a step over several trades, left to the tape): "
          + (", ".join(f"{k} {v}" for k, v in dropped.items()) or "none"))
    frame_rows = [r for r in rows if r.get("exit_src") == "frame" and r.get("side") in ("long", "short") and r.get("exit_et")]
    vt = exit_pass.video_targets(rows)
    lags = exit_pass.video_lags(rows)
    diffs = []
    kinds = Counter()
    for r in frame_rows:
        d = exit_pass.derive(r, vt, exit_pass.lag_for(r.get("video", ""), lags))
        if d is None:
            kinds["no tape data"] += 1
            continue
        price, kind, how = d
        side = 1 if r["side"] == "long" else -1
        tape_pts = side * (price - float(r["entry"]))
        diffs.append((tape_pts - float(r["pts"]), r, price, kind))
        kinds[f"frame {r['exit_kind']} / tape {kind}"] += 1
    n = len(diffs)
    print(f"check (b): {len(frame_rows)} trades have a frame-derived P&L; the tape could derive {n} of them")
    if not n:
        return
    ad = sorted(abs(x[0]) for x in diffs)
    within = lambda k: sum(1 for x in ad if x <= k)
    bias = sum(x[0] for x in diffs) / n
    print(f"    tape vs frame: within 1 pt {within(1) / n:.0%}, 2 pt {within(2) / n:.0%}, 5 pt {within(5) / n:.0%}, "
          f"10 pt {within(10) / n:.0%}; median |diff| {ad[n // 2]:.2f} pt; bias (tape - frame) {bias:+.2f} pt")
    losers = [x for x in diffs if float(x[1]["pts"]) < 0]
    if losers:
        al = sorted(abs(x[0]) for x in losers)
        print(f"    on the {len(losers)} losing trades: within 2 pt {sum(1 for x in al if x <= 2) / len(losers):.0%}, "
              f"5 pt {sum(1 for x in al if x <= 5) / len(losers):.0%}, median |diff| {al[len(al) // 2]:.2f} pt, "
              f"bias {sum(x[0] for x in losers) / len(losers):+.2f} pt")
    print("    kinds: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common(8)))
    print("    worst disagreements (tape - frame):")
    for dlt, r, price, kind in sorted(diffs, key=lambda x: -abs(x[0]))[:8]:
        print(f"      {dlt:+7.2f} pt  {r['date']} {r['exit_et']} {r['side']:<5} entry {r['entry']} frame exit {r['exit']} "
              f"({r['exit_kind']}, ${float(r['usd']):+,.0f}) tape exit {price} ({kind})  video {r['video']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", nargs="?", default="both", choices=["ocr", "validate", "both"])
    ap.add_argument("--force", action="store_true", help="re-read every frame, ignoring the caches")
    args = ap.parse_args()
    if args.stage in ("ocr", "both"):
        stage_ocr(args.force)
    if args.stage in ("validate", "both"):
        try:
            import pandas
        except ImportError:
            print("validate: needs the system Python (pandas); run  python -I -X utf8 frame_pass.py validate")
            return
        stage_validate()


if __name__ == "__main__":
    main()
