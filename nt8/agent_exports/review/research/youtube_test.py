r"""YouTube strategies on MNQ, per YOUTUBE_PLAN.txt (pre-registered 2026-10-20). Run from the research folder:
    python youtube_test.py smoke     2019-06 .. 2019-09 trade counts only
    python youtube_test.py run       tests A (as taught), B (walk-forward over the variants), C (addition to NoiseArea)
Data: Databento MNQ 1-minute bars in D:\dev\trading\MNQPropBook\out (cache in regime_work\mnq_1m.npz).
"""
import os, sys, ctypes
import numpy as np, pandas as pd
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)

TICK = 0.25
SRC = [r'D:\dev\trading\MNQPropBook\out\mnq_1m_raw.parquet', r'D:\dev\trading\MNQPropBook\out\mnq_1m_2026.parquet']
CACHE = os.path.join(HERE, 'regime_work', 'mnq_1m.npz')
P_REF = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
C_SIDE = 0.5 / P_REF                                     # one tick + $0.50 per side at today's price, as a return


# ------------------------------------------------------------------------------------------------ data
def load_1m():
    if os.path.exists(CACHE):
        z = np.load(CACHE); return {k: z[k] for k in z.files}
    a = pd.read_parquet(SRC[0]).reset_index()[['ts_event', 'instrument_id', 'open', 'high', 'low', 'close', 'volume']]
    b = pd.read_parquet(SRC[1])[['ts_event', 'instrument_id', 'open', 'high', 'low', 'close', 'volume']]
    df = pd.concat([a, b]).drop_duplicates('ts_event').sort_values('ts_event')
    t = pd.to_datetime(df.ts_event, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None)
    iid = df.instrument_id.values.astype(np.int64); o, h, l, c = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
    # back-adjusted copy for indicators only (EMA, RSI): remove the price jump at every contract change
    chg = np.where(iid[1:] != iid[:-1])[0] + 1
    cum = np.zeros(len(c))
    for k in chg:
        cum[:k] += o[k] - c[k - 1]                                                   # earlier bars shifted onto the later contract
    d = dict(t=t.values.astype('datetime64[m]'), iid=iid, o=o, h=h, l=l, c=c, v=df.volume.values.astype(float), shift=cum)
    np.savez_compressed(CACHE, **d); return d


class Mins:
    """1-minute arrays plus per-session index ranges."""
    def __init__(self):
        d = load_1m(); self.__dict__.update(d)
        tt = pd.DatetimeIndex(self.t); self.m = (tt.hour * 60 + tt.minute).values; self.date = tt.normalize().values.astype('datetime64[D]')
        self.ac = self.c + self.shift; self.ah = self.h + self.shift; self.al = self.l + self.shift; self.ao = self.o + self.shift
        rth = (self.m >= 570) & (self.m < 960)
        self.days = {}                                                                # RTH date -> (i0, i1) inclusive
        idx = np.where(rth)[0]; dts = self.date[idx]
        starts = np.r_[0, np.where(dts[1:] != dts[:-1])[0] + 1]; ends = np.r_[starts[1:] - 1, len(idx) - 1]
        for s, e in zip(starts, ends):
            i0, i1 = idx[s], idx[e]
            if self.m[i0] == 570 and i1 - i0 >= 200 and self.iid[i0] == self.iid[i1]: self.days[pd.Timestamp(dts[s])] = (i0, i1)
        self.dates = sorted(self.days)

    def at(self, day, minute):
        """Index of the bar starting at 'minute' on RTH date 'day', or None."""
        i0, i1 = self.days[day]; k = i0 + (minute - 570)
        return k if i0 <= k <= i1 and self.m[k] == minute else (lambda w: int(w[0]) + i0 if len(w) else None)(np.where(self.m[i0:i1 + 1] == minute)[0])


def walk(D, i0, i_last, dr, entry, stp, tgt, be_trig=None, be_to=None, check_entry_bar=True):
    """Exit (price, index) on 1-minute bars from i0 to i_last: stop first (the open if it gapped through after the entry bar), target as
    a limit traded through by a tick, break-even move from the bar after the trigger, else the close of i_last."""
    be_armed = False
    for i in range(i0, i_last + 1):
        if i > i0 or check_entry_bar:
            if dr > 0 and D.l[i] <= stp: return (stp if (i == i0 or D.o[i] > stp) else D.o[i]), i
            if dr < 0 and D.h[i] >= stp: return (stp if (i == i0 or D.o[i] < stp) else D.o[i]), i
            if tgt is not None and ((dr > 0 and D.h[i] >= tgt + TICK) or (dr < 0 and D.l[i] <= tgt - TICK)): return tgt, i
        if be_armed: pass
        if be_trig is not None and not be_armed and ((dr > 0 and D.h[i] >= be_trig) or (dr < 0 and D.l[i] <= be_trig)):
            be_armed = True; stp = be_to
    return D.c[i_last], i_last


