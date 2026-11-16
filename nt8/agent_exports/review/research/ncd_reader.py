"""Decode NinjaTrader 8 .ncd history files (tick and minute) into pandas DataFrames.

Usage (from this folder):
    python ncd_reader.py "D:\\nt_ticks\\_work\\tick\\MNQ 03-26\\202601131700.Last.ncd"
    python ncd_reader.py "D:\\nt_ticks\\_work\\minute\\MNQ 03-26\\20260113.Last.ncd"

    from ncd_reader import read_tick_file, read_minute_file
    df = read_tick_file(path)      # one row per stored print
    bars = read_minute_file(path)  # one row per 1-minute bar

Only ever point this at COPIES of the NinjaTrader db files (ncd_export.py makes
them under D:\\nt_ticks\\_work); never read the live db folder from research code.
For research use the exported daily parquet files (D:\\nt_ticks\\MNQ\\<MM-YY>\\YYYY-MM-DD.parquet);
the Friday end-of-week 'artifact' flag needs a whole session and is set there, not per hour file.

File format (reverse-engineered, validated against Databento, see NCD_READER_RESULTS.txt)
  Header, 28 bytes little-endian:
    int32  version/time unit (=1)   double tick size   double base price
    int64  .NET DateTime ticks (100 ns) of the first record, LOCAL wall-clock time of
           the NinjaTrader time zone (US/Eastern on this PC), Kind=Unspecified.
  Then variable-length records; every multi-byte field is BIG-endian.
  Tick record:  mask1, mask2, [time delta], [price delta], [spread], volume
    mask1 & 0x07  time delta: 0 none | 1 u8 | 2 u16 | 3 u32 | 4 u64 (100 ns ticks) | 5 u8 seconds
    mask1 >> 6    price: 0 unchanged | 1 delta = (mask2 & 0x1F) - 16 | 2 u8 - 128 | 3 u32 - 2^31 (in ticks)
    mask1 & 0x38  0x38: u16 spread (>=256: at ask, value>>8) | 0x30: u8 spread (>=16: at ask, value>>4)
                  else bit 0x08 = trade at ask, spread = 1 + ((mask1 >> 4) & 3)
                  'spread' = ticks between the trade price and the opposite quote.
    mask2 >> 5    volume: 1 u8 | 2 u8*100 | 3 u8*500 | 4 u8*1000 | 5 u16 | 6 u32 | 7 u64
  Minute record: mask1, mask2, [time delta minutes], [open], [high], [low], [close], volume
    mask1 & 3 time (0 = +1 minute, 1/2/3 = u8/u16/u32 minutes); (mask1 >> 2) & 3 open
    (0 = unchanged, u8-128 / u16-32768 / u32-2^30 ticks vs the previous bar's OPEN;
    first bar vs the header base price); mask1 >> 5 volume as above.
    mask2 & 3 close-low, (mask2 >> 4) & 3 high-open, (mask2 >> 6) & 3 open-low (unsigned ticks);
    code 0 = 0 bytes (value 0), 1/2/3 = u8/u16/u32.  Bar time = bar END time.
"""
import sys
sys.dont_write_bytecode = True

import os
import struct

import numpy as np
import pandas as pd

HEADER = struct.Struct("<iddq")
HEADER_SIZE = HEADER.size
NET_EPOCH_TICKS = 621355968000000000
LOCAL_TZ = "America/New_York"

_LEN4 = np.array([0, 1, 2, 4], np.int64)


def _build_tick_tables():
    m = np.arange(256)
    tcode = m & 7
    tlen = np.select([tcode == 0, tcode == 1, tcode == 2, tcode == 3, tcode == 4, tcode == 5],
                     [0, 1, 2, 4, 8, 1], default=-1).astype(np.int64)
    pcode = m >> 6
    plen = np.select([pcode == 2, pcode == 3], [1, 4], default=0).astype(np.int64)
    slen = np.where((m & 0x38) == 0x38, 2, np.where((m & 0x30) == 0x30, 1, 0)).astype(np.int64)
    vcode = m >> 5
    vlen = np.select([vcode == 0, vcode <= 4, vcode == 5, vcode == 6, vcode == 7],
                     [-1, 1, 2, 4, 8]).astype(np.int64)
    vmult = np.select([vcode == 2, vcode == 3, vcode == 4], [100, 500, 1000], default=1).astype(np.int64)
    return tlen, plen, slen, vlen, vmult


