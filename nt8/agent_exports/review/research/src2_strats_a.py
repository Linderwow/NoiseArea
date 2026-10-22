r"""SOURCES2 batch A strategies (SOURCES2_PLAN.txt, 'BATCH A'): papers, quant sites and books, open code and records.
Registered into src2_test.STRATS on import. Each returns (gross, sides) daily series via the src2_test runners.
"""
import os, sys, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, '..'))
import numpy as np, pandas as pd
import src2_test as T
import src2_lib as L
from src2_test import strat, intraday, daily_hold, overnight, walk, at, TICK

FOMC = sorted(pd.Timestamp(s.strip()) for s in open(os.path.join(HERE, '..', '..', '..', 'bin', 'Custom', 'Strategies', 'fomc_dates.txt'))
              if s.strip() and not s.startswith('#'))
_C = {}


def cache(key, fn):
    k = (key, id(T.P))
    if k not in _C: _C[k] = fn()
    return _C[k]


# ------------------------------------------------------------------------------------------------ shared series
def J():
    """Joined adjusted daily O/H/L/C for indicators (MNQ shifted so its first open meets NQ's last close)."""
    def mk():
        D = T.P.daily; seg = D.seg.values; sh = np.zeros(len(D))
        if (seg == 1).any() and (seg == 0).any():
            j = int(np.argmax(seg == 1)); sh[j:] = D.aC.values[j - 1] - D.aO.values[j]
        return dict(o=D.aO.values + sh, h=D.aH.values + sh, l=D.aL.values + sh, c=D.aC.values + sh, seg=seg, px=D.px.values,
                    dates=T.P.dates)
    return cache('J', mk)


def sma(x, n): return pd.Series(x).rolling(n).mean().values
def sd(x, n): return pd.Series(x).rolling(n).std(ddof=0).values


