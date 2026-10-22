"""
research_engine.py - one engine for the six strategy families in VARIANTS_PLAN.txt.

Every family returns a daily P&L array aligned to Market.days (1 contract, after costs) plus an entries count.
Fills: signal at a bar close -> next bar open +/- 1 tick; stops fill at the stop -/+ 1 tick (or the open if it gaps
through); limit targets fill at the limit; stop and target in the same bar = stop; flat at the last RTH close.
"""
import itertools
import numpy as np, pandas as pd
from sim import load

TICK = 0.25
NS = 78          # 5-minute RTH slots, 9:35 .. 16:00
WARMUP = 30      # first 30 valid days of every file are excluded from all statistics


class Day:
    __slots__ = ('date', 'O', 'H', 'L', 'C', 'V', 'M', 'A', 'vw', 'slot', 'eff', 'nb')


class State:
    __slots__ = ('ub', 'lb', 'mpos', 'valid')


class Market:
    def __init__(self, path, pv, name, cap=1.0):
        self.path, self.pv, self.name, self.cap = path, pv, name, cap
        df = load(path)
        df['date'] = df.time.dt.date
        r = df[df.in_rth.values]
        days = []
        for d, g in r.groupby('date', sort=True):
            M = g.etmin.values.astype(int)
            if len(g) < 30 or M[0] != 575:
                continue
            x = Day()
            x.date = d; x.M = M; x.nb = len(M)
            x.O = g.open.values.astype(float); x.H = g.high.values.astype(float)
            x.L = g.low.values.astype(float); x.C = g.close.values.astype(float)
            x.V = g.volume.values.astype(float); x.A = g.atr.values.astype(float)
            tp = (x.H + x.L + x.C) / 3
            x.vw = np.cumsum(tp * x.V) / np.maximum(np.cumsum(x.V), 1)
            x.slot = (M - 575) // 5
            c = x.C[M <= 600]
            ch = np.abs(np.diff(np.r_[x.O[0], c])).sum()
            x.eff = abs(c[-1] - x.O[0]) / ch if ch > 0 else 0.0
            days.append(x)
        self.days = days
        self.n = len(days)
        self.dates = pd.to_datetime([x.date for x in days])
        MV = np.full((self.n, NS), np.nan)
        for k, x in enumerate(days):
            MV[k, x.slot] = np.abs(x.C / x.O[0] - 1)
        self.MV = MV
        self.pc = np.r_[np.nan, [x.C[-1] for x in days[:-1]]]
        self._sig, self._state, self._of = {}, None, None

    def sigma(self, L):
        """sigma[k, slot] = mean of |C/O930-1| at that slot over the previous L valid days (NaN if any missing)."""
        if L not in self._sig:
            n = self.n
            out = np.full((n, NS), np.nan)
            need = max(1, (L + 1) // 2)
            for k in range(L, n):
                blk = self.MV[k - L:k]; cnt = (~np.isnan(blk)).sum(axis=0)
                with np.errstate(invalid='ignore'):
                    mu = np.nanmean(blk, axis=0)
                mu[cnt < need] = np.nan
                out[k] = mu                                   # average over the sessions that have the slot, as in published.S1
            self._sig[L] = out
        return self._sig[L]

    def state(self):
        """Published base-case noise area + momentum state machine (VM 1, 14 days, checks 10:00..15:30 every 30)."""
        if self._state is None:
            sig = self.sigma(14); checks = set(range(600, 931, 30)); st = []
            for k, x in enumerate(self.days):
                s = State(); s.valid = k >= 14 and not np.isnan(self.pc[k])
                if s.valid:
                    sg = sig[k, x.slot]
                    s.ub = max(x.O[0], self.pc[k]) * (1 + sg); s.lb = min(x.O[0], self.pc[k]) * (1 - sg)
                    mp = np.zeros(x.nb, int); p = 0
                    for i in range(x.nb):
                        if x.M[i] in checks and not np.isnan(sg[i]):
                            c = x.C[i]
                            if p == 0: p = 1 if c > s.ub[i] else (-1 if c < s.lb[i] else 0)
                            elif p == 1: p = -1 if c < s.lb[i] else (0 if c < max(s.ub[i], x.vw[i]) else 1)
                            else: p = 1 if c > s.ub[i] else (0 if c > min(s.lb[i], x.vw[i]) else -1)
                        mp[i] = p
                    s.mpos = mp
                st.append(s)
            self._state = st
        return self._state

    def onfade_days(self):
        if self._of is None:
            import onfade
            self._of = onfade.prepare(self.path)
        return self._of


# ------------------------------------------------------------------------------------------------ A momentum
def fam_A(mk, vm=1.0, look=14, step=30, trail='paper', eff=None, direction='both', first=600, last=930,
          slip=TICK, comm=0.5):
    sig = mk.sigma(look); pv = mk.pv; checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]):
            continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        can = eff is None or x.eff >= eff
        pos = 0; entry = 0.0; p = 0.0; n_ = 0
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2          # partial session: flat on the bar before the last
        for i in range(flat_i):
            if M[i] not in checks or np.isnan(sg[i]):
                continue
            c = C[i]; ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
            if pos == 0:
                new = (1 if c > ub else (-1 if c < lb else 0)) if can else 0
            elif pos == 1:
                if c < lb: new = -1
                else:
                    thr = max(ub, vw[i]) if trail == 'paper' else (ub if trail == 'band' else vw[i])
                    new = 0 if c < thr else 1
            else:
                if c > ub: new = 1
                else:
                    thr = min(lb, vw[i]) if trail == 'paper' else (lb if trail == 'band' else vw[i])
                    new = 0 if c > thr else -1
            if direction == 'long' and new == -1: new = 0
            if direction == 'short' and new == 1: new = 0
            if new != pos:
                px = O[i + 1]
                if pos != 0: p += (px - slip * pos - entry) * pos * pv - comm
                if new != 0: entry = px + slip * new; p -= comm; n_ += 1
                pos = new
        if pos != 0: p += (C[flat_i] - slip * pos - entry) * pos * pv - comm
        pnl[k] = p; nt[k] = n_
    return pnl, nt


