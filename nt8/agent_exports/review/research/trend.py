"""Multi-day trend-following sleeve beside NoiseArea, per TREND_PLAN.txt (pre-registered 2026-10-19).
   python trend.py [--work DIR] [--dbdir DIR] [--nt CSV]     -> report on stdout (saved as trend_results_raw.txt), trend_daily.csv
   Builds 5-minute bars that keep the raw (unadjusted) close next to convert.build's back-adjusted prices into --work if missing.
   Research only: nothing here touches NinjaTrader."""
import os, argparse
import numpy as np, pandas as pd
from research_engine import Market, fam_A, WARMUP

HERE = os.path.dirname(os.path.abspath(__file__))
P_REF = 27875.0                     # LONGHIST: mean 2026 MNQ 09:30 open; fixed so dollars match spec section 7
DOLLAR = 2.0 * P_REF                # today's $ per unit of return, per micro
C_TODAY = 0.5 / P_REF               # cost basis (b): 1 tick + $0.50 on MNQ in 2026, per side, fraction of notional
TS_L = [21, 63, 126, 252]
SMA_N = [50, 100, 200]
SIGS = [f'TS{L}' for L in TS_L] + [f'SMA{N}' for N in SMA_N]
DISC_END = '2018-12-31'
NQ_END = '2019-05-03'
rng = np.random.default_rng(20261019)


# ------------------------------------------------------------------------------------------------ data
def build_raw(files, out):
    """convert.build (difference back-adjustment at every contract switch, 1m -> 5m ET stamped at close) that also keeps the raw
    close and the instrument id of every bar, and writes the switch list next to the bars."""
    import databento as db
    parts = [db.DBNStore.from_file(f).to_df()[['instrument_id', 'open', 'high', 'low', 'close', 'volume']] for f in files]
    df = pd.concat(parts); del parts
    df = df[~df.index.duplicated(keep='last')].sort_index()
    iid = df.instrument_id.values
    sw = np.flatnonzero(iid[1:] != iid[:-1]) + 1
    off = np.array([df.open.iat[k] - df.close.iat[k - 1] for k in sw])
    adj = np.zeros(len(df))
    for k, o in zip(sw, off):
        adj[:k] += o
    df['raw_close'] = df.close.values
    for c in ('open', 'high', 'low', 'close'):
        df[c] = df[c].values + adj
    close_et = (df.index + pd.Timedelta(minutes=1)).tz_convert('America/New_York').tz_localize(None)
    df = df.assign(bucket=close_et.ceil('5min'))
    g = df.groupby('bucket')
    b = pd.DataFrame({'open': g.open.first(), 'high': g.high.max(), 'low': g.low.min(), 'close': g.close.last(),
                      'volume': g.volume.sum(), 'raw_close': g.raw_close.last(), 'iid': g.instrument_id.last()})
    b.index.name = 'time'; b = b.reset_index(); b.to_csv(out, index=False)
    pd.DataFrame({'time_et': close_et[sw], 'offset': off, 'old': iid[sw - 1], 'new': iid[sw]}).to_csv(out.replace('.csv', '_switches.csv'), index=False)


def daily_table(b, has_raw):
    """One row per RTH date: C = close of the 16:00 bar (else the session's last RTH bar, no decision), F = open of the next bar
    when it is the 16:05 bar (else C), O930 = open of the 09:35 bar, R/iid = raw close and instrument at the C bar."""
    tt = pd.DatetimeIndex(pd.to_datetime(b['time']))
    m = np.asarray(tt.hour * 60 + tt.minute)
    ri = np.flatnonzero((m > 570) & (m <= 960))
    s = pd.Series(ri, index=tt[ri].normalize())
    last = s.groupby(level=0).max(); first = s.groupby(level=0).min()
    i, f = last.values, first.values
    nxt = np.minimum(i + 1, len(b) - 1)
    D = pd.DataFrame(index=last.index)
    D['C'] = b.close.values[i]; D['tC'] = tt[i]; D['decide'] = m[i] == 960
    ok = np.asarray((tt[nxt] - tt[i]) == pd.Timedelta(minutes=5))
    D['hasF'] = D.decide.values & ok
    D['F'] = np.where(D.hasF.values, b.open.values[nxt], D.C.values)
    D['O930'] = np.where(m[f] == 575, b.open.values[f], np.nan)
    if has_raw:
        D['R'] = b.raw_close.values[i]; D['iid'] = b.iid.values[i]
    if not D.decide.iloc[-1]:                       # incomplete final session (download/export taken mid-session)
        print(f"   dropped incomplete final session {D.index[-1].date()} (last bar {D.tC.iloc[-1]})")
        D = D.iloc[:-1]
    return D


def mark_rolls(D, switches):
    D['roll'] = 0
    k = np.searchsorted(D.tC.values.astype('datetime64[ns]'), pd.to_datetime(switches.time_et).values.astype('datetime64[ns]'), side='left')
    for j in k[k < len(D)]:
        D.iloc[j, D.columns.get_loc('roll')] += 1
    return D


# ------------------------------------------------------------------------------------------------ signals, positions, returns
def signals(I):
    """Trailing-window signals on the excess-return index I (a pd.Series). Values -1/0/+1, NaN where the window is not full."""
    v = I.values.astype(float); out = {}
    for L in TS_L:
        s = np.full(len(v), np.nan); s[L:] = np.sign(v[L:] / v[:-L] - 1.0); out[f'TS{L}'] = s
    cs = np.r_[0.0, np.cumsum(v)]
    for N in SMA_N:
        s = np.full(len(v), np.nan); ma = (cs[N:] - cs[:-N]) / N; s[N - 1:] = np.sign(v[N - 1:] - ma); out[f'SMA{N}'] = s
    return out


def carry(p, decide):
    return pd.Series(np.where(decide, p, np.nan)).ffill().fillna(0.0).values


