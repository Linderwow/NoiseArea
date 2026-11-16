from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes

PROCESS_SUSPEND_RESUME = 0x0800
ntdll = ctypes.WinDLL("ntdll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
ntdll.NtSuspendProcess.argtypes = ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)

PS_LIST = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and "
           "($_.CommandLine -like '*vods_watch.py*' -or $_.CommandLine -like '*stream_capture.py*--file*') } | "
           "ForEach-Object { $s = (Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue).Threads | "
           "Where-Object { $_.WaitReason -eq 'Suspended' }; \"$($_.ProcessId)`t$(@($s).Count)`t$($_.CommandLine)\" }")


def readers() -> list[tuple[int, int, str]]:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", PS_LIST], capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0].strip().isdigit():
            rows.append((int(parts[0]), int(parts[1] or 0), parts[2]))
    return rows


def act(pid: int, resume: bool) -> bool:
    h = kernel32.OpenProcess(PROCESS_SUSPEND_RESUME, False, pid)
    if not h:
        return False
    try:
        return (ntdll.NtResumeProcess(h) if resume else ntdll.NtSuspendProcess(h)) == 0
    finally:
        kernel32.CloseHandle(h)


def main() -> None:
    what = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    rows = readers()
    if what in ("pause", "resume"):
        ok = sum(act(pid, what == "resume") for pid, _, _ in rows)
        print(f"{what}d {ok} of {len(rows)} reader processes")
        rows = readers()
    kind = lambda c: "reader" if "--file" in c else "watcher"
    for pid, susp, cmd in sorted(rows, key=lambda r: (kind(r[2]), r[0])):
        vid = cmd.split("--file", 1)[1].split()[0].strip('"').rsplit("\\", 1)[-1] if "--file" in cmd else ""
        print(f"  {pid:>6} {kind(cmd):<7} {'PAUSED' if susp else 'running':<8} {vid}")
    paused = sum(1 for _, s, _ in rows if s)
    print(f"status: {paused} paused, {len(rows) - paused} running")


if __name__ == "__main__":
    main()
