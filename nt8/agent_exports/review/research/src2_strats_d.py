import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import src2_test as T
from src2_test import strat, intraday, at, walk
from src2_strats_b import bars


def _fvgs(lab, h, l, gap, mid_lab):
    out = []
    for n in range(2, len(lab)):
        if l[n] - h[n - 2] >= gap: out.append((1, h[n - 2], l[n], mid_lab[n - 1], lab[n]))
        if l[n - 2] - h[n] >= gap: out.append((-1, h[n], l[n - 2], mid_lab[n - 1], lab[n]))
    return out


def strategy_b(k, mk, i0, i1, D, look=False, both=False, gap=3.0, age5=24, age15=96, buf=5.0, sbuf=2.0, rr=2.0, cut=720, hard=945):
    B = bars(mk, i0, i1, 5)
    if len(B) < 6: return
    lab = np.array([mk.m[b[0]] for b in B]); o = np.array([b[2] for b in B]); h = np.array([b[3] for b in B])
    l = np.array([b[4] for b in B]); c = np.array([b[5] for b in B])
    f5 = _fvgs(lab, h, l, gap, lab)
    g = (lab - 570) // 15; ks = np.unique(g)
    l15 = np.array([570 + 15 * q for q in ks]); h15 = np.array([h[g == q].max() for q in ks]); lo15 = np.array([l[g == q].min() for q in ks])
    f15 = _fvgs(l15, h15, lo15, gap, l15)
    for j in range(len(B) - 1):
        t = lab[j]
        if t >= cut: return
        for dr in ((1, -1) if both else (1,)):
            a5 = [f for f in f5 if f[0] == dr and f[3] < t and f[3] >= t - 5 * age5 and (c[j] >= (f[1] + f[2]) / 2 if dr > 0 else c[j] <= (f[1] + f[2]) / 2)]
            a15 = [f for f in f15 if f[0] == dr and f[3] < t and f[3] >= t - 15 * age15 and (look or f[4] + 15 <= t + 5)
                   and (c[j] >= (f[1] + f[2]) / 2 if dr > 0 else c[j] <= (f[1] + f[2]) / 2)]
            if not a5 or not a15: continue
            hit = None
            for b5 in a5:
                bot, top = b5[1], b5[2]
                if dr > 0 and not (bot <= l[j] <= top and c[j] > top): continue
                if dr < 0 and not (bot <= h[j] <= top and c[j] < bot): continue
                for b15 in a15:
                    if (dr > 0 and bot >= b15[1] and top <= b15[2] + buf) or (dr < 0 and top <= b15[2] and bot >= b15[1] - buf):
                        hit = b5; break
                if hit: break
            if hit is None: continue
            if lab[j + 1] >= hard: return
            e = o[j + 1]; stp = hit[1] - sbuf if dr > 0 else hit[2] + sbuf; risk = (e - stp) * dr
            if risk <= 0: return
            ie = at(mk, i0, i1, hard - 1) or i1
            x, _ = walk(mk, B[j + 1][0], ie, dr, stp, e + dr * rr * risk)
            return [(dr, e, x, 2, int(lab[j + 1]))]


@strat('D01L', 'ICT FVG (reproduction)', 'prashanthaitha24/nq-strategy-b-bot Strategy B as published, 15-min FVG look-ahead kept', '2026-05-20')
def d01l(): return intraday(strategy_b, look=True)


@strat('D01', 'ICT FVG', 'prashanthaitha24/nq-strategy-b-bot Strategy B (5-min IFVG inside 15-min FVG, long), look-ahead removed', '2026-05-20')
def d01(): return intraday(strategy_b)


@strat('D02', 'ICT FVG', 'Strategy B both directions, look-ahead removed', '2026-05-20')
def d02(): return intraday(strategy_b, both=True)