def positions(sig, decide):
    base = {}
    for sn in SIGS:
        s = sig[sn]
        base[f'{sn}-LF'] = carry(np.where(np.isnan(s), 0.0, (s > 0).astype(float)), decide)
        base[f'{sn}-LS'] = carry(np.where(np.isnan(s), 0.0, s), decide)
    out = dict(base)
    for md in ('LF', 'LS'):
        out[f'EW-{md}'] = np.mean([base[f'{sn}-{md}'] for sn in SIGS], axis=0)
        for drop in SIGS:
            out[f'EW-{md}-x{drop}'] = np.mean([base[f'{sn}-{md}'] for sn in SIGS if sn != drop], axis=0)
    out['B&H'] = np.ones(len(decide))
    return out


def leg(D, p, c, delay=False):
    """Daily return on notional of position p (decided at C_d, filled at F_d) after costs c (fraction of notional per side)."""
    p = np.asarray(p, float)
    if delay: p = np.r_[0.0, p[:-1]]                # signal at C_d executed at C_{d+1}
    pp = np.r_[0.0, p[:-1]]; dp = p - pp
    x = np.nan_to_num(D.x.values)
    fillterm = 0.0 if delay else dp * (D.C.values - D.F.values) / D.R.values
    return pp * x + fillterm - np.abs(dp) * c - D.roll.values * np.abs(pp) * 2 * c


# ------------------------------------------------------------------------------------------------ NoiseArea leg (as longhist.py)
def noisearea(csv, pv, tick, comm, name):
    mk = Market(csv, pv, name)
    pg, nt = fam_A(mk, vm=1.0, slip=0.0, comm=0.0)
    pn, nt2 = fam_A(mk, vm=1.0, slip=tick, comm=comm)
    assert (nt == nt2).all()
    P = np.array([x.O[0] for x in mk.days])
    df = pd.DataFrame({'gross': np.nan_to_num(pg), 'net': np.nan_to_num(pn), 'nt': nt, 'P': P}, index=mk.dates).iloc[WARMUP:]
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    hit = df.index.isin(raw.index); df.loc[hit, 'P'] = raw.reindex(df.index[hit]).values
    chk = (df.gross - df.net).sum() - df.nt.sum() * 2 * (tick * pv + comm)
    df['NA_g'] = df.gross / (pv * df.P); df['NA_a'] = df.net / (pv * df.P); df['NA_b'] = df.NA_g - df.nt * 2 * C_TODAY
    print(f"   NoiseArea {name}: {len(df)} sessions {df.index[0].date()}..{df.index[-1].date()}, raw 09:30 price on {hit.sum()}, "
          f"cost linearity {chk:+.4f} $, {int(df.nt.sum())} trades")
    return df


# ------------------------------------------------------------------------------------------------ statistics
def st(x, idx=None):
    x = np.asarray(x, float); n = len(x); sd = x.std(ddof=1)
    eq = np.r_[0.0, np.cumsum(x)]; pk = np.maximum.accumulate(eq); dd = pk - eq
    i = int(dd.argmax()); j = int(np.argmax(eq[:i + 1]))
    uw = run = 0
    for u in dd[1:] > 1e-12:
        run = run + 1 if u else 0; uw = max(uw, run)
    ann = x.mean() * 252
    w12 = (np.convolve(x, np.ones(252), 'valid').min() * DOLLAR) if n >= 252 else np.nan
    r = dict(n=n, sh=x.mean() / sd * np.sqrt(252) if sd > 0 else np.nan, t=x.mean() / (sd / np.sqrt(n)) if sd > 0 else np.nan,
             yr=ann * DOLLAR, mdd=dd.max() * DOLLAR, cush=dd.max() / ann if ann > 0 else np.inf, uw=uw, w12=w12,
             net=x.sum() * DOLLAR)
    if idx is not None and i > 0:
        r['dd_from'] = idx[max(j - 1, 0)].date(); r['dd_to'] = idx[i - 1].date()
    return r


def fmt(r):
    return (f"Sharpe {r['sh']:5.2f}  t {r['t']:5.2f}  ${r['yr']:7,.0f}/yr  maxDD ${r['mdd']:7,.0f}  cushion/profit {r['cush']:5.2f}  "
            f"under water {r['uw']:4d}  worst 12m ${r['w12']:7,.0f}")


def sharpe(x):
    x = np.asarray(x, float); return x.mean() / x.std(ddof=1) * np.sqrt(252)


def micro_ratio(w):
    best = None
    for a in range(1, 11):
        for b in range(1, 11):
            e = abs(b / a - w)
            if best is None or e < best[0] - 1e-12: best = (e, a, b)
    return f"{best[1]} NoiseArea : {best[2]} trend"


def block_p(a, b, blk=63, reps=2000):
    """Paired stationary-block bootstrap: share of resamples with Sharpe(a) - Sharpe(b) <= 0."""
    n = len(a); nb = int(np.ceil(n / blk)); c = 0
    for _ in range(reps):
        st_ = rng.integers(0, n - blk, nb); ix = (st_[:, None] + np.arange(blk)).ravel()[:n]
        c += sharpe(a[ix]) - sharpe(b[ix]) <= 0
    return c / reps


def alpha(y, x):
    beta = np.cov(y, x, ddof=1)[0, 1] / x.var(ddof=1); a = y.mean() - beta * x.mean(); e = y - a - beta * x
    return a * 252, a / (e.std(ddof=2) / np.sqrt(len(y))), beta


