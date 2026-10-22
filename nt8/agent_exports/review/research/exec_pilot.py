"""
exec_pilot.py - fill-cost pilot for the NoiseArea MNQ strategy, pre-registered in EXEC_PILOT_PLAN.txt. Research only.

    python exec_pilot.py [--parts A,B] [--sec <1s file>] [--tape <trades file>] [--tape2 <trades file holding 2026-04-16>]
                         [--m1a <1m 2023-2025 file>] [--m1b <1m 2026 file>] [--sides-out exec_pilot_sides.csv]

Part A: Databento ohlcv-1s MNQ Dec-2025 (trade-price bars only: no quotes, no aggressor side).
Part B: Databento trades MNQ 2026-01-02..2026-04-16 (every trade with its aggressor side), read in chunks of 2 million trades;
        only the windows around the reference fills are kept, so memory stays small.
Signals are not recomputed: the sides come from s1_reference_trades_ntbars_v3.csv. Costs are ticks per side, positive = worse
than the backtest's reference price R (the next bar's open; the bar's close for the flat exit at 16:00 / partial-session end).
"""
import os, sys, argparse, collections, datetime as dt
import numpy as np, pandas as pd, databento as db

TICK = 0.25
NS = 1_000_000_000
SIGNAL_FILL = {f'{h:02d}:{m:02d}' for h in range(10, 16) for m in (5, 35)}      # fill-bar stamps 10:05 .. 15:35
LAT = 250 * 1_000_000                                                           # 250 ms order latency (plan section 3)
LATS = (0, 250 * 1_000_000, 1000 * 1_000_000)
KS, HOLDS = (0, 1), (10, 30, 60)
VARIANTS = [(k, h) for k in KS for h in HOLDS]
SEED, NBOOT = 20261019, 10_000
ROLL = [(dt.date(2025, 12, 11), dt.date(2025, 12, 19)), (dt.date(2026, 3, 12), dt.date(2026, 3, 20))]
WIN_PRE, WIN_POST = 120 * NS, 150 * NS                                          # tape kept around each decision time
DB = r'C:\Users\linde\OneDrive\Desktop\Desktop\databento_data'
AR = r'D:\databento_archive'


def in_roll(d): return any(a <= d <= b for a, b in ROLL)


def et_to_ns(values):
    dti = pd.DatetimeIndex(pd.to_datetime(values)).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None)
    return dti.values.astype('datetime64[ns]').view('i8')


def ns_to_et(ns):
    return pd.DatetimeIndex(np.asarray(ns, dtype='i8').astype('datetime64[ns]')).tz_localize('UTC').tz_convert('America/New_York').tz_localize(None)


def cost(px, R, s): return (px - R) * s / TICK


# ------------------------------------------------------------------------------------------------ sides from the reference trades
def build_sides(ref, d0, d1, part):
    t = ref[(ref.d >= d0) & (ref.d <= d1)].reset_index(drop=True)
    rows = []
    for i, r in t.iterrows():
        tid = f'{part}{i:03d}'
        rows.append(dict(part=part, trade=tid, date=r.d, kind='entry', s=int(r.dir), stamp=r.entry_time, refpx=r.entry, points=r.points))
        kind = 'exit' if r.exit_time.strftime('%H:%M') in SIGNAL_FILL else 'flat'
        rows.append(dict(part=part, trade=tid, date=r.d, kind=kind, s=-int(r.dir), stamp=r.exit_time, refpx=r.exit, points=r.points))
    S = pd.DataFrame(rows)
    S['T_et'] = S.stamp - pd.Series(pd.to_timedelta(np.where(S.kind == 'flat', 0, 5), unit='min'), index=S.index)
    S['T'] = et_to_ns(S.T_et)
    S['event'] = S['T']                                 # reversal legs share the decision time -> one bootstrap cluster
    S['roll'] = S.date.map(in_roll)
    return S


# ------------------------------------------------------------------------------------------------ data
class Bars1s:
    """1-second bars: ts = bar start (ns UTC), arrays o/h/l/c/v/iid, sorted."""
    def __init__(self, ts, o, h, l, c, v, iid):
        order = np.argsort(ts, kind='stable')
        self.ts, self.o, self.h, self.l, self.c, self.v, self.iid = (np.asarray(a)[order] for a in (ts, o, h, l, c, v, iid))


def load_1s(path):
    df = db.DBNStore.from_file(path).to_df(pretty_ts=False, map_symbols=False)
    ts = df.index.to_numpy().astype('i8')
    return Bars1s(ts, df.open.to_numpy(float), df.high.to_numpy(float), df.low.to_numpy(float), df.close.to_numpy(float),
                  df.volume.to_numpy(float), df.instrument_id.to_numpy())


