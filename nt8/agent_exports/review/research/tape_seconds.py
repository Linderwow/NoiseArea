r"""Per-second MNQ tape (last trade, bid, ask) from the recorder's tape files, cached as parquet per day
(regime_work\tape_seconds\sec_<day>.parquet, the same cache yt_delay.py builds), plus two checks the stream ledger needs:
did a price trade near a time, and what was the high/low over a window. Falls back to the recorder's 1-minute bars (and,
before 2026-10-22, to NinjaTrader's minute store D:\nt_ticks\minute\MNQ) when there is no tape for that day.
Times are ET (naive)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

TAPE = Path(r"D:\nt_recorder\MNQ_12-26")
MINUTE_STORE = Path(r"D:\nt_ticks\minute\MNQ")
OUT = Path(__file__).resolve().parent / "regime_work" / "tape_seconds"
OUT.mkdir(parents=True, exist_ok=True)
ET = "America/New_York"
_sec_cache: dict[str, pd.DataFrame | None] = {}
_min_cache: dict[str, pd.DataFrame | None] = {}


def per_second(day: str) -> pd.DataFrame | None:
    """day = UTC file date 'YYYY-MM-DD' of the recorder tape; index ET seconds; columns bid, ask, last."""
    if day in _sec_cache:
        return _sec_cache[day]
    cache = OUT / f"sec_{day}.parquet"
    if cache.exists():
        _sec_cache[day] = pd.read_parquet(cache)
        return _sec_cache[day]
    path = TAPE / f"{day}_tape.csv"
    if not path.exists():
        _sec_cache[day] = None
        return None
    import pyarrow as pa
    import pyarrow.csv as pacsv
    parts = []
    # today's file is still being written: its last line may be cut - skip such rows, and do not cache the day
    live_day = day >= datetime.utcnow().strftime("%Y-%m-%d")
    reader = pacsv.open_csv(str(path), read_options=pacsv.ReadOptions(block_size=64 << 20),
                            parse_options=pacsv.ParseOptions(invalid_row_handler=lambda row: "skip"),
                            convert_options=pacsv.ConvertOptions(include_columns=["kind", "feed_utc", "price", "bid", "ask"],
                                                                 column_types={"feed_utc": pa.string(), "kind": pa.string()}))
    for batch in reader:
        d = batch.to_pandas()
        d["sec"] = d.feed_utc.str.slice(0, 19)
        tr = d[d.kind == "T"]
        g = d.groupby("sec", sort=False).agg(bid=("bid", "last"), ask=("ask", "last"))
        gt = tr.groupby("sec", sort=False).agg(last=("price", "last"), lo=("price", "min"), hi=("price", "max"))
        parts.append(g.join(gt, how="left"))
    if not parts:
        _sec_cache[day] = None
        return None
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    s.index = pd.to_datetime(s.index, utc=True).tz_convert(ET).tz_localize(None)
    s.index.name = "et"
    s["last"] = s["last"].ffill()
    if not live_day:
        s.to_parquet(cache)
    _sec_cache[day] = s
    return s


def seconds_around(t: datetime, before: int, after: int) -> pd.DataFrame | None:
    """Tape seconds from t-before to t+after (ET); spans the UTC file boundary when needed."""
    utc_days = sorted({(t + timedelta(hours=5) + timedelta(seconds=x)).strftime("%Y-%m-%d") for x in (-before, after)})
    frames = [per_second(d) for d in utc_days]
    frames = [f for f in frames if f is not None]
    if not frames:
        return None
    s = pd.concat(frames)
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return s.loc[t - timedelta(seconds=before): t + timedelta(seconds=after)]


def minute_bars(day_et: str) -> pd.DataFrame | None:
    """Recorder 1-minute bars (ET) for an ET date, else NinjaTrader's minute store (UTC -> ET)."""
    if day_et in _min_cache:
        return _min_cache[day_et]
    df = None
    for p in sorted(TAPE.glob("*_bars1minute.csv")):
        pass
    files = [p for p in TAPE.glob("*_bars1minute.csv")]
    rows = []
    for p in files:
        try:
            d = pd.read_csv(p, usecols=["time_et", "open", "high", "low", "close", "volume"])
        except Exception:  # noqa: BLE001
            continue
        d = d[d.time_et.str.startswith(day_et)]
        if len(d):
            rows.append(d)
    if rows:
        df = pd.concat(rows).drop_duplicates("time_et").sort_values("time_et")
        df["t"] = pd.to_datetime(df.time_et)
        df = df.set_index("t")[["open", "high", "low", "close", "volume"]]
    elif MINUTE_STORE.exists():
        parts = []
        for p in sorted(MINUTE_STORE.glob("*.parquet")):
            try:
                m = pd.read_parquet(p)
            except Exception:  # noqa: BLE001
                continue
            m["t"] = pd.to_datetime(m["time"], utc=True).dt.tz_convert(ET).dt.tz_localize(None)
            m = m[m.t.dt.strftime("%Y-%m-%d") == day_et]
            if len(m):
                parts.append(m.set_index("t")[["open", "high", "low", "close", "volume"]])
        if parts:
            df = pd.concat(parts)
            df = df[~df.index.duplicated(keep="last")].sort_index()
    _min_cache[day_et] = df
    return df


def traded_near(price: float, t: datetime, before: int = 60, after: int = 120) -> tuple[bool, str]:
    """Did `price` trade within [t-before, t+after]? Tape if there is one, else the minute bars' high/low range."""
    s = seconds_around(t, before, after)
    if s is not None and len(s):
        lo, hi = s["lo"].min(), s["hi"].max()
        tol = 0.5  # his fills are on NQ, our tape is MNQ: the two trade a tick or two apart at times
        hit = bool(((s["lo"] <= price + tol) & (s["hi"] >= price - tol)).any())
        return hit, f"tape {lo:.2f}-{hi:.2f}"
    bars = minute_bars(t.strftime("%Y-%m-%d"))
    if bars is None or not len(bars):
        return True, "no data"
    w = bars.loc[(t - timedelta(seconds=before)).replace(second=0): t + timedelta(seconds=after)]
    if not len(w):
        return True, "no bars in window"
    lo, hi = w["low"].min(), w["high"].max()
    return bool(lo - 0.25 <= price <= hi + 0.25), f"bars {lo:.2f}-{hi:.2f}"


def price_range(t0: datetime, t1: datetime) -> tuple[float, float] | None:
    s = seconds_around(t0, 0, int((t1 - t0).total_seconds()))
    if s is not None and len(s):
        return float(s["lo"].min()), float(s["hi"].max())
    bars = minute_bars(t0.strftime("%Y-%m-%d"))
    if bars is None or not len(bars):
        return None
    w = bars.loc[t0.replace(second=0): t1]
    return (float(w["low"].min()), float(w["high"].max())) if len(w) else None
