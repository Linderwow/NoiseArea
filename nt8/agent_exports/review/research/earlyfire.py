r"""EARLY FIRE: decide and send the 10:00 order just BEFORE 10:00:00 (EARLYFIRE_PLAN.txt, pre-registered 2026-10-22 13:35 ET,
amendment A1 13:37 ET). Research only - nothing here touches NinjaTrader or the strategy.

Usage (from this folder, agent_exports\review\research):
    python earlyfire.py      -> earlyfire_output.txt, earlyfire_days.csv (491 Databento entry days), earlyfire_ntyear.csv (NT tick year)

Inputs (read-only):
  A  D:\databento_archive\mnq_trades_1000_windows_2019-2026\chunk_*.parquet via limit_test.load() (491 10:00 entry windows)
  B  D:\nt_ticks\MNQ\<MM-YY>\YYYY-MM-DD.parquet (decoded NinjaTrader ticks; sub_ms / artifact rows dropped) and
     ..\..\NoiseArea_checks_MNQ_strategy_Sim101.csv (the strategy's own 10:00 decisions and bands, back-adjusted series)
  C  nt_analyzer_bars.csv + s1_reference_trades_ntbars_v3.csv (bands for 2019-2026, secondary missed-entry count)
Rules: T = 10:00:00.000 ET. BASELINE = limit_test.py (decide on the last trade before T, arrive first trade at/after T + latency,
fill at the touch). EARLY = decide on the last trade before T - delta with the same bands, arrive T - delta + latency, fill at the
touch. Cost in ticks/side vs R (first trade at/after T = the backtest's fill), positive = worse; saving = baseline - early.
"""
import os, sys, glob
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd, pyarrow.parquet as pq
from limit_test import load, evaluate, T_ns
from research_engine import Market, fam_A

CHK = set(range(600, 931, 30)); TICK = 0.25; MS = 1_000_000; S_NS = 1_000_000_000; LATS = (25, 75, 150); PRIMARY = 75; DELTAS = (250, 1000)
NBOOT = 10000; SEED = 20261022; PV = 2.0; COMM = 0.5; SLIP = TICK; DOLLAR_TICK = 0.50
CHECKS_CSV = os.path.normpath(os.path.join(HERE, '..', '..', 'NoiseArea_checks_MNQ_strategy_Sim101.csv'))
NTDIR = r'D:\nt_ticks\MNQ'
NCD_OFF = {'12-25': 1063.0, '03-26': 809.75, '06-26': 595.5, '09-26': 299.75, '12-26': 0.0}   # NCD_READER_RESULTS.txt (b)
LIMIT_75 = 2.60; LIMIT_25 = 2.03; LIMIT_150 = 6.20                                              # LIMIT_RESULTS.txt
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def boot(x, seed=SEED, q=(2.5, 97.5)):
    x = np.asarray(x, float); x = x[np.isfinite(x)]
    if len(x) == 0: return np.nan, np.nan, np.nan, 0
    rng = np.random.default_rng(seed); m = x[rng.integers(0, len(x), (NBOOT, len(x)))].mean(1)
    return x.mean(), np.percentile(m, q[0]), np.percentile(m, q[1]), len(x)


def make_touch(ts, px, sd):
    known = sd != 0; kts, kpx, ksd = ts[known], px[known], sd[known]
    def touch(t):
        k = int(np.searchsorted(kts, t, 'right')) - 1
        if k < 0: return np.nan, np.nan
        p, x = kpx[k], ksd[k]
        return (p - TICK, p) if x > 0 else (p, p + TICK)
    return touch


