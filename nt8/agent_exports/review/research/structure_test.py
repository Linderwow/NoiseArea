r"""Market-structure methods from the web, as setups and as state detectors, per STRUCTURE_PLAN.txt (pre-registered 2026-10-20).
    python structure_test.py build      the 62 new variants on the three files -> regime_work\<file>_struct.npz (+ levels; 66 variants)
    python structure_test.py discover   standalone discovery figures, 28 switcher configurations on FIT/VALIDATE, the chosen one
    python structure_test.py holdout    the single holdout run: standalone edges, additions, the switcher, robustness
Needs regime_work\ from regime_test.py build / build_db (the 733 earlier variants).
"""
import os, sys, json
import numpy as np, pandas as pd
import regime_test as rt
from research_engine import Market
from sim import load

TICK = 0.25


# ------------------------------------------------------------------------------------------------ levels per session
def value_area(H, L, V, ref_price):
    """(VAL, VAH): 70% of volume around the point of control; each bar's volume spread evenly over its range."""
    b = 0.25 if ref_price < 8000 else 1.0
    lo_i = np.floor(L / b).astype(int); hi_i = np.floor(H / b).astype(int); base = lo_i.min()
    vol = np.zeros(hi_i.max() - base + 1)
    for l_, h_, v in zip(lo_i - base, hi_i - base, V):
        vol[l_:h_ + 1] += v / (h_ - l_ + 1)
    poc = int(np.argmax(vol)); lo = hi = poc; acc = vol[poc]; target = 0.7 * vol.sum()
    while acc < target:
        up = vol[hi + 1] if hi + 1 < len(vol) else -1.0; dn = vol[lo - 1] if lo > 0 else -1.0
        if up < 0 and dn < 0: break
        if up >= dn: hi += 1; acc += up
        else: lo -= 1; acc += dn
    return (base + lo) * b, (base + hi + 1) * b


def prep(name):
    mk = Market(rt.FILES[name], 1.0, name)
    df = load(rt.FILES[name]); eth = df[(~df.in_rth) & ((df.etmin <= 570) | (df.etmin > 1080))]
    onh = eth.groupby('tday').high.max(); onl = eth.groupby('tday').low.min()
    onh.index = pd.to_datetime(onh.index); onl.index = pd.to_datetime(onl.index)
    mk.onh = onh.reindex(mk.dates).values; mk.onl = onl.reindex(mk.dates).values
    mk.o = np.array([x.O[0] for x in mk.days]); mk.h = np.array([x.H.max() for x in mk.days])
    mk.l = np.array([x.L.min() for x in mk.days]); mk.c = np.array([x.C[-1] for x in mk.days])
    va = [value_area(x.H, x.L, x.V, x.O[0]) for x in mk.days]
    mk.val = np.array([v[0] for v in va]); mk.vah = np.array([v[1] for v in va])
    return mk


def open_type(x, tol):
    """(type, direction) from the first six bars: 0 drive, 1 test-reverse, 2 auction; None if the bars are missing."""
    if x.nb < 8 or x.M[5] != 600: return None
    O0 = x.O[0]; hi, lo = x.H[:6].max(), x.L[:6].min(); c = x.C[5]; a = x.A[5]; r = hi - lo
    if a <= 0 or r <= 0: return (2, 0)
    if lo >= O0 - tol * a and c > O0 and c >= hi - 0.25 * r: return (0, 1)
    if hi <= O0 + tol * a and c < O0 and c <= lo + 0.25 * r: return (0, -1)
    if lo <= O0 - tol * a and c > O0 and c >= hi - r / 3: return (1, 1)
    if hi >= O0 + tol * a and c < O0 and c <= lo + r / 3: return (1, -1)
    return (2, 0)


def manage(O, H, L, C, M, vw, i0, dr, e, stp, tgt, vwap_exit=False):
    """Exit price from bar i0 on: stop (or the open if gapped through), target (limit), a close back through VWAP (next open),
    the 15:55 close, else the last close."""
    nb = len(C)
    for i in range(i0, nb):
        if dr > 0 and L[i] <= stp: return stp if (i == i0 or O[i] > stp) else O[i]
        if dr < 0 and H[i] >= stp: return stp if (i == i0 or O[i] < stp) else O[i]
        if tgt is not None and ((dr > 0 and H[i] >= tgt) or (dr < 0 and L[i] <= tgt)): return tgt
        if M[i] >= 955: return C[i]
        if vwap_exit and (C[i] - vw[i]) * dr < 0 and i + 1 < nb: return O[i + 1]
    return C[-1]


