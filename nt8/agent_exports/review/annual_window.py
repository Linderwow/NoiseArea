r"""
annual_window.py - like-for-like yearly comparison for the January re-validation routine (2026-10-20).

    python annual_window.py "<agent_exports folder>" --account Sim101 --year 2026

The live record of a year may start late (NoiseArea went live on 2026-10-15), and a partial year must not be judged against
full-year ranges. This compares the live window (strategy start or Jan 1 -> Dec 31) with the SAME calendar window of every
earlier year in the validated reference trade list (research\s1_reference_trades_ntbars_v3.csv), in percent of notional so
price levels are comparable: points / price, the reference priced at the raw 09:30 open (research\raw_open930.csv; the
reference file's prices are back-adjusted). Reference trades are charged 0.5 point (1 tick per side) for slippage; live fills
already contain theirs; commission is ignored on both sides. Prints a table and one verdict line. Read-only.
"""
import argparse, datetime as dt, glob, os
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REF = os.path.join(HERE, 'research', 's1_reference_trades_ntbars_v3.csv')
RAW = os.path.join(HERE, 'research', 'raw_open930.csv')
SLIP_PTS = 0.5


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('folder'); ap.add_argument('--account', default='Sim101'); ap.add_argument('--year', type=int, required=True)
    a = ap.parse_args(); Y = a.year
    files = sorted(glob.glob(os.path.join(a.folder, f"NoiseArea_trades_MNQ_{a.account}_*.csv")))
    if not files: print(f"no trade files for account {a.account}"); return
    tr = pd.concat([pd.read_csv(f) for f in files if os.path.getsize(f) > 0], ignore_index=True)
    tr = tr.drop_duplicates(['entry_time_et', 'exit_time_et', 'direction', 'entry_price', 'exit_price'])
    tr['xt'] = pd.to_datetime(tr.exit_time_et)
    starts = []
    for f in files:
        b = os.path.basename(f).split('_')
        try: starts.append(dt.datetime.strptime(b[-2] + b[-1][:6], '%Y%m%d%H%M%S').date())
        except Exception: pass
    start = max(dt.date(Y, 1, 1), min(starts)) if starts else dt.date(Y, 1, 1)
    end = dt.date(Y, 12, 31)
    lv = tr[(tr.xt.dt.date >= start) & (tr.xt.dt.date <= end)]
    live_ret = float((lv.profit_points / lv.entry_price).sum()) * 100 if len(lv) else 0.0
    live_usd = float(lv.profit_currency.sum()) if len(lv) else 0.0
    p_now = float(lv.entry_price.iloc[-1]) if len(lv) else np.nan

    ref = pd.read_csv(REF, parse_dates=['date', 'exit_time'])
    raw = pd.read_csv(RAW, index_col=0, parse_dates=True).open
    px = ref.date.map(raw).fillna(ref.entry)
    ref['ret'] = (ref.points - SLIP_PTS) / px * 100
    rows = []
    for y in range(2019, Y):
        try: s = start.replace(year=y)
        except ValueError: s = start.replace(year=y, day=28)
        w = ref[(ref.exit_time.dt.date >= s) & (ref.exit_time.dt.date <= dt.date(y, 12, 31))]
        first_ref = ref.exit_time.min().date()
        if s < first_ref: continue                     # the reference does not cover this window fully
        rows.append((y, len(w), float(w.ret.sum())))
    full_year = start <= dt.date(Y, 1, 31)
    print(f"Live window {start} .. {end} ({'full year' if full_year else 'PARTIAL year'}): {len(lv)} trades, net ${live_usd:,.2f}, "
          f"{live_ret:+.2f}% of notional")
    if not rows: print("no reference year covers this window"); return
    print(f"Same window in the reference ({SLIP_PTS} pt/trade slippage, % of notional; today's $ per micro at {p_now:,.2f}):")
    for y, n, r in rows:
        print(f"   {y}: {n:3d} trades  {r:+7.2f}%" + (f"  = ${r / 100 * 2 * p_now:+,.0f}" if not np.isnan(p_now) else ""))
    vals = [r for _, _, r in rows]; lo, hi = min(vals), max(vals)
    inside = lo <= live_ret <= hi
    print(f"Reference range for this window: {lo:+.2f}% .. {hi:+.2f}% (median {np.median(vals):+.2f}%) over {len(vals)} years")
    print(f"VERDICT: {'INSIDE' if inside else 'OUTSIDE'} the historical range for the same calendar window "
          f"(live {live_ret:+.2f}% of notional, ${live_usd:,.0f})" + ("" if full_year else " - partial year: a short sample, read it as a sanity check"))


if __name__ == '__main__':
    main()