# ============================================================================================ PART A: cost on 491 Databento days
def part_a():
    w, tp = load(); sdmap = {'B': 1, 'A': -1}; rows = []; samples = {}
    for i, r in w.iterrows():
        g = tp[tp.win == i].sort_values('ts', kind='mergesort')
        if not len(g) or not np.isfinite(r.dir): continue
        ts = pd.to_datetime(g.ts).values.astype('datetime64[ns]').astype(np.int64)
        px = g.price.values.astype(float); sd = g.side.map(sdmap).fillna(0).values.astype(int)
        T = T_ns(r.fill_open); s = int(r.dir)
        ev = evaluate(ts, px, sd, T, s)                     # limit_test's own baseline, for the re-derivation check
        if ev is None: continue
        touch = make_touch(ts, px, sd)
        mkt = lambda t: touch(t)[1] if s > 0 else touch(t)[0]
        i0 = int(np.searchsorted(ts, T, 'left')); t0 = ts[i0]; C = px[i0 - 1]; R = px[i0]
        cost = lambda f: (f - R) * s / TICK
        o = dict(date=r.fill_open.normalize(), dir=s, C=C, R=R, t0_ms=(t0 - T) / MS, ref_entry=r.entry, iid=int(g.iid.iloc[0]), win=i)
        for lat in LATS:
            o[f'base_{lat}'] = cost(mkt(t0 + lat * MS)); o[f'lt_{lat}'] = ev[f'mkt_{lat}']
        for d in DELTAS:
            Td = T - d * MS; j = int(np.searchsorted(ts, Td, 'left')) - 1
            assert j >= 0 and ts[j] >= T - 60 * S_NS, f"no trade before T-{d}ms on {r.fill_open}"
            o[f'Cp_{d}'] = px[j]; o[f'Cp_age_ms_{d}'] = (Td - ts[j]) / MS
            for lat in LATS:
                ta = Td + lat * MS; assert ta < T
                o[f'early_{d}_{lat}'] = cost(mkt(ta))
            k = int(np.searchsorted(ts, Td, 'left')); o[f'Ck_{d}'] = px[k]; o[f'tk_ms_{d}'] = (ts[k] - T) / MS   # tick-triggered variant
            for lat in LATS:
                o[f'tick_{d}_{lat}'] = cost(mkt(ts[k] + lat * MS))
        rows.append(o); samples[i] = (ts, px, sd, g)
    E = pd.DataFrame(rows)
    for lat in LATS:
        for d in DELTAS:
            E[f'save_{d}_{lat}'] = E[f'base_{lat}'] - E[f'early_{d}_{lat}']
            E[f'savetick_{d}_{lat}'] = E[f'base_{lat}'] - E[f'tick_{d}_{lat}']
    return E, samples


def independent_check(E, samples):
    """Pandas re-computation of baseline and early costs at 75 ms (own touch lookup, no shared helper)."""
    diffs = []
    for _, r in E.iterrows():
        ts, px, sd, g = samples[r.win]
        df = pd.DataFrame(dict(ts=ts, px=px, sd=sd)); kn = df[df.sd != 0]
        T = T_ns(r.date + pd.Timedelta(hours=10)); s = r.dir
        R = df.px[df.ts >= T].iloc[0]; t0 = df.ts[df.ts >= T].iloc[0]
        def fill(t):
            last = kn[kn.ts <= t].iloc[-1]
            ask = last.px if last.sd > 0 else last.px + TICK; bid = last.px - TICK if last.sd > 0 else last.px
            return ask if s > 0 else bid
        b = (fill(t0 + 75 * MS) - R) * s / TICK
        e = [(fill(T - d * MS + 75 * MS) - R) * s / TICK for d in DELTAS]
        diffs.append(max(abs(b - r.base_75), *[abs(x - r[f'early_{d}_75']) for x, d in zip(e, DELTAS)]))
    return np.array(diffs)


# ============================================================================================ rule simulator (checks rows)
def rule(pos, p, ub, lb, vw):
    if pos == 0: return 1 if p > ub else (-1 if p < lb else 0)
    if pos == 1: return -1 if p < lb else (0 if p < max(ub, vw) else 1)
    return 1 if p > ub else (0 if p > min(lb, vw) else -1)


def path(rows, force10=None, fills=None, flat_px=None):
    """rows: checks rows of one day in time order (price, upper, lower, vwap). force10 overrides the 10:00 decision.
    fills[i] = fill price for a change decided at row i (next bar's open). Returns positions after each row and P&L ($/micro)."""
    pos = 0; entry = 0.0; pnl = 0.0; poss = []
    for i, r in enumerate(rows.itertuples()):
        new = force10 if (i == 0 and force10 is not None) else rule(pos, r.price, r.upper, r.lower, r.vwap)
        if new != pos and fills is not None:
            f = fills[i]
            if pos != 0: pnl += (f - SLIP * pos - entry) * pos * PV - COMM
            if new != 0: entry = f + SLIP * new; pnl -= COMM
        pos = new; poss.append(pos)
    if fills is not None and pos != 0: pnl += (flat_px - SLIP * pos - entry) * pos * PV - COMM
    return poss, pnl


