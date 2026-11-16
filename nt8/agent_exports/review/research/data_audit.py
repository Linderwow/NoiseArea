from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = Path(r"D:\stream_capture")
VODS = ROOT / "vods"
BASE = ROOT / "DaCaptainTrader_"
TRASH = ROOT / "_to_delete"
STATE_HEADER = "recv_utc,state,side,qty,avg,unreal,realized,grid_positions,raw"
KINDS = {"entry", "exit", "flat", "realized_change", "grid_change"}

issues: list[str] = []
notes: list[str] = []


def issue(msg: str) -> None:
    issues.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


def live_pids() -> set[int]:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        parts = line.split('","')
        if len(parts) > 1 and parts[1].strip('"').isdigit():
            pids.add(int(parts[1].strip('"')))
    return pids


def durations() -> dict[str, float]:
    try:
        import download_streams as ds
        return {e["id"]: float(e.get("duration") or 0) for e in ds.stream_list(False)}
    except Exception as e:
        note(f"stream list not readable ({e}); video lengths from the files only")
        return {}


def check_markers(pids: set[int]) -> set[str]:
    reading = set()
    for p in VODS.glob("*.mp4"):
        vid = p.stem
        if not (VODS / f"{vid}.ok").exists() and not (VODS / f"{p.name}.part").exists():
            issue(f"{vid}.mp4 in vods has no .ok (never verified)")
        if (VODS / f"{vid}.done").exists():
            issue(f"{vid}.mp4 is read (.done) but still in vods (not moved)")
    for m in VODS.glob("*.bad"):
        issue(f"{m.stem}: failed verification - {m.read_text(encoding='utf-8', errors='replace')[:120]}")
    for m in VODS.glob("*.fail"):
        issue(f"{m.stem}: read failed - {m.read_text(encoding='utf-8', errors='replace')[-160:]}")
    for m in list(VODS.glob("*.part")) + list(VODS.glob("*.ytdl")):
        note(f"leftover partial download {m.name} ({m.stat().st_size / 1e6:.0f} MB)")
    for m in VODS.glob("*.reading"):
        vid = m.stem
        reading.add(vid)
        pid = m.read_text(encoding="utf-8").strip()
        if not pid.isdigit() or int(pid) not in pids:
            issue(f"{vid}: .reading marker but its reader (pid {pid}) is gone - a partial read")
            continue
        files = [BASE / "vod" / vid / n for n in ("status.json", "state.csv", "events.jsonl")]
        mt = max((f.stat().st_mtime for f in files if f.exists()), default=0)
        if mt and time.time() - mt > 900:
            issue(f"{vid}: reader pid {pid} alive but no file of its read moved for {(time.time() - mt) / 60:.0f} min")
    done = {m.stem for m in VODS.glob("*.done")}
    for vid in done:
        if not (BASE / "vod" / vid / "status.json").exists():
            issue(f"{vid}: .done but no read folder")
    for folder in (BASE / "vod").iterdir():
        if folder.is_dir() and folder.name not in done and folder.name not in reading:
            issue(f"vod\\{folder.name}: a read folder with neither .done nor .reading (an orphaned partial read)")
    return reading


def check_videos(dur: dict[str, float]) -> None:
    import download_streams as ds
    files = sorted(VODS.glob("*.mp4")) + sorted(TRASH.glob("*.mp4"))
    t0 = time.time()
    bad = 0
    for p in files:
        ok, why = ds.verify(p, dur.get(p.stem) or None)
        if not ok:
            bad += 1
            issue(f"video {p.stem} ({p.parent.name}): {why}")
        elif "NOTE" in why:
            note(f"video {p.stem}: {why}")
    note(f"videos re-verified: {len(files)} files ({len(files) - bad} whole, {bad} not) in {time.time() - t0:.0f} s")


def parse_events(path: Path, monotonic: bool) -> tuple[list[dict], int]:
    rows, dup, prev, seen = [], 0, "", set()
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            issue(f"{path.parent.name}\\events.jsonl line {n}: not valid JSON (corrupted)")
            continue
        if e.get("kind") not in KINDS:
            issue(f"{path.parent.name}\\events.jsonl line {n}: unknown kind {e.get('kind')!r}")
        if line in seen:
            dup += 1
        seen.add(line)
        t = e.get("recv_utc", "")
        if monotonic and prev and t < prev:
            issue(f"{path.parent.name}\\events.jsonl line {n}: time goes backwards ({prev} -> {t}) - two reads mixed?")
        prev = max(prev, t)
        rows.append(e)
    if dup:
        issue(f"{path.parent.name}\\events.jsonl: {dup} duplicate lines (read twice?)")
    return rows, len(rows)


