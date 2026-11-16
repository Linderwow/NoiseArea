from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(r"D:\stream_capture\DaCaptainTrader_")
TAPE_STORE = Path(r"D:\nt_ticks\minute\MNQ")
RECORDER = Path(r"D:\nt_recorder")
PT_USD_NQ = 20.0
COST_RT_MICRO = 2.0
EXCURSION_USD = 500.0
LOSS_LOOKBACK = timedelta(hours=2)


def money(text) -> float | None:
    t = str(text or "").replace(" ", "").replace("o", "0").replace("O", "0")
    m = re.search(r"\(?\$?(-?[\d,]+\.\d{2})\)?", t)
    if not m:
        m2 = re.search(r"\(?\$(\d{1,3}(?:,\d{3})*)(\d{2})\)?(?!\d)", t)
        if not m2:
            return None
        v = float(m2.group(1).replace(",", "") + "." + m2.group(2))
    else:
        v = float(m.group(1).replace(",", ""))
    return -abs(v) if "(" in t else v


def norm_params(text) -> str:
    m = re.search(r"(\d{2,4})\s*/\s*1\s*/\s*-?\s*(\d{1,4})", str(text or ""))
    return f"{m.group(1)}/1/-{m.group(2)}" if m else ""


def target_usd(label: str) -> float | None:
    m = re.match(r"(\d+)/1/", label)
    return int(m.group(1)) * 5.0 if m else None


class Tape:

    def __init__(self, since: str = "2026-01-01") -> None:
        frames = []
        files = sorted(TAPE_STORE.glob("*.parquet"), key=lambda p: (p.name[3:5], p.name[:2]))
        for order, f in enumerate(files):
            d = pd.read_parquet(f, columns=["time", "high", "low"])
            d["time"] = pd.to_datetime(d["time"], utc=True)
            d = d[d.time >= pd.Timestamp(since, tz="UTC")]
            if len(d):
                d["order"] = order
                frames.append(d)
        for f in sorted(RECORDER.glob("MNQ_*/*_bars1minute.csv")):
            d = pd.read_csv(f, usecols=["time_et", "high", "low"])
            t = pd.to_datetime(d.pop("time_et")).dt.tz_localize("America/New_York", ambiguous="NaT", nonexistent="NaT")
            d["time"] = t.dt.tz_convert("UTC")
            d["order"] = -1
            frames.append(d.dropna())
        df = pd.concat(frames).sort_values(["time", "order"]).drop_duplicates("time", keep="last")
        self.t = df["time"].values.astype("datetime64[ns]").astype(np.int64)
        self.hi = df["high"].to_numpy(float)
        self.lo = df["low"].to_numpy(float)
        self.last = pd.Timestamp(self.t[-1]).tz_localize("UTC") if len(self.t) else None

    def range_pts(self, t0: datetime, t1: datetime) -> float | None:
        a = np.searchsorted(self.t, np.int64(pd.Timestamp(t0).value))
        b = np.searchsorted(self.t, np.int64(pd.Timestamp(t1).value), side="right")
        if b - a < 2:
            return None
        return float(self.hi[a:b].max() - self.lo[a:b].min())


def _t(s: str) -> datetime:
    return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")


def _remap(cur: list, last: list, modal: int) -> list[int] | None:
    pairs = [(i, v) for i, v in enumerate(cur) if v is not None]
    if len(pairs) < modal - 1 or any(v is None for v in last):
        return None
    in_place = sum(1 for i, v in pairs if v == last[i])
    pool = Counter(last)
    multiset = 0
    for _, v in pairs:
        if pool[v] > 0:
            pool[v] -= 1
            multiset += 1
    if in_place > modal - 3 or multiset < modal - 2:
        return None
    mapping = [-1] * modal
    free = set(range(modal))
    for i, v in pairs:
        j = next((j for j in sorted(free) if last[j] == v), None)
        if j is not None:
            mapping[i] = j
            free.discard(j)
    for i in range(modal):
        if mapping[i] < 0:
            mapping[i] = min(free)
            free.discard(mapping[i])
    return mapping