# ============================================================================================ PART B: decision flips, NT tick year
def nt_files():
    by = {}
    for f in sorted(glob.glob(os.path.join(NTDIR, '*', '*.parquet'))):
        by.setdefault(os.path.basename(f)[:10], []).append((os.path.basename(os.path.dirname(f)), f))
    return by


def read_nt(f, a, b, cols=('time', 'price', 'bid', 'ask', 'sub_ms', 'artifact')):
    t = pq.read_table(f, columns=list(cols), filters=[('time', '>=', a), ('time', '<', b)]).to_pandas()
    t = t[~t.sub_ms & ~t.artifact]
    return t.time.values.astype('datetime64[ns]').astype(np.int64), t


def et_utc(date, hhmmss):
    return pd.Timestamp(f'{date} {hhmmss}').tz_localize('America/New_York').tz_convert('UTC')


def part_b(E):
    ck = pd.read_csv(CHECKS_CSV); ck10 = ck[(ck.time_et == '10:00') & (ck.pos_before == 0)].set_index('date')
    by = nt_files(); dates = sorted(set(by) & set(ck10.index))
    pr(f"\nPART B - NT tick year: tick-file dates {len(by)}, checks 10:00 rows from flat {len(ck10)} ({ck10.index.min()}..{ck10.index.max()}), "
       f"both {len(dates)}; tick dates without a checks row: {sorted(set(by) - set(ck10.index))}")
    # positions-only simulator check on EVERY checks day (A1-3)
    bad = []; gaps = []
    for d, g in ck.groupby('date'):
        g = g.sort_values('time_et'); poss, _ = path(g)
        if list(g.pos_after.values) != poss: bad.append(d)
        if not (g.pos_before.values[1:] == g.pos_after.values[:-1]).all(): gaps.append(d)
    pr(f"   rule simulator vs checks pos_after (positions, every row): {ck.date.nunique() - len(bad)}/{ck.date.nunique()} days identical"
       + (f"; differing: {bad}" if bad else '') + f"; days where pos_before != previous pos_after: {gaps if gaps else 0}")
    raw = []
    for d in dates:
        T = et_utc(d, '10:00:00'); Tn = T.value
        for con, f in by[d]:
            ts, t = read_nt(f, T - pd.Timedelta(seconds=60), T + pd.Timedelta(seconds=1))
            px = t.price.values; bid = t.bid.values; ask = t.ask.values
            if not len(ts) or ts[0] >= Tn: continue
            o = dict(date=d, con=con, n_win=len(ts))
            j = int(np.searchsorted(ts, Tn, 'left')) - 1; o['C_nt'] = px[j]
            k = int(np.searchsorted(ts, Tn, 'left')); o['R_nt'] = px[k] if k < len(ts) else np.nan
            for dl in DELTAS:
                jj = int(np.searchsorted(ts, Tn - dl * MS, 'left')) - 1; o[f'Cp_nt_{dl}'] = px[jj] if jj >= 0 else np.nan
                ja = int(np.searchsorted(ts, Tn - dl * MS + PRIMARY * MS, 'right')) - 1     # quote at the early arrival (A1-2)
                o[f'qbid_{dl}'] = bid[ja]; o[f'qask_{dl}'] = ask[ja]
            m2 = (ts >= Tn - 2 * S_NS) & (ts < Tn); sp = (ask[m2] - bid[m2]) / TICK
            o['n_pre2s'] = int(m2.sum()); o['n_sp1'] = int((np.abs(sp - 1) < 1e-9).sum()); o['sp_mean'] = sp.mean() if len(sp) else np.nan
            o['sp_le2'] = int((sp <= 2 + 1e-9).sum())
            raw.append(o)
    B = pd.DataFrame(raw)
    B = B.join(ck10[['price', 'upper', 'lower', 'vwap', 'pos_after']], on='date')
    B['off'] = (B.price - B.C_nt).round(2)
    single = B[B.groupby('date').con.transform('size') == 1]
    modal = single.groupby('con').off.agg(lambda x: x.mode().iloc[0]).to_dict()
    pr("   back-adjustment offset per contract (checks price - last NT print before 10:00:00): "
       + '; '.join(f"{c} modal {modal[c]:g} (NCD {NCD_OFF[c]:g}) exact on {(single[single.con == c].off == modal[c]).sum()}/{(single.con == c).sum()} days"
                   for c in sorted(modal, key=lambda c: (c[3:], c[:2]))))
    for c in modal: assert abs(modal[c] - NCD_OFF[c]) < 1e-9, f"offset {c} {modal[c]} != NCD {NCD_OFF[c]}"
    B['mod_off'] = B.con.map(modal); B['match'] = (B.C_nt + B.mod_off - B.price).abs() < 1e-9
    dup = B[B.groupby('date').con.transform('size') > 1]
    keep = []
    for d, g in B.groupby('date'):
        if len(g) == 1: keep.append(g.index[0]); continue
        m = g[g.match]; pr(f"   roll-edge {d}: folders {g.con.tolist()}, offsets {g.off.tolist()} -> using {m.con.tolist()}")
        if len(m) == 1: keep.append(m.index[0])
    B = B.loc[keep].reset_index(drop=True)
    nonmodal = B[~B.match]
    pr(f"   days whose own offset differs from the contract's modal offset: {len(nonmodal)}"
       + ''.join(f"\n      {r.date} {r.con} off {r.off:g} (modal {r.mod_off:g}): checks price {r.price} vs C_nt+modal {r.C_nt + r.mod_off}" for r in nonmodal.itertuples()))
    B['up_nt'] = B.upper - B.mod_off; B['lo_nt'] = B.lower - B.mod_off
    side = lambda p, u, l: np.where(p > u, 1, np.where(p < l, -1, 0))
    B['real_recomp'] = side(B.C_nt, B.up_nt, B.lo_nt)
    B['usable'] = B.real_recomp == B.pos_after
    pr(f"   real 10:00 decision recomputed from the ticks vs checks pos_after: {int(B.usable.sum())}/{len(B)} days agree"
       + ''.join(f"\n      NOT usable {r.date}: C_nt {r.C_nt} up {r.up_nt:.2f} lo {r.lo_nt:.2f} recomputed {r.real_recomp:+d} checks {r.pos_after:+d}"
                 for r in B[~B.usable].itertuples()))
    for dl in DELTAS:
        B[f'early_{dl}'] = side(B[f'Cp_nt_{dl}'], B.up_nt, B.lo_nt)
        e = B[f'early_{dl}']; s = B.pos_after
        B[f'cls_{dl}'] = np.select([e == s, (s != 0) & (e == 0), (s == 0) & (e != 0), (s != 0) & (e == -s)], ['agree', 'missed', 'false', 'side'], 'x')
    # P&L consequence of every flip (both deltas)
    flipdays = sorted(set(B[B.usable & ((B.cls_250 != 'agree') | (B.cls_1000 != 'agree'))].date))
    cons = {}
    for d in flipdays:
        r = B[B.date == d].iloc[0]; f = by[d][[c for c, _ in by[d]].index(r.con)][1]
        rows = ck[ck.date == d].sort_values('time_et').reset_index(drop=True)
        ts, t = read_nt(f, et_utc(d, '09:59:00'), et_utc(d, '16:00:01'), cols=('time', 'price', 'sub_ms', 'artifact')); px = t.price.values
        fills = []
        for hm in rows.time_et:
            k = int(np.searchsorted(ts, et_utc(d, hm + ':00').value, 'left')); fills.append(px[k] if k < len(ts) else np.nan)
        last_bar_end = (ts[ts <= et_utc(d, '16:00:00').value].max() // (300 * S_NS) + 1) * 300 * S_NS
        cut = et_utc(d, '16:00:00').value if last_bar_end >= et_utc(d, '16:00:00').value else last_bar_end - 300 * S_NS
        flat_px = px[int(np.searchsorted(ts, cut, 'left')) - 1]
        rp, rpnl = path(rows, None, fills, flat_px)
        ok = rp == list(rows.pos_after.values) and np.isfinite(fills).all()
        for dl in DELTAS:
            if r[f'cls_{dl}'] == 'agree': continue
            ep, epnl = path(rows, int(r[f'early_{dl}']), fills, flat_px)
            cons[(d, dl)] = dict(real_pnl=rpnl if ok else np.nan, early_pnl=epnl if ok else np.nan, cons=(epnl - rpnl) if ok else np.nan,
                                 real_path=''.join('+' if x > 0 else '-' if x < 0 else '0' for x in rp),
                                 early_path=''.join('+' if x > 0 else '-' if x < 0 else '0' for x in ep), sim_ok=ok, flat_px=flat_px)
    return B, cons


# ============================================================================================ PART C: bands 2019-2026 (secondary)
def engine_day(mk, k, sig, force10=None):
    """relvol.engine (filter off) for one session, with an optional forced 10:00 decision. Returns pnl, entries, 10:00 info."""
    x = mk.days[k]; O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
    hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
    pos = 0; entry = 0.0; p = 0.0; pending = 0; ent = []; info = None
    flat_i = nb - 1 if M[-1] >= 960 else nb - 2
    for i in range(flat_i + 1):
        if pending != 0:
            pos = pending; entry = O[i] + SLIP * pos; p -= COMM; ent.append((int(M[i]), pos)); pending = 0
        if i == flat_i: break
        if int(M[i]) in CHK and not np.isnan(sg[i]):
            c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i]); new = rule(pos, c, ub, lb, vw[i])
            if M[i] == 600:
                info = (c, ub, lb, new)
                if force10 is not None: new = force10
            if new != pos:
                if pos != 0: p += (O[i + 1] - SLIP * pos - entry) * pos * mk.pv - COMM; pos = 0
                if new != 0: pending = new
    if pos != 0: p += (C[flat_i] - SLIP * pos - entry) * pos * mk.pv - COMM
    return p, ent, info