def bars_from_trades(tp):
    sec = tp.ts // NS * NS
    st = np.r_[0, np.flatnonzero(np.diff(sec)) + 1]; en = np.r_[st[1:] - 1, len(sec) - 1]
    return Bars1s(sec[st], tp.px[st], np.maximum.reduceat(tp.px, st), np.minimum.reduceat(tp.px, st), tp.px[en],
                  np.add.reduceat(tp.sz, st), tp.iid[st])


class Tape:
    def __init__(self, ts, px, sz, sd, iid, seq):
        order = np.argsort(ts, kind='stable')                     # by exchange time; ties keep the file's (receive) order
        self.ts, self.px, self.sz, self.sd, self.iid, self.seq = (a[order] for a in (ts, px, sz, sd, iid, seq))
        k = self.sd != 0; self.kts, self.kpx, self.ksd = self.ts[k], self.px[k], self.sd[k]
        b = self.sd > 0; a = self.sd < 0
        self.bts, self.bpx, self.ats, self.apx = self.ts[b], self.px[b], self.ts[a], self.px[a]


def merged_windows(T):
    T = np.sort(np.unique(T)); ws, we = [], []
    for t in T:
        a, b = t - WIN_PRE, t + WIN_POST
        if ws and a <= we[-1]: we[-1] = max(we[-1], b)
        else: ws.append(a); we.append(b)
    return np.array(ws, dtype='i8'), np.array(we, dtype='i8')


def rth_volume_add(acc, ts, sz):
    et = pd.DatetimeIndex(ts.astype('datetime64[ns]')).tz_localize('UTC').tz_convert('America/New_York')
    m = np.asarray(et.hour) * 60 + np.asarray(et.minute)
    rth = (m >= 570) & (m < 960)
    if rth.any():
        d = et[rth].tz_localize(None).normalize()
        for k, v in pd.Series(sz[rth]).groupby(np.asarray(d)).sum().items(): acc[pd.Timestamp(k).date()] += float(v)


def load_tape(sources, T):
    """sources: (path, tmin ns) - records before tmin are ignored. Returns the tape, RTH volume per session, trades read, and
    the time span each source actually covers (a file can end early: the Jan-Apr download is truncated, see the results)."""
    ws, we = merged_windows(T)
    keep = []; vol = collections.defaultdict(float); n = 0; cover = []; base = 0
    for path, tmin in sources:
        if tmin is None: tmin = cover[-1][2] + 1                  # continue right after the previous file's last trade (no overlap)
        st = db.DBNStore.from_file(path); lo = max(tmin, int(st.metadata.start)); hi = None
        for df in st.to_df(pretty_ts=False, map_symbols=False, count=2_000_000):
            ts = df['ts_event'].to_numpy().astype('i8'); px = df['price'].to_numpy(float); sz = df['size'].to_numpy(float)
            sd = np.where((df['side'] == 'B').to_numpy(bool), 1, np.where((df['side'] == 'A').to_numpy(bool), -1, 0)).astype(np.int8)
            iid = df['instrument_id'].to_numpy(); seq = base + np.arange(len(df), dtype='i8'); base += len(df)
            ok = (px > 0) & (ts >= tmin)
            if ok.any(): hi = max(hi or 0, int(ts[ok].max()))
            rth_volume_add(vol, ts[ok], sz[ok])
            j = np.searchsorted(ws, ts, 'right') - 1
            inw = ok & (j >= 0) & (ts < we[np.maximum(j, 0)])
            keep.append((ts[inw], px[inw], sz[inw], sd[inw], iid[inw], seq[inw])); n += len(df)
            print(f'  tape: {n:,} trades read, {sum(len(k[0]) for k in keep):,} kept', file=sys.stderr)
        cover.append((os.path.basename(path), lo, hi))
    cat = [np.concatenate([k[i] for k in keep]) for i in range(6)]
    return Tape(*cat), dict(vol), n, cover


def load_1m(paths, d0, d1):
    parts = []
    for p in paths:
        df = db.DBNStore.from_file(p).to_df(pretty_ts=False, map_symbols=False)
        parts.append(pd.DataFrame({'ts': df.index.to_numpy().astype('i8'), 'open': df.open.to_numpy(float), 'close': df.close.to_numpy(float)}))
    m = pd.concat(parts).drop_duplicates('ts', keep='last').set_index('ts').sort_index()
    lo, hi = et_to_ns([pd.Timestamp(d0)])[0], et_to_ns([pd.Timestamp(d1) + pd.Timedelta(days=1)])[0]
    return m[(m.index >= lo) & (m.index < hi)]