def vwap_arrays(D, i0, i1):
    tp = (D.h[i0:i1 + 1] + D.l[i0:i1 + 1] + D.c[i0:i1 + 1]) / 3; v = D.v[i0:i1 + 1]
    cv = np.cumsum(v); vw = np.cumsum(tp * v) / np.maximum(cv, 1)
    return vw


# ------------------------------------------------------------------------------------------------ Y1 Bull Barbie 15-minute ORB
def Y1(D, day, vwap_filter=True, tgt_mult=1.0):
    i0, i1 = D.days[day]; o, h, l, c = D.o, D.h, D.l, D.c
    if i1 - i0 < 60 or D.m[i0 + 14] != 584: return []
    orh, orl = h[i0:i0 + 15].max(), l[i0:i0 + 15].min(); orr = orh - orl
    if orr <= 0: return []
    vw = vwap_arrays(D, i0, i1); bl, sl = orh + 2 * TICK, orl - 2 * TICK
    cnt = {1: 0, -1: 0}; trades = []; i = i0 + 15
    while i <= i1 and D.m[i] < 720:
        k = i - i0; up = cnt[1] < 2 and c[i - 1] < bl <= h[i]; dn = cnt[-1] < 2 and c[i - 1] > sl >= l[i]
        if up and dn: up, dn = (abs(o[i] - bl) <= abs(o[i] - sl)), not (abs(o[i] - bl) <= abs(o[i] - sl))
        dr = 1 if up else (-1 if dn else 0)
        if dr and vwap_filter and k >= 16:
            ok = (vw[k - 1] - vw[k - 16]) * dr > 0 and (c[i - 1] - vw[k - 1]) * dr > 0
            if not ok: dr = 0
        if dr == 0: i += 1; continue
        cnt[dr] += 1; lvl = bl if dr > 0 else sl; e = max(o[i], lvl) if dr > 0 else min(o[i], lvl)
        stp = sl if dr > 0 else bl
        tgt = (orh + tgt_mult * orr - 2 * TICK) if dr > 0 else (orl - tgt_mult * orr + 2 * TICK)
        trig = orh + 0.5 * orr if dr > 0 else orl - 0.5 * orr
        xp, xi = walk(D, i, i1, dr, e, stp, tgt, trig, e + dr * TICK)
        trades.append((i, xi, dr, e, xp)); i = xi + 1
    return trades


# ------------------------------------------------------------------------------------------------ Y4 Pasha 15-minute ORB, close then retest
def Y4(D, day, r_mult=1.0):
    i0, i1 = D.days[day]; h, l, c = D.h, D.l, D.c
    if i1 - i0 < 60 or D.m[i0 + 14] != 584: return []
    orh, orl = h[i0:i0 + 15].max(), l[i0:i0 + 15].min(); mid = (orh + orl) / 2
    b = bars5(D, i0 + 15, i1)                                                      # 5-minute bars 09:45-09:50, 09:50-09:55, ...
    for row in b:
        s = int(row[0])
        if D.m[s] >= 720: return []
        cl = row[4]
        dr = 1 if cl > orh else (-1 if cl < orl else 0)
        if not dr: continue
        lim = orh if dr > 0 else orl; risk = (lim - mid) * dr; tgt = lim + dr * r_mult * risk
        for j in range(s + 5, i1 + 1):
            if D.m[j] >= 720: return []
            if (dr > 0 and h[j] >= tgt + TICK) or (dr < 0 and l[j] <= tgt - TICK): return []   # ran to the target unfilled
            if (dr > 0 and l[j] <= lim - TICK) or (dr < 0 and h[j] >= lim + TICK):
                xp, xi = walk(D, j, i1, dr, lim, mid, tgt)
                return [(j, xi, dr, lim, xp)]
        return []
    return []


