r"""Each session's intraday worst point of the live NoiseArea rules (BIGLEVER_PLAN.txt amendment 1), on the same 16-year joined series
as nav_lab.series (NQ <= 2019-05-05, MNQ NinjaTrader bars after), at today's cost.
    python intraday_worst.py   -> intraday_worst.csv (date, r_close, r_min, nt) + a check that r_close == nav_lab.series exactly
r_min = the lowest realized + open P&L reached during the session, open P&L marked at each 5-minute bar's low (long) / high (short),
with the whole day's trading costs charged (slightly conservative). A position decided at a bar's close is held from the next bar's
open, so that bar's range counts; the session-end flat is at the last bar's close (as fam_V).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N


def day_close_and_worst(mk, look=14, vm=1.0, checks=frozenset(range(600, 931, 30))):
    sig = mk.sigma(look); pc = np.full(mk.n, np.nan); pw = np.full(mk.n, np.nan); nt = np.zeros(mk.n)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, H, L, C, M, vw, nb = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        s = 0; ref = 0.0; p = 0.0; worst = 0.0; vol = 0
        for i in range(flat_i):
            if s > 0: worst = min(worst, p + (L[i] - ref))
            elif s < 0: worst = min(worst, p - (H[i] - ref))
            if M[i] not in checks or np.isnan(sg[i]): continue
            c = C[i]; ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
            if s == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif s == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if new != s:
                px = O[i + 1]; p += s * (px - ref); vol += abs(new - s); s = new; ref = px
        if s > 0: worst = min(worst, p + (L[flat_i] - ref))
        elif s < 0: worst = min(worst, p - (H[flat_i] - ref))
        if s != 0: p += s * (C[flat_i] - ref); vol += abs(s)
        pc[k] = p; pw[k] = min(worst, p); nt[k] = vol / 2
    return pc, pw, nt


def build():
    from research_engine import WARMUP
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * 1 + 0.25) / p_ref
    parts = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQ', lambda d: d > N.JOIN)):
        mk = N.market(nm); p, w, n = day_close_and_worst(mk); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c,
                                   'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c, 'nt': n[sel]}, index=d[sel]))
    s = pd.concat(parts); s.index.name = 'date'
    ref, nref = N.series('MNQ', fn=N.fam_V)
    print(f"check: {len(s)} sessions vs nav_lab.series {len(ref)}; max |r_close - series| {(s.r_close - ref).abs().max():.3e}; "
          f"trades {s.nt.sum():.0f} vs {nref.sum():.0f}; r_min <= r_close everywhere: {bool((s.r_min <= s.r_close + 1e-15).all())}")
    usd = 55751.0
    q = (s.r_min * usd).quantile([0.5, 0.9, 0.99]).values
    print(f"intraday worst point per micro: median ${-q[0] if False else (s.r_min * usd).median():,.0f}, 10th pct ${(s.r_min * usd).quantile(0.10):,.0f}, "
          f"1st pct ${(s.r_min * usd).quantile(0.01):,.0f}, worst ${(s.r_min * usd).min():,.0f}; days where the worst point is below the "
          f"close by > $100: {int(((s.r_close - s.r_min) * usd > 100).sum())} of {len(s)}")
    s.to_csv(os.path.join(HERE, 'intraday_worst.csv'))


if __name__ == '__main__':
    build()
