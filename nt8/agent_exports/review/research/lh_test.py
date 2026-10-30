r"""LH_PLAN.txt (pre-registered 2026-10-30): Baltussen et al.'s last-half-hour rule on NQ/MNQ - sign of (15:30 close - prior close)
traded from the 15:35 open to the 16:00 close; standalone (T1) and as an addition to NoiseArea on the days it is flat at 15:30 (T2).
    python lh_test.py -> lh_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX, navar as NV, metalabel as ML, chval as CV, regime3 as R3
import weekend as W
from research_engine import WARMUP

USD = ML.USD; out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)


def lh(mk):
    """Returns per session: LH points (nan = no session / skipped), traded (0/1), r_ROD, NoiseArea's position after the 15:30 decision."""
    sig = mk.sigma(14); n = mk.n; pts = np.full(n, np.nan); nt = np.zeros(n); rod = np.full(n, np.nan); na_pos = np.zeros(n)
    for k, x in enumerate(mk.days):
        if k < 14 or np.isnan(mk.pc[k]): continue
        M = list(x.M)
        if x.M[-1] < 960 or 930 not in M or 935 not in M: continue           # early close or missing bars
        i30, i35 = M.index(930), M.index(935)
        r = x.C[i30] / mk.pc[k] - 1; rod[k] = r; d = np.sign(r)
        sg = sig[k, x.slot]
        na_pos[k] = R3.decisions_to(x, mk.pc[k], sg, 930)[0] if not np.isnan(sg).any() else 0
        if d == 0: pts[k] = 0.0; continue
        pts[k] = d * (x.C[-1] - x.O[i35]); nt[k] = 1
    return pts, nt, rod, na_pos


