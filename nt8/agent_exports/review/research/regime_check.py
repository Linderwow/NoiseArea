r"""Independent checks for REGIME_PLAN.txt audits (2) and (3). Written separately from regime_test.py; shares only the data loader.
    python regime_check.py families    G, H, I re-implemented over the raw bar table, 200 random sessions per file, vs regime_work\*.npz
    python regime_check.py holdout     the switcher's holdout series rebuilt from the saved matrices with separately written code
"""
import os, sys, json, ctypes
import numpy as np, pandas as pd
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
from sim import load

WORK = os.path.join(HERE, 'regime_work')
FILES = {'NQ': os.path.join(HERE, 'scaling_inputs', 'nq_2010-2019_5m.csv'),
         'MNQ': os.path.join(HERE, 'scaling_inputs', 'nt_analyzer_bars_to_2026-10-14.csv')}


def sessions(path):
    """[(date, DataFrame of that session's RTH bars)] for valid sessions (>= 30 bars, first bar 09:35), with vwap."""
    df = load(path); df['date'] = df.time.dt.date
    out = []
    for d, g in df[df.in_rth].groupby('date', sort=True):
        if len(g) < 30 or int(g.etmin.iloc[0]) != 575: continue
        g = g.copy(); tp = (g.high + g.low + g.close) / 3
        g['vw'] = (tp * g.volume).cumsum() / np.maximum(g.volume.cumsum(), 1)
        out.append((d, g.reset_index(drop=True)))
    return out


def sim_exit(g, start, side, entry, stop, target):
    """Walk bars from 'start': stop first, then target, then the 15:55 close, else the last close. Returns exit price."""
    for i in range(start, len(g)):
        b = g.iloc[i]
        if (side > 0 and b.low <= stop) or (side < 0 and b.high >= stop): return stop
        if target is not None and ((side > 0 and b.high >= target) or (side < 0 and b.low <= target)): return target
        if b.etmin >= 955: return b.close
    return g.close.iloc[-1]


def G(g, k, s, maxn):
    """VWAP stretch: same-bar re-entry semantics written out explicitly as a bar-by-bar event list."""
    total = 0.0; n = 0; i = 0; trades_today = 0; nb = len(g)
    while i < nb:
        b = g.iloc[i]
        if trades_today < maxn and 630 <= b.etmin <= 900 and b.atr > 0 and i + 1 < nb:
            side = -1 if b.close > b.vw + k * b.atr else (1 if b.close < b.vw - k * b.atr else 0)
            if side:
                trades_today += 1
                e = g.open.iloc[i + 1]; stop = b.close - side * s * b.atr; tgt = b.vw
                if (e - stop) * side > 0 and (tgt - e) * side > 0:
                    # walk from the entry bar; find the exit bar so the next signal can only come after it
                    j = i + 1; ex = None
                    while j < nb:
                        c = g.iloc[j]
                        if (side > 0 and c.low <= stop) or (side < 0 and c.high >= stop): ex = stop; break
                        if (side > 0 and c.high >= tgt) or (side < 0 and c.low <= tgt): ex = tgt; break
                        if c.etmin >= 955: ex = c.close; break
                        j += 1
                    if ex is None: ex = g.close.iloc[-1]; j = nb
                    total += (ex - e) * side; n += 1; i = j; continue      # the exit bar may itself signal again

                i += 1; continue
        i += 1
    return total, n


