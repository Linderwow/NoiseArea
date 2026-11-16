import os, sys, time, math
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as nl
from research_engine import WARMUP
import yt_backtest as yb
import yt_captain_st as cs
import yt_captain_improve as ci

ET = 'America/New_York'
P_REF = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
USD = 55751.0
JOIN = nl.JOIN
RAW = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
DBN = {'NQ': ['NQ_ohlcv-1m_2010-2019.dbn.zst'],
       'MNQDB': ['mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst', 'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst', 'MNQ_ohlcv-1m_2026-2026.dbn.zst'],
       'MES': ['MES_ohlcv-1m_2019-2026.dbn.zst']}
OUT = os.path.join(HERE, 'MERGE_RESULTS.txt')
NS_MIN = 60_000_000_000
K_RULES = 7
P_BAR = 0.05 / K_RULES
W1_GRID = [(vm, lk, st) for vm in (0.75, 1.0, 1.25, 1.5) for lk in (10, 14, 30, 60) for st in (30, 60)]
W2_GRID = [(k, m, T, False) for k in (1, 3, 5, 15, 30, 60) for m in (2.5, 3.5) for T in (100, None)] + \
          [(k, 2.5, T, True) for k in (1, 5) for T in (100, None)]
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def wname(v):
    k, m, T, h = v
    return f"{k}m x{m} {'T' + str(T) if T else 'band'} us{' +60m' if h else ''}"


def fam_M(mk, vm=1.0, look=14, step=30, first=600, last=930, filt=None, exit_dir=None, keep_pos=False):
    sig = mk.sigma(look); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int); P = [None] * mk.n
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; fb = -1
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        pp = np.zeros(nb, np.int8)
        fk = filt[k] if filt is not None else None; ek = exit_dir[k] if exit_dir is not None else None
        for i in range(flat_i):
            new = pos
            if M[i] in checks and not np.isnan(sg[i]):
                c = C[i]; ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
                if pos == 0: new = 1 if c > ub else (-1 if c < lb else 0)
                elif pos == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                if fk is not None and new != 0 and new != pos and fk[i] != new: new = 0
            if ek is not None and pos != 0 and new == pos and i > fb and ek[i] == -pos: new = 0
            if new != pos:
                px = O[i + 1]
                if pos != 0: p += (px - entry) * pos
                if new != 0: entry = px; n_ += 1; fb = i + 1
                pos = new
            pp[i] = pos
        if pos != 0: p += (C[flat_i] - entry) * pos
        pnl[k] = p; nt[k] = n_
        if keep_pos: P[k] = pp
    return (pnl, nt, P) if keep_pos else (pnl, nt)


def day_ns(mk):
    return [pd.Timestamp(x.date).value + x.M.astype(np.int64) * NS_MIN for x in mk.days]


