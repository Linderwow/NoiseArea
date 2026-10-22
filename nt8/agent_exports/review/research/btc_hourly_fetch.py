r"""Free hourly BTC-USD candles from Coinbase Exchange's public API (no key, no cost), 2015-01 .. now, for the weekend-crypto test.
    python btc_hourly_fetch.py  -> event_calendars\btc_usd_hourly_coinbase.csv (time_utc, open, high, low, close, volume)
"""
import os, sys, time, json, urllib.request
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
import pandas as pd

OUT = os.path.join(HERE, 'event_calendars', 'btc_usd_hourly_coinbase.csv')
URL = "https://api.exchange.coinbase.com/products/BTC-USD/candles?granularity=3600&start={s}&end={e}"


def get(s, e, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(URL.format(s=s.strftime('%Y-%m-%dT%H:%M:%SZ'), e=e.strftime('%Y-%m-%dT%H:%M:%SZ')), headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r: return json.loads(r.read())
        except Exception:
            time.sleep(1 + 2 * k)
    return []


def main():
    start = pd.Timestamp('2015-01-01', tz='UTC'); end = pd.Timestamp.now(tz='UTC').floor('h'); rows = []; n = 0
    t = start
    while t < end:
        e = min(t + pd.Timedelta(hours=299), end); rows += get(t, e); t = e + pd.Timedelta(hours=1); n += 1
        if n % 50 == 0: print(f"  {n} requests, up to {t:%Y-%m-%d}, {len(rows)} candles", flush=True)
        time.sleep(0.15)
    df = pd.DataFrame(rows, columns=['ts', 'low', 'high', 'open', 'close', 'volume']).drop_duplicates('ts').sort_values('ts')
    df['time_utc'] = pd.to_datetime(df.ts, unit='s', utc=True)
    df[['time_utc', 'open', 'high', 'low', 'close', 'volume']].to_csv(OUT, index=False)
    print(f"{len(df)} hourly candles {df.time_utc.min()} .. {df.time_utc.max()} -> {OUT}")


if __name__ == '__main__':
    main()