# ------------------------------------------------------------------------------------------------ families J..P (gross points)
def fam_J(mk, types='both', tol=0.5, stop='open', ex='close'):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for d, x in enumerate(mk.days):
        ot = open_type(x, tol)
        if ot is None or ot[0] == 2 or (types == 'drive' and ot[0] != 0): continue
        dr = ot[1]; O, H, L, C, M, A, vw = x.O, x.H, x.L, x.C, x.M, x.A, x.vw; e = O[6]
        stp = (O[0] - dr * 0.5 * A[5]) if stop == 'open' else (L[:6].min() if dr > 0 else H[:6].max())
        if (e - stp) * dr <= 0: continue
        pnl[d] = (manage(O, H, L, C, M, vw, 6, dr, e, stp, None, ex == 'vwap') - e) * dr; nt[d] = 1
    return pnl, nt


def fam_K(mk, mode='break', lvl='mid', ex='close', filt='none'):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int); widths = np.full(mk.n, np.nan)
    for d, x in enumerate(mk.days):
        if x.nb >= 14 and x.M[11] == 630: widths[d] = x.H[:12].max() - x.L[:12].min()
    for d, x in enumerate(mk.days):
        if np.isnan(widths[d]): continue
        if filt == 'narrow':
            mw = np.nanmean(widths[max(0, d - 14):d]) if d >= 14 else np.nan
            if not widths[d] < 0.8 * mw: continue
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        ibh, ibl = H[:12].max(), L[:12].min(); mid = (ibh + ibl) / 2
        for i in range(12, nb - 1):
            if M[i] > 840: break
            a = A[i]; dr = 0
            if mode == 'break':
                if C[i] > ibh + 0.1 * a: dr = 1
                elif C[i] < ibl - 0.1 * a: dr = -1
                if dr:
                    e = O[i + 1]; stp = mid if lvl == 'mid' else (ibl if dr > 0 else ibh)
                    tgt = e + 2 * (e - stp) if ex == '2R' else None
            else:
                if H[i] > ibh and C[i] < ibh: dr = -1
                elif L[i] < ibl and C[i] > ibl: dr = 1
                if dr:
                    e = O[i + 1]; stp = H[i] + 0.25 * a if dr < 0 else L[i] - 0.25 * a
                    tgt = (e + 2 * (e - stp) if ex == '2R' else (mid if lvl == 'mid' else (ibl if dr < 0 else ibh)))
            if dr:
                if (e - stp) * dr > 0 and (tgt is None or (tgt - e) * dr > 0):
                    pnl[d] = (manage(O, H, L, C, M, vw, i + 1, dr, e, stp, tgt) - e) * dr; nt[d] = 1
                break
    return pnl, nt


def fam_L(mk, h=2, s=0.5):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for d, x in enumerate(mk.days):
        if d == 0: continue
        vah, val = mk.vah[d - 1], mk.val[d - 1]; O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        if O[0] > vah: dr = -1
        elif O[0] < val: dr = 1
        else: continue
        cnt = 0
        for i in range(nb - 1):
            if M[i] > 780: break
            if M[i] < 600 or (M[i] - 600) % 30: continue
            cnt = cnt + 1 if val < C[i] < vah else 0
            if cnt >= h:
                e = O[i + 1]; tgt = val if dr < 0 else vah; stp = vah + s * A[i] if dr < 0 else val - s * A[i]
                if (e - stp) * dr > 0 and (tgt - e) * dr > 0:
                    pnl[d] = (manage(O, H, L, C, M, vw, i + 1, dr, e, stp, tgt) - e) * dr; nt[d] = 1
                break
    return pnl, nt


