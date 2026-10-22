r"""Scaling-schedule test per SCALING_PLAN.txt (pre-registered 2026-10-19).

Run from the research folder:  python scaling.py
Stage 1 rebuilds the LONGHIST inputs into scaling_inputs\ (once), stage 2 rebuilds the 16-year daily series exactly as longhist.py does
and audits it against LONGHIST_RESULTS.txt column (b), stage 3 runs the four sizing rules (historical path + month-block bootstrap).
"""
import os, sys
import numpy as np, pandas as pd
from research_engine import Market, fam_A, WARMUP

HERE = os.path.dirname(os.path.abspath(__file__))
INP = os.path.join(HERE, 'scaling_inputs')
NT8 = os.path.abspath(os.path.join(HERE, '..', '..', '..'))
DBN_NQ = os.path.join(os.path.expanduser('~'), 'OneDrive', 'Desktop', 'Desktop', 'databento_data', 'NQ_ohlcv-1m_2010-2019.dbn.zst')
NT_BARS = os.path.join(NT8, 'agent_exports', 'NoiseArea_bars_MNQ_strategy_Backtest.csv')
NQ_CSV = os.path.join(INP, 'nq_2010-2019_5m.csv')
MNQ_CSV = os.path.join(INP, 'nt_analyzer_bars_to_2026-10-14.csv')
DAILY = os.path.join(INP, 'scaling_daily.csv')

# LONGHIST_RESULTS.txt column (b) and segment facts - the audit targets
TARGET = dict(sessions=4092, trades=3599, net=76947, per_year=4739, maxdd=7906, nq_sessions=2201, mnq_sessions=1891, p_ref=27875)
SEED = 20261019
N_PATHS, N_MONTHS = 10_000, 60
STARTS = (25_000, 50_000, 105_000)


# ---------------------------------------------------------------- stage 1: inputs
def build_inputs():
    os.makedirs(INP, exist_ok=True)
    if not os.path.exists(NQ_CSV):
        import convert
        b, rolls = convert.build([DBN_NQ], NQ_CSV)
        print(f"built {NQ_CSV}: {len(b)} 5-minute bars {b.time.min()} -> {b.time.max()}, {len(rolls)} rolls")
    if not os.path.exists(MNQ_CSV):
        df = pd.read_csv(NT_BARS).rename(columns={'time_et': 'time'})
        t = pd.to_datetime(df.time)
        df = df[t < pd.Timestamp('2026-10-15')]
        df.to_csv(MNQ_CSV, index=False)
        print(f"built {MNQ_CSV}: {len(df)} bars {df.time.iloc[0]} -> {df.time.iloc[-1]}")


# ---------------------------------------------------------------- stage 2: daily series (same code path as longhist.py)
def segment(csv, pv, tick, comm, name):
    mk = Market(csv, pv, name)
    pg, nt = fam_A(mk, vm=1.0, slip=0.0, comm=0.0)
    pn, nt2 = fam_A(mk, vm=1.0, slip=tick, comm=comm)
    assert (nt == nt2).all()
    P = np.array([x.O[0] for x in mk.days])
    df = pd.DataFrame({'gross': np.nan_to_num(pg), 'net': np.nan_to_num(pn), 'nt': nt, 'P': P, 'pv': pv}, index=mk.dates)
    df = df.iloc[WARMUP:]
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    hit = df.index.isin(raw.index); df.loc[hit, 'P'] = raw.reindex(df.index[hit]).values
    print(f"   {name}: raw 09:30 prices on {hit.sum()} of {len(df)} days")
    return df


def daily_series():
    if os.path.exists(DAILY):
        d = pd.read_csv(DAILY, index_col=0, parse_dates=True)
        return d, float(d.attrs_p_ref.iloc[0])
    nq = segment(NQ_CSV, 20.0, 0.25, 2.00, 'NQ')
    mnq = segment(MNQ_CSV, 2.0, 0.25, 0.50, 'MNQ')
    mnq = mnq[mnq.index >= '2019-05-06']
    nq = nq[nq.index <= '2019-05-05']
    p_ref = mnq[mnq.index.year == 2026].P.mean()
    c = 0.5 / p_ref
    for df in (nq, mnq):
        df['r_gross'] = df.gross / (df.pv * df.P)
        df['r_today'] = df.r_gross - df.nt * 2 * c
    d = pd.concat([nq, mnq])
    d['seg'] = np.where(d.index <= '2019-05-05', 'NQ', 'MNQ')
    d['attrs_p_ref'] = p_ref
    d.to_csv(DAILY)
    return d, p_ref


