r"""Repeated-look calibration of the fill-cost alarm (MONITOR_PLAN.txt, amendment 2026-10-20). Run from the research folder:
    python monitor_cost_seq.py
Statistic per look: t_n = (mean_n - 1.5) / (sd_n / sqrt(n)); a level-c rule fires at a look when t_n > c, so
P(any fire over the looks) = P(max_n t_n > c), and the calibrated c is a quantile of max_n t_n under a true mean of 1.0.
"""
import os
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SIDES = os.path.join(HERE, 'exec_pilot_sides.csv')
GATE, N_MAX, N_MIN, STEP, PATHS = 1.5, 450, 20, 2, 20_000
LOOKS = np.arange(N_MIN, N_MAX + 1, STEP)


def costs(col):
    s = pd.read_csv(SIDES)
    s = s[(s.part == 'B') & ~s.roll.astype(str).str.lower().eq('true')]
    return ((s[col] - s.R) * s.s / 0.25).dropna().values.astype(float)


def tstats(base, true_mean, seed):
    rng = np.random.default_rng(seed)
    x = base - base.mean() + true_mean
    d = x[rng.integers(0, len(x), size=(PATHS, N_MAX))]
    n = np.arange(1, N_MAX + 1)
    cs = np.cumsum(d, 1); cs2 = np.cumsum(d * d, 1)
    m = cs / n; var = (cs2 - n * m * m) / np.maximum(n - 1, 1)
    sd = np.sqrt(np.maximum(var, 1e-12))
    t = (m - GATE) / (sd / np.sqrt(n))
    return t[:, LOOKS - 1]                                   # (paths, looks)


def main():
    hi, lo = costs('mtouch_250'), costs('mtouch_0')
    print(f"pilot sides: high-noise (250 ms) n={len(hi)} mean {hi.mean():+.2f} sd {hi.std(ddof=1):.2f}; low-noise (0 ms) n={len(lo)} mean {lo.mean():+.2f} sd {lo.std(ddof=1):.2f}")
    print(f"looks: every {STEP} sides from {N_MIN} to {N_MAX} ({len(LOOKS)} looks), {PATHS:,} paths")
    mx1 = tstats(hi, 1.0, 20261020).max(1)
    c_alarm = float(np.quantile(mx1, 0.975)); c_warn = float(np.quantile(mx1, 0.80))
    print(f"\nCALIBRATED (seed 20261020, high noise, true 1.0): c_alarm = {c_alarm:.3f}  c_warn = {c_warn:.3f}")
    mx2 = tstats(hi, 1.0, 20261021).max(1)
    pa2, pw2 = (mx2 > c_alarm).mean() * 100, (mx2 > c_warn).mean() * 100
    ok = abs(pa2 - 2.5) <= 0.5 and abs(pw2 - 20.0) <= 0.5
    print(f"second seed 20261021: P(any NEEDS ATTENTION) {pa2:.2f}% (target 2.5), P(any WARNING) {pw2:.2f}% (target 20) -> {'PASS' if ok else 'FAIL'}")
    mxl = tstats(lo, 1.0, 20261020).max(1)
    pal, pwl = (mxl > c_alarm).mean() * 100, (mxl > c_warn).mean() * 100
    print(f"low noise, true 1.0: P(any NEEDS ATTENTION) {pal:.2f}%, P(any WARNING) {pwl:.2f}% -> {'PASS' if pal <= 2.5 and pwl <= 20 else 'FAIL'}")
    old = tstats(hi, 1.0, 20261021)
    print(f"(for comparison, the old per-look 95% rule ~ c = 1.96: P(any NEEDS ATTENTION) {(old.max(1) > 1.96).mean() * 100:.1f}%)")
    print("\nPOWER: P(NEEDS ATTENTION within 450 sides) and median sides to the first NEEDS ATTENTION")
    for name, base in (('high noise', hi), ('low noise', lo)):
        for tm in (1.5, 2.3, 5.4):
            t = tstats(base, tm, 20261022)
            fired = t > c_alarm; anyf = fired.any(1)
            first = np.where(anyf, LOOKS[fired.argmax(1)], np.nan)
            med = np.nanmedian(first) if anyf.any() else float('nan')
            print(f"   {name:10s} true {tm:3.1f}: {anyf.mean() * 100:5.1f}%  median first at {med:5.0f} sides")


if __name__ == '__main__':
    main()