# ------------------------------------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--work', default=HERE)
    ap.add_argument('--dbdir', default=r'C:\Users\linde\OneDrive\Desktop\Desktop\databento_data')
    ap.add_argument('--nt', default=os.path.join(HERE, '..', '..', 'NoiseArea_bars_MNQ_strategy_Backtest.csv'))
    a = ap.parse_args()
    W = a.work; os.makedirs(W, exist_ok=True)
    nq_csv = os.path.join(W, 'nq_2010-2019_5m_raw.csv'); mdb_csv = os.path.join(W, 'mnq_db_2019-2026_5m_raw.csv')
    nt_csv = os.path.join(W, 'nt_bars_trend.csv')
    if not os.path.exists(nq_csv): build_raw([os.path.join(a.dbdir, 'NQ_ohlcv-1m_2010-2019.dbn.zst')], nq_csv)
    if not os.path.exists(mdb_csv):
        build_raw([os.path.join(a.dbdir, f) for f in ('mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst',
                   'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst', 'MNQ_ohlcv-1m_2026-2026.dbn.zst')], mdb_csv)
    if not os.path.exists(nt_csv):
        pd.read_csv(a.nt).rename(columns={'time_et': 'time'}).to_csv(nt_csv, index=False)

    print("=== DATA ===")
    NQ = daily_table(pd.read_csv(nq_csv), True); MDB = daily_table(pd.read_csv(mdb_csv), True)
    MNT = daily_table(pd.read_csv(nt_csv), False)
    swq = pd.read_csv(nq_csv.replace('.csv', '_switches.csv')); swm = pd.read_csv(mdb_csv.replace('.csv', '_switches.csv'))
    NQ = mark_rolls(NQ, swq); MDB = mark_rolls(MDB, swm); MNT = mark_rolls(MNT, swm)
    off = (MDB.R - MNT.C.reindex(MDB.index)).reindex(MNT.index).ffill().bfill()
    MNT['R'] = MNT.C + off
    print(f"   NT raw close from Databento on {MNT.index.isin(MDB.index).sum()} of {len(MNT)} days (rest: last raw-minus-adjusted offset)")
    for nm, D in (('NQ', NQ), ('MNQ-DB', MDB), ('MNQ-NT', MNT)):
        D['x'] = D.C.diff() / D.R.shift(1)
        yrs = (D.index[-1] - D.index[0]).days / 365.25
        print(f"   {nm:7s} {len(D)} sessions {D.index[0].date()}..{D.index[-1].date()}, decisions on {int(D.decide.sum())}, "
              f"no-16:00 days {int((~D.decide).sum())}, rolls charged {int(D.roll.sum())} ({D.roll.sum()/yrs:.2f}/yr), "
              f"16:05 fill bar present on {D.hasF.sum()/D.decide.sum()*100:.1f}% of decision days")
    print(f"   switch rows: NQ {len(swq)}, MNQ {len(swm)}")

    # signal indices
    fred = pd.read_csv(os.path.join(HERE, 'nasdaq100_fred.csv')); fred.columns = ['date', 'v']
    fred['v'] = pd.to_numeric(fred.v, errors='coerce'); fred = fred.dropna(); fred = pd.Series(fred.v.values, index=pd.to_datetime(fred.date))
    fr = fred.pct_change().dropna()
    ret_disc = pd.concat([fr[fr.index <= NQ.index[0]], NQ.x.iloc[1:]])
    assert pd.Timestamp(NQ_END) in NQ.index and NQ.decide.loc[NQ_END]
    print(f"   discovery signal history: FRED NASDAQ100 {fr.index[0].date()}..{NQ.index[0].date()} ({(fr.index <= NQ.index[0]).sum()} returns), then NQ")
    hist = ret_disc[ret_disc.index <= NQ_END]; rq = NQ.R.loc[NQ_END]

    def index_for(D):
        j = (MDB.R.loc[D.index[0]] - rq) / rq if D.index[0] in MDB.index else (D.R.iloc[0] - rq) / rq
        r = pd.concat([hist, pd.Series([j], index=[D.index[0]]), D.x.iloc[1:]])
        assert r.index.is_monotonic_increasing and not r.index.duplicated().any()
        return (1 + r).cumprod(), j
    Iq = (1 + ret_disc).cumprod()
    Idb, jdb = index_for(MDB); Int, jnt = index_for(MNT)
    print(f"   junction NQ {NQ_END} -> MNQ {MDB.index[0].date()}: {jdb*100:+.3f}% (DB) / {jnt*100:+.3f}% (NT, same Databento prices)")

    def run(D, I, cost_a):
        sig_full = signals(I); k = I.index.get_indexer(D.index); assert (k >= 0).all()
        sig = {s: v[k] for s, v in sig_full.items()}
        P = positions(sig, D.decide.values)
        out = {}
        for nm, p in P.items():
            out[nm + '|b'] = leg(D, p, C_TODAY); out[nm + '|a'] = leg(D, p, cost_a); out[nm + '|g'] = leg(D, p, 0.0)
            out[nm + '|d'] = leg(D, p, C_TODAY, delay=True); out[nm + '|x3'] = leg(D, p, 3 * C_TODAY)
        return pd.DataFrame(out, index=D.index), P, sig
    Tq, Pq, Sq = run(NQ, Iq, (0.25 * 20 + 2.0) / (20 * NQ.R.values))
    Tdb, Pdb, Sdb = run(MDB, Idb, (0.25 * 2 + 0.5) / (2 * MDB.R.values))
    Tnt, Pnt, Snt = run(MNT, Int, (0.25 * 2 + 0.5) / (2 * MNT.R.values))
    VAR16 = [f'{s}-{m}' for m in ('LF', 'LS') for s in SIGS] + ['EW-LF', 'EW-LS']

    print("\n=== NOISEAREA LEG ===")
    Nq = noisearea(nq_csv, 20.0, 0.25, 2.00, 'NQ'); Nq = Nq[Nq.index <= NQ_END]
    Ndb = noisearea(mdb_csv, 2.0, 0.25, 0.50, 'MNQ-DB'); Nnt = noisearea(nt_csv, 2.0, 0.25, 0.50, 'MNQ-NT')
    Ndb = Ndb[Ndb.index >= '2019-05-06']; Nnt = Nnt[Nnt.index >= '2019-05-06']

    def frame(T, N, end=None):
        F = T.copy(); N = N[N.index <= T.index[-1]]
        for c in ('NA_b', 'NA_a', 'NA_g'): F[c] = N[c].reindex(F.index).fillna(0.0)
        F = F[F.index >= N.index[0]]
        miss = (~N.index.isin(F.index)).sum(); assert miss == 0, f"{miss} NoiseArea days without a trend row"
        return F if end is None else F[F.index <= end]
    Fd = frame(Tq, Nq, DISC_END); Fq_all = frame(Tq, Nq, NQ_END); Fdb = frame(Tdb, Ndb); Fnt = frame(Tnt, Nnt)
    J_nt = pd.concat([Fq_all, Fnt]); J_db = pd.concat([Fq_all, Fdb])
    print(f"   windows: discovery {Fd.index[0].date()}..{Fd.index[-1].date()} ({len(Fd)} sessions); validation DB {Fdb.index[0].date()}.."
          f"{Fdb.index[-1].date()} ({len(Fdb)}); NT {Fnt.index[0].date()}..{Fnt.index[-1].date()} ({len(Fnt)}); 16-year joined NT {len(J_nt)}, DB {len(J_db)}")

    # ------------------------------------------------------------------ AUDITS
    print("\n=== AUDITS ===")
    for nm, D in (('NQ', NQ), ('MNQ-DB', MDB)):
        same = D.iid.values[1:] == D.iid.values[:-1]
        e = np.abs(np.diff(D.C.values)[same] - np.diff(D.R.values)[same]).max()
        sw = pd.read_csv((nq_csv if nm == 'NQ' else mdb_csv).replace('.csv', '_switches.csv'))
        t = pd.to_datetime(sw.time_et).values.astype('datetime64[ns]'); tc = D.tC.values.astype('datetime64[ns]')
        inwin = (t > tc[0]) & (t <= tc[-1])
        lhs = D.C.values[-1] - D.C.values[0]; rhs = (D.R.values[-1] - D.R.values[0]) - sw.offset.values[inwin].sum()
        bh = Tq if nm == 'NQ' else Tdb
        g = bh['B&H|g'].values[1:]                                      # gross B&H return; first day is the entry
        idx_growth = np.prod(1 + np.nan_to_num(D.x.values[1:])); raw_ratio = D.R.values[-1] / D.R.values[0]
        yrs = (D.index[-1] - D.index[0]).days / 365.25
        print(f"A1 {nm}: same-contract days {same.sum()}: max |adjusted change - raw change| = {e:.2e} points ({'PASS' if e < 1e-6 else 'FAIL'}); "
              f"whole period B&H points {lhs:,.2f} = raw change {D.R.values[-1]-D.R.values[0]:,.2f} - roll gaps "
              f"{sw.offset.values[inwin].sum():,.2f} -> difference {lhs-rhs:.2e} ({'PASS' if abs(lhs-rhs) < 1e-6 else 'FAIL'})")
        print(f"   B&H gross return = sum of x: {np.abs(g - np.nan_to_num(D.x.values[1:])).max():.1e} max gap; excess-return index x{idx_growth:.3f} "
              f"vs raw price x{raw_ratio:.3f} over {yrs:.1f} years -> roll carry {(np.log(idx_growth)-np.log(raw_ratio))/yrs*100:+.2f}%/yr")
    for nm, T, D in (('NQ', Tq, NQ), ('MNQ-DB', Tdb, MDB), ('MNQ-NT', Tnt, MNT)):
        P = Pq if nm == 'NQ' else (Pdb if nm == 'MNQ-DB' else Pnt)
        ca = (0.25 * 20 + 2.0) / (20 * D.R.values) if nm == 'NQ' else 1.0 / (2 * D.R.values)
        worst = 0.0
        for v in list(P):
            p = P[v]; pp = np.r_[0.0, p[:-1]]; nside = np.abs(p - pp) + D.roll.values * np.abs(pp) * 2
            worst = max(worst, np.abs((T[v + '|g'] - T[v + '|b']).values - nside * C_TODAY).max(),
                        np.abs((T[v + '|g'] - T[v + '|a']).values - nside * ca).max())
        print(f"A2 {nm}: cost linearity over {len(P)} position series, max error {worst:.1e} ({'PASS' if worst < 1e-12 else 'FAIL'})")
    bad = 0
    for nm, I, D, S in (('NQ', Iq, NQ, Sq), ('MNQ-NT', Int, MNT, Snt), ('MNQ-DB', Idb, MDB, Sdb)):
        k = I.index.get_indexer(D.index)
        for d in rng.choice(len(D), 50 if nm != 'MNQ-DB' else 20, replace=False):
            s2 = signals(I.iloc[:k[d] + 1])
            for sn in SIGS:
                x1, x2 = S[sn][d], s2[sn][-1]
                bad += not ((np.isnan(x1) and np.isnan(x2)) or x1 == x2)
    print(f"A3 no look-ahead: 120 truncated recomputations x 7 signals, mismatches {bad} ({'PASS' if bad == 0 else 'FAIL'})")
    mis = 0
    for nm, I, D, P in (('NQ', Iq, NQ, Pq), ('MNQ-NT', Int, MNT, Pnt), ('MNQ-DB', Idb, MDB, Pdb)):
        sma = np.sign(I - I.rolling(200).mean()); ts = np.sign(I / I.shift(252) - 1)
        k = I.index.get_indexer(D.index)
        p1 = carry(np.where(sma.isna().values, 0.0, (sma.values > 0).astype(float))[k], D.decide.values)
        p2 = carry(np.nan_to_num(ts.values)[k], D.decide.values)
        m1 = int((p1 != P['SMA200-LF']).sum()); m2 = int((p2 != P['TS252-LS']).sum()); mis += m1 + m2
        print(f"A4 {nm}: pandas recomputation SMA200-LF mismatches {m1}, TS252-LS mismatches {m2}")
    print(f"A4 {'PASS' if mis == 0 else 'FAIL'}")
    # A5: NoiseArea reproduces LONGHIST (b): through 2026-10-14 on NT
    jl = pd.concat([Nq.NA_b, Nnt.NA_b[Nnt.index <= '2026-10-14']])
    r = st(jl.values, jl.index)
    print(f"A5 NoiseArea 16 years (b) to 2026-10-14 on its own days: {len(jl)} sessions, t {r['t']:.2f}, ${r['yr']:,.0f}/yr, maxDD ${r['mdd']:,.0f} "
          f"({r['dd_from']}..{r['dd_to']})   [LONGHIST: 4,092 sessions, t 4.37, $4,739/yr, maxDD $7,906]")
    r7 = st(Nnt.NA_b[Nnt.index <= '2026-10-14'].values)
    print(f"   7 years NT (b): t {r7['t']:.2f}, ${r7['yr']:,.0f}/yr, maxDD ${r7['mdd']:,.0f}   [LONGHIST: t 3.52, $6,180/yr, maxDD $4,590]")
    c = pd.concat([MDB.x, MNT.x], axis=1, join='inner').dropna(); c.columns = ['db', 'nt']
    dd_ = (c.db - c.nt).abs()
    print(f"A6 DB vs NT daily excess returns: {len(c)} common days, correlation {c.db.corr(c.nt):.4f}, largest gap {dd_.max()*100:.2f}% on "
          f"{dd_.idxmax().date()}, days with gap > 0.1%: {(dd_ > 0.001).sum()}")
    # Databento .c.0 keeps the expiring contract through expiry Friday (it stops trading 09:30), so those Fridays have no session and
    # the move from Friday 09:30 to the Sunday reopen sits inside the roll gap. NT rolls a week earlier and has them. Size of the hole:
    miss = MNT.index[~MNT.index.isin(MDB.index) & (MNT.index <= MDB.index[-1])]
    lost = []
    for d in miss:
        nx = MDB.index[MDB.index > d]
        if len(nx) == 0: continue
        nx = nx[0]; win = MNT.x[(MNT.index >= d) & (MNT.index <= nx)].sum(); lost.append(win - MDB.x.loc[nx])
    lost = np.array(lost)
    print(f"   sessions in NT but not in DB (inside DB's span): {len(miss)}, weekdays {sorted(set(miss.day_name()))}; NT return over each missing day + "
          f"the next DB day minus DB's return for that next day: sum {lost.sum()*100:+.2f}% of notional (${lost.sum()*DOLLAR:+,.0f} for 1 micro long over 7 years), "
          f"sd per event {lost.std(ddof=1)*100:.2f}%, largest {lost[np.abs(lost).argmax()]*100:+.2f}%")

    # ------------------------------------------------------------------ DISCOVERY AND SELECTION
    print("\n=== DISCOVERY (NQ, cost basis (b), today's $ per micro) ===")
    na = Fd.NA_b.values; sd_na = na.std(ddof=1); bh = Fd['B&H|b'].values
    print(f"NoiseArea alone: {fmt(st(na))}")
    print(f"B&H            : {fmt(st(bh))}")
    W_ = {}; rows = []
    for v in VAR16 + ['B&H'] + [c_.split('|')[0] for c_ in Fd.columns if c_.startswith('EW-') and '-x' in c_ and c_.endswith('|b')]:
        t_ = Fd[v + '|b'].values; w = sd_na / t_.std(ddof=1); W_[v] = w
        comb = na + w * t_
        rows.append((v, st(t_), sharpe(comb), w))
    sh_bh = sharpe(bh)
    print(f"{'variant':16s} {'Sharpe':>6s} {'net $/yr':>9s} {'maxDD $':>8s} {'w':>5s} {'combined Sharpe':>15s}  eligible")
    elig = []
    for v, r, cs, w in rows:
        if v not in VAR16 + ['B&H']: continue
        e = v in VAR16 and r['net'] > 0 and r['sh'] > sh_bh
        if e: elig.append((cs, v))
        print(f"{v:16s} {r['sh']:6.2f} {r['yr']:9,.0f} {r['mdd']:8,.0f} {w:5.2f} {cs:15.2f}  {'yes' if e else '-'}")
    print(f"NoiseArea alone Sharpe {sharpe(na):.2f}; B&H Sharpe {sh_bh:.2f}")
    if not elig:
        cand = None; print("SELECTION: no variant beats B&H in discovery -> FAIL at discovery (validation reported, never selected)")
    else:
        cand = max(elig)[1]; print(f"SELECTION: {len(elig)} eligible; candidate = {cand} (discovery combined Sharpe {max(elig)[0]:.2f}, w {W_[cand]:.3f} = {micro_ratio(W_[cand])})")

    # ------------------------------------------------------------------ VALIDATION
    def val_metrics(F, v, cost='b'):
        na = F['NA_' + cost].values; t_ = F[f'{v}|{cost}'].values; b_ = F[f'B&H|{cost}'].values
        comb = na + W_[v] * t_; combbh = na + W_['B&H'] * b_
        return dict(net=t_.sum() * DOLLAR, sh=sharpe(t_), shbh=sharpe(b_), shna=sharpe(na), shc=sharpe(comb), shcbh=sharpe(combbh),
                    cush_c=st(comb)['cush'], cush_na=st(na)['cush'], cush_cbh=st(combbh)['cush'])

    def passes(m):
        return m['net'] > 0 and m['sh'] > m['shbh'], m['shc'] > m['shna'] and m['shc'] > m['shcbh'], m['cush_c'] <= m['cush_na']

    for nm, F in (('DB', Fdb), ('NT', Fnt)):
        print(f"\n=== VALIDATION MNQ-{nm} {F.index[0].date()}..{F.index[-1].date()} (cost basis (b), w fixed from discovery) ===")
        na_ = F.NA_b.values
        print(f"NoiseArea alone: {fmt(st(na_))}")
        print(f"B&H            : {fmt(st(F['B&H|b'].values))}")
        print(f"NA + w_BH x B&H: {fmt(st(na_ + W_['B&H'] * F['B&H|b'].values))}   (w_BH {W_['B&H']:.3f})")
        print(f"{'variant':16s} {'net $':>8s} {'Sharpe':>6s} {'B&H Sh':>6s} {'comb Sh':>7s} {'NA Sh':>5s} {'NA+BH':>5s} {'cush comb':>9s} {'cush NA':>7s}  V1 V2 V3")
        for v in VAR16:
            m = val_metrics(F, v); p1, p2, p3 = passes(m)
            print(f"{v:16s} {m['net']:8,.0f} {m['sh']:6.2f} {m['shbh']:6.2f} {m['shc']:7.2f} {m['shna']:5.2f} {m['shcbh']:5.2f} {m['cush_c']:9.2f} "
                  f"{m['cush_na']:7.2f}  {'Y' if p1 else '.'}  {'Y' if p2 else '.'}  {'Y' if p3 else '.'}{'   <- candidate' if v == cand else ''}")

    # ------------------------------------------------------------------ BAR FOR THE CANDIDATE (or the best-in-discovery if none)
    show = cand if cand else max((cs, v) for v, r, cs, w in rows if v in VAR16)[1]
    print(f"\n=== ADOPTION BAR: {show}{'' if cand else ' (NOT a candidate: shown for information only)'} ===")
    ok = cand is not None
    for nm, F in (('DB', Fdb), ('NT', Fnt)):
        m = val_metrics(F, show); p1, p2, p3 = passes(m); ok &= p1 and p2 and p3
        print(f"{nm}: V1 net ${m['net']:,.0f} Sharpe {m['sh']:.2f} vs B&H {m['shbh']:.2f} -> {'pass' if p1 else 'FAIL'} | V2 combined {m['shc']:.2f} vs NA "
              f"{m['shna']:.2f} and NA+B&H {m['shcbh']:.2f} -> {'pass' if p2 else 'FAIL'} | V3 cushion/profit {m['cush_c']:.2f} vs NA {m['cush_na']:.2f} -> {'pass' if p3 else 'FAIL'}")
    for nm, J in (('16-year NQ+NT', J_nt), ('16-year NQ+DB', J_db)):
        na_ = J.NA_b.values; comb = na_ + W_[show] * J[f'{show}|b'].values
        c1, c2 = st(comb)['cush'], st(na_)['cush']; ok &= c1 <= c2
        print(f"{nm}: V3 cushion/profit combined {c1:.2f} vs NoiseArea {c2:.2f} -> {'pass' if c1 <= c2 else 'FAIL'}")
    if show.startswith('EW-'):
        nb = [f'{show}-x{s}' for s in SIGS]
    else:
        sname, md = show.split('-'); lst = [f'TS{L}' for L in TS_L] if sname.startswith('TS') else [f'SMA{N}' for N in SMA_N]
        i = lst.index(sname); nb = [f'{lst[j]}-{md}' for j in (i - 1, i + 1) if 0 <= j < len(lst)]
    pl = True
    for v in nb:
        for nm, F in (('DB', Fdb), ('NT', Fnt)):
            if v not in W_: W_[v] = Fd.NA_b.values.std(ddof=1) / Fd[v + '|b'].values.std(ddof=1)
            m = val_metrics(F, v); good = m['net'] > 0 and m['sh'] > m['shbh'] and m['shc'] > m['shna']; pl &= good
            print(f"V4 neighbour {v:16s} {nm}: net ${m['net']:8,.0f} Sharpe {m['sh']:.2f} vs B&H {m['shbh']:.2f}, combined {m['shc']:.2f} vs NA {m['shna']:.2f} -> {'ok' if good else 'FAIL'}")
    ok &= pl
    print(f"V4 plateau -> {'pass' if pl else 'FAIL'}")
    print(f"\nVERDICT: {'PASS - candidate for a separate decision' if ok else 'FAIL - nothing adopted'}")

    # ------------------------------------------------------------------ REPORTED, NEVER SELECTED
    print(f"\n=== REPORTED, NEVER SELECTED ({show}) ===")
    for nm, F in (('DB', Fdb), ('NT', Fnt)):
        for cost, lab in (('a', 'actual costs'), ('d', '1-day execution delay'), ('x3', 'costs x3'), ('g', 'gross')):
            na_ = F['NA_' + ('a' if cost == 'a' else ('g' if cost == 'g' else 'b'))].values
            t_ = F[f'{show}|{cost}'].values; b_ = F[f'B&H|{cost}'].values
            print(f"{nm} {lab:22s}: trend Sharpe {sharpe(t_):.2f} (B&H {sharpe(b_):.2f}), net ${t_.sum()*DOLLAR:,.0f}; combined {sharpe(na_ + W_[show]*t_):.2f} vs NA {sharpe(na_):.2f}")
        t_ = F[f'{show}|b'].values; b_ = F['B&H|b'].values
        al, at, be = alpha(t_, b_)
        print(f"{nm}: alpha of {show} on B&H {al*100:+.2f}% of notional/yr = ${al*DOLLAR:+,.0f}/yr (t {at:.2f}, beta {be:.2f}); block-bootstrap "
              f"P(Sharpe {show} <= B&H) = {block_p(t_, b_):.2f}; matched-vol B&H: ${b_.sum()*t_.std(ddof=1)/b_.std(ddof=1)*DOLLAR:,.0f} net, "
              f"maxDD ${st(b_*t_.std(ddof=1)/b_.std(ddof=1))['mdd']:,.0f} vs trend ${st(t_)['mdd']:,.0f}")
        pos = (Pdb if nm == 'DB' else Pnt)[show]; k = (MDB if nm == 'DB' else MNT).index.get_indexer(F.index)
        p = pos[k]; ch = np.abs(np.diff(np.r_[pos[k[0] - 1] if k[0] > 0 else 0, p]))
        print(f"{nm}: {show} long {np.mean(p > 0)*100:.0f}% / short {np.mean(p < 0)*100:.0f}% / flat {np.mean(p == 0)*100:.0f}% of days, "
              f"{ch.sum()/(len(p)/252):.1f} units traded per year (excl. rolls)")
    for nm, J in (('16-year NQ+NT', J_nt), ('16-year NQ+DB', J_db), ('discovery NQ', Fd), ('validation NT', Fnt), ('validation DB', Fdb)):
        na_ = J.NA_b.values; t_ = J[f'{show}|b'].values; b_ = J['B&H|b'].values
        print(f"\n{nm} ({len(J)} sessions)")
        print(f"   NoiseArea alone          {fmt(st(na_, J.index))}")
        print(f"   {show:12s} alone     {fmt(st(t_, J.index))}")
        print(f"   B&H alone                {fmt(st(b_, J.index))}")
        for lab, s_ in ((f'NA + {W_[show]:.2f} {show}', na_ + W_[show] * t_), (f'NA + {W_["B&H"]:.2f} B&H', na_ + W_['B&H'] * b_),
                        (f'NA + 1.00 {show}', na_ + t_)):
            r = st(s_, J.index)
            print(f"   {lab:24s} {fmt(r)}  (DD {r.get('dd_from')}..{r.get('dd_to')})")
        r0 = st(na_, J.index); print(f"   NoiseArea DD {r0.get('dd_from')}..{r0.get('dd_to')}")
        mo = pd.DataFrame({'na': na_, 't': t_}, index=J.index).groupby(J.index.to_period('M')).sum()
        q1 = na_ <= np.quantile(na_, 0.05); q2 = t_ <= np.quantile(t_, 0.05)
        print(f"   correlation NA vs {show}: daily {np.corrcoef(na_, t_)[0,1]:+.3f}, monthly {mo.na.corr(mo.t):+.3f}; NA vs B&H daily {np.corrcoef(na_, b_)[0,1]:+.3f}; "
              f"mean {show} on NA's worst 5% days ${t_[q1].mean()*DOLLAR*W_[show]:+,.0f} (w-scaled), mean NA on {show}'s worst 5% days ${na_[q2].mean()*DOLLAR:+,.0f}")
        print("   Sharpe of NA + w x " + show + " for w = 0..1: " + ' '.join(f"{w:.1f}:{sharpe(na_ + w*t_):.2f}" for w in np.arange(0, 1.01, 0.1)))
    for nm, F in (('NT', Fnt), ('DB', Fdb)):
        wh = F.NA_b.values.std(ddof=1) / F[f'{show}|b'].values.std(ddof=1)
        print(f"hindsight w from validation {nm}: {wh:.3f} (discovery w {W_[show]:.3f}); combined Sharpe at hindsight w {sharpe(F.NA_b.values + wh*F[f'{show}|b'].values):.2f}")
    print(f"whole-micro ratio for w {W_[show]:.3f}: {micro_ratio(W_[show])}")

    # bootstrap on the 16-year NQ+NT series
    J = J_nt; na_ = J.NA_b.values; series = {'NoiseArea alone': na_, f'NA + w {show}': na_ + W_[show] * J[f'{show}|b'].values,
                                              'NA + w_BH B&H': na_ + W_['B&H'] * J['B&H|b'].values}
    ix = rng.integers(0, len(na_), size=(10000, 250))
    print("\nBootstrap 10,000 synthetic years from the 16-year NQ+NT series (same draws for every series):")
    for lab, s_ in series.items():
        y = s_[ix]; ys = y.sum(1); eq = np.cumsum(y, 1); dds = (np.maximum.accumulate(np.c_[np.zeros(10000), eq], 1) - np.c_[np.zeros(10000), eq]).max(1)
        print(f"   {lab:24s} P(losing year) {np.mean(ys < 0)*100:4.1f}%  median year ${np.median(ys)*DOLLAR:7,.0f}  5th ${np.percentile(ys,5)*DOLLAR:7,.0f}  "
              f"within-year DD 95th ${np.percentile(dds,95)*DOLLAR:6,.0f} 99th ${np.percentile(dds,99)*DOLLAR:6,.0f}  99th DD / median year {np.percentile(dds,99)/np.median(ys):.2f}")
        del y, eq
    # year by year
    yb = pd.DataFrame({'NoiseArea': J.NA_b, show: W_[show] * J[f'{show}|b'], 'B&H (w_BH)': W_['B&H'] * J['B&H|b']})
    yb['NA+trend'] = yb.NoiseArea + yb[show]; yb['NA+B&H'] = yb.NoiseArea + yb['B&H (w_BH)']
    print("\nBy year, 16-year NQ+NT, today's $ per NoiseArea micro (trend and B&H at their discovery w):")
    print((yb.groupby(yb.index.year).sum() * DOLLAR).round(0).astype(int).to_string())

    # gap risk
    print("\n=== OVERNIGHT RISK, 1 micro long at today's notional ($55,750) ===")
    for nm, D in (('NQ 2010-2019', NQ), ('MNQ-DB', MDB), ('MNQ-NT', MNT)):
        x = D.x.dropna(); gap = ((D.O930 - D.C.shift(1)) / D.R.shift(1)).dropna()
        print(f"   {nm}: worst close-to-close day {x.min()*100:+.2f}% = ${x.min()*DOLLAR:+,.0f} ({x.idxmin().date()}); worst 16:00->09:30 gap "
              f"{gap.min()*100:+.2f}% = ${gap.min()*DOLLAR:+,.0f} ({gap.idxmin().date()}); days below -3%: {(x < -0.03).sum()}")
    for nm, F in (('16-year NQ+NT', J_nt),):
        t_ = F[f'{show}|b']; print(f"   {show} (1 unit) worst day {t_.min()*100:+.2f}% = ${t_.min()*DOLLAR:+,.0f} ({t_.idxmin().date()})")

    # ------------------------------------------------------------------ SENSITIVITIES (added after the audit found the expiry-Friday hole; reported, never selected)
    print("\n=== SENSITIVITY 1: Databento roll weekends patched with the cash index (reported, never selected) ===")
    def patch(D):
        Dp = D.copy(); n = 0
        for j in np.flatnonzero(Dp.roll.values > 0):
            if j == 0: continue
            a_, b_ = Dp.index[j - 1], Dp.index[j]
            if a_ in fred.index and b_ in fred.index:
                Dp.iloc[j, Dp.columns.get_loc('x')] = fred.loc[b_] / fred.loc[a_] - 1; n += 1
        return Dp, n
    NQp, nq_n = patch(NQ); MDBp, db_n = patch(MDB)
    res_p, res_u = [], []
    for j in np.flatnonzero(MDB.roll.values > 0):
        if j == 0: continue
        a_, b_ = MDB.index[j - 1], MDB.index[j]
        true = MNT.x[(MNT.index > a_) & (MNT.index <= b_)].sum()
        res_p.append(MDBp.x.iloc[j] - true); res_u.append(MDB.x.iloc[j] - true)
    res_p, res_u = np.array(res_p), np.array(res_u)
    print(f"   patched roll sessions: NQ {nq_n} of {int(NQ.roll.sum())}, MNQ-DB {db_n} of {int(MDB.roll.sum())}")
    print(f"   check against NT (which has the expiry Fridays), {len(res_p)} roll windows 2019-2026: unpatched DB - NT sum {res_u.sum()*100:+.2f}%, "
          f"sd {res_u.std(ddof=1)*100:.2f}%/event | patched DB - NT sum {res_p.sum()*100:+.2f}%, sd {res_p.std(ddof=1)*100:.2f}%/event")
    ret_dp = pd.concat([fr[fr.index <= NQp.index[0]], NQp.x.iloc[1:]]); Iqp = (1 + ret_dp).cumprod()
    histp = ret_dp[ret_dp.index <= NQ_END]
    rp = pd.concat([histp, pd.Series([jdb], index=[MDBp.index[0]]), MDBp.x.iloc[1:]]); Idbp = (1 + rp).cumprod()
    Tqp, _, _ = run(NQp, Iqp, (0.25 * 20 + 2.0) / (20 * NQp.R.values)); Tdbp, _, _ = run(MDBp, Idbp, (0.25 * 2 + 0.5) / (2 * MDBp.R.values))
    Fdp = frame(Tqp, Nq, DISC_END); Fdbp = frame(Tdbp, Ndb)
    nap = Fdp.NA_b.values; shbp = sharpe(Fdp['B&H|b'].values); Wp = {v: nap.std(ddof=1) / Fdp[v + '|b'].values.std(ddof=1) for v in VAR16 + ['B&H']}
    print(f"   discovery (patched NQ): B&H Sharpe {shbp:.3f} (unpatched {sharpe(Fd['B&H|b'].values):.3f}); NA + w_BH B&H {sharpe(nap + Wp['B&H']*Fdp['B&H|b'].values):.2f}")
    print("   " + "  ".join(f"{v} {sharpe(Fdp[v + '|b'].values):.3f}" for v in VAR16))
    el = [v for v in VAR16 if Fdp[v + '|b'].sum() > 0 and sharpe(Fdp[v + '|b'].values) > shbp]
    print(f"   eligible under the patch: {el if el else 'none'}")
    nadb = Fdbp.NA_b.values; bdb = Fdbp['B&H|b'].values
    print(f"   validation DB (patched): B&H Sharpe {sharpe(bdb):.2f} (unpatched {sharpe(Fdb['B&H|b'].values):.2f}); NA+B&H {sharpe(nadb + Wp['B&H']*bdb):.2f}")
    print("   " + "  ".join(f"{v} {sharpe(Fdbp[v + '|b'].values):.2f}/{sharpe(nadb + Wp[v]*Fdbp[v + '|b'].values):.2f}" for v in VAR16) + "   (trend Sharpe / combined Sharpe)")

    print("\n=== SENSITIVITY 2: one-day execution delay across all 16 variants (reported, never selected) ===")
    for nm, F in (('discovery NQ', Fd), ('validation DB', Fdb), ('validation NT', Fnt)):
        dlt = np.array([sharpe(F[v + '|d'].values) - sharpe(F[v + '|b'].values) for v in VAR16])
        print(f"   {nm:14s}: Sharpe change from the delay, mean {dlt.mean():+.3f}, range {dlt.min():+.2f}..{dlt.max():+.2f}, better in {(dlt > 0).sum()} of 16; "
              f"{show} {sharpe(F[show + '|b'].values):.2f} -> {sharpe(F[show + '|d'].values):.2f}; best delayed variant vs B&H "
              f"{max(sharpe(F[v + '|d'].values) for v in VAR16):.2f} vs {sharpe(F['B&H|b'].values):.2f}")
    out = J_nt[['NA_b', f'{show}|b', 'B&H|b']].copy(); out.columns = ['noisearea_b', f'trend_{show}_b', 'bh_b']
    out.to_csv(os.path.join(HERE, 'trend_daily.csv'))


if __name__ == '__main__':
    main()