def max_dd(eq):
    peak = np.maximum.accumulate(np.r_[0.0, eq]); dd = peak - np.r_[0.0, eq]
    i = int(dd.argmax()); j = int(np.argmax(np.r_[0.0, eq][:i + 1]))
    return dd.max(), j, i


def audit(d, p_ref):
    N = 2.0 * p_ref
    s = d.r_today.values; eq = np.cumsum(s) * N
    mdd, j, i = max_dd(eq)
    got = dict(sessions=len(d), trades=int(d.nt.sum()), net=s.sum() * N, per_year=s.sum() * N / (len(d) / 252), maxdd=mdd,
               nq_sessions=int((d.seg == 'NQ').sum()), mnq_sessions=int((d.seg == 'MNQ').sum()), p_ref=p_ref)
    print("\n=== AUDIT: fixed 1 micro vs LONGHIST_RESULTS.txt column (b) ===")
    for k in TARGET:
        print(f"   {k:13s} target {TARGET[k]:>10,.0f}   got {got[k]:>12,.2f}   diff {got[k] - TARGET[k]:+,.2f}")
    print(f"   max drawdown {d.index[max(j - 1, 0)].date()} -> {d.index[i - 1].date()}  (target 2010-07-22 -> 2011-05-10)")
    nq_ok = got['nq_sessions'] == TARGET['nq_sessions']
    net_ok = abs(got['net'] / TARGET['net'] - 1) < 0.01
    dd_ok = abs(got['maxdd'] / TARGET['maxdd'] - 1) < 0.02
    ok = nq_ok and net_ok and dd_ok
    print(f"   NQ segment identical: {nq_ok} | net within 1%: {net_ok} | max DD within 2%: {dd_ok}  ->  AUDIT {'PASSED' if ok else 'FAILED'}")
    return ok


# ---------------------------------------------------------------- stage 3: sizing rules
def month_blocks(d, N):
    """Per calendar month: per-micro $ P&L per day and trades per day, padded to the longest month."""
    g = list(d.groupby([d.index.year, d.index.month]))
    L = max(len(x) for _, x in g)
    pnl = np.zeros((len(g), L)); nt = np.zeros((len(g), L)); n_days = np.zeros(len(g), int)
    for k, (_, x) in enumerate(g):
        m = len(x); pnl[k, :m] = x.r_today.values * N; nt[k, :m] = x.nt.values; n_days[k] = m
    return pnl, nt, n_days, [f"{y}-{m:02d}" for (y, m), _ in g]


def contracts(rule, E, N, kelly_lev):
    if rule == 'R0':
        return np.where(E >= 0, 1, 0)
    if rule == 'R1':
        return np.floor(E / (0.21 * N))
    if rule == 'R2':
        return np.floor(E / (0.38 * N))
    if rule == 'R3':
        return np.floor(kelly_lev * E / N)
    raise ValueError(rule)