# ------------------------------------------------------------------------------------------------ 5-minute helpers
def bars5(D, i0, i1):
    """5-minute bars from 1-minute index i0 (aligned on :00/:05) to i1: arrays of start index, o, h, l, c, v (complete bars only)."""
    out = []
    j = i0
    while j + 4 <= i1:
        if D.m[j] % 5 == 0 and D.m[j + 4] == D.m[j] + 4:
            out.append((j, D.o[j], D.h[j:j + 5].max(), D.l[j:j + 5].min(), D.c[j + 4], D.v[j:j + 5].sum())); j += 5
        else: j += 1
    return np.array(out) if out else np.zeros((0, 6))


class EMAs:
    """50-period EMAs of 15-, 30- and 60-minute closes (back-adjusted), RSI(14) of 5-minute closes, hourly bars - all causal."""
    def __init__(self, D):
        t = pd.DatetimeIndex(D.t); s = pd.Series(D.ac, index=t)
        self.e = {}
        for f in ('15min', '30min', '60min'):
            cl = s.resample(f, label='left', closed='left').last().dropna()
            ema = cl.ewm(span=50, adjust=False).mean()
            self.e[f] = (cl.index + pd.Timedelta(f), ema.values)                  # value known at the bar's END time
        c5 = s.resample('5min', label='left', closed='left').last().dropna()
        d = c5.diff(); up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(); dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        self.rsi = pd.Series((100 - 100 / (1 + up / dn.replace(0, np.nan))).values, index=c5.index + pd.Timedelta('5min'))

    def at(self, f, when):
        """(EMA value, previous EMA value) of timeframe f known at time 'when'."""
        ends, vals = self.e[f]; k = ends.searchsorted(when, side='right') - 1
        return (vals[k], vals[k - 1]) if k >= 1 else (np.nan, np.nan)


# ------------------------------------------------------------------------------------------------ Y2 EMA stack + counter-trend FVG inversion
def Y2(D, day, E, tgt_r=2.0, stop='swing'):
    i0, i1 = D.days[day]
    p0 = int(D.t.searchsorted(np.datetime64(day + pd.Timedelta(hours=8), 'm')))
    if i0 - p0 < 60 or D.iid[p0] != D.iid[i0]: return []
    pre = [p0]
    when = pd.Timestamp(day) + pd.Timedelta(minutes=570)
    vals = [E.at(f, when) for f in ('15min', '30min', '60min')]
    if any(np.isnan(v[0]) or np.isnan(v[1]) for v in vals): return []
    last5 = D.ac[i0 - 1] if D.m[i0 - 1] == 569 else np.nan
    (e15, p15), (e30, p30), (e60, p60) = vals
    if last5 > max(e15, e30, e60) and e15 > e30 > e60 and e15 > p15 and e30 > p30 and e60 > p60: bias = 1
    elif last5 < min(e15, e30, e60) and e15 < e30 < e60 and e15 < p15 and e30 < p30 and e60 < p60: bias = -1
    else: return []
    b = bars5(D, pre[0], i1)
    if len(b) < 20: return []
    st = b[:, 0].astype(int); O5, H5, L5, C5 = b[:, 1], b[:, 2], b[:, 3], b[:, 4]
    m5 = D.m[st]
    for k in range(len(b)):
        if m5[k] < 570: continue
        if m5[k] > 595: break                                           # break bars 09:30-09:35 .. 09:55-10:00
        fvg = None
        for j in range(k - 1, 1, -1):                                  # latest counter-trend FVG formed before this bar and not yet broken
            if m5[j - 2] < 480: break
            if bias > 0 and L5[j - 2] > H5[j] and not (C5[j + 1:k] > L5[j - 2]).any(): fvg = (j, L5[j - 2]); break
            if bias < 0 and H5[j - 2] < L5[j] and not (C5[j + 1:k] < H5[j - 2]).any(): fvg = (j, H5[j - 2]); break
        if fvg is None: continue
        j, lvl = fvg
        if (bias > 0 and C5[k] > lvl) or (bias < 0 and C5[k] < lvl):
            ei = st[k] + 5
            if ei > i1: return []
            e = D.o[ei]
            if stop == 'swing': stp = (L5[j - 2:k + 1].min() - TICK) if bias > 0 else (H5[j - 2:k + 1].max() + TICK)
            else: stp = (L5[k] - TICK) if bias > 0 else (H5[k] + TICK)
            risk = (e - stp) * bias
            if risk <= 0: return []
            xp, xi = walk(D, ei, i1, bias, e, stp, e + bias * tgt_r * risk)
            return [(ei, xi, bias, e, xp)]
    return []