def rsi(x, n):
    d = np.diff(x, prepend=x[0]); up = pd.Series(np.clip(d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).values


def atr(n):
    j = J(); pc = np.r_[np.nan, j['c'][:-1]]
    tr = np.nanmax(np.c_[j['h'] - j['l'], np.abs(j['h'] - pc), np.abs(j['l'] - pc)], axis=1)
    return pd.Series(tr).rolling(n).mean().values


def same_seg(k, back=1):
    D = T.P.daily; return k - back >= 0 and D.seg.iat[k - back] == D.seg.iat[k]


def same_iid(k, back=1):
    D = T.P.daily; return k - back >= 0 and D.iid.iat[k - back] == D.iid.iat[k]


def idx_at(mk, day, minute):
    """Index of the 1-minute bar starting at `minute` (minutes after midnight) on calendar date `day`, or None."""
    t = np.datetime64(pd.Timestamp(day) + pd.Timedelta(minutes=int(minute)), 'm'); j = int(np.searchsorted(mk.t, t))
    return j if j < len(mk.t) and mk.t[j] == t else None


def series_on(df, col):
    """A daily external series aligned to the sessions: the value of the latest date STRICTLY BEFORE each session date (so it was
    published before that session's open - no look-ahead)."""
    idx = T.P.dates; v = df[col].sort_index()
    pos = np.searchsorted(v.index.values, idx.values, side='left') - 1
    out = np.full(len(idx), np.nan); ok = pos >= 0; out[ok] = v.values[pos[ok]]
    return out


def gexdix():
    return cache('gex', lambda: pd.read_csv(os.path.join(HERE, 'gex_dix.csv'), index_col=0, parse_dates=True))


def vix():
    def mk():
        v = pd.read_csv(os.path.join(HERE, 'vix_history_cboe.csv')); v.index = pd.to_datetime(v.DATE, format='%m/%d/%Y'); return v
    return cache('vix', mk)


def hold_from_days(days_mask):
    """Hold over every session k with days_mask[k] True: pos[k-1] = 1."""
    pos = np.zeros(len(T.P.daily)); m = np.asarray(days_mask, bool); pos[:-1] = m[1:]; return daily_hold(pos)


def holidays():
    """US stock-market holidays: CME full closures plus the cash-market holidays on which CME closes at 13:00."""
    import daily_review as dr
    yrs = range(T.P.dates[0].year, T.P.dates[-1].year + 2)
    return cache('hol', lambda: {pd.Timestamp(d) for y in yrs for d in dr.full_closures(y)} |
                 {pd.Timestamp(d) for y in yrs for d, m in dr.early_closes(y).items() if m == 780})


def nfp_dates():
    out = []
    for y in range(2010, 2027):
        for m in range(1, 13):
            d12 = dt.date(y, m, 12); sat = d12 + dt.timedelta(days=(5 - d12.weekday()) % 7)   # Saturday ending the week with the 12th
            rel = sat + dt.timedelta(days=6 + 14)                                             # third Friday after that week
            out.append(pd.Timestamp(rel))
    return set(out)


def third_friday(y, m):
    d = dt.date(y, m, 1); d += dt.timedelta(days=(4 - d.weekday()) % 7); return pd.Timestamp(d + dt.timedelta(days=14))


def sess_index():
    return cache('sidx', lambda: {d: k for k, d in enumerate(T.P.dates)})


def state_machine(entry, exitf, long_only=True):
    """Long-only daily state machine: enter at the close when entry(k), exit at the close when exitf(k). Returns pos array."""
    n = len(T.P.daily); pos = np.zeros(n); p = 0
    for k in range(n):
        if p == 0 and entry(k): p = 1
        elif p == 1 and exitf(k): p = 0
        pos[k] = p
    return pos


# ------------------------------------------------------------------------------------------------ W01-W16 intraday
@strat('W01', 'intraday momentum', 'Baltussen-Da-Lammers-Martens 2021 JFE', '2019-06-01', gamma=False, last=30)
def w01(gamma=False, last=30):
    g = series_on(gexdix(), 'gex') if gamma else None
    def f(k, mk, i0, i1, D):
        if not same_seg(k) or (gamma and not (g[k] < 0)): return
        end = mk.m[i1] + 1; j = at(mk, i0, i1, end - last)
        if j is None or j <= i0: return
        s = np.sign(mk.ac[j - 1] - D.aC.iat[k - 1])
        if s: return [(s, mk.o[j], mk.c[i1], 2, end - last)]
    return intraday(f)


@strat('W02', 'intraday momentum', 'Baltussen et al. 2021, negative-gamma days only', '2019-06-01', gamma=True, last=30)
def w02(gamma=True, last=30): return w01(gamma=gamma, last=last)


@strat('W03', 'intraday momentum', 'Shum et al. 2016 leveraged-ETF rebalancing', '2013-11-01', thr=0.02, minute=885)
def w03(thr=0.02, minute=885):
    def f(k, mk, i0, i1, D):
        if not same_seg(k): return
        j = at(mk, i0, i1, minute)
        if j is None: return
        r = (mk.ac[j - 1] - D.aC.iat[k - 1]) / D.px.iat[k]
        if abs(r) >= thr: return [(np.sign(r), mk.o[j], mk.c[i1], 2, minute)]
    return intraday(f)


@strat('W04', 'overnight', 'Boyarchenko-Larsen-Whelan 2023 RFS overnight drift', '2020-02-01', a=120, b=180, after_down=False)
def w04(a=120, b=180, after_down=False):
    def f(k, mk, i0, i1, D):
        if after_down and not (same_seg(k, 2) and D.aC.iat[k - 1] < D.aC.iat[k - 2]): return
        ia, ib = idx_at(mk, D.date.iat[k], a), idx_at(mk, D.date.iat[k], b - 1)
        if ia is None or ib is None or mk.iid[ia] != mk.iid[ib]: return
        return [(1, mk.o[ia], mk.c[ib], 2, a)]
    return intraday(f)


@strat('W05', 'overnight', 'Boyarchenko et al. 2023, after a down day', '2020-02-01', a=120, b=180, after_down=True)
def w05(a=120, b=180, after_down=True): return w04(a=a, b=b, after_down=after_down)


@strat('W06', 'overnight', 'overnight long close->open (Kelly & Clark 2011)', '2011-01-01')
def w06(): return overnight(np.ones(len(T.P.daily)))


SEP = {2011: {4, 6, 11}, 2012: {1, 4, 6, 9, 12}}
@strat('W08', 'event', 'Gu-Kurov-Wolfe 2018 post-FOMC relief rally', '2018-01-01', pre=5, post=55)
def w08(pre=5, post=55):
    fs = {d for d in FOMC if (d.month in SEP.get(d.year, {3, 6, 9, 12})) and d.year >= 2011 and not (d.year == 2011 and d.month < 4)}
    def f(k, mk, i0, i1, D):
        d = D.date.iat[k]
        if d not in fs: return
        st = 750 if d.year <= 2012 else 840
        a, b = at(mk, i0, i1, st - pre), at(mk, i0, i1, st + post - 1)
        if a is None or b is None: return
        return [(1, mk.o[a], mk.c[b], 2, st - pre)]
    return intraday(f)


@strat('W09', 'statistics fade', 'NQ Stats AM TBR (nqstats.com, video xlfoh4FoBP0)', '2026-07-01', z=0.25, look=20, ref=480, end=720)
def w09(z=0.25, look=20, ref=480, end=720):
    D = T.P.daily; r = pd.Series(np.r_[np.nan, np.diff(D.aC.values) / D.px.values[1:]]); r[np.r_[False, D.seg.values[1:] != D.seg.values[:-1]]] = np.nan
    sig = r.rolling(look, min_periods=look).std().shift(1).values
    def f(k, mk, i0, i1, D):
        if np.isnan(sig[k]): return
        a = idx_at(mk, D.date.iat[k], ref); e = at(mk, i0, i1, end - 1)
        if a is None or e is None or mk.iid[a] != mk.iid[e]: return
        p0 = mk.o[a]; up, dn = p0 * (1 + z * sig[k]), p0 * (1 - z * sig[k])
        for i in range(a, e + 1):
            hu, hd = mk.h[i] >= up, mk.l[i] <= dn
            if hu and hd: return
            if hu or hd:
                dr = -1 if hu else 1; ent = max(mk.o[i], up) if hu else min(mk.o[i], dn)
                if i == e: return [(dr, ent, mk.c[e], 2, mk.m[i])]
                x, _ = walk(mk, i + 1, e, dr, None, p0)
                return [(dr, ent, x, 2, mk.m[i])]
    return intraday(f)


@strat('W10', 'statistics fade', 'NQ Stats Hour Stats (video aQzDN7jXXyc)', '2026-07-01', first=9, last=15)
def w10(first=9, last=15):
    def f(k, mk, i0, i1, D):
        out = []; d = D.date.iat[k]
        for h in range(first, last + 1):
            p0, p1 = idx_at(mk, d, (h - 1) * 60), idx_at(mk, d, (h - 1) * 60 + 59)
            a, e = idx_at(mk, d, h * 60), idx_at(mk, d, h * 60 + 59)
            if None in (p0, p1, a, e) or e > i1 or mk.iid[p0] != mk.iid[e] or e - p0 != 119: continue
            H, Lo = mk.h[p0:p1 + 1].max(), mk.l[p0:p1 + 1].min(); op = mk.o[a]
            if not (Lo <= op <= H): continue
            for i in range(a, e + 1):
                hu, hd = mk.h[i] >= H + TICK, mk.l[i] <= Lo - TICK
                if hu and hd: break
                if hu or hd:
                    dr = -1 if hu else 1; ent = max(mk.o[i], H + TICK) if hu else min(mk.o[i], Lo - TICK)
                    x = mk.c[e] if i == e else walk(mk, i + 1, e, dr, None, op)[0]
                    out.append((dr, ent, x, 2, h * 60)); break
        return out
    return intraday(f)


@strat('W11', 'statistics', 'NQ Stats initial-balance breaks (video 24P7rsb9P_8)', '2026-07-01', ib=60, end=720)
def w11(ib=60, end=720):
    def f(k, mk, i0, i1, D):
        b = at(mk, i0, i1, 570 + ib); e = at(mk, i0, i1, end - 1)
        if b is None or e is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min(); mid = (H + Lo) / 2; c = mk.c[b - 1]
        th, tl = int(np.argmax(mk.h[i0:b])), int(np.argmin(mk.l[i0:b]))
        if c > mid and tl < th: dr, tg = 1, H + TICK
        elif c < mid and th < tl: dr, tg = -1, Lo - TICK
        else: return
        x, _ = walk(mk, b, e, dr, None, tg)
        return [(dr, mk.o[b], x, 2, 570 + ib)]
    return intraday(f)


def _breakout(k, mk, i0, i1, D, kl, ks, stop_mult=None):
    if not same_iid(k): return
    R = D.H.iat[k - 1] - D.L.iat[k - 1]; O = mk.o[i0]; up, dn = O + kl * R, O - ks * R
    for i in range(i0, i1 + 1):
        hu, hd = mk.h[i] >= up, mk.l[i] <= dn
        if hu and hd: return
        if hu or hd:
            dr = 1 if hu else -1; ent = max(mk.o[i], up) if hu else min(mk.o[i], dn)
            stp = tgt = None
            if stop_mult: stp, tgt = ent - dr * stop_mult * R, ent + dr * stop_mult * R
            x, _ = walk(mk, i, i1, dr, stp, tgt, check_first=False) if i < i1 else (mk.c[i1], i1)
            return [(dr, ent, x, 2, mk.m[i])]


@strat('W12', 'volatility breakout', 'Larry Williams volatility breakout (WH SelfInvest rules)', '1999-01-01', kk=0.25, sm=2.0)
def w12(kk=0.25, sm=2.0): return intraday(lambda k, mk, i0, i1, D: _breakout(k, mk, i0, i1, D, kk, kk, sm))


@strat('W13', 'volatility breakout', 'Unger Academy NQ prior-range breakout', '2023-01-01', kl=0.65, ks=1.2)
def w13(kl=0.65, ks=1.2): return intraday(lambda k, mk, i0, i1, D: _breakout(k, mk, i0, i1, D, kl, ks, None))


@strat('W14', 'pivot breakout/reversal', 'R-Breaker (Saidenberg 1994)', '1994-01-01', maxchg=3)
def w14(maxchg=3):
    def f(k, mk, i0, i1, D):
        if not same_iid(k): return
        H, Lo, C = D.H.iat[k - 1], D.L.iat[k - 1], D.C.iat[k - 1]; P_ = (H + Lo + C) / 3
        bb, ss, se, be, bs, sb = H + 2 * (P_ - Lo), P_ + (H - Lo), 2 * P_ - Lo, 2 * P_ - H, P_ - (H - Lo), Lo - 2 * (H - P_)
        pos, ent, out, chg, dh, dl, t0 = 0, 0.0, [], 0, -1e18, 1e18, None
        for i in range(i0, i1 + 1):
            o, h, l = mk.o[i], mk.h[i], mk.l[i]; new, px = pos, None
            if pos == 0 and h >= bb: new, px = 1, max(o, bb)
            elif pos == 0 and l <= sb: new, px = -1, min(o, sb)
            elif pos >= 0 and dh > ss and l <= se: new, px = -1, min(o, se)
            elif pos <= 0 and dl < bs and h >= be: new, px = 1, max(o, be)
            dh, dl = max(dh, h), min(dl, l)
            if new != pos and chg < maxchg:
                if pos != 0: out.append((pos, ent, px, 2, t0))                 # each completed leg = entry side + exit side
                pos, ent, t0 = new, px, mk.m[i]; chg += 1
        if pos != 0: out.append((pos, ent, mk.c[i1], 2, t0))
        return out or None
    return intraday(f)


@strat('W15', 'gap momentum', 'Chan 2013 opening-gap momentum', '2013-05-01', zz=0.1, look=90)
def w15(zz=0.1, look=90):
    D = T.P.daily; r = pd.Series(np.r_[np.nan, np.diff(D.aC.values) / D.px.values[1:]]); sig = r.rolling(look).std().shift(1).values
    def f(k, mk, i0, i1, D):
        if not same_iid(k) or np.isnan(sig[k]): return
        O = mk.o[i0]
        if O > D.H.iat[k - 1] * (1 + zz * sig[k]): return [(1, O, mk.c[i1], 2, 570)]
        if O < D.L.iat[k - 1] * (1 - zz * sig[k]): return [(-1, O, mk.c[i1], 2, 570)]
    return intraday(f)


def value_area(mk, a, b, frac=0.7):
    lo = mk.l[a:b + 1].min(); hi = mk.h[a:b + 1].max(); n = int(round((hi - lo) / TICK)) + 1; prof = np.zeros(n)
    for i in range(a, b + 1):
        x0, x1 = int(round((mk.l[i] - lo) / TICK)), int(round((mk.h[i] - lo) / TICK)); prof[x0:x1 + 1] += mk.v[i] / (x1 - x0 + 1)
    poc = int(np.argmax(prof)); lo_i, hi_i = poc, poc; tot = prof[poc]
    while tot < frac * prof.sum():
        up = prof[hi_i + 1] if hi_i + 1 < n else -1; dn = prof[lo_i - 1] if lo_i - 1 >= 0 else -1
        if up >= dn: hi_i += 1; tot += up
        else: lo_i -= 1; tot += dn
    return lo + lo_i * TICK, lo + poc * TICK, lo + hi_i * TICK


@strat('W16', 'ORB retrace', 'dws-data NQ ORB value-area retrace (github)', '2025-01-01', orm=15, cutoff=720, flat=955, tmult=1.0)
def w16(orm=15, cutoff=720, flat=955, tmult=1.0):
    def f(k, mk, i0, i1, D):
        b = at(mk, i0, i1, 570 + orm); c = at(mk, i0, i1, cutoff); fl = at(mk, i0, i1, flat - 1) or i1
        if b is None or c is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min(); val, poc, vah = value_area(mk, i0, b - 1); R = H - Lo
        brk = None
        for i in range(b, c):
            if mk.c[i] > H: brk = (1, i); break
            if mk.c[i] < Lo: brk = (-1, i); break
        if brk is None: return
        dr, i = brk; lvl = vah if dr > 0 else val; stp = Lo - TICK if dr > 0 else H + TICK; tgt = H + tmult * R if dr > 0 else Lo - tmult * R
        for j in range(i + 1, c):
            if (dr > 0 and mk.l[j] <= lvl - TICK) or (dr < 0 and mk.h[j] >= lvl + TICK):
                ent = min(mk.o[j], lvl) if dr > 0 else max(mk.o[j], lvl)
                x, _ = walk(mk, j, fl, dr, stp, tgt)
                return [(dr, ent, x, 2, mk.m[j])]
    return intraday(f)


@strat('W54', 'time-of-day continuation', 'Heston-Korajczyk-Sadka 2010 JF', '2010-08-01', look=40)
def w54(look=40):
    if T.FILT: return None                                                 # whole-day slot portfolio: the stage-2 filters do not apply
    D = T.P.daily; n = len(D); R = np.full((n, 13), np.nan)
    for k, (d, mk) in enumerate(T.P.sess):
        i0, i1 = mk.days[d]
        if i1 - i0 != 389: continue
        R[k] = (mk.c[i0 + 29:i1 + 1:30] - mk.o[i0:i1 + 1:30]) / D.px.iat[k]
    out = []
    for k in range(look, n):
        if np.isnan(R[k]).any() or D.seg.iat[k - look] != D.seg.iat[k]: continue
        sgn = np.sign(np.nansum(R[k - look:k], axis=0))
        sides = abs(sgn[0]) + np.abs(np.diff(sgn)).sum() + abs(sgn[-1])
        out.append((k, float((sgn * R[k]).sum()), float(sides)))
    return L.series(T.P, out)


@strat('W55', 'closing reversal', 'Bogousslavsky-Muravyev closing pressure', '2020-01-01', look=10)
def w55(look=10):
    D = T.P.daily; sig = np.zeros(len(D))
    for k, (d, mk) in enumerate(T.P.sess):
        i0, i1 = mk.days[d]
        if mk.m[i1] == 959: sig[k] = -np.sign(mk.c[i1] - mk.c[i1 - look])
    return overnight(sig)


# ------------------------------------------------------------------------------------------------ W17-W57 daily
@strat('W17', 'trend filter', 'SMA200 (Faber 2007; Gayed-Bilello 2016)', '2007-01-01', n=200)
def w17(n=200):
    j = J(); s = sma(j['c'], n); return daily_hold(np.where(j['c'] > s, 1.0, 0.0))


@strat('W18', 'time-series momentum', 'Moskowitz-Ooi-Pedersen 2012 (12-month TSMOM)', '2012-01-01', look=252)
def w18(look=252):
    j = J(); c = j['c']; d = T.P.dates; n = len(c); pos = np.zeros(n); p = 0.0
    for k in range(n):
        if k + 1 < n and d[k + 1].month != d[k].month and k >= look: p = np.sign(c[k] - c[k - look])
        pos[k] = p
    return daily_hold(pos)


@strat('W19', 'trend following', 'Carver EWMAC (Systematic Trading 2015)', '2015-01-01', cap=20.0)
def w19(cap=20.0):
    j = J(); c = pd.Series(j['c']); vol = c.diff().ewm(span=25).std().values; fc = np.zeros(len(c))
    for f_, s_, sc in ((16, 64, 4.1), (32, 128, 2.79), (64, 256, 1.91)):
        e = (c.ewm(span=f_).mean() - c.ewm(span=s_).mean()).values / vol * sc; fc += np.clip(np.nan_to_num(e), -cap, cap) / 3
    fc[:256] = 0; return daily_hold(np.clip(fc / 10, -2, 2))


@strat('W20', 'trend following', 'Aberration (Fitschen)', '2013-01-01', n=80, w=2.0)
def w20(n=80, w=2.0):
    j = J(); c = j['c']; m = sma(c, n); s = sd(c, n); pos = np.zeros(len(c)); p = 0
    for k in range(len(c)):
        if np.isnan(m[k]): continue
        if p == 0: p = 1 if c[k] > m[k] + w * s[k] else (-1 if c[k] < m[k] - w * s[k] else 0)
        elif p == 1 and c[k] < m[k]: p = -1 if c[k] < m[k] - w * s[k] else 0
        elif p == -1 and c[k] > m[k]: p = 1 if c[k] > m[k] + w * s[k] else 0
        pos[k] = p
    return daily_hold(pos)


def fomc_k():
    si = sess_index(); return sorted(si[d] for d in FOMC if d in si)


@strat('W21', 'FOMC cycle', 'Cieslak-Morse-Vissing-Jorgensen 2019 JF (even weeks)', '2015-11-01')
def w21():
    n = len(T.P.daily); fk = fomc_k(); even = np.zeros(n, bool)
    for a, f in enumerate(fk):
        nxt = fk[a + 1] if a + 1 < len(fk) else n + 1
        for k in range(f - 1, min(nxt - 1, n)):
            if k >= 0 and ((k - f + 1) // 5) % 2 == 0: even[k] = True
    return hold_from_days(even)


@strat('W22', 'seasonal', 'Halloween (Bouman-Jacobsen 2002)', '2002-01-01')
def w22():
    d = T.P.dates; return hold_from_days(np.isin(d.month, [11, 12, 1, 2, 3, 4]))


@strat('W23', 'calendar', 'pre-holiday (Ariel 1990; Quantpedia)', '1990-01-01')
def w23():
    hol = holidays(); m = []
    for dd in T.P.dates:
        nd = dd + pd.Timedelta(days=1)
        while nd.weekday() >= 5: nd += pd.Timedelta(days=1)
        m.append(nd in hol)                                                # the next weekday is a stock-market holiday
    return hold_from_days(m)


@strat('W24', 'calendar', 'option-expiration week (Stivers-Sun 2010)', '2010-03-01')
def w24():
    d = T.P.dates; tf = {(x.year, x.month): third_friday(x.year, x.month) for x in d}
    m = [(tf[(x.year, x.month)] - pd.Timedelta(days=4)) <= x <= tf[(x.year, x.month)] for x in d]
    return hold_from_days(m)


def payday_mask():
    d = T.P.dates; n = len(d); m = np.zeros(n, bool)
    for k in range(n - 1):
        if d[k].day <= 15 and (d[k + 1].day > 15 or d[k + 1].month != d[k].month): m[k + 1] = True
    return m


@strat('W25', 'calendar', 'payday anomaly (Ma-Pratt)', '2018-10-01')
def w25(): return hold_from_days(payday_mask())


@strat('W26', 'calendar', 'Quantpedia composite seasonal + SMA200', '2020-01-01', n=200)
def w26(n=200):
    j = J(); s = sma(j['c'], n); d = T.P.dates; N = len(d); m = payday_mask().copy(); si = sess_index()
    for k in range(1, N):
        if d[k].month != d[k - 1].month: m[k] = True
    for f in fomc_k(): m[f] = True
    for k in range(N):
        tf = third_friday(d[k].year, d[k].month)
        if tf - pd.Timedelta(days=4) <= d[k] <= tf - pd.Timedelta(days=1): m[k] = True
    up = np.r_[False, (j['c'] > s)[:-1]]                                  # filter known at the prior close
    return hold_from_days(m & up)


def connors(entry, exitf):
    j = J(); return daily_hold(state_machine(entry, exitf))


@strat('W27', 'mean reversion', 'Connors R3 (High Probability ETF Trading 2009)', '2009-01-01')
def w27():
    j = J(); c = j['c']; r = rsi(c, 2); s = sma(c, 200)
    return connors(lambda k: k > 3 and c[k] > s[k] and r[k - 3] < 60 and r[k - 2] < r[k - 3] and r[k - 1] < r[k - 2] and r[k] < r[k - 1] and r[k] < 10,
                   lambda k: r[k] > 70)


@strat('W28', 'mean reversion', 'Connors Multiple Days Down (2008)', '2008-01-01')
def w28():
    j = J(); c = j['c']; s200, s5 = sma(c, 200), sma(c, 5); dn = np.r_[False, np.diff(c) < 0].astype(int)
    d5 = pd.Series(dn).rolling(5).sum().values
    return connors(lambda k: c[k] > s200[k] and c[k] < s5[k] and d5[k] >= 4, lambda k: c[k] > s5[k])


@strat('W29', 'mean reversion', 'Connors TPS (2009)', '2009-01-01')
def w29():
    j = J(); c = j['c']; r = rsi(c, 2); s = sma(c, 200); n = len(c); pos = np.zeros(n); p = 0.0; last = None; steps = (0.1, 0.2, 0.3, 0.4); st = 0
    for k in range(1, n):
        if p == 0 and c[k] > s[k] and r[k] < 25 and r[k - 1] < 25: p, last, st = 0.1, c[k], 1
        elif p > 0:
            if r[k] > 70: p, st = 0.0, 0
            elif st < 4 and c[k] < last: p += steps[st]; st += 1; last = c[k]
        pos[k] = p
    return daily_hold(pos)


@strat('W30', 'mean reversion', 'Connors %b (2009)', '2009-01-01')
def w30():
    j = J(); c = j['c']; m = sma(c, 5); s = sd(c, 5); b = (c - (m - s)) / np.where(2 * s > 0, 2 * s, np.nan); s200 = sma(c, 200)
    return connors(lambda k: k > 2 and c[k] > s200[k] and b[k] < 0.2 and b[k - 1] < 0.2 and b[k - 2] < 0.2, lambda k: b[k] > 0.8)


@strat('W31', 'mean reversion', 'Connors Cumulative RSI (2008)', '2008-01-01')
def w31():
    j = J(); c = j['c']; r = rsi(c, 2); s = sma(c, 200)
    return connors(lambda k: k > 0 and c[k] > s[k] and r[k] + r[k - 1] < 35, lambda k: r[k] > 65)


@strat('W32', 'mean reversion', 'Connors VIX stretch (2008)', '2008-01-01')
def w32():
    j = J(); c = j['c']; r = rsi(c, 2); s = sma(c, 200); v = vix(); vv = v.CLOSE; vs = vv.rolling(10).mean()
    st = (vv >= 1.05 * vs).astype(int); run3 = (st.rolling(3).sum() == 3).astype(float)
    ok = series_on(pd.DataFrame({'x': run3}), 'x')
    return connors(lambda k: c[k] > s[k] and ok[k] == 1, lambda k: r[k] > 65)


@strat('W33', 'mean reversion', 'Williams %R(5) (Quantified Strategies, QQQ)', '2020-01-01', n=5, lvl=10.0)
def w33(n=5, lvl=10.0):
    j = J(); c, h, l = j['c'], j['h'], j['l']; hh = pd.Series(h).rolling(n).max().values; ll = pd.Series(l).rolling(n).min().values
    wr = (hh - c) / np.where(hh - ll > 0, hh - ll, np.nan) * -100
    return connors(lambda k: wr[k] < -100 + lvl, lambda k: k > 0 and c[k] > h[k - 1])


@strat('W34', 'pivot reclaim', 'Unger Academy ES pivot S1 reclaim', '2024-01-01', stop=0.0045, tgt=0.012, start=300)
def w34(stop=0.0045, tgt=0.012, start=300):
    def f(k, mk, i0, i1, D):
        if not same_iid(k): return
        H, Lo, C = D.H.iat[k - 1], D.L.iat[k - 1], D.C.iat[k - 1]; S1 = 2 * (H + Lo + C) / 3 - H
        a = idx_at(mk, D.date.iat[k], start)
        if a is None or mk.iid[a] != mk.iid[i1]: return
        lowseen, below = 1e18, False
        for s0 in range(a, i1 + 1, 15):
            s1 = min(s0 + 14, i1); lowseen = min(lowseen, mk.l[s0:s1 + 1].min()); below |= mk.l[s0:s1 + 1].min() < S1
            if lowseen < Lo and below and mk.c[s1] > S1 and s1 < i1:
                e = mk.o[s1 + 1]; x, _ = walk(mk, s1 + 1, i1, 1, e * (1 - stop), e * (1 + tgt))
                return [(1, e, x, 2, mk.m[s1 + 1])]
    return intraday(f)


def mtm(trades):
    """trades: list of (k0, dir, entry_adj, k1, exit_adj) -> daily marked-to-market series in adjusted prices."""
    D = T.P.daily; aC, px = D.aC.values, D.px.values; out = []
    for k0, dr, e, k1, x in trades:
        if k1 == k0: out.append((k0, dr * (x - e) / px[k0], 2)); continue
        out.append((k0, dr * (aC[k0] - e) / px[k0], 1))
        for k in range(k0 + 1, k1): out.append((k, dr * (aC[k] - aC[k - 1]) / px[k], 0))
        out.append((k1, dr * (x - aC[k1 - 1]) / px[k1], 1))
    return L.series(T.P, out)


def _filt_ok(dr, k):
    return T.keep(dr, k, 600)


@strat('W35', 'gap reversal', 'Larry Williams OOPS (1999)', '1999-01-01', stop=0.006)
def w35(stop=0.006):
    D = T.P.daily; aO, aH, aL, aC, seg = D.aO.values, D.aH.values, D.aL.values, D.aC.values, D.seg.values; n = len(D); tr = []; k = 1
    while k < n:
        if seg[k] != seg[k - 1]: k += 1; continue
        dr = 1 if aO[k] < aL[k - 1] and aH[k] >= aL[k - 1] else (-1 if aO[k] > aH[k - 1] and aL[k] <= aH[k - 1] else 0)
        if not dr or not _filt_ok(dr, k): k += 1; continue
        mk = T.P.mk_of(k); i0, i1 = mk.days[D.date.iat[k]]; lvl = aL[k - 1] if dr > 0 else aH[k - 1]
        fi = next(i for i in range(i0, i1 + 1) if (dr > 0 and mk.ah[i] >= lvl) or (dr < 0 and mk.al[i] <= lvl))
        e = max(mk.ao[fi], lvl) if dr > 0 else min(mk.ao[fi], lvl); sp = e - dr * stop * D.px.iat[k]
        x, xi = walk_adj(mk, fi, i1, dr, sp)
        if xi < i1: tr.append((k, dr, e, k, x)); k += 1; continue
        j = k + 1; done = False
        while j < n and seg[j] == seg[k]:
            if dr * (aO[j] - e) > 0: tr.append((k, dr, e, j, aO[j])); done = True; break
            if (dr > 0 and aL[j] <= sp) or (dr < 0 and aH[j] >= sp):
                tr.append((k, dr, e, j, sp if dr * (aO[j] - sp) > 0 else aO[j])); done = True; break
            j += 1
        if not done: tr.append((k, dr, e, j - 1, aC[j - 1]))
        k = (j if done else j) + 1
    return mtm(tr)


def walk_adj(mk, i0, i1, dr, stp):
    for i in range(i0, i1 + 1):
        if (dr > 0 and mk.al[i] <= stp) or (dr < 0 and mk.ah[i] >= stp): return (stp if (i == i0 or dr * (mk.ao[i] - stp) > 0) else mk.ao[i]), i
    return mk.ac[i1], i1


@strat('W36', 'failed breakout', 'Raschke 80-20 (Street Smarts 1995)', '1995-01-01', hi=0.8, lo=0.2)
def w36(hi=0.8, lo=0.2):
    def f(k, mk, i0, i1, D):
        if not same_iid(k): return
        H, Lo, O, C = D.H.iat[k - 1], D.L.iat[k - 1], D.O.iat[k - 1], D.C.iat[k - 1]; R = H - Lo
        if R <= 0: return
        if (O - Lo) / R >= hi and (C - Lo) / R <= lo: dr, lvl = 1, Lo
        elif (O - Lo) / R <= lo and (C - Lo) / R >= hi: dr, lvl = -1, H
        else: return
        armed, ext = False, None
        for i in range(i0, i1 + 1):
            if not armed:
                if (dr > 0 and mk.l[i] <= lvl - TICK) or (dr < 0 and mk.h[i] >= lvl + TICK): armed = True; ext = mk.l[i] if dr > 0 else mk.h[i]
                continue
            ext = min(ext, mk.l[i]) if dr > 0 else max(ext, mk.h[i])
            if (dr > 0 and mk.h[i] >= lvl) or (dr < 0 and mk.l[i] <= lvl):
                e = max(mk.o[i], lvl) if dr > 0 else min(mk.o[i], lvl); sp = ext - TICK if dr > 0 else ext + TICK
                x, _ = walk(mk, i, i1, dr, sp, None, check_first=False) if i < i1 else (mk.c[i1], i1)
                return [(dr, e, x, 2, mk.m[i])]
    return intraday(f)


@strat('W37', 'momentum pinball', 'Raschke Momentum Pinball (Street Smarts 1995)', '1995-01-01', lo=30, hi=70)
def w37(lo=30, hi=70):
    j = J(); roc = np.r_[0.0, np.diff(j['c'])]; lb = rsi(roc, 3)
    def f(k, mk, i0, i1, D):
        if k == 0 or not same_seg(k) or np.isnan(lb[k - 1]): return
        dr = 1 if lb[k - 1] < lo else (-1 if lb[k - 1] > hi else 0)
        b = at(mk, i0, i1, 630)
        if not dr or b is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min(); lvl, sp = (H + TICK, Lo) if dr > 0 else (Lo - TICK, H)
        for i in range(b, i1 + 1):
            if (dr > 0 and mk.h[i] >= lvl) or (dr < 0 and mk.l[i] <= lvl):
                e = max(mk.o[i], lvl) if dr > 0 else min(mk.o[i], lvl)
                x, _ = walk(mk, i, i1, dr, sp, None, check_first=False) if i < i1 else (mk.c[i1], i1)
                return [(dr, e, x, 2, mk.m[i])]
    return intraday(f)


@strat('W38', 'smash day', 'Larry Williams Smash Day (roguequant 2024 rules)', '1999-01-01', look=8)
def w38(look=8):
    D = T.P.daily; aO, aH, aL, aC, seg = D.aO.values, D.aH.values, D.aL.values, D.aC.values, D.seg.values; n = len(D)
    pos, ent, k0, tr = 0, 0.0, 0, []
    for k in range(look + 1, n):
        if seg[k] != seg[k - 1]:
            if pos: tr.append((k0, pos, ent, k - 1, aC[k - 1])); pos = 0
            continue
        sd_, su_ = aC[k - 1] < aL[k - 2] and aC[k - 1] <= aC[k - look:k].min(), aC[k - 1] > aH[k - 2] and aC[k - 1] >= aC[k - look:k].max()
        if sd_ and pos <= 0 and aH[k] >= aH[k - 1] + TICK and _filt_ok(1, k):
            e = max(aO[k], aH[k - 1] + TICK)
            if pos: tr.append((k0, pos, ent, k, e))
            pos, ent, k0 = 1, e, k
        elif su_ and pos >= 0 and aL[k] <= aL[k - 1] - TICK and _filt_ok(-1, k):
            e = min(aO[k], aL[k - 1] - TICK)
            if pos: tr.append((k0, pos, ent, k, e))
            pos, ent, k0 = -1, e, k
    if pos: tr.append((k0, pos, ent, n - 1, aC[n - 1]))
    return mtm(tr)


@strat('W39', 'sentiment', 'SqueezeMetrics DIX > 45%', '2017-01-01', thr=0.45)
def w39(thr=0.45):
    dix = series_on(gexdix(), 'dix')                                       # dix[k] = DIX published the evening before session k
    return daily_hold((dix > thr).astype(float))                           # decided at k's close with that, held over k+1


@strat('W40', 'gap drift', 'StatOasis gap > ATR next-day drift (video qKtWzLZjy38)', '2026-07-01', mult=1.0, n=14)
def w40(mult=1.0, n=14):
    j = J(); a = atr(n); o, c = j['o'], j['c']; pos = np.zeros(len(c))
    for k in range(1, len(c)):
        if not np.isnan(a[k - 1]) and o[k] - c[k - 1] > mult * a[k - 1]: pos[k] = 1
    return daily_hold(pos)


@strat('W42', 'pre-announcement', 'Hu-Pan-Wang-Zhu 2022 JFE pre-payrolls', '2019-04-01', exit_m=509)
def w42(exit_m=509):
    nfp = nfp_dates()
    def f(k, mk, i0, i1, D):
        d = D.date.iat[k]
        if d not in nfp or not same_seg(k): return
        j = idx_at(mk, d, exit_m)
        if j is None: return
        pk = D.i1.iat[k - 1]
        return [(1, mk.ac[pk], mk.ac[j], 2, 960)]
    return intraday(f)


@strat('W43', 'pre-announcement', 'Hu et al. 2022 pre-ISM (first business day, 10:00)', '2019-04-01', exit_m=599)
def w43(exit_m=599):
    d = T.P.dates; first = {d[k] for k in range(1, len(d)) if d[k].month != d[k - 1].month}
    def f(k, mk, i0, i1, D):
        if D.date.iat[k] not in first or not same_seg(k): return
        j = at(mk, i0, i1, exit_m)
        if j is None: return
        return [(1, mk.ac[D.i1.iat[k - 1]], mk.ac[j], 2, 960)]
    return intraday(f)


@strat('W44', 'announcement days', 'Savor-Wilson 2013 JFQA (payrolls + FOMC days)', '2013-04-01')
def w44():
    nfp = nfp_dates(); fs = set(FOMC); return hold_from_days([x in nfp or x in fs for x in T.P.dates])


@strat('W45', 'pre-FOMC', 'Martello-Ribeiro: FOMC-eve long only when VIX > 252-day median', '2018-06-01', look=252)
def w45(look=252):
    v = vix().CLOSE; med = v.rolling(look).median(); hi = series_on(pd.DataFrame({'x': (v > med).astype(float)}), 'x')
    sig = np.zeros(len(T.P.daily))                                         # hi[f-1] = VIX state of the day before the FOMC eve
    for f in fomc_k():
        if f >= 1 and hi[f - 1] == 1: sig[f - 1] = 1
    return overnight(sig)


def na_close_long():
    def mk():
        import regime_test as rt
        from research_engine import Market
        out = {}
        for nm, pv in (('NQ', 20.0), ('MNQ', 2.0)):
            m = Market(rt.FILES[nm], pv, nm); st = m.state()
            for x, s in zip(m.days, st):
                if s.valid: out[pd.Timestamp(x.date)] = int(s.mpos[-1] if x.M[-1] >= 960 else s.mpos[-2])
        return np.array([out.get(d, 0) == 1 for d in T.P.dates], float)
    return cache('na_long', mk)


@strat('W51', 'overnight hold', 'Concretum 2026 hybrid: NoiseArea longs held overnight', '2026-01-01')
def w51(): return overnight(na_close_long())


@strat('W56', 'volatility managed', 'Moreira-Muir 2017 volatility-managed long', '2017-01-01', n=22, cap=2.0)
def w56(n=22, cap=2.0):
    j = J(); r = pd.Series(np.r_[np.nan, np.diff(j['c'])] / j['px']); rv = r.rolling(n).std()
    med = rv.expanding(min_periods=250).median(); return daily_hold(np.nan_to_num(np.clip(med / rv, 0, cap).values))


@strat('W57', 'weekly reversal', 'Wang-Yu 2004 style weekly reversal (single instrument)', '2004-01-01')
def w57():
    j = J(); c = j['c']; d = T.P.dates; n = len(c); pos = np.zeros(n); p = 0.0; lastw = None
    for k in range(n):
        if d[k].weekday() == 2:
            if lastw is not None: p = -np.sign(c[k] - c[lastw])
            lastw = k
        pos[k] = p
    return daily_hold(pos)
