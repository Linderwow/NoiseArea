"""Dealer gamma exposure as a condition, per GEX_PLAN.txt.  python3 gex_test.py   (needs gex_dix.csv: date,price,dix,gex)"""
import numpy as np, pandas as pd
from research_engine import Market, fam_A, stats, WARMUP, TICK

g = pd.read_csv('gex_dix.csv', parse_dates=['date']).set_index('date').sort_index()
gex = g.gex


def tstat(x): x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def S(mk, pnl, nt, a, b): return stats(mk, pnl, nt, a, b)


for lab, csv in (('NT', 'nt_analyzer_bars.csv'), ('DB', 'mnq_db_5m.csv')):
    mk = Market(csv, 2.0, 'MNQ'); end = str(mk.dates[-1].date()); D = ('2019-01-01', '2022-12-31'); V = ('2023-01-01', end)
    pnl, nt = fam_A(mk, vm=1.0, slip=TICK, comm=0.5); p = np.nan_to_num(pnl)
    dates = mk.dates
    # prior-session GEX: the last GEX value strictly before each session date
    prior = pd.Series(gex.reindex(gex.index.union(dates)).ffill().shift(1).reindex(dates).values, index=dates)
    # shift(1) above shifts within the union index; make it strictly "last value before this date":
    prior = pd.Series([gex[gex.index < d].iloc[-1] if (gex.index < d).any() else np.nan for d in dates], index=dates)
    idx = np.arange(mk.n)
    def mask(a, b): return (idx >= WARMUP) & (dates >= pd.Timestamp(a)) & (dates <= pd.Timestamp(b)) & ~np.isnan(prior.values)
    mD, mV = mask(*D), mask(*V)
    q1, q2 = np.nanpercentile(prior.values[mD], [33.3, 66.7]); med = np.nanmedian(prior.values[mD])
    print(f"\n######## {lab}: sessions {mk.n}, with prior-day GEX {int((~np.isnan(prior.values)).sum())}; discovery terciles at {q1/1e9:.2f}bn / {q2/1e9:.2f}bn, median {med/1e9:.2f}bn")
    print(" 1. MECHANISM CHECK - baseline $/day by prior-day GEX bucket (bucket edges fixed on discovery):")
    for per, m in (('discovery', mD), ('validation', mV)):
        v = prior.values
        lo, mid, hi = m & (v <= q1), m & (v > q1) & (v <= q2), m & (v > q2); neg, pos = m & (v < 0), m & (v >= 0)
        print(f"   {per:10s} low ${p[lo].mean():6.1f}/day (n {lo.sum():3d}, t {tstat(p[lo]):4.2f}) | mid ${p[mid].mean():6.1f} (n {mid.sum():3d}) | high ${p[hi].mean():6.1f}/day (n {hi.sum():3d}, t {tstat(p[hi]):4.2f}) || GEX<0 ${p[neg].mean():6.1f}/day (n {neg.sum():3d}) vs >=0 ${p[pos].mean():6.1f} (n {pos.sum():3d})")
    base_d, base_v = S(mk, pnl, nt, *D), S(mk, pnl, nt, *V)
    print(f" 2. FILTERS (baseline: disc ${base_d['net']:,.0f} t {base_d['t']:.2f} | val ${base_v['net']:,.0f} t {base_v['t']:.2f} maxDD ${base_v['mdd']:,.0f})")
    for name, keep in (('(a) prior GEX below discovery median', prior.values < med), ('(b) prior GEX < 0', prior.values < 0), ('(c) drop top third (prior GEX <= 67th pct)', prior.values <= q2)):
        pf = np.where(keep, pnl, 0.0); nf = np.where(keep, nt, 0)
        sd, sv = S(mk, pf, nf, *D), S(mk, pf, nf, *V)
        ok = (sv['t'] - base_v['t'] >= 0.3) and (sv['net'] >= 0.8 * base_v['net'])
        print(f"   {name:44s} disc ${sd['net']:8,.0f} t {sd['t']:5.2f} | val ${sv['net']:8,.0f} t {sv['t']:5.2f} maxDD ${sv['mdd']:6,.0f} days kept {int(keep[mV].sum())}/{int(mV.sum())}  -> {'passes (ii)+(iii)' if ok else 'fails'}")
    print(" 3. SIZING (reported only): 2 micros on low-tercile days, 1 otherwise, vs flat 4/3 micros")
    w = np.where(prior.values <= q1, 2.0, 1.0); flat = 4.0 / 3.0
    for per, (a, b) in (('discovery', D), ('validation', V)):
        s2, s1 = S(mk, pnl * w, nt, a, b), S(mk, pnl * flat, nt, a, b)
        print(f"   {per:10s} gamma-sized net ${s2['net']:8,.0f} t {s2['t']:5.2f} maxDD ${s2['mdd']:6,.0f}  |  flat net ${s1['net']:8,.0f} t {s1['t']:5.2f} maxDD ${s1['mdd']:6,.0f}")
    # correlation of GEX with the day's realised range (sanity: is GEX informative at all here?)
    rng = np.array([(x.H.max() - x.L.min()) / x.O[0] for x in mk.days]); m = mD | mV
    print(f"   sanity: corr(prior GEX, day range%) = {np.corrcoef(prior.values[m], rng[m])[0,1]:+.3f}; corr(prior GEX, |daily P&L|) = {np.corrcoef(prior.values[m], np.abs(p[m]))[0,1]:+.3f}")