# ------------------------------------------------------------------------------------------------ Y3 VWAP 2-sigma bounce
def Y3(D, day, E, use_rsi=True, stop='low'):
    i0, i1 = D.days[day]; b = bars5(D, i0, i1)
    if len(b) < 20: return []
    st = b[:, 0].astype(int); O5, H5, L5, C5, V5 = b[:, 1], b[:, 2], b[:, 3], b[:, 4], b[:, 5]
    tp = (H5 + L5 + C5) / 3; cv = np.cumsum(V5); vw = np.cumsum(tp * V5) / np.maximum(cv, 1)
    var = np.maximum(np.cumsum(V5 * tp * tp) / np.maximum(cv, 1) - vw * vw, 0); sd = np.sqrt(var)
    trades = []; nxt = 0
    for k in range(1, len(b) - 1):
        end_m = D.m[st[k]] + 5
        if end_m < 585: continue
        if end_m > 900: break
        if st[k] < nxt or len(trades) >= 2: continue
        when = pd.Timestamp(D.t[st[k]]) + pd.Timedelta(minutes=5)
        r_now = E.rsi.get(when, np.nan); r_prev = E.rsi.get(when - pd.Timedelta(minutes=5), np.nan)
        for dr in (1, -1):
            band = vw[k] - 2 * sd[k] if dr > 0 else vw[k] + 2 * sd[k]
            touch = (L5[k] <= band and C5[k] > band) if dr > 0 else (H5[k] >= band and C5[k] < band)
            if not touch or sd[k] <= 0: continue
            if use_rsi and not ((r_now - r_prev) * dr > 0): continue
            ei = st[k + 1]; e = D.o[ei]; tg = vw[k]
            stp = ((L5[k] - TICK) if dr > 0 else (H5[k] + TICK)) if stop == 'low' else (vw[k] - dr * 3 * sd[k])
            if (e - stp) * dr <= 0 or (tg - e) * dr <= 0: continue
            xp, xi = walk(D, ei, i1, dr, e, stp, tg); trades.append((ei, xi, dr, e, xp)); nxt = xi + 1; break
    return trades


# ------------------------------------------------------------------------------------------------ Y5 opening fair price
def Y5(D, day, open_tf=1, rev_target='fixed'):
    i0, i1 = D.days[day]; o, h, l, c = D.o, D.h, D.l, D.c
    if i1 - i0 < 120: return []
    O0 = o[i0]; scale = O0 / P_REF; sd_pts, tp_pts = 25 * scale, 38 * scale
    oc = c[i0 + open_tf - 1] - O0; cdir = int(np.sign(oc))
    trades = []; i = i0 + open_tf
    def disp(k):
        rng = h[k] - l[k]; body = abs(c[k] - o[k])
        if rng <= 0 or body <= h[k - 1] - l[k - 1] or (rng - body) > 0.25 * rng: return 0
        return int(np.sign(c[k] - o[k]))
    while i <= i1 - 1 and len(trades) < 4:
        mm = D.m[i]
        if mm > 659: break
        dr = 0; tgt_override = None
        if mm <= 579 + (0 if open_tf == 1 else 0) and cdir and disp(i) == cdir and mm >= 570 + open_tf: dr = cdir
        elif mm >= 580:
            away = c[i] - O0
            if abs(away) >= sd_pts and disp(i) == -int(np.sign(away)):
                dr = -int(np.sign(away)); tgt_override = O0 if rev_target == 'open' else None
        if not dr: i += 1; continue
        ei = i + 1; e = o[ei]; stp = e - dr * sd_pts; tg = tgt_override if tgt_override is not None else e + dr * tp_pts
        if (tg - e) * dr <= 0: i += 1; continue
        xp, xi = walk(D, ei, i1, dr, e, stp, tg); trades.append((ei, xi, dr, e, xp)); i = xi + 1
    return trades


