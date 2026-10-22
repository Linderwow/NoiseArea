r"""BIGLEVER follow-up (2026-10-21, after "so 6 micros should be good to try out?"): the evaluation pass rate at a few sizes on the
ACTUAL consecutive 21-session windows (every start day, no bootstrap), split by era, so a recent weak stretch cannot hide inside the
16-year average. Same simulator as biglever.py (intraday worst points, DLL, end-of-day threshold, $78 resets with >= 5 sessions
left). Reported, not a new decision.
    python biglever_eras.py  -> biglever_eras_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import bulenox_routes as BR
import biglever as B

ERAS = (('NQ 2010-2019', '2010-01-01', '2019-05-05'), ('MNQ 2019-2026', '2019-05-06', '2026-12-31'),
        ('last 24 months', '2024-10-14', '2026-12-31'), ('last 12 months', '2025-10-14', '2026-12-31'))
CASES = (('25K', 3), ('25K', 4), ('25K', 6), ('50K', 4), ('50K', 6), ('50K', 8))


def main():
    s = pd.read_csv(os.path.join(HERE, 'intraday_worst.csv'), index_col=0, parse_dates=True)
    import src2_lib as L
    usd, usdm = (s.r_close * L.N_USD).values, (s.r_min * L.N_USD).values; d = s.index
    out = [f"BIGLEVER eras: every consecutive 21-session window starting in the era (ending by {d[-1].date()}), intraday model, resets "
           f"allowed; pass % per 30-day try | average resets | expected fees per funded account (fee + $78 x resets, / pass rate)"]
    starts_all = np.arange(len(d) - B.N_WIN + 1)
    for a, q in CASES:
        p = BR.SIZES[a]; line = []
        for lab, x0, x1 in ERAS:
            st = starts_all[(d[starts_all] >= pd.Timestamp(x0)) & (d[starts_all] <= pd.Timestamp(x1))]
            idx = st[:, None] + np.arange(B.N_WIN)
            ok, nres = B.evaluation(usd[idx] * q, usdm[idx] * q, p['dd'], p['dll'], p['target'], resets=True)
            fees = (p['qual'] + B.RESET_FEE * nres.mean()) / ok.mean() if ok.mean() > 0 else float('inf')
            line.append(f"{lab} ({len(st)}): {ok.mean() * 100:4.1f}% | {nres.mean():.2f} | ${fees:,.0f}")
        out.append(f"   {a} at {q} micros: " + '   '.join(line))
    print('\n'.join(out))
    with open(os.path.join(HERE, 'biglever_eras_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
