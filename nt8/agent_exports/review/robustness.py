"""Robustness suite on the NinjaTrader Analyzer trade log (per contract, 1 tick slippage, $1 commission added where the log has none).
   python3 robustness.py <NoiseArea_trades_*.csv> [bars csv for the slippage sweep]"""
import sys, numpy as np, pandas as pd
rng = np.random.default_rng(11)
tr = pd.read_csv(sys.argv[1])
# 2026-10-20 (double verification): NinjaTrader's profit_currency is already net of its own commission (profit_points carries it
# too), so the $1 round-trip commission is added only where the log has none (Analyzer with commission off, Sim101); it used to be
# taken again from every trade, which turned the 2026-10-19 Analyzer run ($1.30) into $2.30 a round trip. Rows with the same entry
# time and direction are one position (a 2-lot filled 1+1 is two NinjaTrader trades): results are per position, per contract.
q = tr.qty.astype(float).clip(lower=1) if 'qty' in tr else pd.Series(1.0, index=tr.index)
comm0 = tr.commission.astype(float) == 0 if 'commission' in tr else pd.Series(True, index=tr.index)
tr = tr.assign(net=tr.profit_currency - np.where(comm0, 1.0, 0.0) * q, q=q)
tr = tr.groupby(['entry_time_et', 'direction'], as_index=False).agg(exit_time_et=('exit_time_et', 'max'), net=('net', 'sum'), q=('q', 'sum'))
tr['net'] = tr.net / tr.q; tr['xt'] = pd.to_datetime(tr.exit_time_et)
p = tr.net.values; N = len(p); base = p.sum()
day = tr.groupby(tr.xt.dt.date).net.sum(); years = pd.to_datetime(day.index).year
def mdd(x):
    eq = np.cumsum(x); return (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
def pf(x): g = x[x > 0].sum(); l = -x[x < 0].sum(); return g / l if l > 0 else np.inf
def tstat(x): return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
print(f"BASE  trades {N}  net ${base:,.0f}  PF {pf(p):.2f}  maxDD ${mdd(day.values):,.0f}  t(daily) {tstat(day.values):+.2f}  win rate {np.mean(p>0):.1%}  avg win ${p[p>0].mean():.0f}  avg loss ${p[p<0].mean():.0f}")

print("\n1) SLIPPAGE DRAG (ticks per side; every trade has 2 sides; $0.50 per tick per side):")
for k in range(0, 9):
    q = p - (k - 1) * 0.5 * 2            # base already carries 1 tick/side
    d = pd.Series(q, index=tr.xt.dt.date).groupby(level=0).sum()
    yr = d.groupby(pd.to_datetime(d.index).year).sum()
    print(f"   {k} ticks: net ${q.sum():8,.0f}  PF {pf(q):.2f}  maxDD ${mdd(d.values):6,.0f}  t {tstat(d.values):+.2f}  losing years {int((yr<0).sum())}/{len(yr)}  per-trade ${q.mean():+.1f}")

print("\n2) TOP-TRADE DEPENDENCE:")
s = np.sort(p)[::-1]
for frac in (0.05, 0.10, 0.15, 0.20):
    k = int(round(frac * N)); rest = s[k:]
    print(f"   remove best {frac:.0%} of trades ({k}): net ${rest.sum():8,.0f} ({rest.sum()/base:+.0%} of base)  PF {pf(rest):.2f}  |  those {k} trades made ${s[:k].sum():,.0f} = {s[:k].sum()/p[p>0].sum():.0%} of gross profit")
for frac in (0.05, 0.10, 0.15):
    k = int(round(frac * N)); both = s[k:N-k]
    print(f"   remove best AND worst {frac:.0%}: net ${both.sum():8,.0f}  PF {pf(both):.2f}")
ds = np.sort(day.values)[::-1]
for n in (5, 10, 20):
    print(f"   remove best {n} days of {len(day)}: net ${ds[n:].sum():8,.0f} ({ds[n:].sum()/base:+.0%})  |  best {n} days = {ds[:n].sum()/base:.0%} of net")
print(f"   median trade ${np.median(p):+.1f}; trades > $500: {int((p>500).sum())}; trades < -$300: {int((p<-300).sum())}; largest win ${p.max():,.0f}, largest loss ${p.min():,.0f}")

print("\n3) MONTE CARLO - 10,000 random re-orderings of the same trades (what the equity curve could have looked like):")
M = 10000; dd = np.empty(M); dd_yr = np.empty(M)
WIN = 200 if N > 200 else max(1, N // 2)       # 2026-10-17: rng.integers(0, N-200) raised ValueError below 201 trades (a live year)
for i in range(M):
    q = rng.permutation(p); dd[i] = mdd(q)
    j = rng.integers(0, max(N - WIN, 1)); dd_yr[i] = mdd(q[j:j+WIN])      # ~1 year of trades
print(f"   full-period maxDD: actual ${mdd(p):,.0f} | shuffled median ${np.median(dd):,.0f}  95th ${np.percentile(dd,95):,.0f}  99th ${np.percentile(dd,99):,.0f}  P(DD > $6,000) {np.mean(dd>6000):.1%}")
print(f"   any {WIN}-trade " + ("(~1 yr) window" if WIN == 200 else "window (fewer than 201 trades in the file)") + f": median ${np.median(dd_yr):,.0f}  95th ${np.percentile(dd_yr,95):,.0f}  99th ${np.percentile(dd_yr,99):,.0f}")
B = 10000; boot = np.array([rng.choice(p, N, replace=True).sum() for _ in range(B)])
print(f"   bootstrap (resample trades with replacement) full-period net: 5th ${np.percentile(boot,5):,.0f}  median ${np.median(boot):,.0f}  P(net <= 0) {np.mean(boot<=0):.2%}")
bd = np.array([rng.choice(day.values, 252, replace=True).sum() for _ in range(B)])
print(f"   bootstrap 1-year net (252 sessions): 5th ${np.percentile(bd,5):,.0f}  median ${np.median(bd):,.0f}  P(losing year) {np.mean(bd<0):.0%}")

print("\n4) STABILITY:")
yr = day.groupby(years).sum(); print("   by year:", {int(k): int(v) for k, v in yr.items()})
h = tr.xt.dt.hour; ent = pd.to_datetime(tr.entry_time_et).dt.hour
print("   by entry hour $:", {int(k): int(v) for k, v in tr.groupby(ent).net.sum().items()})
print("   long $%d (%d trades)  short $%d (%d trades)" % (tr[tr.direction=='long'].net.sum(), (tr.direction=='long').sum(), tr[tr.direction=='short'].net.sum(), (tr.direction=='short').sum()))
dow = tr.xt.dt.dayofweek; print("   by weekday $:", {['Mon','Tue','Wed','Thu','Fri'][k]: int(v) for k, v in tr.groupby(dow).net.sum().items()})
half = np.array_split(p, 4); print("   quarters of the sample $:", [int(x.sum()) for x in half])