# ------------------------------------------------------------------------------------------------ estimators
def eval_1s(B, T, s, kind):
    """Plan section 3 (M1s) and section 4 with the 1-second rules. Returns dict of fills (prices) and diagnostics."""
    j0 = int(np.searchsorted(B.ts, T, 'left')); jb = j0 - 1
    if jb < 0 or j0 >= len(B.ts) or B.ts[jb] < T - WIN_PRE or B.ts[j0] > T + 60 * NS: return None
    C = B.c[jb]; R = C if kind == 'flat' else B.o[j0]

    def mkt(t):
        j = int(np.searchsorted(B.ts, t, 'left'))
        return np.nan if j >= len(B.ts) or B.ts[j] > t + 60 * NS else (B.h[j] if s > 0 else B.l[j])
    out = dict(C=C, R=R, iid=B.iid[j0], first_1s_open=B.o[j0], m1s=mkt(T), first_gap_s=(B.ts[j0] - T) / NS)
    for k, H in VARIANTS:
        L = C - s * k * TICK; je = int(np.searchsorted(B.ts, T + H * NS, 'left'))
        if s > 0:
            thru = je > j0 + 1 and B.l[j0 + 1:je].min() <= L - TICK; touch = je > j0 and B.l[j0:je].min() <= L
        else:
            thru = je > j0 + 1 and B.h[j0 + 1:je].max() >= L + TICK; touch = je > j0 and B.h[j0:je].max() >= L
        conv = mkt(T + H * NS)
        out[f'c_{k}_{H}'] = L if thru else conv; out[f'cf_{k}_{H}'] = bool(thru)
        out[f'o_{k}_{H}'] = L if touch else conv; out[f'of_{k}_{H}'] = bool(touch)
    return out


def eval_tape(P, T, s, kind, LAT=LAT):
    """Plan section 3 (Mtouch, Mnext) and section 4 with the tape rules (LAT = order latency in ns; 250 ms is the plan's)."""
    i0 = int(np.searchsorted(P.ts, T, 'left'))
    if i0 == 0 or i0 >= len(P.ts) or P.ts[i0 - 1] < T - WIN_PRE or P.ts[i0] > T + 60 * NS: return None
    t0 = int(P.ts[i0]); C = P.px[i0 - 1]; R = C if kind == 'flat' else P.px[i0]

    def touch(t):
        k = int(np.searchsorted(P.kts, t, 'right')) - 1
        p, sd = P.kpx[k], P.ksd[k]
        return (p - TICK, p) if sd > 0 else (p, p + TICK)

    def mtouch(t):
        b, a = touch(t); return a if s > 0 else b

    def mnext(t):
        xs, xp = (P.bts, P.bpx) if s > 0 else (P.ats, P.apx)
        j = int(np.searchsorted(xs, t, 'left'))
        return np.nan if j >= len(xs) or xs[j] > T + WIN_POST else xp[j]
    t1 = t0 + LAT
    out = dict(C=C, R=R, iid=P.iid[i0], t0_ms=(t0 - T) / 1e6, mnext=mnext(t1), mkt=mtouch(t1))
    for lat in LATS: out[f'mtouch_{lat // 1_000_000}'] = mtouch(t0 + lat)
    b1, a1 = touch(t1)
    for k, H in VARIANTS:
        L = C - s * k * TICK; tend = T + H * NS
        marketable = (s > 0 and L >= a1) or (s < 0 and L <= b1)
        if marketable:
            fc = fo = (a1 if s > 0 else b1); thru = touchf = True
        else:
            iw0 = int(np.searchsorted(P.ts, t1, 'right')); iw1 = int(np.searchsorted(P.ts, tend, 'left')); seg = P.px[iw0:iw1]
            if s > 0: thru = len(seg) > 0 and seg.min() <= L - TICK; touchf = len(seg) > 0 and seg.min() <= L
            else: thru = len(seg) > 0 and seg.max() >= L + TICK; touchf = len(seg) > 0 and seg.max() >= L
            conv = mtouch(tend + LAT)
            fc = L if thru else conv; fo = L if touchf else conv
        out[f'c_{k}_{H}'] = fc; out[f'cf_{k}_{H}'] = bool(thru); out[f'mk_{k}_{H}'] = bool(marketable)
        out[f'o_{k}_{H}'] = fo; out[f'of_{k}_{H}'] = bool(touchf)
    return out


# ------------------------------------------------------------------------------------------------ statistics
def boot(x, ev):
    x = np.asarray(x, float); ev = np.asarray(ev); m = ~np.isnan(x); x, ev = x[m], ev[m]
    if len(x) == 0: return dict(n=0, mean=np.nan, lo=np.nan, hi=np.nan, se=np.nan)
    u, inv = np.unique(ev, return_inverse=True); sums = np.bincount(inv, x); cnt = np.bincount(inv)
    rng = np.random.default_rng(SEED); idx = rng.integers(0, len(u), (NBOOT, len(u)))
    means = sums[idx].sum(1) / cnt[idx].sum(1)
    return dict(n=len(x), mean=x.mean(), lo=np.percentile(means, 2.5), hi=np.percentile(means, 97.5), se=means.std(ddof=1))


