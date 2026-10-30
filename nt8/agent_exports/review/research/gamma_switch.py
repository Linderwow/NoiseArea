r"""GAMMA_SWITCH_PLAN.txt (pre-registered 2026-10-30): the dealer-gamma state (prior-day SPX GEX, Squeezemetrics) as the market-structure
read that picks the strategy - REGIME's switch with the one state variable that has a documented mechanism.
    python gamma_switch.py -> gamma_switch_output.txt
Uses regime_test.py's audited catalog matrices (regime_work\*.npz) and gex_dix.csv. Below-normal priority, one core.
"""
import os, sys, json
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import regime_test as rt

FIT = ('2011-05-03', '2014-12-31'); VAL = ('2015-01-01', '2019-05-05'); DISC = ('2011-05-03', '2019-05-05'); HOLD = ('2019-05-06', '2026-10-16')
out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)
def save(): open(os.path.join(HERE, 'gamma_switch_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


def prior_gex(dates):
    g = pd.read_csv(os.path.join(HERE, 'gex_dix.csv'), parse_dates=['date']).set_index('date').gex
    v = np.full(len(dates), np.nan); miss = 0
    for k in range(1, len(dates)):
        d_prev = dates[k - 1]
        if d_prev in g.index: v[k] = g[d_prev]
        elif dates[k] >= pd.Timestamp('2011-05-03'): miss += 1
    return v, miss


def states(gp, scheme, ref_mask):
    ok = ~np.isnan(gp); cid = np.full(len(gp), -1)
    if scheme == 'G3':
        cuts = list(np.percentile(gp[ref_mask & ok], [100 / 3, 200 / 3])); cid[ok] = np.digitize(gp[ok], cuts)
    else:
        cuts = [0.0]; cid[ok] = (gp[ok] >= 0).astype(int)
    return cid, cuts


def boot_p(dates, a, b, n=10000, seed=rt.SEED):
    """P(model Sharpe <= benchmark Sharpe) under a month-block bootstrap of the paired daily returns."""
    blocks = rt.month_blocks(dates); rng = np.random.default_rng(seed); worse = 0
    for _ in range(n):
        ix = np.concatenate([blocks[j] for j in rng.integers(0, len(blocks), len(blocks))])
        worse += rt.sharpe(a[ix]) <= rt.sharpe(b[ix])
    return worse / n


def main():
    rt_ok = rt.audit()
    if not rt_ok: pr("regime_test audit failed - stop"); save(); return
    dates, R, NT, c, p_ref, px, P, full, _ = rt.joined('MNQ'); N = 2 * p_ref
    gp, miss = prior_gex(dates); has = ~np.isnan(gp)
    fit, val, disc, hold = (rt.between(dates, w) for w in (FIT, VAL, DISC, HOLD))
    fit &= has; val &= has; disc &= has; hold &= has
    pr(f"GAMMA SWITCH (GAMMA_SWITCH_PLAN.txt): sessions with a prior-day GEX {has.sum():,} of {len(dates):,} (dropped for a missing prior GEX date: {miss}); "
       f"FIT {fit.sum()}, VALIDATE {val.sum()}, HOLDOUT {hold.sum()}; notional ${N:,.0f}")
    na = R[:, rt.A_DEF]
    # audit: same strategy in every cell reproduces its series
    cid3, cuts3 = states(gp, 'G3', fit); sel_same = {cc: [(rt.A_DEF, 1.0)] for cc in range(3)}
    r_same, _ = rt.apply(R, NT, cid3, sel_same)
    pr(f"audit: NoiseArea in every gamma cell == NoiseArea's own series on the state days: {'PASS' if np.allclose(r_same[has], na[has]) else 'FAIL'}")
    pr(f"G3 tercile cuts (FIT): {cuts3[0] / 1e9:.2f}bn / {cuts3[1] / 1e9:.2f}bn; share of HOLDOUT days per cell: " +
       ', '.join(f"{(cid3[hold] == cc).mean() * 100:.0f}%" for cc in range(3)) + f"; GEX < 0 on {np.mean(gp[hold] < 0) * 100:.0f}% of holdout days")

    # mechanism footprint: each family default per gamma state, FIT / VALIDATE / HOLDOUT ($/day)
    pr("\nMECHANISM FOOTPRINT - $/day per micro by prior-day GEX tercile (low / mid / high), family defaults:")
    pr(f"   {'family':<34} {'FIT low/mid/high':>24}   {'VALIDATE low/mid/high':>24}   {'HOLDOUT low/mid/high':>24}")
    for fam, j in zip(rt.DEFAULTS, rt.DEF_IDX):
        cells_txt = []
        for m in (fit, val, hold):
            cells_txt.append(' / '.join(f"{R[m & (cid3 == cc), j].mean() * N:+6.1f}" for cc in range(3)))
        pr(f"   {fam} {rt.FAMILY_NAMES[fam]:<32} {cells_txt[0]:>24}   {cells_txt[1]:>24}   {cells_txt[2]:>24}")
    pr("   by sign (GEX < 0 / >= 0), NoiseArea: " + '; '.join(f"{lab} {na[m & (gp < 0)].mean() * N:+.1f} / {na[m & (gp >= 0)].mean() * N:+.1f} (n {int((m & (gp < 0)).sum())} / {int((m & (gp >= 0)).sum())})"
                                                              for lab, m in (('FIT', fit), ('VAL', val), ('HOLD', hold))))
    save()

    # the two registered configurations + the full-catalog information rows
    rows = []
    for scheme in ('G3', 'G2'):
        for cat in ('defaults', 'full'):
            cid, cuts = states(gp, scheme, fit); sel = rt.select(R, NT, cid, fit, rt.cat_cols(cat), 1)
            rv, nv = rt.apply(R, NT, cid, sel)
            rows.append(dict(scheme=scheme, catalog=cat, fit_sharpe=rt.sharpe(rv[fit]), val_sharpe=rt.sharpe(rv[val]), val_usd_yr=rv[val].sum() * N / (val.sum() / 252),
                             val_trades=int(nv[val].sum()), picks='; '.join(f"cell {cc}: {rt.label(lst[0][0]) if lst else 'flat'}" for cc, lst in sel.items())))
    tab = pd.DataFrame(rows)
    pr("\nCONFIGURATIONS fitted on FIT, scored on VALIDATE (the two 'defaults' rows are the registered trials; 'full' is information):")
    for r in rows: pr(f"   {r['scheme']} {r['catalog']:<9} FIT Sharpe {r['fit_sharpe']:+.2f}  VALIDATE Sharpe {r['val_sharpe']:+.2f}  ${r['val_usd_yr']:,.0f}/yr  trades {r['val_trades']}  | {r['picks']}")
    pr(f"   NoiseArea alone: FIT Sharpe {rt.sharpe(na[fit]):+.2f}, VALIDATE Sharpe {rt.sharpe(na[val]):+.2f}, ${na[val].sum() * N / (val.sum() / 252):,.0f}/yr")
    best = max((r for r in rows if r['catalog'] == 'defaults'), key=lambda r: r['val_sharpe'])
    pr(f"CHOSEN (best VALIDATE Sharpe among the registered two): {best['scheme']} defaults")
    cid, cuts = states(gp, best['scheme'], disc); sel = rt.select(R, NT, cid, disc, rt.DEF_IDX, 1)
    pr("refitted on FIT + VALIDATE: " + '; '.join(f"cell {cc}: {rt.label(lst[0][0]) if lst else 'flat'}" for cc, lst in sel.items()))
    save()

    # the single holdout run
    rm, nm = rt.apply(R, NT, cid, sel)
    for lab, mm in (('HOLDOUT NinjaTrader MNQ', hold),):
        Mm, Mn = rt.metrics(rm[mm], nm[mm], dates[mm], N), rt.metrics(na[mm], NT[mm, rt.A_DEF].astype(float), dates[mm], N)
        P = boot_p(dates[mm], rm[mm], na[mm])
        po_m, _ = rt.prop_odds(dates[mm], rm[mm] * N); po_n, _ = rt.prop_odds(dates[mm], na[mm] * N)
        pr(f"\n{lab} {dates[mm][0].date()} .. {dates[mm][-1].date()} ({mm.sum()} sessions):")
        pr(f"   gamma switch   {rt.fmt(Mm)}  50K pass odds {po_m * 100:.0f}%")
        pr(f"   NoiseArea      {rt.fmt(Mn)}  50K pass odds {po_n * 100:.0f}%")
        pr(f"   bootstrap P(switch Sharpe <= NoiseArea's) = {P:.3f}; prop rule: DD {Mm['maxdd'] / Mn['maxdd']:.2f}x, $ {Mm['usd_yr'] / Mn['usd_yr']:.2f}x, pass odds {po_m * 100 - po_n * 100:+.0f} pts")
        usable = (Mm['sharpe'] > Mn['sharpe'] and P < 0.05) or (Mm['maxdd'] <= 0.6 * Mn['maxdd'] and Mm['usd_yr'] >= 0.6 * Mn['usd_yr'] and po_m >= po_n + 0.10)
        pr(f"   -> {'USABLE (pending the Databento holdout)' if usable else 'DEAD'}")
        for y in sorted(set(dates[mm].year)):
            ym = mm & (dates.year == y); pr(f"      {y}: switch ${rm[ym].sum() * N:>8,.0f}   NoiseArea ${na[ym].sum() * N:>8,.0f}")
    # secondary holdout
    d2, R2, NT2, *_ = rt.joined('MNQDB'); gp2, _ = prior_gex(d2); h2 = rt.between(d2, HOLD) & ~np.isnan(gp2)
    cid2 = np.full(len(gp2), -1); ok2 = ~np.isnan(gp2)
    cid2[ok2] = np.digitize(gp2[ok2], cuts) if best['scheme'] == 'G3' else (gp2[ok2] >= 0).astype(int)
    rm2, nm2 = rt.apply(R2, NT2, cid2, sel); na2 = R2[:, rt.A_DEF]
    M2m, M2n = rt.metrics(rm2[h2], nm2[h2], d2[h2], N), rt.metrics(na2[h2], NT2[h2, rt.A_DEF].astype(float), d2[h2], N)
    pr(f"\nSECONDARY HOLDOUT Databento MNQ ({h2.sum()} sessions): switch {rt.fmt(M2m)} | NoiseArea {rt.fmt(M2n)} | P {boot_p(d2[h2], rm2[h2], na2[h2]):.3f}")
    # information: the other registered configuration and the full-catalog switch on the holdout
    pr("\nINFORMATION - every configuration on the holdout (refitted on FIT + VALIDATE):")
    for scheme in ('G3', 'G2'):
        for cat in ('defaults', 'full'):
            cidx, _ = states(gp, scheme, disc); selx = rt.select(R, NT, cidx, disc, rt.cat_cols(cat), 1); rx, nx = rt.apply(R, NT, cidx, selx)
            Mx = rt.metrics(rx[hold], nx[hold], dates[hold], N)
            pr(f"   {scheme} {cat:<9} {rt.fmt(Mx)}  | " + '; '.join(f"cell {cc}: {rt.label(lst[0][0])[:28] if lst else 'flat'}" for cc, lst in selx.items()))
    # information: NoiseArea on low+mid gamma days only, and the best reversion default on high-gamma days alone (holdout)
    rev = [j for fam, j in zip(rt.DEFAULTS, rt.DEF_IDX) if fam in 'CDEGHI']
    hi_days = hold & (cid3 == 2)
    pr(f"\nINFORMATION - holdout high-gamma days ({hi_days.sum()}): NoiseArea ${na[hi_days].sum() * N:,.0f} total; reversion defaults there: " +
       ', '.join(f"{rt.FAM_OF[j]} ${R[hi_days, j].sum() * N:,.0f}" for j in rev))
    save()


if __name__ == '__main__':
    rt.below_normal() if hasattr(rt, 'below_normal') else None
    main()
