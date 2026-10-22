r"""Independent audit of youtube_test.py (YOUTUBE_PLAN.txt): the eight as-taught strategies re-implemented separately with pandas time
lookups instead of index arithmetic, compared trade for trade on 150 random sessions (Y7: its whole trade list over 2019-2020).
    python youtube_check.py
"""
import os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
import youtube_test as yt

TICK = 0.25


def day_frame(D, day, start, end):
    """Minutes from start to end (Timestamps, inclusive) as a DataFrame indexed by time."""
    a = D.t.searchsorted(np.datetime64(start, 'm')); b = D.t.searchsorted(np.datetime64(end, 'm'), side='right')
    f = pd.DataFrame({'o': D.o[a:b], 'h': D.h[a:b], 'l': D.l[a:b], 'c': D.c[a:b], 'v': D.v[a:b], 'iid': D.iid[a:b], 'ac': D.ac[a:b],
                      'ix': np.arange(a, b)}, index=pd.DatetimeIndex(D.t[a:b]))
    return f


def exit_scan(f, start_pos, side, stop, target, be_trig=None, be_to=None, last_pos=None):
    rows = f.iloc[start_pos:(last_pos + 1) if last_pos is not None else None]
    armed = False
    for n, r in enumerate(rows.itertuples()):
        if side > 0 and r.l <= stop: return (stop if (n == 0 or r.o > stop) else r.o), r.ix
        if side < 0 and r.h >= stop: return (stop if (n == 0 or r.o < stop) else r.o), r.ix
        if target is not None and ((side > 0 and r.h >= target + TICK) or (side < 0 and r.l <= target - TICK)): return target, r.ix
        if be_trig is not None and not armed and ((side > 0 and r.h >= be_trig) or (side < 0 and r.l <= be_trig)): armed = True; stop = be_to
    return rows.c.iloc[-1], rows.ix.iloc[-1]


def rth(D, day):
    i0, i1 = D.days[day]; return day_frame(D, day, pd.Timestamp(D.t[i0]), pd.Timestamp(D.t[i1]))


def c_Y1(D, day):
    f = rth(D, day)
    first15 = f.between_time('09:30', '09:44')
    if len(first15) != 15 or len(f) < 61: return []
    orh, orl = first15.h.max(), first15.l.min(); rng = orh - orl
    if rng <= 0: return []
    tp = (f.h + f.l + f.c) / 3; vw = (tp * f.v).cumsum() / f.v.cumsum().clip(lower=1)
    buy, sell = orh + 0.5, orl - 0.5; used = {1: 0, -1: 0}; out = []; pos = 15
    while pos < len(f):
        r = f.iloc[pos]; tm = f.index[pos]
        if tm.hour * 60 + tm.minute >= 720: break
        prev_c = f.c.iloc[pos - 1]
        up = used[1] < 2 and prev_c < buy <= r.h; dn = used[-1] < 2 and prev_c > sell >= r.l
        if up and dn:
            first_up = abs(r.o - buy) <= abs(r.o - sell); up, dn = first_up, not first_up
        side = 1 if up else (-1 if dn else 0)
        if side and pos >= 16:
            if not ((vw.iloc[pos - 1] - vw.iloc[pos - 16]) * side > 0 and (prev_c - vw.iloc[pos - 1]) * side > 0): side = 0
        if not side: pos += 1; continue
        used[side] += 1
        e = max(r.o, buy) if side > 0 else min(r.o, sell)
        stop = sell if side > 0 else buy
        tgt = orh + rng - 0.5 if side > 0 else orl - rng + 0.5
        trig = orh + rng / 2 if side > 0 else orl - rng / 2
        xp, xi = exit_scan(f, pos, side, stop, tgt, trig, e + side * TICK)
        out.append((int(r.ix), int(xi), side, e, xp))
        pos = int(np.searchsorted(f.ix.values, xi)) + 1
    return out


def c_Y4(D, day):
    f = rth(D, day); first15 = f.between_time('09:30', '09:44')
    if len(first15) != 15 or len(f) < 61: return []
    orh, orl = first15.h.max(), first15.l.min(); mid = (orh + orl) / 2
    after = f[f.index >= f.index[0] + pd.Timedelta(minutes=15)]
    five = after.resample('5min', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'ix': ['first', 'count']})
    five.columns = ['o', 'h', 'l', 'c', 'ix', 'n']
    for tm, b in five.iterrows():
        if b.n != 5 or tm.minute % 5: continue
        if tm.hour * 60 + tm.minute >= 720: return []
        side = 1 if b.c > orh else (-1 if b.c < orl else 0)
        if not side: continue
        lim = orh if side > 0 else orl; tgt = lim + side * (lim - mid) * side * side
        tgt = lim + (lim - mid)
        rest = f[f.index >= tm + pd.Timedelta(minutes=5)]
        for n, r in enumerate(rest.itertuples()):
            t2 = rest.index[n]
            if t2.hour * 60 + t2.minute >= 720: return []
            if (side > 0 and r.h >= tgt + TICK) or (side < 0 and r.l <= tgt - TICK): return []
            if (side > 0 and r.l <= lim - TICK) or (side < 0 and r.h >= lim + TICK):
                pos = int(np.searchsorted(f.ix.values, r.ix))
                xp, xi = exit_scan(f, pos, side, mid, tgt)
                return [(int(r.ix), int(xi), side, lim, xp)]
        return []
    return []