def prior_close(k, D):
    if k < 1 or D.seg.iat[k - 1] != D.seg.iat[k] or D.iid.iat[k - 1] != D.iid.iat[k]: return None
    return D.C.iat[k - 1]


@strat('D03', 'late-day trend', 'quantrocket-codeload/trend-day (Chan): +-2% from prior close at 14:00 -> hold 14:01 to close', '2013-05-01', thr=0.02)
def d03(thr=0.02):
    def f(k, mk, i0, i1, D):
        pc = prior_close(k, D); b = at(mk, i0, i1, 840)
        if pc is None or b is None: return
        r = mk.o[b] / pc - 1
        if abs(r) > thr: return [(int(np.sign(r)), mk.c[b], mk.c[i1], 2, 841)]
    return intraday(f)


@strat('D04', 'last-half-hour momentum', 'quantrocket-codeload/first-last (Gao et al.): close->10:00 and 15:00->15:30 same sign -> 15:30 to close', '2018-01-01')
def d04():
    def f(k, mk, i0, i1, D):
        pc = prior_close(k, D); a, b, e = at(mk, i0, i1, 600), at(mk, i0, i1, 900), at(mk, i0, i1, 930)
        if pc is None or a is None or b is None or e is None: return
        r1 = mk.o[a] / pc - 1; r2 = mk.o[e] / mk.o[b] - 1
        if r1 * r2 > 0: return [(int(np.sign(r1)), mk.o[e], mk.c[i1], 2, 930)]
    return intraday(f)


PAT = {(1, 1): ('RBR', 1), (-1, -1): ('DBD', -1), (1, -1): ('RBD', -1), (-1, 1): ('DBR', 1)}


def zones_ending(mk, e, lo, prio, allowed, imp):
    out = []
    if imp[e] == 0: return out
    for bc in range(1, 4):
        li = e - bc - 1
        if li < lo: continue
        bs = slice(li + 1, e)
        if not (np.abs(mk.c[bs] - mk.o[bs]) < 10.0).all() or imp[li] == 0: continue
        pat, zt = PAT[(imp[li], imp[e])]
        if allowed and pat not in allowed: continue
        bh = np.maximum(mk.o[bs], mk.c[bs]); bl = np.minimum(mk.o[bs], mk.c[bs])
        prox, dist = (bl.min(), mk.h[bs].max()) if zt < 0 else (bh.max(), mk.l[bs].min())
        th = abs(dist - prox)
        if not 4.0 <= th <= 20.0: continue
        out.append(dict(t=zt, p=prio, prox=prox, dist=dist, th=th, f=e, trd=(mk.c[e] < prox) if zt < 0 else (mk.c[e] > prox),
                        ins=False, inv=False, done=False))
    return out


def impulses(mk, a, b):
    o, h, l, c = mk.o[a:b], mk.h[a:b], mk.l[a:b], mk.c[a:b]; body = np.abs(c - o); rng = h - l
    ok = (rng > 0) & (body >= 10.0) & (body >= 0.6 * np.where(rng > 0, rng, 1)) & (c != o)
    return np.where(ok, np.sign(c - o), 0).astype(int)


