"""Remaining-ideas tests per ALPHA2_PLAN.txt.  python3 alpha2.py"""
import datetime as dt
import numpy as np, pandas as pd
from research_engine import Market, fam_A, stats, WARMUP, TICK

FOMC = """2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11
2020-01-29 2020-03-03 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16""".split()
FOMC = {dt.date.fromisoformat(d) for d in FOMC}


def third_friday(y, m):
    d = dt.date(y, m, 15)
    while d.weekday() != 4: d += dt.timedelta(days=1)
    return d
QUAD = {third_friday(y, m) for y in range(2019, 2027) for m in (3, 6, 9, 12)}


def engine(mk, vm=1.0, look=14, first=600, last=930, reverse=True, day_stop=None, skip=None, flat_win=None, slip=TICK, comm=0.5):
    """Validated rules per bar. skip = set of dates with no trading; flat_win = (dates, flat_at_min, resume_min): on those dates go
    flat at the flat_at_min check and take no entries before resume_min. day_stop = X (noise widths) or None.
    Returns pnl, entries, end-of-day position, end-of-day exit price, entry price of the eod position, w (noise width $) per day."""
    sig = mk.sigma(look); pv = mk.pv; checks = set(range(600, 931, 30)); entries_ok = set(range(first, last + 1, 30))
    n = mk.n; pnl = np.full(n, np.nan); nt = np.zeros(n, int); eod_pos = np.zeros(n, int); eod_px = np.full(n, np.nan); eod_entry = np.full(n, np.nan); wd = np.full(n, np.nan)
    skip = skip or set(); fw_dates, fw_flat, fw_resume = flat_win if flat_win else (set(), None, None)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]):
            continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        d = x.date; today_skip = d in skip; fw = d in fw_dates
        i10 = int(np.argmax(M >= 600)) if (M >= 600).any() else 0
        w_pts = hi * vm * sg[i10] if not np.isnan(sg[i10]) else np.nan; wd[k] = w_pts * pv
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; pending = 0; halted = False
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        for i in range(flat_i + 1):
            if pending != 0:
                pos = pending; entry = O[i] + slip * pos; p -= comm; n_ += 1; pending = 0
            if i == flat_i:
                break
            # daily loss stop, evaluated at every bar close on realised + open P&L
            if day_stop is not None and not halted and not np.isnan(w_pts):
                open_p = (C[i] - entry) * pos * pv if pos != 0 else 0.0
                if p + open_p <= -day_stop * w_pts * pv:
                    halted = True
                    if pos != 0:
                        p += (O[i + 1] - slip * pos - entry) * pos * pv - comm; pos = 0
                    continue
            if M[i] in checks and not np.isnan(sg[i]):
                c = C[i]; ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
                can_enter = (M[i] in entries_ok) and not today_skip and not halted and not (fw and fw_resume is not None and M[i] < fw_resume and M[i] >= fw_flat)
                if fw and M[i] == fw_flat and pos != 0:
                    new = 0
                elif pos == 0:
                    new = (1 if c > ub else (-1 if c < lb else 0)) if can_enter else 0
                elif pos == 1:
                    if c < lb: new = -1 if (reverse and can_enter) else 0
                    else: new = 0 if c < max(ub, vw[i]) else 1
                else:
                    if c > ub: new = 1 if (reverse and can_enter) else 0
                    else: new = 0 if c > min(lb, vw[i]) else -1
                if new != pos:
                    if pos != 0:
                        p += (O[i + 1] - slip * pos - entry) * pos * pv - comm; pos = 0
                    if new != 0: pending = new
        if pos != 0:
            p += (C[flat_i] - slip * pos - entry) * pos * pv - comm
            eod_pos[k] = pos; eod_px[k] = C[flat_i]; eod_entry[k] = entry
        pnl[k] = p; nt[k] = n_
    return pnl, nt, eod_pos, eod_px, eod_entry, wd


def tstat(x): x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))


def S(mk, pnl, nt, a, b): return stats(mk, pnl, nt, a, b)


def line(lab, sd, sv, extra=""):
    print(f"   {lab:44s} disc ${sd['net']:8,.0f} t {sd['t']:5.2f} | val ${sv['net']:8,.0f} t {sv['t']:5.2f} maxDD ${sv['mdd']:6,.0f} trades {sv['trades']:4d} {extra}")


