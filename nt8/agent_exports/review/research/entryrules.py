r"""ENTRYRULES (ENTRYRULES_PLAN.txt, pre-registered 2026-10-23 12:18 ET): the never-isolated rule parameters, one at a time.
    python entryrules.py      -> entryrules_output.txt, entryrules_series.csv
Same harness and gates as navar.py (D1 bootstrap, D2 three Nasdaq data sets, D3 fresh markets, D5 2 ticks, D6 Bulenox 50K Master),
K = 10, plus the strategy-validation checks Z (zero cost), R5 (5 best days dropped), Y (per year) and P (plateau).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import navar as NV

K = 10; P_BAR = 0.05 / K; USD = NV.USD
VARIANTS = {'F5 every 5 min': dict(step=5), 'F10 every 10 min': dict(step=10), 'F15 every 15 min': dict(step=15),
            'F60 every 60 min': dict(step=60), 'S0945 extra 09:45 decision': dict(checks_add=(585,)), 'S1030 first decision 10:30': dict(first=630),
            'B080 band VM 0.8': dict(vm=0.8), 'B120 band VM 1.2': dict(vm=1.2), 'XBAND exit on band only': dict(trail='band'),
            'XVWAP exit on VWAP only': dict(trail='vwap')}
PLATEAU = {'F5 every 5 min': ['F10 every 10 min'], 'F10 every 10 min': ['F5 every 5 min', 'F15 every 15 min'], 'F15 every 15 min': ['F10 every 10 min'],
           'B080 band VM 0.8': [dict(vm=0.9)], 'B120 band VM 1.2': [dict(vm=1.1)]}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
mar, sharpe, mdd, seg_mar, boot_mar, bulenox = NV.mar, NV.sharpe, NV.mdd, NV.seg_mar, NV.boot_mar, NV.bulenox


def drop5(r): return r.drop(r.nlargest(5).index)


def main():
    try:                                    # below-normal priority: NinjaTrader is live while this runs
        import ctypes; k32 = ctypes.windll.kernel32; k32.SetPriorityClass(k32.GetCurrentProcess(), 0x4000)
    except Exception: pass
    r0, n0 = N.series('MNQ', fn=N.fam_V); d0, _ = N.series('MNQDB', fn=N.fam_V)
    r0_2, _ = N.series('MNQ', fn=N.fam_V, ticks=2); d0_2, _ = N.series('MNQDB', fn=N.fam_V, ticks=2)
    r0_z, _ = N.series('MNQ', fn=N.fam_V, ticks=-1)          # ticks=-1: (0.25 x -1 + 0.25 commission) = zero cost per side
    fresh0 = {nm: sharpe(N.single(nm, fn=N.fam_V, a=a, b=b).g) for nm, a, b in NV.FRESH}
    b0 = bulenox(r0, n0)
    yr = lambda r: r.groupby(r.index.year).sum() * USD
    pr(f"ENTRYRULES (ENTRYRULES_PLAN.txt). Base = live NoiseArea via nav_lab.fam_V: {len(r0)} sessions {r0.index[0].date()} .. {r0.index[-1].date()}, "
       f"${r0.mean() * 252 * USD:,.0f}/yr, Sharpe {sharpe(r0):.2f}, maxDD ${mdd(r0.values * USD):,.0f}, MAR {mar(r0):.3f}, {n0.sum() / len(r0) * 252:.0f} trades/yr")
    pr(f"   base MAR: NQ {seg_mar(r0, 'NQ'):.3f} | MNQ NT {seg_mar(r0, 'MNQ'):.3f} | MNQ Databento {seg_mar(d0, 'MNQ'):.3f}; zero-cost Sharpe {sharpe(r0_z):.2f}; "
       f"fresh gross Sharpe " + ', '.join(f"{k} {v:+.2f}" for k, v in fresh0.items()))
    pr(f"   base Bulenox 50K (1 micro): 2-year payouts ${b0[0]:,.0f}, open after 1 year {b0[1] * 100:.0f}%, qualify in 21 sessions {b0[2] * 100:.1f}%")
    rows = []; series = {}; verdicts = {}
    for name, kw in VARIANTS.items():
        r, n = N.series('MNQ', fn=N.fam_V, **kw); d, _ = N.series('MNQDB', fn=N.fam_V, **kw); series[name] = r
        r2, _ = N.series('MNQ', fn=N.fam_V, ticks=2, **kw); dd2, _ = N.series('MNQDB', fn=N.fam_V, ticks=2, **kw)
        rz, _ = N.series('MNQ', fn=N.fam_V, ticks=-1, **kw)
        p1 = boot_mar(r, r0); p2 = boot_mar(r2, r0_2)
        g1 = p1 < P_BAR and sharpe(r) >= sharpe(r0) - 0.05
        segs = {'NQ': (seg_mar(r, 'NQ'), seg_mar(r0, 'NQ')), 'MNQ NT': (seg_mar(r, 'MNQ'), seg_mar(r0, 'MNQ')), 'MNQ DB': (seg_mar(d, 'MNQ'), seg_mar(d0, 'MNQ'))}
        g2 = all(v > b for v, b in segs.values())
        fr = {nm: sharpe(N.single(nm, fn=N.fam_V, a=a, b=b, **kw).g) for nm, a, b in NV.FRESH}
        g3 = fr['ES'] > fresh0['ES'] and sum(fr[m] > fresh0[m] for m in ('MES', 'MYM', 'M2K')) >= 2
        segs2 = [seg_mar(r2, 'NQ') > seg_mar(r0_2, 'NQ'), seg_mar(r2, 'MNQ') > seg_mar(r0_2, 'MNQ'), seg_mar(dd2, 'MNQ') > seg_mar(d0_2, 'MNQ')]
        g5 = p2 < P_BAR and sharpe(r2) >= sharpe(r0_2) - 0.05 and all(segs2)
        bv = bulenox(r, n); g6 = bv[0] >= b0[0] and bv[1] >= b0[1]
        gz = not (sharpe(rz) > sharpe(r0_z) and sharpe(r) <= sharpe(r0))       # Z: a gain only at zero cost is a turnover problem
        g_r5 = mar(drop5(r)) > mar(drop5(r0))
        dy = yr(r) - yr(r0); gy = (dy.sum() - dy.max()) > 0
        G = {'D1': g1, 'D2': g2, 'D3': g3, 'D5': g5, 'D6': g6, 'Z': gz, 'R5': g_r5, 'Y': gy}
        verdicts[name] = G
        pr(f"\n{name}: ${r.mean() * 252 * USD:,.0f}/yr, Sharpe {sharpe(r):.2f} (base {sharpe(r0):.2f}), maxDD ${mdd(r.values * USD):,.0f} "
           f"(base ${mdd(r0.values * USD):,.0f}), MAR {mar(r):.3f} (base {mar(r0):.3f}), {n.sum() / len(r) * 252:.0f} trades/yr (base {n0.sum() / len(r0) * 252:.0f})")
        pr(f"   D1 bootstrap P(MAR <= base) {p1:.4f} (bar {P_BAR:.4f}) | D2 MAR " + ', '.join(f"{k} {v:.3f} vs {b:.3f}" for k, (v, b) in segs.items()))
        pr(f"   D3 fresh gross Sharpe " + ', '.join(f"{k} {fr[k]:+.2f} vs {fresh0[k]:+.2f}" for k in fr) +
           f" | D5 2 ticks: P {p2:.4f}, Sharpe {sharpe(r2):.2f} vs {sharpe(r0_2):.2f}, segments {segs2}")
        pr(f"   D6 Bulenox 50K: payouts ${bv[0]:,.0f} vs ${b0[0]:,.0f}, open after 1 year {bv[1] * 100:.0f}% vs {b0[1] * 100:.0f}%, qualify {bv[2] * 100:.1f}% vs {b0[2] * 100:.1f}%")
        pr(f"   Z zero-cost Sharpe {sharpe(rz):.2f} vs base {sharpe(r0_z):.2f} | R5 MAR without the 5 best days {mar(drop5(r)):.3f} vs {mar(drop5(r0)):.3f} | "
           f"Y gain by year: " + ' '.join(f"{y} {v:+,.0f}" for y, v in dy.items()))
        pr("   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()))
        rows.append(r.rename(name.split()[0]))
    pr('\nPLATEAU (P) for variants that pass every gate above:')
    winners = [nm for nm, G in verdicts.items() if all(G.values())]
    for nm in winners:
        ok = True
        for nb in PLATEAU.get(nm, []):
            if isinstance(nb, str): rr = series[nb]; lab = nb
            else: rr, _ = N.series('MNQ', fn=N.fam_V, **nb); lab = str(nb)
            good = mar(rr) > mar(r0); ok &= good
            pr(f"   {nm}: neighbour {lab} MAR {mar(rr):.3f} vs base {mar(r0):.3f} -> {'ok' if good else 'WORSE THAN BASE'}")
        pr(f"   {nm}: {'RECOMMENDED for a Sim101 forward test' if ok else 'not adopted (plateau)'}")
    if not winners: pr('   none - no variant passes every gate; the live rules stay as they are.')
    pd.concat([r0.rename('base')] + rows, axis=1).to_csv(os.path.join(HERE, 'entryrules_series.csv'))
    with open(os.path.join(HERE, 'entryrules_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