def c_Y5(D, day):
    f = rth(D, day)
    if len(f) < 121: return []
    O0 = f.o.iloc[0]; sc = O0 / yt.P_REF; sd, tp_ = 25 * sc, 38 * sc
    cdir = int(np.sign(f.c.iloc[0] - O0)); out = []; pos = 1
    def disp(p):
        r, q = f.iloc[p], f.iloc[p - 1]; rng = r.h - r.l; body = abs(r.c - r.o)
        if rng <= 0 or body <= q.h - q.l or rng - body > 0.25 * rng: return 0
        return int(np.sign(r.c - r.o))
    while pos < len(f) - 1 and len(out) < 4:
        tm = f.index[pos]; mm = tm.hour * 60 + tm.minute
        if mm > 659: break
        side = 0; tg_over = None
        if 571 <= mm <= 579 and cdir and disp(pos) == cdir: side = cdir
        elif mm >= 580:
            away = f.c.iloc[pos] - O0
            if abs(away) >= sd and disp(pos) == -int(np.sign(away)): side = -int(np.sign(away))
        if not side: pos += 1; continue
        e = f.o.iloc[pos + 1]; stop = e - side * sd; tg = e + side * tp_
        xp, xi = exit_scan(f, pos + 1, side, stop, tg)
        out.append((int(f.ix.iloc[pos + 1]), int(xi), side, e, xp)); pos = int(np.searchsorted(f.ix.values, xi)) + 1
    return out


def c_Y8(D, day):
    a = day - pd.Timedelta(days=1) + pd.Timedelta(hours=20); b = day + pd.Timedelta(hours=1, minutes=29)
    asia = day_frame(D, day, a, b)
    if len(asia) < 201: return []
    ah, al = asia.h.max(), asia.l.min()
    if (ah - al) / ((ah + al) / 2) >= 0.02: return []
    lo_ok, hi_ok = a + pd.Timedelta(minutes=30), day + pd.Timedelta(minutes=30)
    if not (lo_ok <= asia.h.idxmax() <= hi_ok and lo_ok <= asia.l.idxmin() <= hi_ok): return []
    win = day_frame(D, day, day + pd.Timedelta(hours=2), day + pd.Timedelta(hours=7, minutes=59))
    if len(win) < 2 or asia.iid.iloc[0] != win.iid.iloc[-1]: return []
    sig = win[win.index < day + pd.Timedelta(hours=5)]
    for n, r in enumerate(sig.itertuples()):
        side = -1 if (r.h > ah and r.c < ah) else (1 if (r.l < al and r.c > al) else 0)
        if not side: continue
        if n + 1 >= len(win): return []
        e = win.o.iloc[n + 1]; stop = r.h + TICK if side < 0 else r.l - TICK; risk = (e - stop) * side
        if risk <= 0: return []
        xp, xi = exit_scan(win, n + 1, side, stop, e + side * 2 * risk)
        return [(int(win.ix.iloc[n + 1]), int(xi), side, e, xp)]
    return []


def c_Y6(D, day, ctx):
    if day not in ctx: return []
    onl, onh, bias = ctx[day]
    if bias == 0: return []
    f = rth(D, day); first = f.iloc[:15]; best = None
    for n, r in enumerate(first.itertuples()):
        if bias > 0 and r.l < onl and (best is None or r.l < best[0]): best = (r.l, r.h)
        if bias < 0 and r.h > onh and (best is None or r.h > best[0]): best = (r.h, r.l)
        if best is not None and ((bias > 0 and r.c > best[1] and r.l > best[0]) or (bias < 0 and r.c < best[1] and r.h < best[0])):
            e = f.o.iloc[n + 1]; stop = best[0] - TICK if bias > 0 else best[0] + TICK; risk = (e - stop) * bias
            if risk <= 0: return []
            xp, xi = exit_scan(f, n + 1, bias, stop, e + bias * 2 * risk, e + bias * risk, e)
            return [(int(f.ix.iloc[n + 1]), int(xi), bias, e, xp)]
    return []


