r"""Calibration of the two alarms in MONITOR_PLAN.txt (pre-registered 2026-10-19). Run from the research folder:
    python monitor_calibrate.py
Writes nothing but its printed report (captured as monitor_calibrate_output.txt).
"""
import os
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DAILY = os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv')
SIDES = os.path.join(HERE, 'exec_pilot_sides.csv')
ARL_WATCH, ARL_ALARM = 504, 2520
N_PATHS, CAP = 20_000, 40_000
SEED1, SEED2 = 20261019, 20261020


def load():
    d = pd.read_csv(DAILY, index_col=0, parse_dates=True)
    x = d.r_today.values * 1e4                                  # bp of notional per session
    months = [g.r_today.values * 1e4 for _, g in d.groupby([d.index.year, d.index.month])]
    L = max(len(m) for m in months)
    blk = np.full((len(months), L), np.nan)
    for i, m in enumerate(months): blk[i, :len(m)] = m
    return d, x, blk


def run_lengths(blk, shift, k, h, seed, n=N_PATHS, cap=CAP):
    """First session at which the CUSUM S_t = max(0, S_(t-1) + k - x_t) reaches h, per path (month-block bootstrap)."""
    rng = np.random.default_rng(seed)
    nb, L = blk.shape
    rl = np.full(n, cap, float); S = np.zeros(n); t = np.zeros(n); act = np.arange(n)
    while len(act) and t[act].min() < cap:
        draw = rng.integers(0, nb, size=len(act))
        y = k - (blk[draw] + shift)                                   # NaN on padding
        Sa = S[act]; ta = t[act]; done = np.zeros(len(act), bool)
        for j in range(L):
            v = ~np.isnan(y[:, j]) & ~done
            Sa[v] = np.maximum(0.0, Sa[v] + y[v, j]); ta[v] += 1
            hit = v & (Sa >= h)
            if hit.any():
                rl[act[hit]] = ta[hit]; done |= hit
        S[act] = Sa; t[act] = ta
        act = act[~done & (ta < cap)]
    return rl


def find_h(blk, k, target, seed, lo=0.0, hi=3000.0, it=22):
    for _ in range(it):                                              # bisection; ARL is increasing in h (common random numbers)
        mid = 0.5 * (lo + hi)
        if run_lengths(blk, 0.0, k, mid, seed).mean() < target: lo = mid
        else: hi = mid
    return 0.5 * (lo + hi)


def cusum_path(x, k):
    S = np.zeros(len(x)); s = 0.0
    for i, v in enumerate(x): s = max(0.0, s + k - v); S[i] = s
    return S


