"""
downsample_trades.py - turn a Databento trades file (GLBX.MDP3, schema 'trades', .dbn.zst) into 5-minute
order-flow bars small enough to share: OHLC, volume, buy/sell aggressor volume, delta, trade count, large prints,
point of control (POC) and value area (70% of volume) per bar. Times are US/Eastern, bars stamped at their close.

    pip install databento pandas
    python downsample_trades.py <trades.dbn.zst> <output.csv> [--minutes 5] [--big 20]

Memory-safe: the file is read in chunks of 2 million trades. A 1 GB file takes a few minutes.
"""
import sys, argparse, collections, numpy as np, pandas as pd, databento as db

ap = argparse.ArgumentParser(); ap.add_argument('src'); ap.add_argument('out')
ap.add_argument('--minutes', type=int, default=5); ap.add_argument('--big', type=int, default=20)
a = ap.parse_args()
W = f'{a.minutes}min'
agg = {}                                   # bucket -> dict of running values
vap = collections.defaultdict(lambda: collections.defaultdict(float))   # bucket -> price -> volume
store = db.DBNStore.from_file(a.src)
n = 0
for df in store.to_df(count=2_000_000):
    df = df[df.price > 0]
    if 'symbol' in df.columns and df.symbol.nunique() > 1:
        df = df[df.symbol == df.symbol.mode().iloc[0]]        # keep the front contract if several are present
    b = df.index.floor(W)
    side = df.side.values; size = df['size'].values.astype(float); px = df.price.values
    # Databento side: 'B' = the aggressor was a BUYER (lifted the ask), 'A' = the aggressor was a SELLER (hit the bid), 'N' = unknown
    buy = np.where(side == 'B', size, 0.0); sell = np.where(side == 'A', size, 0.0)
    big_b = np.where((side == 'B') & (size >= a.big), size, 0.0); big_s = np.where((side == 'A') & (size >= a.big), size, 0.0)
    tmp = pd.DataFrame({'b': b, 'px': px, 'sz': size, 'buy': buy, 'sell': sell, 'bb': big_b, 'bs': big_s, 't': df.index})
    g = tmp.groupby('b', sort=True)
    stats = g.agg(open=('px', 'first'), high=('px', 'max'), low=('px', 'min'), close=('px', 'last'), vol=('sz', 'sum'),
                  buy=('buy', 'sum'), sell=('sell', 'sum'), n=('px', 'size'), bb=('bb', 'sum'), bs=('bs', 'sum'),
                  t0=('t', 'first'), t1=('t', 'last'))
    for k, r in stats.iterrows():
        if k in agg:
            x = agg[k]
            if r.t0 < x['t0']: x['open'] = r.open; x['t0'] = r.t0
            if r.t1 > x['t1']: x['close'] = r.close; x['t1'] = r.t1
            x['high'] = max(x['high'], r.high); x['low'] = min(x['low'], r.low)
            for f in ('vol', 'buy', 'sell', 'n', 'bb', 'bs'): x[f] += r[f]
        else:
            agg[k] = dict(open=r.open, high=r.high, low=r.low, close=r.close, vol=r.vol, buy=r.buy, sell=r.sell, n=r.n, bb=r.bb, bs=r.bs, t0=r.t0, t1=r.t1)
    for k, sub in tmp.groupby('b', sort=False):
        v = sub.groupby('px').sz.sum()
        d = vap[k]
        for p_, s_ in v.items(): d[p_] += s_
    n += len(df); print(f'  {n:,} trades, {len(agg):,} bars', file=sys.stderr)

rows = []
for k in sorted(agg):
    x = agg[k]; d = vap[k]
    prices = np.array(sorted(d)); vols = np.array([d[p_] for p_ in prices])
    poc = prices[vols.argmax()] if len(prices) else np.nan
    if len(prices):
        order = np.argsort(-vols); cum = np.cumsum(vols[order]); inva = order[:int(np.searchsorted(cum, 0.7 * vols.sum()) + 1)]
        vah, val = prices[inva].max(), prices[inva].min()
    else: vah = val = np.nan
    end = (k + pd.Timedelta(minutes=a.minutes)).tz_convert('America/New_York').tz_localize(None)
    rows.append(dict(time_et=end.strftime('%Y-%m-%d %H:%M:%S'), open=x['open'], high=x['high'], low=x['low'], close=x['close'],
                     volume=int(x['vol']), buy_vol=int(x['buy']), sell_vol=int(x['sell']), delta=int(x['buy'] - x['sell']), trades=int(x['n']),
                     big_buy=int(x['bb']), big_sell=int(x['bs']), poc=poc, vah=vah, val=val))
out = pd.DataFrame(rows); out.to_csv(a.out, index=False)
print(f'wrote {len(out):,} bars to {a.out} ({out.time_et.iloc[0]} -> {out.time_et.iloc[-1]})')