def fam_M(mk, levels='both', tgt='2R'):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for d, x in enumerate(mk.days):
        if d == 0: continue
        hs, ls = [], []
        if levels in ('both', 'ON') and not np.isnan(mk.onh[d]): hs.append(mk.onh[d]); ls.append(mk.onl[d])
        if levels in ('both', 'PD'): hs.append(mk.h[d - 1]); ls.append(mk.l[d - 1])
        if not hs: continue
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        up_sw = np.maximum.accumulate(np.array([any(H[i] >= lv + TICK for lv in hs) for i in range(nb)]))
        dn_sw = np.maximum.accumulate(np.array([any(L[i] <= lv - TICK for lv in ls) for i in range(nb)]))
        order = None
        for i in range(2, nb):
            if M[i] > 660: break
            if M[i] < 605: continue
            if up_sw[i] and L[i - 2] > H[i]: order = (-1, H[i], i); break
            if dn_sw[i] and H[i - 2] < L[i]: order = (1, L[i], i); break
        if order is None: continue
        dr, lim, i0 = order
        for j in range(i0 + 1, nb):
            if M[j] > 690: break
            if (dr < 0 and H[j] >= lim + TICK) or (dr > 0 and L[j] <= lim - TICK):
                stp = (H[:j + 1].max() + TICK) if dr < 0 else (L[:j + 1].min() - TICK)
                if (lim - stp) * dr <= 0: break
                t_ = None if tgt == 'close' else lim + (2 if tgt == '2R' else 3) * (lim - stp)
                pnl[d] = (manage(O, H, L, C, M, vw, j, dr, lim, stp, t_) - lim) * dr; nt[d] = 1
                break
    return pnl, nt


def fam_N(mk, levels='OR30', tgt='vwap', buf=0.0):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for d, x in enumerate(mk.days):
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        if levels == 'OR30':
            if nb < 8 or M[5] != 600: continue
            lh, ll = H[:6].max(), L[:6].min()
        else:
            if np.isnan(mk.onh[d]): continue
            lh, ll = mk.onh[d], mk.onl[d]
        used = set(); p = 0.0; n_ = 0; i = 0
        while i < nb - 1:
            if M[i] > 840: break
            if M[i] < 605: i += 1; continue
            a = A[i]; dr = 0
            if -1 not in used and H[i] > lh and C[i] < lh - buf * a: dr = -1; stp = H[i] + 0.25 * a
            elif 1 not in used and L[i] < ll and C[i] > ll + buf * a: dr = 1; stp = L[i] - 0.25 * a
            if dr == 0: i += 1; continue
            used.add(dr); e = O[i + 1]
            t_ = vw[i] if tgt == 'vwap' else ((ll if dr < 0 else lh) if tgt == 'opposite' else e + 2 * (e - stp))
            if (e - stp) * dr <= 0 or (t_ - e) * dr <= 0: i += 1; continue
            xp, k = exit_bar(O, H, L, C, M, i + 1, dr, stp, t_)
            p += (xp - e) * dr; n_ += 1; i = k
        pnl[d] = p; nt[d] = n_
    return pnl, nt


def exit_bar(O, H, L, C, M, i0, dr, stp, tgt):
    """Like manage() without the VWAP exit, but also returns the exit bar index (the bar that can signal next)."""
    for i in range(i0, len(C)):
        if dr > 0 and L[i] <= stp: return (stp if (i == i0 or O[i] > stp) else O[i]), i
        if dr < 0 and H[i] >= stp: return (stp if (i == i0 or O[i] < stp) else O[i]), i
        if tgt is not None and ((dr > 0 and H[i] >= tgt) or (dr < 0 and L[i] <= tgt)): return tgt, i
        if M[i] >= 955: return C[i], len(C)
    return C[-1], len(C)


def fam_O(mk, thr=0.0, entry=930):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int); r = np.full(mk.n, np.nan)
    for d, x in enumerate(mk.days):
        i10 = np.where(x.M == 600)[0]
        if len(i10) and not np.isnan(mk.pc[d]): r[d] = x.C[i10[0]] / mk.pc[d] - 1
    for d, x in enumerate(mk.days):
        if d < 15 or np.isnan(r[d]) or r[d] == 0: continue
        if thr > 0:
            m = np.nanmean(np.abs(r[d - 14:d]))
            if not abs(r[d]) >= thr * m: continue
        ie = np.where(x.M == entry)[0]
        if not len(ie) or ie[0] + 1 >= x.nb: continue
        dr = 1 if r[d] > 0 else -1; e = x.O[ie[0] + 1]
        pnl[d] = (x.C[-1] - e) * dr; nt[d] = 1
    return pnl, nt