class Seg:

    def __init__(self, name):
        self.name = name; self.mk = nl.market(name); mk = self.mk
        d = mk.dates; o = np.array([x.O[0] for x in mk.days])
        if name == 'MES':
            self.px = pd.Series(o, index=d)
        else:
            px = RAW.reindex(d).values; self.px = pd.Series(np.where(np.isnan(px), o, px), index=d)
        sel = np.arange(len(d)) >= WARMUP
        if name == 'NQ': sel &= d <= JOIN
        elif name in ('MNQ', 'MNQDB'): sel &= d > JOIN
        self.sel = sel; self.dates = d[sel]; self.st = {}; self.bars = {}
        self.t5 = day_ns(mk)

    def n_ret(self, pnl, nt, ticks=1, gross=False):
        p = np.nan_to_num(pnl[self.sel]); n = nt[self.sel]; px = self.px.values[self.sel]
        if gross: return pd.Series(p / px, index=self.dates)
        c = (0.25 * ticks + 0.25) / P_REF
        return pd.Series(p / px - n * 2 * c, index=self.dates)

    def sleeve(self, T, ticks=1, gross=False):
        T = T[T.day.isin(self.dates)]
        r = T.pts.to_numpy() / self.px.reindex(T.day).to_numpy()
        if not gross: r = r - (0.5 + 0.25 * ticks * T.nfill.to_numpy()) / P_REF
        return pd.Series(r, index=T.day.to_numpy()).groupby(level=0).sum().reindex(self.dates, fill_value=0.0)

    def load_1m(self):
        if self.name == 'MNQ':
            df = yb.load('MNQ', None)[['time', 'open', 'high', 'low', 'close', 'order']].copy()
        else:
            import databento as db
            parts = [db.DBNStore.from_file(os.path.join(nl.DB, f)).to_df()[['instrument_id', 'open', 'high', 'low', 'close']]
                     for f in DBN[self.name]]
            raw = pd.concat(parts); raw = raw[~raw.index.duplicated(keep='last')].sort_index()
            iid = raw.instrument_id.values; sw = np.flatnonzero(iid[1:] != iid[:-1]) + 1
            adj = np.zeros(len(raw)); order = np.zeros(len(raw), int)
            for k in sw:
                adj[:k] += raw.open.iat[k] - raw.close.iat[k - 1]; order[k:] += 1
            df = pd.DataFrame({'time': raw.index + pd.Timedelta(minutes=1), 'order': order})
            for c in ('open', 'high', 'low', 'close'): df[c] = raw[c].values + adj
            df['time'] = pd.to_datetime(df.time, utc=True)
        self.b1raw = df.reset_index(drop=True)

    def bar(self, k):
        if k not in self.bars:
            b = ci.resample(self.b1raw, k)
            b['ns'] = b.time.dt.tz_convert(ET).dt.tz_localize(None).values.astype('datetime64[ns]').astype(np.int64)
            self.bars[k] = b
        return self.bars[k]

    def trend(self, k, mult):
        if (k, mult) not in self.st:
            b = self.bar(k)
            self.st[(k, mult)] = cs.hma_supertrend(*(b[c].to_numpy(float) for c in ('high', 'low', 'close')), 14, mult, 14)
        return self.st[(k, mult)]

    def map_dir(self, k, mult):
        b = self.bar(k); tr = self.trend(k, mult)[2]; t = b.ns.to_numpy(); out = []; stale = tot = 0
        for t5 in self.t5:
            j = np.searchsorted(t, t5, side='right') - 1
            out.append(np.where(j >= 0, tr[np.clip(j, 0, None)], 0).astype(np.int8))
            stale += int(((t5 - t[np.clip(j, 0, None)]) > k * 5 * NS_MIN).sum()); tot += len(t5)
        return out, stale / max(tot, 1)

    def capt(self, k, mult, T, session='us', htf=False, allow_x=None, flat_x=None):
        b = self.bar(k); O, H, L, C = (b[c].to_numpy(float) for c in ('open', 'high', 'low', 'close'))
        fu, fd, trend = self.trend(k, mult); mins = b.mins.to_numpy()
        if session == 'us':
            allow = (mins >= 9 * 60 + 31) & (mins <= 15 * 60 + 30); flat = mins == 16 * 60
        else:
            allow = np.ones(len(C), bool); flat = np.zeros(len(C), bool)
        if htf:
            hb = self.bar(60); ht = self.trend(60, 2.5)[2]
            j = np.searchsorted(hb.time.to_numpy(), b.time.to_numpy(), side='right') - 1
            allow = allow & (np.where(j >= 0, ht[np.clip(j, 0, None)], 0) == trend)
        if allow_x is not None: allow = allow & allow_x(trend)
        if flat_x is not None: flat = flat | flat_x
        tr = ci.simulate2(O, H, L, C, b.order.to_numpy(), fu, fd, trend, T, 0.0, allow, flat)
        return pd.DataFrame({'day': pd.to_datetime(b.day.to_numpy()[tr.exit_i.to_numpy()]), 'pts': tr.pts.to_numpy(),
                             'nfill': 1 + (tr.kind.to_numpy() != 'target').astype(int), 'kind': tr.kind.to_numpy()})

    def alignment(self):
        b = self.bar(1); t = b.ns.to_numpy(); c1 = b.close.to_numpy(); eq = mv = n = nm = mvx = nmx = 0; roll_days = []
        for x, t5 in zip(self.mk.days, self.t5):
            j = np.searchsorted(t, t5); ok = (j < len(t)) & (t[np.clip(j, 0, len(t) - 1)] == t5); jj = np.clip(j, 0, len(t) - 1)
            n += ok.sum(); eq += (ok & np.isclose(c1[jj], x.C)).sum()
            both = ok[1:] & ok[:-1]; nm += both.sum()
            good = (both & np.isclose(np.diff(c1[jj]), np.diff(x.C))).sum(); mv += good
            if both.sum() and (both.sum() - good) / both.sum() > 0.2:
                roll_days.append(pd.Timestamp(x.date))
            else:
                mvx += good; nmx += both.sum()
        tot = sum(len(t5) for t5 in self.t5)
        self.roll_days = roll_days; self.mv_ex = mvx / max(nmx, 1)
        return n / tot, eq / max(n, 1), mv / max(nm, 1)

    def pos_eff(self, P):
        b = self.bar(1); keep = [k for k in range(self.mk.n) if P[k] is not None]
        ts = np.concatenate([self.t5[k] for k in keep]); ps = np.concatenate([P[k] for k in keep])
        j = np.searchsorted(ts, b.ns.to_numpy() - NS_MIN, side='right') - 1
        return np.where(j >= 0, ps[np.clip(j, 0, None)], 0).astype(np.int8)