def simulate(rule, E0, seq_pnl, seq_nt, N, kelly_lev):
    """seq_pnl/seq_nt: (paths, months, days) per-micro $ and trades. Returns dict of path arrays."""
    P, M, D = seq_pnl.shape
    thr = 0.21 * N
    E = np.full(P, float(E0)); peak = E.copy(); mdd_pct = np.zeros(P); mdd_usd = np.zeros(P)
    ruined = np.zeros(P, bool); month_end = np.zeros((P, M)); n_hist = np.zeros((P, M))
    for m in range(M):
        n = contracts(rule, E, N, kelly_lev); n[ruined] = 0; n = np.maximum(n, 0)
        blocks = np.floor(n / 10)
        daily = n[:, None] * seq_pnl[:, m, :] + blocks[:, None] * seq_nt[:, m, :] * 2 * 3.0
        path = E[:, None] + np.cumsum(daily, axis=1)
        below = path < thr
        first = np.where(below.any(axis=1), below.argmax(axis=1), D)
        idx = np.arange(D)[None, :]
        frozen = np.take_along_axis(path, np.minimum(first, D - 1)[:, None], axis=1)
        path = np.where(idx > first[:, None], frozen, path)
        pk = np.maximum(peak[:, None], np.maximum.accumulate(path, axis=1))
        mdd_pct = np.maximum(mdd_pct, ((pk - path) / pk).max(axis=1))
        mdd_usd = np.maximum(mdd_usd, (pk - path).max(axis=1))
        peak = pk[:, -1]; E = path[:, -1]
        ruined |= first < D
        month_end[:, m] = E; n_hist[:, m] = n
    return dict(E=E, mdd_pct=mdd_pct, mdd_usd=mdd_usd, ruined=ruined, month_end=month_end, n=n_hist)


