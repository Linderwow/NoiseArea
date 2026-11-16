import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS

USD = 55751.0
TICK = 0.25
FIRST, LAST_ENTRY, CLOSE = 575, 930, 960


def wilder(x, n):
    out = np.full(len(x), np.nan); a = 1.0 / n; v = np.nan
    for i, xi in enumerate(x):
        if np.isnan(xi): out[i] = v; continue
        v = xi if np.isnan(v) else v + a * (xi - v); out[i] = v
    return out


def seg_cum(flag, cid, how='sum'):
    s = pd.Series(flag.astype(float)).groupby(cid)
    return (s.cumsum() if how == 'sum' else s.cummax()).to_numpy()


def derive(D):
    e50, e200, c, l, h, n = D['e50'], D['e200'], D['c'], D['l'], D['h'], len(D['c'])
    st = np.sign(e50 - e200); st0 = np.r_[0.0, st[:-1]]
    cross = (st != st0) & (st != 0); cross[0] = False
    cid = np.cumsum(cross); idx = np.arange(n)
    last = np.maximum.accumulate(np.where(cross, idx, 0)); bsc = idx - last
    xs = np.flatnonzero(cross); gaps = np.diff(np.r_[-10 ** 9, xs]); prevgap = np.zeros(n); prevgap[xs] = gaps
    prevgap = pd.Series(np.where(cross, prevgap, np.nan)).ffill().fillna(10 ** 9).to_numpy()
    pb = ((st > 0) & (l <= e50) & (c > e50) & (c > e200)) | ((st < 0) & (h >= e50) & (c < e50) & (c < e200))
    pbn = seg_cum(pb, cid)
    a_up = seg_cum((D['rsi'] > 80) & (st > 0), cid, 'max'); a_dn = seg_cum((D['rsi'] < 20) & (st < 0), cid, 'max')
    armed = np.where(st > 0, a_up, np.where(st < 0, a_dn, 0)) > 0
    sep = np.abs(e50 - e200) > 0.5 * D['atr']; sepn = seg_cum(sep & (bsc >= 3), cid)
    f = np.sign(D['e9'] - D['e21']); f0 = np.r_[0.0, f[:-1]]; fcross = (f != f0) & (f != 0); fcross[0] = False
    D.update(st=st, cross=cross, bsc=bsc, prevgap=prevgap, pb=pb, firstpull=pb & (pbn == 1) & (prevgap >= 12) & (bsc >= 1), armed=armed,
             sepfirst=sep & (bsc >= 3) & (sepn == 1), fst=f, fcross=fcross)
    return D


def load(nm):
    df = LS.full_bars(N.market(nm)).copy()
    t = pd.to_datetime(df.time); c = df.close.to_numpy(float); h = df.high.to_numpy(float); l = df.low.to_numpy(float)
    o = df.open.to_numpy(float); v = df.volume.to_numpy(float)
    m = (t.dt.hour * 60 + t.dt.minute).to_numpy()
    s = pd.Series(c); ema = lambda n: s.ewm(span=n, adjust=False).mean().to_numpy()
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    atr = wilder(tr, 14)
    up = h - np.r_[np.nan, h[:-1]]; dn = np.r_[np.nan, l[:-1]] - l
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        pdi = 100 * wilder(pdm, 14) / atr; mdi = 100 * wilder(mdm, 14) / atr
        dx = 100 * np.abs(pdi - mdi) / np.where(pdi + mdi > 0, pdi + mdi, np.nan)
    adx = wilder(dx, 14)
    d = np.diff(c, prepend=c[0]); g = wilder(np.where(d > 0, d, 0.0), 14); ls_ = wilder(np.where(d < 0, -d, 0.0), 14)
    with np.errstate(invalid='ignore', divide='ignore'): rsi = 100 - 100 / (1 + g / np.where(ls_ > 0, ls_, np.nan))
    hist = (ema(12) - ema(26)); hist = hist - pd.Series(hist).ewm(span=9, adjust=False).mean().to_numpy()
    vavg = pd.Series(v).rolling(20, min_periods=20).mean().shift(1).to_numpy()
    date = t.dt.normalize().to_numpy()
    rth = (m >= FIRST) & (m <= CLOSE)
    tp = (h + l + c) / 3; key = pd.Series(np.where(rth, date.astype('int64'), -1))
    num = pd.Series(np.where(rth, tp * v, 0.0)).groupby(key).cumsum().to_numpy(); den = pd.Series(np.where(rth, v, 0.0)).groupby(key).cumsum().to_numpy()
    vwap = np.where(rth & (den > 0), num / np.where(den > 0, den, 1), np.nan)
    hr = pd.DataFrame({'c': c}, index=t).resample('60min', label='right', closed='right').last().dropna()
    hstate = np.sign(hr.c.ewm(span=50, adjust=False).mean() - hr.c.ewm(span=200, adjust=False).mean()).to_numpy()
    j = np.searchsorted(hr.index.values, t.values, side='right') - 1
    htf = np.where(j >= 0, hstate[np.clip(j, 0, None)], 0.0)
    sc = pd.Series(c[m == CLOSE], index=pd.DatetimeIndex(date[m == CLOSE])).groupby(level=0).last()
    dstate = np.sign(sc - sc.rolling(20).mean())
    k = np.searchsorted(sc.index.values, date, side='left') - 1
    daily20 = np.where(k >= 0, dstate.to_numpy()[np.clip(k, 0, None)], 0.0)
    D = dict(nm=nm, t=t.to_numpy(), m=m, date=date, o=o, h=h, l=l, c=c, v=v, e9=ema(9), e20=ema(20), e21=ema(21), e50=ema(50),
             e200=ema(200), atr=atr, adx=adx, pdi=pdi, mdi=mdi, rsi=rsi, hist=hist, vavg=vavg, vwap=vwap, htf=htf,
             daily20=np.nan_to_num(daily20), rth=rth)
    return derive(D)