def _build_minute_tables():
    m = np.arange(256)
    tlen = _LEN4[m & 3]
    olen = _LEN4[(m >> 2) & 3]
    vcode = m >> 5
    vlen = np.select([vcode == 0, vcode <= 4, vcode == 5, vcode == 6, vcode == 7],
                     [-1, 1, 2, 4, 8]).astype(np.int64)
    vmult = np.select([vcode == 2, vcode == 3, vcode == 4], [100, 500, 1000], default=1).astype(np.int64)
    clen = _LEN4[m & 3]
    hlen = _LEN4[(m >> 4) & 3]
    llen = _LEN4[(m >> 6) & 3]
    return tlen, olen, vlen, vmult, clen, hlen, llen


_T_TLEN, _T_PLEN, _T_SLEN, _T_VLEN, _T_VMULT = _build_tick_tables()
_M_TLEN, _M_OLEN, _M_VLEN, _M_VMULT, _M_CLEN, _M_HLEN, _M_LLEN = _build_minute_tables()


class NcdFormatError(ValueError):
    pass


def read_header(buf):
    if len(buf) < HEADER_SIZE:
        raise NcdFormatError("file shorter than header")
    return HEADER.unpack_from(buf, 0)


def _chain(n, reclen, start=HEADER_SIZE):
    END, BAD = n, n + 1
    idx = np.int64 if n > 2**31 - 16 else np.int32
    tgt = np.arange(n, dtype=np.int64) + reclen
    nxt = np.empty(n + 2, idx)
    nxt[:n] = np.where(reclen <= 0, BAD, np.where(tgt > n, BAD, tgt))
    nxt[END] = END
    nxt[BAD] = BAD
    orbit = np.array([start], idx)
    jump = nxt
    while True:
        new = jump[orbit]
        orbit = np.concatenate([orbit, new])
        if new.max() >= n:
            break
        jump = jump[jump]
    mark = np.zeros(n + 2, bool)
    mark[orbit] = True
    if mark[BAD]:
        good = np.flatnonzero(mark[:n])
        raise NcdFormatError(f"record chain broken after offset {good[-1] if len(good) else start} of {n}")
    return np.flatnonzero(mark[:n]).astype(np.int64)


def _read_be(buf, pos, length):
    out = np.zeros(len(pos), np.uint64)
    if len(pos) == 0:
        return out
    maxlen = int(length.max()) if len(length) else 0
    for k in range(maxlen):
        sel = length > k
        out[sel] = (out[sel] << np.uint64(8)) | buf[pos[sel] + k].astype(np.uint64)
    return out


def ticks_to_utc(local_ticks):
    ns = (np.asarray(local_ticks, np.int64) - NET_EPOCH_TICKS) * 100
    naive = pd.DatetimeIndex(ns.astype("datetime64[ns]"))
    try:
        loc = naive.tz_localize(LOCAL_TZ, ambiguous="infer", nonexistent="shift_forward")
    except Exception:
        loc = naive.tz_localize(LOCAL_TZ, ambiguous=np.ones(len(naive), bool), nonexistent="shift_forward")
    return loc.tz_convert("UTC")


def local_naive(local_ticks):
    return pd.DatetimeIndex(((np.asarray(local_ticks, np.int64) - NET_EPOCH_TICKS) * 100).astype("datetime64[ns]"))


def is_sub_ms(local_ticks):
    return (np.asarray(local_ticks, np.int64) % 10_000) != 0


