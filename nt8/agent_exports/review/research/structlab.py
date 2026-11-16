import os, sys, time, types, itertools
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2
import msb

OUT = os.path.join(HERE, 'STRUCT_RESULTS.txt')
CELLS_CSV = os.path.join(HERE, 'regime_work', 'struct_cells.csv')
TICK = 0.25
DISC = (pd.Timestamp('2010-07-01'), pd.Timestamp('2019-05-05'))
HOLD = (pd.Timestamp('2019-05-06'), pd.Timestamp('2026-10-15'))
TFS = ('15m', '1h', '4h'); SIDES = ('L', 'S'); EXITS = ('H4', 'R2')
LEN = {'15m': 15, '1h': 60, '4h': 240}; OFF = {'15m': 0, '1h': 0, '4h': 360}; MINB = {'15m': 8, '1h': 30, '4h': 90}
A_SET = ['HAM', 'INV', 'DFD', 'ENG', 'PRC', 'MST', 'SWP', 'OUT', 'RSI', 'STR', 'ANY']
B_SET = ['E50', 'E200', 'PDL', 'PDH', 'PDC', 'ONL', 'LNL']
SETUPS = A_SET + B_SET + ['X200']
NAME = {'L': {'HAM': 'hammer', 'INV': 'inverted hammer', 'DFD': 'dragonfly doji', 'ENG': 'bullish engulfing', 'PRC': 'piercing line',
              'MST': 'morning star', 'SWP': 'sweep below 20-bar low + reclaim', 'OUT': 'outside reversal up', 'RSI': 'RSI(2) <= 5',
              'STR': 'stretch 2.5 ATR below EMA20', 'ANY': 'any 10-bar low (control)', 'E50': 'EMA50 hold', 'E200': 'EMA200 hold',
              'PDL': 'prior-day low hold', 'PDH': 'prior-day high hold (retest)', 'PDC': 'prior-day close hold', 'ONL': 'overnight low hold',
              'LNL': 'London low hold', 'X200': 'EMA200 hold after losing EMA50'},
        'S': {'HAM': 'shooting star', 'INV': 'hanging man', 'DFD': 'gravestone doji', 'ENG': 'bearish engulfing', 'PRC': 'dark cloud cover',
              'MST': 'evening star', 'SWP': 'sweep above 20-bar high + reject', 'OUT': 'outside reversal down', 'RSI': 'RSI(2) >= 95',
              'STR': 'stretch 2.5 ATR above EMA20', 'ANY': 'any 10-bar high (control)', 'E50': 'EMA50 rejection', 'E200': 'EMA200 rejection',
              'PDL': 'prior-day high rejection', 'PDH': 'prior-day low rejection (retest)', 'PDC': 'prior-day close rejection',
              'ONL': 'overnight high rejection', 'LNL': 'London high rejection', 'X200': 'EMA200 rejection after reclaiming EMA50 (his)'}}
DIMS = {'D': ('all', '3', '5'), 'S': ('all', '1.5'), 'V': ('all', '1.5'), 'T': ('all', 'with', 'against'), 'Z': ('all', 'cash', 'night'),
        'M': ('all', 'with', 'against')}
COMBOS = list(itertools.product(*DIMS.values()))
N_CELLS = len(COMBOS) * len(SETUPS) * 2 * len(EXITS) * len(TFS)
BONF = 4.83
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def sh(x, k=1):
    x = np.asarray(x, float); out = np.full(len(x), np.nan)
    if k < len(x): out[k:] = x[:-k]
    return out


def rmin(x, n): return pd.Series(x).rolling(n, min_periods=n).min().values
def rmax(x, n): return pd.Series(x).rolling(n, min_periods=n).max().values


