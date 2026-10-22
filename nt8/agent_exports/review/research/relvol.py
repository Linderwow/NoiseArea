"""Relative-volume filter on break-out entries, per RELVOL_PLAN.txt (pre-registered 2026-10-19).   python relvol.py [databento folder]
Per-bar overlay on the validated rules. With the filter off it must reproduce research_engine.fam_A to the cent on every session
and s1_reference_trades_ntbars_v3.csv trade for trade (checks R1-R4 run first and stop the script on failure).
Inputs (relvol_data.py builds them): nt_analyzer_bars.csv, mnq_db_5m_2019-2026.csv, nq_2010-2019_5m.csv, mnq_db_5m.csv; the
Databento .dbn.zst files are read only for their contract-switch dates (roll windows)."""
import os, sys, warnings
import numpy as np, pandas as pd
from research_engine import Market, fam_A, stats, WARMUP, TICK, NS

warnings.filterwarnings('ignore', category=RuntimeWarning)
DBDIR = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\linde\OneDrive\Desktop\Desktop\databento_data'
MNQ_FILES = ['mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst', 'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst', 'MNQ_ohlcv-1m_2026-2026.dbn.zst']
NQ_FILES = ['NQ_ohlcv-1m_2010-2019.dbn.zst']
CHECKS = tuple(range(600, 931, 30))
THETAS = (0.8, 1.0, 1.25, 1.5, 1.75); ADOPTABLE = (1.0, 1.25, 1.5); SCOPES = ('E', 'ER')
LOOK = 14; NEED = (LOOK + 1) // 2; ROLL_N = 5
D = ('2019-01-01', '2022-12-31')


# ------------------------------------------------------------------------------------------------ data helpers
def switch_dates(files):
    """Dates on which the continuous contract's underlying instrument changes (from the files' symbology mappings)."""
    import databento as dbn
    ent = []
    for f in files:
        for v in dbn.DBNStore.from_file(os.path.join(DBDIR, f)).mappings.values():
            ent += [(pd.Timestamp(e['start_date']).date(), str(e['symbol'])) for e in v]
    ent.sort()
    return [d1 for (d0, s0), (d1, s1) in zip(ent, ent[1:]) if s1 != s0]


def roll_mask(mk, switches):
    """True on the ROLL_N valid sessions before each contract switch that lies inside the file."""
    dates = np.array([x.date for x in mk.days]); m = np.zeros(mk.n, bool)
    for s in switches:
        before = np.flatnonzero(dates < s)
        if len(before) and (dates >= s).any():
            m[before[-ROLL_N:]] = True
    return m


def rv_table(mk, elig):
    """RV[k, slot] = V30(k, slot) / mean of V30 over the previous LOOK eligible sessions that have the slot (>= NEED of them)."""
    V30 = np.full((mk.n, NS), np.nan)
    for k, x in enumerate(mk.days):
        c0 = np.r_[0.0, np.cumsum(x.V)]
        j = np.searchsorted(x.M, x.M - 30, side='right')          # first bar stamped after s - 30 min
        V30[k, x.slot] = c0[1:] - c0[j]
    AVG = np.full((mk.n, NS), np.nan); e = np.flatnonzero(elig)
    for k in range(mk.n):
        prev = e[e < k]
        if len(prev) < LOOK: continue
        blk = V30[prev[-LOOK:]]; cnt = (~np.isnan(blk)).sum(0)
        mu = np.nanmean(blk, 0); mu[cnt < NEED] = np.nan; AVG[k] = mu
    return V30 / AVG


def rv_independent(csv, excluded_dates):
    """Second, independent computation of RV at the check slots (pandas time-window rolling from the raw CSV)."""
    df = pd.read_csv(csv, usecols=['time', 'volume']); df['t'] = pd.to_datetime(df.time)
    df['m'] = df.t.dt.hour * 60 + df.t.dt.minute
    r = df[(df.m > 570) & (df.m <= 960)].sort_values('t').copy(); r['date'] = r.t.dt.date
    g = r.groupby('date').m
    r = r[(g.transform('first') == 575) & (g.transform('size') >= 30)]
    r['v30'] = r.set_index('t').groupby('date').volume.transform(lambda s: s.rolling('30min').sum()).values
    P = r[r.m.isin(CHECKS)].pivot_table(index='date', columns='m', values='v30', aggfunc='first').reindex(columns=list(CHECKS))
    Pe = P[~P.index.isin(excluded_dates)]
    avg = Pe.rolling(LOOK, min_periods=NEED).mean().shift(1); avg.iloc[:LOOK] = np.nan
    return Pe / avg