# ------------------------------------------------------------------------------------------------ B opening range
def _manage(O, H, L, C, i0, nb, dr, e, stp, tgt, slip, entry_at_open):
    for i in range(i0, nb):
        first = (i == i0)
        if dr > 0:
            if L[i] <= stp:
                return (stp if (first or O[i] > stp) else O[i]) - slip
            if tgt is not None and H[i] >= tgt and (entry_at_open or not first or C[i] >= tgt):
                return tgt
        else:
            if H[i] >= stp:
                return (stp if (first or O[i] < stp) else O[i]) + slip
            if tgt is not None and L[i] <= tgt and (entry_at_open or not first or C[i] <= tgt):
                return tgt
    return C[nb - 1] - slip * dr


def fam_B(mk, orm=5, R=10.0, stop='side', entry='candle', cutoff=720, slip=TICK, comm=0.5):
    nbar = orm // 5; pv = mk.pv
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        O, H, L, C, M, nb = x.O, x.H, x.L, x.C, x.M, x.nb
        if nb < nbar + 2 or M[nbar - 1] != 575 + 5 * (nbar - 1):
            continue
        oh = H[:nbar].max(); ol = L[:nbar].min(); mid = (oh + ol) / 2
        if entry == 'candle':
            oc = C[nbar - 1]
            if oc == O[0]: continue
            dr = 1 if oc > O[0] else -1; i0 = nbar; e = O[i0] + slip * dr; at_open = True
        else:
            lu, ld = oh + TICK, ol - TICK; dr = 0
            for i in range(nbar, nb):
                if M[i] > cutoff: break
                up = H[i] >= lu; dn = L[i] <= ld
                if not (up or dn): continue
                if up and dn: dr = 1 if abs(O[i] - lu) <= abs(O[i] - ld) else -1
                else: dr = 1 if up else -1
                e = (max(O[i], lu) if dr > 0 else min(O[i], ld)) + slip * dr; i0 = i; break
            if dr == 0: continue
            at_open = False
        stp = (ol if dr > 0 else oh) if stop == 'side' else mid
        risk = (e - stp) * dr
        if risk <= 0: continue
        tgt = e + dr * R * risk if R is not None else None
        ex = _manage(O, H, L, C, i0, nb, dr, e, stp, tgt, slip, at_open)
        pnl[k] = (ex - e) * dr * pv - 2 * comm; nt[k] = 1
    return pnl, nt


