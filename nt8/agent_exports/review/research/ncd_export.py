"""Export NinjaTrader 8 MNQ tick history (.ncd) to daily parquet files, plus MNQ/MES minute bars.

Usage (from this folder):
    python ncd_export.py                 # sync copies, export ticks + minute bars (skips done days)
    python ncd_export.py --ticks-only    # skip the minute export
    python ncd_export.py --no-sync       # do not refresh the copies from the NinjaTrader db
    python ncd_export.py --workers 8     # decoding processes (default 8)
    python ncd_export.py --force         # re-export everything

Steps
  1. sync: copy new/changed .ncd files from the live NinjaTrader db (read-only open,
     files touched in the last 15 min are skipped) to D:\\nt_ticks\\_work\\{tick,minute}\\.
     Nothing inside the NinjaTrader folder is ever written, renamed or locked for long.
  2. ticks: D:\\nt_ticks\\MNQ\\<MM-YY>\\YYYY-MM-DD.parquet, one file per CME TRADING DATE
     in US/Eastern: date D = prints stamped from D-1 18:00 ET up to D 17:00 ET (weekend
     strays roll forward to Monday). One row per stored print (NinjaTrader keeps every
     fill, so a CME trade of 8 lots can be several rows). Columns:
       time      UTC, ns dtype; NinjaTrader feed time at 1 ms resolution, mostly on a 4 ms grid
       price, volume, side ('A' traded at the ask / 'B' at the bid), bid, ask
       sub_ms    time has sub-ms digits = recorded by the local NT client (mostly reconnect
                 snapshots, often while the market is closed), not an exchange print
       artifact  NinjaTrader's Friday 15:59:59.9xx end-of-week prints (ncd_reader.eow_artifact_mask)
     Research code should drop rows with sub_ms | artifact. Each output is re-read and verified
     (rows, volume, first/last time). The trading date still in progress (no file after
     its 17:00 ET close yet) is not written. A day is redone only when its source
     files changed (signature in the summary) or with --force.
     Summary: D:\\nt_ticks\\MNQ\\tick_summary.csv
  3. minute: D:\\nt_ticks\\minute\\<MNQ|MES>\\<MM-YY>.parquet (time = bar END, UTC),
     summary D:\\nt_ticks\\minute\\minute_summary.csv
"""
import sys
sys.dont_write_bytecode = True

import argparse
import datetime as dt
import hashlib
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ncd_reader as R  # noqa: E402

NT_DB = r"C:\Users\linde\OneDrive\Documents\NinjaTrader 8\db"
WORK = r"D:\nt_ticks\_work"
OUT_TICKS = r"D:\nt_ticks\MNQ"
OUT_MIN = r"D:\nt_ticks\minute"
TICK_SUMMARY = os.path.join(OUT_TICKS, "tick_summary.csv")
MIN_SUMMARY = os.path.join(OUT_MIN, "minute_summary.csv")
SKIP_RECENT_SEC = 900

TICK_SCHEMA = pa.schema([
    ("time", pa.timestamp("ns", tz="UTC")),
    ("price", pa.float64()),
    ("volume", pa.int32()),
    ("side", pa.dictionary(pa.int8(), pa.string())),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
    ("sub_ms", pa.bool_()),
    ("artifact", pa.bool_()),
])


# ----------------------------------------------------------------------------- sync
def _read_shared(path):
    """Read a file opened with FILE_SHARE_READ|WRITE|DELETE so NinjaTrader can still
    write, rename or delete it while we read (falls back to a plain open off Windows)."""
    if os.name != "nt":
        with open(path, "rb") as f:
            return f.read()
    import ctypes
    import msvcrt
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    GENERIC_READ, SHARE_ALL, OPEN_EXISTING = 0x80000000, 0x1 | 0x2 | 0x4, 3
    h = k32.CreateFileW(path, GENERIC_READ, SHARE_ALL, None, OPEN_EXISTING, 0x80, None)
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed", path)
    fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
    with os.fdopen(fd, "rb") as f:
        return f.read()


