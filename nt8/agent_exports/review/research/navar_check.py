r"""Independent checks of nav_lab.fam_V (NAVAR_PLAN.txt), written separately from it.
    python navar_check.py  -> navar_check_output.txt
 1. V5 exit persistence gate and V4 fast-alpha execution re-implemented as explicit per-day event loops (trade lists, entry/exit
    prices) on the MNQ NinjaTrader bars and NQ 2010-2019; daily P&L compared with fam_V.
 2. V6 lunch pause, V9 no gap and V1 long-only checked against the published-rule state machine (research_engine Market.state)
    by construction: days without a blocked entry must be identical to the base.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
CHECKS = set(range(600, 931, 30))


def day_bounds(mk, k):
    x = mk.days[k]; sg = mk.sigma(14)[k, x.slot]
    return x, sg, max(x.O[0], mk.pc[k]), min(x.O[0], mk.pc[k]), (x.nb - 1 if x.M[-1] >= 960 else x.nb - 2)


def decide(pos, c, ub, lb, vw):
    if pos == 0: return 1 if c > ub else (-1 if c < lb else 0)
    if pos == 1: return -1 if c < lb else (0 if c < max(ub, vw) else 1)
    return 1 if c > ub else (0 if c > min(lb, vw) else -1)


def gate_day(mk, k, n=4):
    x, sg, hi, lo, fl = day_bounds(mk, k); trades = []; pos = 0; e = None; cnt = 0; i = 0
    while i < fl:
        if pos != 0 and not np.isnan(sg[i]):
            ub, lb = hi * (1 + sg[i]), lo * (1 - sg[i])
            bad = x.C[i] < max(ub, x.vw[i]) if pos == 1 else x.C[i] > min(lb, x.vw[i])
            cnt = cnt + 1 if bad else 0
            if cnt == n:
                trades.append((pos, e, x.O[i + 1])); pos = 0; cnt = 0; i += 1; continue
        if pos == 0 and x.M[i] in CHECKS and not np.isnan(sg[i]):
            nw = decide(0, x.C[i], hi * (1 + sg[i]), lo * (1 - sg[i]), x.vw[i])
            if nw != 0: pos = nw; e = x.O[i + 1]; cnt = 0
        i += 1
    if pos != 0: trades.append((pos, e, x.C[fl]))
    return sum(p * (b - a) for p, a, b in trades), len(trades)


def fast_day(mk, k):
    x, sg, hi, lo, fl = day_bounds(mk, k); pos = 0; e = 0.0; pnl = 0.0; n = 0; pend = None
    for i in range(fl):
        if x.M[i] in CHECKS and not np.isnan(sg[i]):
            nw = decide(pos, x.C[i], hi * (1 + sg[i]), lo * (1 - sg[i]), x.vw[i])
            pend = None if nw == pos else nw
            continue
        if pend is not None:
            buy = pend > pos
            if (buy and x.C[i] < x.O[i]) or (not buy and x.C[i] > x.O[i]):
                px = x.O[i + 1]
                if pos != 0: pnl += pos * (px - e)
                if pend != 0: e = px; n += 1
                pos = pend; pend = None
    if pos != 0: pnl += pos * (x.C[fl] - e)
    return pnl, n


def main():
    for nm in ('MNQ', 'NQ'):
        mk = N.market(nm)
        for lab, fn, kw in (('V5 gate', gate_day, dict(gate=4)), ('V4 fast', fast_day, dict(fast=True))):
            p, nt = N.fam_V(mk, **kw); diffs = []; tr_own = tr_lab = 0
            for k in range(mk.n):
                if k < 14 or np.isnan(mk.pc[k]): continue
                q, m = fn(mk, k); diffs.append(abs(q - p[k])); tr_own += m; tr_lab += nt[k]
            pr(f"1. {nm} {lab}: {len(diffs)} sessions, max |daily P&L diff| {max(diffs):.6f} points, trades own {tr_own} vs fam_V {tr_lab:.1f}")
        base, _ = N.fam_V(mk); st = mk.state()
        for lab, kw in (('V6 lunch', dict(lunch=True)), ('V9 no gap', dict(no_gap=True)), ('V1 long-only', dict(direction='long'))):
            v, _ = N.fam_V(mk, **kw); same = np.isclose(np.nan_to_num(v), np.nan_to_num(base)); n_diff = int((~same).sum())
            if lab == 'V6 lunch':                                 # differing days must have a base entry from flat at 12:00-13:00
                why = sum(1 for k in np.where(~same)[0] if st[k].valid and any(
                    st[k].mpos[i] != 0 and (i == 0 or st[k].mpos[i - 1] == 0) and mk.days[k].M[i] in (720, 750, 780) for i in range(mk.days[k].nb)))
                pr(f"2. {nm} {lab}: {n_diff} days differ from the base; {why} of them have a base entry from flat at 12:00/12:30/13:00")
            elif lab == 'V1 long-only':
                why = sum(1 for k in np.where(~same)[0] if st[k].valid and (st[k].mpos == -1).any())
                pr(f"2. {nm} {lab}: {n_diff} days differ from the base; {why} of them have a short position in the base")
            else:
                gapdays = sum(1 for k in np.where(~same)[0] if abs(mk.days[k].O[0] - mk.pc[k]) > 0)
                pr(f"2. {nm} {lab}: {n_diff} days differ from the base; {gapdays} of them open away from the prior close (all must)")
    with open(os.path.join(HERE, 'navar_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