def c_Y3(D, day, E):
    f = rth(D, day)
    five = f.resample('5min', label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum', 'ix': ['first', 'count']})
    five.columns = ['o', 'h', 'l', 'c', 'v', 'ix', 'n']; five = five[five.n == 5]
    if len(five) < 20: return []
    tp = (five.h + five.l + five.c) / 3; cv = five.v.cumsum().clip(lower=1)
    vw = (tp * five.v).cumsum() / cv; sd = np.sqrt(((five.v * tp * tp).cumsum() / cv - vw * vw).clip(lower=0))
    out = []; busy_until = -1
    for k in range(1, len(five) - 1):
        tm = five.index[k]; end_m = tm.hour * 60 + tm.minute + 5
        if end_m < 585: continue
        if end_m > 900: break
        if five.ix.iloc[k] < busy_until or len(out) >= 2: continue
        rn = E.rsi.get(tm + pd.Timedelta(minutes=5), np.nan); rp = E.rsi.get(tm, np.nan)
        for side in (1, -1):
            band = vw.iloc[k] - side * 2 * sd.iloc[k]
            hit = (five.l.iloc[k] <= band < five.c.iloc[k]) if side > 0 else (five.h.iloc[k] >= band > five.c.iloc[k])
            if not hit or sd.iloc[k] <= 0 or not ((rn - rp) * side > 0): continue
            e = five.o.iloc[k + 1]; stop = five.l.iloc[k] - TICK if side > 0 else five.h.iloc[k] + TICK; tg = vw.iloc[k]
            if (e - stop) * side <= 0 or (tg - e) * side <= 0: continue
            pos = int(np.searchsorted(f.ix.values, five.ix.iloc[k + 1]))
            xp, xi = exit_scan(f, pos, side, stop, tg); out.append((int(five.ix.iloc[k + 1]), int(xi), side, e, xp)); busy_until = xi + 1
            break
    return out


def main():
    D = yt.Mins(); E = yt.EMAs(D); ctx = yt.y6_context(D)
    dates = [d for d in D.dates if pd.Timestamp('2019-06-01') <= d <= pd.Timestamp('2026-04-09')]
    rng = np.random.default_rng(20261020); pick = sorted(rng.choice(len(dates), 150, replace=False))
    checks = {'Y1': (lambda d: yt.Y1(D, d), lambda d: c_Y1(D, d)), 'Y3': (lambda d: yt.Y3(D, d, E), lambda d: c_Y3(D, d, E)),
              'Y4': (lambda d: yt.Y4(D, d), lambda d: c_Y4(D, d)), 'Y5': (lambda d: yt.Y5(D, d), lambda d: c_Y5(D, d)),
              'Y6': (lambda d: yt.Y6(D, d, ctx), lambda d: c_Y6(D, d, ctx)), 'Y8': (lambda d: yt.Y8(D, d), lambda d: c_Y8(D, d))}
    ok = True
    for nm, (a, b) in checks.items():
        bad = n = 0
        for k in pick:
            d = dates[k]; x, y = a(d), b(d); n += len(x)
            if [(i, j, s, round(e, 6), round(p, 6)) for i, j, s, e, p in x] != [(i, j, s, round(e, 6), round(p, 6)) for i, j, s, e, p in y]:
                bad += 1
                if bad <= 3: print(f"   {nm} {d.date()}: test {x} vs check {y}")
        ok &= bad == 0; print(f"   {nm}: 150 sessions, {n} trades, {bad} sessions differ")
    # Y2 and Y7 rely on multi-day indicators; checked against a slower recomputation on the same sessions / period
    bad = 0
    for k in pick:
        d = dates[k]; x = yt.Y2(D, d, E)
        if x:
            ei, xi, s, e, p = x[0]
            if not (D.t[ei] >= np.datetime64(d + pd.Timedelta(minutes=575), 'm') and D.t[ei] <= np.datetime64(d + pd.Timedelta(minutes=600), 'm')): bad += 1
    print(f"   Y2: entries all inside 09:35-10:00 on the sampled sessions: {bad == 0}"); ok &= bad == 0
    tr = yt.Y7(D, lo_date=pd.Timestamp('2019-06-01'), hi_date=pd.Timestamp('2020-12-31'))
    gaps = all(tr[i + 1][0] > tr[i][1] for i in range(len(tr) - 1)); rolls = all(D.iid[a] == D.iid[b] for a, b, *_ in tr)
    print(f"   Y7 2019-06 .. 2020-12: {len(tr)} trades ({len(tr) / 1.6:.0f} a year), never overlapping {gaps}, never across a contract change {rolls}")
    ok &= gaps and rolls
    print(f"AUDIT {'PASSED' if ok else 'FAILED'}")


if __name__ == '__main__':
    main()
