r"""SOURCES2 batch B strategies (SOURCES2_PLAN.txt, 'BATCH B'): rules extracted from the transcripts of the screened YouTube videos
(regime_work\yt_txt, read by 10 extractor agents; only the rules are kept). Registered into src2_test.STRATS on import.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_test as T
import src2_lib as L
from src2_test import strat, intraday, daily_hold, overnight, walk, at, TICK
from src2_strats_a import J, sma, sd, rsi, atr, same_seg, same_iid, idx_at, cache, state_machine, mtm, hold_from_days, _filt_ok


# ------------------------------------------------------------------------------------------------ bar helpers
def bars(mk, a, b, n):
    """n-minute bars from 1-minute indices a..b (inclusive), aligned to the clock (bar starts where minute % n == 0, or at a).
    Returns list of (first_idx, last_idx, o, h, l, c)."""
    out = []; s = a
    for i in range(a, b + 1):
        if i == b or (mk.m[i + 1] % n == 0) or mk.m[i + 1] - mk.m[s] >= n or mk.date[i + 1] != mk.date[s]:
            out.append((s, i, mk.o[s], mk.h[s:i + 1].max(), mk.l[s:i + 1].min(), mk.c[i])); s = i + 1
    return out


def full_bars(mk, n):
    """n-minute bars over the whole 1-minute cache (24-hour session), clock-aligned, split at contract changes. Cached per market."""
    def mkb():
        t = mk.t.astype('datetime64[m]').astype(np.int64); key = t // n
        brk = np.r_[True, (key[1:] != key[:-1]) | (mk.iid[1:] != mk.iid[:-1])]
        st = np.where(brk)[0]; en = np.r_[st[1:] - 1, len(t) - 1]
        h = np.maximum.reduceat(mk.ah, st); l = np.minimum.reduceat(mk.al, st)
        return dict(st=st, en=en, o=mk.ao[st], h=h, l=l, c=mk.ac[en], m=mk.m[st], iid=mk.iid[st], t=t[st])
    return cache(('fb', mk.name, n), mkb)


def wilder_adx(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    up = h - np.r_[h[0], h[:-1]]; dn = np.r_[l[0], l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    ew = lambda x: pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values
    atr_ = ew(tr); pdi = 100 * ew(pdm) / np.where(atr_ > 0, atr_, np.nan); ndi = 100 * ew(ndm) / np.where(atr_ > 0, atr_, np.nan)
    dx = 100 * abs(pdi - ndi) / np.where(pdi + ndi > 0, pdi + ndi, np.nan)
    return ew(np.nan_to_num(dx)), atr_


# ------------------------------------------------------------------------------------------------ B01-B
@strat('B01', 'opening-range fade', 'ProRealAlgos "Touch & Turn" (YouTube BifyQ6ppdLU, 690K views)', '2025-12-01', orm=15, minf=0.25, stopf=0.191,
       tgtf=0.382, until=660)
def b01(orm=15, minf=0.25, stopf=0.191, tgtf=0.382, until=660):
    a14 = atr(14)
    def f(k, mk, i0, i1, D):
        b = at(mk, i0, i1, 570 + orm); u = at(mk, i0, i1, until)
        if b is None or u is None or k < 1 or np.isnan(a14[k - 1]): return
        H, Lo, O, C = mk.h[i0:b].max(), mk.l[i0:b].min(), mk.o[i0], mk.c[b - 1]; R = H - Lo
        if R < minf * a14[k - 1] or C == O: return
        dr, lvl = (1, Lo) if C < O else (-1, H)
        for i in range(b, u):
            if (dr > 0 and mk.l[i] <= lvl - TICK) or (dr < 0 and mk.h[i] >= lvl + TICK):
                e = min(mk.o[i], lvl) if dr > 0 else max(mk.o[i], lvl)
                x, _ = walk(mk, i, i1, dr, lvl - dr * stopf * R, lvl + dr * tgtf * R)
                return [(dr, e, x, 2, mk.m[i])]
    return intraday(f)


@strat('B02', 'London-range breakout', 'Jooviers Gems London breakout as coded by Eddy Pips (YouTube cxhIEtGNHhk / 8Lhfo2urf58)', '2025-08-01',
       r0=180, r1=540, e1=660, rr=2.0)
def b02(r0=180, r1=540, e1=660, rr=2.0):
    def f(k, mk, i0, i1, D):
        d = D.date.iat[k]; a, b = idx_at(mk, d, r0), idx_at(mk, d, r1 - 1); u = at(mk, i0, i1, e1)
        if a is None or b is None or u is None or mk.iid[a] != mk.iid[i1]: return
        H, Lo = mk.h[a:b + 1].max(), mk.l[a:b + 1].min()
        if mk.h[b + 1:i0].max(initial=-1e18) > H or mk.l[b + 1:i0].min(initial=1e18) < Lo: return       # broken before 09:30: skip
        for s, e, o, h, l, c in bars(mk, i0, u - 1, 5):
            if c > H or c < Lo:
                dr = 1 if c > H else -1; sp = l - TICK if dr > 0 else h + TICK; R = abs(c - sp)
                if R <= 0 or e >= i1: return
                x, _ = walk(mk, e + 1, i1, dr, sp, c + dr * rr * R)
                return [(dr, c, x, 2, mk.m[s])]
    return intraday(f)


@strat('B03', 'day-of-week momentum', 'Quantified Strategies SPY Friday last hour (YouTube 034h7qvVCPs)', '2026-10-01', up=0.006, rng=0.007)
def b03(up=0.006, rng=0.007):
    def f(k, mk, i0, i1, D):
        if D.date.iat[k].weekday() != 4 or not same_seg(k): return
        j10, j15, e = at(mk, i0, i1, 600), at(mk, i0, i1, 900), at(mk, i0, i1, 905)
        if None in (j10, j15, e): return
        if (mk.ac[j10 - 1] - D.aC.iat[k - 1]) / D.px.iat[k] < up: return
        if (mk.h[i0:j15].max() - mk.l[i0:j15].min()) / mk.o[i0] < rng: return
        return [(1, mk.o[e], mk.c[i1], 2, 905)]
    return intraday(f)


@strat('B04', 'momentum + trend', 'Jacob Amaral NQ automated momentum (YouTube 3f4XNJqk2qQ), points as % of NQ 15,000', '2024-06-01',
       mom=0.006, n=100, stop=0.0042, hold=60)
def b04(mom=0.006, n=100, stop=0.0042, hold=60):
    def prep(mk):
        def m():
            b = full_bars(mk, 4); c = b['c']
            mo = np.r_[np.full(14, np.nan), c[14:] - c[:-14]] / c; s = pd.Series(c).rolling(n).mean().values
            return dict(b=b, mo=mo, s=s)
        return cache(('b04', mk.name, n), m)
    def f(k, mk, i0, i1, D):
        z = prep(mk); b = z['b']; a = np.searchsorted(b['st'], i0); e = np.searchsorted(b['st'], i1, side='right') - 1
        for q in range(max(a, n + 1), e):
            if b['iid'][q] != mk.iid[i1]: continue
            if z['mo'][q] > mom and z['mo'][q - 1] <= mom and b['c'][q] > z['s'][q] and b['c'][q - 1] <= z['s'][q - 1]:
                fi = b['en'][q] + 1; ent = mk.ao[fi]; last = b['en'][min(q + hold, e)]
                sp = ent * (1 - stop); x = mk.ac[last]
                for i in range(fi, last + 1):
                    if mk.al[i] <= sp: x = sp if (i == fi or mk.ao[i] > sp) else mk.ao[i]; break
                return [(1, ent, x, 2, mk.m[fi])]
    return intraday(f)


@strat('B05', 'trend filter', 'TraderNick NAS100 SMA20/50 risk-on leg (YouTube cnzS0T3ktnE; bond leg not available)', '2022-01-01', f=20, s=50)
def b05(f=20, s=50):
    j = J(); c = j['c']; return daily_hold(np.where(sma(c, f) > sma(c, s), 1.0, 0.0))


@strat('B06', '3-bar play', 'Live Traders 3-bar play, mechanical core (YouTube eXO1EXDnCpE 3.0M / xEjUd82NVVg)', '2019-01-01', n=2, wide=2.0, avg=20,
       end=630, rr=2.0)
def b06(n=2, wide=2.0, avg=20, end=630, rr=2.0):
    def f(k, mk, i0, i1, D):
        u = at(mk, i0, i1, end); fl = at(mk, i0, i1, 955) or i1
        if u is None or i0 < avg * n + 1: return
        B = bars(mk, i0 - avg * n, i1, n); out = []; busy = -1; taken = 0
        rng = np.array([x[3] - x[4] for x in B])
        for j in range(avg + 1, len(B) - 2):
            s1, e1, o1, h1, l1, c1 = B[j - 1]; s2, e2, o2, h2, l2, c2 = B[j]
            if s2 < i0 or s2 >= u or e2 <= busy or taken >= 2: continue
            R1 = h1 - l1; av = rng[j - 1 - avg:j - 1].mean()
            if R1 < wide * av or R1 <= 0: continue
            for dr in (1, -1):
                ok = (dr > 0 and c1 > o1 and l2 >= l1 + 0.5 * R1 and h2 <= h1 and h2 >= h1 - 0.25 * R1) or \
                     (dr < 0 and c1 < o1 and h2 <= h1 - 0.5 * R1 and l2 >= l1 and l2 <= l1 + 0.25 * R1)
                if not ok: continue
                lvl = max(h1, h2) + TICK if dr > 0 else min(l1, l2) - TICK; sp = l2 - TICK if dr > 0 else h2 + TICK
                s3, e3 = B[j + 1][0], B[j + 1][1]
                if not ((dr > 0 and mk.h[s3:e3 + 1].max() >= lvl) or (dr < 0 and mk.l[s3:e3 + 1].min() <= lvl)): continue
                fi = next(i for i in range(s3, e3 + 1) if (dr > 0 and mk.h[i] >= lvl) or (dr < 0 and mk.l[i] <= lvl))
                ent = max(mk.o[fi], lvl) if dr > 0 else min(mk.o[fi], lvl); R = abs(ent - sp)
                x, xi = walk(mk, fi, fl, dr, sp, ent + dr * rr * R)
                out.append((dr, ent, x, 2, mk.m[fi])); busy = xi; taken += 1; break
        return out
    return intraday(f)


def _connors_qs(entry, exitf):
    return daily_hold(state_machine(entry, exitf))


@strat('B07', 'mean reversion', 'Quantified Strategies Double-5 (YouTube gX8FjjKR7ec)', '2024-01-01')
def b07():
    j = J(); c = j['c']; s = sma(c, 200); lo5 = pd.Series(c).rolling(5).min().values; hi5 = pd.Series(c).rolling(5).max().values
    return _connors_qs(lambda k: c[k] > s[k] and c[k] <= lo5[k], lambda k: c[k] >= hi5[k])


@strat('B08', 'mean reversion', 'Quantified Strategies RSI(5) < 30 (YouTube gX8FjjKR7ec)', '2024-01-01', n=5, lo=30.0, hi=50.0)
def b08(n=5, lo=30.0, hi=50.0):
    j = J(); c = j['c']; s = sma(c, 200); r = rsi(c, n)
    return _connors_qs(lambda k: c[k] > s[k] and r[k] < lo, lambda k: r[k] > hi)


@strat('B09', 'mean reversion', 'Quantified Strategies %B(5,2) < 0.2 three days, exit above prior high (YouTube gX8FjjKR7ec)', '2024-01-01')
def b09():
    j = J(); c, h = j['c'], j['h']; m = sma(c, 5); s = sd(c, 5); b = (c - (m - 2 * s)) / np.where(s > 0, 4 * s, np.nan); s200 = sma(c, 200)
    return _connors_qs(lambda k: k > 2 and c[k] > s200[k] and b[k] < 0.2 and b[k - 1] < 0.2 and b[k - 2] < 0.2, lambda k: c[k] > h[k - 1])


@strat('B10', 'band reversion', 'Trade Pro BB(21,2) + RSI(8) 15-min reversal (YouTube CeuLK-gCe94)', '2020-01-01', n=21, w=2.0, rn=8)
def b10(n=21, w=2.0, rn=8):
    def f(k, mk, i0, i1, D):
        B = bars(mk, max(i0 - 40 * 15, 0), i1, 15); c = np.array([x[5] for x in B]); o = np.array([x[2] for x in B])
        m = pd.Series(c).rolling(n).mean().values; s = pd.Series(c).rolling(n).std(ddof=0).values; r = rsi(c, rn); out = []; busy = -1
        for j in range(n + 1, len(B) - 1):
            if B[j][0] < i0 or B[j][1] <= busy: continue
            for dr in (1, -1):
                setup = (dr > 0 and r[j - 1] < 30 and c[j - 1] <= m[j - 1] - w * s[j - 1]) or (dr < 0 and r[j - 1] > 70 and c[j - 1] >= m[j - 1] + w * s[j - 1])
                turn = (dr > 0 and r[j] > r[j - 1] and c[j] > o[j]) or (dr < 0 and r[j] < r[j - 1] and c[j] < o[j])
                if setup and turn and B[j + 1][0] <= i1:
                    e = B[j + 1][2]; sp = B[j][4] - TICK if dr > 0 else B[j][3] + TICK; tg = m[j]
                    if dr * (tg - e) <= 0: continue
                    x, xi = walk(mk, B[j + 1][0], i1, dr, sp, tg); out.append((dr, e, x, 2, mk.m[B[j + 1][0]])); busy = xi; break
        return out
    return intraday(f)


@strat('B11', 'regime gate', 'NQ Stats price-distribution regime: NoiseArea only when sigma20 < sigma5y (YouTube pxs2MuqvH3w)', '2026-05-01',
       short=20, long_=1260)
def b11(short=20, long_=1260):
    """Gate type: returns the CHANGE to NoiseArea (minus NoiseArea on gated-out days), so 'NoiseArea + this' = gated NoiseArea."""
    if T.FILT: return None
    D = T.P.daily; r = pd.Series(np.r_[np.nan, (D.C.values[1:] - D.O.values[1:]) / D.O.values[1:]])
    s20 = r.rolling(short).std().shift(1); s5 = r.rolling(long_, min_periods=250).std().shift(1)
    gate = (s20 < s5).values
    na = T.noisearea().reindex(T.P.dates).fillna(0).values
    g = np.where(gate, 0.0, -na); return pd.Series(g + 0.0, index=T.P.dates), pd.Series(np.zeros(len(g)), index=T.P.dates)


@strat('B12', 'intraday momentum', 'Gao et al. discussant variant: overnight return -> last 10 minutes (YouTube n_eUvqMbTg8)', '2015-01-01', last=10)
def b12(last=10):
    def f(k, mk, i0, i1, D):
        if not same_seg(k): return
        s = np.sign(mk.ao[i0] - D.aC.iat[k - 1]); end = mk.m[i1] + 1; j = at(mk, i0, i1, end - last)
        if s and j is not None: return [(s, mk.o[j], mk.c[i1], 2, end - last)]
    return intraday(f)


@strat('B13', 'trend filter', 'Financial Wisdom QQQ 225-day SMA (YouTube MCOiVEBjFnA)', '2025-02-01', n=225)
def b13(n=225):
    j = J(); return daily_hold(np.where(j['c'] > sma(j['c'], n), 1.0, 0.0))


@strat('B14', 'seasonal', 'Financial Wisdom Nov-Jun seasonality (YouTube MCOiVEBjFnA)', '2025-02-01')
def b14(): return hold_from_days(np.isin(T.P.dates.month, [11, 12, 1, 2, 3, 4, 5, 6]))


@strat('B16', 'opening reversal', 'The Rumers opening-range reversal (YouTube 8vufTzGZqiI, 344K views)', '2025-01-01', thr=0.2, sman=50, cut=720)
def b16(thr=0.2, sman=50, cut=720):
    j = J(); s50 = sma(j['c'], sman); a14 = atr(14)
    def f(k, mk, i0, i1, D):
        if k < 1 or np.isnan(s50[k - 1]) or np.isnan(a14[k - 1]): return
        dr = 1 if j['c'][k - 1] > s50[k - 1] else -1; u = at(mk, i0, i1, cut) or i1
        B = bars(mk, i0, i1, 5); O = mk.o[i0]; ext, ebar = None, None
        for q, (s, e, o, h, l, c) in enumerate(B):
            if s >= u: return
            if ebar is not None and ebar < q:
                lvl = B[ebar][3] + TICK if dr > 0 else B[ebar][4] - TICK
                if (dr > 0 and h >= lvl) or (dr < 0 and l <= lvl):
                    move = abs(ext - O)
                    if move >= thr * a14[k - 1]:
                        fi = next(i for i in range(s, e + 1) if (dr > 0 and mk.h[i] >= lvl) or (dr < 0 and mk.l[i] <= lvl))
                        ent = max(mk.o[fi], lvl) if dr > 0 else min(mk.o[fi], lvl)
                        tg = ext + dr * 0.5 * move; sp = ext - dr * TICK
                        if dr * (tg - ent) <= 0: return
                        x, _ = walk(mk, fi, i1, dr, sp, tg); return [(dr, ent, x, 2, mk.m[fi])]
            if (dr > 0 and (ext is None or l < ext)) or (dr < 0 and (ext is None or h > ext)):
                ext, ebar = (l if dr > 0 else h), q
    return intraday(f)


@strat('B17', 'ORB + filter', 'StatOasis ORB long with falling ATR (YouTube AvnkY8crUhM)', '2026-04-01', orm=15, kk=0.0, an=5)
def b17(orm=15, kk=0.0, an=5, side=1):
    a = atr(an)
    def f(k, mk, i0, i1, D):
        if k < 2 or np.isnan(a[k - 2]): return
        if side > 0 and not (a[k - 1] < a[k - 2]): return
        if side < 0:
            Hp, Lp, Cp = D.H.iat[k - 1], D.L.iat[k - 1], D.C.iat[k - 1]
            if not (Hp > Lp and (Cp - Lp) / (Hp - Lp) > 0.5): return
        b = at(mk, i0, i1, 570 + orm)
        if b is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min(); lvl = H + kk * a[k - 1] + TICK if side > 0 else Lo - kk * a[k - 1] - TICK
        for i in range(b, i1 + 1):
            if (side > 0 and mk.h[i] >= lvl) or (side < 0 and mk.l[i] <= lvl):
                e = max(mk.o[i], lvl) if side > 0 else min(mk.o[i], lvl); return [(side, e, mk.c[i1], 2, mk.m[i])]
    return intraday(f)


@strat('B18', 'ORB + filter', 'StatOasis ORB short when yesterday closed in its upper half (YouTube AvnkY8crUhM)', '2026-04-01', orm=15, kk=0.0, an=5)
def b18(orm=15, kk=0.0, an=5): return b17(orm=orm, kk=kk, an=an, side=-1)


@strat('B19', 'daily breakout', 'StatOasis prior-high + 0.3 ATR breakout, falling ATR, 5-day hold (YouTube AvnkY8crUhM)', '2026-04-01', kk=0.3, an=5, hold=5)
def b19(kk=0.3, an=5, hold=5):
    D = T.P.daily; aO, aH, aC, seg = D.aO.values, D.aH.values, D.aC.values, D.seg.values; a = atr(an); n = len(D); tr = []; k = 2
    while k < n:
        lvl = aH[k - 1] + kk * a[k - 1] if not np.isnan(a[k - 1]) else np.inf
        if seg[k] == seg[k - 1] and a[k - 1] < a[k - 2] and aH[k] >= lvl and _filt_ok(1, k):
            e = max(aO[k], lvl); x = min(k + hold - 1, n - 1)
            while seg[x] != seg[k]: x -= 1
            tr.append((k, 1, e, x, aC[x])); k = x + 1
        else: k += 1
    return mtm(tr)


@strat('B20', 'outside-bar reversal', 'Larry Williams outside bar (Transparent Trader, YouTube lrKWUeBc14s)', '2021-01-01', stop=0.017)
def b20(stop=0.017):
    D = T.P.daily; aH, aL, aC, aO, seg = D.aH.values, D.aL.values, D.aC.values, D.aO.values, D.seg.values; n = len(D); tr = []; k = 1
    while k < n - 1:
        if seg[k] == seg[k - 1] and aH[k] > aH[k - 1] and aL[k] < aL[k - 1] and aC[k] < aL[k - 1] and _filt_ok(1, k + 1):
            e = aC[k]; sp = e - stop * D.px.iat[k]; j = k + 1; done = False
            while j < n and seg[j] == seg[k]:
                if aL[j] <= sp: tr.append((k, 1, e, j, min(sp, aO[j]))); done = True; break
                if aC[j] > e: tr.append((k, 1, e, j, aC[j])); done = True; break
                j += 1
            if not done: tr.append((k, 1, e, j - 1, aC[j - 1]))
            k = j + 1
        else: k += 1
    out = []
    for k0, dr, e, k1, x in tr:                                   # entry at k0's close: the first marked day is k0 + 1
        out.append((k0, dr, e, k1, x))
    return _mtm_from_close(out)


def _mtm_from_close(trades):
    """Trades entered AT the close of k0 (no P&L on k0): daily marked-to-market from k0+1 to the exit day."""
    D = T.P.daily; aC, px = D.aC.values, D.px.values; out = []
    for k0, dr, e, k1, x in trades:
        out.append((k0, 0.0, 1))
        for k in range(k0 + 1, k1): out.append((k, dr * (aC[k] - aC[k - 1]) / px[k], 0))
        out.append((k1, dr * (x - aC[k1 - 1]) / px[k1], 1))
    return L.series(T.P, out)


@strat('B21', 'NR4 stretch reversion', 'StatOasis NR4 + Crabel stretch, long limit at open - stretch (YouTube 8qqYox2r6XI)', '2026-08-01', kk=1.0, look=10)
def b21(kk=1.0, look=10):
    D = T.P.daily; H, Lo, O = D.aH.values, D.aL.values, D.aO.values; rng = H - Lo
    st = pd.Series(np.minimum(H - O, O - Lo)).rolling(look).mean().values
    def f(k, mk, i0, i1, D):
        if k < 4 or not same_seg(k, 4) or np.isnan(st[k - 1]): return
        if rng[k - 1] > rng[k - 4:k - 1].min(): return                     # NR4: yesterday's range the smallest of 4
        lvl = mk.ao[i0] - kk * st[k - 1]
        for i in range(i0, i1 + 1):
            if mk.al[i] <= lvl - TICK:
                e = min(mk.ao[i], lvl); return [(1, e + mk.o[i0] - mk.ao[i0], mk.c[i1], 2, mk.m[i])]
    return intraday(f)


@strat('B22', 'late-day momentum', 'Quantified Strategies QQQ gap-up + late strength since 1999 (YouTube EGcbll-cIqk)', '2026-10-01', gap=0.006,
       rpos=0.77, stop=0.01, n=50)
def b22(gap=0.006, rpos=0.77, stop=0.01, n=50):
    j = J(); s50 = sma(j['c'], n)
    def f(k, mk, i0, i1, D):
        if not same_seg(k) or np.isnan(s50[k - 1]) or not j['c'][k - 1] < s50[k - 1]: return
        if (mk.ao[i0] - D.aC.iat[k - 1]) / D.px.iat[k] <= gap: return
        j15, e = at(mk, i0, i1, 900), at(mk, i0, i1, 905)
        if j15 is None or e is None: return
        p = mk.c[j15 - 1]; H, Lo = mk.h[i0:j15].max(), mk.l[i0:j15].min()
        if not (p > mk.o[i0] and H > Lo and (p - Lo) / (H - Lo) > rpos): return
        ent = mk.o[e]; x, _ = walk(mk, e, i1, 1, ent * (1 - stop), None)
        return [(1, ent, x, 2, 905)]
    return intraday(f)


@strat('B23', 'VWAP drift pullback', 'Matteo Conti drift VWAP (IQCapital, YouTube wm4A6qo0g3I, 251K); points as % of NQ 20,000', '2026-08-01',
       drift=0.001, stop=0.004, tl=0.002, ts=0.0025, start=630, last_new=930, flat=955, maxt=4, maxloss=2)
def b23(drift=0.001, stop=0.004, tl=0.002, ts=0.0025, start=630, last_new=930, flat=955, maxt=4, maxloss=2):
    def f(k, mk, i0, i1, D):
        fl = at(mk, i0, i1, flat - 1) or i1; vw = T.vwap(mk, i0, i1); out = []; busy = -1; losses = 0
        for s, e, o, h, l, c in bars(mk, i0, fl, 5):
            if mk.m[s] < start or mk.m[s] >= last_new or e <= busy or e >= fl or len(out) >= maxt or losses >= maxloss: continue
            q = e
            while q > i0 and (mk.m[q] + 1) % 15 != 0: q -= 1
            if q - 60 < i0: continue
            c15, v15, v0, c60 = mk.c[q], vw[q - i0], vw[q - 15 - i0], mk.c[q - 60]
            up = c15 > v15 and v15 > v0 and (c15 - c60) / c60 >= drift; dn = c15 < v15 and v15 < v0 and (c15 - c60) / c60 <= -drift
            dr = 1 if (up and c < o) else (-1 if (dn and c > o) else 0)
            if not dr: continue
            ent = mk.o[e + 1]; tg = ent * (1 + tl) if dr > 0 else ent * (1 - ts)
            x, xi = walk(mk, e + 1, fl, dr, ent * (1 - dr * stop), tg)
            out.append((dr, ent, x, 2, mk.m[e + 1])); busy = xi; losses += dr * (x - ent) < 0
        return out
    return intraday(f)


@strat('B24', 'opening-range fade', 'ProRealAlgos Quick Flip (YouTube XFtayhPIdEs, 1.87M views)', '2025-01-01', orm=15, minf=0.25, until=660)
def b24(orm=15, minf=0.25, until=660):
    a14 = atr(14)
    def f(k, mk, i0, i1, D):
        b, u = at(mk, i0, i1, 570 + orm), at(mk, i0, i1, until)
        if b is None or u is None or k < 1 or np.isnan(a14[k - 1]): return
        H, Lo, O, C = mk.h[i0:b].max(), mk.l[i0:b].min(), mk.o[i0], mk.c[b - 1]
        if H - Lo < minf * a14[k - 1] or C == O: return
        dr = -1 if C > O else 1; B5 = bars(mk, b, u - 1, 5)
        for q in range(1, len(B5)):
            s, e, o, h, l, c = B5[q]; po, pc_ = B5[q - 1][2], B5[q - 1][5]; body = abs(c - o); up = h - max(o, c); lw = min(o, c) - l
            if dr < 0: pat = h > H and ((body > 0 and up >= 2 * body and lw <= body) or (c < o and pc_ > po and o >= pc_ and c <= po))
            else: pat = l < Lo and ((body > 0 and lw >= 2 * body and up <= body) or (c > o and pc_ < po and o <= pc_ and c >= po))
            if pat and e < i1:
                ent = mk.o[e + 1]; sp = h + TICK if dr < 0 else l - TICK; tg = Lo if dr < 0 else H
                if dr * (tg - ent) <= 0 or dr * (ent - sp) <= 0: return
                x, _ = walk(mk, e + 1, i1, dr, sp, tg); return [(dr, ent, x, 2, mk.m[e + 1])]
    return intraday(f)


def _levels(x, grid=100.0):
    b = np.floor(x / grid) * grid; c = b + np.array([-80.0, -20.0, 20.0, 80.0, 120.0, 180.0]) * grid / 100
    return c[c > x].min(), c[c < x].max()


@strat('B25', 'round-number fade', 'Chart Fanatics NQ xx20/xx80 level fade (YouTube jsUTbjwpFVk, 318K); bracket as % of NQ 25,000', '2026-01-01',
       sl=10.0, tp=15.0, start=570, end=690)
def b25(sl=10.0, tp=15.0, start=570, end=690):
    def f(k, mk, i0, i1, D):
        u = at(mk, i0, i1, end) or i1; sc = mk.o[i0] / 25000.0; SL, TP = sl * sc, tp * sc; out = []; i = max(i0 + 1, at(mk, i0, i1, start) or i0 + 1)
        while i < u:
            Lu, Ld = _levels(mk.c[i - 1]); hu, hd = mk.h[i] >= Lu + TICK, mk.l[i] <= Ld - TICK
            if hu == hd: i += 1; continue
            dr, lvl = (-1, Lu) if hu else (1, Ld); ent = max(mk.o[i], lvl) if dr < 0 else min(mk.o[i], lvl)
            x, xi = walk(mk, i, i1, dr, ent - dr * SL, ent + dr * TP); out.append((dr, ent, x, 2, mk.m[i])); i = xi + 1
        return out
    return intraday(f)


def _ema(x, n): return pd.Series(x).ewm(span=n, adjust=False).mean().values


@strat('B26', 'EMA trend pullback', 'TradersPost 8/55 EMA NQ/MNQ (public Pine code, YouTube 61mYTkmM8XU)', '2023-01-01', f8=8, s55=55, look=5,
       trail=0.007, start=600, end=955)
def b26(f8=8, s55=55, look=5, trail=0.007, start=600, end=955):
    def prep(mk):
        def m():
            b5, b15 = full_bars(mk, 5), full_bars(mk, 15)
            e8, e55 = _ema(b5['c'], f8), _ema(b5['c'], s55); e55h = _ema(b15['c'], s55)
            jj = np.searchsorted(b15['en'], b5['en'], side='right') - 1                      # last completed 15-min bar at each 5-min close
            return dict(b5=b5, e8=e8, e55=e55, e55h=np.where(jj >= 0, e55h[np.clip(jj, 0, None)], np.nan))
        return cache(('b26', mk.name, f8, s55), m)
    def f(k, mk, i0, i1, D):
        z = prep(mk); b5 = z['b5']; a = np.searchsorted(b5['st'], i0); b = np.searchsorted(b5['st'], i1, side='right') - 1; out = []; busy = -1
        for q in range(max(a, look), b + 1):
            if b5['m'][q] < start or b5['m'][q] >= end or b5['en'][q] <= busy or b5['en'][q] >= i1: continue
            if not (z['e8'][q] > z['e55'][q] and b5['c'][q] > z['e55h'][q] and z['e8'][q] > z['e55h'][q]): continue
            if not (b5['c'][q] > z['e8'][q] and b5['c'][q - 1] <= z['e8'][q - 1]): continue
            if not (b5['l'][q - look:q] <= z['e55'][q - look:q]).any(): continue
            ent = b5['c'][q]; hi = ent; xi = i1; x = mk.ac[i1]
            for i in range(b5['en'][q] + 1, i1 + 1):
                sp = hi * (1 - trail)
                if mk.al[i] <= sp: x, xi = (sp if mk.ao[i] > sp else mk.ao[i]), i; break
                hi = max(hi, mk.ah[i])
            out.append((1, ent, x, 2, b5['m'][q])); busy = xi
        return out
    return intraday(f)


@strat('B27', 'gap drift', 'StatOasis SPY gap study: |gap| >= 1 ATR(20) -> long next session (YouTube qKtWzLZjy38)', '2026-07-01', mult=1.0, n=20)
def b27(mult=1.0, n=20):
    j = J(); a = atr(n); o, c = j['o'], j['c']; pos = np.zeros(len(c))
    for k in range(1, len(c)):
        if not np.isnan(a[k - 1]) and abs(o[k] - c[k - 1]) >= mult * a[k - 1]: pos[k] = 1
    return daily_hold(pos)


def _fractal_swings(h, l, i0, i):
    """Most recent confirmed 3-bar fractal swing high/low strictly before bar i (needs bar j+1 < i)."""
    sh = sl_ = None
    for j in range(i - 2, i0, -1):
        if sh is None and h[j] > h[j - 1] and h[j] > h[j + 1]: sh = h[j]
        if sl_ is None and l[j] < l[j - 1] and l[j] < l[j + 1]: sl_ = l[j]
        if sh is not None and sl_ is not None: break
    return sh, sl_


@strat('B28', 'fair-price reversion', 'JJ Simon Fair Pricing (Chart Fanatics KHEQ5g55dQ4, 556K; FX Replay SNO1wqJTq5A); bracket as % of NQ 20,000',
       '2025-06-01', sl=25.0, tp=38.0, reach=0.8, maxloss=3)
def b28(sl=25.0, tp=38.0, reach=0.8, maxloss=3):
    def f(k, mk, i0, i1, D):
        sc = mk.o[i0] / 20000.0; SL, TP = sl * sc, tp * sc; out = []
        for fm, em in ((570, 660), (840, 930)):
            a, u = at(mk, i0, i1, fm), at(mk, i0, i1, em)
            if a is None or u is None: continue
            fair = mk.o[a]; losses = 0; cont = False; i = a + 2
            while i < u and losses < maxloss:
                o, h, l, c = mk.o[i], mk.h[i], mk.l[i], mk.c[i]; po, ph, pl, pc = mk.o[i - 1], mk.h[i - 1], mk.l[i - 1], mk.c[i - 1]
                sh, sw = _fractal_swings(mk.h, mk.l, a, i)
                bl = sh is not None and c > sh >= pc; bs = sw is not None and c < sw <= pc
                dl = c > o and pc < po and (c - o) > (po - pc) and c > ph; ds = c < o and pc > po and (o - c) > (pc - po) and c < pl
                dr = 0
                if not cont and mk.m[i] < fm + 5:
                    d0 = np.sign(mk.c[a] - mk.o[a])
                    if (d0 > 0 and bl) or (d0 < 0 and bs): dr = int(d0); cont = True
                if not dr and (bl or dl) and fair - c >= reach * TP: dr = 1
                if not dr and (bs or ds) and c - fair >= reach * TP: dr = -1
                if dr and i < i1:
                    x, xi = walk(mk, i + 1, i1, dr, c - dr * SL, c + dr * TP); out.append((dr, c, x, 2, mk.m[i]))
                    losses = losses + 1 if dr * (x - c) < 0 else 0; i = xi + 1
                else: i += 1
        return out
    return intraday(f)


@strat('B29', 'volatility breakout', 'StatOasis Larry Williams momentum breakout k=1.0, exit 15:00 (YouTube JbmD7sFqRXI)', '2026-01-01', kk=1.0, exit_m=900)
def b29(kk=1.0, exit_m=900):
    def f(k, mk, i0, i1, D):
        if not same_iid(k): return
        R = D.H.iat[k - 1] - D.L.iat[k - 1]; O = mk.o[i0]; up, dn = O + kk * R, O - kk * R; xe = at(mk, i0, i1, exit_m - 1) or i1
        for i in range(i0, xe + 1):
            hu, hd = mk.h[i] >= up, mk.l[i] <= dn
            if hu and hd: return
            if hu or hd:
                dr = 1 if hu else -1; e = max(mk.o[i], up) if hu else min(mk.o[i], dn); return [(dr, e, mk.c[xe], 2, mk.m[i])]
    return intraday(f)


def supertrend(h, l, c, n, m):
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))); a = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values
    hl2 = (h + l) / 2; bu, bl = hl2 + m * a, hl2 - m * a; fu, fl = bu.copy(), bl.copy(); tr_ = np.ones(len(c))
    for k in range(1, len(c)):
        fu[k] = bu[k] if (bu[k] < fu[k - 1] or c[k - 1] > fu[k - 1]) else fu[k - 1]
        fl[k] = bl[k] if (bl[k] > fl[k - 1] or c[k - 1] < fl[k - 1]) else fl[k - 1]
        tr_[k] = 1 if c[k] > fu[k - 1] else (-1 if c[k] < fl[k - 1] else tr_[k - 1])
    return tr_


@strat('B30', 'trend following', 'StatOasis dual SuperTrend NQ daily (YouTube F-C9V29h2fw)', '2024-01-01', fn=10, fm=2.0, sn=30, smult=4.0)
def b30(fn=10, fm=2.0, sn=30, smult=4.0):
    j = J(); D = T.P.daily; aO, aC, seg = D.aO.values, D.aC.values, D.seg.values
    f_, s_ = supertrend(j['h'], j['l'], j['c'], fn, fm), supertrend(j['h'], j['l'], j['c'], sn, smult); n = len(aC); tr = []; k = 1
    while k < n - 1:
        if f_[k] == 1 and f_[k - 1] == -1 and s_[k] == 1 and seg[k + 1] == seg[k] and _filt_ok(1, k + 1):
            x = k + 1
            while x < n - 1 and seg[x + 1] == seg[k] and f_[x] != -1: x += 1
            k1 = x + 1 if (x + 1 < n and seg[x + 1] == seg[k]) else x
            tr.append((k + 1, 1, aO[k + 1], k1, aO[k1] if k1 > x else aC[k1])); k = k1 + 1
        else: k += 1
    return mtm(tr)


@strat('B31', 'statistics fade', 'NQ Stats Hour Stats per the videos: sweep in the first 20 min, room >= 20 pts at NQ 18,000, 1-min reclaim entry',
       '2026-07-01', first=8, last=15, seg=20, room=20.0)
def b31(first=8, last=15, seg=20, room=20.0):
    def f(k, mk, i0, i1, D):
        out = []; d = D.date.iat[k]; rm = room * mk.o[i0] / 18000.0
        for h in range(first, last + 1):
            p0, a, e = idx_at(mk, d, (h - 1) * 60), idx_at(mk, d, h * 60), idx_at(mk, d, h * 60 + 59)
            if None in (p0, a, e) or e > i1 or mk.iid[p0] != mk.iid[e] or e - p0 != 119: continue
            H, Lo = mk.h[p0:a].max(), mk.l[p0:a].min(); op = mk.o[a]
            if not (Lo <= op <= H): continue
            sw = None
            for i in range(a, a + seg):
                if mk.h[i] > H and H - op >= rm: sw = -1; break
                if mk.l[i] < Lo and op - Lo >= rm: sw = 1; break
            if sw is None: continue
            for j in range(i, e):
                if (sw < 0 and mk.c[j] < H) or (sw > 0 and mk.c[j] > Lo):
                    ent = mk.c[j]
                    if sw * (op - ent) <= 0: break
                    x, _ = walk(mk, j + 1, e, sw, None, op); out.append((sw, ent, x, 2, mk.m[j])); break
        return out
    return intraday(f)


@strat('B32', 'ORB + filters', 'SoloQuantTrader NQ 5-min ORB, 232 trades with costs (YouTube OI791yQSsps)', '2026-08-01', an=14, kk=1.0, cut=600,
       stopk=3.0, smn=20)
def b32(an=14, kk=1.0, cut=600, stopk=3.0, smn=20):
    j = J(); s20 = sma(j['c'], smn)
    def f(k, mk, i0, i1, D):
        if k < 1 or np.isnan(s20[k - 1]) or not j['c'][k - 1] > s20[k - 1]: return
        B = bars(mk, max(i0 - 5 * (an + 2), 0), i1, 5); st = [x[0] for x in B]
        if i0 not in st: return
        q0 = st.index(i0); hl = np.array([x[3] - x[4] for x in B]); orh = B[q0][3]
        for q in range(q0 + 1, len(B) - 1):
            if mk.m[B[q][1]] + 1 >= cut: return
            a5 = hl[q - an + 1:q + 1].mean()
            if B[q][5] > orh + kk * a5:
                e = B[q + 1][2]; x, _ = walk(mk, B[q + 1][0], i1, 1, e - stopk * a5, None); return [(1, e, x, 2, mk.m[B[q + 1][0]])]
    return intraday(f)


@strat('B33', 'ORB hold to close', 'Algovibes 30-min ORB, frozen settings 2020-2026 (YouTube 8wJQrfn_otM)', '2026-10-01', orm=30)
def b33(orm=30):
    def f(k, mk, i0, i1, D):
        b = at(mk, i0, i1, 570 + orm)
        if b is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min()
        for i in range(b, i1):
            if mk.c[i] > H or mk.c[i] < Lo:
                dr = 1 if mk.c[i] > H else -1; e = mk.c[i]; x, _ = walk(mk, i + 1, i1, dr, Lo - TICK if dr > 0 else H + TICK, None)
                return [(dr, e, x, 2, mk.m[i])]
    return intraday(f)


@strat('B34', 'mean reversion', 'ProRealAlgos 500-entry study: RSI(14) < 30 long, exit below EMA(10) (YouTube xTsT63b_2dc)', '2025-01-01', stop=0.05,
       tmax=20)
def b34(stop=0.05, tmax=20):
    j = J(); c = j['c']; r = rsi(c, 14); e10 = _ema(c, 10); D = T.P.daily; aO, aL, aC, seg = D.aO.values, D.aL.values, D.aC.values, D.seg.values
    n = len(c); tr = []; k = 15
    while k < n - 1:
        if r[k] < 30 and seg[k + 1] == seg[k] and _filt_ok(1, k + 1):
            k0 = k + 1; e = aO[k0]; sp = e * (1 - stop); x = None; j_ = k0
            while j_ < n and seg[j_] == seg[k0]:
                if aL[j_] <= sp: x = (j_, min(sp, aO[j_]) if j_ > k0 else sp); break
                if c[j_] < e10[j_] or j_ - k0 >= tmax - 1:
                    jx = j_ + 1 if j_ + 1 < n and seg[j_ + 1] == seg[k0] else j_
                    x = (jx, aO[jx] if jx > j_ else aC[jx]); break
                j_ += 1
            if x is None: x = (j_ - 1, aC[j_ - 1])
            tr.append((k0, 1, e, x[0], x[1])); k = x[0] + 1
        else: k += 1
    return mtm(tr)


@strat('B35', '1-min scalp', 'Quant Trade Edge NQ 1-min momentum-loss bar (YouTube lhW5czmfc14, 107K)', '2022-01-01', en=10, an=10, tk=0.5, sk=1.0,
       end=690)
def b35(en=10, an=10, tk=0.5, sk=1.0, end=690):
    def f(k, mk, i0, i1, D):
        u = at(mk, i0, i1, end) or i1; a0 = max(i0 - 60, 0); c = mk.c[a0:i1 + 1]; em = _ema(c, en)
        tr = np.maximum(mk.h[a0:i1 + 1] - mk.l[a0:i1 + 1], 0); at_ = pd.Series(tr).rolling(an).mean().values; out = []; i = i0 + 1
        while i < u:
            z = i - a0; h, l, o, cc, ph, pl = mk.h[i], mk.l[i], mk.o[i], mk.c[i], mk.h[i - 1], mk.l[i - 1]
            dr = -1 if (h > ph and l > pl and cc < o and cc < em[z]) else (1 if (l < pl and h < ph and cc > o and cc > em[z]) else 0)
            if dr and i + 1 <= i1 and not np.isnan(at_[z]):
                lvl = l - TICK if dr < 0 else h + TICK; j = i + 1
                if (dr < 0 and mk.l[j] <= lvl) or (dr > 0 and mk.h[j] >= lvl):
                    e = min(mk.o[j], lvl) if dr < 0 else max(mk.o[j], lvl); A = at_[z]
                    x, xi = walk(mk, j, i1, dr, e - dr * sk * A, e + dr * tk * A); out.append((dr, e, x, 2, mk.m[j])); i = xi + 1; continue
            i += 1
        return out
    return intraday(f)


@strat('B36', 'breakout + ADX', 'Breakout Trading Academy Nasdaq Hitter (YouTube eq7alcWRJpA); ADX(14) < 17.5 chosen; stop as % of NQ 15,000',
       '2023-01-01', n=34, adx_thr=17.5, adx_below=True, stop=0.0033, start=540, end=960)
def b36(n=34, adx_thr=17.5, adx_below=True, stop=0.0033, start=540, end=960):
    def prep(mk):
        def m():
            b = full_bars(mk, 30); adx, _ = wilder_adx(b['h'], b['l'], b['c'], 14)
            hc = pd.Series(b['c']).rolling(n).max().values; return dict(b=b, adx=adx, hc=hc)
        return cache(('b36', mk.name, n), m)
    def f(k, mk, i0, i1, D):
        z = prep(mk); b = z['b']; a = np.searchsorted(b['st'], i0); e = np.searchsorted(b['st'], i1, side='right') - 1
        for q in range(max(a, n + 1), e + 1):
            if b['m'][q] < start or b['m'][q] >= end or b['iid'][q] != mk.iid[i1]: continue
            lvl = z['hc'][q - 1]; ok = z['adx'][q - 1] < adx_thr if adx_below else z['adx'][q - 1] > adx_thr
            if ok and b['h'][q] >= lvl:
                fi = next(i for i in range(b['st'][q], b['en'][q] + 1) if mk.ah[i] >= lvl); ent = max(mk.ao[fi], lvl)
                sp = ent * (1 - stop); x = mk.ac[i1]
                for i in range(fi, i1 + 1):
                    if mk.al[i] <= sp: x = sp if (i == fi or mk.ao[i] > sp) else mk.ao[i]; break
                return [(1, ent, x, 2, mk.m[fi])]
    return intraday(f)


@strat('B37', 'IB continuation', 'NQ Stats IB tips: upper-half close, low first, limit at 40% retrace (YouTube UPsSQP4yAO0 / 24P7rsb9P_8)', '2025-11-01',
       ret=0.4, end=720)
def b37(ret=0.4, end=720, edge=False):
    def f(k, mk, i0, i1, D):
        b, e = at(mk, i0, i1, 630), at(mk, i0, i1, end - 1)
        if b is None or e is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min(); R = H - Lo; c = mk.c[b - 1]
        th, tl = int(np.argmax(mk.h[i0:b])), int(np.argmin(mk.l[i0:b]))
        if R <= 0: return
        if edge:
            up = tl < th and c >= H - 0.25 * R and c > mk.o[i0]; dn = th < tl and c <= Lo + 0.25 * R and c < mk.o[i0]
            dr = 1 if up else (-1 if dn else 0); lvl = H - 0.25 * R if dr > 0 else Lo + 0.25 * R; sp = Lo + 0.5 * R
        else:
            dr = 1 if (c > (H + Lo) / 2 and tl < th) else (-1 if (c < (H + Lo) / 2 and th < tl) else 0)
            lvl = H - ret * R if dr > 0 else Lo + ret * R; sp = Lo - TICK if dr > 0 else H + TICK
        if not dr: return
        tg = H + TICK if dr > 0 else Lo - TICK
        for i in range(b, e + 1):
            if (dr > 0 and mk.h[i] >= tg + TICK) or (dr < 0 and mk.l[i] <= tg - TICK): return            # target reached before the pullback
            if (dr > 0 and mk.l[i] <= lvl - TICK) or (dr < 0 and mk.h[i] >= lvl + TICK):
                ent = min(mk.o[i], lvl) if dr > 0 else max(mk.o[i], lvl); x, _ = walk(mk, i, e, dr, sp, tg)
                return [(dr, ent, x, 2, mk.m[i])]
    return intraday(f)


@strat('B38', 'IB continuation', 'edgeful IB model: low first + top-quartile close + green, limit at 25%, stop at IB mid (YouTube x-R4VxV-7K4)',
       '2025-06-01', end=720)
def b38(end=720): return b37(end=end, edge=True)


@strat('B39', 'RSI reversal', 'Trading Strategy Testing RSI(14) 30/70 hourly, 1 ATR stop / 1.5 ATR target (YouTube PqRLE3M9HoQ)', '2020-01-01', n=14,
       sk=1.0, tk=1.5)
def b39(n=14, sk=1.0, tk=1.5):
    def prep(mk):
        def m():
            b = full_bars(mk, 60); r = rsi(b['c'], n)
            pc = np.r_[b['c'][0], b['c'][:-1]]; tr = np.maximum(b['h'] - b['l'], np.maximum(abs(b['h'] - pc), abs(b['l'] - pc)))
            return dict(b=b, r=r, a=pd.Series(tr).rolling(n).mean().values)
        return cache(('b39', mk.name, n), m)
    def f(k, mk, i0, i1, D):
        z = prep(mk); b = z['b']; a = np.searchsorted(b['st'], i0); e = np.searchsorted(b['st'], i1, side='right') - 1; out = []; busy = -1
        for q in range(max(a, n + 1), e):
            if b['en'][q] <= busy or b['en'][q] >= i1 or np.isnan(z['a'][q]): continue
            dr = 1 if z['r'][q] < 30 else (-1 if z['r'][q] > 70 else 0)
            if not dr: continue
            ent = mk.ac[b['en'][q]]; A = z['a'][q]; x, xi = mk.ac[i1], i1
            for i in range(b['en'][q] + 1, i1 + 1):
                if (dr > 0 and mk.al[i] <= ent - sk * A) or (dr < 0 and mk.ah[i] >= ent + sk * A): x, xi = ent - dr * sk * A, i; break
                if (dr > 0 and mk.ah[i] >= ent + tk * A + TICK) or (dr < 0 and mk.al[i] <= ent - tk * A - TICK): x, xi = ent + dr * tk * A, i; break
            out.append((dr, ent, x, 2, mk.m[b['en'][q]])); busy = xi
        return out
    return intraday(f)


@strat('B40', 'ORB + filters', 'QuantCrawler MNQ ORB: 15-min range, Mon-Thu, 1.5R, flat 11:00 (YouTube cje0E2ZcOo8)', '2025-06-01', orm=15, rr=1.5,
       flat=660)
def b40(orm=15, rr=1.5, flat=660):
    def f(k, mk, i0, i1, D):
        if D.date.iat[k].weekday() == 4: return
        b, fl = at(mk, i0, i1, 570 + orm), at(mk, i0, i1, flat - 1)
        if b is None or fl is None: return
        H, Lo = mk.h[i0:b].max(), mk.l[i0:b].min()
        for s, e, o, h, l, c in bars(mk, b, fl, 5):
            if c > H or c < Lo:
                dr = 1 if c > H else -1; sp = Lo if dr > 0 else H; R = abs(c - sp)
                if R <= 0 or e >= fl: return
                x, _ = walk(mk, e + 1, fl, dr, sp, c + dr * rr * R); return [(dr, c, x, 2, mk.m[s])]
    return intraday(f)
