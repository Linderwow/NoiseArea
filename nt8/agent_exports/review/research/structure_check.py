r"""Independent audit of structure_test.py (STRUCTURE_PLAN.txt): the 66 new variants re-implemented over the raw bar table with
separately written code, on 150 random sessions of each file; the value area by brute force on 50 sessions; the switcher's
holdout series rebuilt from the saved matrices.   python structure_check.py families | holdout
"""
import os, sys, json
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); os.chdir(HERE)
from sim import load
from regime_check import sessions

TICK = 0.25
WORK = os.path.join(HERE, 'regime_work')
FILES = {'NQ': os.path.join(HERE, 'scaling_inputs', 'nq_2010-2019_5m.csv'), 'MNQ': os.path.join(HERE, 'scaling_inputs', 'nt_analyzer_bars_to_2026-10-14.csv')}


def va_brute(g):
    """Value area by brute force: a dict of price -> volume over every bin each bar touches; expand from the POC one bin at a time."""
    b = 0.25 if g.open.iloc[0] < 8000 else 1.0; vol = {}
    for r in g.itertuples():
        ks = range(int(np.floor(r.low / b)), int(np.floor(r.high / b)) + 1)
        for k in ks: vol[k] = vol.get(k, 0.0) + r.volume / len(ks)
    keys = sorted(vol); arr = [vol[k] for k in keys]; poc = int(np.argmax(arr)); lo = hi = poc; acc = arr[poc]; tot = sum(arr)
    while acc < 0.7 * tot:
        up = arr[hi + 1] if hi + 1 < len(arr) else -1.0; dn = arr[lo - 1] if lo > 0 else -1.0
        if up < 0 and dn < 0: break
        if up >= dn: hi += 1; acc += up
        else: lo -= 1; acc += dn
    return keys[lo] * b, (keys[hi] + 1) * b


def walk(g, i0, side, entry, stop, target, vwap_exit=False, want_bar=False):
    rows = list(g.itertuples()); n = len(rows)
    for i in range(i0, n):
        r = rows[i]
        hit = r.low <= stop if side > 0 else r.high >= stop
        if hit:
            gapped = (r.open <= stop) if side > 0 else (r.open >= stop)
            px = r.open if (i > i0 and gapped) else stop
            return (px, i) if want_bar else px
        if target is not None and (r.high >= target if side > 0 else r.low <= target): return (target, i) if want_bar else target
        if r.etmin >= 955: return (r.close, n) if want_bar else r.close
        if vwap_exit and (r.close - r.vw) * side < 0 and i + 1 < n: return rows[i + 1].open
    return (rows[-1].close, n) if want_bar else rows[-1].close


def otype(g, tol):
    if len(g) < 8 or int(g.etmin.iloc[5]) != 600: return None
    f = g.iloc[:6]; O0 = g.open.iloc[0]; hi, lo, c, a = f.high.max(), f.low.min(), f.close.iloc[-1], f.atr.iloc[-1]; r = hi - lo
    if a <= 0 or r <= 0: return (2, 0)
    for kind, side, cond in ((0, 1, lo >= O0 - tol * a and c > O0 and c >= hi - 0.25 * r), (0, -1, hi <= O0 + tol * a and c < O0 and c <= lo + 0.25 * r),
                             (1, 1, lo <= O0 - tol * a and c > O0 and c >= hi - r / 3), (1, -1, hi >= O0 + tol * a and c < O0 and c <= lo + r / 3)):
        if cond: return (kind, side)
    return (2, 0)


def J(g, types, tol, stop, ex):
    t = otype(g, tol)
    if t is None or t[0] == 2 or (types == 'drive' and t[0] != 0): return 0.0, 0
    side = t[1]; e = g.open.iloc[6]; a5 = g.atr.iloc[5]
    stp = g.open.iloc[0] - side * 0.5 * a5 if stop == 'open' else (g.low.iloc[:6].min() if side > 0 else g.high.iloc[:6].max())
    if (e - stp) * side <= 0: return 0.0, 0
    return (walk(g, 6, side, e, stp, None, ex == 'vwap') - e) * side, 1


