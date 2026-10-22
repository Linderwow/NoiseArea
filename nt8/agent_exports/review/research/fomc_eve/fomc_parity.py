r"""Parity check of a Strategy Analyzer run of NoiseAreaStrategy against the references (FOMC-eve add-on deployment, 2026-10-20).
The strategy writes the Analyzer's trades to agent_exports\NoiseArea_trades_MNQ_Backtest_<time>.csv by itself.
    python fomc_parity.py off [file]    switch OFF run: must equal s1_reference_trades_ntbars_v3.csv (1,666 trades 2019-05-28 .. 2026-10-14)
    python fomc_parity.py on  [file]    switch ON run: the same minus the 7 NoiseArea longs kept into a hold, plus the 58 FOMC-eve rows
                                        of fomc_eve_reference_trades.csv (1,717); add-on rows compared in points (bars are back-adjusted)
Without [file] the newest Backtest trade file is used. Times are the fill bars' close stamps (entry 10:05 = decision 10:00).
"""
import os, sys, glob
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); EXP = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
import pandas as pd

REF = os.path.join(HERE, '..', 's1_reference_trades_ntbars_v3.csv'); FOMC = os.path.join(HERE, 'fomc_eve_reference_trades.csv')
END = pd.Timestamp('2026-10-14 23:59')


def load_run(path):
    t = pd.read_csv(path); t['et'] = pd.to_datetime(t.entry_time_et); t['xt'] = pd.to_datetime(t.exit_time_et)
    t['dir'] = t.direction.map({'long': 1, 'short': -1}); t['pts'] = (t.exit_price - t.entry_price) * t.dir
    return t[t.et <= END].reset_index(drop=True)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode not in ('on', 'off'): print(__doc__); return
    path = sys.argv[2] if len(sys.argv) > 2 else max(glob.glob(os.path.join(EXP, 'NoiseArea_trades_MNQ_Backtest_*.csv')), key=os.path.getmtime)
    run = load_run(path); ref = pd.read_csv(REF, parse_dates=['entry_time', 'exit_time'])
    ref['pts'] = (ref.exit - ref.entry) * ref.dir; ref = ref[ref.entry_time <= END]
    print(f"run file: {os.path.basename(path)} - {len(run)} trades to 2026-10-14 (Analyzer net ${run.profit_currency.sum():,.2f})")
    # keyed on (entry bar, direction); the exit may be stamped up to 5 minutes later in the Analyzer (the 16:00 flat is logged on the
    # 16:05 bar) and the Analyzer's points include 1 tick of slippage a side (0.5 point a round trip) that the references do not
    if mode == 'on':
        fe = pd.read_csv(FOMC, parse_dates=['entry_bar', 'exit_bar']); kept = fe[fe.note.fillna('').str.startswith('NoiseArea long kept')]
        drop = {pd.Timestamp(e) for e in kept.entry_bar}                         # NoiseArea longs whose exit moves to the next 09:35
        exp_na = ref[~((ref.dir == 1) & ref.entry_time.isin(drop))]
        exp = {(r.entry_time, int(r.dir)): (r.exit_time, r.pts) for r in exp_na.itertuples()}
        exp.update({(r.entry_bar, 1): (r.exit_bar, r.points) for r in fe.itertuples()})
        print(f"expected: {len(ref)} reference trades - {len(drop)} kept longs + {len(fe)} FOMC-eve rows = {len(exp)} "
              f"({len(fe) - len(drop)} add-on longs entered at 16:00 + {len(drop)} NoiseArea longs held into the next 09:30)")
    else:
        exp = {(r.entry_time, int(r.dir)): (r.exit_time, r.pts) for r in ref.itertuples()}; print(f"expected: {len(exp)} reference trades")
    got = {(r.et, int(r.dir)): (r.xt, r.pts) for r in run.itertuples()}
    both = set(exp) & set(got); miss = sorted(set(exp) - set(got)); extra = sorted(set(got) - set(exp))
    bad_x = [x for x in both if abs((got[x][0] - exp[x][0]).total_seconds()) > 300]
    dp = {x: got[x][1] - (exp[x][1] - 0.5) for x in both}
    bad_p = [x for x in both if x not in bad_x and abs(dp[x]) > 2.0]
    ok = len(both) - len(bad_x) - len(bad_p)
    print(f"matched entries {len(both)} of {len(exp)}; missing {len(miss)}; extra {len(extra)}; exit time off by > 5 min {len(bad_x)}; "
          f"points off by > 2 pt {len(bad_p)}; exact within 1 tick {sum(abs(v) <= 0.25 for v in dp.values())}")
    for lab, xs in (('missing', miss[:8]), ('extra', extra[:8]), ('exit differs', sorted(bad_x)[:8]), ('points differ', sorted(bad_p)[:8])):
        for x in xs:
            e, g = exp.get(x), got.get(x)
            print(f"   {lab}: entry {x[0]} dir {x[1]:+d}" + (f" | ref exit {e[0]} {e[1]:+.2f} pt" if e else '') + (f" | run exit {g[0]} {g[1]:+.2f} pt" if g else ''))
    if mode == 'on':
        fk = {(r.entry_bar, 1) for r in fe.itertuples()}; fm = [x for x in fk if x in both and x not in bad_x and x not in bad_p]
        print(f"FOMC-eve rows: {len(fm)} of {len(fk)} matched (entry, exit, points)")
    share = ok / max(len(exp), 1)
    print(("PARITY PASSED" if share >= 0.95 and len(extra) <= 0.05 * len(exp) else "PARITY CHECK - look at the list above") +
          f": {ok} of {len(exp)} expected trades match ({share * 100:.1f}%; gate 95%)")

if __name__ == '__main__':
    main()