def sync_copies(kinds=(("tick", ("MNQ ",)), ("minute", ("MNQ ", "MES "))), log=print):
    """Copy new/changed .ncd files from the NinjaTrader db into WORK (never writes to the db)."""
    now = time.time()
    copied = skipped_recent = 0
    nbytes = 0
    for kind, prefixes in kinds:
        root = os.path.join(NT_DB, kind)
        if not os.path.isdir(root):
            continue
        for inst in sorted(os.listdir(root)):
            if not inst.startswith(prefixes):
                continue
            sd = os.path.join(root, inst)
            dd = os.path.join(WORK, kind, inst)
            os.makedirs(dd, exist_ok=True)
            for fn in os.listdir(sd):
                if not fn.endswith(".ncd"):
                    continue
                sp, dp = os.path.join(sd, fn), os.path.join(dd, fn)
                st = os.stat(sp)
                if now - st.st_mtime < SKIP_RECENT_SEC:
                    skipped_recent += 1
                    continue
                if os.path.exists(dp):
                    ds_ = os.stat(dp)
                    if ds_.st_size == st.st_size and int(ds_.st_mtime) == int(st.st_mtime):
                        continue
                data = _read_shared(sp)
                tmp = dp + ".tmp"
                with open(tmp, "wb") as g:
                    g.write(data)
                os.utime(tmp, (st.st_atime, st.st_mtime))
                os.replace(tmp, dp)
                copied += 1
                nbytes += len(data)
    log(f"sync: copied {copied} files ({nbytes / 1e6:.1f} MB), skipped {skipped_recent} touched <15 min ago")


# ----------------------------------------------------------------------------- helpers
def contract_short(folder):
    return folder.split(" ", 1)[1]  # "MNQ 12-26" -> "12-26"


def trading_date(local_naive_idx):
    """CME trading date for naive US/Eastern times: >= 18:00 belongs to the next day; Sat/Sun -> Monday."""
    d = (local_naive_idx + pd.Timedelta(hours=6)).normalize()
    wd = d.weekday
    d = d + pd.to_timedelta(np.where(wd == 5, 2, np.where(wd == 6, 1, 0)), unit="D")
    return d


def tick_file_groups(folder_path):
    """{trading_date: [file names]} using each hourly file's start hour (name = hour END, local)."""
    files = sorted(f for f in os.listdir(folder_path) if f.endswith(".Last.ncd"))
    if not files:
        return {}, None
    starts = pd.DatetimeIndex([dt.datetime.strptime(f[:12], "%Y%m%d%H%M") for f in files]) - pd.Timedelta(hours=1)
    dates = trading_date(starts)
    groups = {}
    for f, d in zip(files, dates):
        groups.setdefault(d, []).append(f)
    last_end = starts.max() + pd.Timedelta(hours=1)
    return groups, last_end


def signature(folder_path, files):
    h = hashlib.sha1()
    for f in files:
        st = os.stat(os.path.join(folder_path, f))
        h.update(f"{f}:{st.st_size}:{int(st.st_mtime)};".encode())
    return h.hexdigest()[:16]


def load_summary(path):
    if os.path.exists(path):
        return pd.read_csv(path, dtype=str)
    return None


# ----------------------------------------------------------------------------- tick day export
def export_tick_day(args):
    folder, contract, date_str, files, sig, out_path = args
    t0 = time.time()
    parts = []
    for f in files:
        with open(os.path.join(folder, f), "rb") as fh:
            raw = fh.read()
        d, meta = R.decode_tick_bytes(raw)
        if meta["tick_size"] != 0.25:
            raise ValueError(f"{f}: unexpected tick size {meta['tick_size']}")
        parts.append(d)
    lt = np.concatenate([p["local_ticks"] for p in parts])
    px = np.concatenate([p["price_ticks"] for p in parts]) * 0.25
    vol = np.concatenate([p["volume"] for p in parts])
    at_ask = np.concatenate([p["at_ask"] for p in parts])
    spr = np.concatenate([p["spread"] for p in parts]) * 0.25
    n = len(lt)
    # sanity: every print must belong to this trading date and times must not go backwards
    loc_naive = R.local_naive(lt)
    tdates = trading_date(loc_naive) if n else pd.DatetimeIndex([])
    artifact = R.eow_artifact_mask(loc_naive, px, date_str)
    foreign = int((tdates != pd.Timestamp(date_str)).sum()) if n else 0
    backwards = int((np.diff(lt) < 0).sum()) if n > 1 else 0
    utc = R.ticks_to_utc(lt)
    side = pa.DictionaryArray.from_arrays(pa.array(np.where(at_ask, 1, 0).astype(np.int8)), pa.array(["B", "A"]))
    table = pa.Table.from_arrays([
        pa.array(utc.asi8, type=pa.int64()).cast(pa.timestamp("ns", tz="UTC")),
        pa.array(px),
        pa.array(vol.astype(np.int32)),
        side,
        pa.array(np.where(at_ask, px - spr, px)),
        pa.array(np.where(at_ask, px, px + spr)),
        pa.array(R.is_sub_ms(lt)),
        pa.array(artifact),
    ], schema=TICK_SCHEMA)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    pq.write_table(table, tmp, compression="zstd", compression_level=6,
                   use_dictionary=["price", "volume", "side", "bid", "ask", "sub_ms", "artifact"],
                   column_encoding={"time": "DELTA_BINARY_PACKED"})
    # verify
    back = pq.read_table(tmp)
    ok = back.num_rows == n
    if ok and n:
        bt = back.column("time").to_numpy().astype("datetime64[ns]").astype(np.int64)
        ok = (bt[0] == utc.asi8[0] and bt[-1] == utc.asi8[-1]
              and int(back.column("volume").to_numpy().sum()) == int(vol.sum()))
    if not ok:
        os.remove(tmp)
        raise RuntimeError(f"verification failed for {out_path}")
    os.replace(tmp, out_path)
    first = utc[0].isoformat() if n else ""
    last = utc[-1].isoformat() if n else ""
    return dict(date=date_str, contract=contract, rows=n, volume=int(vol.sum()), first=first, last=last,
                files=len(files), sub_ms_rows=int(R.is_sub_ms(lt).sum()),
                artifact_rows=int(artifact.sum()),
                on_4ms_pct=round(100 * float(np.mean(lt % 40_000 == 0)), 3) if n else 0.0,
                foreign_rows=foreign, backwards_steps=backwards,
                out_mb=round(os.path.getsize(out_path) / 1e6, 3), signature=sig,
                verified="yes", seconds=round(time.time() - t0, 2))