# ------------------------------------------------------------------------------------------------ Y6 open sweep and reclaim
def Y6(D, day, ctx, target='2R', bias_filter=True):
    i0, i1 = D.days[day]; o, h, l, c = D.o, D.h, D.l, D.c
    onl, onh, bias = ctx.get(day, (np.nan, np.nan, 0))
    if np.isnan(onl): return []
    dirs = [bias] if bias_filter else [1, -1]
    if bias_filter and bias == 0: return []
    ext = {1: None, -1: None}
    for i in range(i0, min(i0 + 15, i1)):
        for dr in dirs:
            if dr > 0 and l[i] < onl and (ext[1] is None or l[i] < ext[1][0]): ext[1] = (l[i], h[i])
            if dr < 0 and h[i] > onh and (ext[-1] is None or h[i] > ext[-1][0]): ext[-1] = (h[i], l[i])
            x = ext[dr]
            if x is not None and ((dr > 0 and c[i] > x[1] and l[i] > x[0]) or (dr < 0 and c[i] < x[1] and h[i] < x[0])):
                ei = i + 1; e = o[ei]; stp = x[0] - TICK if dr > 0 else x[0] + TICK; risk = (e - stp) * dr
                if risk <= 0: return []
                tg = e + dr * 2 * risk if target == '2R' else (onh if dr > 0 else onl)
                if (tg - e) * dr <= 0: return []
                xp, xi = walk(D, ei, i1, dr, e, stp, tg, e + dr * risk, e)
                return [(ei, xi, dr, e, xp)]
    return []


def y6_context(D):
    """{RTH date: (overnight low, overnight high, bias)} - overnight 18:00..09:29 of the same contract; bias from the prior RTH close
    against its 20-session average (back-adjusted)."""
    out = {}; closes = []
    for k, day in enumerate(D.dates):
        i0, i1 = D.days[day]
        prev_end = D.days[D.dates[k - 1]][1] if k else None
        if prev_end is not None:
            j = np.arange(prev_end + 1, i0)
            j = j[(D.m[j] >= 1080) | (D.m[j] < 570)]
            if len(j) and (D.iid[j] == D.iid[i0]).all():
                bias = 0
                if len(closes) >= 20:
                    sma = np.mean(closes[-20:]); bias = 1 if closes[-1] > sma else (-1 if closes[-1] < sma else 0)
                out[day] = (D.l[j].min(), D.h[j].max(), bias)
        closes.append(D.ac[i1])
    return out


# ------------------------------------------------------------------------------------------------ Y8 Asia range, London sweep
def Y8(D, day, target='2R'):
    a = np.datetime64(day - pd.Timedelta(days=1) + pd.Timedelta(hours=20), 'm'); b = np.datetime64(day + pd.Timedelta(minutes=89), 'm')
    lo = D.t.searchsorted(a); hi = D.t.searchsorted(b, side='right') - 1
    if hi - lo < 200: return []
    seg = slice(lo, hi + 1); H, L = D.h[seg], D.l[seg]
    ah, al = H.max(), L.min(); mid = (ah + al) / 2
    if (ah - al) / mid >= 0.02: return []
    th = D.t[lo + int(np.argmax(H))]; tl = D.t[lo + int(np.argmin(L))]
    lo_ok, hi_ok = np.datetime64(day - pd.Timedelta(days=1) + pd.Timedelta(hours=20, minutes=30), 'm'), np.datetime64(day + pd.Timedelta(minutes=30), 'm')
    if not (lo_ok <= th <= hi_ok and lo_ok <= tl <= hi_ok): return []
    s0 = D.t.searchsorted(np.datetime64(day + pd.Timedelta(hours=2), 'm')); s1 = D.t.searchsorted(np.datetime64(day + pd.Timedelta(hours=5), 'm'))
    xend = D.t.searchsorted(np.datetime64(day + pd.Timedelta(hours=7, minutes=59), 'm'), side='right') - 1
    if s1 <= s0 or xend <= s1 or D.iid[lo] != D.iid[xend]: return []
    for i in range(s0, s1):
        dr = -1 if (D.h[i] > ah and D.c[i] < ah) else (1 if (D.l[i] < al and D.c[i] > al) else 0)
        if not dr: continue
        ei = i + 1; e = D.o[ei]; stp = D.h[i] + TICK if dr < 0 else D.l[i] - TICK; risk = (e - stp) * dr
        if risk <= 0: return []
        tg = e + dr * (2.0 if target == '2R' else 1.5) * risk if target != 'range' else (al if dr < 0 else ah)
        if (tg - e) * dr <= 0: return []
        xp, xi = walk(D, ei, xend, dr, e, stp, tg)
        return [(ei, xi, dr, e, xp)]
    return []


