r"""Today's whipsaw ideas + the near-miss combination (DAYCAP_PLAN.txt, pre-registered 2026-10-21 15:23 ET).
    python daycap.py      -> daycap_output.txt, daycap_series.csv
Same engine, data, costs, gates and seed as navar.py (its functions are reused unchanged); only the variants and the D1 bars differ.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import navar as NV

USD = NV.USD
VARIANTS = {'C1 one position a day': (dict(cap=1), 0.05 / 5), 'C2 at most two positions a day': (dict(cap=2), 0.05 / 5),
            'C3 stop after two losing trades': (dict(stop_losses=2), 0.05 / 5),
            'C4 no re-entry in the direction that lost': (dict(no_relose=True), 0.05 / 5),
            'K1 exit gate + lunch pause + EMA': (dict(gate=4, lunch=True, ema=True), 0.05 / 14)}
LIVE_BARS = os.path.join(HERE, '..', '..', 'NoiseArea_bars_MNQ_strategy_Sim101.csv')
LIVE_FROM = pd.Timestamp('2026-10-15')
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def live_market():
    """The bars the live strategy recorded, in the research engine's format (time = bar close, ET)."""
    from research_engine import Market
    os.makedirs(os.path.join(HERE, 'fwd'), exist_ok=True); p = os.path.join(HERE, 'fwd', 'live_bars_research_format.csv')
    b = pd.read_csv(LIVE_BARS).drop_duplicates('time_et', keep='last').rename(columns={'time_et': 'time'}); b.to_csv(p, index=False)
    return Market(p, 1.0, 'LIVE')


def live_days(mk, **kw):
    """$ per micro (1 tick + $0.50 a side) and trades for each live session since LIVE_FROM."""
    pts, n = N.fam_V(mk, **kw); sel = mk.dates >= LIVE_FROM
    return pd.DataFrame({'usd': np.nan_to_num(pts[sel]) * 2.0 - n[sel] * 2 * 1.0, 'trades': n[sel]}, index=mk.dates[sel])