def export_ticks(force=False, workers=8, log=print):
    root = os.path.join(WORK, "tick")
    summary = load_summary(TICK_SUMMARY)
    done = {} if summary is None else {(r.contract, r.date): r for r in summary.itertuples()}
    rows = [] if summary is None else summary.to_dict("records")
    keep = {(r["contract"], r["date"]): r for r in rows}
    tasks, partial = [], []
    newest_any = None
    folders = sorted(f for f in os.listdir(root) if f.startswith("MNQ ")) if os.path.isdir(root) else []
    groups_by = {}
    for folder in folders:
        g, last_end = tick_file_groups(os.path.join(root, folder))
        groups_by[folder] = (g, last_end)
        if last_end is not None and (newest_any is None or last_end > newest_any):
            newest_any = last_end
    for folder in folders:
        fpath = os.path.join(root, folder)
        contract = contract_short(folder)
        g, last_end = groups_by[folder]
        for d, files in sorted(g.items()):
            date_str = d.strftime("%Y-%m-%d")
            session_end = d + pd.Timedelta(hours=17)
            # complete if this folder has data after the close, or the contract stopped >2 days before the newest data
            complete = last_end > session_end or (newest_any - last_end) > pd.Timedelta(days=2)
            if not complete:
                partial.append(f"{contract} {date_str}")
                continue
            sig = signature(fpath, files)
            out_path = os.path.join(OUT_TICKS, contract, date_str + ".parquet")
            prev = done.get((contract, date_str))
            if not force and prev is not None and prev.signature == sig and os.path.exists(out_path):
                continue
            tasks.append((fpath, contract, date_str, files, sig, out_path))
    log(f"ticks: {len(tasks)} trading days to export, {len(keep)} already in summary, "
        f"in progress (not written): {', '.join(partial) or 'none'}")
    t0 = time.time()
    failed = []
    if tasks:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(export_tick_day, t): t for t in tasks}
            for k, fut in enumerate(futs):
                t = futs[fut]
                try:
                    res = fut.result()
                    keep[(res["contract"], res["date"])] = res
                except Exception as e:  # keep going, report at the end
                    failed.append(f"{t[1]} {t[2]}: {e}")
                if (k + 1) % 25 == 0 or k + 1 == len(tasks):
                    log(f"  {k + 1}/{len(tasks)} days, {time.time() - t0:.0f}s")
    out = pd.DataFrame(list(keep.values()))
    if len(out):
        out = out.sort_values(["date", "contract"]).reset_index(drop=True)
        os.makedirs(OUT_TICKS, exist_ok=True)
        out.to_csv(TICK_SUMMARY, index=False)
    for f in failed:
        log("  FAILED " + f)
    return out, failed, partial


