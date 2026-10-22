"""Builds the 5-minute bar files for the relative-volume test (RELVOL_PLAN.txt) and checks the volume series before the plan is
written.   python relvol_data.py [databento folder]

Writes (into this folder, skipped if present):
  nt_analyzer_bars.csv          NinjaTrader's own bars from the Strategy Analyzer export (agent_exports\\NoiseArea_bars_MNQ_strategy_
                                Backtest.csv, time_et renamed to time), cut after the 2026-10-14 session (the parity reference's end)
  mnq_db_5m_2019-2026.csv       Databento MNQ.c.0 1-minute 2018-2022 + 2023-2025 + 2026 files through convert.build
  nq_2010-2019_5m.csv           Databento NQ.c.0 1-minute 2010-2019 through convert.build
  mnq_db_5m.csv                 the 2018-2022 + 2023-2025 build used by every earlier test (to reproduce their baselines)
Volume check (no strategy P&L is computed here): daily RTH volume of Databento vs NinjaTrader on the same dates, by session
position relative to the Databento contract switch, so the calendar-roll artifact can be sized before the plan is fixed."""
import os, sys
import numpy as np, pandas as pd
from convert import build

DB = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\linde\OneDrive\Desktop\Desktop\databento_data'
NT_EXPORT = os.path.join('..', '..', 'NoiseArea_bars_MNQ_strategy_Backtest.csv')


def make_nt(out='nt_analyzer_bars.csv'):
    if os.path.exists(out): print(f"{out}: present, kept"); return
    df = pd.read_csv(NT_EXPORT).rename(columns={'time_et': 'time'})
    t = pd.to_datetime(df.time); df = df[t < pd.Timestamp('2026-10-14 18:00')]
    df.to_csv(out, index=False); print(f"{out}: {len(df)} bars {df.time.iloc[0]} -> {df.time.iloc[-1]} (from {NT_EXPORT})")


def make_db(files, out):
    if os.path.exists(out): print(f"{out}: present, kept"); return
    b, rolls = build([os.path.join(DB, f) for f in files], out)
    print(f"{out}: {len(b)} bars {b.time.min()} -> {b.time.max()}, {len(rolls)} contract switches; first {rolls[:2]} last {rolls[-2:]}")


def rth_daily(path):
    df = pd.read_csv(path, usecols=['time', 'volume']); t = pd.to_datetime(df.time); m = t.dt.hour * 60 + t.dt.minute
    r = df[(m > 570) & (m <= 960)].assign(date=t.dt.date)
    return r.groupby('date').volume.sum()


def switch_dates(files):
    import databento as dbn
    out = []
    for f in files:
        s = dbn.DBNStore.from_file(os.path.join(DB, f))
        for v in s.mappings.values():
            out += [pd.Timestamp(x['start_date']).date() for x in v]
    return sorted(set(out))


if __name__ == '__main__':
    mnq_files = ['mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst', 'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst', 'MNQ_ohlcv-1m_2026-2026.dbn.zst']
    make_nt()
    make_db(mnq_files[:2], 'mnq_db_5m.csv')
    make_db(mnq_files, 'mnq_db_5m_2019-2026.csv')
    make_db(['NQ_ohlcv-1m_2010-2019.dbn.zst'], 'nq_2010-2019_5m.csv')

    print("\nVOLUME CHECK (Databento .c.0 vs NinjaTrader, daily RTH volume 09:35-16:00, same dates)")
    nt = rth_daily('nt_analyzer_bars.csv'); db = rth_daily('mnq_db_5m_2019-2026.csv')
    j = pd.concat([nt.rename('nt'), db.rename('db')], axis=1, join='inner').dropna(); j = j[(j.nt > 0)]
    j['ratio'] = j.db / j.nt
    sw = [d for d in switch_dates(mnq_files) if d > pd.Timestamp('2019-06-01').date()]
    dates = np.array(j.index); pos = {}
    for k, d in enumerate(dates):
        # sessions until the next Databento switch (1 = last session before it), or after the previous one (-1 = first after)
        nxt = [s for s in sw if s > d]; prv = [s for s in sw if s <= d]
        before = (dates[k:] < nxt[0]).sum() if nxt else 99
        after = (dates[:k + 1] >= prv[-1]).sum() if prv else 99
        pos[d] = before if before <= 10 else (-after if after <= 3 else 0)
    j['pos'] = pd.Series(pos)
    print(f"   {len(j)} common sessions; median DB/NT volume ratio on ordinary sessions {j[j.pos == 0].ratio.median():.3f}")
    g = j.groupby('pos').ratio.agg(['count', 'median', 'min', 'max'])
    print("   sessions before (+n) / after (-n) the Databento contract switch, DB/NT volume ratio:")
    print(g.to_string(float_format=lambda v: f"{v:.3f}"))
    nq = rth_daily('nq_2010-2019_5m.csv')
    nq_sw = [d for d in switch_dates(['NQ_ohlcv-1m_2010-2019.dbn.zst']) if d > pd.Timestamp('2010-07-01').date()]
    nd = np.array(nq.index); rel = nq / nq.rolling(20, min_periods=10).median().shift(1)
    lastn = {}
    for k, d in enumerate(nd):
        nxt = [s for s in nq_sw if s > d]; before = (nd[k:] < nxt[0]).sum() if nxt else 99
        lastn[d] = before if before <= 10 else 0
    q = pd.DataFrame({'rel': rel, 'pos': pd.Series(lastn)}).dropna()
    print("   NQ 2010-2019 (no second source): RTH volume / median of the previous 20 sessions, by sessions before the switch:")
    print(q.groupby('pos').rel.agg(['count', 'median']).to_string(float_format=lambda v: f"{v:.3f}"))