# ------------------------------------------------------------------------------------------------ engine
def stamp(d, m): return f"{d} {m // 60:02d}:{m % 60:02d}:00"


def engine(mk, RV=None, theta=None, scope='E', nofilt=None, slip=TICK, comm=0.5, log=False):
    """Validated rules per bar + relative-volume filter. theta None = filter off. scope 'E' filters entries from flat, 'ER' also the
    new leg of a reversal (blocked reversal = exit to flat). nofilt[k] True = no filter on session k (roll window)."""
    sig = mk.sigma(LOOK); pv = mk.pv; checks = set(CHECKS)
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int); trades = []; cnt = dict(blocked_entry=0, blocked_rev=0, undefined=0)
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]):
            continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        filt = theta is not None and not (nofilt is not None and nofilt[k])
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; pending = 0; info = None; cur = None
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2               # partial session: flat on the bar before the last
        for i in range(flat_i + 1):
            if pending != 0:                                       # decided at the previous bar's close, filled at this open
                pos = pending; entry = O[i] + slip * pos; p -= comm; n_ += 1; pending = 0
                if log: cur = dict(date=str(x.date), entry_time=stamp(x.date, M[i]), dir=pos, entry=O[i], fill=entry, rv=info[0], kind=info[1], k=k)
            if i == flat_i:
                break
            if M[i] in checks and not np.isnan(sg[i]):
                c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
                if pos == 0:
                    new = 1 if c > ub else (-1 if c < lb else 0)
                elif pos == 1:
                    new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else:
                    new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                rv = RV[k, x.slot[i]] if RV is not None else np.nan
                kind = 'entry' if pos == 0 else 'rev'
                if filt and new != 0 and new != pos and (kind == 'entry' or scope == 'ER'):
                    if np.isnan(rv): cnt['undefined'] += 1
                    if not (rv >= theta):
                        cnt['blocked_' + kind] += 1; new = 0
                if new != pos:
                    if pos != 0:
                        p += (O[i + 1] - slip * pos - entry) * pos * pv - comm
                        if log: cur.update(exit_time=stamp(x.date, M[i + 1]), exit=O[i + 1]); trades.append(cur)
                        pos = 0
                    if new != 0:
                        pending = new; info = (rv, kind)
        if pos != 0:
            p += (C[flat_i] - slip * pos - entry) * pos * pv - comm
            if log: cur.update(exit_time=stamp(x.date, M[flat_i]), exit=C[flat_i]); trades.append(cur)
        pnl[k] = p; nt[k] = n_
    if log:
        t = pd.DataFrame(trades)
        if len(t): t['net'] = (t.exit - slip * t.dir - t.fill) * t.dir * pv - 2 * comm
        return pnl, nt, t, cnt
    return pnl, nt, cnt


# ------------------------------------------------------------------------------------------------ reporting helpers
def pmask(mk, a, b):
    idx = np.arange(mk.n)
    return (idx >= WARMUP) & (mk.dates >= pd.Timestamp(a)) & (mk.dates <= pd.Timestamp(b))


def years(mk, pnl):
    s = pd.Series(np.nan_to_num(pnl)[WARMUP:], index=mk.dates[WARMUP:])
    return s.groupby(s.index.year).sum()


def top_keep(mk, base, var, a, b):
    m = pmask(mk, a, b); bp = np.nan_to_num(base)[m]; vp = np.nan_to_num(var)[m]
    n1 = max(1, int(round(0.01 * len(bp)))); top = np.argsort(-bp, kind='stable')[:n1]
    return vp[top].sum() / bp[top].sum(), n1, bp[top].sum(), vp[top].sum()


def fmt(s): return f"${s['net']:9,.0f} t {s['t']:5.2f} maxDD ${s['mdd']:6,.0f} trades {s['trades']:4d}"