def main():
    d, x, blk = load()
    mu0, sd = x.mean(), x.std(ddof=1)
    k = mu0 / 2
    print(f"Reference: {len(x)} sessions {d.index[0].date()}..{d.index[-1].date()}, mu0 = {mu0:.3f} bp/day, sd {sd:.2f} bp/day, "
          f"k = mu0/2 = {k:.3f} bp; {blk.shape[0]} calendar months")

    print("\n=== A. CUSUM levels (month-block bootstrap, 20,000 paths) ===")
    hw = find_h(blk, k, ARL_WATCH, SEED1); ha = find_h(blk, k, ARL_ALARM, SEED1)
    rw1 = run_lengths(blk, 0.0, k, hw, SEED1); ra1 = run_lengths(blk, 0.0, k, ha, SEED1)
    rw2 = run_lengths(blk, 0.0, k, hw, SEED2); ra2 = run_lengths(blk, 0.0, k, ha, SEED2)
    print(f"   h_watch = {hw:.1f} bp: ARL0 seed1 {rw1.mean():,.0f} (target {ARL_WATCH}), seed2 {rw2.mean():,.0f} "
          f"({rw2.mean()/ARL_WATCH*100-100:+.1f}%), median {np.median(rw2):,.0f}; censored at {CAP:,}: {(rw2>=CAP).mean()*100:.2f}%")
    print(f"   h_alarm = {ha:.1f} bp: ARL0 seed1 {ra1.mean():,.0f} (target {ARL_ALARM}), seed2 {ra2.mean():,.0f} "
          f"({ra2.mean()/ARL_ALARM*100-100:+.1f}%), median {np.median(ra2):,.0f}; censored: {(ra2>=CAP).mean()*100:.2f}%")
    ok_w = abs(rw2.mean() / ARL_WATCH - 1) <= 0.10; ok_a = abs(ra2.mean() / ARL_ALARM - 1) <= 0.10
    print(f"   second-seed check within +/-10%: watch {ok_w}, alarm {ok_a}")
    print(f"   in-control: P(watch within 1 year / 252 sessions) {(rw2<=252).mean()*100:.1f}%, P(alarm within 1 year) {(ra2<=252).mean()*100:.1f}%, "
          f"within 5 years {(ra2<=1260).mean()*100:.1f}%")

    print("\n   Out of control (shift every session; seed2):  sessions to WATCH mean / median | to ALARM mean / median | P(alarm within 1y / 2y)")
    for name, mu in (('mu0/2 (half the edge)', mu0 / 2), ('0 (edge gone)', 0.0), ('-mu0 (losing as fast as it earned)', -mu0),
                     ('-5 mu0 (broken)', -5 * mu0)):
        sh = mu - mu0
        w = run_lengths(blk, sh, k, hw, SEED2); a = run_lengths(blk, sh, k, ha, SEED2)
        print(f"   mean {mu:+7.3f} bp  {name:36s} {w.mean():7,.0f} / {np.median(w):6,.0f} | {a.mean():7,.0f} / {np.median(a):6,.0f} | "
              f"{(a<=252).mean()*100:5.1f}% / {(a<=504).mean()*100:5.1f}%")

    print("\n   Historical 16-year path (descriptive; levels calibrated on the same data; reset to 0 after each alarm):")
    s = 0.0; first_w = None; events = []
    for dt_, v in zip(d.index, x):
        s = max(0.0, s + k - v)
        if s >= hw and first_w is None: first_w = dt_
        if s >= ha:
            events.append(dt_); s = 0.0; first_w = None
    S = cusum_path(x, k)
    print(f"   alarms (reset after each): {[str(e.date()) for e in events] or 'none'}")
    print(f"   watch-level crossings without reset: {int(((S[1:] >= hw) & (S[:-1] < hw)).sum())}; max S {S.max():.1f} bp on {d.index[S.argmax()].date()}; "
          f"days at/above watch {(S>=hw).mean()*100:.1f}%")

    print("\n=== B. Cost rule (pilot Part B per-side market costs, recentred; 2,000 trials, 1,000 bootstrap resamples each) ===")
    sd_ = pd.read_csv(SIDES)
    sd_ = sd_[(sd_.part == 'B') & ~sd_.roll.astype(str).str.lower().eq('true')]       # the pilot's primary set: Part B outside the roll window
    rng = np.random.default_rng(SEED1)
    for col in ('mtouch_0', 'mtouch_250'):
        # the columns hold the touch PRICE; cost = (price - R) x s / tick, exactly exec_pilot.py's cost() (positive = worse)
        v = ((sd_[col] - sd_.R) * sd_.s / 0.25).dropna().values.astype(float)
        print(f"   {col}: {len(v)} sides, mean {v.mean():+.2f}, sd {v.std(ddof=1):.2f} ticks  (pilot check: +0.59 at 0 ms, +5.42 at 250 ms, n=124)")
        for target in (1.0, 1.5, 2.3, 5.4):
            base = v - v.mean() + target; row = []
            for n in (20, 50, 100, 200):
                att = warn = 0; trials = 2000
                for c0 in range(0, trials, 100):
                    smp = base[rng.integers(0, len(base), size=(100, n))]
                    m = smp.mean(1)
                    bi = rng.integers(0, n, size=(100, 1000, n))
                    bm = np.take_along_axis(smp[:, None, :].repeat(1000, 1), bi, axis=2).mean(2)
                    lo = np.percentile(bm, 2.5, axis=1)
                    att += (lo > 1.5).sum(); warn += ((m > 1.5) & ~(lo > 1.5)).sum()
                row.append(f"n={n:3d}: attention {att/trials*100:5.1f}% warn {warn/trials*100:5.1f}%")
            print(f"      true mean {target:3.1f}  " + " | ".join(row))

    print("\n=== D. The existing 20-session gate (-$598 / +$1,114, 2019-2025 dollars, 1 contract) at today's price ===")
    N = 2 * d.attrs_p_ref.iloc[0]
    for lab, mask in (('16 years', np.ones(len(d), bool)), ('2019-2026', (d.index >= '2019-05-06'))):
        usd = d.r_today.values[mask] * N
        r20 = pd.Series(usd).rolling(20).sum().dropna()
        print(f"   {lab}: rolling 20-session P&L in today's $ below -598 on {(r20 < -598).mean()*100:.1f}% of days, above +1,114 on "
              f"{(r20 > 1114).mean()*100:.1f}%; 5th/95th percentile ${np.percentile(r20,5):,.0f} / ${np.percentile(r20,95):,.0f}")


if __name__ == '__main__':
    main()
