"""Cross-market and long-history test per MARKETS_PLAN.txt.
   python3 markets.py <folder with the Databento .dbn.zst files>      (converts each file to 5-min ET bars, then runs the plan)"""
import sys, os, glob, numpy as np, pandas as pd
from convert import build
from research_engine import Market, fam_A, stats, WARMUP

SPEC = {   # symbol: (point value $, tick, commission per side, label)
    'MGC': (10.0, 0.10, 0.50, 'micro gold'), 'ZN': (1000.0, 1 / 64, 2.00, '10-year note'), 'M6E': (12500.0, 0.0001, 0.50, 'micro euro'),
    'MCL': (100.0, 0.01, 0.50, 'micro crude'), 'MBT': (0.10, 5.0, 0.50, 'micro bitcoin'), 'NQ': (20.0, 0.25, 2.00, 'NQ full-size'),
    'ES': (50.0, 0.25, 2.00, 'ES full-size'), 'MNQ': (2.0, 0.25, 0.50, 'micro Nasdaq'), 'MES': (5.0, 0.25, 0.50, 'micro S&P'),
    'M2K': (5.0, 0.10, 0.50, 'micro Russell 2000'), 'MYM': (0.50, 1.00, 0.50, 'micro Dow'),
}


def tstat(x): x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def run_market(sym, csv, a, b, vm=1.0):
    pv, tick, comm, _ = SPEC[sym]
    mk = Market(csv, pv, sym)
    pnl, nt = fam_A(mk, vm=vm, slip=tick, comm=comm)
    s = stats(mk, pnl, nt, a, b)
    daily = pd.Series(np.nan_to_num(pnl)[WARMUP:], index=mk.dates[WARMUP:])
    # average daily range in points, for a size-free comparison
    rng = np.mean([x.H.max() - x.L.min() for x in mk.days[WARMUP:]]); s['net_in_ranges'] = s['net'] / (rng * pv) if rng > 0 else np.nan
    return s, daily, mk


if __name__ == '__main__':
    folder = sys.argv[1]
    files = sorted(glob.glob(os.path.join(folder, '*_ohlcv-1m_*.dbn.zst')))
    print("files:", [os.path.basename(f) for f in files])
    results = {}; dailies = {}
    for f in files:
        sym = os.path.basename(f).split('_')[0].upper()
        if sym not in SPEC: print("skip", f); continue
        csv = f"{sym.lower()}_{os.path.basename(f).split('_')[2].split('.')[0]}_5m.csv"
        if not os.path.exists(csv):
            bars, rolls = build([f], csv); print(f"{sym}: {len(bars)} 5-min bars {bars.time.min()} -> {bars.time.max()}, {len(rolls)} contract switches")
        results[(sym, csv)] = None
    print("\n=== A. CROSS-MARKET (same rules, same clock) ===")
    print(f"{'market':14s} {'period':23s} {'disc net':>10s} {'disc t':>7s} {'val net':>10s} {'val t':>7s} {'full net':>10s} {'full t':>7s} {'trades':>7s} {'maxDD':>8s}  verdict")
    passers = []
    for (sym, csv) in results:
        if sym in ('NQ', 'ES') and '2010' in csv: continue
        if sym == 'MNQ': print("MNQ 2026 file: parity reference only (plan item C), not tested here"); continue
        d, daily, mk = run_market(sym, csv, '2019-01-01', '2022-12-31')
        v, _, _ = run_market(sym, csv, '2023-01-01', '2026-12-31')
        f_, _, _ = run_market(sym, csv, '2019-01-01', '2026-12-31')
        ok = d['net'] > 0 and v['net'] > 0 and f_['t'] > 2.0
        print(f"{sym:14s} {str(mk.dates[WARMUP].date())+'..'+str(mk.dates[-1].date()):23s} {d['net']:10,.0f} {d['t']:7.2f} {v['net']:10,.0f} {v['t']:7.2f} {f_['net']:10,.0f} {f_['t']:7.2f} {f_['trades']:7d} {f_['mdd']:8,.0f}  {'PASS' if ok else 'fail'}")
        raw = pd.read_csv(csv, usecols=['time']); raw['time'] = pd.to_datetime(raw.time); rd = raw[(raw.time.dt.hour*60+raw.time.dt.minute >= 575) & (raw.time.dt.hour*60+raw.time.dt.minute <= 960)].time.dt.date.nunique()
        print(f"   valid sessions {mk.n} of {rd} RTH days in file ({100*mk.n/max(rd,1):.0f}%); disc days {d['days']} trades {d['trades']} | val days {v['days']} trades {v['trades']} | PF full {f_['pf']:.2f}")
        dailies[sym] = daily
        for vm in (0.75, 1.5):
            x, _, _ = run_market(sym, csv, '2019-01-01', '2026-12-31', vm=vm); print(f"   {'VM '+str(vm):11s} full net {x['net']:10,.0f} t {x['t']:6.2f}   (reported, not selected)")
        y = daily.groupby(daily.index.year).sum().round(0).astype(int).to_dict(); print("   by year:", y)
        if ok: passers.append(sym)
    if os.path.exists('nt_analyzer_bars.csv'):
        mnq = run_market('MNQ', 'nt_analyzer_bars.csv', '2019-01-01', '2026-12-31')[1]
        print("\nCorrelation of daily strategy P&L with MNQ's (overlapping days):")
        for sym, dly in dailies.items():
            j = pd.concat([dly, mnq], axis=1, join='inner').dropna(); print(f"   {sym:6s} {j.iloc[:, 0].corr(j.iloc[:, 1]):+.3f}  ({len(j)} days)")
        if passers:
            print("\nPortfolio check (MNQ + passers, each scaled to MNQ's daily P&L volatility):")
            base = mnq.copy(); port = base.copy(); sd0 = base.std()
            for sym in passers:
                x = dailies[sym].reindex(base.index).fillna(0); port = port + x * (sd0 / x.std() if x.std() > 0 else 0)
            for lab, s_ in (('MNQ alone', base), ('portfolio', port)):
                eq = np.cumsum(s_.values); dd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
                print(f"   {lab:10s} t {tstat(s_):5.2f}  net ${s_.sum():9,.0f}  maxDD ${dd:8,.0f}  net/maxDD {s_.sum()/dd:5.2f}")
    print("\n=== B. LONG HISTORY 2010-2019 ===")
    for (sym, csv) in results:
        if not (sym in ('NQ', 'ES') and '2010' in csv): continue
        r, daily, mk = run_market(sym, csv, '2010-01-01', '2019-05-05')
        ok = r['net'] > 0 and r['t'] > 2.0
        print(f"{sym}: {mk.dates[WARMUP].date()}..{mk.dates[-1].date()} net ${r['net']:,.0f} t {r['t']:.2f} trades {r['trades']} maxDD ${r['mdd']:,.0f} -> {'PASS' if ok else 'fail'}")
        print("   by year:", daily.groupby(daily.index.year).sum().round(0).astype(int).to_dict())