def fam_P(mk, filt='none'):
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int); rng = mk.h - mk.l
    for d, x in enumerate(mk.days):
        if d < 11: continue
        if filt != 'none':
            p = d - 1
            nr4 = rng[p] < rng[p - 3:p].min(); nr7 = rng[p] < rng[p - 6:p].min() if p >= 7 else False
            idy = mk.h[p] < mk.h[p - 1] and mk.l[p] > mk.l[p - 1]
            ok = {'NR4': nr4, 'NR7': nr7, 'ID': idy, 'IDNR4': idy and nr4}[filt]
            if not ok: continue
        st = np.mean(np.minimum(mk.h[d - 10:d] - mk.o[d - 10:d], mk.o[d - 10:d] - mk.l[d - 10:d]))
        if not st > 0: continue
        O, H, L, C, M, vw, nb = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb
        bs, ss = O[0] + st, O[0] - st
        for i in range(nb):
            if M[i] > 900: break
            up, dn = H[i] >= bs, L[i] <= ss
            if not (up or dn): continue
            dr = (1 if abs(O[i] - bs) <= abs(O[i] - ss) else -1) if (up and dn) else (1 if up else -1)
            e = max(O[i], bs) if dr > 0 else min(O[i], ss); stp = ss if dr > 0 else bs
            if (up and dn): xp = stp                                              # both stops in one bar: stopped out
            else: xp = manage(O, H, L, C, M, vw, i + 1, dr, e, stp, None) if i + 1 < nb else C[-1]
            pnl[d] = (xp - e) * dr; nt[d] = 1; break
    return pnl, nt


def new_catalog():
    cat = []
    for ty in ('both', 'drive'):
        for tol in (0.5, 1.0):
            for st in ('open', 'extreme'):
                for ex in ('close', 'vwap'): cat.append(('J', dict(types=ty, tol=tol, stop=st, ex=ex)))
    for mo in ('break', 'fade'):
        for lv in ('mid', 'opposite'):
            for ex in ('close', '2R'):
                for fi in ('none', 'narrow'): cat.append(('K', dict(mode=mo, lvl=lv, ex=ex, filt=fi)))
    for h in (2, 1):
        for s in (0.5, 1.0): cat.append(('L', dict(h=h, s=s)))
    for lv in ('both', 'ON', 'PD'):
        for tg in ('2R', '3R', 'close'): cat.append(('M', dict(levels=lv, tgt=tg)))
    for lv in ('OR30', 'ON'):
        for tg in ('vwap', 'opposite', '2R'):
            for b in (0.0, 0.25): cat.append(('N', dict(levels=lv, tgt=tg, buf=b)))
    for th in (0.0, 0.5):
        for en in (930, 900): cat.append(('O', dict(thr=th, entry=en)))
    for fi in ('none', 'NR4', 'NR7', 'ID', 'IDNR4'): cat.append(('P', dict(filt=fi)))
    return cat


NEW = new_catalog()
FNS = {'J': fam_J, 'K': fam_K, 'L': fam_L, 'M': fam_M, 'N': fam_N, 'O': fam_O, 'P': fam_P}
NEW_NAMES = {'J': 'Dalton open type', 'K': 'initial balance', 'L': '80% rule', 'M': 'ICT silver bullet', 'N': 'liquidity sweep / spring',
             'O': 'last-half-hour momentum', 'P': 'Crabel ORB + stretch'}
NEW_DEF = {f: next(j for j, (g, _) in enumerate(NEW) if g == f) for f in FNS}
OFF = len(rt.CAT)                                                # new columns start after the 733 earlier ones
CAT_ALL = rt.CAT + NEW
FAM_ALL = np.array([f for f, _ in CAT_ALL])
FULL_COLS = list(range(rt.N_FULL)) + list(range(OFF, OFF + len(NEW)))
DEF_COLS = list(rt.DEF_IDX) + [OFF + NEW_DEF[f] for f in FNS]
AFTER_10 = set('ADEFGJKLMNO')


def label_all(j):
    f, p = CAT_ALL[j]; return f + ' ' + ' '.join(f"{k}={v}" for k, v in p.items())