def days(D):
    idx = np.flatnonzero(D['rth']); out = {}
    if len(idx) == 0: return out
    br = np.flatnonzero(np.diff(D['date'][idx].astype('int64')) != 0) + 1
    for seg in np.split(idx, br):
        if len(seg) >= 60 and D['m'][seg[-1]] == CLOSE: out[pd.Timestamp(D['date'][seg[0]])] = seg
    return out


def filt_ok(D, i, side, F):
    for f in F:
        if f == 'adx20' and not D['adx'][i] > 20: return False
        if f == 'adx25' and not D['adx'][i] > 25: return False
        if f == 'di' and not (D['pdi'][i] - D['mdi'][i]) * side > 0: return False
        if f == 'vwap' and not (D['c'][i] - D['vwap'][i]) * side > 0: return False
        if f == 'e50side' and not (D['c'][i] - D['e50'][i]) * side > 0: return False
        if f == 'htf' and not D['htf'][i] == side: return False
        if f == 'daily20' and not D['daily20'][i] == side: return False
        if f == 'rsi50' and not (D['rsi'][i] - 50) * side > 0: return False
        if f == 'rsi' and not ((50 < D['rsi'][i] < 70) if side > 0 else (30 < D['rsi'][i] < 50)): return False
        if f == 'macd' and not D['hist'][i] * side > 0: return False
        if f == 'vol' and not (D['v'][i] > 1.5 * D['vavg'][i]): return False
        if f == 'stack' and not ((D['c'][i] > D['e50'][i] > D['e200'][i]) if side > 0 else (D['c'][i] < D['e50'][i] < D['e200'][i])): return False
        if f == 'sep' and not abs(D['e50'][i] - D['e200'][i]) > 0.5 * D['atr'][i]: return False
    return True


def entry_signal(D, i, mode):
    st = D['st'][i]
    if mode == 'cross': return int(st) if D['cross'][i] else 0
    if mode == 'state': return int(st)
    if mode == 'pullback': return int(st) if D['pb'][i] else 0
    if mode == 'pullback_armed': return int(st) if D['pb'][i] and D['armed'][i] else 0
    if mode == 'firstpull': return int(st) if D['firstpull'][i] else 0
    if mode == 'sepcross': return int(st) if D['sepfirst'][i] else 0
    if mode == 'fast921': return int(st) if D['fcross'][i] and D['fst'][i] == st else 0
    if mode == 'reclaim':
        c, e50 = D['c'], D['e50']
        if st > 0 and c[i] > e50[i] and c[i - 1] <= e50[i - 1]: return 1
        if st < 0 and c[i] < e50[i] and c[i - 1] >= e50[i - 1]: return -1
        return 0
    raise ValueError(mode)


def stop_level(D, i, pos, ent, sp):
    if sp is None: return np.nan
    if isinstance(sp, (int, float)): return ent - pos * sp * D['atr'][i]
    kind = sp[0]
    if kind == 'swing': lo = max(0, i - sp[1] + 1); return D['l'][lo:i + 1].min() if pos > 0 else D['h'][lo:i + 1].max()
    if kind == 'e200atr': return D['e200'][i] - pos * sp[1] * D['atr'][i]
    if kind == 'e200tick': return D['e200'][i] - pos * TICK
    if kind == 'trail200': return D['e200'][i] - pos * 2 * TICK
    raise ValueError(sp)