def supply_demand(k, mk, i0, i1, D, allowed=None):
    if k < 1 or D.seg.iat[k - 1] != D.seg.iat[k] or D.iid.iat[k - 1] != D.iid.iat[k]: return
    p0, p1 = int(D.i0.iat[k - 1]), int(D.i1.iat[k - 1])
    if mk.iid[p0] != mk.iid[i0]: return
    imp = np.zeros(len(mk.c), int); imp_rng = slice(p0, i1 + 1); imp[imp_rng] = impulses(mk, p0, i1 + 1)
    zs = []
    for lo, hi, pr in ((p0, p1, 1), (p1 + 1, i0 - 1, 2)):
        for e in range(lo + 2, hi + 1):
            if imp[e]: zs += zones_ending(mk, e, lo, pr, allowed, imp)
    keep = []
    for z in zs:
        later = slice(z['f'] + 1, i0)
        if (mk.c[later] > z['dist']).any() if z['t'] < 0 else (mk.c[later] < z['dist']).any(): continue
        if not z['trd'] and ((mk.h[later] < z['prox']).any() if z['t'] < 0 else (mk.l[later] > z['prox']).any()): z['trd'] = True
        keep.append(z)
    act = []
    for z in sorted(keep, key=lambda z: (z['p'], z['th'], z['f'])):
        if any(abs(z['prox'] - s['prox']) <= 4.0 for s in act): continue
        act.append(z)
    end = at(mk, i0, i1, 719) or i1
    for z in act:
        if mk.h[i0] >= min(z['prox'], z['dist']) and mk.l[i0] <= max(z['prox'], z['dist']): z['ins'] = True; z['trd'] = False

    def add_intraday(i):
        if i - i0 < 2: return
        for z in zones_ending(mk, i, i0, 3, allowed, imp):
            if any(not x['inv'] and abs(z['prox'] - x['prox']) <= 4.0 and (x['p'], x['th']) <= (z['p'], z['th']) for x in act): continue
            act.append(z)

    def touches(z, i):
        if z['t'] < 0:
            if mk.o[i] > z['dist']: z['inv'] = True; return False
            return mk.o[i] < z['prox'] and mk.h[i] >= z['prox']
        if mk.o[i] < z['dist']: z['inv'] = True; return False
        return mk.o[i] > z['prox'] and mk.l[i] <= z['prox']

    sig = []; skip = i0
    for i in range(i0, end + 1):
        for z in act:
            if not z['inv'] and ((z['t'] < 0 and mk.c[i] > z['dist']) or (z['t'] > 0 and mk.c[i] < z['dist'])): z['inv'] = True
        for z in act:
            if z['inv'] or z['done']: continue
            left = mk.h[i] < z['prox'] if z['t'] < 0 else mk.l[i] > z['prox']
            if not z['trd'] and left: z['trd'] = True; z['ins'] = False
            elif z['ins'] and left: z['ins'] = False
        if i < skip or mk.m[i] < 575 or i >= end or mk.m[i + 1] + 1 >= 705:
            add_intraday(i); continue
        cand = [z for z in act if not z['inv'] and not z['done'] and z['trd'] and not z['ins'] and touches(z, i)]
        if not cand: add_intraday(i); continue
        z = sorted(cand, key=lambda z: (z['p'], z['th'], z['f']))[0]; z['ins'] = True; skip = i + 2; dr = z['t']
        if not ((dr < 0 and mk.c[i + 1] < mk.l[i]) or (dr > 0 and mk.c[i + 1] > mk.h[i])): add_intraday(i); continue
        e = mk.c[i + 1]; stp = z['dist'] - 4.0 if dr > 0 else z['dist'] + 4.0; dist = abs(e - stp)
        if not 8.0 <= dist <= 40.0: add_intraday(i); continue
        z['done'] = True; sig.append((i + 1, dr, e, stp, e + dr * 2.0 * dist)); add_intraday(i)
    out = []; busy = -1
    for ie, dr, e, stp, tgt in sig:
        if ie <= busy or ie + 1 > end: continue
        x, ix = walk(mk, ie + 1, min(ie + 15, end), dr, stp, tgt)
        out.append((dr, e, x, 2, int(mk.m[ie]))); busy = ix
    return out


@strat('D05', 'supply/demand zones', 'danielnik06/nq-futures-backtesting S&D model (1-min RBR/DBD/RBD/DBR zones, 2R, NY AM)', '2026-05-26')
def d05(): return intraday(supply_demand)


@strat('D06', 'supply/demand zones', 'danielnik06 S&D Experiment 2 (RBR/DBR demand zones only)', '2026-05-26')
def d06(): return intraday(supply_demand, allowed=('RBR', 'DBR'))