def sharpe(x):
    s = x.std(ddof=1); return float(x.mean() / s * math.sqrt(252)) if len(x) > 2 and s > 0 else float('nan')


def tstat(x):
    return yb.tstat(np.asarray(x, float))


def dollars(x):
    eq = np.cumsum(x.values) * USD; dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()
    yr = x.mean() * 252 * USD
    return yr, dd, (yr / dd if dd > 0 else float('nan'))


def boot_p(m, n, reps=10_000, seed=20261106):
    g = pd.DataFrame({'a': m.values, 'b': n.values, 'a2': m.values ** 2, 'b2': n.values ** 2, 'c': 1.0},
                     index=m.index).groupby(m.index.to_period('M')).sum().to_numpy()
    rng = np.random.default_rng(seed); S = np.zeros((reps, 5))
    for lo in range(0, reps, 1000):
        idx = rng.integers(0, len(g), (min(1000, reps - lo), len(g))); S[lo:lo + len(idx)] = g[idx].sum(axis=1)

    def sh(s1, s2, c):
        mu = s1 / c; var = np.maximum(s2 / c - mu ** 2, 1e-30); return mu / np.sqrt(var)
    return float((sh(S[:, 0], S[:, 2], S[:, 4]) <= sh(S[:, 1], S[:, 3], S[:, 4])).mean())


def drop5(m, n):
    d = (m - n).sort_values(ascending=False); keep = ~m.index.isin(d.index[:5])
    return sharpe(m[keep]), sharpe(n[keep])


def years(x):
    return x.groupby(x.index.year).sum() * USD


def walk_forward(series, start, lab):
    idx = next(iter(series.values())).index; parts = []; picks = []
    for y in range(start, idx[-1].year + 1):
        a = pd.Timestamp(f'{y}-01-01'); b = pd.Timestamp(f'{y + 1}-01-01')
        best = max(series, key=lambda key: (lambda s: -1e9 if np.isnan(s) else s)(sharpe(series[key][series[key].index < a])))
        picks.append((y, lab(best))); parts.append(series[best][(series[best].index >= a) & (series[best].index < b)])
    return pd.concat(parts), picks