# ------------------------------------------------------------------------------------------------ C overnight fade
def fam_C(mk, n_out=2, stop_atr=1.5, exit='close', on_ret='any'):
    import onfade
    v = onfade.V(n_out=n_out, stop_atr=stop_atr, exit=exit, on_ret=on_ret, max_stop_pts=75.0 * mk.cap, pv=mk.pv)
    tr = onfade.run(mk.onfade_days(), v)
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    if len(tr):
        idx = {d: k for k, d in enumerate(mk.dates.date)}
        for d, g in tr.groupby('tday'):
            k = idx.get(pd.Timestamp(d).date())
            if k is not None: pnl[k] = g.net.sum(); nt[k] = len(g)
    return pnl, nt


# ------------------------------------------------------------------------------------------------ D double top / bottom
def fam_D(mk, tol=0.15, pull=1.0, tgt='vwap', buf=0.25, state=True, slip=TICK, comm=0.5):
    st = mk.state(); pv = mk.pv
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        s = st[k]
        if not s.valid: continue
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        ub, lb, mpos = s.ub, s.lb, s.mpos
        hod, lod = H[0], L[0]; hod_i = lod_i = 0; min_since_hod, max_since_lod = L[0], H[0]
        pos = 0; pend = None; n_today = 0; p = 0.0; n_ = 0
        for i in range(nb):
            if pend is not None and pos == 0:
                dr, stop, tv = pend; e = O[i] + slip * dr; pend = None
                risk = (e - stop) * dr
                if risk > 0:
                    t_ = tv if tgt == 'vwap' else e + dr * (1.0 if tgt == '1R' else 2.0) * risk
                    if (dr > 0 and t_ > e) or (dr < 0 and t_ < e):
                        pos = dr; entry = e; target = t_; stp = stop; n_ += 1
            if pos != 0:
                ex = None
                if (pos < 0 and H[i] >= stp) or (pos > 0 and L[i] <= stp): ex = stp - slip * pos
                elif (pos < 0 and L[i] <= target) or (pos > 0 and H[i] >= target): ex = target
                elif M[i] >= 955: ex = C[i] - slip * pos
                if ex is not None: p += (ex - entry) * pos * pv - 2 * comm; pos = 0
            a = A[i]; t = tol * a
            ok = pos == 0 and pend is None and n_today < 2 and 630 <= M[i] <= 900 and a > 0
            if ok and state and not (mpos[i] == 0 and lb[i] < C[i] < ub[i]): ok = False
            if ok:
                est_h = (i - hod_i) >= 6 and (hod - min_since_hod) >= pull * a
                if est_h and hod - t <= H[i] <= hod + t and C[i] < hod - t and vw[i] < C[i]:
                    pend = (-1, max(hod, H[i]) + buf * a, vw[i]); n_today += 1
                est_l = (i - lod_i) >= 6 and (max_since_lod - lod) >= pull * a
                if pend is None and est_l and lod - t <= L[i] <= lod + t and C[i] > lod + t and vw[i] > C[i]:
                    pend = (1, min(lod, L[i]) - buf * a, vw[i]); n_today += 1
            if H[i] > hod + t or (H[i] > hod and i == 0): hod, hod_i, min_since_hod = H[i], i, L[i]
            else:
                min_since_hod = min(min_since_hod, L[i]); hod = max(hod, H[i])
            if L[i] < lod - t or (L[i] < lod and i == 0): lod, lod_i, max_since_lod = L[i], i, H[i]
            else:
                max_since_lod = max(max_since_lod, H[i]); lod = min(lod, L[i])
        if pos != 0: p += (C[-1] - slip * pos - entry) * pos * pv - 2 * comm
        pnl[k] = p; nt[k] = n_
    return pnl, nt


