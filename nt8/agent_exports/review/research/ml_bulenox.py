import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2, ml_lab as ML, ml_run as MR, biglever as BL, bulenox_routes as BR

nq, mq, es, ms = (ML.Sessions(n) for n in ('nq', 'mnqx', 'es', 'mes'))
T, pred = MR.family(nq, mq, es, ms, 15, 15)
s, sides = ML.trade(T, pred, 2 * 2 * MR.SIDE(2.35), MR.SIDE(2.35)); m = s.index.year >= 2013
usd = (s[m] * L2.N_USD).values; nt = sides[m] / 2.0; a = BR.SIZES['50K']
print("BULENOX 50K MODEL FOR L1 (information only - L1 failed G1; close-to-close marks, so OPTIMISTIC about the trailing drawdown)")
for q in (1, 2):
    e = BL.eval_stats(usd, usd, nt, q, BL.SEED_REPORT, a['dd'], a['dll'], a['target'], a['qual'], resets=True)
    f = BL.funded_stats(BL.master_qm, usd, usd, nt, a, q, BL.SEED_REPORT)
    print(f"   {q} micro: evaluation pass per try {e['p'] * 100:.1f}% (fees per pass ${e['fees']:,.0f}) | Master 2-yr payouts ${f['pay']:,.0f}, any payout {f['anyp'] * 100:.0f}%, open after 1 yr {f['open1'] * 100:.0f}%")