def parse_state(path: Path, monotonic: bool) -> int:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != STATE_HEADER:
        issue(f"{path.parent.name}\\state.csv: missing or wrong header")
        return 0
    n, prev, backwards = 0, "", 0
    for r in csv.reader(lines[1:]):
        if not r:
            continue
        if len(r) != 9:
            issue(f"{path.parent.name}\\state.csv: a row with {len(r)} fields (corrupted line)")
            continue
        if monotonic and prev and r[0] < prev:
            backwards += 1
        prev = max(prev, r[0])
        n += 1
    if backwards:
        issue(f"{path.parent.name}\\state.csv: time goes backwards {backwards} times - two reads mixed?")
    return n


def check_reads(reading: set[str], dur: dict[str, float]) -> None:
    folders = [f for f in sorted((BASE / "vod").iterdir()) if f.is_dir() and f.name not in reading]
    covered = []
    for f in folders:
        st_path = f / "status.json"
        if not st_path.exists():
            continue
        try:
            st = json.loads(st_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            issue(f"vod\\{f.name}\\status.json corrupted")
            continue
        if not st.get("done") or (st.get("progress") or 0) < 99.0:
            issue(f"vod\\{f.name}: read not finished (progress {st.get('progress')} %, done {st.get('done')})")
        length = dur.get(f.name) or 0
        pos = st.get("video_pos") or 0
        if length and pos < 0.98 * length - 60:
            issue(f"vod\\{f.name}: read stopped at {pos / 3600:.2f} h of a {length / 3600:.2f} h video")
        if not (f / "events.jsonl").exists():
            note(f"vod\\{f.name}: no events at all ({pos / 3600:.1f} h read; nothing happened on his panel or no layout)")
            events = []
        else:
            events, _ = parse_events(f / "events.jsonl", monotonic=True)
        missing = [e["frame"] for e in events if e.get("frame") and not (f / "frames" / e["frame"]).exists()]
        if missing:
            issue(f"vod\\{f.name}: {len(missing)} frames named by events are missing (e.g. {missing[0]})")
        n_state = parse_state(f / "state.csv", monotonic=True) if (f / "state.csv").exists() else 0
        if (f / "panel_reread.json").exists():
            try:
                json.loads((f / "panel_reread.json").read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                issue(f"vod\\{f.name}\\panel_reread.json corrupted")
        share = min(1.0, n_state * 10 / pos) if pos else 0.0
        covered.append((f.name, share, pos))
        if share < 0.3 and pos > 3600:
            note(f"vod\\{f.name}: his usual screen found on only {share:.0%} of the {pos / 3600:.1f} h (other layout / window)")
    if covered:
        hrs = sum(p for _, _, p in covered) / 3600
        lay = sum(s * p for _, s, p in covered) / 3600
        note(f"finished reads: {len(covered)} videos, {hrs:,.0f} h of video, his screen layout found on {lay:,.0f} h ({lay / max(hrs, 1e-9):.0%})")


def check_live() -> None:
    for f in sorted(BASE.glob("20??-??-??")):
        if (f / "events.jsonl").exists():
            parse_events(f / "events.jsonl", monotonic=False)
        if (f / "state.csv").exists():
            parse_state(f / "state.csv", monotonic=False)


def check_ledger() -> None:
    p = BASE / "trades.csv"
    if not p.exists():
        issue("trades.csv missing")
        return
    try:
        rows = list(csv.DictReader(open(p, encoding="utf-8")))
    except Exception as e:
        issue(f"trades.csv does not parse: {e}")
        return
    keys = [(r["date"], r["side"], r["entry"]) for r in rows]
    dups = len(keys) - len(set(keys))
    if dups:
        issue(f"trades.csv: {dups} duplicate trades (same day, side, entry)")
    vids = {r["video"] for r in rows} - {"live", "screenshot"}
    lost = [v for v in vids if not (BASE / "vod" / v).exists()]
    if lost:
        issue(f"trades.csv names videos without a read folder: {', '.join(sorted(lost)[:5])}")
    note(f"ledger: {len(rows)} trades from {len(vids)} videos + live days; "
         f"{sum(r.get('entry_ok') in ('yes', 'fixed') for r in rows)} entries verified on the tape")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", action="store_true", help="also re-verify every downloaded file (a few seconds each)")
    args = ap.parse_args()
    t0 = time.time()
    pids = live_pids()
    dur = durations()
    reading = check_markers(pids)
    if args.videos:
        check_videos(dur)
    check_reads(reading, dur)
    check_live()
    check_ledger()
    marks = {ext: len(list(VODS.glob(f"*.{ext}"))) for ext in ("ok", "reading", "done", "skip", "bad", "fail")}
    print(f"AUDIT {datetime.now():%Y-%m-%d %H:%M} ET ({time.time() - t0:.0f} s): read {marks['done']}, reading {marks['reading']}, "
          f"waiting {marks['ok']}, skipped {marks['skip']}, bad {marks['bad']}, failed {marks['fail']}")
    for n in notes:
        print("  note  " + n)
    for i in issues:
        print("  ISSUE " + i)
    print("RESULT: " + ("all good - videos whole, reads complete, files parse, nothing lost" if not issues
                        else f"{len(issues)} issue(s)"))
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