def build(names=('NQ', 'MNQ', 'MNQDB', 'MES'), full=True):
    S = {}
    for nm in names:
        s = Seg(nm); mk = s.mk; S[nm] = s
        s.N = fam_M(mk, keep_pos=True)
        say(f"[{time.time() - T0:5.0f} s] {nm}: NoiseArea {mk.n} sessions, graded {len(s.dates)} ({s.dates[0].date()} .. {s.dates[-1].date()})")
        s.load_1m()
        cov, eq, mv = s.alignment()
        s.align = (cov, eq, mv)
        say(f"          1-minute bars {len(s.b1raw):,}; 5-minute bars with a 1-minute bar at the same end stamp {cov:.1%}, "
            f"equal close {eq:.1%}, equal bar-to-bar move {mv:.1%}")
        if not full:
            if nm == 'MNQ': s.C30 = s.capt(30, 3.5, None, 'us')
            s.bars = {}; s.st = {}; del s.b1raw
            continue
        d1, st1 = s.map_dir(1, 2.5); d30, st30 = s.map_dir(30, 3.5)
        say(f"          Captain direction older than 5 bars at a decision: C1 {st1:.2%}, C30 {st30:.2%}")
        s.M2 = fam_M(mk, filt=d1); s.M3 = fam_M(mk, filt=d30); s.M4 = fam_M(mk, exit_dir=d1)
        s.C30 = s.capt(30, 3.5, None, 'us')
        pe = s.pos_eff(s.N[2]); nxt = np.r_[pe[1:], 0]
        s.IN = s.capt(1, 2.5, 100, '24h', allow_x=lambda tr, nxt=nxt: (tr == nxt) & (nxt != 0), flat_x=(nxt != pe))
        say(f"[{time.time() - T0:5.0f} s] {nm}: M2-M5 built; Captain-30 trades {len(s.C30):,}, Captain-inside trades {len(s.IN):,}")
        s.W2 = {}
        for v in W2_GRID:
            s.W2[v] = s.capt(v[0], v[1], v[2], 'us', htf=v[3])
        say(f"[{time.time() - T0:5.0f} s] {nm}: W2 28 Captain variants built")
        s.W1 = {g: fam_M(mk, vm=g[0], look=g[1], step=g[2]) for g in W1_GRID}
        say(f"[{time.time() - T0:5.0f} s] {nm}: W1 32 parameter sets built")
        s.bars = {}; s.st = {}; del s.b1raw
    return S


def checks(S):
    say("=" * 118); say("CHECKS"); say("=" * 118)
    ok = True
    for second in ('MNQ', 'MNQDB'):
        ref, _ = nl.series(second)
        mine = pd.concat([S['NQ'].n_ret(*S['NQ'].N[:2]), S[second].n_ret(*S[second].N[:2])])
        both = ref.index.intersection(mine.index); d = float((ref[both] - mine[both]).abs().max())
        good = len(both) == len(ref) == len(mine) and d < 1e-12; ok &= good
        say(f"NoiseArea parity, NQ + {second}: merge_lab {len(mine)} sessions vs nav_lab {len(ref)}, max |diff| {d:.1e} -> "
            f"{'PASS' if good else 'FAIL'}")
    for nm, s in S.items():
        cov, eq, mv = s.align; rd = s.roll_days
        in_roll_months = all(d.month in (3, 6, 9, 12) and 6 <= d.day <= 16 for d in rd)
        good = s.mv_ex >= 0.99 and len(rd) <= 0.02 * s.mk.n and in_roll_months; ok &= good
        say(f"bar alignment {nm}: equal 5-minute moves {mv:.2%}, {s.mv_ex:.2%} outside {len(rd)} days on which the two sources hold "
            f"different contract months (all in roll weeks: {in_roll_months}); levels {eq:.1%}, coverage {cov:.1%} -> "
            f"{'PASS' if good else 'FAIL'}")
    if 'MNQ' in S and hasattr(S['MNQ'], 'C30'):
        mnq = yb.load('MNQ', None); days = pd.Index(sorted(mnq.day.unique())); prim = days[days <= yb.PRIMARY_END]
        T = S['MNQ'].C30
        usd = pd.Series((T.pts.to_numpy() - 0.25 * T.nfill.to_numpy()) * 2.0 - 1.0, index=T.day.dt.strftime('%Y-%m-%d'))
        x = usd.groupby(level=0).sum().reindex(prim, fill_value=0.0)
        good = abs(x.mean() - 8.07) < 0.01 and abs(tstat(x) - 2.61) < 0.01; ok &= good
        say(f"Captain-30 parity vs yt_captain_improve: ${x.mean():+.2f}/day, t {tstat(x):.2f} (expected +8.07, 2.61) -> "
            f"{'PASS' if good else 'FAIL'}")
    say(f"ALL CHECKS {'PASS' if ok else 'FAIL'}")
    return ok


def series_for(S, rule, seg, ticks=1, gross=False):
    s = S[seg]; n = s.n_ret(*s.N[:2], ticks=ticks, gross=gross)
    if rule == 'N': return n
    if rule == 'M1': return n + s.sleeve(s.C30, ticks, gross)
    if rule in ('M2', 'M3', 'M4'): return s.n_ret(*getattr(s, rule), ticks=ticks, gross=gross)
    if rule == 'M5': return n + s.sleeve(s.IN, ticks, gross)
    raise KeyError(rule)