if __name__ == '__main__':
    for lab, csv in (('NT', 'nt_analyzer_bars.csv'), ('DB', 'mnq_db_5m.csv')):
        mk = Market(csv, 2.0, 'MNQ'); end = str(mk.dates[-1].date()); D = ('2019-01-01', '2022-12-31'); V = ('2023-01-01', end)
        p0, n0 = fam_A(mk, vm=1.0, slip=TICK, comm=0.5); pb, nb_, eod_pos, eod_px, eod_entry, wd = engine(mk)
        dmax = np.abs(np.nan_to_num(p0) - np.nan_to_num(pb)).max(); assert dmax < 0.005 and (n0 == nb_).all()
        print(f"\n######## {lab}: engine check max |diff| ${dmax:.4f} (OK). Days {mk.n}, {int(nb_.sum())} entries")
        bd, bv = S(mk, pb, nb_, *D), S(mk, pb, nb_, *V); line('BASELINE', bd, bv)
        dates = np.array([x.date for x in mk.days]); idx = np.arange(mk.n)
        def period_mask(a, b): return (idx >= WARMUP) & (mk.dates >= pd.Timestamp(a)) & (mk.dates <= pd.Timestamp(b))

        print(" T1 FOMC / quad witching")
        fm = np.array([d in FOMC for d in dates]); qm = np.array([d in QUAD for d in dates]); pbz = np.nan_to_num(pb)
        for nm, m in (('FOMC days', fm), ('quad-witching days', qm)):
            for per, (a, b) in (('disc', D), ('val', V)):
                mm = m & period_mask(a, b); print(f"   baseline on {nm:18s} {per}: {int(mm.sum()):3d} days, net ${pbz[mm].sum():7,.0f}, mean/day ${pbz[mm].mean() if mm.any() else 0:6,.0f} vs all days ${pbz[period_mask(a,b)].mean():5,.0f}")
        p, n_, *_ = engine(mk, skip=FOMC); line('skip FOMC days', S(mk, p, n_, *D), S(mk, p, n_, *V))
        p, n_, *_ = engine(mk, flat_win=(FOMC, 810, 900)); line('FOMC: flat at 13:30, no entries before 15:00', S(mk, p, n_, *D), S(mk, p, n_, *V))
        p, n_, *_ = engine(mk, skip=QUAD); line('skip quad-witching Fridays', S(mk, p, n_, *D), S(mk, p, n_, *V))

        print(" T2 hold the 16:00 position to the next session's open (reported only)")
        nxt_open = np.r_[[x.O[0] for x in mk.days][1:], np.nan]; gap_days = np.r_[[(mk.dates[i + 1] - mk.dates[i]).days for i in range(mk.n - 1)], 0]
        for nm, cond in (('always', np.ones(mk.n, bool)), ('only winners at 16:00', (eod_px - eod_entry) * eod_pos > 0)):
            carry = np.where((eod_pos != 0) & cond & ~np.isnan(nxt_open), (nxt_open - eod_px) * eod_pos * 2.0, 0.0)
            p = np.nan_to_num(pb) + carry; p[np.isnan(pb)] = np.nan
            held = ((eod_pos != 0) & cond).sum(); multi = ((eod_pos != 0) & cond & (gap_days > 3)).sum()
            line(f'hold overnight: {nm}', S(mk, p, nb_, *D), S(mk, p, nb_, *V), f"| {held} holds, {multi} span >3 days; carry alone disc ${carry[period_mask(*D)].sum():,.0f} val ${carry[period_mask(*V)].sum():,.0f}")

        print(" T3 reversal -> flat")
        p, n_, *_ = engine(mk, reverse=False); line('no reversal (flat, may re-enter next check)', S(mk, p, n_, *D), S(mk, p, n_, *V))

        print(" T4 daily loss stop (X noise widths)")
        for X in (1.0, 2.0, 3.0):
            p, n_, *_ = engine(mk, day_stop=X); line(f'daily stop {X} w (~${np.nanmedian(wd)*X:,.0f} median today)', S(mk, p, n_, *D), S(mk, p, n_, *V))

        print(" T5 parameter ensembles (3 micros of exposure each)")
        base3 = np.nan_to_num(pb) * 3
        ens_vm = sum(np.nan_to_num(engine(mk, vm=v)[0]) for v in (0.75, 1.0, 1.5)); ens_lk = sum(np.nan_to_num(engine(mk, look=l)[0]) for l in (10, 14, 21))
        for nm, p in (('3 x VM 1.0 (baseline)', base3), ('VM 0.75 + 1.0 + 1.5', ens_vm), ('lookback 10 + 14 + 21', ens_lk)):
            line(nm, S(mk, p, nb_, *D), S(mk, p, nb_, *V))

        print(" T6 entry window (first check x last entry check)")
        for f_ in (600, 630, 660):
            for l_ in (930, 870, 810):
                p, n_, *_ = engine(mk, first=f_, last=l_); line(f'first {f_//60:02d}:{f_%60:02d} last entry {l_//60:02d}:{l_%60:02d}' + (' (base)' if (f_, l_) == (600, 930) else ''), S(mk, p, n_, *D), S(mk, p, n_, *V))