def main():
    r0, n0 = N.series('MNQ', fn=N.fam_V); d0, _ = N.series('MNQDB', fn=N.fam_V); r0_2, _ = N.series('MNQ', fn=N.fam_V, ticks=2)
    d0_2, _ = N.series('MNQDB', fn=N.fam_V, ticks=2)
    fresh0 = {nm: NV.sharpe(N.single(nm, fn=N.fam_V, a=a, b=b).g) for nm, a, b in NV.FRESH}
    b0 = NV.bulenox(r0, n0)
    yr = lambda r: ' | '.join(f"{y} {v * USD:+,.0f}" for y, v in r.groupby(r.index.year).sum().items())
    pr(f"DAYCAP (DAYCAP_PLAN.txt). Base = live NoiseArea via nav_lab.fam_V: {len(r0)} sessions {r0.index[0].date()} .. {r0.index[-1].date()}, "
       f"${r0.mean() * 252 * USD:,.0f}/yr, Sharpe {NV.sharpe(r0):.2f}, maxDD ${NV.mdd(r0.values * USD):,.0f}, MAR {NV.mar(r0):.3f}, "
       f"{n0.sum() / len(r0) * 252:.0f} trades/yr")
    pr(f"   base Bulenox 50K (1 micro): 2-year payouts net of activation ${b0[0]:,.0f}, open after 1 year {b0[1] * 100:.0f}%")
    lm = live_market(); L0 = live_days(lm)
    pr(f"   live sessions recomputed from the strategy's own bars: " + ', '.join(f"{d.date()} ${v.usd:+,.0f} ({v.trades:g})" for d, v in L0.iterrows()))
    rows = []
    for name, (kw, bar) in VARIANTS.items():
        r, n = N.series('MNQ', fn=N.fam_V, **kw); d, _ = N.series('MNQDB', fn=N.fam_V, **kw)
        r2, _ = N.series('MNQ', fn=N.fam_V, ticks=2, **kw); dd2, _ = N.series('MNQDB', fn=N.fam_V, ticks=2, **kw)
        p1 = NV.boot_mar(r, r0); p2 = NV.boot_mar(r2, r0_2)
        g1 = p1 < bar and NV.sharpe(r) >= NV.sharpe(r0) - 0.05
        segs = {'NQ': (NV.seg_mar(r, 'NQ'), NV.seg_mar(r0, 'NQ')), 'MNQ NT': (NV.seg_mar(r, 'MNQ'), NV.seg_mar(r0, 'MNQ')),
                'MNQ DB': (NV.seg_mar(d, 'MNQ'), NV.seg_mar(d0, 'MNQ'))}
        g2 = all(v > b for v, b in segs.values())
        fr = {nm: NV.sharpe(N.single(nm, fn=N.fam_V, a=a, b=b, **kw).g) for nm, a, b in NV.FRESH}
        g3 = fr['ES'] > fresh0['ES'] and sum(fr[m] > fresh0[m] for m in ('MES', 'MYM', 'M2K')) >= 2
        segs2 = [NV.seg_mar(r2, 'NQ') > NV.seg_mar(r0_2, 'NQ'), NV.seg_mar(r2, 'MNQ') > NV.seg_mar(r0_2, 'MNQ'),
                 NV.seg_mar(dd2, 'MNQ') > NV.seg_mar(d0_2, 'MNQ')]
        g5 = p2 < bar and NV.sharpe(r2) >= NV.sharpe(r0_2) - 0.05 and all(segs2)
        bv = NV.bulenox(r, n); g6 = bv[0] >= b0[0] and bv[1] >= b0[1]
        G = {'D1': g1, 'D2': g2, 'D3': g3, 'D5': g5, 'D6': g6}; ok = all(G.values())
        ch = int(((r - r0).abs() > 1e-12).sum())
        pr(f"\n{name}: ${r.mean() * 252 * USD:,.0f}/yr, Sharpe {NV.sharpe(r):.2f} (base {NV.sharpe(r0):.2f}), maxDD ${NV.mdd(r.values * USD):,.0f} "
           f"(base ${NV.mdd(r0.values * USD):,.0f}), MAR {NV.mar(r):.3f} (base {NV.mar(r0):.3f}), {n.sum() / len(r) * 252:.0f} trades/yr, "
           f"{ch} of {len(r)} sessions changed")
        pr(f"   D1 bootstrap P(MAR <= base) {p1:.4f} (bar {bar:.4f}) | D2 MAR " + ', '.join(f"{k} {v:.3f} vs {b:.3f}" for k, (v, b) in segs.items()))
        pr(f"   D3 fresh gross Sharpe " + ', '.join(f"{k} {fr[k]:+.2f} vs {fresh0[k]:+.2f}" for k in fr) +
           f" | D5 2 ticks: P {p2:.4f}, Sharpe {NV.sharpe(r2):.2f} vs {NV.sharpe(r0_2):.2f}, segments {segs2}")
        pr(f"   D6 Bulenox 50K (1 micro): 2-year payouts ${bv[0]:,.0f} vs ${b0[0]:,.0f}, open after 1 year {bv[1] * 100:.0f}% vs {b0[1] * 100:.0f}%")
        pr(f"   per year: {yr(r)}")
        Lv = live_days(lm, **kw)
        pr(f"   live sessions: " + ', '.join(f"{d.date()} ${v.usd:+,.0f} ({v.trades:g})" for d, v in Lv.iterrows()) +
           f" | total ${Lv.usd.sum():+,.0f} vs base ${L0.usd.sum():+,.0f}")
        pr("   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()) + f"  -> {'RECOMMENDED' if ok else 'not adopted'}")
        rows.append(r.rename(name.split()[0]))
    pd.concat([r0.rename('base')] + rows, axis=1).to_csv(os.path.join(HERE, 'daycap_series.csv'))
    with open(os.path.join(HERE, 'daycap_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