def build(name):
    mk = prep(name); P = np.zeros((mk.n, len(NEW))); NT = np.zeros((mk.n, len(NEW)), int)
    for j, (f, p) in enumerate(NEW):
        a, b = FNS[f](mk, **p); P[:, j] = a; NT[:, j] = b
    lv = pd.DataFrame({'onh': mk.onh, 'onl': mk.onl, 'vah': mk.vah, 'val': mk.val,
                       'otype': [(open_type(x, 0.5) or (np.nan, 0))[0] for x in mk.days],
                       'vr': vr_feature(mk)}, index=mk.dates)
    np.savez_compressed(os.path.join(rt.WORK, f'{name}_struct.npz'), P=P, NT=NT, dates=mk.dates.values.astype('datetime64[D]'))
    lv.to_csv(os.path.join(rt.WORK, f'{name}_levels.csv'))
    print(f"   {name}: {len(NEW)} new variants on {mk.n} sessions; trades per family default: " +
          ", ".join(f"{f} {int(NT[:, NEW_DEF[f]].sum())}" for f in FNS), flush=True)


def vr_feature(mk, q=6, look=10):
    rets = [np.diff(np.log(x.C)) for x in mk.days]; out = np.full(mk.n, np.nan)
    for d in range(look, mk.n):
        r1 = np.concatenate(rets[d - look:d]); v1 = r1.var(ddof=1)
        rq = np.concatenate([np.convolve(r, np.ones(q), 'valid') for r in rets[d - look:d] if len(r) >= q])
        out[d] = rq.var(ddof=1) / (q * v1) if v1 > 0 else np.nan
    return out


# ------------------------------------------------------------------------------------------------ joined data and states
def joined_all(second='MNQ'):
    dates, R, NT, c, p_ref, px, P, full, done = rt.joined(second)
    z1 = np.load(os.path.join(rt.WORK, 'NQ_struct.npz')); z2 = np.load(os.path.join(rt.WORK, f'{second}_struct.npz'))
    d1, d2 = pd.to_datetime(z1['dates']), pd.to_datetime(z2['dates'])
    k1 = (np.arange(len(d1)) >= rt.WARMUP) & (d1 <= rt.JOIN); k2 = (np.arange(len(d2)) >= rt.WARMUP) & (d2 > rt.JOIN)
    assert list(d1[k1].append(d2[k2])) == list(dates)
    Pn = np.vstack([z1['P'][k1], z2['P'][k2]]); Nn = np.vstack([z1['NT'][k1], z2['NT'][k2]])
    R = np.hstack([R, Pn / px[:, None] - Nn * 2 * c]); NT = np.hstack([NT, Nn])
    l1 = pd.read_csv(os.path.join(rt.WORK, 'NQ_levels.csv'), index_col=0, parse_dates=True)
    l2 = pd.read_csv(os.path.join(rt.WORK, f'{second}_levels.csv'), index_col=0, parse_dates=True)
    lv = pd.concat([l1[l1.index <= rt.JOIN], l2[l2.index > rt.JOIN]])
    return dates, R, NT, c, p_ref, full.join(lv)


def states(full):
    O, H, L, C, seg = (full[k].values.astype(float) for k in ('O', 'H', 'L', 'C', 'seg')); T = len(full)
    cross = np.r_[True, seg[1:] != seg[:-1]]          # first session of a file: comparisons with the previous one are invalid
    rng = H - L; f = pd.DataFrame(index=full.index, columns=['T1', 'T2', 'T3', 'T4', 'T5'], dtype=float)
    for t in range(T):
        p = t - 1
        if t >= 8 and not cross[max(0, t - 7):t + 1].any():
            nr4 = rng[p] < rng[p - 3:p].min(); nr7 = rng[p] < rng[p - 6:p].min(); idy = H[p] < H[p - 1] and L[p] > L[p - 1]
            f.iat[t, 0] = 1.0 if (nr7 or (idy and nr4)) else 0.0
        if t >= 1 and not cross[t]:
            vah, val = full.vah.values[p], full.val.values[p]
            f.iat[t, 1] = 0.0 if val <= O[t] <= vah else (1.0 if L[p] <= O[t] <= H[p] else 2.0)
        f.iat[t, 2] = full.otype.values[t]
        f.iat[t, 3] = full.vr.values[t]
        if t >= 15 and not cross[t - 14:t + 1].any():
            pc = C[t - 15:t - 1]; hh, ll = H[t - 14:t], L[t - 14:t]
            tr = np.maximum(hh, pc) - np.minimum(ll, pc); span = hh.max() - ll.min()
            f.iat[t, 4] = 100 * np.log10(tr.sum() / span) / np.log10(14) if span > 0 else np.nan
    return f