# ------------------------------------------------------------------------------------------------ Y7 9/21 EMA pullback, 1-hour
def Y7(D, target_r=2.0, trail=False, lo_date=None, hi_date=None):
    t = pd.DatetimeIndex(D.t); hours = t.floor('h')
    key = hours.values; starts = np.r_[0, np.where(key[1:] != key[:-1])[0] + 1]; ends = np.r_[starts[1:] - 1, len(key) - 1]
    hkey = key[starts]
    HC = D.ac[ends]; HL = D.al[starts]; HH = np.array([D.ah[s:e + 1].max() for s, e in zip(starts, ends)])
    HLo = np.array([D.al[s:e + 1].min() for s, e in zip(starts, ends)]); rawL = np.array([D.l[s:e + 1].min() for s, e in zip(starts, ends)])
    rawH = np.array([D.h[s:e + 1].max() for s, e in zip(starts, ends)])
    e9 = pd.Series(HC).ewm(span=9, adjust=False).mean().values; e21 = pd.Series(HC).ewm(span=21, adjust=False).mean().values
    trades = []; k = 25; state = 0; armed = False            # state: side of the last 9/21 cross; armed after a close beyond both
    while k < len(starts) - 1:
        cross = 1 if (e9[k] > e21[k] and e9[k - 1] <= e21[k - 1]) else (-1 if (e9[k] < e21[k] and e9[k - 1] >= e21[k - 1]) else 0)
        if cross: state, armed = cross, False; k += 1; continue
        if lo_date is not None and pd.Timestamp(hkey[k]) < lo_date: k += 1; continue    # 2026-10-20 fix: hour keys, not minute keys
        if hi_date is not None and pd.Timestamp(hkey[k]) > hi_date: break
        dr = 0
        if state > 0 and armed and HLo[k] <= e21[k] and HC[k] > e9[k]: dr = 1
        elif state < 0 and armed and HH[k] >= e21[k] and HC[k] < e9[k]: dr = -1
        if state and not armed and (HC[k] - max(e9[k], e21[k]) if state > 0 else min(e9[k], e21[k]) - HC[k]) > 0:
            armed = True; k += 1; continue
        if not dr: k += 1; continue
        state, armed = 0, False                                   # one entry per crossover
        ei = starts[k + 1]
        if D.iid[ei] != D.iid[starts[k - 2]]: k += 1; continue
        e = D.o[ei]; stp = (rawL[k - 2:k + 1].min() - TICK) if dr > 0 else (rawH[k - 2:k + 1].max() + TICK)
        risk = (e - stp) * dr
        if risk <= 0: k += 1; continue
        tg = None if trail else e + dr * target_r * risk
        # walk minute by minute until stop / target / contract change / (trail) the EMA cross at an hour's close
        j = ei; xp = None; xi = None; hk = k + 1
        while j < len(D.t):
            if D.iid[j] != D.iid[ei]: xp, xi = D.c[j - 1], j - 1; break
            if dr > 0 and D.l[j] <= stp: xp, xi = (stp if (j == ei or D.o[j] > stp) else D.o[j]), j; break
            if dr < 0 and D.h[j] >= stp: xp, xi = (stp if (j == ei or D.o[j] < stp) else D.o[j]), j; break
            if tg is not None and ((dr > 0 and D.h[j] >= tg + TICK) or (dr < 0 and D.l[j] <= tg - TICK)): xp, xi = tg, j; break
            if trail and j == ends[hk]:
                if (e9[hk] - e21[hk]) * dr < 0 and j + 1 < len(D.t): xp, xi = D.o[j + 1], j + 1; break
                hk += 1
            if j == ends[min(hk, len(ends) - 1)] and not trail: hk += 1
            j += 1
        if xp is None: break
        trades.append((ei, xi, dr, e, xp))
        k = int(np.searchsorted(starts, xi, side='right'))                       # next signal from the hour after the exit
    return trades


# ------------------------------------------------------------------------------------------------ daily series and tests
def session_of(D, i):
    """RTH date a trade exiting at minute index i is booked on: after 16:00 -> the next weekday."""
    ts = pd.Timestamp(D.t[i]); d = ts.normalize()
    if ts.hour * 60 + ts.minute >= 960: d += pd.Timedelta(days=1)
    while d.weekday() >= 5: d += pd.Timedelta(days=1)
    return d


