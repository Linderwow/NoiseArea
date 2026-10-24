r"""Per-session intraday path of NoiseArea's live rules on the extended 16-year series (2026-10-24, for prop firms whose drawdown
trails the intraday unrealized peak).
    python intraday_paths.py   -> intraday_paths_extended.csv (date, r_close, r_min, r_max, r_dd, nt)
r_close, r_min: exactly intraday_worst_extended.csv's (checked). r_max: the session's best point of realized + open P&L (open P&L at
each 5-minute bar's favourable extreme), net of the whole day's costs like r_min. r_dd: the largest drop from the session's running
peak (the peak starts at 0, the day's opening balance) - within a bar the favourable extreme is taken BEFORE the adverse one, the
conservative order for a trailing drawdown. With a trailing threshold h below the balance at the open, a session breaches iff
min(r_min - h, -r_dd) < -drawdown (exact on these bars: e - max(h, peak) = min(e - h, e - peak)).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import history_extend as HX


def day_paths(mk, look=14, checks=frozenset(range(600, 931, 30))):
    sig = mk.sigma(look); pc = np.full(mk.n, np.nan); pw = np.full(mk.n, np.nan); pb = np.full(mk.n, np.nan); pd_ = np.full(mk.n, np.nan); nt = np.zeros(mk.n)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, H, L, C, M, vw, nb = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        s = 0; ref = 0.0; p = 0.0; worst = 0.0; best = 0.0; peak = 0.0; dd = 0.0; vol = 0
        def mark(i):
            nonlocal worst, best, peak, dd
            fav = p + (s * (H[i] - ref) if s > 0 else s * (L[i] - ref)); adv = p + (s * (L[i] - ref) if s > 0 else s * (H[i] - ref))
            best = max(best, fav); peak = max(peak, fav); worst = min(worst, adv); dd = max(dd, peak - adv)
        for i in range(flat_i):
            if s != 0: mark(i)
            if M[i] not in checks or np.isnan(sg[i]): continue
            c = C[i]; ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
            if s == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif s == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if new != s:
                px = O[i + 1]; p += s * (px - ref); vol += abs(new - s); s = new; ref = px
                best = max(best, p); peak = max(peak, p); worst = min(worst, p); dd = max(dd, peak - p)
        if s != 0: mark(flat_i); p += s * (C[flat_i] - ref); vol += abs(s)
        best = max(best, p); worst = min(worst, p); dd = max(dd, max(peak, best) - p)
        pc[k] = p; pw[k] = worst; pb[k] = best; pd_[k] = dd; nt[k] = vol / 2
    return pc, pw, pb, pd_, nt


def build():
    from research_engine import WARMUP
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * 1 + 0.25) / p_ref; parts = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQX', lambda d: d > N.JOIN)):
        mk = N.market(nm); p, w, b, dd, n = day_paths(mk); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        cost = n[sel] * 2 * c
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - cost, 'r_min': np.nan_to_num(w[sel]) / px - cost,
                                   'r_max': np.nan_to_num(b[sel]) / px - cost, 'r_dd': np.nan_to_num(dd[sel]) / px, 'nt': n[sel]}, index=d[sel]))
    s = pd.concat(parts); s.index.name = 'date'
    ref = pd.read_csv(os.path.join(HERE, 'intraday_worst_extended.csv'), index_col=0, parse_dates=True)
    assert s.index.equals(ref.index), "sessions differ from intraday_worst_extended.csv"
    dc, dw = float((s.r_close - ref.r_close).abs().max()), float((s.r_min - ref.r_min).abs().max())
    assert dc < 1e-12 and dw < 1e-12, f"close/worst differ from intraday_worst_extended.csv ({dc:.2e}, {dw:.2e})"
    assert bool((s.r_max >= s.r_close - 1e-15).all()) and bool((s.r_dd >= -1e-15).all())
    usd = 58300.0
    print(f"{len(s)} sessions; close and worst identical to intraday_worst_extended.csv (max diff {max(dc, dw):.1e}); per micro at MNQ 29,150: "
          f"best point median ${(s.r_max * usd).median():,.0f}, 99th pct ${(s.r_max * usd).quantile(0.99):,.0f}; drop from the intraday peak "
          f"median ${(s.r_dd * usd).median():,.0f}, 99th pct ${(s.r_dd * usd).quantile(0.99):,.0f}, max ${(s.r_dd * usd).max():,.0f}")
    s.to_csv(os.path.join(HERE, 'intraday_paths_extended.csv'))


if __name__ == '__main__':
    build()