def _track(seq: list[tuple[datetime, float]], tgt: float | None, tape: Tape) -> dict:
    runs = []
    for t, v in seq:
        if runs and runs[-1][2] == v:
            runs[-1][1] = t
            runs[-1][3] += 1
        else:
            runs.append([t, t, v, 1])
    excursions = 0
    changed = True
    while changed and len(runs) >= 3:
        changed = False
        for i in range(1, len(runs) - 1):
            a, b, c = runs[i - 1][2], runs[i][2], runs[i + 1][2]
            out, back = abs(b - a), abs(c - b)
            if out > EXCURSION_USD and back > EXCURSION_USD and (b - a) * (c - b) < 0 and abs(c - a) <= 0.25 * min(out, back):
                del runs[i]
                excursions += 1
                if runs[i - 1][2] == runs[i][2]:
                    runs[i - 1][1] = runs[i][1]
                    runs[i - 1][3] += runs[i][3]
                    del runs[i]
                changed = True
                break
    steps = []
    for p, c in zip(runs, runs[1:]):
        mins = max(1.0, (c[0] - p[1]).total_seconds() / 60.0)
        r = tape.range_pts(p[1] - LOSS_LOOKBACK, c[0])
        bound = PT_USD_NQ * (r if r is not None else 100.0) * math.ceil(mins / 10) + 50
        steps.append({"d": c[2] - p[2], "mins": mins, "bound": bound, "reset": c[2] == 0 and abs(p[2]) >= 100, "cut": ""})
    for s in steps:
        if s["reset"]:
            s["cut"] = "reset"
        elif abs(s["d"]) > s["bound"]:
            s["cut"] = "tape"

    def segments() -> list[list[int]]:
        segs, cur = [], []
        for i, s in enumerate(steps):
            if s["cut"]:
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append(i)
        if cur:
            segs.append(cur)
        return segs

    def win_size(idx: list[int]) -> float | None:
        c = Counter(round(steps[i]["d"], 2) for i in idx if steps[i]["d"] > 0)
        if not c:
            return None
        v, n = c.most_common(1)[0]
        return v if n >= 2 else None

    changed = True
    while changed:
        changed = False
        for seg in segments():
            w = win_size(seg)
            if w is None:
                w = tgt
            if w is None:
                continue
            gains = Counter(round(steps[i]["d"], 2) for i in seg if steps[i]["d"] > 0)
            other = [(v, n) for v, n in gains.items() if n >= 3 and abs(v - w) > 0.01 and not any(
                abs(v - k * w) <= 0.01 for k in range(2, 6))]
            if other:
                v2 = max(other, key=lambda vn: vn[1])[0]
                a = [i for i in seg if abs(steps[i]["d"] - w) <= 0.01]
                b = [i for i in seg if abs(steps[i]["d"] - v2) <= 0.01]
                between = []
                if a and b and (max(a) < min(b) or max(b) < min(a)):
                    lo_i, hi_i = (max(a), min(b)) if max(a) < min(b) else (max(b), min(a))
                    between = [i for i in seg if lo_i < i < hi_i]
                if between:
                    j = max(between, key=lambda i: abs(steps[i]["d"]))
                    steps[j]["cut"] = "replaced"
                    changed = True
                    break
    kept = sum(s["d"] for s in steps if not s["cut"])
    cuts = Counter(s["cut"] for s in steps if s["cut"])
    left_pos = sum(s["d"] for s in steps if s["cut"] and s["cut"] != "reset" and s["d"] > 0)
    left_neg = sum(s["d"] for s in steps if s["cut"] and s["cut"] != "reset" and s["d"] < 0)
    return {"change": kept, "resets": cuts["reset"], "breaks": sum(n for k, n in cuts.items() if k != "reset"),
            "excursions": excursions, "left_pos": left_pos, "left_neg": left_neg, "cuts": cuts,
            "t0": runs[0][0] if runs else None, "t1": runs[-1][1] if runs else None}