def part_c(E):
    mk = Market(os.path.join(HERE, 'nt_analyzer_bars.csv'), PV, 'NT'); sig = mk.sigma(14)
    p0, n0 = fam_A(mk, vm=1.0, slip=TICK, comm=COMM)
    info = {}; mx = 0.0; e10 = {}
    for k in range(mk.n):
        if k < 14 or np.isnan(mk.pc[k]): continue
        p, ent, inf = engine_day(mk, k, sig); mx = max(mx, abs(p - np.nan_to_num(p0[k])))
        info[pd.Timestamp(mk.days[k].date)] = (k, inf)
        for m_, s_ in ent:
            if m_ == 605: e10[pd.Timestamp(mk.days[k].date)] = s_
    ref = pd.read_csv(os.path.join(HERE, 's1_reference_trades_ntbars_v3.csv'), parse_dates=['date', 'entry_time'])
    r10 = ref[ref.entry_time.dt.strftime('%H:%M') == '10:05']; r10d = dict(zip(r10.date, r10.dir))
    same = r10d == e10
    pr(f"\nPART C (secondary) - bands 2019-2026 from nt_analyzer_bars.csv: day engine vs fam_A max |daily diff| ${mx:.6f}; 10:00 entries "
       f"{len(e10)} vs reference {len(r10d)}, identical date and side: {same}")
    assert mx < 0.005 and same
    rows = []
    for _, r in E.iterrows():
        k, (c10, ub, lb, new) = info[r.date]; s = r.dir; assert new == s
        o = dict(date=r.date, margin_ticks=((c10 - (ub if s > 0 else lb)) * s) / TICK, shift=c10 - r.C)
        for dl in DELTAS:
            cm = r[f'Cp_{dl}'] + (c10 - r.C); e = 1 if cm > ub else (-1 if cm < lb else 0)
            o[f'c_early_{dl}'] = e
            if e != s:
                p_real, _, _ = engine_day(mk, k, sig); p_e, _, _ = engine_day(mk, k, sig, force10=e)
                o[f'c_cons_{dl}'] = p_e - p_real
        rows.append(o)
    return pd.DataFrame(rows)


