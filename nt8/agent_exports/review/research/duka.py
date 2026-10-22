r"""Free 1-minute history from Dukascopy's public datafeed (no key, no cost): per-day BID_candles_min_1.bi5 files (LZMA, 24-byte
big-endian records: seconds from 00:00 UTC, open, close, low, high as int32 x point, volume float32).
    python duka.py probe                  which candidate symbols exist and their price scale
    python duka.py fetch SYMBOL [scale]   download 2019-05-01 .. 2026-10-16 -> regime_work\duka\<symbol>_1m.parquet
"""
import os, sys, lzma, struct, time, urllib.request
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
import numpy as np, pandas as pd

OUT = os.path.join(HERE, 'regime_work', 'duka')
URL = "https://datafeed.dukascopy.com/datafeed/{s}/{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
           'Referer': 'https://www.dukascopy.com/swiss/english/marketwatch/historical/'}      # without these the feed answers 503
CANDIDATES = ['GBPUSD', 'USDJPY', 'AUDUSD', 'USDCAD', 'USDMXN', 'NZDUSD', 'USDCHF', 'XAGUSD', 'XPTUSD', 'COPPER.CMD.USD', 'GAS.CMD.USD',
              'DIESEL.CMD.USD', 'SOYBEAN.CMD.USX', 'WHEAT.CMD.USX', 'CORN.CMD.USX', 'SOYBEANOIL.CMD.USX', 'ETHUSD', 'ETHUSD.CMD.USD',
              'USSC2000.IDX.USD', 'USA500.IDX.USD']


FAILED = []


def raw_day(sym, day, tries=8):
    """Bytes of one day's 1-minute candles; b'' = no data that day; None = 404 (symbol/day does not exist). The feed throttles with
    503s, so those are retried with growing waits; a day that still fails is recorded in FAILED (never silently dropped)."""
    u = URL.format(s=sym, y=day.year, m=day.month - 1, d=day.day)
    for k in range(tries):
        try:
            req = urllib.request.Request(u, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r: b = r.read()
            if not b: return b''
            return lzma.decompress(b)
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(min(30, 2 ** k))
        except Exception:
            time.sleep(min(30, 2 ** k))
    FAILED.append((sym, str(day.date())))
    return b''


def parse(b, day, scale):
    n = len(b) // 24
    if n == 0: return pd.DataFrame()
    a = np.frombuffer(b[:n * 24], dtype='>i4').reshape(n, 6)
    vol = np.frombuffer(b[:n * 24], dtype='>f4').reshape(n, 6)[:, 5]
    t = pd.Timestamp(day, tz='UTC') + pd.to_timedelta(a[:, 0], unit='s')
    return pd.DataFrame({'time_utc': t, 'open': a[:, 1] / scale, 'close': a[:, 2] / scale, 'low': a[:, 3] / scale, 'high': a[:, 4] / scale,
                         'volume': vol.astype(float)})


def probe():
    day = pd.Timestamp('2024-03-05')
    for s in CANDIDATES:
        b = raw_day(s, day)
        if b is None: print(f"{s:20s} not available", flush=True); continue
        a = np.frombuffer(b[:24 * (len(b) // 24)], dtype='>i4').reshape(-1, 6)
        print(f"{s:20s} {len(a)} minutes; first raw open {a[0, 1] if len(a) else None}", flush=True)


def fetch(sym, scale, a='2019-05-01', b='2026-10-16', workers=12):
    from concurrent.futures import ThreadPoolExecutor
    os.makedirs(OUT, exist_ok=True)
    days = [d for d in pd.date_range(a, b) if d.weekday() < 5 or d.weekday() == 6]
    t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(lambda d: (d, raw_day(sym, d)), days))
    frames = [parse(x, d, scale) for d, x in parts if x]
    df = pd.concat([f for f in frames if len(f)], ignore_index=True).sort_values('time_utc')
    miss = sum(1 for _, x in parts if x is None)
    df.to_parquet(os.path.join(OUT, f"{sym.replace('.', '_')}_1m.parquet"), index=False)
    print(f"{sym}: {len(df):,} minutes {df.time_utc.min()} .. {df.time_utc.max()}, {miss} days unavailable, {time.time() - t0:.0f}s", flush=True)


if __name__ == '__main__':
    if sys.argv[1:2] == ['probe']: probe()
    elif sys.argv[1:2] == ['fetch']: fetch(sys.argv[2], float(sys.argv[3]) if len(sys.argv) > 3 else 1e5)
    else: print(__doc__)
