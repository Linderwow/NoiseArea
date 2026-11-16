import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2

COST_RT = lambda ticks: 2 * L2.side_cost(ticks)
FEATS = ['r5', 'r15', 'r30', 'r60', 'ropen', 'gap', 'prevday', 'vol30', 'vol120', 'dvol20', 'vwapd', 'rngpos', 'vratio', 'tod', 'dow',
         'r15z', 'ropenz', 'x_r15', 'x_r60', 'x_ropen', 'rel15', 'rel_open']


class Sessions:
    def __init__(self, name):
        mk = L2.Mins(name); self.name = name; rows = []; O = []; H = []; Lo = []; C = []; V = []
        for d in mk.dates:
            i0, i1 = mk.days[d]
            if mk.m[i1] < 959: continue
            pos = mk.m[i0:i1 + 1] - 570; ok = (pos >= 0) & (pos < 390)
            a = {k: np.full(390, np.nan) for k in 'ohlcv'}
            for k, src in (('o', mk.o), ('h', mk.h), ('l', mk.l), ('c', mk.c), ('v', mk.v)): a[k][pos[ok]] = src[i0:i1 + 1][ok]
            c = pd.Series(a['c']).ffill().bfill().values; o = np.where(np.isnan(a['o']), c, a['o'])
            h = np.where(np.isnan(a['h']), c, a['h']); l = np.where(np.isnan(a['l']), c, a['l']); v = np.nan_to_num(a['v'])
            rows.append(dict(date=pd.Timestamp(d), iid=int(mk.iid[i0]))); O.append(o); H.append(h); Lo.append(l); C.append(c); V.append(v)
        self.D = pd.DataFrame(rows); self.O, self.H, self.L, self.C, self.V = (np.array(x) for x in (O, H, Lo, C, V))
        self.idx = {d: k for k, d in enumerate(self.D.date)}


def features(S, X, step, horizon):
    D = S.D; n = len(D); lc = np.log(S.C); lo = np.log(S.O)
    r1 = np.diff(lc, axis=1, prepend=lc[:, :1])
    tp = (S.H + S.L + S.C) / 3; vw = np.cumsum(tp * S.V, 1) / np.maximum(np.cumsum(S.V, 1), 1)
    hi = np.maximum.accumulate(S.H, 1); low = np.minimum.accumulate(S.L, 1)
    cv = np.cumsum(S.V, 1); v15 = cv - np.c_[np.zeros((n, 15)), cv[:, :-15]]
    v15avg = pd.DataFrame(v15).shift(1).rolling(20, min_periods=10).mean().values
    prevC = np.r_[np.nan, S.C[:-1, -1]]; prevO = np.r_[np.nan, S.O[:-1, 0]]
    same = np.r_[False, D.iid.values[1:] == D.iid.values[:-1]] & np.r_[False, (D.date.diff().dt.days.values[1:] <= 5)]
    gap = np.where(same, np.log(S.O[:, 0] / prevC), np.nan); prevday = np.where(same, np.log(prevC / prevO), np.nan)
    dret = np.log(S.C[:, -1] / S.O[:, 0]); dvol = pd.Series(dret).shift(1).rolling(20, min_periods=10).std().values
    xi = None
    if X is not None:
        xi = np.array([X.idx.get(d, -1) for d in D.date]); xl = np.log(X.C)
    decs = list(range(15 if step >= 15 else 5, 390 - horizon + 1, step))
    rows = []
    for p_ in decs:
        p = p_ - 1
        f = {}
        for w in (5, 15, 30, 60): f[f'r{w}'] = lc[:, p] - lc[:, p - w] if p - w >= 0 else np.full(n, np.nan)
        f['ropen'] = lc[:, p] - lo[:, 0]; f['gap'] = gap; f['prevday'] = prevday
        f['vol30'] = r1[:, max(1, p - 29):p + 1].std(1) if p >= 10 else np.full(n, np.nan)
        f['vol120'] = r1[:, max(1, p - 119):p + 1].std(1) if p >= 30 else np.full(n, np.nan)
        f['dvol20'] = dvol; f['vwapd'] = lc[:, p] - np.log(vw[:, p])
        with np.errstate(invalid='ignore', divide='ignore'):
            f['rngpos'] = (S.C[:, p] - low[:, p]) / np.where(hi[:, p] > low[:, p], hi[:, p] - low[:, p], np.nan)
            f['vratio'] = v15[:, p] / v15avg[:, p] if p >= 14 else np.full(n, np.nan)
            f['r15z'] = f['r15'] / (f['vol30'] * np.sqrt(15)); f['ropenz'] = f['ropen'] / (f['vol30'] * np.sqrt(p + 1))
        f['tod'] = np.full(n, 570 + p_); f['dow'] = D.date.dt.dayofweek.values.astype(float)
        if xi is not None:
            ok = xi >= 0; j = np.where(ok, xi, 0)
            for nm, w in (('x_r15', 15), ('x_r60', 60)):
                f[nm] = np.where(ok, xl[j, p] - xl[j, p - w], np.nan) if p - w >= 0 else np.full(n, np.nan)
            f['x_ropen'] = np.where(ok, xl[j, p] - np.log(X.O[j, 0]), np.nan)
        else:
            f['x_r15'] = f['x_r60'] = f['x_ropen'] = np.full(n, np.nan)
        f['rel15'] = f['r15'] - f['x_r15']; f['rel_open'] = f['ropen'] - f['x_ropen']
        e = p + 1; x = min(p + horizon, 389)
        f['y'] = lc[:, x] - lo[:, e]; f['entry'] = S.O[:, e]; f['exitp'] = S.C[:, x]; f['dec'] = np.full(n, p_)
        f['k'] = np.arange(n); f['date'] = D.date.values
        rows.append(pd.DataFrame(f))
    T = pd.concat(rows, ignore_index=True).sort_values(['k', 'dec']).reset_index(drop=True)
    T['open930'] = S.O[T.k.values, 0]
    return T


def walk_forward(T, years, first_test, model_fn):
    pred = np.full(len(T), np.nan); yr = T.date.dt.year.values
    for y in years:
        if y < first_test: continue
        tr = (yr < y) & np.isfinite(T.y.values); te = yr == y
        if te.sum() == 0 or tr.sum() < 5000: continue
        m = model_fn(); m.fit(T.loc[tr, FEATS].values, T.y.values[tr]); pred[te] = m.predict(T.loc[te, FEATS].values)
    return pred


def trade(T, pred, thr, c1):
    out = []
    for k, g in T.assign(pred=pred).groupby('k', sort=True):
        pos = 0; pnl = 0.0; sides = 0; o930 = g.open930.iat[0]; last_px = None
        for r in g.itertuples():
            if not np.isfinite(r.pred): continue
            new = 1 if r.pred > thr else (-1 if r.pred < -thr else 0)
            if pos != 0: pnl += pos * (r.entry - last_px); last_px = r.entry
            if new != pos:
                sides += abs(new - pos); pos = new; last_px = r.entry
        if pos != 0: pnl += pos * (g.exitp.iat[-1] - last_px); sides += 1
        if sides: out.append((g.date.iat[0], pnl / o930 - sides * c1, sides))
    df = pd.DataFrame(out, columns=['date', 'net', 'sides'])
    return pd.Series(df.net.values, index=pd.DatetimeIndex(df.date)), df.sides.values
