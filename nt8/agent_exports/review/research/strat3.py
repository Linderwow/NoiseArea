r"""STRAT3 (STRAT3_PLAN.txt, pre-registered 2026-10-24): the entry / exit mechanics never tested on NoiseArea - pyramiding (P1, P2),
breakeven stop (BE), time stop (TS), chandelier trail (CH).
    python strat3.py   -> strat3_output.txt
A bar-by-bar engine of the live rules (nav_lab.fam_V) with the five switches; with every switch off it must reproduce fam_V exactly.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import history_extend as HX                 # registers nav_lab.FILES['MNQX']
import navar as NV
import metalabel as ML

LOOK = 14; CHECKS = set(range(600, 931, 30)); P_BAR = 0.05 / 5; USD = ML.USD
VARIANTS = {'P1 pyramid at +1 width': dict(pyr=1.0), 'P2 pyramid at +2 widths': dict(pyr=2.0), 'BE breakeven after +1 width': dict(be=1.0),
            'TS time stop, 60 min not in profit': dict(ts=2), 'CH chandelier 3 x ATR': dict(ch=3.0)}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def run(mk, pyr=None, be=None, ts=None, ch=None, worst_out=None):
    """Returns (daily points per base unit, units traded / 2 as fam_V counts trades, average units while in a position). worst_out (an
    array of mk.n) receives each session's worst point of realized + open P&L, marked at every bar's adverse extreme exactly as
    intraday_worst.day_close_and_worst does (added after the verdict, for the Bulenox diagnostic)."""
    sig = mk.sigma(LOOK); pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n); u_bars = 0; in_bars = 0
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, H, L, C, M, vw, nb, A = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb, x.A
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        st = dict(d=0, u=0, ref=0.0, p=0.0, vol=0, worst=0.0)
        e1 = 0.0; held = 0; ext = 0.0; armed = False; w_in = 0.0

        def mark(i):
            pos = st['d'] * st['u']
            if pos: st['worst'] = min(st['worst'], st['p'] + pos * ((L[i] if pos > 0 else H[i]) - st['ref']))

        def change(newpos, px):
            pos = st['d'] * st['u']; st['p'] += pos * (px - st['ref']); st['vol'] += abs(newpos - pos); st['ref'] = px
            st['d'] = int(np.sign(newpos)); st['u'] = abs(int(newpos))

        for i in range(flat_i):
            c = C[i]; d = st['d']; mark(i)
            if d != 0:
                u_bars += st['u']; in_bars += 1
                ext = max(ext, c) if d > 0 else min(ext, c)
                if be is not None:
                    if d * (ext - e1) >= be * w_in: armed = True
                    if armed and d * (c - e1) <= 0: change(0, O[i + 1]); continue
                if ch is not None and ((d > 0 and c < ext - ch * A[i]) or (d < 0 and c > ext + ch * A[i])): change(0, O[i + 1]); continue
            if M[i] not in CHECKS or np.isnan(sg[i]): continue
            ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
            if d == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif d == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if new == d and d != 0:
                held += 1
                if ts is not None and held >= ts and d * (c - e1) <= 0: new = 0
            if new != d:
                change(new, O[i + 1])
                if new != 0: e1 = O[i + 1]; held = 0; ext = e1; armed = False; w_in = O[0] * sg[i]
            elif d != 0 and pyr is not None and st['u'] == 1 and d * (c - e1) >= pyr * O[0] * sg[i]:
                change(2 * d, O[i + 1])
        if st['d'] != 0: mark(flat_i); change(0, C[flat_i])
        pnl[k] = st['p']; nt[k] = st['vol'] / 2
        if worst_out is not None: worst_out[k] = min(st['worst'], st['p'])
    return pnl, nt, (u_bars / in_bars if in_bars else 0.0)


def main():
    mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    pr(f"STRAT3 (STRAT3_PLAN.txt): NQ {mks['NQ'].dates[0].date()} .. {N.JOIN.date()} + MNQ to {mks['MNQX'].dates[-1].date()} (extended); "
       f"1 tick + $0.50 a side per unit; $ per base unit (1 micro) at MNQ 29,150")
    base = {}
    for nm, _ in ML.PARTS:
        mk = mks[nm]; p0, n0 = N.fam_V(mk); p1, n1, _ = run(mk)
        assert np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and np.array_equal(n0, n1), f"audit failed on {nm}"
        base[nm] = (mk, p1, n1)
    pr("audit: the engine with every switch off reproduces nav_lab.fam_V exactly on both markets")
    rb, rb2 = ML.joined(base), ML.joined(base, 2)
    def summ(r):
        eq = np.cumsum(r.values * USD); return r.mean() * 252 * USD, float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    a_, d_ = summ(rb)
    pr(f"\nlive rules {rb.index[0].date()} .. {rb.index[-1].date()}: ${a_:,.0f}/yr, max drawdown ${d_:,.0f}, MAR {NV.mar(rb):.3f}, Sharpe {NV.sharpe(rb):.2f}, "
       f"trades {ML.trades(base):,.0f}")
    pr(" variant                              $/yr    max DD   MAR    Sharpe  trades  avg units  G1 P(boot)   G2 NQ / MNQ MAR (live)          G3 2 ticks (live)   G4 drop-5   verdict")
    per_year = {'live rules': rb.groupby(rb.index.year).sum() * USD}
    for name, kw in VARIANTS.items():
        res = {}; units = []
        for nm, _ in ML.PARTS:
            p, n, au = run(mks[nm], **kw); res[nm] = (mks[nm], p, n); units.append(au)
        rv, rv2 = ML.joined(res), ML.joined(res, 2)
        a, d = summ(rv); P = NV.boot_mar(rv, rb)
        seg = [(NV.mar(rv[sl]), NV.mar(rb[sl])) for sl in (rv.index <= N.JOIN, rv.index > N.JOIN)]
        drop5 = NV.mar(rv.drop(rv.nlargest(5).index))
        g1 = NV.mar(rv) > NV.mar(rb) and P < P_BAR; g2 = all(v > b for v, b in seg); g3 = NV.mar(rv2) > NV.mar(rb2); g4 = drop5 > NV.mar(rb)
        pr(f" {name:<34} {a:>8,.0f}  {d:>7,.0f}  {NV.mar(rv):5.3f}   {NV.sharpe(rv):4.2f}  {ML.trades(res):6,.0f}    {np.mean(units):4.2f}     {P:6.4f} {'ok' if g1 else 'no'}   "
           f"{seg[0][0]:.3f} ({seg[0][1]:.3f}) / {seg[1][0]:.3f} ({seg[1][1]:.3f}) {'ok' if g2 else 'no'}   {NV.mar(rv2):.3f} ({NV.mar(rb2):.3f}) {'ok' if g3 else 'no'}   "
           f"{drop5:.3f} {'ok' if g4 else 'no'}   {'FORWARD-TEST CANDIDATE' if g1 and g2 and g3 and g4 else 'rejected'}")
        per_year[name] = rv.groupby(rv.index.year).sum() * USD
    years = sorted(set(rb.index.year))
    pr("\nper year, $ per base unit:")
    pr("   " + 'year'.ljust(36) + ''.join(f"{y:>8}" for y in years))
    for nm, s in per_year.items(): pr("   " + nm.ljust(36) + ''.join(f"{s.get(y, 0):>8,.0f}" for y in years))
    with open(os.path.join(HERE, 'strat3_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