def historical(rule, E0, d, N, kelly_lev):
    pnl, nt, n_days, labels = month_blocks(d, N)
    r = simulate(rule, E0, pnl[None], nt[None], N, kelly_lev)
    # longest time under water in sessions, from the unpadded daily path
    eq = []; E = float(E0); ruined = False; thr = 0.21 * N
    for k in range(len(labels)):
        n = 0 if ruined else max(0, int(contracts(rule, np.array([E]), N, kelly_lev)[0]))
        for t in range(n_days[k]):
            if not ruined:
                E += n * pnl[k, t] + (n // 10) * nt[k, t] * 6.0
                if E < thr: ruined = True
            eq.append(E)
    eq = np.array(eq); peak = np.maximum.accumulate(np.r_[E0, eq])[1:]
    run = best = 0
    for u in eq < peak:
        run = run + 1 if u else 0; best = max(best, run)
    years = len(eq) / 252
    assert abs(eq[-1] - r['E'][0]) < 1e-6, "historical path mismatch between vectorised and loop versions"
    return dict(final=eq[-1], cagr=(eq[-1] / E0) ** (1 / years) - 1 if eq[-1] > 0 else -1, mdd_pct=r['mdd_pct'][0],
                mdd_usd=r['mdd_usd'][0], under=best, ruined=bool(r['ruined'][0]), n_end=int(r['n'][0, -1]))


def main():
    build_inputs()
    d, p_ref = daily_series()
    N = 2.0 * p_ref
    if not audit(d, p_ref):
        print("Audit failed: stopping before any scaling figure, per SCALING_PLAN.txt."); sys.exit(1)

    disc = d[(d.index >= '2010-07-21') & (d.index <= '2018-12-31')].r_today
    mu, var = disc.mean(), disc.var(ddof=1)
    kelly_lev = 0.5 * mu / var
    print(f"\nR3 half-Kelly from 2010-07-21..2018-12-31 ({len(disc)} sessions): mean {mu*1e4:.3f} bp/day, sd {np.sqrt(var)*1e4:.2f} bp/day, "
          f"full Kelly {mu/var:.2f}x notional, half-Kelly {kelly_lev:.2f}x notional = 1 micro per ${N/kelly_lev:,.0f} of equity")
    print(f"Notional per micro N = ${N:,.0f}; survival cushion 0.21N = ${0.21*N:,.0f}; prudent cushion 0.38N = ${0.38*N:,.0f}; ruin below ${0.21*N:,.0f}")

    rules = ('R0', 'R1', 'R2', 'R3')
    names = dict(R0='fixed 1 micro', R1='survival 21%', R2='prudent 38%', R3='half-Kelly')

    print("\n=== 1. HISTORICAL PATH, 16 years 2010-07-21 .. 2026-10-14 (constant 2026 notional) ===")
    hist = {}
    for E0 in STARTS:
        for ru in rules:
            h = historical(ru, E0, d, N, kelly_lev); hist[(ru, E0)] = h
            print(f"   start ${E0:>7,}  {ru} {names[ru]:14s} final ${h['final']:>13,.0f}  CAGR {h['cagr']*100:6.1f}%  maxDD {h['mdd_pct']*100:5.1f}% (${h['mdd_usd']:>11,.0f})  "
                  f"longest under water {h['under']:4d} sessions  ruin {h['ruined']}  micros at end {h['n_end']}")

    print(f"\n=== 2. BOOTSTRAP: {N_PATHS:,} paths x {N_MONTHS} calendar months drawn with replacement, seed {SEED} ===")
    pnl, nt, n_days, labels = month_blocks(d, N)
    rng = np.random.default_rng(SEED)
    draw = rng.integers(0, len(labels), size=(N_PATHS, N_MONTHS))
    seq_pnl, seq_nt = pnl[draw], nt[draw]
    boot = {}
    for E0 in STARTS:
        for ru in rules:
            r = simulate(ru, E0, seq_pnl, seq_nt, N, kelly_lev); boot[(ru, E0)] = r
            me = r['month_end']; m12 = me[:, 11]; m60 = me[:, -1]
            inc12 = me[:, 11] - me[:, 10]; inc60 = me[:, -1] - me[:, -2]
            print(f"   start ${E0:>7,}  {ru} {names[ru]:14s} 12m median ${np.median(m12):>11,.0f} p5 ${np.percentile(m12,5):>10,.0f} | "
                  f"60m median ${np.median(m60):>12,.0f} p5 ${np.percentile(m60,5):>11,.0f} | P(ruin) {r['ruined'].mean()*100:5.2f}%  "
                  f"P(DD>=50%) {(r['mdd_pct']>=0.5).mean()*100:5.2f}% | maxDD med {np.median(r['mdd_pct'])*100:4.1f}% p95 {np.percentile(r['mdd_pct'],95)*100:4.1f}% "
                  f"(${np.median(r['mdd_usd']):,.0f} / ${np.percentile(r['mdd_usd'],95):,.0f}) | month P&L med m12 ${np.median(inc12):,.0f} m60 ${np.median(inc60):,.0f}")

    print("\n=== 3. DECISION (SCALING_PLAN.txt rule) ===")
    verdict = {}
    for E0 in STARTS:
        elig = []
        for ru in rules:
            r, h = boot[(ru, E0)], hist[(ru, E0)]
            ok = r['ruined'].mean() <= 0.01 and (r['mdd_pct'] >= 0.5).mean() <= 0.05 and h['mdd_pct'] < 0.5 and not h['ruined']
            print(f"   start ${E0:>7,}  {ru} eligible {ok}  (P ruin {r['ruined'].mean()*100:.2f}% <= 1%, P DD>=50% {(r['mdd_pct']>=0.5).mean()*100:.2f}% <= 5%, "
                  f"hist maxDD {h['mdd_pct']*100:.1f}% < 50%, hist ruin {h['ruined']})")
            if ok: elig.append(ru)
        base = np.median(boot[('R0', E0)]['month_end'][:, -1])
        cand = [ru for ru in elig if ru != 'R0' and np.median(boot[(ru, E0)]['month_end'][:, -1]) >= 1.10 * base]
        if not cand:
            pick = 'R0'
        else:
            cand.sort(key=lambda ru: -np.median(boot[(ru, E0)]['month_end'][:, -1]))
            pick = cand[0]
            top = np.median(boot[(pick, E0)]['month_end'][:, -1])
            for ru in cand[1:]:  # within 5%: the one holding fewer contracts wins
                if np.median(boot[(ru, E0)]['month_end'][:, -1]) >= 0.95 * top and \
                        np.median(boot[(ru, E0)]['n'][:, -1]) < np.median(boot[(pick, E0)]['n'][:, -1]):
                    pick = ru
        verdict[E0] = pick
        print(f"   start ${E0:>7,}: ADOPTED {pick} ({names[pick]})  - R0 median 60m ${base:,.0f}")
    same = len(set(verdict.values())) == 1
    print(f"   same rule at all three starts: {same} -> " + (f"Section 7 sizing schedule = {verdict[STARTS[0]]} ({names[verdict[STARTS[0]]]})" if same
                                                           else "Section 7 gets the per-equity verdict table"))


if __name__ == '__main__':
    main()