# ============================================================================================ main
def main():
    pr(f"EARLY FIRE TEST (EARLYFIRE_PLAN.txt, pre-registered 2026-10-22 13:35 ET, amendment A1 13:37 ET). deltas {DELTAS} ms, latencies "
       f"{LATS} ms (primary {PRIMARY}); bootstrap {NBOOT} seed {SEED}; trial count 2 (the two deltas)")
    E, samples = part_a()
    pr(f"\nPART A - cost on the Databento 10:00 entry days: {len(E)} days evaluated (expected 491)"); assert len(E) == 491
    pr(f"   decision trade age at T-250ms median {E.Cp_age_ms_250.median():.0f} ms (max {E.Cp_age_ms_250.max():.0f}); at T-1s median "
       f"{E.Cp_age_ms_1000.median():.0f} ms (max {E.Cp_age_ms_1000.max():.0f}); all early arrivals before T: asserted")
    lt = pd.read_csv(os.path.join(HERE, 'limit_entries.csv'))
    pr("   BASELINE re-derivation: " + '; '.join(
        f"{lat} ms {E[f'base_{lat}'].mean():+.2f} (limit_test.evaluate {E[f'lt_{lat}'].mean():+.2f}, limit_entries.csv {lt[f'mkt_{lat}'].mean():+.2f}, "
        f"max per-day diff {np.abs(E[f'base_{lat}'] - E[f'lt_{lat}']).max():.2f})" for lat in LATS)
       + f" | LIMIT_RESULTS {LIMIT_25:+.2f} / {LIMIT_75:+.2f} / {LIMIT_150:+.2f}")
    assert abs(E.base_75.mean() - LIMIT_75) < 0.005 and abs(E.base_25.mean() - LIMIT_25) < 0.005 and abs(E.base_150.mean() - LIMIT_150) < 0.005
    dif = independent_check(E, samples)
    pr(f"   independent pandas re-computation (baseline + both early fills at 75 ms): max |diff| {dif.max():.4f} ticks over {len(dif)} days")
    assert dif.max() < 1e-9
    h1 = E.date <= '2022-12-31'; h2 = E.date >= '2023-01-01'
    res = {}
    for d in DELTAS:
        pr(f"\n   EARLY delta {d} ms (decision on the last trade before T-{d}ms, arrival T-{d}ms+latency):")
        for lat in LATS:
            b = boot(E[f'base_{lat}']); e = boot(E[f'early_{d}_{lat}']); sv = boot(E[f'save_{d}_{lat}'])
            pr(f"     {lat:3d} ms: baseline {b[0]:+.2f} | early {e[0]:+.2f} [{e[1]:+.2f}, {e[2]:+.2f}] | SAVING {sv[0]:+.2f} [{sv[1]:+.2f}, {sv[2]:+.2f}] ticks/side")
        x = E[f'save_{d}_{PRIMARY}']
        a = boot(x); a1 = boot(x[h1]); a2 = boot(x[h2]); a1b = boot(x[h1], q=(1.25, 98.75)); a2b = boot(x[h2], q=(1.25, 98.75))
        res[d] = dict(save=a[0], ci=(a[1], a[2]), h1=a1, h2=a2, h1b=a1b, h2b=a2b)
        pr(f"     PRIMARY 75 ms: saving {a[0]:+.2f} [{a[1]:+.2f}, {a[2]:+.2f}] n {a[3]}; 2019-2022 {a1[0]:+.2f} [{a1[1]:+.2f}, {a1[2]:+.2f}] n {a1[3]}; "
           f"2023-2026 {a2[0]:+.2f} [{a2[1]:+.2f}, {a2[2]:+.2f}] n {a2[3]}  (97.5% CIs: [{a1b[1]:+.2f}, {a1b[2]:+.2f}] / [{a2b[1]:+.2f}, {a2b[2]:+.2f}])")
        yr = E.groupby(E.date.dt.year).agg(n=('dir', 'size'), base=(f'base_{PRIMARY}', 'mean'), early=(f'early_{d}_{PRIMARY}', 'mean'), save=(f'save_{d}_{PRIMARY}', 'mean'))
        pr("     by year (75 ms) n / baseline / early / saving: " + ' | '.join(f"{y} {int(r.n)} {r.base:+.2f} {r.early:+.2f} {r.save:+.2f}" for y, r in yr.iterrows()))
        pr(f"     share of days early cheaper / equal / dearer at 75 ms: {(x > 0).mean() * 100:.0f}% / {(x == 0).mean() * 100:.0f}% / {(x < 0).mean() * 100:.0f}%; "
           f"median saving {x.median():+.2f}; drop the 5 best days {np.sort(x.values)[:-5].mean():+.2f}")
        pr(f"     decomposition: R - C' (pre-release move in the trade's direction, ticks) mean {((E.R - E[f'Cp_{d}']) * E.dir / TICK).mean():+.2f}, "
           f"median {((E.R - E[f'Cp_{d}']) * E.dir / TICK).median():+.2f}")
        tv = boot(E[f'savetick_{d}_{PRIMARY}'])
        pr(f"     tick-triggered variant (decide on the first trade at/after T-{d}ms, arrive its time + 75 ms): saving {tv[0]:+.2f} [{tv[1]:+.2f}, {tv[2]:+.2f}]; "
           f"trigger trade median {E[f'tk_ms_{d}'].median():+.0f} ms vs T, arrivals after T on {(E[f'tk_ms_{d}'] + PRIMARY > 0).mean() * 100:.1f}% of days")
    # ---------------------------------------------------------------- Part C (needs Part A's early prices)
    Cdf = part_c(E); E = E.merge(Cdf, on='date', how='left')
    for d in DELTAS:
        ag = E[f'c_early_{d}'] == E.dir; miss = ~ag
        sv = boot(E.loc[ag, f'save_{d}_{PRIMARY}'])
        cons = E.loc[miss, f'c_cons_{d}']
        yrs = (E.date.max() - E.date.min()).days / 365.25
        pr(f"   delta {d} ms: early decision agrees on {int(ag.sum())}/{len(E)} entry days; MISSED/side-flipped {int(miss.sum())} "
           f"({miss.mean() * 100:.2f}% of entries; by year {E[miss].date.dt.year.value_counts().sort_index().to_dict()}); "
           f"P&L consequence of the misses (early path - real path, S1 on NT bars) sum ${cons.sum():+,.0f} over {yrs:.1f} years "
           f"(${cons.sum() / yrs:+,.0f}/yr; harm-only ${cons[cons < 0].sum():+,.0f}); saving on agreeing days {sv[0]:+.2f} [{sv[1]:+.2f}, {sv[2]:+.2f}]")
    pr(f"   margin by which the real 10:00 decision cleared its band (ticks): median {E.margin_ticks.median():.1f}, 5th pct {E.margin_ticks.quantile(.05):.1f}, "
       f"share within 4 ticks {(E.margin_ticks <= 4).mean() * 100:.1f}%")
    # ---------------------------------------------------------------- Part B
    B, cons = part_b(E)
    U = B[B.usable].copy(); n = len(U); nent = int((U.pos_after != 0).sum())
    pr(f"   usable 10:00 entry-from-flat decision days: {n} (real entries {nent}, no-entry {n - nent})")
    pr(f"   pre-release quotes (NinjaTrader recorded bid/ask, prints 09:59:58-10:00:00): {U.n_pre2s.sum():,} prints, 1-tick spread on "
       f"{U.n_sp1.sum() / U.n_pre2s.sum() * 100:.1f}%, <= 2 ticks {U.sp_le2.sum() / U.n_pre2s.sum() * 100:.1f}%; mean spread by day median "
       f"{U.sp_mean.median():.2f} ticks (worst day {U.sp_mean.max():.2f})")
    verdict = {}
    for d in DELTAS:
        c = U[f'cls_{d}'].value_counts().to_dict(); fl = sum(c.get(k, 0) for k in ('missed', 'false', 'side')); rate = fl / n
        pr(f"\n   delta {d} ms: agree {c.get('agree', 0)}, MISSED {c.get('missed', 0)}, FALSE {c.get('false', 0)}, SIDE {c.get('side', 0)} -> "
           f"flip rate {rate * 100:.2f}% of {n} decisions ({fl / max(nent, 1) * 100:.1f}% per real entry)")
        net = 0.0; harm = 0.0; unknown = 0
        for r in U[U[f'cls_{d}'] != 'agree'].itertuples():
            k = cons[(r.date, d)]
            if not np.isfinite(k['cons']): unknown += 1
            else: net += k['cons']; harm += min(0.0, k['cons'])
            pr(f"     {r.date} {r[U.columns.get_loc(f'cls_{d}') + 1]:6s} real {r.pos_after:+d} early {r[U.columns.get_loc(f'early_{d}') + 1]:+d} | "
               f"C' {r[U.columns.get_loc(f'Cp_nt_{d}') + 1]} C {r.C_nt} band up {r.up_nt:.2f} lo {r.lo_nt:.2f} (tick scale) | paths real {k['real_path']} "
               f"early {k['early_path']} sim_ok {k['sim_ok']} | P&L real ${k['real_pnl']:+,.2f} early ${k['early_pnl']:+,.2f} -> consequence ${k['cons']:+,.2f}")
        agree_ent = int(((U.pos_after != 0) & (U[f'cls_{d}'] == 'agree')).sum())
        sav_usd = res[d]['save'] * DOLLAR_TICK * agree_ent; flip_cost = -net
        # A1-2 quote cross-check on NT-year days that are Databento entry days
        ov = U[U.date.isin(E.date.dt.strftime('%Y-%m-%d')) & (U.pos_after != 0) & (U[f'cls_{d}'] == 'agree')].copy()
        ov['q_fill'] = np.where(ov.pos_after > 0, ov[f'qask_{d}'], ov[f'qbid_{d}'])
        ov['q_cost'] = (ov.q_fill - ov.R_nt) * ov.pos_after / TICK
        ov = ov.merge(E.assign(dd=E.date.dt.strftime('%Y-%m-%d'))[['dd', f'early_{d}_{PRIMARY}', f'base_{PRIMARY}']], left_on='date', right_on='dd')
        qd = ov.q_cost.mean() - ov[f'early_{d}_{PRIMARY}'].mean()
        adj = res[d]['save'] - (qd if qd > 0.5 else 0.0)
        pr(f"     quote cross-check (A1-2) on {len(ov)} overlap entry days: early cost from NT quotes {ov.q_cost.mean():+.2f} vs touch model "
           f"{ov[f'early_{d}_{PRIMARY}'].mean():+.2f} (diff {qd:+.2f}; adjustment {'APPLIED' if qd > 0.5 else 'not triggered'}); baseline on those days {ov[f'base_{PRIMARY}'].mean():+.2f}")
        pr(f"     NT-year money: saving {res[d]['save']:+.2f} ticks x $0.50 x {agree_ent} agreeing entries = ${sav_usd:+,.0f}; flip cost (net) ${flip_cost:+,.0f}, "
           f"harm-only ${-harm:+,.0f}; unknown consequences {unknown}")
        A = adj >= 1.0 and res[d]['h1'][1] > 0 and res[d]['h2'][1] > 0
        Bc = rate <= 0.03
        Cc = unknown == 0 and flip_cost < sav_usd
        verdict[d] = A and Bc and Cc
        flag = A and not (res[d]['h1b'][1] > 0 and res[d]['h2b'][1] > 0)
        pr(f"     BAR delta {d}: (a) saving {adj:+.2f} >= +1.0 and both half CIs > 0 ({res[d]['h1'][1]:+.2f}, {res[d]['h2'][1]:+.2f}): {A}"
           f"{' [would not hold at 97.5%]' if flag else ''}; (b) flip rate {rate * 100:.2f}% <= 3%: {Bc}; (c) flip cost ${flip_cost:+,.0f} < saving ${sav_usd:+,.0f}"
           f"{' (unknown flips present)' if unknown else ''}: {Cc} -> {'PROMISING' if verdict[d] else 'NOT PROMISING'}")
    pr(f"\nVERDICT: early fire {'PROMISING' if any(verdict.values()) else 'NOT PROMISING'} "
       f"({', '.join(f'delta {d} ms ' + ('PROMISING' if v else 'NOT PROMISING') for d, v in verdict.items())})")
    # ---------------------------------------------------------------- hand-check material
    pr("\nHAND-CHECK MATERIAL (three days, E.sample(3, random_state=22)); trades as (ms vs T, price, side +1 buyer/-1 seller):")
    for _, r in E.sample(3, random_state=22).iterrows():
        ts, px, sd, g = samples[r.win]; T = T_ns(r.date + pd.Timedelta(hours=10))
        pr(f"  {r.date.date()} dir {r.dir:+d}: C {r.C} R {r.R} t0 {r.t0_ms:+.0f} ms | baseline75 {r.base_75:+.2f} | C'250 {r.Cp_250} early250_75 {r.early_250_75:+.2f} | "
           f"C'1000 {r.Cp_1000} early1000_75 {r.early_1000_75:+.2f} | Part C early sides {int(r.c_early_250):+d}/{int(r.c_early_1000):+d}, margin {r.margin_ticks:.1f} ticks")
        for lo, hi in ((-1100, -900), (-300, -150), (-30, 0), (0, 160)):
            m = (ts >= T + lo * MS) & (ts <= T + hi * MS); z = [(round(float(a), 1), float(b), int(c)) for a, b, c in zip((ts[m] - T) / MS, px[m], sd[m])]
            pr(f"     [{lo:+d},{hi:+d}] ms: {z[:6]}{' ...' if len(z) > 12 else ''}{z[-6:] if len(z) > 12 else z[6:]}")
    E.to_csv(os.path.join(HERE, 'earlyfire_days.csv'), index=False)
    B.to_csv(os.path.join(HERE, 'earlyfire_ntyear.csv'), index=False)
    with open(os.path.join(HERE, 'earlyfire_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
