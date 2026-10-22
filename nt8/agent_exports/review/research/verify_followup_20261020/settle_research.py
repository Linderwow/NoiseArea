r"""Settles the four research disagreements the 2026-10-19 double verification left open (tie-breakers cut off):
  R1  R1 'survival' at $50k: P(max drawdown >= 50% within 60 months) above or below the 5% bar (1,000,000 paths)
  R2  effect of the partial calendar months on that tail probability and on the CUSUM ARL0
  R3  CUSUM levels: noise or a small low bias (exact common-random-number ARL curves, 8 seeds)
  R6  MONITOR_RESULTS section D: the 'same bootstrap' rates next to the rolling-window rates
Read-only on the research folder: imports scaling.py / monitor_calibrate.py and reads scaling_inputs\scaling_daily.csv.
    python -I settle_research.py "<research folder>"
"""
import sys, os, time, ctypes
import numpy as np, pandas as pd

try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)   # BELOW_NORMAL
except Exception: pass

RES = sys.argv[1]
sys.path.insert(0, RES)
import scaling as sc

d, p_ref = sc.daily_series()
N = 2.0 * p_ref
disc = d[(d.index >= '2010-07-21') & (d.index <= '2018-12-31')].r_today
kelly = 0.5 * disc.mean() / disc.var(ddof=1)
pnl, nt, n_days, labels = sc.month_blocks(d, N)
print(f"daily series {len(d)} sessions {d.index[0].date()}..{d.index[-1].date()}, notional/micro ${N:,.0f}, {len(labels)} months")
short = [(lab, int(n)) for lab, n in zip(labels, n_days) if n < 17]
print("months with fewer than 17 sessions:", short)
part = {lab for lab, _ in short}
keep = np.array([lab not in part for lab in labels])

# ------------------------------------------------------------------ R1 / R2
def tail(rule, E0, idx, n_paths, seed, chunk=20_000):
    rng = np.random.default_rng(seed); dd50 = ruin = 0; tot = 0
    while tot < n_paths:
        m = min(chunk, n_paths - tot)
        draw = idx[rng.integers(0, len(idx), size=(m, sc.N_MONTHS))]
        r = sc.simulate(rule, E0, pnl[draw], nt[draw], N, kelly)
        dd50 += int((r['mdd_pct'] >= 0.5).sum()); ruin += int(r['ruined'].sum()); tot += m
    p = dd50 / tot
    return p, np.sqrt(p * (1 - p) / tot), ruin / tot

t0 = time.time()
print("\n=== R1 / R2: P(DD>=50% within 60 months) and P(ruin), month-block bootstrap as in scaling.py ===")
all_idx = np.arange(len(labels)); kept_idx = np.where(keep)[0]
for rule, E0 in (('R1', 50_000), ('R1', 25_000), ('R2', 50_000)):
    for lab, idx in (('partial months included (as registered)', all_idx), ('partial months excluded', kept_idx)):
        n = 1_000_000 if (rule, E0) == ('R1', 50_000) else 200_000
        p, se, pr = tail(rule, E0, idx, n, 777_001)
        print(f"   {rule} ${E0:>7,} {lab:40s} {n:>9,} paths: P(DD>=50%) {p*100:5.2f}% (SE {se*100:.3f}, 95% {100*(p-1.96*se):.2f}..{100*(p+1.96*se):.2f})  P(ruin) {pr*100:.2f}%")
print(f"   ({time.time()-t0:.0f} s)")

# ------------------------------------------------------------------ R3 (+R2 for the CUSUM)
import monitor_calibrate as mc
_, x, blk = mc.load()
mu0 = x.mean(); k = mu0 / 2
print(f"\n=== R3: CUSUM levels with exact common random numbers (mu0 {mu0:.3f} bp, k {k:.3f}) ===")
print("   S_t = max(0, S_(t-1) + k - x_t) does not depend on h, so each path's first passage over any h is read off the record")
print("   highs of one simulated path; ARL(h) is then exactly monotone in h (the defect in run_lengths()).")