def fmt(b): return f"{b['mean']:+6.2f} [{b['lo']:+6.2f}, {b['hi']:+6.2f}] n={b['n']}"


def describe(x):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    return f"median {np.median(x):+.2f}  sd {x.std(ddof=1):.2f}  min {x.min():+.2f}  max {x.max():+.2f}" if len(x) > 1 else ''


def report_set(S, label, mcol, prefix_fill, has_mk=False):
    """Market cost and the six passive variants for one set of sides. mcol = market fill column; prefix_fill e.g. '' (tape) or '1s_'."""
    print(f'\n  --- {label}: {len(S)} sides, {S.trade.nunique()} trades, {S.event.nunique()} decision events, {S.date.nunique()} sessions')
    mc = cost(S[mcol], S.R, S.s)
    print(f'  market order cost ({mcol}) ticks/side: {fmt(boot(mc, S.event))}   {describe(mc)}')
    for kd in ('entry', 'exit', 'flat'):
        sub = S.kind == kd
        if sub.any(): print(f'      {kd:5s}: {fmt(boot(mc[sub], S.event[sub]))}')
    thr = S.drop_duplicates('trade').points.quantile(0.8)
    run = (S.kind == 'entry') & (S.points >= thr); oth = (S.kind == 'entry') & (S.points < thr)
    res = {}
    print(f'  passive variants (saving = market cost - passive cost, ticks/side, + = cheaper; runners = entries of the top 20% trades by points, >= {thr:+.2f} pts)')
    for rule, tag in (('c', 'CONSERVATIVE'), ('o', 'optimistic')):
        print(f'   {tag}')
        for k, H in VARIANTS:
            f = S[f'{prefix_fill}{rule}_{k}_{H}']; filled = S[f'{prefix_fill}{rule}f_{k}_{H}'].astype(bool)
            sv = (S[mcol] - f) * S.s / TICK; pc = cost(f, S.R, S.s)
            b = boot(sv, S.event); res[(rule, k, H)] = dict(b, sv=sv)
            mk = f" mkt-able {S[f'mk_{k}_{H}'].mean():4.0%}" if has_mk else ''
            fs = sv[filled].mean() if filled.any() else np.nan; ms = sv[~filled].mean() if (~filled).any() else np.nan
            print(f'    k={k} H={H:2d}s fill {filled.mean():4.0%}{mk} | cost {pc.mean():+6.2f} | saving {fmt(b)} | on fills {fs:+6.2f}'
                  f' on misses {ms:+7.2f} | worst {sv.min():+7.2f} | runners {sv[run].mean():+7.2f} (n={int(run.sum())}) others {sv[oth].mean():+6.2f}')
    return mc, res