SCHEMES = ['S0', 'T1', 'T2', 'T3', 'T4', 'T5', 'T6']


def cells(st, scheme, ref):
    n = len(st)
    if scheme == 'S0': return np.zeros(n, int)
    if scheme in ('T1', 'T2', 'T3'):
        v = st[scheme].values; return np.where(np.isnan(v), -1, np.nan_to_num(v)).astype(int)
    if scheme in ('T4', 'T5'):
        v = st[scheme].values; q = np.percentile(v[ref & ~np.isnan(v)], [100 / 3, 200 / 3])
        return np.where(np.isnan(v), -1, np.digitize(np.nan_to_num(v), q)).astype(int)
    a, b = st.T2.values, st.T1.values                                    # T6 = open location x compression
    return np.where(np.isnan(a) | np.isnan(b), -1, np.nan_to_num(a) * 2 + np.nan_to_num(b)).astype(int)


def cols_for(scheme, cat):
    cols = FULL_COLS if cat == 'full' else DEF_COLS
    return [j for j in cols if FAM_ALL[j] in AFTER_10] if scheme == 'T3' else cols


def select(R, NT, cid, mask, cols, K):
    out = {}
    for cc in sorted(set(cid[cid >= 0])):
        m = mask & (cid == cc)
        if m.sum() < 20: out[cc] = []; continue
        t, ntr = rt.tstats(R, NT, m, cols); pick = []
        for k in np.argsort(-t):
            if len(pick) == K or t[k] < rt.T_MIN: break
            if ntr[k] >= rt.MIN_TR and all(FAM_ALL[cols[k]] != FAM_ALL[j] for j in pick): pick.append(cols[k])
        out[cc] = [(int(j), 1.0 / len(pick)) for j in pick]
    return out


CONFIGS = [(s, cat, K) for s in SCHEMES for cat in ('defaults', 'full') for K in (1, 2)]


def best_variants(R, NT, mask, min_tr=100):
    out = {}
    for f in FNS:
        idx = [OFF + j for j, (g, _) in enumerate(NEW) if g == f]
        t, ntr = rt.tstats(R, NT, mask, idx); ok = [k for k in range(len(idx)) if ntr[k] >= min_tr]
        out[f] = (idx[max(ok, key=lambda k: t[k])], max(t[k] for k in ok)) if ok else (None, np.nan)
    return out