def eow_artifact_mask(local_naive_times, price, session_date):
    n = len(price)
    out = np.zeros(n, bool)
    d = pd.Timestamp(session_date).normalize()
    if n < 2 or d.weekday() != 4:
        return out
    loc = pd.DatetimeIndex(local_naive_times)
    price = np.asarray(price, float)
    final = price[-1]
    t16 = d + pd.Timedelta(hours=16)
    win = (loc >= t16 - pd.Timedelta("100ms")) & (loc < t16)
    ctx = (loc >= t16 - pd.Timedelta("1s")) & (loc < t16 + pd.Timedelta("100ms")) & (price != final)
    if not ctx.any():
        return out
    ref = float(np.median(price[ctx]))
    return np.asarray(win & (price == final) & (np.abs(price - ref) >= 0.5))


def decode_tick_bytes(raw):
    version, tick_size, base_price, t0 = read_header(raw)
    n_body = len(raw) - HEADER_SIZE
    empty = dict(local_ticks=np.zeros(0, np.int64), price_ticks=np.zeros(0, np.int64),
                 volume=np.zeros(0, np.int64), at_ask=np.zeros(0, bool),
                 spread=np.zeros(0, np.int64))
    meta = dict(version=version, tick_size=tick_size, base_price=base_price, start_ticks=t0)
    if n_body == 0:
        return empty, meta
    buf = np.frombuffer(raw, np.uint8)
    pad = np.concatenate([buf, np.zeros(16, np.uint8)])
    n = len(raw)
    m1all = pad[:n].astype(np.int64)
    m2all = pad[1:n + 1].astype(np.int64)
    tl, vl = _T_TLEN[m1all], _T_VLEN[m2all]
    reclen = np.where((tl < 0) | (vl < 0), -1, 2 + tl + _T_PLEN[m1all] + _T_SLEN[m1all] + vl)
    starts = _chain(n, reclen)
    m1 = pad[starts].astype(np.int64)
    m2 = pad[starts + 1].astype(np.int64)
    tpos = starts + 2
    tlen = _T_TLEN[m1]
    dt = _read_be(pad, tpos, tlen).astype(np.int64)
    dt = np.where((m1 & 7) == 5, dt * 10_000_000, dt)
    ppos = tpos + tlen
    plen = _T_PLEN[m1]
    praw = _read_be(pad, ppos, plen).astype(np.int64)
    pcode = m1 >> 6
    dp = np.select([pcode == 1, pcode == 2, pcode == 3],
                   [(m2 & 0x1F) - 16, praw - 128, praw - 2**31], default=0)
    spos = ppos + plen
    slen = _T_SLEN[m1]
    sraw = _read_be(pad, spos, slen).astype(np.int64)
    at_ask = np.where(slen == 2, sraw >= 256, np.where(slen == 1, sraw >= 16, (m1 & 8) != 0))
    spread = np.where(slen == 2, np.where(sraw >= 256, sraw >> 8, sraw),
                      np.where(slen == 1, np.where(sraw >= 16, sraw >> 4, sraw), 1 + ((m1 >> 4) & 3)))
    vpos = spos + slen
    vol = _read_be(pad, vpos, _T_VLEN[m2]).astype(np.int64) * _T_VMULT[m2]
    base_ticks = int(round(base_price / tick_size))
    out = dict(local_ticks=t0 + np.cumsum(dt), price_ticks=base_ticks + np.cumsum(dp),
               volume=vol, at_ask=at_ask.astype(bool), spread=spread)
    return out, meta