class Bars:
    def __init__(self, mk, tf):
        tm = mk.t.astype('datetime64[m]').astype(np.int64)
        key = tm - ((tm + OFF[tf]) % LEN[tf])
        b = np.r_[0, np.where(key[1:] != key[:-1])[0] + 1]; e = np.r_[b[1:] - 1, len(key) - 1]
        hi = np.maximum.reduceat(mk.ah, b); lo = np.minimum.reduceat(mk.al, b); vol = np.add.reduceat(mk.v, b)
        keep = ((e - b + 1) >= MINB[tf]) & (mk.iid[b] == mk.iid[e])
        self.mk, self.tf = mk, tf
        self.b, self.e, self.key = b[keep], e[keep], key[b][keep]; self.n = len(self.b)
        self.O, self.C, self.H, self.L, self.V = mk.ao[self.b], mk.ac[self.e], hi[keep], lo[keep], vol[keep]
        self.iid_b, self.iid_e = mk.iid[self.b], mk.iid[self.e]
        self.mod = (self.key % 1440).astype(int)
        self.sess = ((self.key + 360) // 1440).astype(np.int64)
        pc = sh(self.C)
        tr = np.nanmax(np.vstack([self.H - self.L, np.abs(self.H - pc), np.abs(self.L - pc)]), axis=0)
        self.atr = pd.Series(tr).rolling(14).mean().values
        ema = lambda n: pd.Series(self.C).ewm(span=n, adjust=False).mean().values
        self.e20, self.e50, self.e200 = ema(20), ema(50), ema(200)
        d = np.diff(self.C, prepend=self.C[0]); up = pd.Series(np.clip(d, 0, None)).ewm(alpha=0.5, adjust=False).mean().values
        dn = pd.Series(np.clip(-d, 0, None)).ewm(alpha=0.5, adjust=False).mean().values
        with np.errstate(divide='ignore', invalid='ignore'): self.rsi = np.where(dn > 0, 100 - 100 / (1 + up / dn), 100.0)
        self.bias = msb.structure(self.O, self.H, self.L, self.C, 3)[1].astype(int)
        slot = pd.Series(self.V).groupby(self.mod).transform(lambda s: s.shift(1).rolling(20, min_periods=10).mean()).values
        with np.errstate(divide='ignore', invalid='ignore'): self.vr = self.V / slot
        self.levels()
        cons = np.zeros(self.n, bool); brk = np.zeros(self.n, bool)
        nx = np.arange(self.n - 1)
        same = (self.sess[nx + 1] == self.sess[nx]) & (self.key[nx + 1] - self.key[nx] == LEN[tf])
        cons[nx] = same & (self.iid_b[nx + 1] == self.iid_e[nx]); brk[nx] = same & (self.iid_b[nx + 1] != self.iid_e[nx])
        self.link, self.cbreak = cons, brk

    def levels(self):
        mk = self.mk; tm = mk.t.astype('datetime64[m]').astype(np.int64); mod = tm % 1440; sess = (tm + 360) // 1440
        df = pd.DataFrame({'s': sess, 'h': mk.ah, 'l': mk.al, 'c': mk.ac, 'm': mod})
        rth = df[(df.m >= 570) & (df.m < 960)].groupby('s').agg(h=('h', 'max'), l=('l', 'min'), c=('c', 'last'), n=('h', 'size'))
        rth = rth[rth.n >= 200]
        if len(rth):
            ix = np.searchsorted(rth.index.values, self.sess) - 1; okp = ix >= 0; ix = np.maximum(ix, 0)
            self.PDH = np.where(okp, rth.h.values[ix], np.nan); self.PDL = np.where(okp, rth.l.values[ix], np.nan)
            self.PDC = np.where(okp, rth.c.values[ix], np.nan)
        else:
            self.PDH = self.PDL = self.PDC = np.full(self.n, np.nan)
        on = df[(df.m < 570) | (df.m >= 1080)].groupby('s').agg(h=('h', 'max'), l=('l', 'min'))
        ln = df[(df.m >= 180) & (df.m < 510)].groupby('s').agg(h=('h', 'max'), l=('l', 'min'))
        def per(tab, col, start):
            v = tab[col].reindex(self.sess).values
            return np.where((self.mod >= start) & (self.mod < 1020), v, np.nan)
        self.ONH, self.ONL = per(on, 'h', 570), per(on, 'l', 570)
        self.LNH, self.LNL = per(ln, 'h', 510), per(ln, 'l', 510)

    def side(self, s):
        if s == 'L':
            return dict(O=self.O, H=self.H, L=self.L, C=self.C, e20=self.e20, e50=self.e50, e200=self.e200, rsi=self.rsi, bias=self.bias,
                        lv={'PDL': self.PDL, 'PDH': self.PDH, 'PDC': self.PDC, 'ONL': self.ONL, 'LNL': self.LNL})
        return dict(O=-self.O, H=-self.L, L=-self.H, C=-self.C, e20=-self.e20, e50=-self.e50, e200=-self.e200, rsi=100 - self.rsi,
                    bias=-self.bias, lv={'PDL': -self.PDH, 'PDH': -self.PDL, 'PDC': -self.PDC, 'ONL': -self.ONH, 'LNL': -self.LNH})


def setups_long(O, H, L, C, atr, e20, e50, e200, rsi, lv):
    R = H - L; B = np.abs(C - O); LW = np.minimum(O, C) - L; UW = H - np.maximum(O, C)
    with np.errstate(invalid='ignore'):
        ok = (R > 0) & np.isfinite(atr)
        low10 = rmin(L, 10); at10 = L <= low10
        O1, C1, H1, L1 = sh(O), sh(C), sh(H), sh(L); R1 = H1 - L1; O2, C2 = sh(O, 2), sh(C, 2); R2 = sh(H, 2) - sh(L, 2)
        lowp20 = sh(rmin(L, 20))
        out = {}
        out['HAM'] = ok & at10 & (LW >= 2 * B) & (UW <= 0.25 * R)
        out['INV'] = ok & at10 & (UW >= 2 * B) & (LW <= 0.25 * R)
        out['DFD'] = ok & at10 & (B <= 0.1 * R) & (UW <= 0.1 * R)
        out['ENG'] = ok & at10 & (C1 < O1) & (C > O) & (C >= O1) & (O <= C1)
        out['PRC'] = ok & at10 & (C1 < O1) & ((O1 - C1) >= 0.6 * R1) & (C > O) & (C > (O1 + C1) / 2) & (C < O1)
        out['MST'] = ok & (C2 < O2) & ((O2 - C2) >= 0.6 * R2) & (np.abs(C1 - O1) <= 0.3 * R1) & (C > O) & (C > (O2 + C2) / 2) & \
            (np.fmin(np.fmin(L, L1), sh(L, 2)) <= low10)
        out['SWP'] = ok & (L < lowp20) & (C > lowp20)
        out['OUT'] = ok & at10 & (H > H1) & (L < L1) & (C >= L + 0.75 * R)
        out['RSI'] = ok & (rsi <= 5)
        out['STR'] = ok & (C <= e20 - 2.5 * atr)
        out['ANY'] = ok & at10
        e50p, e200p = sh(e50), sh(e200)
        for nm, lvl in (('E50', e50p), ('E200', e200p), ('PDL', lv['PDL']), ('PDH', lv['PDH']), ('PDC', lv['PDC']), ('ONL', lv['ONL']), ('LNL', lv['LNL'])):
            out[nm] = ok & (L <= lvl) & (C > lvl) & (C1 > lvl)
        cd = ((C < e50) & (C1 >= sh(e50))).astype(float)
        recent = pd.Series(cd).shift(1).rolling(10, min_periods=1).max().fillna(0).values > 0
        out['X200'] = ok & (e50 > e200) & recent & (L <= e200p) & (C > e200p)
    return out


def chain(br, h):
    n = br.n; last = np.arange(n); alive = np.ones(n, bool); bad = np.zeros(n, bool)
    first = br.link.copy()
    for step in range(h):
        j = np.minimum(np.arange(n) + step, n - 1)
        bad |= alive & br.cbreak[j]
        go = alive & br.link[j]; last = np.where(go, j + 1, last); alive = go
    return last, first & ~bad


def outcomes(br, s, h_exit):
    mk = br.mk
    if s == 'L': ao, ah, al, ac = mk.ao, mk.ah, mk.al, mk.ac
    else: ao, ah, al, ac = -mk.ao, -mk.al, -mk.ah, -mk.ac
    Lk = br.L if s == 'L' else -br.H
    n = br.n; valid = np.zeros(n, bool); xp = np.full(n, np.nan); jx = np.zeros(n, np.int64); j0 = np.zeros(n, np.int64); px = np.full(n, np.nan)
    if h_exit == 'H4':
        last, ok = chain(br, 4)
        k = np.where(ok)[0]; j0[k] = br.b[k + 1]; px[k] = ao[j0[k]]; jx[k] = br.e[last[k]]; xp[k] = ac[jx[k]]; valid[k] = True
    else:
        last, ok = chain(br, 8)
        k = np.where(ok)[0]; j0k = br.b[k + 1]; pxk = ao[j0k]; stop = Lk[k] - TICK
        good = pxk > stop; k, j0k, pxk, stop = k[good], j0k[good], pxk[good], stop[good]
        tp = pxk + 2 * (pxk - stop); jend = br.e[last[k]]
        xk = ac[jend].copy(); jk = jend.copy()
        order = np.argsort(jend - j0k)
        for ch in np.array_split(order, max(1, len(order) // 3000)):
            if len(ch) == 0: continue
            W = int((jend[ch] - j0k[ch]).max()) + 1
            idx = j0k[ch, None] + np.arange(W)[None, :]; inside = idx <= jend[ch, None]; idx = np.minimum(idx, jend[ch, None])
            hs = (al[idx] <= stop[ch, None]) & inside; ht = (ah[idx] >= tp[ch, None]) & inside
            fs = np.where(hs.any(1), hs.argmax(1), W + 5); ft = np.where(ht.any(1), ht.argmax(1), W + 5)
            st = (fs <= ft) & (fs < W + 5); tg = (ft < fs)
            js = np.minimum(j0k[ch] + np.minimum(fs, W - 1), jend[ch]); jt = np.minimum(j0k[ch] + np.minimum(ft, W - 1), jend[ch])
            sp = np.where(fs == 0, stop[ch], np.minimum(stop[ch], ao[js]))
            xk[ch] = np.where(st, sp, np.where(tg, tp[ch], xk[ch])); jk[ch] = np.where(st, js, np.where(tg, jt, jk[ch]))
        j0[k] = j0k; px[k] = pxk; xp[k] = xk; jx[k] = jk; valid[k] = True
    assert (j0[valid] > br.e[np.where(valid)[0]]).all()
    raw = mk.o[j0]
    with np.errstate(invalid='ignore', divide='ignore'): gross = np.where(valid, (xp - px) / raw, np.nan)
    return dict(valid=valid, gross=gross, j0=j0, jx=jx)


def conditions(br, sd):
    with np.errstate(invalid='ignore', divide='ignore'):
        depth = (rmax(sd['H'], 20) - sd['L']) / br.atr; spike = (sd['H'] - sd['L']) / br.atr
    tr_with = sd['e50'] > sd['e200']; tr_ag = sd['e50'] < sd['e200']
    cash = (br.mod >= 570) & (br.mod < 960)
    return {'D': {'all': None, '3': depth >= 3, '5': depth >= 5}, 'S': {'all': None, '1.5': spike >= 1.5},
            'V': {'all': None, '1.5': br.vr >= 1.5}, 'T': {'all': None, 'with': tr_with, 'against': tr_ag},
            'Z': {'all': None, 'cash': cash, 'night': ~cash}, 'M': {'all': None, 'with': sd['bias'] == 1, 'against': sd['bias'] == -1}}, depth, spike


class Stream:
    def __init__(self, br, s):
        self.br, self.s = br, s; sd = br.side(s)
        self.sd = sd
        self.setups = setups_long(sd['O'], sd['H'], sd['L'], sd['C'], br.atr, sd['e20'], sd['e50'], sd['e200'], sd['rsi'], sd['lv'])
        self.cond, self.depth, self.spike = conditions(br, sd)
        self.out = {x: outcomes(br, s, x) for x in EXITS}

    def combo_mask(self, combo, idx):
        m = np.ones(len(idx), bool)
        for dim, val in zip(DIMS, combo):
            c = self.cond[dim][val]
            if c is not None: m &= np.nan_to_num(c[idx].astype(float)) > 0
        return m


def day_t(net, day):
    if len(net) == 0: return 0, 0, 0.0, 0.0
    u, inv = np.unique(day, return_inverse=True); ds = np.bincount(inv, weights=net)
    nd = len(ds); m = ds.mean(); sd = ds.std(ddof=1) if nd > 1 else 0.0
    return len(net), nd, float(m), float(m / (sd / np.sqrt(nd))) if sd > 0 else 0.0


def one_at_a_time(idx, j0, jx):
    keep = []; busy = -1
    for k in idx:
        if j0[k] > busy: keep.append(k); busy = jx[k]
    return np.array(keep, np.int64)


def cost(ticks): return 2 * L2.side_cost(ticks)


def in_window(br, lo, hi):
    d = br.sess; return (d >= (lo - pd.Timestamp('1970-01-01')).days) & (d <= (hi - pd.Timestamp('1970-01-01')).days)


def year_of(sessday): return pd.to_datetime(sessday, unit='D').year.values


def audit():
    ok = True
    n = 30; O = np.linspace(110, 101, n); C = O - 0.3; H = O + 0.2; L = C - 0.2
    O[25], H[25], L[25], C[25] = 100.0, 100.1, 97.0, 100.0
    atr = np.full(n, 1.0); e = np.full(n, 105.0); lv = {k: np.full(n, np.nan) for k in ('PDL', 'PDH', 'PDC', 'ONL', 'LNL')}
    r = setups_long(O, H, L, C, atr, e, e, e - 1, np.full(n, 50.0), lv)
    a1 = list(np.where(r['HAM'])[0]) == [25]; ok &= a1
    C2 = np.full(n, 103.0); O2 = C2 - 0.1; H2 = C2 + 0.3; L2_ = C2 - 0.3
    e50 = np.full(n, 102.0); e200 = np.full(n, 100.0)
    C2[20] = 101.5; O2[20] = 102.2; L2_[20] = 101.4; H2[20] = 102.3
    C2[21:24] = 101.2; O2[21:24] = 101.3; H2[21:24] = 101.4; L2_[21:24] = 101.0
    O2[24], H2[24], L2_[24], C2[24] = 101.0, 101.1, 99.8, 100.6
    C2[25:] = 101.5; O2[25:] = 101.4; H2[25:] = 101.6; L2_[25:] = 101.3
    r2 = setups_long(O2, H2, L2_, C2, atr, e50, e50, e200, np.full(n, 50.0), lv)
    a2 = list(np.where(r2['X200'])[0]) == [24]; ok &= a2
    lv3 = dict(lv); lv3['PDL'] = np.full(n, 100.0)
    C3 = np.full(n, 102.0); O3 = C3 + 0.1; H3 = C3 + 0.3; L3 = C3 - 0.3
    O3[27], H3[27], L3[27], C3[27] = 101.0, 101.2, 99.5, 100.5
    r3 = setups_long(O3, H3, L3, C3, atr, e50, e50, e200, np.full(n, 50.0), lv3)
    a3 = list(np.where(r3['PDL'])[0]) == [27]; ok &= a3
    t0 = np.datetime64('2015-01-05T06:00'); nm = 6 * 60; t = t0 + np.arange(nm).astype('timedelta64[m]')
    o = np.full(nm, 100.0); hh = o + 0.25; ll = o - 0.25; c = o.copy()
    for k in range(6): o[k * 60:(k + 1) * 60] += k; hh[k * 60:(k + 1) * 60] += k; ll[k * 60:(k + 1) * 60] += k; c[k * 60:(k + 1) * 60] += k
    mk = types.SimpleNamespace(t=t.astype('datetime64[m]'), o=o, h=hh, l=ll, c=c, ao=o, ah=hh, al=ll, ac=c, v=np.ones(nm), iid=np.ones(nm, np.int64), shift=np.zeros(nm))
    br = Bars(mk, '1h')
    oc = outcomes(br, 'L', 'H4')
    a4 = bool(oc['valid'][0]) and oc['j0'][0] == 60 and oc['jx'][0] == 299 and abs(oc['gross'][0] - (104.0 - 101.0) / 101.0) < 1e-12
    ok &= a4
    ll2 = ll.copy(); hh2 = hh.copy(); ll2[130] = 99.0; hh2[130] = 120.0
    mk2 = types.SimpleNamespace(**{**mk.__dict__, 'h': hh2, 'l': ll2, 'ah': hh2, 'al': ll2})
    oc2 = outcomes(Bars(mk2, '1h'), 'L', 'R2')
    a5 = bool(oc2['valid'][0]) and oc2['jx'][0] == 130 and abs(oc2['gross'][0] - (99.5 - 101.0) / 101.0) < 1e-12
    ok &= a5
    say(f"audit detection: hammer {a1}, EMA200 hold after losing EMA50 {a2}, prior-day low hold {a3} | outcomes: H4 entry/exit by hand {a4}, "
        f"R2 minute touching stop and target = stop {a5} -> {'PASS' if ok else 'FAIL'}")
    return ok


def audit_levels(mk):
    br = Bars(mk, '1h'); d0 = br.sess[br.mod == 600][5]
    tm = mk.t.astype('datetime64[m]').astype(np.int64); sess = (tm + 360) // 1440; mod = tm % 1440
    prev = np.unique(sess[(sess < d0) & (mod >= 570) & (mod < 960)])[-1]
    sel = (sess == prev) & (mod >= 570) & (mod < 960)
    k = np.where((br.sess == d0) & (br.mod == 600))[0][0]
    ok = abs(br.PDL[k] - mk.al[sel].min()) < 1e-9 and abs(br.PDH[k] - mk.ah[sel].max()) < 1e-9
    say(f"audit levels: prior-day high/low of session {pd.Timestamp(d0, unit='D'):%Y-%m-%d} recomputed from minutes -> {'PASS' if ok else 'FAIL'}")
    return ok


def build(mk):
    out = {}
    for tf in TFS:
        br = Bars(mk, tf); out[tf] = {s: Stream(br, s) for s in SIDES}
    return out


def grid(streams, window, ticks=1):
    rows = []
    for tf in TFS:
        for s in SIDES:
            st = streams[tf][s]; win = in_window(st.br, *window)
            for x in EXITS:
                oc = st.out[x]; net = oc['gross'] - cost(ticks)
                for su in SETUPS:
                    idx = np.where(st.setups[su] & oc['valid'] & win)[0]
                    day = st.br.sess[idx]; nn = net[idx]
                    cm = {dim: {v: (None if c is None else np.nan_to_num(c[idx].astype(float)) > 0) for v, c in st.cond[dim].items()} for dim in DIMS}
                    for combo in COMBOS:
                        m = np.ones(len(idx), bool)
                        for dim, val in zip(DIMS, combo):
                            c = cm[dim][val]
                            if c is not None: m &= c
                        n, nd, mean, t = day_t(nn[m], day[m])
                        rows.append((tf, s, x, su) + combo + (n, nd, mean * L2.N_USD if n else 0.0, t, (nn[m].mean() * L2.N_USD) if n else 0.0))
    return pd.DataFrame(rows, columns=['tf', 'side', 'exit', 'setup'] + list(DIMS) + ['n', 'days', 'day_usd', 't', 'usd_trade'])


def cell_label(r):
    cond = ', '.join(f"{d}={r[d]}" for d in DIMS if r[d] != 'all') or 'no filter'
    return f"{r['tf']:>3s} {r['side']} {r['exit']} {NAME[r['side']][r['setup']]} [{cond}]"


def cell_events(st, r, window):
    oc = st.out[r['exit']]; win = in_window(st.br, *window)
    idx = np.where(st.setups[r['setup']] & oc['valid'] & win)[0]
    m = st.combo_mask(tuple(r[d] for d in DIMS), idx)
    return one_at_a_time(idx[m], oc['j0'], oc['jx'])


def series(st, ev, ex, ticks=1):
    net = st.out[ex]['gross'][ev] - cost(ticks)
    return pd.Series(net, index=pd.to_datetime(st.br.sess[ev], unit='D')).groupby(level=0).sum()


def q1(streams):
    say(); say("=" * 124); say("Q1  DO BIGGER SELL-OFFS / SPIKES RAISE THE ODDS? (discovery NQ 2010-19, all A setups pooled, H4 exit, $ per trade, day-clustered t)")
    say("=" * 124)
    db = [(-np.inf, 1), (1, 2), (2, 3), (3, 5), (5, np.inf)]; sb = [(-np.inf, 1), (1, 1.5), (1.5, 2), (2, 3), (3, np.inf)]
    for tf in TFS:
        for s in SIDES:
            st = streams[tf][s]; oc = st.out['H4']; win = in_window(st.br, *DISC)
            pool = np.zeros(st.br.n, bool)
            for su in A_SET[:-1]: pool |= st.setups[su]
            idx = np.where(pool & oc['valid'] & win)[0]; net = oc['gross'][idx] - cost(1); day = st.br.sess[idx]
            for lab, val, bk in (('depth', st.depth[idx], db), ('spike', st.spike[idx], sb)):
                cells = []
                for a, b in bk:
                    m = (val >= a) & (val < b); n, nd, mean, t = day_t(net[m], day[m])
                    cells.append(f"{('<' + str(b)) if a == -np.inf else (str(a) + '+') if b == np.inf else f'{a}-{b}':>6s}: ${net[m].mean() * L2.N_USD if n else 0:+6.2f} (t {t:+5.2f}, n {n})")
                say(f"{tf:>3s} {'long ' if s == 'L' else 'short'} {lab}: " + " | ".join(cells))


def main():
    say(f"structlab.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (STRUCT_PLAN.txt)  cells {N_CELLS:,}, Bonferroni bar t {BONF}")
    if not audit(): say("audit FAILED - nothing graded"); return
    nq = L2.Mins('nq')
    if not audit_levels(nq): say("audit FAILED - nothing graded"); return
    S_nq = build(nq)
    band = {'15m': (20000, 24500), '1h': (5000, 6100), '4h': (1300, 1650)}; okb = True
    for tf in TFS:
        y = pd.Series(1, index=year_of(S_nq[tf]['L'].br.sess)).groupby(level=0).sum(); f = y[(y.index >= 2011) & (y.index <= 2018)]
        good = bool(((f >= band[tf][0]) & (f <= band[tf][1])).all()); okb &= good
        say(f"audit bars per full year {tf}: {f.min():,} .. {f.max():,} (band {band[tf][0]:,}-{band[tf][1]:,}) -> {'PASS' if good else 'FAIL'}")
    if not okb: say("audit FAILED - nothing graded"); return
    say(f"build NQ done {time.time() - T0:.0f} s")
    q1(S_nq)
    say(); say("=" * 124); say("BASE CELLS (no filter), discovery NQ 2010-19: $ per trade (day-clustered t) for 15m | 1h | 4h"); say("=" * 124)
    G = grid(S_nq, DISC); G.to_csv(CELLS_CSV, index=False)
    base = G[(G[list(DIMS)] == 'all').all(1)]
    for x in EXITS:
        say(f"-- exit {x}")
        for s in SIDES:
            for su in SETUPS:
                r = base[(base.side == s) & (base.setup == su) & (base.exit == x)].set_index('tf')
                say(f"{'L' if s == 'L' else 'S'} {NAME[s][su]:<46s} " + " | ".join(
                    f"{tf}: ${r.loc[tf, 'usd_trade']:+6.2f} (t {r.loc[tf, 't']:+5.2f}, n {int(r.loc[tf, 'n'])})" for tf in TFS))
    say(); say("=" * 124); say("Q2  GRID ON DISCOVERY"); say("=" * 124)
    el = G[(G.n >= 100) & (G.days >= 80)]
    import math
    for z in (3.0, 4.0, BONF):
        say(f"eligible cells {len(el):,}: t >= {z}: {int((el.t >= z).sum())} (chance alone, if independent: {len(el) * 0.5 * math.erfc(z / math.sqrt(2)):.1f}); t <= -{z}: {int((el.t <= -z).sum())}")
    top = el.sort_values('t', ascending=False).head(25)
    say("top 25 cells by t:")
    for _, r in top.iterrows(): say(f"   t {r.t:+5.2f} | {int(r.n):5d} trades | ${r.usd_trade:+7.2f}/trade | {cell_label(r)}")
    passing = el[el.t >= BONF].sort_values('t', ascending=False).drop_duplicates(['setup', 'side', 'tf']).head(10)
    survivors = []
    mq = None
    if len(passing):
        say(); say("=" * 124); say(f"Q3  HOLDOUT MNQ 2019-26 for {len(passing)} cell(s) clearing t {BONF}"); say("=" * 124)
        mq = L2.Mins('mnqx'); S_mq = build(mq); S_es = build(L2.Mins('es')); S_mes = build(L2.Mins('mes'))
        for _, r in passing.iterrows():
            st = S_mq[r.tf][r.side]; ev = cell_events(st, r, HOLD); s1 = series(st, ev, r.exit); s2 = series(st, ev, r.exit, 2)
            net = st.out[r.exit]['gross'][ev] - cost(1); n, nd, mean, t = day_t(net, st.br.sess[ev])
            yr = s1.groupby(s1.index.year).sum(); drop = np.sort(net)[:-5] if len(net) > 5 else net
            e1 = cell_events(S_es[r.tf][r.side], r, DISC); e2 = cell_events(S_mes[r.tf][r.side], r, HOLD)
            m_es = (S_es[r.tf][r.side].out[r.exit]['gross'][e1] - cost(1)).mean() if len(e1) else np.nan
            m_mes = (S_mes[r.tf][r.side].out[r.exit]['gross'][e2] - cost(1)).mean() if len(e2) else np.nan
            g = {'K1': t >= 2.0, 'K2': int((yr < 0).sum()) <= 2, 'K3': drop.mean() > 0 if len(drop) else False, 'K4': m_es > 0 and m_mes > 0, 'K5': s2.sum() > 0}
            fails = [k for k, v in g.items() if not v]
            say(f"{cell_label(r)} | disc t {r.t:+.2f} | holdout {n} trades ${net.mean() * L2.N_USD if n else 0:+.2f}/trade, t {t:+.2f}, "
                f"losing years {int((yr < 0).sum())}, ES ${m_es * L2.N_USD:+.2f} MES ${m_mes * L2.N_USD:+.2f} -> {'SURVIVOR' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
            if not fails: survivors.append(cell_label(r))
    else:
        say(f"no cell clears t {BONF}: Q3 holdout not opened")
    say(); say("=" * 124); say("Q4  WALK-FORWARD SWITCHER 2013-2026 (selection each January from past data only)"); say("=" * 124)
    if mq is None: mq = L2.Mins('mnqx'); S_mq = build(mq)
    res = {}
    for label, only_base in (('switcher (all conditions)', False), ('reference: base cells only', True)):
        pnl = switcher(S_nq, S_mq, only_base); res[label] = pnl
        st_ = stats(pnl)
        say(f"{label}: OOS {st_['days']} days | ${st_['yr']:+,.0f}/yr | t {st_['t']:+.2f} | Sharpe {st_['sh']:.2f} | max DD ${st_['dd']:,.0f} | "
            f"2013-19 ${st_['yr_a']:+,.0f}/yr, 2019-26 ${st_['yr_b']:+,.0f}/yr | 2 ticks ${st_['yr2']:+,.0f}/yr | drop 5 best days ${st_['drop5']:+,.0f}/yr")
        say("   per year: " + "; ".join(f"{y} {v:+,.0f}" for y, v in st_['years'].items()))
    s_ = stats(res['switcher (all conditions)'])
    ok = s_['t'] >= 2.0 and s_['yr_a'] > 0 and s_['yr_b'] > 0 and s_['yr2'] > 0 and s_['drop5'] > 0
    say(f"VERDICT Q4: {'PASS - forward test only' if ok else 'DEAD - the switcher does not earn out of sample by the registered bar'}")
    say(); say("SURVIVORS: " + ("; ".join(survivors) if survivors else "none") + (" + switcher" if ok else ""))


Y0, NY = 2010, 17
KEYS = [(tf, s, x, su) + c for tf in TFS for s in SIDES for x in EXITS for su in SETUPS for c in COMBOS]
PICKS = {}


def yearly(streams, window):
    A = np.zeros((len(KEYS), 4, NY)); ci = 0
    for tf in TFS:
        for s in SIDES:
            st = streams[tf][s]; win = in_window(st.br, *window)
            for x in EXITS:
                oc = st.out[x]; net = oc['gross'] - cost(1)
                for su in SETUPS:
                    idx = np.where(st.setups[su] & oc['valid'] & win)[0]
                    day = st.br.sess[idx]; nn = net[idx]
                    cm = {dim: {v: (None if c is None else np.nan_to_num(c[idx].astype(float)) > 0) for v, c in st.cond[dim].items()} for dim in DIMS}
                    for combo in COMBOS:
                        m = np.ones(len(idx), bool)
                        for dim, val in zip(DIMS, combo):
                            c = cm[dim][val]
                            if c is not None: m &= c
                        if m.any():
                            u, inv = np.unique(day[m], return_inverse=True); ds = np.bincount(inv, weights=nn[m]); dc = np.bincount(inv)
                            yi = u.astype('datetime64[D]').astype('datetime64[Y]').astype(int) + 1970 - Y0
                            A[ci, 0] = np.bincount(yi, weights=ds, minlength=NY); A[ci, 1] = np.bincount(yi, weights=ds * ds, minlength=NY)
                            A[ci, 2] = np.bincount(yi, minlength=NY); A[ci, 3] = np.bincount(yi, weights=dc, minlength=NY)
                        ci += 1
    return A


def switcher(S_nq, S_mq, only_base):
    if 'A' not in PICKS: PICKS['A'] = yearly(S_nq, DISC) + yearly(S_mq, HOLD)
    Cm = np.cumsum(PICKS['A'], axis=2)
    base = np.array([all(v == 'all' for v in k[4:]) for k in KEYS])
    daily = {1: [], 2: []}; log = []
    for Y in range(2013, 2027):
        i = Y - Y0 - 1; X, XX, ND, NT = Cm[:, 0, i], Cm[:, 1, i], Cm[:, 2, i], Cm[:, 3, i]
        with np.errstate(invalid='ignore', divide='ignore'):
            var = (XX - X * X / ND) / (ND - 1); t = (X / ND) / np.sqrt(var / ND)
        okc = (NT >= 100) & (ND >= 80) & (var > 0) & (t >= 3.0)
        if only_base: okc &= base
        ci = np.where(okc)[0]; ci = ci[np.argsort(-t[ci])][:30]
        cand = [KEYS[c] for c in ci]; log.append((Y, len(cand), [(round(float(t[c]), 2), KEYS[c]) for c in ci[:3]]))
        for mk_streams, window in ((S_nq, DISC), (S_mq, HOLD)):
            lo = max(window[0], pd.Timestamp(f'{Y}-01-01')); hi = min(window[1], pd.Timestamp(f'{Y}-12-31'))
            if lo > hi: continue
            for tf in TFS:
                for s in SIDES:
                    for x in EXITS:
                        sel = [c for c in cand if c[0] == tf and c[1] == s and c[2] == x]
                        if not sel: continue
                        st = mk_streams[tf][s]; oc = st.out[x]; win = in_window(st.br, lo, hi)
                        mask = np.zeros(st.br.n, bool)
                        for c in sel:
                            idx = np.where(st.setups[c[3]] & oc['valid'] & win)[0]
                            mask[idx[st.combo_mask(c[4:], idx)]] = True
                        ev = one_at_a_time(np.where(mask)[0], oc['j0'], oc['jx'])
                        if len(ev):
                            for tk in (1, 2): daily[tk].append(series(st, ev, x, tk))
    PICKS[('log', only_base)] = log
    return {tk: (pd.concat(v).groupby(level=0).sum() if v else pd.Series(dtype=float)) for tk, v in daily.items()}


def stats(p):
    s1, s2 = p[1], p[2]
    if s1.empty: return dict(days=0, yr=0, t=0, sh=0, dd=0, yr_a=0, yr_b=0, yr2=0, drop5=0, years=pd.Series(dtype=float))
    cal = pd.date_range('2013-01-01', '2026-10-15', freq='B'); x = s1.reindex(cal, fill_value=0.0) * L2.N_USD
    x2 = s2.reindex(cal, fill_value=0.0) * L2.N_USD; yrs = len(cal) / 261
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if x.std() > 0 else 0
    eq = x.cumsum(); dd = float((eq.cummax() - eq).max())
    a = x[x.index <= DISC[1]]; b = x[x.index > DISC[1]]
    drop = x.drop(x.nlargest(5).index)
    return dict(days=int((s1 != 0).sum()), yr=x.sum() / yrs, t=t, sh=x.mean() / x.std() * np.sqrt(252) if x.std() > 0 else 0, dd=dd,
                yr_a=a.sum() / (len(a) / 261), yr_b=b.sum() / (len(b) / 261), yr2=x2.sum() / yrs, drop5=drop.sum() / yrs,
                years=x.groupby(x.index.year).sum())


if __name__ == '__main__':
    main()
    for k, log in [(k, v) for k, v in PICKS.items() if isinstance(k, tuple) and k[0] == 'log']:
        say(f"picks ({'base only' if k[1] else 'all conditions'}): " + "; ".join(f"{y}: {n}" for y, n, _ in log))
        for y, n, top in log: say(f"   {y} top: " + "; ".join(f"t {tt} {kk[0]} {kk[1]} {kk[2]} {kk[3]} {'/'.join(kk[4:])}" for tt, kk in top))
    say(f"run {time.time() - T0:.0f} s")
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