# ------------------------------------------------------------------------------------------------ E band reversion
def fam_E(mk, zone=0.5, stop='band', tgt='vwap', state=True, slip=TICK, comm=0.5):
    st = mk.state(); pv = mk.pv
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        s = st[k]
        if not s.valid: continue
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        ub, lb, mpos = s.ub, s.lb, s.mpos
        pos = 0; pend = None; n_today = 0; p = 0.0; n_ = 0
        for i in range(nb):
            if pend is not None and pos == 0:
                dr, stop_px, tv = pend; e = O[i] + slip * dr; pend = None
                risk = (e - stop_px) * dr
                if risk > 0:
                    t_ = tv if tgt == 'vwap' else (e + dr * risk if tgt == '1R' else None)
                    if t_ is None or (dr > 0 and t_ > e) or (dr < 0 and t_ < e):
                        pos = dr; entry = e; target = t_; stp = stop_px; n_ += 1
            if pos != 0:
                ex = None
                if (pos > 0 and L[i] <= stp) or (pos < 0 and H[i] >= stp): ex = stp - slip * pos
                elif target is not None and ((pos > 0 and H[i] >= target) or (pos < 0 and L[i] <= target)): ex = target
                elif M[i] >= 955: ex = C[i] - slip * pos
                if ex is not None: p += (ex - entry) * pos * pv - 2 * comm; pos = 0
            if pos != 0 or pend is not None or n_today >= 2: continue
            if not (630 <= M[i] <= 900) or np.isnan(ub[i]): continue
            if state and mpos[i] != 0: continue
            c = C[i]; a = A[i]
            if lb[i] < c < ub[i]:
                if c > vw[i] + zone * (ub[i] - vw[i]) and ub[i] > vw[i]:
                    pend = (-1, ub[i] + (0.5 * a if stop == 'band+' else 0.0), vw[i]); n_today += 1
                elif c < vw[i] - zone * (vw[i] - lb[i]) and lb[i] < vw[i]:
                    pend = (1, lb[i] - (0.5 * a if stop == 'band+' else 0.0), vw[i]); n_today += 1
        if pos != 0: p += (C[-1] - slip * pos - entry) * pos * pv - 2 * comm
        pnl[k] = p; nt[k] = n_
    return pnl, nt


# ------------------------------------------------------------------------------------------------ F VWAP pullback continuation
def fam_F(mk, kk=0.0, s_atr=0.5, exit='close', maxn=1, slip=TICK, comm=0.5):
    st = mk.state(); pv = mk.pv
    pnl = np.zeros(mk.n); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        s = st[k]
        if not s.valid: continue
        O, H, L, C, M, A, vw, nb = x.O, x.H, x.L, x.C, x.M, x.A, x.vw, x.nb
        mpos = s.mpos
        pos = 0; pend = None; n_today = 0; p = 0.0; n_ = 0; exit_next = False
        for i in range(nb):
            if exit_next and pos != 0:
                p += (O[i] - slip * pos - entry) * pos * pv - 2 * comm; pos = 0; exit_next = False
            if pend is not None and pos == 0:
                dr, stop_px = pend; e = O[i] + slip * dr; pend = None
                risk = (e - stop_px) * dr
                if risk > 0:
                    pos = dr; entry = e; stp = stop_px; n_ += 1
                    target = e + dr * 2.0 * risk if exit == '2R' else None
            if pos != 0:
                ex = None
                if (pos > 0 and L[i] <= stp) or (pos < 0 and H[i] >= stp): ex = stp - slip * pos
                elif target is not None and ((pos > 0 and H[i] >= target) or (pos < 0 and L[i] <= target)): ex = target
                elif M[i] >= 955: ex = C[i] - slip * pos
                if ex is not None: p += (ex - entry) * pos * pv - 2 * comm; pos = 0
                elif exit == 'mom' and mpos[i] != pos and i < nb - 1: exit_next = True
            if pos != 0 or pend is not None or n_today >= maxn: continue
            if not (600 < M[i] <= 900): continue
            a = A[i]; m = mpos[i]
            if m == 1 and L[i] <= vw[i] + kk * a and C[i] > vw[i]:
                pend = (1, vw[i] - s_atr * a); n_today += 1
            elif m == -1 and H[i] >= vw[i] - kk * a and C[i] < vw[i]:
                pend = (-1, vw[i] + s_atr * a); n_today += 1
        if pos != 0: p += (C[-1] - slip * pos - entry) * pos * pv - 2 * comm
        pnl[k] = p; nt[k] = n_
    return pnl, nt


