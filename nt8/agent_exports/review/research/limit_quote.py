r"""Exact Databento price (metadata.get_cost only - free; nothing downloaded or bought) of MNQ tick data for a limit-order entry/exit
test: a window around EVERY NoiseArea order (entries and exits of the 1,666 reference trades, 2019-05 .. 2026-10).
    python limit_quote.py      -> limit_quote.txt, limit_quote_windows.csv
Window per order: fill-bar open - 1 minute .. fill-bar open + 5 minutes (the reference stamps fills at the fill bar's CLOSE, so the
fill-bar open = stamp - 5 min; the 16:00 session flat falls inside its 15:54-16:00 window). Covers a limit held 60 s, the market
fallback and a few seconds of latency. Schemas: trades (every trade with its aggressor side), tbbo (each trade with the best bid/
offer just before it - lets a limit fill be judged against the book), mbp-1 (every top-of-book change - the most realistic queue).
"""
import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from tickdata_quote import run_jobs, utc

SCHEMAS = ('trades', 'tbbo', 'mbp-1')


def windows():
    t = pd.read_csv(os.path.join(HERE, 's1_reference_trades_ntbars_v3.csv'), parse_dates=['entry_time', 'exit_time'])
    opens = pd.concat([t.entry_time, t.exit_time]) - pd.Timedelta(minutes=5)
    w = pd.DataFrame({'start': opens - pd.Timedelta(minutes=1), 'end': opens + pd.Timedelta(minutes=5)}).drop_duplicates().sort_values('start')
    return t, w.reset_index(drop=True)


def main():
    t, w = windows()
    lines = [f"Databento GLBX.MDP3 exact cost quotes for a limit-order test, {pd.Timestamp.now():%Y-%m-%d %H:%M} (metadata.get_cost; nothing "
             f"downloaded or bought)", f"orders: {len(t)} entries + {len(t)} exits of s1_reference_trades_ntbars_v3.csv "
             f"({t.entry_time.min():%Y-%m-%d} .. {t.exit_time.max():%Y-%m-%d}) -> {len(w)} distinct 6-minute windows, symbol MNQ.v.0"]
    for sc in SCHEMAS:
        jobs = [(i, 'MNQ.v.0', sc, utc(r.start), utc(r.end)) for i, r in enumerate(w.itertuples())]
        res = dict(run_jobs(jobs)); w[sc] = [res[i] for i in range(len(w))]
        miss = int(w[sc].isna().sum()); by = w.groupby(w.start.dt.year)[sc].sum()
        lines.append(f"   {sc:6s}: ${w[sc].sum():,.2f} total ({miss} windows not quoted); by year " + ' '.join(f"{y}:${v:.2f}" for y, v in by.items()))
        print(lines[-1], flush=True)
    w.to_csv(os.path.join(HERE, 'limit_quote_windows.csv'), index=False)
    for yrs, lab in (((2025, 2026), '2025-2026 only'), ((2023, 2024, 2025, 2026), '2023-2026 only')):
        m = w.start.dt.year.isin(yrs)
        lines.append(f"   {lab}: " + ' | '.join(f"{sc} ${w.loc[m, sc].sum():,.2f}" for sc in SCHEMAS) + f" ({int(m.sum())} windows)")
    txt = '\n'.join(lines); print(txt); open(os.path.join(HERE, 'limit_quote.txt'), 'w', encoding='utf-8').write(txt + '\n')


if __name__ == '__main__':
    main()
