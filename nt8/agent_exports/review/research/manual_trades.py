import os, sys, glob
sys.dont_write_bytecode = True
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from manual_book import fills, roundtrips, decisions, PV, FEE, ET


def minutes(contract):
    code = contract.split()[-1]
    m = pd.read_parquet(f'D:/nt_ticks/minute/MNQ/{code}.parquet')
    m = pd.DataFrame({'o': m.open.values, 'h': m.high.values, 'l': m.low.values, 'c': m.close.values},
                     index=pd.DatetimeIndex(m.time).tz_convert(ET).tz_localize(None))
    parts = [m]
    for f in sorted(glob.glob(f'D:/nt_recorder/MNQ_{code}/*_bars1minute.csv')):
        x = pd.read_csv(f)
        if x.empty: continue
        parts.append(pd.DataFrame({'o': x.open.values, 'h': x.high.values, 'l': x.low.values, 'c': x.close.values},
                                  index=pd.to_datetime(x.time_et)))
    a = pd.concat(parts); a = a[~a.index.duplicated(keep='first')].sort_index()
    return a


def hourly(m):
    key = (m.index - pd.Timedelta(minutes=1)).floor('h') + pd.Timedelta(hours=1)
    g = m.groupby(key)
    h = pd.DataFrame({'o': g.o.first(), 'h': g.h.max(), 'l': g.l.min(), 'c': g.c.last(), 'n': g.c.size()})
    h = h[h.n >= 30].copy()
    h['e50'] = h.c.ewm(span=50, adjust=False).mean(); h['e200'] = h.c.ewm(span=200, adjust=False).mean()
    return h


if __name__ == '__main__':
    f = fills(); t = roundtrips(f); d = decisions(t)
    print(f"{len(f):,} MNQ fills on {f.acct.nunique()} Bulenox accounts {f.et.min():%Y-%m-%d} .. {f.et.max():%Y-%m-%d}")
    print(f"{len(t):,} round trips; {len(d):,} distinct decisions (same entry second + side across copied accounts)")
    print(d.accts.value_counts().sort_index().to_string())
    m = t[t.acct == 'ACCOUNT-104']
    print(m[['entry_et', 'exit_et', 'dir', 'qty', 'entry', 'exit', 'pts', 'usd', 'hold_s', 'entry_name', 'exit_name']].to_string())
