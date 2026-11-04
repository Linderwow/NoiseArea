"""What a 22-25 s stream delay costs when copying a 1-minute stop-and-reverse NQ strategy: (a) the three entries seen on the stream
this morning replayed on our MNQ tape (same price scale as NQ), (b) the size of 22-s moves on the tape against the streamer's stops.
Builds a per-second table (last trade, bid, ask) from the recorder's tape files by streaming them with pyarrow."""
import os, sys, time
import numpy as np, pandas as pd
import pyarrow as pa, pyarrow.csv as pacsv, pyarrow.compute as pc

TAPE = r"D:\nt_recorder\MNQ_12-26"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regime_work", "tape_seconds"); os.makedirs(OUT, exist_ok=True)
ET = 'America/New_York'


def per_second(day):
    cache = os.path.join(OUT, f'sec_{day}.parquet')
    if os.path.exists(cache): return pd.read_parquet(cache)
    path = os.path.join(TAPE, f'{day}_tape.csv'); parts = []
    reader = pacsv.open_csv(path, read_options=pacsv.ReadOptions(block_size=64 << 20),
                            convert_options=pacsv.ConvertOptions(include_columns=['kind', 'feed_utc', 'price', 'bid', 'ask'],
                                                                 column_types={'feed_utc': pa.string(), 'kind': pa.string()}))
    for batch in reader:
        d = batch.to_pandas()
        d['sec'] = d.feed_utc.str.slice(0, 19)
        tr = d[d.kind == 'T']
        g = d.groupby('sec', sort=False).agg(bid=('bid', 'last'), ask=('ask', 'last'))
        gt = tr.groupby('sec', sort=False).agg(last=('price', 'last'))
        parts.append(g.join(gt, how='left'))
    s = pd.concat(parts)
    s = s[~s.index.duplicated(keep='last')].sort_index()
    s.index = pd.to_datetime(s.index, utc=True).tz_convert(ET).tz_localize(None); s.index.name = 'et'
    s['last'] = s['last'].ffill()
    s = s.reindex(pd.date_range(s.index[0], s.index[-1], freq='s')).ffill()
    s.to_parquet(cache); return s


T0 = time.time()
s = pd.concat([per_second('2026-11-03'), per_second('2026-11-04')]); s = s[~s.index.duplicated(keep='last')]
print(f"per-second table {len(s):,} s  {s.index[0]} .. {s.index[-1]}  ({time.time() - T0:.0f} s)")

# ---- (a) the entries seen on the stream (ET, side, the streamer's fill price); fill second = first second that traded at that price
# in the minute shown (05:39 / 06:03 are minute stamps from the chart; 07:19:48-07:20:00 is the tape match the other session found)
fills = [('2026-11-04 05:39', 'short', 28579.75), ('2026-11-04 06:03', 'long', 28596.50), ('2026-11-04 07:19:48', 'long', 28420.00)]
print("\n(a) the stream's entries replayed on our tape; a copier fills at the ask (long) / bid (short) after the delay")
for tm, side, px in fills:
    t = pd.Timestamp(tm); w = s[(s.index >= t) & (s.index < t + pd.Timedelta(seconds=60))]
    hit = w[np.isclose(w['last'], px)]
    t0 = hit.index[0] if len(hit) else w.index[0]
    note = '' if len(hit) else f' (price {px} never traded in that minute; using the minute start; last then {w["last"].iloc[0]})'
    row = f"   {side:5} {px:.2f} at {t0:%H:%M:%S}{note}"
    for dly in (22, 25, 30):
        r = s.loc[t0 + pd.Timedelta(seconds=dly)]
        fill = r['ask'] if side == 'long' else r['bid']
        cost = (fill - px) if side == 'long' else (px - fill)
        row += f" | +{dly}s copier fill {fill:.2f} = {cost:+.2f} pts ({cost * 4:+.0f} ticks) worse"
    w60 = s[(s.index > t0 + pd.Timedelta(seconds=22)) & (s.index <= t0 + pd.Timedelta(seconds=82))]['last']
    mae = (px - w60.min()) if side == 'long' else (w60.max() - px)
    print(row + f" | worst vs the streamer's price in the next minute {mae:+.2f} pts")

# ---- (b) the size of a 22-s move, by session segment, against the stops 10 / 20 / 40 / 60 points (and MNQ-sized 2.5 / 5)
print("\n(b) |price(t+22 s) - price(t)| sampled every second, NQ/MNQ points")
def seg(name, lo, hi, days):
    x = s[(s.index.strftime('%H:%M') >= lo) & (s.index.strftime('%H:%M') < hi) & (s.index.normalize().isin(pd.to_datetime(days)))]['last']
    if len(x) < 1000: print(f"   {name}: too little data ({len(x)} s)"); return
    mv = (x.shift(-22) - x).dropna().abs()
    q = mv.quantile([0.5, 0.75, 0.9, 0.95, 0.99])
    over = {k: (mv >= k).mean() * 100 for k in (2.5, 5, 10, 20, 40, 60)}
    print(f"   {name:<34} n {len(mv):7,}  median {q[0.5]:5.2f}  p75 {q[0.75]:5.2f}  p90 {q[0.9]:5.2f}  p95 {q[0.95]:5.2f}  p99 {q[0.99]:6.2f} pts"
          f" | moves >= 2.5: {over[2.5]:4.1f}%  >= 5: {over[5]:4.1f}%  >= 10: {over[10]:4.1f}%  >= 20: {over[20]:4.1f}%  >= 40: {over[40]:4.2f}%  >= 60: {over[60]:4.2f}%")
    # a momentum entry: the direction of the last 60 s; the signed move in that direction over the next 22 s = the copier's expected cost
    prior = np.sign(x - x.shift(60)); fwd = (x.shift(-22) - x) * prior
    f = fwd[prior != 0].dropna()
    print(f"   {'':34} entering with the last minute's direction, the next 22 s go the same way by {f.mean():+.2f} pts on average (median {f.median():+.2f}); "
          f"the move is adverse to a late copier {(f > 0).mean() * 100:.0f}% of the time")
seg('RTH 09:30-16:00 (11-03)', '09:30', '16:00', ['2026-11-03'])
seg('pre-market 04:00-09:30 (11-03, 11-04)', '04:00', '09:30', ['2026-11-03', '2026-11-04'])
seg('overnight 18:00-04:00 (both)', '00:00', '04:00', ['2026-11-03', '2026-11-04'])
print(f"\nrun {time.time() - t0:.0f} s")
