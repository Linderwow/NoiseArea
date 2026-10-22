r"""Independent checks of the DAYCAP switches in nav_lab.fam_V (DAYCAP_PLAN.txt), written separately from them.
    python daycap_check.py  -> daycap_check_output.txt
 1. C1-C4 re-implemented as explicit per-day trade lists (entry/exit fills, losses counted from the list) and K1 (exit gate + lunch
    pause + EMA, with its own EMA) as a per-day event loop, on the MNQ NinjaTrader bars and NQ 2010-2019; daily P&L vs fam_V.
 2. The live strategy's own bars (fwd\live_bars_research_format.csv): the base must reproduce the live trades of 2026-10-21
    (three longs, decided 12:00, 13:00, 14:30).
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


def cap_day(mk, k, cap=None, stop_losses=None, no_relose=False):
    x, sg, hi, lo, fl = day_bounds(mk, k); trades = []; pos = 0; e = None; log = []
    for i in range(fl):
        if x.M[i] not in CHECKS or np.isnan(sg[i]): continue
        nw = decide(pos, x.C[i], hi * (1 + sg[i]), lo * (1 - sg[i]), x.vw[i])
        if nw != 0 and nw != pos:
            lost = [p for p, a, b in trades if p * (b - a) < 1.0]
            n_open = len(trades) + (pos != 0)
            if (cap is not None and n_open >= cap) or (stop_losses is not None and len(lost) >= stop_losses) or (no_relose and nw in lost):
                nw = 0
        if nw != pos:
            px = x.O[i + 1]
            if pos != 0: trades.append((pos, e, px))
            if nw != 0: e = px; log.append((x.M[i], nw))
            pos = nw
    if pos != 0: trades.append((pos, e, x.C[fl]))
    return sum(p * (b - a) for p, a, b in trades), log


def ema_all(mk):
    out_, e = [], None
    for x in mk.days:
        a = np.empty(x.nb)
        for i in range(x.nb):
            e = x.C[i] if e is None else e + (2 / 21) * (x.C[i] - e); a[i] = e
        out_.append(a)
    return out_


def k1_day(mk, k, E, n=4):
    x, sg, hi, lo, fl = day_bounds(mk, k); trades = []; pos = 0; e = None; cnt = 0; i = 0
    while i < fl:
        if pos != 0 and not np.isnan(sg[i]):
            ub, lb = hi * (1 + sg[i]), lo * (1 - sg[i])
            bad = x.C[i] < max(ub, x.vw[i]) if pos == 1 else x.C[i] > min(lb, x.vw[i])
            cnt = cnt + 1 if bad else 0
            if cnt == n:
                trades.append((pos, e, x.O[i + 1])); pos = 0; cnt = 0; i += 1; continue
        if pos == 0 and x.M[i] in CHECKS and not np.isnan(sg[i]) and x.M[i] not in (720, 750, 780):
            nw = decide(0, x.C[i], hi * (1 + sg[i]), lo * (1 - sg[i]), x.vw[i])
            if (nw == 1 and x.C[i] > E[k][i]) or (nw == -1 and x.C[i] < E[k][i]): pos = nw; e = x.O[i + 1]; cnt = 0
        i += 1
    if pos != 0: trades.append((pos, e, x.C[fl]))
    return sum(p * (b - a) for p, a, b in trades)


def main():
    cases = (('C1', dict(cap=1)), ('C2', dict(cap=2)), ('C3', dict(stop_losses=2)), ('C4', dict(no_relose=True)))
    for nm in ('MNQ', 'NQ'):
        mk = N.market(nm); base, _ = N.fam_V(mk); E = ema_all(mk)
        for lab, kw in cases:
            p, _ = N.fam_V(mk, **kw); diffs = []; nd = 0
            for k in range(mk.n):
                if k < 14 or np.isnan(mk.pc[k]): continue
                q, _ = cap_day(mk, k, **kw); diffs.append(abs(q - p[k])); nd += abs(p[k] - base[k]) > 1e-9
            pr(f"1. {nm} {lab}: {len(diffs)} sessions, max |daily P&L diff| {max(diffs):.6f} points, {nd} days differ from the base")
        p, _ = N.fam_V(mk, gate=4, lunch=True, ema=True)
        diffs = [abs(k1_day(mk, k, E) - p[k]) for k in range(mk.n) if not (k < 14 or np.isnan(mk.pc[k]))]
        pr(f"1. {nm} K1: {len(diffs)} sessions, max |daily P&L diff| {max(diffs):.6f} points")
    from research_engine import Market
    lm = Market(os.path.join(HERE, 'fwd', 'live_bars_research_format.csv'), 1.0, 'LIVE')
    k = int(np.where(lm.dates == pd.Timestamp('2026-10-21'))[0][0])
    for lab, kw in (('base', {}),) + cases:
        q, log = cap_day(lm, k, **kw)
        pr(f"2. live bars 2026-10-21 {lab}: entries " + ', '.join(f"{m // 60}:{m % 60:02d} {'long' if d > 0 else 'short'}" for m, d in log) +
           f" | {q:+.2f} points gross")
    with open(os.path.join(HERE, 'daycap_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
