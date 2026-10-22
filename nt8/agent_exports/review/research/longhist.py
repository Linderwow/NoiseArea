"""16-year expectations per LONGHIST_PLAN.txt: NQ 2010-2019 (Databento) joined to MNQ 2019-2026 (NinjaTrader bars), in return-on-notional units."""
import numpy as np, pandas as pd
from research_engine import Market, fam_A, WARMUP

rng = np.random.default_rng(20261016)


def tstat(x): x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def segment(csv, pv, tick, comm, name):
    mk = Market(csv, pv, name)
    pg, nt = fam_A(mk, vm=1.0, slip=0.0, comm=0.0)          # gross
    pn, nt2 = fam_A(mk, vm=1.0, slip=tick, comm=comm)       # actual costs
    assert (nt == nt2).all()
    P = np.array([x.O[0] for x in mk.days])
    df = pd.DataFrame({'gross': np.nan_to_num(pg), 'net': np.nan_to_num(pn), 'nt': nt, 'P': P, 'pv': pv}, index=mk.dates)
    df = df.iloc[WARMUP:]
    raw = pd.read_csv('raw_open930.csv', index_col=0, parse_dates=True).open
    hit = df.index.isin(raw.index); df.loc[hit, 'P'] = raw.reindex(df.index[hit]).values
    print(f"   raw 09:30 prices substituted on {hit.sum()} of {len(df)} days (continuous files are difference-adjusted; the strategy's signals are shift-invariant, the notional is not)")
    chk = (df.gross - df.net).sum() - df.nt.sum() * 2 * (tick * pv + comm)
    print(f"{name}: {len(df)} sessions {df.index[0].date()}..{df.index[-1].date()}, cost linearity check {chk:+.4f} $")
    return df


nq = segment('nq_2010-2019_5m.csv', 20.0, 0.25, 2.00, 'NQ')
mnq = segment('nt_analyzer_bars.csv', 2.0, 0.25, 0.50, 'MNQ')
mnq = mnq[mnq.index >= '2019-05-06']
nq = nq[nq.index <= '2019-05-05']
P_ref = mnq[mnq.index.year == 2026].P.mean()
dollar = 2.0 * P_ref                                       # today's dollars per unit return, per micro
c = 0.5 / P_ref                                            # today's cost per side as a fraction of price
print(f"P_ref (mean 2026 MNQ 09:30 open) = {P_ref:,.0f}; notional per micro ${dollar:,.0f}; today's cost per side {c*1e4:.2f} bp of price")

for df in (nq, mnq):
    df['r_gross'] = df.gross / (df.pv * df.P)
    df['r_net'] = df.net / (df.pv * df.P)
    df['r_today'] = df.r_gross - df.nt * 2 * c            # counterfactual: today's relative cost all along
all_ = pd.concat([nq, mnq]); all_['seg'] = np.where(all_.index <= '2019-05-05', 'NQ', 'MNQ')
print(f"combined: {len(all_)} sessions {all_.index[0].date()}..{all_.index[-1].date()}, {int(all_.nt.sum())} trades")
# sanity: MNQ segment actual == today's cost (same contract) -> r_net == r_today up to rounding
d = (mnq.r_net - mnq.r_today).abs().max() * dollar
print(f"MNQ segment: actual-cost vs today's-cost series differ by at most ${d:.2f}/day (should be ~0 except price-level drift inside 2026)")


def report(col, label):
    s = all_[col]
    print(f"\n=== {label} ===")
    for nm, x in (('16 years 2010-2026', s), ('7 years 2019-2026', s[s.index >= '2019-05-06']), ('9 years 2010-2019', s[s.index <= '2019-05-05'])):
        eq = np.cumsum(x.values); peak = np.maximum.accumulate(np.r_[0, eq]); dd = peak - np.r_[0, eq]
        i = int(dd.argmax()); j = int(np.argmax(np.r_[0, eq][:i + 1])) if i > 0 else 0
        under = (dd > 0).astype(int); runs = []; k = 0
        for u in under:
            k = k + 1 if u else 0; runs.append(k)
        yr = x.groupby(x.index.year).sum()
        print(f"{nm:20s} t {tstat(x):5.2f}  Sharpe {x.mean()/x.std()*np.sqrt(252):5.2f}  net {x.sum()*100:6.1f}% of notional = ${x.sum()*dollar:8,.0f} today  "
              f"per year ${x.sum()*dollar/ (len(x)/252):6,.0f}  maxDD {dd.max()*100:5.1f}% = ${dd.max()*dollar:6,.0f} ({x.index[max(j-1,0)].date()} -> {x.index[i-1].date()}, {i-j} sessions)  "
              f"longest under water {max(runs)} sessions  losing years {(yr<0).sum()} of {len(yr)}")
    yr = s.groupby(s.index.year).sum()
    print("   by year (% of notional | today's $):", {int(y): f"{v*100:+.1f}% | {v*dollar:+,.0f}" for y, v in yr.items()})
    top = np.sort(s.values)[::-1]; n1 = max(1, int(round(len(s) * 0.01)))
    print(f"   best 1% of days ({n1}) = {top[:n1].sum()/s.sum()*100:.0f}% of the 16-year net; median day {np.median(s.values)*dollar:+.1f} $; days>0 {(s>0).mean()*100:.0f}%")
    # bootstrap years
    v = s.values; yrs = rng.choice(v, size=(10000, 250), replace=True); ysum = yrs.sum(1)
    eqs = np.cumsum(yrs, axis=1); peaks = np.maximum.accumulate(np.c_[np.zeros(10000), eqs], axis=1); dds = (peaks - np.c_[np.zeros(10000), eqs]).max(1)
    print(f"   bootstrap 10,000 years: P(losing year) {np.mean(ysum<0)*100:.0f}%  year median ${np.median(ysum)*dollar:,.0f}  5th ${np.percentile(ysum,5)*dollar:,.0f}  95th ${np.percentile(ysum,95)*dollar:,.0f}  "
          f"within-year maxDD median ${np.median(dds)*dollar:,.0f}  95th ${np.percentile(dds,95)*dollar:,.0f}  99th ${np.percentile(dds,99)*dollar:,.0f}")
    # 2010-2017 only (the calm stretch)
    q = s[(s.index.year >= 2010) & (s.index.year <= 2017)]
    print(f"   2010-2017 only: t {tstat(q):.2f}, ${q.sum()*dollar:,.0f} today over {len(q)/252:.1f} years = ${q.sum()*dollar/(len(q)/252):,.0f}/yr")


report('r_net', '(a) ACTUAL historical costs (1 tick + commission of the time)')
report('r_today', "(b) COUNTERFACTUAL: today's relative cost applied to all 16 years")
report('r_gross', '(c) GROSS, no costs (for reference)')
all_.to_csv('longhist_daily.csv')