def H(g, prev, mode, buf, s, ex):
    pdh, pdl = prev.high.max(), prev.low.min(); total = 0.0; n = 0; used = set(); i = 0; nb = len(g)
    while i < nb:
        b = g.iloc[i]; side = 0
        if 585 <= b.etmin <= 900 and b.atr > 0 and i + 1 < nb:
            a = b.atr
            if mode == 'fade':
                if -1 not in used and b.high >= pdh and b.close < pdh - buf * a: side, lvl_stop, tv = -1, pdh + s * a, b.vw
                elif 1 not in used and b.low <= pdl and b.close > pdl + buf * a: side, lvl_stop, tv = 1, pdl - s * a, b.vw
            else:
                if 1 not in used and b.close > pdh + buf * a: side, lvl_stop, tv = 1, pdh - s * a, None
                elif -1 not in used and b.close < pdl - buf * a: side, lvl_stop, tv = -1, pdl + s * a, None
        if side == 0: i += 1; continue
        used.add(side); e = g.open.iloc[i + 1]; risk = (e - lvl_stop) * side
        tgt = tv if (ex == 'vwap' and mode == 'fade') else (e + 2 * risk * side if ex == '2R' else None)
        if risk <= 0 or (tgt is not None and (tgt - e) * side <= 0): i += 1; continue
        j = i + 1; xp = None
        while j < nb:
            c = g.iloc[j]
            if (side > 0 and c.low <= lvl_stop) or (side < 0 and c.high >= lvl_stop): xp = lvl_stop; break
            if tgt is not None and ((side > 0 and c.high >= tgt) or (side < 0 and c.low <= tgt)): xp = tgt; break
            if c.etmin >= 955: xp = c.close; break
            if mode == 'break' and ex == 'vwap' and (c.close - c.vw) * side < 0 and j < nb - 1: xp = g.open.iloc[j + 1]; j += 1; break
            j += 1
        if xp is None: xp = g.close.iloc[-1]; j = nb
        total += (xp - e) * side; n += 1; i = j                  # the exit bar may itself signal the other side
    return total, n


def I(g, pc, mean_gap, gthr, mode, s):
    gap = g.open.iloc[0] - pc
    if not mean_gap > 0 or abs(gap) < gthr * mean_gap or gap == 0 or len(g) < 3: return 0.0, 0
    side = -int(np.sign(gap)) if mode == 'fade' else int(np.sign(gap))
    e = g.open.iloc[1]; b0 = g.iloc[0]
    stop = b0.high + s * b0.atr if side < 0 else b0.low - s * b0.atr
    tgt = pc if mode == 'fade' else None
    if (e - stop) * side <= 0 or (tgt is not None and (tgt - e) * side <= 0): return 0.0, 0
    return (sim_exit(g, 1, side, e, stop, tgt) - e) * side, 1


def families():
    import regime_test as rt
    rng = np.random.default_rng(7); bad = 0; checked = 0
    for name, path in FILES.items():
        z = np.load(os.path.join(WORK, f'{name}.npz')); P, NT = z['P'], z['NT']; dates = pd.to_datetime(z['dates'])
        S = sessions(path); assert [d for d, _ in S] == list(dates.date), "session list differs"
        closes = [g.close.iloc[-1] for _, g in S]; gaps = [np.nan] + [abs(S[k][1].open.iloc[0] - closes[k - 1]) for k in range(1, len(S))]
        pick = rng.choice(np.arange(20, len(S)), 200, replace=False)
        cols = [j for j in range(len(rt.CAT)) if rt.CAT[j][0] in 'GHI']
        for k in pick:
            d, g = S[k]
            for j in cols:
                f, p = rt.CAT[j]
                if f == 'G': tot, n = G(g, p['k'], p['s'], p['maxn'])
                elif f == 'H': tot, n = H(g, S[k - 1][1], p['mode'], p['buf'], p['s'], p['ex'])
                else: tot, n = (I(g, closes[k - 1], np.nanmean(gaps[k - 14:k]), p['g'], p['mode'], p['s']) if k >= 15 else (0.0, 0))
                checked += 1
                if abs(tot - P[k, j]) > 1e-6 or n != NT[k, j]:
                    bad += 1
                    if bad <= 10: print(f"   MISMATCH {name} {d} {rt.label(j)}: check {tot:.2f}/{n} vs build {P[k, j]:.2f}/{NT[k, j]}")
        print(f"   {name}: 200 sessions x {len(cols)} G/H/I variants checked", flush=True)
    print(f"AUDIT 2: {checked} session-variant pairs, {bad} mismatches -> {'PASSED' if bad == 0 else 'FAILED'}")