def K(g, hist_widths, mode, lvl, ex, filt):
    if len(g) < 14 or int(g.etmin.iloc[11]) != 630: return 0.0, 0
    ib = g.iloc[:12]; ibh, ibl = ib.high.max(), ib.low.min(); w = ibh - ibl; mid = (ibh + ibl) / 2
    if filt == 'narrow':
        if len(hist_widths) < 14 or not w < 0.8 * np.nanmean(hist_widths[-14:]): return 0.0, 0
    for i in range(12, len(g) - 1):
        r = g.iloc[i]
        if r.etmin > 840: break
        a = r.atr; side = 0
        if mode == 'break':
            side = 1 if r.close > ibh + 0.1 * a else (-1 if r.close < ibl - 0.1 * a else 0)
            if side:
                e = g.open.iloc[i + 1]; stp = mid if lvl == 'mid' else (ibl if side > 0 else ibh); tg = e + 2 * (e - stp) if ex == '2R' else None
        else:
            side = -1 if (r.high > ibh and r.close < ibh) else (1 if (r.low < ibl and r.close > ibl) else 0)
            if side:
                e = g.open.iloc[i + 1]; stp = r.high + 0.25 * a if side < 0 else r.low - 0.25 * a
                tg = e + 2 * (e - stp) if ex == '2R' else (mid if lvl == 'mid' else (ibl if side < 0 else ibh))
        if side:
            if (e - stp) * side > 0 and (tg is None or (tg - e) * side > 0): return (walk(g, i + 1, side, e, stp, tg) - e) * side, 1
            return 0.0, 0
    return 0.0, 0


def L_(g, prev, h, s):
    val, vah = va_brute(prev); O0 = g.open.iloc[0]
    side = -1 if O0 > vah else (1 if O0 < val else 0)
    if not side: return 0.0, 0
    cnt = 0
    for i in range(len(g) - 1):
        r = g.iloc[i]
        if r.etmin > 780: break
        if r.etmin < 600 or (r.etmin - 600) % 30: continue
        cnt = cnt + 1 if val < r.close < vah else 0
        if cnt >= h:
            e = g.open.iloc[i + 1]; tg = val if side < 0 else vah; stp = vah + s * r.atr if side < 0 else val - s * r.atr
            if (e - stp) * side > 0 and (tg - e) * side > 0: return (walk(g, i + 1, side, e, stp, tg) - e) * side, 1
            return 0.0, 0
    return 0.0, 0


def M_(g, prev, onh, onl, levels, tgt):
    hs, ls = [], []
    if levels in ('both', 'ON') and not np.isnan(onh): hs.append(onh); ls.append(onl)
    if levels in ('both', 'PD'): hs.append(prev.high.max()); ls.append(prev.low.min())
    if not hs: return 0.0, 0
    H, L = g.high.values, g.low.values; swept_up = swept_dn = False; order = None
    for i in range(len(g)):
        swept_up = swept_up or any(H[i] >= x + TICK for x in hs); swept_dn = swept_dn or any(L[i] <= x - TICK for x in ls)
        m = g.etmin.iloc[i]
        if i < 2 or m < 605: continue
        if m > 660: break
        if swept_up and L[i - 2] > H[i]: order = (-1, H[i], i); break
        if swept_dn and H[i - 2] < L[i]: order = (1, L[i], i); break
    if order is None: return 0.0, 0
    side, lim, i0 = order
    for j in range(i0 + 1, len(g)):
        if g.etmin.iloc[j] > 690: break
        if (side < 0 and H[j] >= lim + TICK) or (side > 0 and L[j] <= lim - TICK):
            stp = H[:j + 1].max() + TICK if side < 0 else L[:j + 1].min() - TICK
            if (lim - stp) * side <= 0: return 0.0, 0
            tg = None if tgt == 'close' else lim + (2 if tgt == '2R' else 3) * (lim - stp)
            return (walk(g, j, side, lim, stp, tg) - lim) * side, 1
    return 0.0, 0


def N_(g, onh, onl, levels, tgt, buf):
    if levels == 'OR30':
        if len(g) < 8 or int(g.etmin.iloc[5]) != 600: return 0.0, 0
        lh, ll = g.high.iloc[:6].max(), g.low.iloc[:6].min()
    else:
        if np.isnan(onh): return 0.0, 0
        lh, ll = onh, onl
    tot, n, done, i = 0.0, 0, set(), 0
    while i < len(g) - 1:
        r = g.iloc[i]
        if r.etmin > 840: break
        if r.etmin < 605: i += 1; continue
        side = -1 if (-1 not in done and r.high > lh and r.close < lh - buf * r.atr) else (1 if (1 not in done and r.low < ll and r.close > ll + buf * r.atr) else 0)
        if not side: i += 1; continue
        done.add(side); e = g.open.iloc[i + 1]; stp = r.high + 0.25 * r.atr if side < 0 else r.low - 0.25 * r.atr
        tg = r.vw if tgt == 'vwap' else ((ll if side < 0 else lh) if tgt == 'opposite' else e + 2 * (e - stp))
        if (e - stp) * side <= 0 or (tg - e) * side <= 0: i += 1; continue
        px, k = walk(g, i + 1, side, e, stp, tg, want_bar=True); tot += (px - e) * side; n += 1; i = k
    return tot, n


