r"""A second edge on another Bulenox market beside NoiseArea (XMKT_PLAN.txt, pre-registered 2026-10-20).
    python xmkt.py      -> xmkt_output.txt, xmkt_stage1.csv, xmkt_<market>_<rule>_daily.csv for edges
Ten intraday rules (regime catalog family defaults A-I + LH last-half-hour momentum) on seven Bulenox micros, Bulenox costs,
discovery to 2022 -> holdout 2023-2026 for the candidates, judged as an addition to NoiseArea.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from scipy.stats import norm

SPEC = {'MES': (5.0, 0.25, 0.61), 'MYM': (0.5, 1.0, 0.61), 'M2K': (5.0, 0.10, 0.61), 'MGC': (10.0, 0.10, 0.76), 'M6E': (12500.0, 0.0001, 0.50),
        'MCL': (100.0, 0.01, 0.76), 'MBT': (0.10, 5.0, 2.76)}
BARS = os.path.join(HERE, 'regime_work', 'bars5m'); DISC_END, HOLD_START, END = '2022-12-31', '2023-01-01', '2026-10-16'
FAMS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I']
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def tstat(x):
    x = np.asarray(x, float); sd = x.std(ddof=1) if len(x) > 2 else 0.0; return x.mean() / (sd / np.sqrt(len(x))) if sd > 0 else 0.0


def mdd(x):
    eq = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max()) if len(x) else 0.0


def lh(mk):
    """Last-half-hour momentum: sign(prior 16:00 close -> 10:00 close) held from the 15:30 open to the 16:00 close (bars stamped at
    their end: the 10:00 close is the bar with M 600, the 15:30 open is the open of the bar with M 935)."""
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        if np.isnan(mk.pc[k]) or x.M[-1] != 960: continue
        i10 = np.where(x.M == 600)[0]; i35 = np.where(x.M == 935)[0]
        if not len(i10) or not len(i35): pnl[k] = 0.0; continue
        s = np.sign(x.C[i10[0]] - mk.pc[k])
        pnl[k] = s * (x.C[-1] - x.O[i35[0]]) if s != 0 else 0.0; nt[k] = 1 if s != 0 else 0
    return pnl, nt


def rule_series(mk, rule, spec, ticks=1):
    """Daily net return (on the 09:30 price) at today's relative cost, trades, $ per micro factor; warm-up sessions dropped."""
    import regime_test as rt
    from research_engine import WARMUP
    pv, tick, comm = spec
    if rule == 'LH': p, n = lh(mk)
    else: p, n = rt.run_variant(mk, rt.DEF_IDX[FAMS.index(rule)])
    o = np.array([x.O[0] for x in mk.days]); p_today = mk.days[-1].C[-1]
    cost = (ticks * tick + comm / pv) / p_today                                    # per side, as a return at today's price
    r = np.nan_to_num(p) / o - np.asarray(n) * 2 * cost
    s = pd.Series(r, index=mk.dates).iloc[WARMUP:]; nn = pd.Series(np.asarray(n), index=mk.dates).iloc[WARMUP:]
    return s, nn, p_today * pv