def joined(S, rule, second='MNQ', ticks=1, gross=False):
    return pd.concat([series_for(S, rule, 'NQ', ticks, gross), series_for(S, rule, second, ticks, gross)])


def w1_series(S, seg, ticks=1, gross=False, second=None):
    s = S[seg]
    if second is None:
        return {g: s.n_ret(*s.W1[g], ticks=ticks, gross=gross) for g in W1_GRID}
    t = S[second]
    return {g: pd.concat([s.n_ret(*s.W1[g], ticks=ticks, gross=gross), t.n_ret(*t.W1[g], ticks=ticks, gross=gross)]) for g in W1_GRID}


def w2_series(S, seg, ticks=1, gross=False, second=None):
    s = S[seg]
    if second is None:
        return {v: s.sleeve(s.W2[v], ticks, gross) for v in W2_GRID}
    t = S[second]
    return {v: pd.concat([s.sleeve(s.W2[v], ticks, gross), t.sleeve(t.W2[v], ticks, gross)]) for v in W2_GRID}


def era_split(x):
    return x[x.index <= JOIN], x[x.index > JOIN]


def grade_rule(S, rule):
    out = []
    if rule in ('M1', 'M2', 'M3', 'M4', 'M5'):
        m, n = joined(S, rule), joined(S, 'N')
        mdb, ndb = joined(S, rule, 'MNQDB'), joined(S, 'N', 'MNQDB')
        m2, n2 = joined(S, rule, ticks=2), joined(S, 'N', ticks=2)
        mdb2, ndb2 = joined(S, rule, 'MNQDB', ticks=2), joined(S, 'N', 'MNQDB', ticks=2)
        mes_m, mes_n = series_for(S, rule, 'MES', gross=True), series_for(S, 'N', 'MES', gross=True)
        mz, nz = joined(S, rule, gross=True), joined(S, 'N', gross=True)
    else:
        if rule == 'W1':
            sel = lambda tk, sec: walk_forward(w1_series(S, 'NQ', tk, second=sec), 2012, lambda g: f"vm{g[0]} L{g[1]} s{g[2]}")
            base = lambda tk, sec: joined(S, 'N', sec, tk)
            m, picks = sel(1, 'MNQ'); n = base(1, 'MNQ').reindex(m.index)
            mdb, _ = sel(1, 'MNQDB'); ndb = base(1, 'MNQDB').reindex(mdb.index)
            m2, _ = sel(2, 'MNQ'); n2 = base(2, 'MNQ').reindex(m2.index)
            mdb2, _ = sel(2, 'MNQDB'); ndb2 = base(2, 'MNQDB').reindex(mdb2.index)
            mes_m, _ = walk_forward(w1_series(S, 'MES', gross=True), 2020, str); mes_n = series_for(S, 'N', 'MES', gross=True).reindex(mes_m.index)
            mz, _ = walk_forward(w1_series(S, 'NQ', gross=True, second='MNQ'), 2012, str); nz = joined(S, 'N', gross=True).reindex(mz.index)
        else:
            def sel(tk, sec):
                c, pk = walk_forward(w2_series(S, 'NQ', tk, second=sec), 2012, wname)
                return c, pk
            c1, picks = sel(1, 'MNQ'); n = joined(S, 'N').reindex(c1.index); m = n + c1
            cdb, _ = sel(1, 'MNQDB'); ndb = joined(S, 'N', 'MNQDB').reindex(cdb.index); mdb = ndb + cdb
            c2, _ = sel(2, 'MNQ'); n2 = joined(S, 'N', ticks=2).reindex(c2.index); m2 = n2 + c2
            cdb2, _ = sel(2, 'MNQDB'); ndb2 = joined(S, 'N', 'MNQDB', ticks=2).reindex(cdb2.index); mdb2 = ndb2 + cdb2
            cm, _ = walk_forward(w2_series(S, 'MES', gross=True), 2020, wname)
            mes_n = series_for(S, 'N', 'MES', gross=True).reindex(cm.index); mes_m = mes_n + cm
            cz, _ = walk_forward(w2_series(S, 'NQ', gross=True, second='MNQ'), 2012, wname)
            nz = joined(S, 'N', gross=True).reindex(cz.index); mz = nz + cz
            yrs = c1.groupby(c1.index.year).sum()
            out.append(f"   Captain chosen each January, ALONE out of sample 2012-{c1.index[-1].year}: ${c1.mean() * 252 * USD:+,.0f}/yr, "
                       f"daily t {tstat(c1):.2f} (bar 3.2), losing years {int((yrs < 0).sum())}/{len(yrs)}; per year $: "
                       + ", ".join(f"{y} {v * USD:+,.0f}" for y, v in yrs.items()))
            alone_ok = tstat(c1) >= 3.2 and int((yrs < 0).sum()) <= 1
            out.append(f"   W2 alone gate (t >= 3.2 and <= 1 losing year): {'PASS' if alone_ok else 'FAIL'}")
        if rule == 'W1' or rule == 'W2':
            out.append("   picks: " + "; ".join(f"{y} {p}" for y, p in picks))
    p = boot_p(m, n)
    nq_m, mq_m = era_split(m); nq_n, mq_n = era_split(n); _, db_m = era_split(mdb); _, db_n = era_split(ndb)
    g1 = p < P_BAR
    g2 = sharpe(nq_m) > sharpe(nq_n) and sharpe(mq_m) > sharpe(mq_n) and sharpe(db_m) > sharpe(db_n)
    g3 = sharpe(mes_m) > sharpe(mes_n)
    p2 = boot_p(m2, n2); a2, b2 = era_split(m2), era_split(n2); _, c2m = era_split(mdb2); _, c2n = era_split(ndb2)
    g4 = p2 < P_BAR and sharpe(a2[0]) > sharpe(b2[0]) and sharpe(a2[1]) > sharpe(b2[1]) and sharpe(c2m) > sharpe(c2n)
    d5m, d5n = drop5(m, n); g5 = d5m > d5n
    if rule == 'W2': g_all = g1 and g2 and g3 and g4 and g5 and alone_ok
    else: g_all = g1 and g2 and g3 and g4 and g5
    ym, ddm, marm = dollars(m); yn, ddn, marn = dollars(n)
    out.insert(0, f"   joined {m.index[0].date()}..{m.index[-1].date()}: Sharpe {sharpe(m):.2f} vs NoiseArea {sharpe(n):.2f}; "
                  f"${ym:,.0f}/yr vs ${yn:,.0f}; max DD ${ddm:,.0f} vs ${ddn:,.0f}; MAR {marm:.2f} vs {marn:.2f}; "
                  f"zero-cost Sharpe {sharpe(mz):.2f} vs {sharpe(nz):.2f}")
    out.append(f"   G1 bootstrap P(Sharpe <= NoiseArea) {p:.4f} (bar {P_BAR:.4f}) {'pass' if g1 else 'FAIL'} | "
               f"G2 Sharpe NQ {sharpe(nq_m):.2f}/{sharpe(nq_n):.2f}, MNQ NT {sharpe(mq_m):.2f}/{sharpe(mq_n):.2f}, "
               f"MNQ Databento {sharpe(db_m):.2f}/{sharpe(db_n):.2f} {'pass' if g2 else 'FAIL'}")
    out.append(f"   G3 MES gross Sharpe {sharpe(mes_m):.2f}/{sharpe(mes_n):.2f} {'pass' if g3 else 'FAIL'} | G4 2 ticks: P {p2:.4f}, "
               f"Sharpe NQ {sharpe(a2[0]):.2f}/{sharpe(b2[0]):.2f}, MNQ {sharpe(a2[1]):.2f}/{sharpe(b2[1]):.2f}, "
               f"Databento {sharpe(c2m):.2f}/{sharpe(c2n):.2f} {'pass' if g4 else 'FAIL'} | "
               f"G5 drop-5 {d5m:.2f}/{d5n:.2f} {'pass' if g5 else 'FAIL'}")
    out.append(f"   VERDICT {rule}: {'PASS - recommend a paper forward test' if g_all else 'DEAD'}")
    return out, g_all, m, n


