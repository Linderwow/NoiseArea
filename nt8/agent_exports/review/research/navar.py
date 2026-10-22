r"""NoiseArea modifications for a drawdown-limited account (NAVAR_PLAN.txt, pre-registered 2026-10-20).
    python navar.py      -> navar_output.txt, navar_series.csv
Every variant runs through nav_lab.fam_V (== the live rules when no switch is set) on the 16-year NQ + MNQ series, the Databento MNQ
source and four fresh markets, then through the Bulenox 50K Master model (bulenox_routes).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import bulenox_routes as BR
import src2_lib as L

USD = L.N_USD; JOIN = N.JOIN; K = 9; P_BAR = 0.05 / K
VARIANTS = {'V1 long-only': dict(direction='long'), 'V2 shorts at half size': dict(w_short=0.5), 'V3 90-day lookback': dict(look=90),
            'V4 fast-alpha execution': dict(fast=True), 'V5 exit persistence gate (4 bars)': dict(gate=4), 'V6 lunch pause': dict(lunch=True),
            'V7 EMA(20) confirmation': dict(ema=True), 'V8 intraday shock control': dict(shock='frac'), 'V9 no gap adjustment': dict(no_gap=True)}
BULENOX_2 = {'V2 shorts at half size': dict(w_long=2.0, w_short=1.0), 'V8 intraday shock control': dict(shock='steps2')}
FRESH = (('ES', '2010-07-01', '2019-05-05'), ('MES', '2019-05-06', '2026-10-16'), ('MYM', '2019-05-06', '2026-10-16'),
         ('M2K', '2019-05-06', '2026-10-16'))
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def mdd(x):
    eq = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())


def mar(r): d = mdd(r.values * USD); return (r.mean() * 252 * USD) / d if d > 0 else np.inf


def sharpe(r): return r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0.0


def boot_mar(rv, rb, n=10000, seed=20261020):
    idx = rv.index; key = idx.year * 12 + idx.month; blocks = [np.where(key == k)[0] for k in np.unique(key)]
    rng = np.random.default_rng(seed); a, b = rv.values * USD, rb.values * USD; worse = 0
    for _ in range(n):
        ix = np.concatenate([blocks[j] for j in rng.integers(0, len(blocks), len(blocks))])
        x, y = a[ix], b[ix]; dx, dy = mdd(x), mdd(y)
        mx = x.mean() / dx if dx > 0 else np.inf; my = y.mean() / dy if dy > 0 else np.inf
        worse += mx <= my
    return worse / n


def seg_mar(r, part):
    return mar(r[r.index <= JOIN]) if part == 'NQ' else mar(r[r.index > JOIN])


def bulenox(r, nt):
    usd = r.values * USD; X, NT = BR.boot_paths(504, 20000, usd, nt.values.astype(float)); p = BR.SIZES['50K']
    pay, ou = BR.master(X, NT, p); return pay.sum(1).mean() - p['act'], float(np.mean(ou > 251)), BR.qualify(X, p)


def main():
    r0, n0 = N.series('MNQ', fn=N.fam_V); d0, _ = N.series('MNQDB', fn=N.fam_V); r0_2, _ = N.series('MNQ', fn=N.fam_V, ticks=2)
    d0_2, _ = N.series('MNQDB', fn=N.fam_V, ticks=2)
    fresh0 = {nm: sharpe(N.single(nm, fn=N.fam_V, a=a, b=b).g) for nm, a, b in FRESH}
    b0 = bulenox(r0, n0); b0_2 = bulenox(r0 * 2, n0)
    yr = lambda r: ' | '.join(f"{y} {v * USD:+,.0f}" for y, v in r.groupby(r.index.year).sum().items())
    pr(f"NAVAR (NAVAR_PLAN.txt). Base = live NoiseArea via nav_lab.fam_V: {len(r0)} sessions {r0.index[0].date()} .. {r0.index[-1].date()}, "
       f"${r0.mean() * 252 * USD:,.0f}/yr, Sharpe {sharpe(r0):.2f}, maxDD ${mdd(r0.values * USD):,.0f}, MAR {mar(r0):.3f}, {n0.sum() / len(r0) * 252:.0f} trades/yr")
    pr(f"   base MAR: NQ {seg_mar(r0, 'NQ'):.3f} | MNQ NT {seg_mar(r0, 'MNQ'):.3f} | MNQ Databento {seg_mar(d0, 'MNQ'):.3f}; fresh-market gross Sharpe: "
       + ', '.join(f"{k} {v:+.2f}" for k, v in fresh0.items()))
    pr(f"   base Bulenox 50K (1 micro): 2-year payouts net of activation ${b0[0]:,.0f}, open after 1 year {b0[1] * 100:.0f}%, "
       f"qualify in 21 sessions {b0[2] * 100:.1f}% | 2 micros: ${b0_2[0]:,.0f}, {b0_2[1] * 100:.0f}%, {b0_2[2] * 100:.1f}%")
    pr(f"   base per year: {yr(r0)}")
    rows = []
    for name, kw in VARIANTS.items():
        r, n = N.series('MNQ', fn=N.fam_V, **kw); d, _ = N.series('MNQDB', fn=N.fam_V, **kw)
        r2, _ = N.series('MNQ', fn=N.fam_V, ticks=2, **kw); dd2, _ = N.series('MNQDB', fn=N.fam_V, ticks=2, **kw)
        p1 = boot_mar(r, r0); p2 = boot_mar(r2, r0_2)
        g1 = p1 < P_BAR and sharpe(r) >= sharpe(r0) - 0.05
        segs = {'NQ': (seg_mar(r, 'NQ'), seg_mar(r0, 'NQ')), 'MNQ NT': (seg_mar(r, 'MNQ'), seg_mar(r0, 'MNQ')), 'MNQ DB': (seg_mar(d, 'MNQ'), seg_mar(d0, 'MNQ'))}
        g2 = all(v > b for v, b in segs.values())
        fr = {nm: sharpe(N.single(nm, fn=N.fam_V, a=a, b=b, **kw).g) for nm, a, b in FRESH}
        g3 = fr['ES'] > fresh0['ES'] and sum(fr[m] > fresh0[m] for m in ('MES', 'MYM', 'M2K')) >= 2
        segs2 = [seg_mar(r2, 'NQ') > seg_mar(r0_2, 'NQ'), seg_mar(r2, 'MNQ') > seg_mar(r0_2, 'MNQ'), seg_mar(dd2, 'MNQ') > seg_mar(d0_2, 'MNQ')]
        g5 = p2 < P_BAR and sharpe(r2) >= sharpe(r0_2) - 0.05 and all(segs2)
        if name in BULENOX_2:
            rb, nb = N.series('MNQ', fn=N.fam_V, **BULENOX_2[name]); bv, bref, lab = bulenox(rb, nb), b0_2, '2 micros'
        else:
            bv, bref, lab = bulenox(r, n), b0, '1 micro'
        g6 = bv[0] >= bref[0] and bv[1] >= bref[1]
        G = {'D1': g1, 'D2': g2, 'D3': g3, 'D5': g5, 'D6': g6}
        plat = ''
        if 'lookback' in name:
            pl = {}
            for lk in (60, 120):
                rr, _ = N.series('MNQ', fn=N.fam_V, look=lk); dd, _ = N.series('MNQDB', fn=N.fam_V, look=lk)
                pl[lk] = (seg_mar(rr, 'NQ') > seg_mar(r0, 'NQ')) and (seg_mar(rr, 'MNQ') > seg_mar(r0, 'MNQ')) and (seg_mar(dd, 'MNQ') > seg_mar(d0, 'MNQ'))
                plat += f" look {lk}: MAR NQ {seg_mar(rr, 'NQ'):.3f} / MNQ {seg_mar(rr, 'MNQ'):.3f} / DB {seg_mar(dd, 'MNQ'):.3f} -> {'pass' if pl[lk] else 'fail'};"
            G['D4'] = all(pl.values())
        ok = all(G.values())
        pr(f"\n{name}: ${r.mean() * 252 * USD:,.0f}/yr, Sharpe {sharpe(r):.2f} (base {sharpe(r0):.2f}), maxDD ${mdd(r.values * USD):,.0f} "
           f"(base ${mdd(r0.values * USD):,.0f}), MAR {mar(r):.3f} (base {mar(r0):.3f}), {n.sum() / len(r) * 252:.0f} trades/yr")
        pr(f"   D1 bootstrap P(MAR <= base) {p1:.4f} (bar {P_BAR:.4f}) | D2 MAR " + ', '.join(f"{k} {v:.3f} vs {b:.3f}" for k, (v, b) in segs.items()))
        pr(f"   D3 fresh gross Sharpe " + ', '.join(f"{k} {fr[k]:+.2f} vs {fresh0[k]:+.2f}" for k in fr) +
           f" | D5 2 ticks: P {p2:.4f}, Sharpe {sharpe(r2):.2f} vs {sharpe(r0_2):.2f}, segments {segs2}")
        pr(f"   D6 Bulenox 50K ({lab}): 2-year payouts ${bv[0]:,.0f} vs ${bref[0]:,.0f}, open after 1 year {bv[1] * 100:.0f}% vs {bref[1] * 100:.0f}%, "
           f"qualify in 21 sessions {bv[2] * 100:.1f}% vs {bref[2] * 100:.1f}%" + (f" |{plat}" if plat else ''))
        pr(f"   per year: {yr(r)}")
        pr("   gates: " + ', '.join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in G.items()) + f"  -> {'RECOMMENDED for a Sim101 forward test' if ok else 'not adopted'}")
        rows.append(r.rename(name.split()[0]))
    pd.concat([r0.rename('base')] + rows, axis=1).to_csv(os.path.join(HERE, 'navar_series.csv'))
    with open(os.path.join(HERE, 'navar_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