def O_(g, r_today, r_hist, thr, entry):
    if np.isnan(r_today) or r_today == 0 or len(r_hist) < 14: return 0.0, 0
    if thr > 0 and not abs(r_today) >= thr * np.nanmean(np.abs(r_hist[-14:])): return 0.0, 0
    idx = np.where(g.etmin.values == entry)[0]
    if not len(idx) or idx[0] + 1 >= len(g): return 0.0, 0
    side = 1 if r_today > 0 else -1; e = g.open.iloc[idx[0] + 1]
    return (g.close.iloc[-1] - e) * side, 1


def P_(g, daily, k, filt):
    if k < 11: return 0.0, 0
    o, h, l = daily['o'], daily['h'], daily['l']; rng = [h[j] - l[j] for j in range(len(h))]; p = k - 1
    if filt != 'none':
        nr4 = all(rng[p] < rng[p - q] for q in (1, 2, 3)); nr7 = p >= 7 and all(rng[p] < rng[p - q] for q in range(1, 7))
        idy = h[p] < h[p - 1] and l[p] > l[p - 1]
        if not {'NR4': nr4, 'NR7': nr7, 'ID': idy, 'IDNR4': idy and nr4}[filt]: return 0.0, 0
    st = np.mean([min(h[j] - o[j], o[j] - l[j]) for j in range(k - 10, k)])
    if not st > 0: return 0.0, 0
    O0 = g.open.iloc[0]; bs, ss = O0 + st, O0 - st
    for i in range(len(g)):
        r = g.iloc[i]
        if r.etmin > 900: break
        up, dn = r.high >= bs, r.low <= ss
        if not (up or dn): continue
        side = (1 if abs(r.open - bs) <= abs(r.open - ss) else -1) if (up and dn) else (1 if up else -1)
        e = max(r.open, bs) if side > 0 else min(r.open, ss); stp = ss if side > 0 else bs
        if up and dn: return (stp - e) * side, 1
        return ((walk(g, i + 1, side, e, stp, None) if i + 1 < len(g) else g.close.iloc[-1]) - e) * side, 1
    return 0.0, 0


def families():
    import structure_test as st
    rng = np.random.default_rng(11); bad = checked = va_bad = 0
    for name, path in FILES.items():
        z = np.load(os.path.join(WORK, f'{name}_struct.npz')); P, NT = z['P'], z['NT']
        lv = pd.read_csv(os.path.join(WORK, f'{name}_levels.csv'), index_col=0, parse_dates=True)
        S = sessions(path); df = load(path); eth = df[(~df.in_rth) & ((df.etmin <= 570) | (df.etmin > 1080))]
        onh = eth.groupby('tday').high.max(); onl = eth.groupby('tday').low.min()
        daily = {'o': [g.open.iloc[0] for _, g in S], 'h': [g.high.max() for _, g in S], 'l': [g.low.min() for _, g in S]}
        closes = [g.close.iloc[-1] for _, g in S]
        r10 = []
        for k, (d, g) in enumerate(S):
            i10 = np.where(g.etmin.values == 600)[0]
            r10.append(g.close.iloc[i10[0]] / closes[k - 1] - 1 if (k > 0 and len(i10)) else np.nan)
        widths = [(g.high.iloc[:12].max() - g.low.iloc[:12].min()) if (len(g) >= 14 and int(g.etmin.iloc[11]) == 630) else np.nan for _, g in S]
        for k in rng.choice(np.arange(1, len(S)), 50, replace=False):
            a, b = va_brute(S[k][1]); va_bad += int(abs(a - lv.val.iloc[k]) > 1e-9 or abs(b - lv.vah.iloc[k]) > 1e-9)
        for k in rng.choice(np.arange(20, len(S)), 150, replace=False):
            d, g = S[k]; prev = S[k - 1][1]; dd = pd.Timestamp(d)
            oh, ol = onh.get(d, np.nan), onl.get(d, np.nan)
            for j, (f, p) in enumerate(st.NEW):
                if f == 'J': got = J(g, **p)
                elif f == 'K': got = K(g, [w for w in widths[max(0, k - 14):k]] if k >= 14 else [], **p)
                elif f == 'L': got = L_(g, prev, **p)
                elif f == 'M': got = M_(g, prev, oh, ol, **p)
                elif f == 'N': got = N_(g, oh, ol, **p)
                elif f == 'O': got = O_(g, r10[k], r10[max(0, k - 14):k] if k >= 15 else [], **p)
                else: got = P_(g, daily, k, **p)
                checked += 1
                if abs(got[0] - P[k, j]) > 1e-6 or got[1] != NT[k, j]:
                    bad += 1
                    if bad <= 12: print(f"   MISMATCH {name} {d} {f} {p}: check {got[0]:.2f}/{got[1]} vs build {P[k, j]:.2f}/{NT[k, j]}")
        print(f"   {name}: 150 sessions x 66 variants, 50 value areas checked", flush=True)
    print(f"AUDIT (families): {checked} session-variant pairs, {bad} mismatches; value areas {va_bad} mismatches -> "
          f"{'PASSED' if bad == 0 and va_bad == 0 else 'FAILED'}")