def video_rows(folder: Path, tape: Tape):
    readings = []
    for line in open(folder / "events.jsonl", encoding="utf-8"):
        e = json.loads(line)
        rows = e.get("rows") if e.get("kind") == "grid_change" else e.get("grid")
        if rows:
            readings.append((e["recv_utc"], rows))
    if len(readings) < 4:
        return None
    modal = Counter(len(r) for _, r in readings).most_common(1)[0][0]
    use = [(t, r) for t, r in readings if len(r) == modal]
    pos_to_track = list(range(modal))
    last = [None] * modal
    seqs = defaultdict(list)
    labels = defaultdict(Counter)
    sides = defaultdict(list)
    reorders = 0
    for t, rows in use:
        cur = [money(r.get("realized")) for r in rows]
        in_tracks = [None] * modal
        for i, v in enumerate(cur):
            in_tracks[pos_to_track[i]] = v
        m = _remap(in_tracks, last, modal)
        if m is not None:
            reorders += 1
            pos_to_track = [m[pos_to_track[i]] for i in range(modal)]
        tt = _t(t)
        for i, r in enumerate(rows):
            k = pos_to_track[i]
            v = cur[i]
            if v is not None:
                seqs[k].append((tt, v))
                last[k] = v
            labels[k][norm_params(r.get("params"))] += 1
            if r.get("side") in ("flat", "long", "short"):
                sides[k].append(r.get("side"))
    out = {}
    for k in range(modal):
        lab = next((p for p, _ in labels[k].most_common() if p), f"row{k}")
        s = sides[k]
        entries = sum(1 for a, b in zip(s, s[1:]) if b in ("long", "short") and a != b)
        info = _track(seqs[k], target_usd(lab), tape) if len(seqs[k]) >= 6 else None
        out[k] = (lab, seqs[k], entries, info)
    return out, len(use), modal, reorders