NAMES = {'M1': 'PORTFOLIO - NoiseArea + Captain-30 sleeve (30-min x3.5 band, US session), 1 micro each',
         'M2': 'FILTER-1 - NoiseArea legs only when Captain 1-min x2.5 agrees',
         'M3': 'FILTER-30 - NoiseArea legs only when Captain 30-min x3.5 agrees',
         'M4': 'EXIT-1 - NoiseArea exits when Captain 1-min x2.5 turns against it',
         'M5': 'INSIDE - NoiseArea + Captain 1-min T100 trading only in NoiseArea\'s direction while it holds',
         'W1': 'WALK-FORWARD NOISEAREA - best of 32 parameter sets on all past data, each January',
         'W2': 'WALK-FORWARD CAPTAIN - best of 28 US-session Captain variants on all past data each January, added to NoiseArea'}


def report(S):
    say(); say("=" * 118); say("RESULTS (k = 7 pre-registered rules; joined series NQ 2010-2019 + MNQ NinjaTrader 2019-2026, $ per micro "
                               "at today's price and costs)"); say("=" * 118)
    n = joined(S, 'N'); yn, ddn, marn = dollars(n)
    say(f"NoiseArea as published: Sharpe {sharpe(n):.2f}, ${yn:,.0f}/yr, max DD ${ddn:,.0f}, MAR {marn:.2f}, "
        f"{(S['NQ'].N[1][S['NQ'].sel].sum() + S['MNQ'].N[1][S['MNQ'].sel].sum()) / (len(n) / 252):.0f} trades/yr")
    for nm, T in (('Captain-30 sleeve', 'C30'), ('Captain-inside sleeve', 'IN')):
        c = pd.concat([S['NQ'].sleeve(getattr(S['NQ'], T)), S['MNQ'].sleeve(getattr(S['MNQ'], T))])
        yc, ddc, _ = dollars(c); yrs = years(c)
        say(f"{nm} alone: Sharpe {sharpe(c):.2f}, ${yc:,.0f}/yr, t {tstat(c):.2f}, losing years {int((yrs < 0).sum())}/{len(yrs)}, "
            f"correlation with NoiseArea's daily P&L {np.corrcoef(c.values, n.values)[0, 1]:+.2f}")
    verdicts = {}; per_year = {'N': years(n)}
    for rule in ('M1', 'M2', 'M3', 'M4', 'M5', 'W1', 'W2'):
        say(); say(f"{rule} {NAMES[rule]}")
        lines, ok, m, _n = grade_rule(S, rule)
        for ln in lines: say(ln)
        verdicts[rule] = ok; per_year[rule] = years(m)
    say(); say("PER YEAR, $ per micro at today's price (W1 / W2 from 2012, their out-of-sample years)")
    tab = pd.DataFrame(per_year).round(0)
    say(tab.to_string(float_format=lambda v: f"{v:,.0f}"))
    say(); alive = [r for r, ok in verdicts.items() if ok]
    say("SURVIVORS: " + (", ".join(alive) if alive else "none - NoiseArea as published stays the one strategy"))
    say(f"run {time.time() - T0:.0f} s")