def holdout():
    import structure_test as st, regime_test as rt
    m = json.load(open(os.path.join(WORK, 'struct_model.json'))); s, cat, K_ = m['model']
    dates, R, NT, c, p_ref, full = st.joined_all('MNQ')
    # states rebuilt with pandas instead of the loop in structure_test.states
    d = full; newf = d.seg.ne(d.seg.shift()).to_numpy(copy=True); newf[0] = True
    rng_ = (d.H - d.L)
    grp = pd.Series(np.cumsum(newf), index=d.index)
    prev = lambda col: d[col].groupby(grp).shift(1)
    T2 = np.where((d.O >= prev('val')) & (d.O <= prev('vah')), 0.0, np.where((d.O >= prev('L')) & (d.O <= prev('H')), 1.0, 2.0))
    T2 = np.where(prev('vah').isna(), np.nan, T2)
    r_p = rng_.groupby(grp).shift(1)
    nr4 = (r_p < pd.concat([rng_.groupby(grp).shift(q) for q in (2, 3, 4)], axis=1).min(axis=1, skipna=False))
    nr7 = (r_p < pd.concat([rng_.groupby(grp).shift(q) for q in range(2, 8)], axis=1).min(axis=1, skipna=False))
    idy = (prev('H') < d.H.groupby(grp).shift(2)) & (prev('L') > d.L.groupby(grp).shift(2))
    ok = rng_.groupby(grp).shift(7).notna()
    T1 = np.where(ok, (nr7 | (idy & nr4)).astype(float), np.nan)
    st_df = pd.DataFrame({'T1': T1, 'T2': T2, 'T3': d.otype.values, 'T4': d.vr.values}, index=d.index)
    pc = d.C.groupby(grp).shift(1); tr = pd.concat([d.H, pc], axis=1).max(axis=1, skipna=False) - pd.concat([d.L, pc], axis=1).min(axis=1, skipna=False)
    span = d.H.groupby(grp).transform(lambda x: x.rolling(14).max()) - d.L.groupby(grp).transform(lambda x: x.rolling(14).min())
    chop = 100 * np.log10(tr.groupby(grp).transform(lambda x: x.rolling(14).sum()) / span) / np.log10(14)
    st_df['T5'] = chop.groupby(grp).shift(1).values
    st_df = st_df.reindex(dates)
    disc, hold = rt.between(dates, rt.DISC), rt.between(dates, rt.HOLD)
    cid = st.cells(st_df, s, disc)
    sel = {int(k): v for k, v in m['sel'].items()}; r = np.zeros(len(dates))
    for cc, lst in sel.items():
        for j, w in lst: r[cid == cc] += w * R[cid == cc, j]
    saved = np.load(os.path.join(WORK, 'struct_holdout.npz'))
    diff = np.abs(r[hold] - saved['model']).max(); same = (cid[hold] == saved['cid']).mean()
    print(f"AUDIT (holdout): rebuilt series max |difference| {diff:.1e}, same cell on {same * 100:.2f}% of days -> {'PASSED' if diff < 1e-9 and same == 1 else 'FAILED'}")


if __name__ == '__main__':
    {'families': families, 'holdout': holdout}.get(sys.argv[1] if len(sys.argv) > 1 else '', lambda: print(__doc__))()