def main():
    import regime_test as rt, src2_test as T
    from research_engine import Market
    import src2_lib as L
    na = T.noisearea() * L.N_USD                                                   # NoiseArea, $ per MNQ micro at today's price
    rows, keep = [], {}
    pr(f"XMKT (XMKT_PLAN.txt). Rules {['LH'] + FAMS} on {list(SPEC)}; costs 1 tick + Bulenox commission per side; k = {10 * len(SPEC)}")
    mk_cache = {}
    for nm, spec in SPEC.items():
        mk = Market(os.path.join(BARS, f'{nm.lower()}_5m.csv'), 1.0, nm); mk_cache[nm] = mk
        pr(f"   {nm}: {mk.n} sessions {mk.dates[0].date()} .. {mk.dates[-1].date()}, last price {mk.days[-1].C[-1]:,.4g}, "
           f"$ per micro = return x {mk.days[-1].C[-1] * spec[0]:,.0f}")
        for rule in ['LH'] + FAMS:
            s, n, f = rule_series(mk, rule, spec); usd = s * f
            d = s[s.index <= DISC_END]; h = s[(s.index >= HOLD_START) & (s.index <= END)]
            c = np.corrcoef(usd.reindex(na.index).dropna(), na.reindex(usd.dropna().index).reindex(usd.reindex(na.index).dropna().index))[0, 1] \
                if len(usd) > 10 else np.nan
            rows.append(dict(market=nm, rule=rule, n_disc=len(d), t_disc=tstat(d.values), usd_yr_disc=d.mean() * 252 * f,
                             trades_yr=n.mean() * 252, corr_na=c, cand=tstat(d.values) >= 2.0))
            keep[(nm, rule)] = (s, n, f)
    st1 = pd.DataFrame(rows); st1.to_csv(os.path.join(HERE, 'xmkt_stage1.csv'), index=False)
    cands = st1[st1.cand]; m = len(cands)
    pr(f"\nSTAGE 1 - discovery (to 2022-12-31): {m} candidates of {len(st1)} with net t >= 2.0")
    for r in st1.sort_values('t_disc', ascending=False).itertuples():
        pr(f"   {r.market:4s} {r.rule:3s} disc t {r.t_disc:+5.2f}  ${r.usd_yr_disc:+8,.0f}/yr  {r.trades_yr:5.0f} trades/yr  corr NoiseArea {r.corr_na:+.2f}"
           + ("  <- candidate" if r.cand else ''))
    if m == 0:
        pr("\nNo candidate - stage 2 not run (plan)."); finish(); return
    z = norm.ppf(1 - 0.05 / m)
    pr(f"\nSTAGE 2 - holdout 2023-01-01 .. 2026-10-16, m = {m}: H1 bar t >= {z:.2f}, H3 bar P < {0.05 / m:.4f}")
    for r in cands.itertuples():
        s, n, f = keep[(r.market, r.rule)]
        h = s[(s.index >= HOLD_START) & (s.index <= END)]
        s2, _, _ = rule_series(mk_cache[r.market], r.rule, SPEC[r.market], ticks=2); h2 = s2[(s2.index >= HOLD_START) & (s2.index <= END)]
        th = tstat(h.values); g1 = th >= z; g2 = h2.mean() > 0 and tstat(h2.values) >= 1.5
        common = na.index[(na.index >= HOLD_START) & (na.index <= END)]
        a = na.reindex(common).values; b = (h * f).reindex(common).fillna(0.0).values
        p_add = rt.boot_sharpe_diff(common, a + b, a); dd_na, dd_c = mdd(a), mdd(a + b)
        g3 = p_add < 0.05 / m and dd_c <= 1.10 * dd_na
        yr = s.groupby(s.index.year).sum(); full = yr[(yr.index > s.index[0].year) & (yr.index < 2026)]
        g4 = (full > 0).mean() >= 0.6 if len(full) else False
        ok = g1 and g2 and g3 and g4
        pr(f"   {r.market} {r.rule}: holdout t {th:+.2f} (${h.mean() * 252 * f:+,.0f}/yr), 2 ticks t {tstat(h2.values):+.2f}; NoiseArea + it Sharpe "
           f"{rt.sharpe(a + b):.2f} vs {rt.sharpe(a):.2f}, P {p_add:.4f}, maxDD ${dd_c:,.0f} vs ${dd_na:,.0f}; positive years "
           f"{int((full > 0).sum())}/{len(full)} | H1 {'PASS' if g1 else 'FAIL'} H2 {'PASS' if g2 else 'FAIL'} H3 {'PASS' if g3 else 'FAIL'} "
           f"H4 {'PASS' if g4 else 'FAIL'} -> {'EDGE' if ok else 'not adopted'}")
        pr("      per year $: " + ' | '.join(f"{y} {v * f:+,.0f}" for y, v in yr.items()))
        if ok: (s * f).rename('usd').to_csv(os.path.join(HERE, f'xmkt_{r.market.lower()}_{r.rule.lower()}_daily.csv'))
    finish()


def finish():
    with open(os.path.join(HERE, 'xmkt_output.txt'), 'w', encoding='utf-8') as fo: fo.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