KEEP = ('N', 'M2', 'M3', 'M4', 'C30', 'IN', 'W1', 'W2', 'align', 'roll_days', 'mv_ex')
CACHE = os.environ.get('MERGE_CACHE', os.path.join(os.environ.get('TEMP', HERE), 'merge_lab_cache.pkl'))


def save(S):
    import pickle
    with open(CACHE, 'wb') as f:
        pickle.dump({nm: {a: getattr(s, a) for a in KEEP} for nm, s in S.items()}, f)


def load_cache():
    import pickle
    with open(CACHE, 'rb') as f:
        d = pickle.load(f)
    S = {}
    for nm, attrs in d.items():
        s = Seg(nm)
        for a, v in attrs.items(): setattr(s, a, v)
        S[nm] = s
    return S


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    say(f"merge_lab.py {mode}  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (MERGE_PLAN.txt)")
    if mode == 'check':
        S = build(full=False)
        checks(S)
    elif mode in ('run', 'report'):
        if mode == 'run':
            S = build(); save(S); say(f"cache {CACHE}")
        else:
            S = load_cache()
        if not checks(S):
            say("checks failed - results not graded")
        else:
            report(S)
        with open(OUT, 'w', encoding='utf-8') as f:
            f.write("\n".join(LINES) + "\n")
        print(f"wrote {OUT}")
