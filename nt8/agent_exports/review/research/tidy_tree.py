from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path

BASE = Path(r"D:\stream_capture")
DAYS = BASE / "DaCaptainTrader_"
VODS = BASE / "vods"
TRASH = BASE / "_to_delete" / "redundant"


def python_processes() -> dict[int, str]:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } | "
                          "ForEach-Object { \"$($_.ProcessId)`t$($_.CommandLine)\" }"],
                         capture_output=True, text=True).stdout
    procs = {}
    for line in out.splitlines():
        pid, _, cmd = line.partition("\t")
        if pid.strip().isdigit():
            procs[int(pid)] = cmd
    return procs


def busy(folder: Path, procs: dict[int, str]) -> str:
    if folder.parent.name == "vod":
        marker = VODS / f"{folder.name}.reading"
        if marker.exists():
            pid = marker.read_text(encoding="utf-8").strip()
            if not pid.isdigit() or int(pid) in procs:
                return f"reader pid {pid} on it"
    for name in ("events.jsonl", "state.csv"):
        f = folder / name
        if f.exists() and time.time() - f.stat().st_mtime < 120:
            return f"{name} written {int(time.time() - f.stat().st_mtime)} s ago"
    return ""


def tidy(folder: Path, dry: bool) -> dict:
    rel = folder.relative_to(BASE)
    n = {"png": 0, "delay": 0, "refs": 0}
    for png in sorted((folder / "frames").glob("*_grid_change.png")):
        n["png"] += 1
        if not dry:
            dest = TRASH / rel / "frames" / png.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(png), str(dest))
    delay = folder / "delay.csv"
    if folder.parent.name == "vod" and delay.exists() and delay.stat().st_size == 0:
        n["delay"] += 1
        if not dry:
            dest = TRASH / rel / "delay.csv"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(delay), str(dest))
    ev = folder / "events.jsonl"
    if ev.exists():
        out = []
        for line in ev.read_text(encoding="utf-8").splitlines(keepends=True):
            if '"kind": "grid_change"' in line and '"frame": null' not in line:
                e = json.loads(line)
                if e.get("kind") == "grid_change" and e.get("frame"):
                    e["frame"] = None
                    n["refs"] += 1
                    line = json.dumps(e, ensure_ascii=False) + "\n"
            out.append(line)
        if n["refs"] and not dry:
            tmp = ev.with_name("events.jsonl.tmp")
            tmp.write_text("".join(out), encoding="utf-8")
            try:
                tmp.replace(ev)
            except PermissionError:
                tmp.unlink(missing_ok=True)
                n["refs"] = 0
                n["locked"] = True
    return n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    procs = python_processes()
    folders = sorted(DAYS.glob("20??-??-??")) + sorted(p for p in (DAYS / "vod").glob("*") if p.is_dir())
    tot = {"png": 0, "delay": 0, "refs": 0}
    skipped = []
    for folder in folders:
        why = busy(folder, procs)
        if why:
            skipped.append((folder.name, why))
            continue
        n = tidy(folder, args.dry_run)
        if n.pop("locked", False):
            skipped.append((folder.name, "events.jsonl held open by the live capture - frame references stay until it restarts"))
        if any(n.values()):
            print(f"{'would tidy' if args.dry_run else 'tidied'} {folder.relative_to(BASE)}: "
                  f"{n['png']} grid_change frames, {n['delay']} empty delay.csv, {n['refs']} frame references cleared")
        for k in tot:
            tot[k] += n[k]
    print(f"total: {tot['png']} grid_change frames, {tot['delay']} empty delay.csv, {tot['refs']} references"
          f"{' (dry run)' if args.dry_run else ''} -> {TRASH}")
    for name, why in skipped:
        print(f"skipped {name}: {why}")
    left_png = sum(1 for f in folders if (f.name, ) and any((f / "frames").glob("*_grid_change.png")))
    left_delay = [f.name for f in folders if f.parent.name == "vod" and (f / "delay.csv").exists()]
    print(f"left in the tree: grid_change frames in {left_png} folders, delay.csv in {len(left_delay)} vod folders"
          f"{' (' + ', '.join(left_delay) + ')' if left_delay else ''}")


if __name__ == "__main__":
    main()
