import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_test as T
from src2_test import strat, intraday, at, walk
from src2_strats_a import cache
from src2_strats_b import bars, full_bars

SLOTS = list(range(570, 960, 30))


def slot_returns():
    def mk():
        R = np.full((len(T.P.sess), len(SLOTS)), np.nan)
        for k, (d, m) in enumerate(T.P.sess):
            i0, i1 = m.days[d]
            for s, a in enumerate(SLOTS):
                b0 = at(m, i0, i1, a); b1 = at(m, i0, i1, a + 29)
                if b0 is not None and b1 is not None: R[k, s] = m.c[b1] / m.o[b0] - 1
        return R
    return cache('slotR', mk)


def same_seg(k, D): return k >= 1 and D.seg.iat[k - 1] == D.seg.iat[k] and D.iid.iat[k - 1] == D.iid.iat[k]


def periodicity(look):
    R = slot_returns()
    def f(k, mk, i0, i1, D):
        if k < look or D.seg.iat[k - look] != D.seg.iat[k]: return
        out = []
        for s, a in enumerate(SLOTS):
            h = R[k - look:k, s]
            if np.isnan(h).any(): continue
            dr = int(np.sign(h.sum()))
            b0 = at(mk, i0, i1, a); b1 = at(mk, i0, i1, a + 29)
            if dr and b0 is not None and b1 is not None: out.append((dr, mk.o[b0], mk.c[b1], 2, a))
        return out
    return intraday(f)


@strat('E01', 'intraday periodicity', 'Heston-Korajczyk-Sadka 2010 via TradingView "Heston Multi-Slot Periodicity": trade each 30-min slot in the sign of the same slot yesterday', '2010-08-01')
def e01(): return periodicity(1)


@strat('E02', 'intraday periodicity', 'Heston-Korajczyk-Sadka 2010: each 30-min slot in the sign of the same slot summed over the last 20 sessions', '2010-08-01')
def e02(): return periodicity(20)


def phase_osc(mk):
    def mko():
        fb = full_bars(mk, 10); c, h, l = fb['c'], fb['h'], fb['l']
        pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
        atr = pd.Series(tr).ewm(alpha=1 / 14, adjust=False).mean().values
        ema = pd.Series(c).ewm(span=21, adjust=False).mean().values
        raw = (c - ema) / (3 * np.where(atr > 0, atr, np.nan)) * 100
        return pd.Series(raw).ewm(span=3, adjust=False).mean().values
    return cache(('phase', mk.name), mko)


@strat('E03', 'oscillator breakout', 'DefinedEdge "Phase Oscillator Momentum Strategy" (TradingView): 10-min (close-EMA21)/(3 ATR14) x100, EMA3; cross +-100 09:30-11:00', '2025-06-01')
def e03():
    def f(k, mk, i0, i1, D):
        fb = full_bars(mk, 10); osc = phase_osc(mk)
        a = np.searchsorted(fb['st'], i0); b = np.searchsorted(fb['st'], i1, side='right') - 1
        if b - a < 5 or a < 30: return
        pos = 0; e = 0.0; out = []; em = 0
        for q in range(a, b):
            x0, x1 = osc[q - 1], osc[q]; end = fb['m'][q] + 10
            up = x0 <= 100 < x1; dn = x0 >= -100 > x1; win = end <= 660
            new = pos
            if pos == 1 and dn: new = -1 if win else 0
            elif pos == -1 and up: new = 1 if win else 0
            elif pos == 0 and win: new = 1 if up else (-1 if dn else 0)
            if new != pos:
                px = mk.o[fb['st'][q + 1]]
                if pos: out.append((pos, e, px, 2, em))
                if new: e = px; em = int(fb['m'][q + 1])
                pos = new
        if pos: out.append((pos, e, mk.c[i1], 2, em))
        return out
    return intraday(f)