def records(blk_, seed, n=20_000, cap=mc.CAP, hmax=1_600.0):
    rng = np.random.default_rng(seed); nb, L = blk_.shape
    S = np.zeros(n); M = np.zeros(n); t = np.zeros(n); act = np.arange(n)
    ev_p, ev_t, ev_s = [], [], []
    while len(act):
        y = k - blk_[rng.integers(0, nb, size=len(act))]
        Sa, Ma, ta = S[act], M[act], t[act]
        for j in range(L):
            v = ~np.isnan(y[:, j]) & (ta < cap)
            Sa[v] = np.maximum(0.0, Sa[v] + y[v, j]); ta[v] += 1
            up = v & (Sa > Ma)
            if up.any():
                Ma[up] = Sa[up]; ev_p.append(act[up]); ev_t.append(ta[up].copy()); ev_s.append(Sa[up].copy())
        S[act], M[act], t[act] = Sa, Ma, ta
        act = act[(Ma < hmax) & (ta < cap)]
    p = np.concatenate(ev_p); tt = np.concatenate(ev_t); ss = np.concatenate(ev_s)
    o = np.lexsort((tt, p)); return n, p[o], tt[o], ss[o]

def arl(rec, h, cap=mc.CAP):
    n, p, tt, ss = rec
    hit = ss >= h; first = np.full(n, float(cap))
    pp, ti = p[hit], tt[hit]
    u, i0 = np.unique(pp, return_index=True); first[u] = ti[i0]
    return first.mean()

def solve(rec, target, lo, hi):
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if arl(rec, mid) < target: lo = mid
        else: hi = mid
    return 0.5 * (lo + hi)

t0 = time.time(); hw, ha, a_w, a_a = [], [], [], []
for s in range(8):
    rec = records(blk, 9_100 + s)
    hw.append(solve(rec, mc.ARL_WATCH, 400, 1000)); ha.append(solve(rec, mc.ARL_ALARM, 900, 1600))
    a_w.append(arl(rec, 681.1)); a_a.append(arl(rec, 1296.7))
    print(f"   seed {9_100+s}: h for ARL0 504 = {hw[-1]:7.1f}, for 2,520 = {ha[-1]:7.1f} | ARL0 at the published 681.1 = {a_w[-1]:6.0f}, at 1,296.7 = {a_a[-1]:6.0f}")
def ms(v): v = np.array(v); return f"{v.mean():.1f} (SE {v.std(ddof=1)/np.sqrt(len(v)):.1f})"
print(f"   mean over 8 x 20,000 paths: h_watch {ms(hw)} vs published 681.1; h_alarm {ms(ha)} vs 1,296.7")
print(f"   ARL0 at the published levels: {ms(a_w)} (target 504), {ms(a_a)} (target 2,520)  ({time.time()-t0:.0f} s)")

# R2 for the CUSUM: the same with the partial months dropped from the block pool
labs_m = [f"{y}-{m:02d}" for (y, m), _ in d.groupby([d.index.year, d.index.month])]
keep_m = np.array([lab not in part for lab in labs_m])
aw2, aa2 = [], []
for s in range(4):
    rec = records(blk[keep_m], 9_100 + s)
    aw2.append(arl(rec, 681.1)); aa2.append(arl(rec, 1296.7))
print(f"   R2 partial months excluded (4 seeds): ARL0 at 681.1 = {ms(aw2)}, at 1,296.7 = {ms(aa2)}")

# ------------------------------------------------------------------ R6
print("\n=== R6: section D with the month-block bootstrap (the plan's 'same bootstrap') next to rolling windows ===")
rng = np.random.default_rng(20261020)
for lab, mask in (('16 years', np.ones(len(d), bool)), ('2019-05-06 .. 2026-10-14', d.index >= '2019-05-06')):
    dd_ = d[mask]
    mb = [g.r_today.values * N for _, g in dd_.groupby([dd_.index.year, dd_.index.month])]
    lo = hi = tot = 0; sums = []
    for _ in range(2_000):
        ser = np.concatenate([mb[i] for i in rng.integers(0, len(mb), size=120)])
        r20 = np.convolve(ser, np.ones(20), 'valid')
        lo += (r20 < -598).sum(); hi += (r20 > 1114).sum(); tot += len(r20); sums.append(r20[::20])
    sums = np.concatenate(sums)
    roll = pd.Series(dd_.r_today.values * N).rolling(20).sum().dropna()
    print(f"   {lab}: bootstrap below -$598 {lo/tot*100:.1f}%, above +$1,114 {hi/tot*100:.1f}%, 5th/95th ${np.percentile(sums,5):,.0f} / ${np.percentile(sums,95):,.0f}"
          f" | rolling windows {(roll<-598).mean()*100:.1f}% / {(roll>1114).mean()*100:.1f}%")
print(f"   today's price used: p_ref = {p_ref:,.2f} (mean 2026 09:30 open), notional/micro ${N:,.0f}")