def holdout():
    """Rebuild the model's holdout series from regime_work\\*.npz with pandas rolling windows instead of loops."""
    m = json.load(open(os.path.join(WORK, 'model.json'))); scheme, cat, K = m['model']
    zq, zm = np.load(os.path.join(WORK, 'NQ.npz')), np.load(os.path.join(WORK, 'MNQ.npz'))
    dq, dm = pd.to_datetime(zq['dates']), pd.to_datetime(zm['dates'])
    sq = pd.read_csv(os.path.join(WORK, 'NQ_daily.csv'), index_col=0, parse_dates=True); sm = pd.read_csv(os.path.join(WORK, 'MNQ_daily.csv'), index_col=0, parse_dates=True)
    j0 = pd.Timestamp('2019-05-05')
    daily = pd.concat([sq[sq.index <= j0].assign(seg=0), sm[sm.index > j0].assign(seg=1)])
    newseg = daily.seg.ne(daily.seg.shift()) & (daily.index != daily.index[0])
    dC = daily.C.diff().mask(newseg); gap = (daily.O - daily.C.shift()).abs().mask(newseg)
    body = (daily.C - daily.O).abs(); rngd = daily.H - daily.L
    feat = pd.DataFrame(index=daily.index)
    feat['ER10'] = dC.rolling(10, min_periods=1).sum().abs().shift(1) / dC.abs().rolling(10, min_periods=1).sum().shift(1)
    feat['VOLR'] = body.rolling(5).mean().shift(1) / body.rolling(60).mean().shift(1)
    feat['ONR'] = daily.ONR / rngd.rolling(14).mean().shift(1)
    feat['GAPZ'] = gap / gap.rolling(14, min_periods=1).mean().shift(1)
    feat['PDE'] = (body / rngd).shift(1)
    raw = pd.read_csv('raw_open930.csv', index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join('scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    def part(z, d, s, keep):
        k = (np.arange(len(d)) >= 30) & keep
        px = raw.reindex(d[k]).values; px = np.where(np.isnan(px), s.O.values[k], px)
        return d[k], z['P'][k] / px[:, None] - z['NT'][k] * (1.0 / p_ref), z['NT'][k]
    a, b = part(zq, dq, sq, dq <= j0), part(zm, dm, sm, dm > j0)
    dates = a[0].append(b[0]); R = np.vstack([a[1], b[1]]); NT = np.vstack([a[2], b[2]])
    f = feat.reindex(dates)
    disc = (dates >= '2010-07-21') & (dates <= '2019-05-05'); hold = (dates >= '2019-05-06') & (dates <= '2026-10-14')
    spec = {'S0': [], 'S1': ['ER10'], 'S2': ['VOLR'], 'S3': ['ER10', 'VOLR'], 'S4': ['ONR', 'GAPZ'], 'S5': ['PDE']}[scheme]
    cell = pd.Series(0, index=dates); missing = pd.Series(False, index=dates)
    for col in spec:
        v = f[col]; ref = v[disc].dropna()
        edges = [ref.median()] if col == 'GAPZ' else [ref.quantile(1 / 3, interpolation='linear'), ref.quantile(2 / 3, interpolation='linear')]
        cell = cell * (len(edges) + 1) + v.apply(lambda x: sum(x >= e for e in edges) if pd.notna(x) else 0)
        missing |= v.isna()
    cell[missing] = -1
    sel = {int(k): v for k, v in m['sel'].items()}
    r = pd.Series(0.0, index=dates)
    for c, lst in sel.items():
        for j, w in lst: r[cell == c] += w * R[(cell == c).values, j]
    saved = np.load(os.path.join(WORK, 'holdout_series.npz'))
    diff = np.abs(r[hold].values - saved['model']).max()
    cells_same = (cell[hold].values == saved['cid']).mean()
    N = 2 * p_ref; x = r[hold].values
    print(f"AUDIT 3: rebuilt model holdout series vs regime_test.py: max |difference| {diff:.2e}, same cell on {cells_same * 100:.2f}% of days; "
          f"rebuilt ${x.sum() * N / (len(x) / 252):,.0f}/yr, Sharpe {x.mean() / x.std(ddof=1) * np.sqrt(252):.2f} -> {'PASSED' if diff < 1e-9 and cells_same == 1 else 'FAILED'}")


if __name__ == '__main__':
    {'families': families, 'holdout': holdout}.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: print(__doc__))()