@strat('E04', '9am candle fade', 'TradingView "NQ 9 AM Hour Bias": A-grade 09:00-10:00 candle unbroken by 11:00 -> fade it 11:00 to close', '2026-01-01')
def e04():
    def f(k, mk, i0, i1, D):
        j = i0 - 30
        if j < 0 or mk.m[j] != 540 or mk.iid[j] != mk.iid[i0] or mk.date[j] != mk.date[i0]: return
        e10, e11 = at(mk, i0, i1, 600), at(mk, i0, i1, 660)
        if e10 is None or e11 is None: return
        H, Lo, O, C = mk.h[j:e10].max(), mk.l[j:e10].min(), mk.o[j], mk.c[e10 - 1]
        rg = H - Lo
        if rg <= 0 or abs(C - O) <= 0.5 * rg: return
        p930 = (mk.o[i0] - Lo) / rg
        if C > O and C >= H - 0.25 * rg and p930 <= 0.75: dr = 1
        elif C < O and C <= Lo + 0.25 * rg and p930 >= 0.25: dr = -1
        else: return
        if mk.h[e10:e11].max() > H or mk.l[e10:e11].min() < Lo: return
        return [(-dr, mk.o[e11], mk.c[i1], 2, 660)]
    return intraday(f)


@strat('E05', 'pullback after new high', 'Unger Academy NQ 5-min long: bar high above the prior 20 bars, next bar closes lower -> buy that close, out at the close', '2025-01-01', n=20)
def e05(n=20):
    def f(k, mk, i0, i1, D):
        B = bars(mk, i0, i1, 5); h = np.array([b[3] for b in B]); c = np.array([b[5] for b in B])
        for j in range(n, len(B) - 2):
            if h[j] > h[j - n:j].max() and c[j + 1] < c[j]:
                return [(1, c[j + 1], mk.c[i1], 2, int(mk.m[B[j + 1][1]]))]
    return intraday(f)


def atr_bars(fb, n=14):
    c, h, l = fb['c'], fb['h'], fb['l']; pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


@strat('E06', 'volatility breakout', 'BreakoutOS "Nasdaq 10-hour" (Nesnidal): buy stop at yesterday 15:00 close + 3 x ATR14(60-min), 09:00-15:00, intraday-flat', '2025-01-01', k_atr=3.0)
def e06(k_atr=3.0):
    def f(k, mk, i0, i1, D):
        if not same_seg(k, D): return
        fb = full_bars(mk, 60); atr = cache(('atr60', mk.name), lambda: atr_bars(fb))
        p0, p1 = int(D.i0.iat[k - 1]), int(D.i1.iat[k - 1])
        q = np.searchsorted(fb['st'], at(mk, p0, p1, 840) or -1)
        if q >= len(fb['st']) or fb['m'][q] != 840 or fb['iid'][q] != mk.iid[i0]: return
        lvl = mk.c[fb['en'][q]] + k_atr * atr[q]
        j0 = i0 - 30 if i0 >= 30 and mk.m[i0 - 30] == 540 and mk.iid[i0 - 30] == mk.iid[i0] else i0
        e15 = at(mk, i0, i1, 899) or i1
        for i in range(j0, e15 + 1):
            if mk.h[i] >= lvl:
                e = max(lvl, mk.o[i]); return [(1, e, mk.c[i1], 2, int(mk.m[i]))]
    return intraday(f)


