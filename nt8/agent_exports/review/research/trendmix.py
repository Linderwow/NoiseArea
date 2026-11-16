import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import regime_test as rt

P_REF = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
USD = 2 * P_REF


def fam_T(mk, mode='na', thr=0.02, vm=1.0, look=14, step=30, first=600, last=930, tmin=840):
    sig = mk.sigma(look); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; td = 0; leg = 0; leg_e = 0.0
        for i in range(flat_i):
            if M[i] not in checks or np.isnan(sg[i]): continue
            c = C[i]
            if M[i] == tmin:
                r = c / mk.pc[k] - 1; td = int(np.sign(r)) if abs(r) > thr else 0
            new = pos
            if mode != 'd03':
                ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
                if pos == 0: new = 1 if c > ub else (-1 if c < lb else 0)
                elif pos == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                if mode in ('hold', 'holdfill') and td != 0 and M[i] >= tmin and pos == td: new = pos
            if new != pos:
                px = O[i + 1]
                if pos != 0: p += (px - entry) * pos
                if new != 0: entry = px; n_ += 1
                pos = new
            if M[i] == tmin and td != 0 and i + 1 <= flat_i:
                take = mode in ('d03', 'add') or (mode in ('fill', 'holdfill') and pos == 0)
                if take: leg = td; leg_e = O[i + 1]; n_ += 1
        if pos != 0: p += (C[flat_i] - entry) * pos
        if leg != 0: p += (C[flat_i] - leg_e) * leg
        pnl[k] = p; nt[k] = n_
    return pnl, nt


def stats(r):
    eq = np.cumsum(r.values) * USD; dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()
    h = r[r.index >= '2023-01-01']; d = r[r.index <= '2019-05-05']; m = r[r.index > '2019-05-05']
    t = lambda x: x.mean() / x.std() * np.sqrt(len(x)) if x.std() > 0 else 0.0
    yr = r.groupby(r.index.year).sum()
    return dict(usd=r.mean() * 252 * USD, sh=r.mean() / r.std() * np.sqrt(252), t=t(r), dd=dd, t_nq=t(d), t_mnq=t(m),
                usd_h=h.mean() * 252 * USD, sh_h=h.mean() / h.std() * np.sqrt(252), yrs=(yr > 0).mean())


if __name__ == '__main__':
    out = [f"TRENDMIX (TRENDMIX_PLAN.txt) - NoiseArea engine, joined NQ 2010-19 + MNQ 2019-26, $ per micro at today's price"]
    base1, _ = N.series('MNQ', ticks=1)
    t0, _ = N.series('MNQ', fn=fam_T, ticks=1, mode='na')
    out.append(f"parity T0 vs nav_lab baseline: {len(t0)} sessions, max |diff| {(t0 - base1).abs().max():.2e}")
    names = [('T0', 'na', 'NoiseArea alone'), ('T1', 'd03', 'trend day alone'), ('T2', 'add', 'ADD (2nd micro)'),
             ('T3', 'fill', 'FILL when NA flat'), ('T4', 'hold', 'HOLD NA to close'), ('T5', 'holdfill', 'HOLD + FILL')]
    for ticks in (1, 2.35):
        S = {sid: N.series('MNQ', fn=fam_T, ticks=ticks, mode=m)[0] for sid, m, _ in names}
        na = S['T0']
        out.append(f"\n--- {ticks} tick(s) a side + $0.50 commission ---")
        out.append("  id  variant              $/yr   Sharpe    t   NQ t  MNQ t  maxDD    yrs+  2023-26 $/yr Sh   | vs NA: P(Sharpe up)")
        for sid, m, lab in names:
            s = stats(S[sid]); x = S[sid]
            p = '' if sid in ('T0', 'T1') else f"P {rt.boot_sharpe_diff(x.index, x.values, na.values):.4f}"
            if sid == 'T1':
                c = np.corrcoef(x.values, na.values)[0, 1]; p = f"corr with NA {c:+.2f}"
            out.append(f"  {sid}  {lab:18s} {s['usd']:+7,.0f}  {s['sh']:5.2f} {s['t']:+5.2f} {s['t_nq']:+5.2f} {s['t_mnq']:+5.2f} ${s['dd']:7,.0f}"
                       f"  {s['yrs'] * 100:3.0f}%  {s['usd_h']:+7,.0f} {s['sh_h']:5.2f}   | {p}")
    txt = "\n".join(out); print(txt); open(os.path.join(HERE, 'trendmix_output.txt'), 'w').write(txt + "\n")
