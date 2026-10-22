r"""Projections for the live set-up (2026-10-20): NoiseArea 1 micro, alone and with the FOMC-eve add-on, per micro at today's cost.
    python projections.py      -> projections_output.txt
Method as LONGHIST (whose figures it must reproduce first): the 16-year daily series of scaling_inputs\scaling_daily.csv (r_today =
return on notional at today's relative cost), 12-month month-block bootstrap. The FOMC-eve add-on is the plain 16:00 close -> 09:30 open
long on each scheduled FOMC eve, from the 1-minute caches (must reproduce SOURCES2: 129 nights, t 3.78). Scenarios are what-ifs, not tests.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L
import bulenox_routes as B

SEED, N = 20261020, 20000
out = []
def pr(*a): print(*a); out.append(' '.join(str(x) for x in a))


def fomc_eve(P):
    """Plain FOMC-eve long: session k-1's 16:00 close -> session k's 09:30 open, net of 2 sides at 1 tick + $0.50."""
    D = P.daily; rows = {}
    for d in B.FOMC:
        k = P.k_of.get(d)
        if k is None or k == 0 or D.seg.iat[k - 1] != D.seg.iat[k]: continue
        rows[d] = (D.aO.iat[k] - D.aC.iat[k - 1]) / D.px.iat[k] - 2 * L.side_cost(1)
    return pd.Series(rows)


def months(idx):
    key = idx.year * 12 + idx.month; return [np.where(key == k)[0] for k in np.unique(key)]


def boot(x, mb, n_months, n=N, seed=SEED, daily=True):
    """Totals (and max drawdowns if daily) of n paths of n_months months drawn with replacement."""
    rng = np.random.default_rng(seed); msum = np.array([x[b].sum() for b in mb]); pick = rng.integers(0, len(mb), (n, n_months))
    tot = msum[pick].sum(1)
    if not daily: return tot, None
    dd = np.empty(n)
    for i in range(n):
        eq = np.cumsum(np.concatenate([x[mb[j]] for j in pick[i]])); dd[i] = (np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max()
    return tot, dd


def report(name, usd, idx, nt=None):
    mb = months(idx); yrs = len(usd) / 252
    eq = np.cumsum(usd); hist_dd = (np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max()
    y1, d1 = boot(usd, mb, 12); y3, _ = boot(usd, mb, 36, daily=False); y5, d5 = boot(usd, mb, 60, n=5000)
    q = lambda a, p: np.percentile(a, p)
    rng = np.random.default_rng(SEED); iid = np.array([usd[rng.integers(0, len(usd), 250)].sum() for _ in range(10000)])
    pr(f"\n{name}")
    pr(f"   expectation ${usd.sum() / yrs:,.0f}/yr; daily t {B.tstat(usd):.2f}; historical worst drawdown ${hist_dd:,.0f}")
    pr(f"   one year, LONGHIST's method (250 days drawn one by one): 5% ${q(iid, 5):,.0f} | median ${q(iid, 50):,.0f} | 95% ${q(iid, 95):,.0f} | "
       f"losing year {np.mean(iid < 0) * 100:.0f}%")
    pr(f"   one year:  5% ${q(y1, 5):,.0f} | 25% ${q(y1, 25):,.0f} | median ${q(y1, 50):,.0f} | 75% ${q(y1, 75):,.0f} | 95% ${q(y1, 95):,.0f} | "
       f"losing year {np.mean(y1 < 0) * 100:.0f}% (1 in {1 / max(np.mean(y1 < 0), 1e-9):.1f})")
    pr(f"   drawdown inside a year: median ${q(d1, 50):,.0f} | 1-in-20 ${q(d1, 95):,.0f}; inside 5 years: median ${q(d5, 50):,.0f} | 1-in-20 ${q(d5, 95):,.0f}")
    pr(f"   3 years: median ${q(y3, 50):,.0f} (5% ${q(y3, 5):,.0f}), behind after 3 years {np.mean(y3 < 0) * 100:.1f}% | "
       f"5 years: median ${q(y5, 50):,.0f} (5% ${q(y5, 5):,.0f}), behind after 5 years {np.mean(y5 < 0) * 100:.1f}%")
    return y1


def main():
    d = pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0, parse_dates=True)
    na = d.r_today.values * L.N_USD; nt = d.nt.values.astype(float); idx = d.index
    pr(f"PROJECTIONS per micro at today's cost (1 tick + $0.50 a side) in dollars of P_REF {L.P_REF:,.0f} (notional ${L.N_USD:,.0f}); "
       f"MNQ closed at {d.P.iloc[-1]:,.0f} on {idx[-1].date()}, so at today's price every dollar figure is x{d.P.iloc[-1] / L.P_REF:.2f}. "
       f"{len(d)} sessions {idx[0].date()} .. {idx[-1].date()}; month-block bootstrap, seed {SEED}, {N:,} paths (5-year drawdowns 5,000).")
    # reproduction checks
    yr = pd.Series(na, idx).groupby(idx.year).sum()
    pr(f"check LONGHIST: 16-year ${na.sum() / (len(na) / 252):,.0f}/yr (doc: ~$4,700), losing calendar years {list(yr[yr < 0].index)}")
    P = B.Pan('nq', 'mnqx'); fe = fomc_eve(P)
    pr(f"check SOURCES2 FOMC eve: {len(fe)} nights, mean {fe.mean() * 1e4 + 2 * L.side_cost(1) * 1e4:+.1f} bp gross, event t {B.tstat(fe):.2f} "
       f"(SOURCES2: 129 nights, +23.5 bp, t 3.78 on the daily series)")
    fe_usd = fe.reindex(idx).fillna(0.0).values * L.N_USD
    pr(f"   FOMC eve inside the scaling window: {int((fe_usd != 0).sum())} nights, ${fe_usd.sum() / (len(na) / 252):,.0f}/yr per micro")
    report("A. NoiseArea alone - the version that trades on Sim101 and would trade on Bulenox", na, idx)
    report("B. A with live fills 1.5 ticks/side worse than the backtest (LATENCY_NOTE: +1.4 to +2.3 at 70-90 ms)", na - 1.5 * nt, idx)
    report("C. A with its edge halved after publication (a what-if: the daily mean cut by 50%, volatility unchanged)", na - 0.5 * na.mean(), idx)
    m = idx > pd.Timestamp('2019-05-05')
    report("D. A on the MNQ era only (2019-05 .. 2026-10, the regime the live strategy has traded)", na[m], idx[m])
    report("E. A + FOMC-eve add-on - only on an account that allows overnight holds (NOT Bulenox; parked since 2026-10-20)", na + fe_usd, idx)
    with open(os.path.join(HERE, 'projections_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