@strat('E07', 'range expansion + failure', 'Pruitt-Hill Super Combo (2002) as published: narrow-day open breakout, failed-breakout reversals, breakeven, late trail', '2002-01-01')
def e07():
    D_ = T.P.daily; O_, H_, L_, C_ = D_.O.values, D_.H.values, D_.L.values, D_.C.values
    def f(k, mk, i0, i1, D):
        if k < 11 or D.seg.iat[k - 11] != D.seg.iat[k] or D.iid.iat[k - 1] != D.iid.iat[k]: return
        oc = np.abs(O_[k - 10:k] - C_[k - 10:k]); ar = (H_[k - 10:k] - L_[k - 10:k]).mean()
        if not abs(O_[k - 1] - C_[k - 1]) < 0.85 * oc.mean(): return
        easy_buy = C_[k - 1] <= C_[k - 2]
        bs = O_[k] + (0.3 if easy_buy else 0.6) * ar; ss = O_[k] - (0.6 if easy_buy else 0.3) * ar
        B = bars(mk, i0, i1, 5); out = []; pos = 0; e = stp = 0.0; held = 0; used = {1: False, -1: False}
        hi_so = -np.inf; lo_so = np.inf; fb_s = fb_b = None
        for j, (s, en, o, h, l, c) in enumerate(B):
            t = mk.m[s]
            if pos:
                held += 1; fill = None
                if (pos > 0 and l <= stp) or (pos < 0 and h >= stp):
                    fill = min(stp, o) if pos > 0 else max(stp, o)
                    out.append((pos, e, fill, 2, 0)); old = pos; pos = 0
                    if held >= 4 and t < 780 and not used[-old]:
                        pos = -old; e = fill; used[pos] = True; held = 0; stp = e - pos * 0.15 * ar
                if pos:
                    if (c - e) * pos >= 0.5 * ar: stp = max(stp, e) if pos > 0 else min(stp, e)
                    if t >= 930 and j >= 3:
                        tr = min(x[4] for x in B[j - 2:j + 1]) if pos > 0 else max(x[3] for x in B[j - 2:j + 1])
                        stp = max(stp, tr) if pos > 0 else min(stp, tr)
            hi_so = max(hi_so, h); lo_so = min(lo_so, l)
            if fb_s is None and hi_so > H_[k - 1] + 0.25 * ar: fb_s = H_[k - 1] - 0.25 * ar
            if fb_b is None and lo_so < L_[k - 1] - 0.25 * ar: fb_b = L_[k - 1] + 0.25 * ar
            if pos == 0 and t >= 600 and j + 1 < len(B):
                nb = B[j + 1]; cands = []
                for dr, lv in ((1, bs), (-1, ss), (-1, fb_s), (1, fb_b)):
                    if lv is None or used[dr]: continue
                    if (dr > 0 and nb[3] >= lv) or (dr < 0 and nb[4] <= lv):
                        cands.append((abs(lv - nb[2]), dr, max(lv, nb[2]) if dr > 0 else min(lv, nb[2])))
                if cands:
                    _, dr, px = min(cands); pos = dr; e = px; used[dr] = True; held = 0
                    stp = e - dr * max(0.25 * ar, 3.0)
                    if (dr > 0 and nb[4] <= stp) or (dr < 0 and nb[3] >= stp):
                        out.append((dr, e, stp, 2, 0)); pos = 0
        if pos: out.append((pos, e, mk.c[i1], 2, 0))
        return out
    return intraday(f)


@strat('E08', 'inside bar breakdown', 'Unger Academy ES 60-min inside-bar short (refined): inside bar ending 10:00-14:00 ET -> sell stop at the low of the bar before it, stop 0.5%', '2024-01-01', stop_pct=0.005)
def e08(stop_pct=0.005):
    def f(k, mk, i0, i1, D):
        fb = full_bars(mk, 60); a = np.searchsorted(fb['st'], i0); b = np.searchsorted(fb['en'], i1)
        for q in range(max(a, 2), min(b, len(fb['st']) - 1)):
            end = fb['m'][q] + 60
            if end > 840: return
            if end < 600 or fb['iid'][q] != fb['iid'][q - 1]: continue
            if fb['h'][q] < fb['h'][q - 1] and fb['l'][q] > fb['l'][q - 1]:
                lvl = fb['l'][q - 1] - mk.shift[fb['st'][q - 1]]
                for i in range(fb['en'][q] + 1, i1 + 1):
                    if mk.l[i] <= lvl:
                        e = min(lvl, mk.o[i]); x, _ = walk(mk, i, i1, -1, e * (1 + stop_pct), None)
                        return [(-1, e, x, 2, int(mk.m[i]))]
                return
    return intraday(f)


