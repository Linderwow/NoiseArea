import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import ml_lab as ML

TFS = (5, 15, 30)
IND = ['rsi', 'macd', 'bbb', 'stk', 'std', 'wr', 'cci', 'adx', 'did', 'atrp', 'roc', 'e20', 'e50', 'don', 'kel', 'vz', 'obv', 'mfi', 'upv']
DAYF = ['prng', 'pdh', 'pdl', 'pdc', 'orpos', 'orbrk', 'vwz', 'opex', 'mend']
FEATS = ML.FEATS + [f'{i}{tf}' for tf in TFS for i in IND] + DAYF


def tf_bars(S, tf):
    n = S.C.shape[0]; nb = 390 // tf
    r = lambda a: a[:, :nb * tf].reshape(n, nb, tf)
    o = r(S.O)[:, :, 0]; h = r(S.H).max(2); l = r(S.L).min(2); c = r(S.C)[:, :, -1]; v = r(S.V).sum(2)
    return nb, o.ravel(), h.ravel(), l.ravel(), c.ravel(), v.ravel()


def indicators(o, h, l, c, v):
    s = pd.Series(c); pc = s.shift(1).values
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc))); tr[0] = h[0] - l[0]
    w = lambda x, n: pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values
    atr = w(tr, 14); d = np.diff(c, prepend=c[0])
    up = w(np.clip(d, 0, None), 14); dn = w(np.clip(-d, 0, None), 14)
    out = {}
    with np.errstate(invalid='ignore', divide='ignore'):
        out['rsi'] = np.where(dn > 0, 100 - 100 / (1 + up / dn), 100.0)
        e12, e26 = s.ewm(span=12, adjust=False).mean().values, s.ewm(span=26, adjust=False).mean().values
        m = e12 - e26; out['macd'] = (m - pd.Series(m).ewm(span=9, adjust=False).mean().values) / c
        ma = s.rolling(20).mean().values; sd = s.rolling(20).std(ddof=0).values; out['bbb'] = (c - (ma - 2 * sd)) / (4 * sd)
        hh14 = pd.Series(h).rolling(14).max().values; ll14 = pd.Series(l).rolling(14).min().values
        k = (c - ll14) / (hh14 - ll14); out['stk'] = k; out['std'] = pd.Series(k).rolling(3).mean().values; out['wr'] = (hh14 - c) / (hh14 - ll14) * -100
        tp = (h + l + c) / 3; tps = pd.Series(tp); out['cci'] = (tp - tps.rolling(20).mean().values) / (0.015 * 0.7979 * tps.rolling(20).std(ddof=0).values)
        upm = h - np.r_[h[0], h[:-1]]; dnm = np.r_[l[0], l[:-1]] - l
        pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0); ndm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
        pdi = 100 * w(pdm, 14) / atr; ndi = 100 * w(ndm, 14) / atr; dx = 100 * np.abs(pdi - ndi) / (pdi + ndi)
        out['adx'] = w(np.nan_to_num(dx), 14); out['did'] = pdi - ndi
        out['atrp'] = atr / c; out['roc'] = c / s.shift(10).values - 1
        e20 = s.ewm(span=20, adjust=False).mean().values; e50 = s.ewm(span=50, adjust=False).mean().values
        out['e20'] = (c - e20) / atr; out['e50'] = (c - e50) / atr
        hh20 = pd.Series(h).rolling(20).max().values; ll20 = pd.Series(l).rolling(20).min().values; out['don'] = (c - ll20) / (hh20 - ll20)
        out['kel'] = (c - e20) / (2 * atr)
        vs = pd.Series(v); out['vz'] = (v - vs.rolling(20).mean().values) / vs.rolling(20).std(ddof=0).values
        obv = np.cumsum(np.sign(d) * v); out['obv'] = (obv - pd.Series(obv).shift(10).values) / (vs.rolling(20).mean().values * 10)
        mf = tp * v; dtp = np.diff(tp, prepend=tp[0]); pos = pd.Series(np.where(dtp > 0, mf, 0.0)).rolling(14).sum().values
        neg = pd.Series(np.where(dtp < 0, mf, 0.0)).rolling(14).sum().values; out['mfi'] = 100 - 100 / (1 + pos / neg)
        out['upv'] = pd.Series(np.where(c > o, v, 0.0)).rolling(20).sum().values / vs.rolling(20).sum().values
    return out


def third_friday(y, m):
    d = pd.Timestamp(y, m, 1); return d + pd.Timedelta(days=(4 - d.dayofweek) % 7 + 14)


def features(S, X, step, horizon):
    T = ML.features(S, X, step, horizon); n = S.C.shape[0]; k = T.k.values; p_ = T.dec.values; p = p_ - 1
    for tf in TFS:
        nb, o, h, l, c, v = tf_bars(S, tf); ind = indicators(o, h, l, c, v)
        j = k * nb + (p_ // tf) - 1; ok = j >= 0; j = np.clip(j, 0, None)
        for nm in IND: T[f'{nm}{tf}'] = np.where(ok, ind[nm][j], np.nan)
    dh, dl, dc, do = S.H.max(1), S.L.min(1), S.C[:, -1], S.O[:, 0]
    pc_ = np.r_[np.nan, dc[:-1]]; dtr = np.maximum(dh - dl, np.maximum(np.abs(dh - pc_), np.abs(dl - pc_)))
    datr = pd.Series(dtr).shift(1).rolling(14, min_periods=10).mean().values
    same = np.r_[False, S.D.iid.values[1:] == S.D.iid.values[:-1]]
    pH, pL, pC = np.where(same, np.r_[np.nan, dh[:-1]], np.nan), np.where(same, np.r_[np.nan, dl[:-1]], np.nan), np.where(same, pc_, np.nan)
    px = S.C[k, p]
    with np.errstate(invalid='ignore', divide='ignore'):
        T['prng'] = ((pH - pL) / datr)[k]; T['pdh'] = (px - pH[k]) / datr[k]; T['pdl'] = (px - pL[k]) / datr[k]; T['pdc'] = (px - pC[k]) / datr[k]
        orh, orl = S.H[:, :30].max(1), S.L[:, :30].min(1)
        T['orpos'] = np.where(p_ >= 30, (px - orl[k]) / (orh[k] - orl[k]), np.nan)
        T['orbrk'] = np.where(p_ >= 30, np.where(px > orh[k], 1.0, np.where(px < orl[k], -1.0, 0.0)), np.nan)
        tp = (S.H + S.L + S.C) / 3; vw = np.cumsum(tp * S.V, 1) / np.maximum(np.cumsum(S.V, 1), 1)
        sd30 = pd.DataFrame(S.C).T.rolling(30, min_periods=10).std().T.values
        T['vwz'] = (px - vw[k, p]) / sd30[k, p]
    dates = pd.DatetimeIndex(S.D.date.values)
    tf_ = np.array([(third_friday(d.year, d.month) - d).days for d in dates], float)
    nxt = np.array([(third_friday(d.year + (d.month == 12), d.month % 12 + 1) - d).days for d in dates], float)
    T['opex'] = np.where(tf_ >= 0, tf_, nxt)[k]
    me = dates.to_period('M'); last2 = pd.Series(me).shift(-2).values != me.values
    T['mend'] = last2.astype(float)[k]
    return T
