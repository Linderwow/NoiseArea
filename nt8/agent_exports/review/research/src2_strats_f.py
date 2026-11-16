import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
import src2_test as T
from src2_test import strat, intraday, at, walk, TICK
from src2_strats_a import cache
from src2_strats_b import full_bars


def last_minute(mk, i0, i1):
    j = at(mk, i0, i1, 954)
    return j if j is not None else i1


def session_bars(fb, i0, i1):
    return int(np.searchsorted(fb['st'], i0)), int(np.searchsorted(fb['st'], i1, side='right')) - 1


def bollinger(mk):
    def mkb():
        fb = full_bars(mk, 5); s = pd.Series(fb['c'])
        mid = s.rolling(20).mean().values; sd = s.rolling(20).std(ddof=0).values
        return mid, mid + 2 * sd, mid - 2 * sd
    return cache(('bb5', mk.name), mkb)


def linreg(x, n):
    out = np.full(len(x), np.nan)
    if len(x) < n: return out
    W = sliding_window_view(x, n); i = np.arange(n, dtype=float)
    sy = W.sum(1); sxy = W @ i; sx = i.sum(); sxx = (i * i).sum()
    slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    out[n - 1:] = sy / n + slope * ((n - 1) - sx / n)
    return out


def squeeze(mk):
    def mks():
        fb = full_bars(mk, 15); c, h, l = fb['c'], fb['h'], fb['l']; s = pd.Series(c)
        basis = s.rolling(20).mean().values; dev = 2.0 * s.rolling(20).std(ddof=0).values
        pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
        rng = pd.Series(tr).rolling(20).mean().values
        with np.errstate(invalid='ignore'):
            on = (basis - dev > basis - 1.5 * rng) & (basis + dev < basis + 1.5 * rng)
        hh = pd.Series(h).rolling(20).max().values; ll = pd.Series(l).rolling(20).min().values
        val = linreg(c - ((hh + ll) / 2 + basis) / 2, 20)
        atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
        run = np.zeros(len(on), int)
        for q in range(1, len(on)): run[q] = run[q - 1] + 1 if on[q] else 0
        return on, run, val, atr
    return cache(('sqz', mk.name), mks)


@strat('F01', 'band breakout', 'TradingCheck Bollinger 20/2 5-min breakout with the move, stop middle band, 2R (YouTube fBmfSre3nwE)', '2026-11-13')
def f01():
    def f(k, mk, i0, i1, D):
        fb = full_bars(mk, 5); mid, up, lo = bollinger(mk); a, b = session_bars(fb, i0, i1); il = last_minute(mk, i0, i1)
        out = []; busy = -1
        for q in range(a, b):
            m0 = fb['m'][q]
            if m0 < 570 or m0 > 945 or fb['en'][q] < busy or np.isnan(up[q]): continue
            c = fb['c'][q]; dr = 1 if c > up[q] else (-1 if c < lo[q] else 0)
            j = fb['st'][q + 1]
            if not dr or j > il or fb['iid'][q + 1] != fb['iid'][q]: continue
            e = mk.o[j]; stp = mid[q] - mk.shift[fb['en'][q]]; dist = (e - stp) * dr
            if dist <= 0: continue
            x, ix = walk(mk, j, il, dr, stp, e + dr * 2 * dist)
            out.append((dr, e, x, 2, int(mk.m[j]))); busy = ix
        return out
    return intraday(f)


def stop_walk(mk, j, end, dr, stp):
    for i in range(j, end + 1):
        if dr > 0 and mk.l[i] <= stp: return (stp if (i == j or mk.o[i] > stp) else mk.o[i]), i
        if dr < 0 and mk.h[i] >= stp: return (stp if (i == j or mk.o[i] < stp) else mk.o[i]), i
    return None, end


def squeeze_rule(rule):
    def f(k, mk, i0, i1, D):
        fb = full_bars(mk, 15); on, run, val, atr = squeeze(mk); a, b = session_bars(fb, i0, i1); il = last_minute(mk, i0, i1)
        lo_q = max(a - 1, 1); out = []; busy = -1
        for q in range(lo_q, b):
            j = fb['st'][q + 1]; mj = int(mk.m[j]) if j <= i1 else -1
            if j < i0 or mj < 570 or mj > 900 or fb['en'][q] < busy or np.isnan(val[q]) or np.isnan(val[q - 1]): continue
            if fb['iid'][q + 1] != fb['iid'][q]: continue
            if rule == 1:
                dr = int(np.sign(val[q])) if (not on[q] and run[q - 1] >= 6) else 0
            else:
                dr = 1 if val[q - 1] <= 0 < val[q] else (-1 if val[q - 1] >= 0 > val[q] else 0)
            if not dr: continue
            e = mk.o[j]; stp = e - dr * 2 * atr[q]; xo = None
            for r in range(q + 1, b + 1):
                if (dr > 0 and val[r] <= val[r - 1]) or (dr < 0 and val[r] >= val[r - 1]):
                    if r + 1 <= b and fb['st'][r + 1] <= il: xo = fb['st'][r + 1]
                    break
            end = (xo - 1) if xo is not None else il
            x, ix = stop_walk(mk, j, end, dr, stp)
            if x is None: x, ix = (mk.o[xo], xo) if xo is not None else (mk.c[il], il)
            out.append((dr, e, x, 2, mj)); busy = ix
        return out
    return f


@strat('F02', 'squeeze breakout', 'Backtest Verdict squeeze momentum rule 1: >= 6 squeeze bars, first bar out, 15-min (YouTube obxfqsc03iQ)', '2026-11-05')
def f02(): return intraday(squeeze_rule(1))


@strat('F03', 'squeeze momentum', 'Backtest Verdict squeeze momentum rule 2: histogram zero cross, 15-min (YouTube obxfqsc03iQ)', '2026-11-05')
def f03(): return intraday(squeeze_rule(2))


@strat('F04', 'gap and go', 'Sharingan Trader ES gap-and-go: gap >= 0.10 x 14-day range, 09:30 5-min candle with the gap, 2R (YouTube pwQrXOUv9aI)', '2026-11-10')
def f04():
    def f(k, mk, i0, i1, D):
        if k < 15 or D.seg.iat[k - 15] != D.seg.iat[k] or D.iid.iat[k - 1] != D.iid.iat[k]: return
        adr = float((D.H.values[k - 14:k] - D.L.values[k - 14:k]).mean()); gap = D.O.iat[k] - D.C.iat[k - 1]
        if abs(gap) < 0.10 * adr: return
        dr = 1 if gap > 0 else -1
        b0 = at(mk, i0, i1, 570); b5 = at(mk, i0, i1, 575)
        if b0 is None or b5 is None or b5 - b0 != 5: return
        cO, cC = mk.o[b0], mk.c[b5 - 1]; cH, cL = mk.h[b0:b5].max(), mk.l[b0:b5].min()
        if (cC - cO) * dr <= 0: return
        e = mk.o[b5]; stp = cL - TICK if dr > 0 else cH + TICK; R = (e - stp) * dr
        if R <= 0: return
        x, _ = walk(mk, b5, last_minute(mk, i0, i1), dr, stp, e + dr * 2 * R)
        return [(dr, e, x, 2, 575)]
    return intraday(f)