def to_daily(D, trades):
    rows = {}
    for (ei, xi, dr, e, xp) in trades:
        d = session_of(D, xi); px = D.o[ei]
        r = (xp - e) * dr / px - 2 * C_SIDE
        a = rows.setdefault(d, [0.0, 0, 0]); a[0] += r; a[1] += 1; a[2] += int(r > 0)
    return rows


VARIANTS = {
    'Y1': [dict(), dict(vwap_filter=False), dict(tgt_mult=2.0)],
    'Y2': [dict(), dict(tgt_r=3.0), dict(stop='candle')],
    'Y3': [dict(), dict(use_rsi=False), dict(stop='3sd')],
    'Y4': [dict(), dict(r_mult=2.0)],
    'Y5': [dict(), dict(open_tf=5), dict(rev_target='open')],
    'Y6': [dict(), dict(target='onx'), dict(bias_filter=False)],
    'Y7': [dict(), dict(target_r=3.0), dict(trail=True)],
    'Y8': [dict(), dict(target='range'), dict(target='1.5R')],
}
NAMES = {'Y1': 'V1 Bull Barbie 15-min ORB + VWAP', 'Y2': 'V2 EMA stack + FVG inversion', 'Y3': 'V5 VWAP 2-sigma bounce',
         'Y4': 'V8 Pasha 15-min ORB retest', 'Y5': 'V9 opening fair price', 'Y6': 'V12 open sweep and reclaim',
         'Y7': 'V13 9/21 EMA pullback (1 h)', 'Y8': 'V15 Asia range London sweep'}
CLAIM = {'Y1': '55% target / 22% stop', 'Y2': '73% win', 'Y4': '72% reach 1R / 25% stop', 'Y7': '73% win', 'Y5': '"break even live"'}


def run_strategy(D, E, ctx, name, prm, dates):
    if name == 'Y7':
        return Y7(D, lo_date=dates[0], hi_date=dates[-1] + pd.Timedelta(days=1), **prm)
    out = []
    for day in dates:
        try:
            if name == 'Y1': out += Y1(D, day, **prm)
            elif name == 'Y2': out += Y2(D, day, E, **prm)
            elif name == 'Y3': out += Y3(D, day, E, **prm)
            elif name == 'Y4': out += Y4(D, day, **prm)
            elif name == 'Y5': out += Y5(D, day, **prm)
            elif name == 'Y6': out += Y6(D, day, ctx, **prm)
            elif name == 'Y8': out += Y8(D, day, **prm)
        except Exception as ex:
            raise RuntimeError(f"{name} {prm} on {day.date()}: {ex}")
    return out


def series(D, trades, dates):
    rows = to_daily(D, trades); idx = pd.DatetimeIndex(dates)
    r = pd.Series([rows.get(d, [0, 0, 0])[0] for d in idx], index=idx); n = pd.Series([rows.get(d, [0, 0, 0])[1] for d in idx], index=idx)
    return r, n


def smoke():
    D = Mins(); E = EMAs(D); ctx = y6_context(D)
    dates = [d for d in D.dates if pd.Timestamp('2019-06-01') <= d <= pd.Timestamp('2019-09-30')]
    print(f"1-minute bars {len(D.t):,}, RTH sessions {len(D.dates)} ({D.dates[0].date()} .. {D.dates[-1].date()}); smoke window {len(dates)} sessions")
    for nm in VARIANTS:
        for prm in VARIANTS[nm]:
            tr = run_strategy(D, E, ctx, nm, prm, dates)
            print(f"   {nm} {prm}: {len(tr)} trades")


