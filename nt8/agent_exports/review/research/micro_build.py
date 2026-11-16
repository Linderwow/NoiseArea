import os, sys, glob, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
import numpy as np, pandas as pd

SRC = 'D:/nt_ticks/MNQ'
OUT = os.path.join(HERE, 'regime_work', 'micro')
NS = 23400


def day_files():
    by = {}
    for c in sorted(os.listdir(SRC)):
        for f in glob.glob(os.path.join(SRC, c, '*.parquet')):
            by.setdefault(os.path.basename(f)[:10], []).append(f)
    return by


def build_day(files):
    best = None
    for f in files:
        t = pd.read_parquet(f, columns=['time', 'price', 'volume', 'side', 'bid', 'ask', 'artifact'])
        t = t[~t.artifact]
        et = t.time.dt.tz_convert('America/New_York')
        s = (et.dt.hour * 3600 + et.dt.minute * 60 + et.dt.second).values - 34200
        m = (s >= 0) & (s < NS)
        if best is None or m.sum() > best[0]: best = (int(m.sum()), t[m], s[m], f)
    n, t, s, f = best
    if n < 50000: return None
    v = t.volume.values.astype(np.float64); buy = (t.side.astype(str).values == 'A')
    bv = np.bincount(s, weights=np.where(buy, v, 0.0), minlength=NS); sv = np.bincount(s, weights=np.where(buy, 0.0, v), minlength=NS)
    nt = np.bincount(s, minlength=NS).astype(np.float64)
    last = np.full(NS, -1, np.int64); last[s] = np.arange(len(s))
    idx = pd.Series(np.where(last >= 0, last, np.nan)).ffill().values
    ok = ~np.isnan(idx); ii = np.where(ok, idx, 0).astype(np.int64)
    bid = np.where(ok, t.bid.values[ii], np.nan); ask = np.where(ok, t.ask.values[ii], np.nan); px = np.where(ok, t.price.values[ii], np.nan)
    good = (ask >= bid) & (ask - bid <= 5.0)
    mid = np.where(good, (bid + ask) / 2, np.nan); mid = pd.Series(mid).ffill().values
    return dict(mid=mid.astype(np.float32), bid=bid.astype(np.float32), ask=ask.astype(np.float32), px=px.astype(np.float32),
                bv=bv.astype(np.float32), sv=sv.astype(np.float32), nt=nt.astype(np.float32), contract=os.path.basename(os.path.dirname(f)))


def main():
    os.makedirs(OUT, exist_ok=True); t0 = time.time(); by = day_files(); done = 0; skipped = []
    for d in sorted(by):
        p = os.path.join(OUT, d + '.npz')
        if os.path.exists(p): done += 1; continue
        r = build_day(by[d])
        if r is None: skipped.append(d); continue
        np.savez_compressed(p, **r); done += 1
    print(f"{done} sessions in {OUT}; skipped (< 50,000 cash-session trades): {skipped}; {time.time() - t0:.0f} s")


if __name__ == '__main__':
    main()