def run(D, spec, ticks=2.35, longs=True, shorts=True):
    mode = spec['entry']; F = spec.get('filters', ()); ex = spec.get('exit', 'flip'); sp = spec.get('stop'); tp = spec.get('target')
    reent = spec.get('reenter', True); cost = 2 * (0.5 * ticks + 0.5)
    trail = isinstance(sp, tuple) and sp[0] == 'trail200'
    o, h, l, c, m = D['o'], D['h'], D['l'], D['c'], D['m']; rows = []
    for day, seg in days(D).items():
        pos = 0; legs = []; stop = np.nan; ent = 0.0; nbar = 0; dirs = set()

        def close_all(px, why):
            nonlocal pos, legs
            for q, _ in legs: rows.append((day, pos, q, ent, px, why))
            legs = []; pos = 0

        for k, i in enumerate(seg[:-1]):
            if pos != 0 and k > 0:
                nbar += 1
                if trail: stop = max(stop, D['e200'][i - 1] - 2 * TICK) if pos > 0 else min(stop, D['e200'][i - 1] + 2 * TICK)
                if not np.isnan(stop) and ((l[i] <= stop) if pos > 0 else (h[i] >= stop)):
                    close_all(min(o[i], stop) if pos > 0 else max(o[i], stop), 'stop')
                else:
                    keep = []
                    for q, tg in legs:
                        if not np.isnan(tg) and ((h[i] >= tg) if pos > 0 else (l[i] <= tg)):
                            rows.append((day, pos, q, ent, max(o[i], tg) if pos > 0 else min(o[i], tg), 'target'))
                            if ex == 'fh': stop = ent
                        else: keep.append((q, tg))
                    legs = keep
                    if not legs: pos = 0
            if np.isnan(D['e200'][i]) or np.isnan(D['atr'][i]): continue
            if pos != 0:
                st = D['st'][i]
                if ex in ('flip', 'time10', 'fh') and st == -pos: close_all(o[i + 1], 'flip')
                elif ex == 'fast' and D['fst'][i] == -pos: close_all(o[i + 1], 'fast')
                elif ex == 'time10' and nbar >= 10: close_all(o[i + 1], 'time')
            if pos == 0 and m[i] <= LAST_ENTRY:
                s = entry_signal(D, i, mode)
                if s != 0 and ((s > 0 and longs) or (s < 0 and shorts)) and (reent or s not in dirs) and filt_ok(D, i, s, F):
                    ent = o[i + 1]; stop = stop_level(D, i, s, ent, sp)
                    if not np.isnan(stop) and (stop - ent) * s >= 0: continue
                    pos = s; dirs.add(s); nbar = 0; risk = abs(ent - stop) if not np.isnan(stop) else np.nan
                    if ex == 'fh': legs = [(0.5, ent + s * 1.5 * risk), (0.5, ent + s * 3.0 * risk)]
                    elif tp is None: legs = [(1.0, np.nan)]
                    elif isinstance(tp, tuple) and tp[0] == 'R': legs = [(1.0, ent + s * tp[1] * risk)]
                    else: legs = [(1.0, ent + s * tp * D['atr'][i])]
        if pos != 0: close_all(c[seg[-1]], 'close')
    tr = pd.DataFrame(rows, columns=['date', 'side', 'qty', 'entry', 'exit', 'why'])
    tr['gross'] = tr.qty * tr.side * (tr.exit / tr.entry - 1) * USD
    tr['usd'] = tr.gross - tr.qty * cost
    return tr


def daily(tr, D, col='usd'):
    idx = pd.DatetimeIndex(sorted(days(D).keys()))
    return tr.groupby('date')[col].sum().reindex(idx).fillna(0.0)


def stats(x):
    sd = x.std(ddof=1); sh = x.mean() / sd * np.sqrt(252) if sd > 0 else float('nan')
    t = x.mean() / (sd / np.sqrt(len(x))) if sd > 0 else float('nan')
    eq = x.cumsum().to_numpy(); dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max())
    yrs = x.groupby(x.index.year).sum()
    return dict(sh=sh, t=t, yr=x.mean() * 252, dd=dd, years_neg=int((yrs < 0).sum()), years=len(yrs), worst=float(yrs.min()))