FAMS = {'A': fam_A, 'B': fam_B, 'C': fam_C, 'D': fam_D, 'E': fam_E, 'F': fam_F}
FAM_NAMES = {'A': 'Noise-area momentum', 'B': 'Opening-range breakout', 'C': 'Overnight-range fade',
             'D': 'Double top/bottom at HOD/LOD', 'E': 'Noise-area band reversion', 'F': 'VWAP-pullback continuation'}


def grid():
    g = []
    for vm, L, st, tr, ef in itertools.product((0.5, 0.75, 1.0, 1.25, 1.5, 2.0), (7, 14, 21, 30), (15, 30, 60),
                                               ('paper', 'band', 'vwap'), (None, 0.30)):
        g.append(('A', dict(vm=vm, look=L, step=st, trail=tr, eff=ef)))
    for orm, R, sp, en in itertools.product((5, 15, 30, 60), (1.0, 2.0, 3.0, 5.0, 10.0, None), ('side', 'mid'), ('candle', 'stop')):
        g.append(('B', dict(orm=orm, R=R, stop=sp, entry=en)))
    for n_out, sa, ex, onr in itertools.product((1, 2, 3), (1.0, 1.5, 2.0), ('close', 'T1'), ('any', 'reversal')):
        g.append(('C', dict(n_out=n_out, stop_atr=sa, exit=ex, on_ret=onr)))
    for tol, pull, tg, buf, stt in itertools.product((0.10, 0.20), (0.5, 1.0, 2.0), ('vwap', '1R', '2R'), (0.25, 0.5), (True, False)):
        g.append(('D', dict(tol=tol, pull=pull, tgt=tg, buf=buf, state=stt)))
    for z, sp, tg, stt in itertools.product((0.5, 0.75), ('band', 'band+'), ('vwap', '1R', 'close'), (True, False)):
        g.append(('E', dict(zone=z, stop=sp, tgt=tg, state=stt)))
    for kk, sa, ex, mx in itertools.product((0.0, 0.25), (0.5, 1.0), ('close', '2R', 'mom'), (1, 2)):
        g.append(('F', dict(kk=kk, s_atr=sa, exit=ex, maxn=mx)))
    return g


def label(fam, prm):
    return fam + ' ' + ' '.join(f"{k}={v}" for k, v in prm.items())


def stats(mk, pnl, nt, a, b):
    idx = np.arange(mk.n)
    m = (idx >= WARMUP) & (mk.dates >= pd.Timestamp(a)) & (mk.dates <= pd.Timestamp(b))
    p = np.nan_to_num(pnl[m]); n = len(p)
    if n < 3: return dict(days=n, trades=0, net=0.0, t=np.nan, mdd=0.0, pf=np.nan)
    sd = p.std(ddof=1)
    eq = np.cumsum(p); mdd = (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()
    gp = p[p > 0].sum(); gl = -p[p < 0].sum()
    return dict(days=n, trades=int(nt[m].sum()), net=float(p.sum()), t=float(p.mean() / (sd / np.sqrt(n))) if sd > 0 else np.nan,
                mdd=float(mdd), pf=float(gp / gl) if gl > 0 else np.nan)