def main():
    import regime_test as rt, mnq_program as mp
    D = Mins(); E = EMAs(D); ctx = y6_context(D)
    dates = [d for d in D.dates if pd.Timestamp('2019-06-01') <= d <= pd.Timestamp('2026-04-09')]
    NA = mp.Data('MNQ'); na = pd.Series(NA.na, index=NA.dates); N = 2 * P_REF
    print(f"MNQ 1-minute data: {len(dates)} RTH sessions {dates[0].date()} .. {dates[-1].date()}; per micro at today's notional ${N:,.0f}")
    res = {}
    for nm in VARIANTS:
        runs = []
        for prm in VARIANTS[nm]:
            tr = run_strategy(D, E, ctx, nm, prm, dates); r, n = series(D, tr, dates); runs.append((prm, tr, r, n))
        res[nm] = runs
    yrs = sorted({d.year for d in dates})
    print("\n=== (A) AS TAUGHT, 2019-06 .. 2026-04-09 (EDGE: t >= 2.50, >= 5 of 8 years positive, positive at 2 ticks) ===")
    flags = {}
    for nm, runs in res.items():
        prm, tr, r, n = runs[0]; t = rt.tstat(r.values); yr = r.groupby(r.index.year).sum()
        pos_years = int((yr > 0).sum()); ex2 = r - n * (0.25 / P_REF) * 2
        trades_r = [((xp - e) * dr / D.o[ei] - 2 * C_SIDE) for (ei, xi, dr, e, xp) in tr]
        win = np.mean(np.array(trades_r) > 0) * 100 if tr else np.nan
        early = r[r.index < '2024-01-01']; edge = t >= 2.5 and pos_years >= 5 and ex2.sum() > 0
        flags[nm] = edge
        print(f"   {nm} {NAMES[nm]:34s} trades {len(tr):5d}  win {win:4.0f}%  ${r.sum() * N / (len(r) / 252):+7,.0f}/yr  t {t:+.2f}  "
              f"years + {pos_years}/{len(yr)}  2019-2023 t {rt.tstat(early.values):+.2f}  at 2 ticks ${ex2.sum() * N / (len(r) / 252):+,.0f}/yr  -> EDGE {edge}"
              + (f"   (claimed: {CLAIM[nm]})" if nm in CLAIM else ""))
        print("        by year $: " + " ".join(f"{y} {v * N:+,.0f}" for y, v in yr.items()))
    print("\n=== (B) WALK-FORWARD over the variants, OOS 2022-01 .. 2026-04-09 ===")
    oos_dates = [d for d in dates if d >= pd.Timestamp('2022-01-01')]
    for nm, runs in res.items():
        pick_r = pd.Series(0.0, index=pd.DatetimeIndex(oos_dates)); log = []
        for y in sorted({d.year for d in oos_dates}):
            best = None
            for prm, tr, r, n in runs:
                fit = r[r.index < f'{y}-01-01']; nfit = n[n.index < f'{y}-01-01'].sum()
                if nfit < 50: continue
                tt = rt.tstat(fit.values)
                if best is None or tt > best[0]: best = (tt, prm, r)
            if best is None: continue
            seg = best[2][(best[2].index >= f'{y}-01-01') & (best[2].index <= f'{y}-12-31')]
            pick_r.loc[seg.index] = seg.values; log.append(f"{y}:{best[1] or 'as taught'}")
        t = rt.tstat(pick_r.values)
        print(f"   {nm} OOS ${pick_r.sum() * N / (len(pick_r) / 252):+7,.0f}/yr  t {t:+.2f}  -> {'EDGE' if t >= 2.5 else 'no edge'}   picks {'; '.join(log)}")
    print("\n=== (C) ADDITION to NoiseArea (1 micro each), common dates; PASS needs bootstrap P < 0.00625 ===")
    for nm, runs in res.items():
        r = runs[0][2]; common = na.index.intersection(r.index); a = na.reindex(common).values; b = r.reindex(common).values
        pb = rt.boot_sharpe_diff(common, a + b, a)
        print(f"   {nm}: NoiseArea {rt.sharpe(a):+.2f} -> with it {rt.sharpe(a + b):+.2f} (corr {np.corrcoef(a, b)[0, 1]:+.2f}), P {pb:.3f} -> "
              f"{'PASS' if rt.sharpe(a + b) > rt.sharpe(a) and pb < 0.05 / 8 else 'no'}")
    np.savez_compressed(os.path.join(HERE, 'regime_work', 'youtube_series.npz'),
                        **{f"{nm}_{k}": runs[k][2].values for nm, runs in res.items() for k in range(len(runs))}, dates=np.array(dates, dtype='datetime64[D]'))
    import pickle; pickle.dump({nm: runs[0][1] for nm, runs in res.items()}, open(os.path.join(HERE, 'regime_work', 'youtube_trades.pkl'), 'wb'))
    print(f"\nSUMMARY: EDGE (as taught) {[n for n, f in flags.items() if f] or 'none'}")


if __name__ == '__main__':
    {'smoke': smoke, 'run': main}.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: print(__doc__))()