def release_days(kind):
    import json
    if kind == 'nfp':
        d = json.load(open(os.path.join(HERE, 'event_calendars', 'bls_release_dates_20261020.json')))['empsit']
        return set(pd.to_datetime(d))
    out = set(); dates = set(T.P.dates)
    for y in range(2010, 2027):
        for m in range(1, 13):
            for dd in pd.date_range(pd.Timestamp(y, m, 1), periods=7):
                if dd in dates: out.add(dd); break
    return out


def pre_release(kind, exit_min):
    from src2_strats_a import fomc_k
    rel = release_days(kind); fk = set(fomc_k())
    def f(k, mk, i0, i1, D):
        d = T.P.dates[k]
        if d not in rel or k in fk or not same_seg(k, D): return
        t0 = np.datetime64(T.P.dates[k - 1] + pd.Timedelta(hours=18), 'm'); t1 = np.datetime64(d + pd.Timedelta(minutes=exit_min), 'm')
        j = np.searchsorted(mk.t, t0); x = np.searchsorted(mk.t, t1)
        if j >= x or x >= len(mk.t) or mk.t[x] != t1 or mk.iid[j] != mk.iid[x] or (mk.t[j] - t0) > np.timedelta64(3, 'D'): return
        return [(1, mk.o[j], mk.o[x], 2, 0)]
    return intraday(f)


@strat('E09', 'macro pre-announcement', 'Hu-Pan-Wang-Zhu 2022 JFE: long from the Globex reopen (18:00) before payrolls to 08:25, FOMC days excluded', '2018-06-01')
def e09(): return pre_release('nfp', 505)


@strat('E10', 'macro pre-announcement', 'Hu-Pan-Wang-Zhu 2022 JFE: long from the Globex reopen before ISM (first business day) to 09:55, FOMC days excluded', '2018-06-01')
def e10(): return pre_release('ism', 595)


@strat('E11', 'open band breakout', 'Kaufman (via Concretum QuanTips 2026): open +- 0.5 x ATR14 daily, checks :00/:15/:30/:45, stop back at the open, flat at close', '2026-02-01', kk=0.5)
def e11(kk=0.5):
    D_ = T.P.daily; H_, L_, C_ = D_.H.values, D_.L.values, D_.C.values
    pc = np.r_[np.nan, C_[:-1]]; tr = np.maximum(H_ - L_, np.maximum(abs(H_ - pc), abs(L_ - pc)))
    atr = pd.Series(tr).rolling(14).mean().values
    def f(k, mk, i0, i1, D):
        if k < 15 or D.seg.iat[k - 15] != D.seg.iat[k] or np.isnan(atr[k - 1]): return
        O = mk.o[i0]; up, dn = O + kk * atr[k - 1], O - kk * atr[k - 1]; out = []; pos = 0; e = 0.0; em = 0
        for i in range(i0 + 14, i1, 15):
            c = mk.c[i]
            if pos == 1 and c <= O or pos == -1 and c >= O: out.append((pos, e, mk.o[i + 1], 2, em)); pos = 0
            if pos == 0:
                nw = 1 if c > up else (-1 if c < dn else 0)
                if nw: pos = nw; e = mk.o[i + 1]; em = int(mk.m[i + 1])
        if pos: out.append((pos, e, mk.c[i1], 2, em))
        return out
    return intraday(f)


@strat('E12', 'overnight reversal', 'Della Corte-Kosowski-Wang (CO-OC): fade the sign of the prior close -> 09:30 move, hold to the close', '2016-02-01')
def e12():
    def f(k, mk, i0, i1, D):
        if not same_seg(k, D): return
        g = mk.o[i0] - D.C.iat[k - 1]
        if g: return [(-int(np.sign(g)), mk.o[i0], mk.c[i1], 2, 570)]
    return intraday(f)