def read_tick_file(path, utc=True):
    with open(path, "rb") as f:
        raw = f.read()
    d, meta = decode_tick_bytes(raw)
    ts = meta["tick_size"]
    price = d["price_ticks"] * ts
    spread_px = d["spread"] * ts
    at_ask = d["at_ask"]
    time = ticks_to_utc(d["local_ticks"]) if utc else local_naive(d["local_ticks"])
    df = pd.DataFrame({
        "time": time,
        "price": price,
        "volume": d["volume"],
        "side": np.where(at_ask, "A", "B"),
        "bid": np.where(at_ask, price - spread_px, price),
        "ask": np.where(at_ask, price, price + spread_px),
        "sub_ms": is_sub_ms(d["local_ticks"]),
    })
    df.attrs.update(meta)
    df.attrs["start_local"] = str(pd.Timestamp((meta["start_ticks"] - NET_EPOCH_TICKS) * 100))
    return df


def decode_minute_bytes(raw):
    version, tick_size, base_price, t0 = read_header(raw)
    meta = dict(version=version, tick_size=tick_size, base_price=base_price, start_ticks=t0)
    n = len(raw)
    if n == HEADER_SIZE:
        z = np.zeros(0, np.int64)
        return dict(local_ticks=z, o=z, h=z, l=z, c=z, volume=z), meta
    buf = np.frombuffer(raw, np.uint8)
    pad = np.concatenate([buf, np.zeros(16, np.uint8)])
    m1all = pad[:n].astype(np.int64)
    m2all = pad[1:n + 1].astype(np.int64)
    vl = _M_VLEN[m1all]
    reclen = np.where(vl < 0, -1, 2 + _M_TLEN[m1all] + _M_OLEN[m1all] + vl
                      + _M_HLEN[m2all] + _M_LLEN[m2all] + _M_CLEN[m2all])
    starts = _chain(n, reclen)
    m1 = pad[starts].astype(np.int64)
    m2 = pad[starts + 1].astype(np.int64)
    p = starts + 2
    L = _M_TLEN[m1]
    dmin = _read_be(pad, p, L).astype(np.int64)
    dmin = np.where((m1 & 3) == 0, 1, dmin)
    dmin[0] = np.where((m1[0] & 3) == 0, 0, dmin[0])
    p = p + L
    L = _M_OLEN[m1]
    oraw = _read_be(pad, p, L).astype(np.int64)
    ocode = (m1 >> 2) & 3
    dopen = np.select([ocode == 1, ocode == 2, ocode == 3], [oraw - 128, oraw - 32768, oraw - 2**30], default=0)
    p = p + L
    L = _M_HLEN[m2]
    dh = _read_be(pad, p, L).astype(np.int64)
    p = p + L
    L = _M_LLEN[m2]
    dl = _read_be(pad, p, L).astype(np.int64)
    p = p + L
    L = _M_CLEN[m2]
    dc = _read_be(pad, p, L).astype(np.int64)
    p = p + L
    vol = _read_be(pad, p, _M_VLEN[m1]).astype(np.int64) * _M_VMULT[m1]
    base_ticks = int(round(base_price / tick_size))
    o = base_ticks + np.cumsum(dopen)
    h = o + dh
    lo = o - dl
    c = lo + dc
    out = dict(local_ticks=t0 + np.cumsum(dmin) * 600_000_000, o=o, h=h, l=lo, c=c, volume=vol)
    return out, meta


def read_minute_file(path, utc=True):
    with open(path, "rb") as f:
        raw = f.read()
    d, meta = decode_minute_bytes(raw)
    ts = meta["tick_size"]
    time = ticks_to_utc(d["local_ticks"]) if utc else local_naive(d["local_ticks"])
    df = pd.DataFrame({"time": time, "open": d["o"] * ts, "high": d["h"] * ts,
                       "low": d["l"] * ts, "close": d["c"] * ts, "volume": d["volume"]})
    df.attrs.update(meta)
    return df


def read_file(path, utc=True):
    parts = [p.lower() for p in os.path.normpath(path).split(os.sep)]
    if "minute" in parts:
        return read_minute_file(path, utc)
    return read_tick_file(path, utc)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    pd.set_option("display.width", 160)
    for path in sys.argv[1:]:
        df = read_file(path)
        print(path, len(df), "rows", dict(df.attrs))
        print(df.head(10).to_string())
        print(df.tail(3).to_string())