# ------------------------------------------------------------------------------------------------ main
if __name__ == '__main__':
    pd.set_option('display.width', 200)
    print(f"RELVOL test per RELVOL_PLAN.txt. thetas {THETAS}, scopes {SCOPES}, roll window {ROLL_N} sessions (Databento/NQ files only)\n")
    sw_mnq = switch_dates(MNQ_FILES); sw_nq = switch_dates(NQ_FILES)
    SETS = {   # label: (csv, point value, commission/side, switches or None, analysis periods)
        'NT': ('nt_analyzer_bars.csv', 2.0, 0.50, None),
        'DB': ('mnq_db_5m_2019-2026.csv', 2.0, 0.50, sw_mnq),
        'NQ': ('nq_2010-2019_5m.csv', 20.0, 2.00, sw_nq),
    }
    MK = {}; ROLL = {}; RVT = {}
    print("=== CORRECTNESS (before any filtered result) ===")
    for lab, (csv, pv, comm, sw) in SETS.items():
        mk = Market(csv, pv, lab); MK[lab] = mk
        ROLL[lab] = roll_mask(mk, sw) if sw is not None else np.zeros(mk.n, bool)
        RVT[lab] = rv_table(mk, ~ROLL[lab])
        p0, n0 = fam_A(mk, vm=1.0, slip=TICK, comm=comm); p1, n1, t1, c1 = engine(mk, RV=RVT[lab], comm=comm, log=True)
        d = np.abs(np.nan_to_num(p0) - np.nan_to_num(p1)).max()
        print(f"R2 {lab}: {mk.n} sessions {mk.dates[0].date()}..{mk.dates[-1].date()}, roll-window sessions {int(ROLL[lab].sum())}; filter off vs fam_A: "
              f"max |daily diff| ${d:.6f}, entries equal {bool((n0 == n1).all())} ({int(n1.sum())}); trade nets sum to daily P&L: "
              f"{abs(t1.net.sum() - np.nan_to_num(p1).sum()) < 0.005}")
        assert d < 0.005 and (n0 == n1).all() and abs(t1.net.sum() - np.nan_to_num(p1).sum()) < 0.005
        if lab == 'NT':
            ref = pd.read_csv('s1_reference_trades_ntbars_v3.csv'); mine = t1[['date', 'entry_time', 'exit_time', 'dir', 'entry', 'exit']].reset_index(drop=True)
            same = len(ref) == len(mine) and all((ref[c].astype(str) == mine[c].astype(str)).all() for c in ('date', 'entry_time', 'exit_time')) \
                and (ref.dir.values == mine.dir.values).all() and np.abs(ref.entry.values - mine.entry.values).max() < 1e-9 and np.abs(ref.exit.values - mine.exit.values).max() < 1e-9
            net_ref = ((ref.exit - ref.entry) * ref.dir * 2 - 2).sum(); net_me = t1.net.sum()
            print(f"R1 NT: filter-off trade list vs s1_reference_trades_ntbars_v3.csv: {len(mine)} vs {len(ref)} trades, identical trade for trade: {same}; "
                  f"net at 1 tick + $0.50/side ${net_me:,.2f} (reference ${net_ref:,.2f})")
            assert same and abs(net_me - net_ref) < 0.005
        # R4: independent RV
        excl = set(np.array([x.date for x in mk.days])[ROLL[lab]])
        ind = rv_independent(csv, excl); mism = 0; worst = 0.0; ncmp = 0
        dates = [x.date for x in mk.days]
        for k, dte in enumerate(dates):
            if ROLL[lab][k] or dte not in ind.index: continue
            row = ind.loc[dte]
            for m_ in CHECKS:
                a = RVT[lab][k, (m_ - 575) // 5]; b = row[m_]
                if np.isnan(a) != np.isnan(b): mism += 1
                elif not np.isnan(a): ncmp += 1; worst = max(worst, abs(a - b) / max(abs(b), 1e-12))
        print(f"R4 {lab}: RV engine vs independent pandas computation at {ncmp} defined checks: max relative diff {worst:.2e}, defined/undefined mismatches {mism}")
        assert mism == 0 and worst < 1e-9
    # R3: data identity with the earlier tests
    mk2 = Market('mnq_db_5m.csv', 2.0, 'DB2'); end2 = str(mk2.dates[-1].date())
    p, n = fam_A(mk2, vm=1.0, slip=TICK, comm=0.5); s_d, s_v = stats(mk2, p, n, *D), stats(mk2, p, n, '2023-01-01', end2)
    print(f"R3 DB two-file build (as every earlier test): disc ${s_d['net']:,.0f} t {s_d['t']:.2f} (earlier $9,737 t 2.32) | val to {end2} ${s_v['net']:,.0f} t {s_v['t']:.2f} (earlier $11,414 t 2.10)")
    mk = MK['NT']; endnt = str(mk.dates[-1].date()); p, n = fam_A(mk, vm=1.0, slip=TICK, comm=0.5)
    s_d, s_v = stats(mk, p, n, *D), stats(mk, p, n, '2023-01-01', endnt)
    print(f"R3 NT: disc ${s_d['net']:,.0f} t {s_d['t']:.2f} (earlier $9,640 t 2.30) | val to {endnt} ${s_v['net']:,.0f} t {s_v['t']:.2f} (earlier $14,230 t 2.25)")
    mk = MK['NQ']; p, n = fam_A(mk, vm=1.0, slip=TICK, comm=2.0); s_f = stats(mk, p, n, '2010-01-01', '2019-05-05')
    print(f"R3 NQ: ${s_f['net']:,.0f} t {s_f['t']:.2f} trades {s_f['trades']} (earlier $39,532 t 1.94, 1,942 trades)")
    del mk2

    # ------------------------------------------------------------------ mechanism (context only)
    print("\n=== MECHANISM (context, never a pass rule): baseline trades by RV at their decision check, roll-window sessions left out ===")
    BASE = {}
    for lab, (csv, pv, comm, sw) in SETS.items():
        mk = MK[lab]; pb, nb_, tb, _ = engine(mk, RV=RVT[lab], comm=comm, log=True); BASE[lab] = (pb, nb_)
        tb = tb[(tb.k >= WARMUP) & ~ROLL[lab][tb.k.values]].copy()
        tb['bucket'] = pd.cut(tb.rv, [-np.inf, 0.8, 1.0, 1.25, 1.5, np.inf], right=False, labels=['<0.8', '0.8-1', '1-1.25', '1.25-1.5', '>=1.5'])
        pers = (('2010-2019', '2010-01-01', '2019-05-05'),) if lab == 'NQ' else (('disc 2019-22', *D), ('val 2023-26', '2023-01-01', '2026-12-31'))
        for nm, a, b in pers:
            q = tb[(tb.date >= a) & (tb.date <= b)]
            g = q.groupby('bucket', observed=False).net.agg(['count', 'mean', 'sum'])
            print(f"  {lab} {nm}: " + " | ".join(f"{ix} n={int(r['count'])} ${r['mean']:+.1f}/tr" for ix, r in g.iterrows())
                  + f" | median RV {q.rv.median():.2f}; entries from flat {int((q.kind == 'entry').sum())}, reversal legs {int((q.kind == 'rev').sum())}")

    # ------------------------------------------------------------------ grid
    print("\n=== GRID (filter on; exits unchanged) ===")
    R = {}
    for lab, (csv, pv, comm, sw) in SETS.items():
        mk = MK[lab]; end = str(mk.dates[-1].date()); pb, nb_ = BASE[lab]
        if lab == 'NQ': pers = {'full': ('2010-01-01', '2019-05-05')}
        else: pers = {'disc': D, 'val': ('2023-01-01', end)}
        yb = years(mk, pb)
        print(f"--- {lab} ({csv}, 1 tick + ${comm:.2f}/side) baseline: " + " | ".join(f"{k_} {fmt(stats(mk, pb, nb_, *v))}" for k_, v in pers.items()))
        print(f"    baseline by year: {yb.round(0).astype(int).to_dict()}")
        R[(lab, 'base')] = dict(pnl=pb, nt=nb_, **{k_: stats(mk, pb, nb_, *v) for k_, v in pers.items()})
        for sc in SCOPES:
            for th in THETAS:
                p, n, c = engine(mk, RV=RVT[lab], theta=th, scope=sc, nofilt=ROLL[lab], comm=comm)
                st = {k_: stats(mk, p, n, *v) for k_, v in pers.items()}
                yv = years(mk, p); better = int((yv.reindex(yb.index).fillna(0) > yb + 1e-9).sum())
                tk = top_keep(mk, pb, p, *(pers['full'] if lab == 'NQ' else pers['val']))
                R[(lab, sc, th)] = dict(pnl=p, nt=n, cnt=c, better_years=better, n_years=len(yb), top=tk, **st)
                print(f"    {sc:2s} theta {th:4.2f}: " + " | ".join(f"{k_} {fmt(st[k_])}" for k_ in pers)
                      + f" | blocked entries {c['blocked_entry']} reversals {c['blocked_rev']} undefined {c['undefined']} | better years {better}/{len(yb)}"
                      + f" | top-1% days kept {tk[0]*100:5.1f}% (n={tk[1]})")

    # ------------------------------------------------------------------ selection and verdict
    print("\n=== SELECTION (Databento discovery t-stat, adoptable thetas only) ===")
    cands = sorted(((R[('DB', sc, th)]['disc']['t'], sc, th) for sc in SCOPES for th in ADOPTABLE), reverse=True)
    for t_, sc, th in cands: print(f"    {sc} theta {th}: DB disc t {t_:.2f} net ${R[('DB', sc, th)]['disc']['net']:,.0f}")
    _, SC, TH = cands[0]; key = lambda lab: (lab, SC, TH)
    print(f"CANDIDATE: scope {SC} theta {TH}")
    b = {lab: R[(lab, 'base')] for lab in SETS}; c = {lab: R[key(lab)] for lab in SETS}
    beats = lambda x, y: x['net'] > y['net'] and x['t'] > y['t']
    chk = {}
    chk['1 discovery DB and NT'] = beats(c['DB']['disc'], b['DB']['disc']) and beats(c['NT']['disc'], b['NT']['disc'])
    chk['2 validation NT and DB, t gain >= 0.3'] = all(beats(c[l]['val'], b[l]['val']) and c[l]['val']['t'] - b[l]['val']['t'] >= 0.3 for l in ('NT', 'DB'))
    i = THETAS.index(TH); nbr = [THETAS[i - 1], THETAS[i + 1]]
    chk['3 plateau ' + str(nbr)] = all(R[(l, SC, th)]['val']['net'] > b[l]['val']['net'] for l in ('NT', 'DB') for th in nbr)
    chk['4 NQ 2010-2019'] = beats(c['NQ']['full'], b['NQ']['full'])
    chk['5 years (NT>=5/8, DB>=5/8, NQ>=6/10)'] = c['NT']['better_years'] >= 5 and c['DB']['better_years'] >= 5 and c['NQ']['better_years'] >= 6
    chk['6 top-1% days kept >= 90% (NT val, DB val, NQ)'] = all(c[l]['top'][0] >= 0.90 for l in ('NT', 'DB', 'NQ'))
    for l in ('NT', 'DB'):
        print(f"    {l}: disc {fmt(c[l]['disc'])} vs base {fmt(b[l]['disc'])} | val {fmt(c[l]['val'])} vs base {fmt(b[l]['val'])} (t gain {c[l]['val']['t'] - b[l]['val']['t']:+.2f})")
        print(f"        neighbours val net: " + ", ".join(f"theta {th} ${R[(l, SC, th)]['val']['net']:,.0f}" for th in nbr) + f" vs base ${b[l]['val']['net']:,.0f}")
    print(f"    NQ: {fmt(c['NQ']['full'])} vs base {fmt(b['NQ']['full'])}")
    for l in SETS:
        tk = c[l]['top']; print(f"    {l}: better years {c[l]['better_years']}/{c[l]['n_years']}; top-1% days ({tk[1]}): baseline ${tk[2]:,.0f}, candidate ${tk[3]:,.0f} = {tk[0]*100:.1f}% kept")
        yb = years(MK[l], b[l]['pnl']); yc = years(MK[l], c[l]['pnl'])
        print(f"        by year candidate - baseline: {(yc - yb).round(0).astype(int).to_dict()}")
    for k_, v in chk.items(): print(f"    [{'PASS' if v else 'fail'}] {k_}")
    print(f"VERDICT: {'ADOPTION CANDIDATE (all six conditions pass)' if all(chk.values()) else 'NOT ADOPTED (fails ' + ', '.join(k_.split()[0] for k_, v in chk.items() if not v) + ')'}")

    # ------------------------------------------------------------------ reported-only sensitivities
    print("\n=== REPORTED ONLY ===")
    print("2 ticks per side (baseline vs candidate):")
    for lab, (csv, pv, comm, sw) in SETS.items():
        mk = MK[lab]; end = str(mk.dates[-1].date()); per = ('2010-01-01', '2019-05-05') if lab == 'NQ' else ('2023-01-01', end)
        p0, n0, _ = engine(mk, RV=RVT[lab], slip=2 * TICK, comm=comm); p1, n1, _ = engine(mk, RV=RVT[lab], theta=TH, scope=SC, nofilt=ROLL[lab], slip=2 * TICK, comm=comm)
        print(f"    {lab} {'full' if lab == 'NQ' else 'val'}: baseline {fmt(stats(mk, p0, n0, *per))} | candidate {fmt(stats(mk, p1, n1, *per))}")
    print("NT with the Databento roll windows applied on the same dates (excluded from averages, unfiltered), all adoptable variants:")
    mk = MK['NT']; dbroll = set(np.array([x.date for x in MK['DB'].days])[ROLL['DB']]); nr = np.array([x.date in dbroll for x in mk.days])
    rv_nr = rv_table(mk, ~nr); endnt = str(mk.dates[-1].date())
    for sc in SCOPES:
        for th in ADOPTABLE:
            p, n, _ = engine(mk, RV=rv_nr, theta=th, scope=sc, nofilt=nr)
            print(f"    {sc} theta {th}: disc {fmt(stats(mk, p, n, *D))} | val {fmt(stats(mk, p, n, '2023-01-01', endnt))}")