# ----------------------------------------------------------------------------- minute export
def export_minute_contract(args):
    folder, inst, contract, files, sig, out_path = args
    parts = []
    for f in files:
        with open(os.path.join(folder, f), "rb") as fh:
            raw = fh.read()
        d, meta = R.decode_minute_bytes(raw)
        parts.append(pd.DataFrame({"lt": d["local_ticks"], "open": d["o"] * meta["tick_size"],
                                   "high": d["h"] * meta["tick_size"], "low": d["l"] * meta["tick_size"],
                                   "close": d["c"] * meta["tick_size"], "volume": d["volume"]}))
    df = pd.concat(parts, ignore_index=True)
    dup = int(df.duplicated("lt").sum())
    df = df.drop_duplicates("lt", keep="last").sort_values("lt").reset_index(drop=True)
    df.insert(0, "time", R.ticks_to_utc(df.pop("lt").values))
    df["volume"] = df["volume"].astype(np.int64)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".tmp"
    df.to_parquet(tmp, index=False, compression="zstd")
    back = pd.read_parquet(tmp)
    if len(back) != len(df) or back.volume.sum() != df.volume.sum():
        os.remove(tmp)
        raise RuntimeError(f"verification failed for {out_path}")
    os.replace(tmp, out_path)
    return dict(instrument=inst, contract=contract, files=len(files), bars=len(df), volume=int(df.volume.sum()),
                first=df.time.iloc[0].isoformat() if len(df) else "", last=df.time.iloc[-1].isoformat() if len(df) else "",
                duplicate_bars_dropped=dup, out_mb=round(os.path.getsize(out_path) / 1e6, 3), signature=sig, verified="yes")


def export_minutes(force=False, workers=8, log=print):
    root = os.path.join(WORK, "minute")
    summary = load_summary(MIN_SUMMARY)
    keep = {} if summary is None else {(r["instrument"], r["contract"]): r for r in summary.to_dict("records")}
    tasks = []
    for folder in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not folder.startswith(("MNQ ", "MES ")):
            continue
        fpath = os.path.join(root, folder)
        files = sorted(f for f in os.listdir(fpath) if f.endswith(".Last.ncd"))
        if not files:
            continue
        inst, contract = folder.split(" ", 1)
        sig = signature(fpath, files)
        out_path = os.path.join(OUT_MIN, inst, contract + ".parquet")
        prev = keep.get((inst, contract))
        if not force and prev is not None and prev.get("signature") == sig and os.path.exists(out_path):
            continue
        tasks.append((fpath, inst, contract, files, sig, out_path))
    log(f"minute: {len(tasks)} contracts to export")
    failed = []
    if tasks:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(export_minute_contract, t): t for t in tasks}
            for fut in futs:
                t = futs[fut]
                try:
                    res = fut.result()
                    keep[(res["instrument"], res["contract"])] = res
                except Exception as e:
                    failed.append(f"{t[1]} {t[2]}: {e}")
    out = pd.DataFrame(list(keep.values()))
    if len(out):
        out["_k"] = out.contract.str[3:] + out.contract.str[:2]
        out = out.sort_values(["instrument", "_k"]).drop(columns="_k").reset_index(drop=True)
        os.makedirs(OUT_MIN, exist_ok=True)
        out.to_csv(MIN_SUMMARY, index=False)
    for f in failed:
        log("  FAILED " + f)
    return out, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-sync", action="store_true")
    ap.add_argument("--ticks-only", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    t0 = time.time()
    if not a.no_sync:
        sync_copies()
    ticks, tfail, partial = export_ticks(force=a.force, workers=a.workers)
    if len(ticks):
        ticks["rows"] = ticks["rows"].astype(np.int64)
        ticks["volume"] = ticks["volume"].astype(np.int64)
        ticks["out_mb"] = ticks["out_mb"].astype(float)
        print("ticks total:", len(ticks), "trading-day files,", f"{ticks.rows.sum():,} rows,",
              f"{ticks.volume.sum():,} contracts,", f"{ticks.out_mb.sum():,.0f} MB")
        print(ticks.groupby("contract").agg(days=("date", "size"), first=("date", "min"), last=("date", "max"),
                                            rows=("rows", "sum"), volume=("volume", "sum"), mb=("out_mb", "sum")).to_string())
    if not a.ticks_only:
        mins, mfail = export_minutes(force=a.force, workers=a.workers)
        if len(mins):
            mins["bars"] = mins["bars"].astype(np.int64)
            mins["out_mb"] = mins["out_mb"].astype(float)
            print("minute total:", len(mins), "contract files,", f"{mins.bars.sum():,} bars,", f"{mins.out_mb.sum():,.1f} MB")
            print(mins.groupby("instrument").agg(contracts=("contract", "size"), bars=("bars", "sum"),
                                                 first=("first", "min"), last=("last", "max")).to_string())
    print(f"done in {time.time() - t0:.0f}s; failures: {len(tfail) + (0 if a.ticks_only else len(mfail))}")


if __name__ == "__main__":
    main()