def discover():
    dates, R, NT, c, p_ref, full = joined_all('MNQ'); st = states(full).reindex(dates)
    fit, val, disc = rt.between(dates, rt.FIT), rt.between(dates, rt.VAL), rt.between(dates, rt.DISC); N = 2 * p_ref
    print("=== (A) New structure families on the discovery period 2010-07 .. 2019-05 (today's costs, per micro) ===")
    bv = best_variants(R, NT, disc)
    for f in FNS:
        j0 = OFF + NEW_DEF[f]; r0 = R[disc, j0]; j, t = bv[f]
        print(f"   {f} {NEW_NAMES[f]:26s} default: ${r0.sum() * N / (disc.sum() / 252):+7,.0f}/yr Sharpe {rt.sharpe(r0):+.2f} trades {int(NT[disc, j0].sum()):5d}"
              f" | best: t {t:+.2f} {label_all(j) if j is not None else '-'}")
    print(f"   (NoiseArea default: Sharpe {rt.sharpe(R[disc, rt.A_DEF]):+.2f}, t {rt.tstat(R[disc, rt.A_DEF]):+.2f})")
    print("\n   State detectors - share of discovery days per cell and NoiseArea's Sharpe in each cell:")
    for s in SCHEMES[1:]:
        cid = cells(st, s, disc)
        print(f"   {s}: " + " | ".join(f"cell {k}: {np.mean(cid[disc] == k) * 100:.0f}% NA {rt.sharpe(R[disc & (cid == k), rt.A_DEF]):+.2f}"
                                       for k in sorted(set(cid[disc & (cid >= 0)]))))
    print("\n=== (B) 28 switcher configurations: fitted on FIT, scored on VALIDATE ===")
    rows = []
    for cfg in CONFIGS:
        s, cat, K = cfg; cid = cells(st, s, fit); sel = select(R, NT, cid, fit, cols_for(s, cat), K); r, n = rt.apply(R, NT, cid, sel)
        rows.append(dict(scheme=s, catalog=cat, K=K, fit_sharpe=rt.sharpe(r[fit]), val_sharpe=rt.sharpe(r[val]), val_usd_yr=r[val].sum() * N / (val.sum() / 252)))
    tab = pd.DataFrame(rows); print(tab.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    best = max(CONFIGS, key=lambda cfg: (round(tab.val_sharpe[CONFIGS.index(cfg)], 6), -(cfg[0] != 'S0'), -(cfg[1] == 'full'), -cfg[2]))
    cid = cells(st, best[0], disc); sel = select(R, NT, cid, disc, cols_for(best[0], best[1]), best[2])
    print(f"\nNoiseArea VALIDATE Sharpe {rt.sharpe(R[val, rt.A_DEF]):.2f}. CHOSEN: {best} (VALIDATE Sharpe {tab.val_sharpe[CONFIGS.index(best)]:.3f}); refitted on discovery:")
    for k, v in sel.items(): print(f"   cell {k}: " + ("; ".join(label_all(j) for j, _ in v) or "no trade"))
    json.dump(dict(model=list(best), sel={str(k): v for k, v in sel.items()}, best={f: (int(j) if j is not None else None) for f, (j, _) in bv.items()},
                   best_t={f: float(t) for f, (_, t) in bv.items()}), open(os.path.join(rt.WORK, 'struct_model.json'), 'w'), indent=1)


def holdout():
    m = json.load(open(os.path.join(rt.WORK, 'struct_model.json'))); s, cat, K = m['model']
    dates, R, NT, c, p_ref, full = joined_all('MNQ'); st = states(full).reindex(dates)
    disc, hold = rt.between(dates, rt.DISC), rt.between(dates, rt.HOLD); N = 2 * p_ref; hd = dates[hold]
    na, nna = R[:, rt.A_DEF], NT[:, rt.A_DEF].astype(float); Mn = rt.metrics(na[hold], nna[hold], hd, N)
    print(f"=== HOLDOUT 2019-05-06 .. 2026-10-14 ({hold.sum()} sessions), today's costs, per micro ===\n   NoiseArea  {rt.fmt(Mn)}")
    print("\n(A) STANDALONE: each family's best discovery variant, run once")
    flags = {}
    for f in FNS:
        j = m['best'][f]; td = m['best_t'][f]
        if j is None: print(f"   {f}: no variant with 100 trades"); continue
        r, n = R[:, j], NT[:, j].astype(float); M_ = rt.metrics(r[hold], n[hold], hd, N)
        comb = na + r; pb = rt.boot_sharpe_diff(hd, comb[hold], na[hold]); rho = np.corrcoef(r[hold], na[hold])[0, 1]
        edge = td >= 2 and M_['t'] >= 2.45; add = rt.sharpe(comb[hold]) > Mn['sharpe'] and pb < 0.05; flags[f] = (edge, add)
        print(f"   {f} {NEW_NAMES[f]:26s} {label_all(j)}\n      discovery t {td:+.2f} | holdout {rt.fmt(M_)}\n"
              f"      corr with NoiseArea {rho:+.2f}; NoiseArea + it: Sharpe {rt.sharpe(comb[hold]):+.2f} (P {pb:.3f}) -> EDGE {edge}, ADDITION {add}")
    print("\n   Every new variant on the holdout (information): t-stat > 2.45 for " +
          f"{sum(rt.tstat(R[hold, j]) > 2.45 for j in range(OFF, OFF + len(NEW)))} of {len(NEW)}; best " +
          max(((rt.tstat(R[hold, j]), label_all(j)) for j in range(OFF, OFF + len(NEW))), key=lambda z: z[0]).__repr__())
    print(f"\n(B) SWITCHER {s} / {cat} / K={K}")
    cid = cells(st, s, disc); sel = {int(k): [(int(j), w) for j, w in v] for k, v in m['sel'].items()}
    for k, v in sel.items(): print(f"   cell {k} ({int(((cid == k) & hold).sum())} holdout days): " + ("; ".join(label_all(j) for j, _ in v) or "no trade"))
    r, n = rt.apply(R, NT, cid, sel); Mm = rt.metrics(r[hold], n[hold], hd, N)
    pb = rt.boot_sharpe_diff(hd, r[hold], na[hold]); pm, _ = rt.prop_odds(hd, r[hold] * N); pn, _ = rt.prop_odds(hd, na[hold] * N)
    print(f"   model      {rt.fmt(Mm)}\n   bootstrap P(model <= NoiseArea) {pb:.3f}; 50K pass odds model {pm * 100:.0f}% vs NoiseArea {pn * 100:.0f}%")
    v_self = Mm['sharpe'] > Mn['sharpe'] and pb < 0.05 and Mm['t'] > 2
    v_prop = Mm['maxdd'] <= 0.6 * Mn['maxdd'] and Mm['usd_yr'] >= 0.6 * Mn['usd_yr'] and pm >= pn + 0.10
    ex2 = 0.25 / p_ref * 2
    print(f"   at 2 ticks/side: model {rt.fmt(rt.metrics(r[hold] - n[hold] * ex2, n[hold], hd, N))}")
    yr = pd.Series(r[hold] * N, index=hd).groupby(hd.year).sum(); print("   by year: " + " ".join(f"{k} {v:+,.0f}" for k, v in yr.items()))
    rows = []
    for cfg in CONFIGS:
        ci = cells(st, cfg[0], disc); se = select(R, NT, ci, disc, cols_for(cfg[0], cfg[1]), cfg[2]); rr, nn = rt.apply(R, NT, ci, se)
        mm = rt.metrics(rr[hold], nn[hold], hd, N); rows.append(dict(scheme=cfg[0], catalog=cfg[1], K=cfg[2], sharpe=mm['sharpe'], usd_yr=mm['usd_yr'], maxdd=mm['maxdd']))
    tab = pd.DataFrame(rows); print("\n   All 28 configurations on the holdout (information only):\n" + tab.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    print(f"   configurations above NoiseArea's Sharpe {Mn['sharpe']:.2f}: {int((tab.sharpe > Mn['sharpe']).sum())} of 28")
    d2, R2, NT2, _, _, full2 = joined_all('MNQDB'); st2 = states(full2).reindex(d2); h2 = rt.between(d2, rt.HOLD)
    r2, n2 = rt.apply(R2, NT2, cells(st2, s, rt.between(d2, rt.DISC)), sel)
    print(f"\n   Databento holdout: model {rt.fmt(rt.metrics(r2[h2], n2[h2], d2[h2], N))}\n"
          f"                      NoiseArea {rt.fmt(rt.metrics(R2[h2, rt.A_DEF], NT2[h2, rt.A_DEF].astype(float), d2[h2], N))}")
    for f, (edge, add) in flags.items():
        if edge or add:
            j = m['best'][f]; a2 = R2[h2, j]
            print(f"   Databento check for {f}: t {rt.tstat(a2):+.2f}, ${a2.sum() * N / (h2.sum() / 252):+,.0f}/yr; NoiseArea + it Sharpe {rt.sharpe(R2[h2, rt.A_DEF] + a2):+.2f}")
    np.savez_compressed(os.path.join(rt.WORK, 'struct_holdout.npz'), model=r[hold], cid=cid[hold])
    winners = [f for f, (e, a) in flags.items() if e or a]
    print(f"\nVERDICT: standalone edge in {[f for f, (e, a) in flags.items() if e] or 'none'}; useful addition in "
          f"{[f for f, (e, a) in flags.items() if a] or 'none'}; switcher self-funded {v_self}, prop {v_prop} -> "
          + ("SOMETHING PASSED - see the robustness lines" if winners or v_self or v_prop else "DEAD ON THIS DATA"))


if __name__ == '__main__':
    stg = sys.argv[1] if len(sys.argv) > 1 else ''
    if stg == 'build':
        for nm in ('NQ', 'MNQ', 'MNQDB'): build(nm)
    elif stg == 'discover': discover()
    elif stg == 'holdout': holdout()
    else: print(__doc__)
