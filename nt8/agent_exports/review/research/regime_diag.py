r"""Descriptive diagnostics after the REGIME_PLAN.txt holdout run (do not change its verdict): why does per-state selection fail?
    python regime_diag.py  -> regime_diag_output.txt
(1) persistence: per cell, rank correlation of every catalog variant's t-stat on discovery vs holdout;
(2) does any classifier split NoiseArea itself into reliably better and worse days (same direction in both periods)?
"""
import numpy as np, pandas as pd
import regime_test as rt

dates, R, NT, c, p_ref, px, P, full, _ = rt.joined('MNQ')
feat = rt.features(full).reindex(dates)
disc, hold = rt.between(dates, rt.DISC), rt.between(dates, rt.HOLD)
cols = list(range(rt.N_FULL)); N = 2 * p_ref
print("(1) PERSISTENCE: Spearman rank correlation of the 732 variants' t-stats, discovery vs holdout, per cell")
print("    (1.0 = the same strategies win in the same state later; 0 = the state ranking is noise)")
for s in rt.SCHEMES:
    cid, _ = rt.cells(feat, s, disc); out = []
    for cc in sorted(set(cid[cid >= 0])):
        td, _ = rt.tstats(R, NT, disc & (cid == cc), cols); th, _ = rt.tstats(R, NT, hold & (cid == cc), cols)
        ok = np.isfinite(td) & np.isfinite(th)
        rho = pd.Series(td[ok]).rank().corr(pd.Series(th[ok]).rank())
        top = np.argsort(-np.where(ok, td, -np.inf))[:20]; top_hold = np.mean(th[top] > 0)
        out.append(f"cell {cc}: rho {rho:+.2f}, discovery top-20 positive on holdout {top_hold * 100:.0f}%")
    print(f"   {s} {rt.SCHEMES[s] or '(one cell)'}: " + " | ".join(out))
print("\n(2) NOISEAREA BY STATE: annualised Sharpe of NoiseArea's own days in each cell, discovery -> holdout")
na = R[:, rt.A_DEF]
for s in [k for k in rt.SCHEMES if k != 'S0']:
    cid, _ = rt.cells(feat, s, disc); out = []
    for cc in sorted(set(cid[cid >= 0])):
        a, b = na[disc & (cid == cc)], na[hold & (cid == cc)]
        out.append(f"cell {cc}: {rt.sharpe(a):+.2f} -> {rt.sharpe(b):+.2f} ({b.sum() * N / (hold.sum() / 252):+,.0f}/yr of holdout)")
    print(f"   {s} {rt.SCHEMES[s]}: " + " | ".join(out))