def main():
    W.below_normal(); mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    pr("LH (LH_PLAN.txt): Baltussen last-half-hour rule, NQ 2010-19 + MNQ 2019-26, 1 tick + $0.50 a side, $ per micro at MNQ 29,150")
    res1, res2, resb, info = {}, {}, {}, {}
    for nm, keep in ML.PARTS:
        mk = mks[nm]; pts, nt, rod, pos = lh(mk); pb, nb_, _, _ = W.engine(mk)
        flat = pos == 0
        add_p = np.where(flat, np.nan_to_num(pts), 0.0); add_n = np.where(flat, nt, 0.0)
        res1[nm] = (mk, pts, nt); res2[nm] = (mk, np.nan_to_num(pb) + add_p, nb_ + add_n); resb[nm] = (mk, pb, nb_)
        info[nm] = dict(rod=rod, pos=pos, pts=pts, nt=nt)
        sel = (np.arange(mk.n) >= WARMUP) & keep(mk.dates)
        pr(f"   {nm}: {int(np.isfinite(pts[sel]).sum()):,} sessions with a 15:30 and 16:00 bar, {int(nt[sel].sum()):,} LH trades; NoiseArea flat at 15:30 on "
           f"{np.mean(flat[sel & np.isfinite(pts)]) * 100:.0f}% of them")
    r1 = ML.joined(res1); r2 = ML.joined(res2); rb = ML.joined(resb)
    def stats(lab, r):
        t = r.mean() / r.std() * np.sqrt(len(r)); yrs = r.groupby(r.index.year).sum()
        pr(f"   {lab:<40} ${r.mean() * 252 * USD:>7,.0f}/yr  Sharpe {NV.sharpe(r):5.2f}  t {t:+.2f}  max DD ${CV.mdd(r.values * USD):>6,.0f}  years positive {int((yrs > 0).sum())}/{len(yrs)}")
        return t
    pr("\nT1 STANDALONE")
    t_all = stats("whole series", r1); t_mnq = stats("MNQ 2019-26 alone", r1[r1.index > N.JOIN]); stats("NQ 2010-19 alone", r1[r1.index <= N.JOIN])
    stats("at 2 ticks a side", ML.joined(res1, 2))
    yrs = r1.groupby(r1.index.year).sum(); frac = float((yrs > 0).mean())
    edge = t_all >= 2.5 and t_mnq >= 2.0 and frac >= 0.6
    pr(f"   EDGE bar (t >= 2.5 whole, >= 2.0 MNQ, >= 60% years positive): {t_all:+.2f} / {t_mnq:+.2f} / {frac * 100:.0f}% -> {'EDGE' if edge else 'no'}")
    # by |r_ROD| tercile and by overlap with NoiseArea
    rods = np.concatenate([info[nm]['rod'][(np.arange(mks[nm].n) >= WARMUP) & keep(mks[nm].dates)] for nm, keep in ML.PARTS])
    ptsv = np.concatenate([info[nm]['pts'][(np.arange(mks[nm].n) >= WARMUP) & keep(mks[nm].dates)] for nm, keep in ML.PARTS])
    posv = np.concatenate([info[nm]['pos'][(np.arange(mks[nm].n) >= WARMUP) & keep(mks[nm].dates)] for nm, keep in ML.PARTS])
    ok = np.isfinite(rods) & np.isfinite(ptsv); q = np.nanpercentile(np.abs(rods[ok]), [100 / 3, 200 / 3])
    pr("   gross points per trade by |r_ROD| tercile: " + ', '.join(
        f"{lab} {ptsv[ok & m].mean():+.2f} (n {int((ok & m).sum())}, hit {np.mean(ptsv[ok & m] > 0) * 100:.0f}%)"
        for lab, m in (('small', np.abs(rods) < q[0]), ('mid', (np.abs(rods) >= q[0]) & (np.abs(rods) < q[1])), ('large', np.abs(rods) >= q[1]))))
    pr(f"   gross points per trade when NoiseArea is in a position at 15:30 {ptsv[ok & (posv != 0)].mean():+.2f} (n {int((ok & (posv != 0)).sum())}; same side as NoiseArea on "
       f"{np.mean(np.sign(rods[ok & (posv != 0)]) == posv[ok & (posv != 0)]) * 100:.0f}% of them) vs flat {ptsv[ok & (posv == 0)].mean():+.2f} (n {int((ok & (posv == 0)).sum())}); round trip cost 1.0 pt")
    pr("\nT2 ADDITION on NoiseArea's flat-at-15:30 days")
    stats("NoiseArea alone", rb); stats("NoiseArea + LH on flat days", r2)
    P = CV.boot_sharpe(r2, rb); pr(f"   bootstrap P(combined Sharpe <= NoiseArea's) = {P:.3f} (bar 0.01)")
    # Databento source
    mk3 = N.market('MNQDB'); pts3, nt3, rod3, pos3 = lh(mk3); pb3, nb3, _, _ = W.engine(mk3); flat3 = pos3 == 0
    keep3 = lambda d: d > N.JOIN
    rb3 = ML.to_series(mk3, pb3, nb3, keep3); r23 = ML.to_series(mk3, np.nan_to_num(pb3) + np.where(flat3, np.nan_to_num(pts3), 0.0), nb3 + np.where(flat3, nt3, 0.0), keep3)
    r13 = ML.to_series(mk3, pts3, nt3, keep3)
    pr(f"   Databento MNQ: LH alone ${r13.mean() * 252 * USD:,.0f}/yr t {r13.mean() / r13.std() * np.sqrt(len(r13)):+.2f}; NoiseArea Sharpe {NV.sharpe(rb3):.2f} vs + LH {NV.sharpe(r23):.2f}, P {CV.boot_sharpe(r23, rb3):.3f}")
    addition = NV.sharpe(r2) > NV.sharpe(rb) and P < 0.01 and NV.sharpe(r23) > NV.sharpe(rb3) and CV.boot_sharpe(r23, rb3) < 0.01
    pr(f"   ADDITION bar -> {'PASS' if addition else 'no'}")
    pr("\n   per year, LH standalone $: " + ', '.join(f"{y} {v * USD:+,.0f}" for y, v in yrs.items()))
    pr(f"\nVERDICT: T1 {'EDGE' if edge else 'DEAD'}; T2 {'ADDITION' if addition else 'DEAD'}")
    open(os.path.join(HERE, 'lh_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