# ------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--parts', default='A,B')
    ap.add_argument('--sec', default=os.path.join(DB, 'mnq_ohlcv1s_2025-12-01_2025-12-31.dbn.zst'))
    ap.add_argument('--tape', default=os.path.join(AR, 'mnq_trades_2026-01-01_2026-04-16.dbn.zst'))
    ap.add_argument('--tape2', default=os.path.join(AR, 'mnq_trades_2026-04-10_2026-04-17.dbn.zst'))
    ap.add_argument('--m1a', default=os.path.join(DB, 'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst'))
    ap.add_argument('--m1b', default=os.path.join(DB, 'MNQ_ohlcv-1m_2026-2026.dbn.zst'))
    ap.add_argument('--sides-out', default='exec_pilot_sides.csv')
    a = ap.parse_args(); parts = a.parts.split(',')

    # check (4): sign convention on a synthetic case
    assert cost(100.5, 100.0, +1) == 2 and cost(100.5, 100.0, -1) == -2 and cost(99.75, 100.0, -1) == 1
    print('check 4: cost sign convention OK (buy 2 ticks above R = +2, sell 1 tick below R = +1)')

    ref = pd.read_csv('s1_reference_trades_ntbars_v3.csv', parse_dates=['entry_time', 'exit_time'])
    ref['d'] = pd.to_datetime(ref.date).dt.date
    y = ref[(ref.d >= dt.date(2019, 6, 1)) & (ref.d <= dt.date(2026, 9, 30))]
    sides_yr = 2 * len(y) / ((dt.date(2026, 9, 30) - dt.date(2019, 6, 1)).days / 365.25)
    print(f'sides per year (reference 2019-06..2026-09): {sides_yr:.0f} -> 1 tick/side = ${sides_yr * TICK * 2:.0f}/yr per micro')

    allS = []; m1 = m1b = None
    # -------------------------------------------------------------------------------------------- Part A
    if 'A' in parts:
        print('\n==================== PART A: Dec-2025 ohlcv-1s (MNQ.c.0), 1-second rules only ====================')
        SA = build_sides(ref, dt.date(2025, 12, 1), dt.date(2025, 12, 31), 'A')
        B = load_1s(a.sec)
        et = ns_to_et(B.ts); m = np.asarray(et.hour) * 60 + np.asarray(et.minute); rth = (m >= 570) & (m < 960)
        vol = pd.Series(B.v[rth]).groupby(np.asarray(et[rth].normalize())).sum()
        ids = pd.Series(B.iid[rth]).groupby(np.asarray(et[rth].normalize())).first()
        print(f'1s bars: {len(B.ts):,}, {ns_to_et(B.ts[:1])[0]} .. {ns_to_et(B.ts[-1:])[0]} ET')
        print('RTH volume per session in the file (contract id):')
        print('  ' + '  '.join(f'{pd.Timestamp(k):%m-%d} {v / 1e3:5.0f}k ({ids[k]})' for k, v in vol.items()))
        rows = []
        for _, r in SA.iterrows():
            e = eval_1s(B, int(r['T']), int(r.s), r.kind)
            if e is None: print(f'  side outside the file: {r.date} {r.kind} {r.T_et}'); continue
            rows.append({**r.to_dict(), **{f'1s_{k}' if k not in ('C', 'R', 'iid') else k: v for k, v in e.items()}})
        SA = pd.DataFrame(rows)
        m1 = load_1m([a.m1a], dt.date(2025, 12, 1), dt.date(2025, 12, 31))
        allS.append(SA)
    # -------------------------------------------------------------------------------------------- Part B
    if 'B' in parts:
        print('\n==================== PART B: 2026-01-02..2026-04-16 trades tape (MNQ.c.0, aggressor side) ====================')
        SB = build_sides(ref, dt.date(2026, 1, 1), dt.date(2026, 4, 16), 'B')
        P, volB, ntr, cover = load_tape([(a.tape, 0), (a.tape2, None)], SB['T'].to_numpy())
        print(f'tape: {ntr:,} trades read, {len(P.ts):,} kept in windows; sides unknown (N): {np.mean(P.sd == 0):.2%} of kept trades')
        for f_, lo, hi in cover: print(f'  {f_}: used {ns_to_et([lo])[0]} .. {ns_to_et([hi])[0]} ET (last trade in the file)')
        print('RTH volume per session in the file:')
        print('  ' + '  '.join(f'{k:%m-%d} {v / 1e3:5.0f}k' for k, v in sorted(volB.items())))
        B2 = bars_from_trades(P)
        rows = []
        for _, r in SB.iterrows():
            T = int(r['T'])
            if not any(lo <= T - WIN_PRE and T + WIN_POST <= hi for _, lo, hi in cover):
                print(f'  side not fully covered by the tape (file gap/truncation): {r.date} {r.kind} {r.T_et}'); continue
            e = eval_tape(P, int(r['T']), int(r.s), r.kind); e1 = eval_1s(B2, int(r['T']), int(r.s), r.kind)
            if e is None or e1 is None: print(f'  side outside the tape: {r.date} {r.kind} {r.T_et}'); continue
            assert abs(e['R'] - e1['R']) < 1e-9 and abs(e['C'] - e1['C']) < 1e-9, 'tape and its 1s bars disagree on R/C'
            rows.append({**r.to_dict(), **e, **{f'1s_{k}': v for k, v in e1.items() if k not in ('C', 'R', 'iid')}})
        SB = pd.DataFrame(rows)
        m1b = load_1m([a.m1a, a.m1b], dt.date(2026, 1, 1), dt.date(2026, 4, 16))
        allS.append(SB)

    S = pd.concat(allS, ignore_index=True)
    # -------------------------------------------------------------------------------------------- checks 1 and 2
    print('\n==================== CHECKS ====================')
    m1all = pd.concat([x for x in (m1, m1b) if x is not None])
    m1all = m1all[~m1all.index.duplicated(keep='last')]
    key = np.where(S.kind == 'flat', S['T'] - 60 * NS, S['T'])
    ref1m = np.where(S.kind == 'flat', m1all.close.reindex(key).to_numpy(), m1all.open.reindex(key).to_numpy())
    eq = np.isclose(ref1m, S.R.to_numpy())
    print(f'check 1: R equals the Databento 1-minute bar (open at T / close of the minute ending at T) on {eq.sum()} of {len(S)} sides')
    if (~eq).any():
        print(S.loc[~eq, ['part', 'date', 'kind', 'T_et', 'R']].assign(m1=ref1m[~eq]).to_string())
        if 'B' in parts:
            print('  detail: the first (last, for flats) trade taken in file/receive order instead of exchange-time order:')
            for i in np.flatnonzero(~eq):
                r = S.iloc[i]
                if r.part != 'B': continue
                T = int(r['T'])
                m_ = ((P.ts >= T - NS) & (P.ts < T)) if r.kind == 'flat' else ((P.ts >= T) & (P.ts < T + NS))
                idx = np.flatnonzero(m_); j = idx[np.argmax(P.seq[idx])] if r.kind == 'flat' else idx[np.argmin(P.seq[idx])]
                print(f'    {r.date} {r.kind:5s} R(exchange order) {r.R}  R(receive order) {P.px[j]}  1-minute bar {ref1m[i]}'
                      f'  -> receive order {"matches" if np.isclose(P.px[j], ref1m[i]) else "does not match"}')
    S['off'] = S.R - S.refpx
    print('check 2: R - reference-file price (NinjaTrader bars; back-adjusted continuous) by part / contract / roll window:')
    for (p_, iid, rl), g in S.groupby(['part', 'iid', 'roll']):
        mode = g.off.round(2).mode().iloc[0]; near = (np.abs(g.off - mode) <= TICK + 1e-9).mean()
        print(f'  part {p_} contract {iid} roll={rl!s:5s}: {len(g):3d} sides, modal offset {mode:+8.2f}, within +-1 tick of it {near:4.0%},'
              f' exactly {np.isclose(g.off, mode).mean():4.0%}, residual range {g.off.min() - mode:+.2f} .. {g.off.max() - mode:+.2f}')
    # check 3: worked examples
    print('check 3: worked examples')
    if 'B' in parts:
        for kd in ('entry', 'flat'):
            r = SB[(SB.kind == kd) & ~SB.roll].iloc[0]; T = int(r['T'])
            i0 = np.searchsorted(P.ts, T - 2 * NS); i1 = np.searchsorted(P.ts, T + 2 * NS)
            print(f"  B {r.date} {kd} T={r.T_et} s={int(r.s):+d}: C={r.C} R={r.R} t0=+{r.t0_ms:.0f}ms mtouch250={r.mtouch_250} mnext={r.mnext}"
                  f" m1s={r['1s_m1s']} | conservative k=0 H=10 fill {r['c_0_10']} filled={r['cf_0_10']} marketable={r['mk_0_10']}")
            seg = pd.DataFrame({'et': ns_to_et(P.ts[i0:i1]).strftime('%H:%M:%S.%f').str[:-3], 'px': P.px[i0:i1], 'sz': P.sz[i0:i1], 'side': P.sd[i0:i1]})
            print('    ' + seg.head(14).to_string(index=False).replace('\n', '\n    '))
    if 'A' in parts:
        r = SA.iloc[0]; T = int(r['T']); j0 = np.searchsorted(B.ts, T - 3 * NS); j1 = np.searchsorted(B.ts, T + 4 * NS)
        print(f"  A {r.date} {r.kind} T={r.T_et} s={int(r.s):+d}: C={r.C} R={r.R} m1s={r['1s_m1s']} conservative k=0 H=10 {r['1s_c_0_10']} filled={r['1s_cf_0_10']}")
        seg = pd.DataFrame({'et': ns_to_et(B.ts[j0:j1]).strftime('%H:%M:%S'), 'o': B.o[j0:j1], 'h': B.h[j0:j1], 'l': B.l[j0:j1], 'c': B.c[j0:j1], 'v': B.v[j0:j1]})
        print('    ' + seg.to_string(index=False).replace('\n', '\n    '))

    # -------------------------------------------------------------------------------------------- results
    print('\n==================== RESULTS ====================')
    out = {}
    if 'A' in parts:
        print('\nPART A (1-second rules; market = adverse extreme of the first 1-second bar at/after T)')
        out['A_all'] = report_set(SA, f'Part A all sides in the file ({int(SA.roll.sum())} in the roll window, expiring contract)', '1s_m1s', '1s_')
        fa = SA[~SA.roll]
        if len(fa): out['A_front'] = report_set(fa, 'Part A front-contract sides only (Dec 5)', '1s_m1s', '1s_')
    if 'B' in parts:
        pb = SB[~SB.roll]; rb = SB[SB.roll]
        print('\nPART B primary (outside the March roll window), TAPE rules: market = touch rebuilt at t0 + 250 ms')
        for lat in LATS:
            c_ = cost(pb[f'mtouch_{lat // 1_000_000}'], pb.R, pb.s); print(f'  Mtouch latency {lat // 1_000_000:4d} ms: {fmt(boot(c_, pb.event))}')
        c_ = cost(pb.mnext, pb.R, pb.s); print(f'  Mnext (next same-side aggressor at t1): {fmt(boot(c_, pb.event))}')
        c_ = cost(pb['1s_m1s'], pb.R, pb.s); print(f'  M1s (1-second bars built from the tape): {fmt(boot(c_, pb.event))}')
        d_ = cost(pb['1s_m1s'], pb.R, pb.s) - cost(pb.mtouch_250, pb.R, pb.s)
        print(f'  calibration (i): M1s - Mtouch per side {fmt(boot(d_, pb.event))}   {describe(d_)}')
        print(f'  first trade after T arrives +{pb.t0_ms.median():.0f} ms (median), 90th pct +{pb.t0_ms.quantile(.9):.0f} ms')
        out['B_tape'] = report_set(pb, 'Part B primary, tape rules', 'mtouch_250', '', has_mk=True)
        print('\nPART B primary, 1-SECOND rules on 1-second bars built from the same tape (calibration ii)')
        out['B_1s'] = report_set(pb, 'Part B primary, 1-second rules', '1s_m1s', '1s_')
        if len(rb):
            print('\nPART B roll window (expiring MNQH6, reported only)')
            report_set(rb, 'Part B roll window, tape rules', 'mtouch_250', '', has_mk=True)

        # decision rules (plan section 5)
        print('\n==================== DECISION RULES (plan section 5) ====================')
        mc = boot(cost(pb.mtouch_250, pb.R, pb.s), pb.event)['mean']
        verdict = 'ADEQUATE' if mc <= 1.0 else ('BORDERLINE' if mc <= 1.5 else 'BREACH')
        print(f'1-tick assumption: Part B Mtouch(250 ms) mean {mc:+.2f} tick/side -> {verdict}')
        tape_res = out['B_tape'][1]; s1_res = out['B_1s'][1]
        best = max(VARIANTS, key=lambda v: tape_res[('c',) + v]['mean'])
        bt = tape_res[('c',) + best]; b1 = s1_res[('c',) + best]
        a_front = out.get('A_front')
        a_sv = a_front[1][('c',) + best]['mean'] if a_front else np.nan
        promising = [v for v in VARIANTS if tape_res[('c',) + v]['mean'] >= 0.5 and not (a_front and a_front[1][('c',) + v]['mean'] < 0)]
        print(f'best conservative tape variant: k={best[0]} H={best[1]}s saving {fmt(bt)}; Part A front-contract saving {a_sv:+.2f}')
        print(f'PROMISING variants (>= +0.5 tick/side on Part B, not negative on Part A front): {promising or "none"}')
        cal_i = abs(boot(d_, pb.event)['mean']) <= 0.25
        cal_ii = np.sign(b1['mean']) == np.sign(bt['mean']) and abs(b1['mean'] - bt['mean']) <= 0.5
        print(f'1-second calibration: (i) |M1s - Mtouch| = {abs(boot(d_, pb.event)["mean"]):.2f} -> {"pass" if cal_i else "FAIL"};'
              f' (ii) best variant 1s saving {b1["mean"]:+.2f} vs tape {bt["mean"]:+.2f} -> {"pass" if cal_ii else "FAIL"}')
        hw = 1.96 * bt['se'] * np.sqrt(bt['n'] / sides_yr)
        print(f'power: a year (~{sides_yr:.0f} sides) would give the best variant a 95% half-width of about {hw:.2f} tick/side')
        go1 = bool(promising) and cal_i and cal_ii; go2 = mc > 1.25 and cal_i
        print(f'PURCHASE: {"GO" if (go1 or go2) else "NO-GO"} (promising branch {go1}, cost-doubt branch {go2})')
        print(f'SIM101 EXECUTION TRIAL: {"recommended (see plan conditions)" if go1 else "NO"}')

        # ---------------------------------------------------------------------------------------- post-hoc (labelled as such)
        print('\n==================== POST-HOC DIAGNOSTICS (not pre-registered: they explain the verdict, they do not change it) ====================')
        hh = pd.to_datetime(pb.T_et).dt.strftime('%H:%M').to_numpy()
        slot = np.where(hh == '10:00', '10:00', np.where(pb.kind.to_numpy() == 'flat', 'close', 'other'))
        print(f'decision slots: 10:00 = {int((slot == "10:00").sum())} sides, 10:30-15:30 = {int((slot == "other").sum())}, flat at the close = {int((slot == "close").sum())}')

        def trim(x, q=0.10):
            x = np.sort(np.asarray(x, float)); k = int(len(x) * q); return x[k:len(x) - k].mean()
        print('(a) latency profile (exchange time from the first trade after T to the order reaching the book), tape rules, conservative fills')
        print('     lat |  market cost: mean [95% CI]          median  trim10 | by slot 10:00 / other / close (mean) | saving k=0 H=10s   k=1 H=60s (mean, median)')
        prof = {}
        for lm in (0, 5, 10, 25, 50, 100, 250, 500, 1000):
            ev = pd.DataFrame([eval_tape(P, int(r['T']), int(r.s), r.kind, LAT=lm * 1_000_000) for _, r in pb.iterrows()], index=pb.index)
            mc_ = cost(ev.mkt, pb.R, pb.s).to_numpy(); s010 = ((ev.mkt - ev.c_0_10) * pb.s / TICK).to_numpy(); s160 = ((ev.mkt - ev.c_1_60) * pb.s / TICK).to_numpy()
            prof[lm] = (mc_, s160, ev.cf_1_60.to_numpy(bool))
            bm = boot(mc_, pb.event)
            print(f'  {lm:4d}ms | {bm["mean"]:+6.2f} [{bm["lo"]:+6.2f}, {bm["hi"]:+6.2f}]  {np.median(mc_):+6.2f}  {trim(mc_):+6.2f} |'
                  f' {mc_[slot == "10:00"].mean():+7.2f} / {mc_[slot == "other"].mean():+5.2f} / {mc_[slot == "close"].mean():+5.2f} |'
                  f' {s010.mean():+6.2f} {np.median(s010):+6.2f}   {s160.mean():+6.2f} {np.median(s160):+6.2f}')
        assert np.allclose(prof[250][0], cost(pb.mtouch_250, pb.R, pb.s).to_numpy()), 'latency profile disagrees with the primary run at 250 ms'
        print('(b) the same without the 10:00 decisions (10:30-15:30 and the close only)')
        for lm in (0, 25, 50, 100, 250):
            mc_, s160, _ = prof[lm]; keep_ = slot != '10:00'
            print(f'  {lm:4d}ms | market {fmt(boot(mc_[keep_], pb.event.to_numpy()[keep_]))} median {np.median(mc_[keep_]):+.2f} |'
                  f' saving k=1 H=60s {fmt(boot(s160[keep_], pb.event.to_numpy()[keep_]))}')
        thr = pb.drop_duplicates('trade').points.quantile(0.8)
        run = ((pb.kind == 'entry') & (pb.points >= thr)).to_numpy(); oth = ((pb.kind == 'entry') & (pb.points < thr)).to_numpy()
        print(f'(b2) k=1 H=60s by latency: misses, their mean cost, and the saving on runner entries (top 20% trades, >= {thr:+.2f} pts) vs other entries')
        for lm in (0, 25, 50, 100, 250):
            _, s160, fl = prof[lm]
            print(f'  {lm:4d}ms | misses {int((~fl).sum()):2d} of {len(fl)}, mean saving on misses {s160[~fl].mean() if (~fl).any() else np.nan:+7.2f},'
                  f' worst {s160.min():+7.2f} | runners {s160[run].mean():+6.2f} (n={int(run.sum())})  other entries {s160[oth].mean():+6.2f}'
                  f'  exits+flats {s160[pb.kind.to_numpy() != "entry"].mean():+6.2f}')
        print('(c) sides with |market cost| >= 40 ticks at 250 ms (cost at 0 / 50 / 250 ms; points = the trade\'s final result)')
        big = np.flatnonzero(np.abs(prof[250][0]) >= 40)
        for i in big:
            r = pb.iloc[i]
            print(f'    {r.date} {hh[i]} {r.kind:5s} {"buy " if r.s > 0 else "sell"}: {prof[0][0][i]:+6.1f} / {prof[50][0][i]:+6.1f} / {prof[250][0][i]:+7.1f}  points {r.points:+8.2f}')

    # -------------------------------------------------------------------------------------------- live comparison
    print('\n==================== LIVE Sim101 (recomputed from the exports) ====================')
    try:
        ex = os.path.normpath(os.path.join(os.getcwd(), '..', '..'))
        tr = pd.read_csv(os.path.join(ex, 'NoiseArea_trades_MNQ_Sim101_20261018_163602.csv'))
        bars = pd.read_csv(os.path.join(ex, 'NoiseArea_bars_MNQ_strategy_Sim101.csv')).drop_duplicates('time_et', keep='last')
        bo = bars.assign(t=pd.to_datetime(bars.time_et)).set_index('t').open
        for _, r in tr.iterrows():
            dr = 1 if r.direction == 'long' else -1
            for tm, px, sg in ((r.entry_time_et, r.entry_price, dr), (r.exit_time_et, r.exit_price, -dr)):
                t = pd.Timestamp(tm); c = t.ceil('5min'); c = c + pd.Timedelta(minutes=5) if c == t else c
                print(f'  {tm} {"buy " if sg > 0 else "sell"} fill {px} vs next-bar open {bo.get(c)} ({c:%H:%M} bar) = {(px - bo.get(c)) * sg / TICK:+.1f} ticks')
    except Exception as e:
        print(f'  live comparison not recomputed: {e}')

    S.drop(columns=['stamp']).to_csv(a.sides_out, index=False)
    print(f'\nper-side table written to {a.sides_out} ({len(S)} rows)')


if __name__ == '__main__':
    main()