def _stats(title: str, xs: list[float], hrs: float) -> None:
    mean = sum(xs) / len(xs)
    sd = math.sqrt(sum((x - mean) ** 2 for x in xs) / (len(xs) - 1))
    t = mean / sd * math.sqrt(len(xs)) if sd else float("nan")
    rng = random.Random(7)
    boots = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(5000))
    best = sorted(xs)[-5:]
    print(f"\nTHE AVERAGE COPY per video-day, {title}: mean {mean:+,.1f} $ over {len(xs)} video-days ({hrs:,.0f} h on screen), "
          f"sd {sd:,.0f}, t {t:.2f}, 90 % bootstrap interval {boots[250]:+,.1f} .. {boots[4750]:+,.1f} $; "
          f"drop the 5 best days: {(sum(xs) - sum(best)) / max(1, len(xs) - 5):+,.1f} $; positive days {sum(x > 0 for x in xs)} of {len(xs)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="")
    args = ap.parse_args()
    tape = Tape()
    print(f"tape for the loss bound: MNQ 1-minute bars to {tape.last:%Y-%m-%d %H:%M} UTC (later steps use a 100-point range)")
    per_copy = defaultdict(float)
    per_copy_n = Counter()
    per_month = defaultdict(float)
    per_video = []
    per_copy_net = defaultdict(list)
    diag = Counter()
    endpoint_total = 0.0
    left_pos = left_neg = 0.0
    print(f"{'video':<12} {'day':<10} {'rows':>4} {'reads':>5} {'reord':>5}  per copy: label = kept change $ [resets/left out/excursions]")
    folders = sorted(BASE.glob("20??-??-??")) + sorted(p for p in (BASE / "vod").glob("*") if p.is_dir())
    for folder in folders:
        if not (folder / "events.jsonl").exists():
            continue
        res = video_rows(folder, tape)
        if res is None:
            continue
        rows, n_use, modal, reorders = res
        infos = [(lab, seq, entries, info) for lab, seq, entries, info in rows.values() if info is not None]
        if not infos:
            continue
        day = min(info["t0"] for *_, info in infos).strftime("%Y-%m-%d")
        if args.since and day < args.since:
            continue
        t_first = min(info["t0"] for *_, info in infos)
        t_last = max(info["t1"] for *_, info in infos)
        parts, changes, nets = [], [], []
        for lab, seq, entries, info in infos:
            ch = info["change"]
            changes.append(ch)
            net = ch / 10.0 - entries * COST_RT_MICRO
            nets.append(net)
            per_copy_net[lab].append(net)
            per_copy[lab] += ch
            per_copy_n[lab] += 1
            first = statistics.median(v for _, v in seq[:5])
            lastv = statistics.median(v for _, v in seq[-5:])
            endpoint_total += lastv - first
            for k in ("resets", "breaks", "excursions"):
                diag[k] += info[k]
            for k, n in info["cuts"].items():
                diag["cut " + k] += n
            left_pos += info["left_pos"]
            left_neg += info["left_neg"]
            flag = f" [{info['resets']}/{info['breaks']}/{info['excursions']}]" if info["resets"] or info["breaks"] or info["excursions"] else ""
            parts.append(f"{lab} {ch:+,.0f}{flag}")
        diag["reorders"] += reorders
        name = folder.name if folder.parent.name == "vod" else "live"
        vid_total = sum(changes)
        per_month[day[:7]] += vid_total
        hours = max(0.1, (t_last - t_first).total_seconds() / 3600)
        per_video.append((day, sum(changes) / len(changes), hours, len(changes), sum(nets) / len(nets)))
        print(f"{name:<12} {day:<10} {modal:>4} {n_use:>5} {reorders:>5}  " + " | ".join(parts) + f"   all {vid_total:+,.0f}")
    grand = sum(per_copy.values())
    print(f"\nALL COPIES, all videos: {grand:+,.0f} $ gross (1 NQ per copy) = {grand / PT_USD_NQ:+,.0f} pts summed over copies")
    print(f"tracker: {diag['reorders']} re-orders followed, {diag['resets']} re-enables (to $0) left out, {diag['excursions']} OCR "
          f"excursions dropped, {diag['breaks']} impossible steps left out (beyond what the tape allows {diag['cut tape']}, "
          f"copy replaced {diag['cut replaced']}; gains {left_pos:+,.0f} $, losses {left_neg:+,.0f} $); a plain "
          f"last-minus-first would say {endpoint_total:+,.0f} $")
    print("per month (all copies, gross): " + ", ".join(f"{m} {v:+,.0f}" for m, v in sorted(per_month.items())))
    if len(per_video) >= 3:
        hrs = sum(p[2] for p in per_video)
        _stats("1 NQ, gross (as his grid shows it)", [p[1] for p in per_video], hrs)
        _stats(f"1 MNQ micro, after ${COST_RT_MICRO:.2f} a round trip", [p[4] for p in per_video], hrs)
    print(f"\nPER COPY at one MNQ micro after ${COST_RT_MICRO:.2f} a round trip (copies seen in 8+ videos; with ~13 copies tested,"
          f" one reaching t 2 is expected by chance - the bar is t ~2.9):")
    print(f"  {'copy':<12} {'videos':>6} {'net $ total':>11} {'$/video':>8} {'t':>6}")
    for lab, xs in sorted(per_copy_net.items(), key=lambda kv: -sum(kv[1])):
        if len(xs) < 8:
            continue
        m = sum(xs) / len(xs)
        sd = (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5
        print(f"  {lab:<12} {len(xs):>6} {sum(xs):>11,.0f} {m:>8,.1f} {m / sd * len(xs) ** 0.5 if sd else float('nan'):>6.2f}")
    print("\nper copy, gross at 1 NQ (summed over the videos where that copy was readable):")
    for lab, v in sorted(per_copy.items(), key=lambda kv: -kv[1]):
        print(f"  {lab:<12} {v:+10,.0f} $  over {per_copy_n[lab]} videos")
    print("\nread this with: a video shows a copy's P&L only while it is on screen; artefact steps are left out, so a real\n"
          "step that broke a bound would be lost too (gains cannot: a win is capped at the target). Copies with the same label\n"
          "in different periods are different parameter sets of the same family and are summed by label.")


if __name__ == "__main__":
    main()
