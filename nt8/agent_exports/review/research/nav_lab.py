r"""NoiseArea variant lab (2026-10-20): the published rules with optional modifications, graded on the same 16-year joined series as
regime_test.joined (NQ 2010-2019 5-min + MNQ NinjaTrader bars 2019-2026; MNQ Databento bars as the second source), today's costs.
    python nav_lab.py check     reproduce regime_test.joined()'s NoiseArea column (must be identical) and the long-only figures
Modifications are switched on by keyword in fam_A2; with none set, fam_A2 == research_engine.fam_A(trail='paper').
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd

FILES = {'NQ': os.path.join(HERE, 'scaling_inputs', 'nq_2010-2019_5m.csv'),
         'MNQ': os.path.join(HERE, 'scaling_inputs', 'nt_analyzer_bars_to_2026-10-14.csv'),
         'MNQDB': os.path.join(HERE, 'mnq_db_5m_2019-2026.csv')}
BARS = os.path.join(HERE, 'regime_work', 'bars5m'); DB = r"C:\Users\linde\OneDrive\Desktop\Desktop\databento_data"
OTHER = {'ES': 'ES_ohlcv-1m_2010-2019.dbn.zst', 'MES': 'MES_ohlcv-1m_2019-2026.dbn.zst', 'MYM': 'MYM_ohlcv-1m_2019-2026.dbn.zst',
         'M2K': 'M2K_ohlcv-1m_2019-2026.dbn.zst'}
JOIN = pd.Timestamp('2019-05-05')
_MK = {}


def market(name):
    from research_engine import Market
    if name not in _MK:
        path = FILES.get(name)
        if path is None:
            os.makedirs(BARS, exist_ok=True); path = os.path.join(BARS, f'{name.lower()}_5m.csv')
            if not os.path.exists(path):
                from convert import build
                build([os.path.join(DB, OTHER[name])], path)
        _MK[name] = Market(path, 1.0, name)
    return _MK[name]


def fam_A2(mk, vm=1.0, look=14, step=30, first=600, last=930, direction='both', **mod):
    """research_engine.fam_A (trail='paper', slip 0, commission 0) plus optional modifications in `mod` (see MODS)."""
    sig = mk.sigma(look); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int)
    vm_l = mod.get('vm_long', vm); vm_s = mod.get('vm_short', vm)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; n_entries = 0; held = 0; best = 0.0
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        for i in range(flat_i):
            if M[i] not in checks or np.isnan(sg[i]): continue
            c = C[i]; ub = hi * (1 + vm_l * sg[i]); lb = lo * (1 - vm_s * sg[i])
            if pos == 0:
                new = 1 if c > ub else (-1 if c < lb else 0)
                if new != 0 and mod.get('max_entries') is not None and n_entries >= mod['max_entries']: new = 0
            elif pos == 1:
                if c < lb: new = -1
                else: new = 0 if c < max(ub, vw[i]) else 1
            else:
                if c > ub: new = 1
                else: new = 0 if c > min(lb, vw[i]) else -1
            if pos != 0 and new == pos:                                   # still holding: optional exits
                held += 1; best = max(best, (c - entry) * pos)
                if mod.get('time_stop') is not None and held >= mod['time_stop'] and (c - entry) * pos <= 0: new = 0
                if mod.get('breakeven') is not None and best >= mod['breakeven'] * sg[i] * O[0] and (c - entry) * pos <= 0: new = 0
            if direction == 'long' and new == -1: new = 0
            if direction == 'short' and new == 1: new = 0
            if new != pos:
                px = O[i + 1]
                if pos != 0: p += (px - entry) * pos
                if new != 0: entry = px; n_ += 1; n_entries += 1; held = 0; best = 0.0
                pos = new
        if pos != 0: p += (C[flat_i] - entry) * pos
        pnl[k] = p; nt[k] = n_
    return pnl, nt


_EMA, _RV = {}, {}


def _ema20(mk):
    """EMA(20) of the 5-minute RTH closes, carried across sessions, as a list of per-day arrays."""
    if mk.name not in _EMA:
        a = 2 / 21; e = None; out = []
        for x in mk.days:
            arr = np.empty(x.nb)
            for i, c in enumerate(x.C):
                e = c if e is None else e + a * (c - e); arr[i] = e
            out.append(arr)
        _EMA[mk.name] = out
    return _EMA[mk.name]


def _shock(mk, L=14):
    """g[k, slot] = min(1, sqrt(H / RV)): RV = cumulative squared 5-minute log returns since the open (first bar vs the 09:30 open),
    H = median RV at that slot over the previous L sessions."""
    if mk.name not in _RV:
        from research_engine import NS
        RV = np.full((mk.n, NS), np.nan)
        for k, x in enumerate(mk.days):
            r = np.diff(np.log(np.r_[x.O[0], x.C])); RV[k, x.slot] = np.cumsum(r * r)
        G = np.full((mk.n, NS), np.nan)
        for k in range(L, mk.n):
            H = np.nanmedian(RV[k - L:k], axis=0)
            with np.errstate(invalid='ignore', divide='ignore'): G[k] = np.minimum(1.0, np.sqrt(H / RV[k]))
        _RV[mk.name] = G
    return _RV[mk.name]


LOSS_PTS = 1.0     # DAYCAP: a closed trade is a loser when it made less than the modelled round trip (1 tick + $0.50 a side = 1.0 pt)


def fam_V(mk, vm=1.0, look=14, step=30, first=600, last=930, direction='both', w_long=1.0, w_short=1.0, no_gap=False, lunch=False,
          ema=False, fast=False, gate=0, shock=None, cap=None, stop_losses=None, no_relose=False):
    """Bar-by-bar NoiseArea with the NAVAR_PLAN switches (V1 direction='long', V2 w_short=0.5, V3 look=90, V4 fast, V5 gate=4,
    V6 lunch, V7 ema, V8 shock='frac'|'steps2', V9 no_gap) and the DAYCAP_PLAN ones (C1/C2 cap=1|2, C3 stop_losses=2, C4 no_relose).
    Mark-to-market P&L in points x size; returns (pnl, traded/2) where traded = sum of |size changes| incl. the final flat (so the
    base's 'trades' equals traded/2). Defaults == fam_A2 == fam_A."""
    sig = mk.sigma(look); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n)
    E = _ema20(mk) if ema else None; G = _shock(mk) if shock else None
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb = x.O, x.C, x.M, x.vw, x.nb
        hi, lo = (O[0], O[0]) if no_gap else (max(O[0], mk.pc[k]), min(O[0], mk.pc[k])); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        s = 0.0; ref = 0.0; p = 0.0; vol = 0.0; pend = None; viol = 0; d = 0          # d = direction of the position (+1/-1/0)
        t_in = 0.0; opened = 0; losses = 0; blocked = set()                           # DAYCAP bookkeeping (entry fill, counts)

        def fill(target, px):
            nonlocal s, ref, p, vol, d, t_in, opened, losses
            nd = int(np.sign(target))
            if d != 0 and nd != d and (px - t_in) * d < LOSS_PTS: losses += 1; blocked.add(d)
            if nd != 0 and nd != d: t_in = px; opened += 1
            p += s * (px - ref); vol += abs(target - s); s = target; ref = px; d = nd

        def size_for(dr, i):
            if dr == 0: return 0.0
            base = w_long if dr > 0 else w_short
            if shock == 'frac': return dr * base * (G[k, x.slot[i]] if np.isfinite(G[k, x.slot[i]]) else 1.0)
            if shock == 'steps2':
                g = G[k, x.slot[i]] if np.isfinite(G[k, x.slot[i]]) else 1.0
                return dr * (2.0 if g >= 0.75 else (1.0 if g >= 0.25 else 0.0))
            return dr * base

        for i in range(flat_i):
            ub = hi * (1 + vm * sg[i]) if not np.isnan(sg[i]) else np.nan; lb = lo * (1 - vm * sg[i]) if not np.isnan(sg[i]) else np.nan
            c = C[i]
            # V5: persistence gate on every bar while in a position
            if gate and d != 0 and not np.isnan(sg[i]):
                bad = (c < max(ub, vw[i])) if d > 0 else (c > min(lb, vw[i]))
                viol = viol + 1 if bad else 0
                if viol >= gate: fill(0.0, O[i + 1]); viol = 0; continue
            is_check = M[i] in checks and not np.isnan(sg[i])
            if not is_check:
                if fast and pend is not None:                                        # V4: wait for a counter bar
                    buy = pend > s
                    if (buy and C[i] < O[i]) or ((not buy) and C[i] > O[i]): fill(pend, O[i + 1]); pend = None
                continue
            if d == 0:
                new = 1 if c > ub else (-1 if c < lb else 0)
                if lunch and M[i] in (720, 750, 780): new = 0
            elif gate:
                new = d                                                              # V5: exits only through the gate
            elif d == 1:
                new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else:
                new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if direction == 'long' and new == -1: new = 0
            if direction == 'short' and new == 1: new = 0
            if ema and new != 0 and new != d:
                if (new == 1 and not c > E[k][i]) or (new == -1 and not c < E[k][i]): new = 0
            if new != 0 and new != d:                                                # DAYCAP: a blocked new leg becomes flat
                if (cap is not None and opened >= cap) or (stop_losses is not None and losses >= stop_losses) or \
                        (no_relose and new in blocked): new = 0
            if new == d and d != 0 and shock:
                tgt = d * min(abs(s), abs(size_for(d, i)))                          # V8: shrink only
            elif new == d:
                tgt = s
            else:
                tgt = size_for(new, i)
            if fast:
                pend = None if tgt == s else tgt
                continue
            if tgt != s: fill(tgt, O[i + 1])
        if s != 0: fill(0.0, C[flat_i])
        pnl[k] = p; nt[k] = vol / 2
    return pnl, nt


def series(second='MNQ', fn=fam_A2, ticks=1, **kw):
    """Joined NQ (<= 2019-05-05) + `second` daily net return series at today's cost, exactly as regime_test.joined builds its columns."""
    from research_engine import WARMUP
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * ticks + 0.25) / p_ref                     # per side: ticks x 0.25 pt slippage + $0.50 commission (0.25 pt) on MNQ
    parts = []
    for nm, keep in (('NQ', lambda d: d <= JOIN), (second, lambda d: d > JOIN)):
        mk = market(nm); p, n = fn(mk, **kw); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days])
        px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c, 'nt': n[sel]}, index=d[sel]))
    s = pd.concat(parts); return s.r, s.nt


def single(name, fn=fam_A2, a=None, b=None, **kw):
    """One market's daily gross return per session (points / 09:30 open), and trades, for the second-instrument checks."""
    mk = market(name); p, n = fn(mk, **kw); o = np.array([x.O[0] for x in mk.days])
    s = pd.DataFrame({'g': np.nan_to_num(p) / o, 'nt': n}, index=mk.dates)
    if a: s = s[s.index >= pd.Timestamp(a)]
    if b: s = s[s.index <= pd.Timestamp(b)]
    return s


if __name__ == '__main__':
    if sys.argv[1:2] == ['check']:
        import regime_test as rt
        dts, R, NT, *_ = rt.joined('MNQ'); ref = pd.Series(R[:, rt.A_DEF], index=dts)
        r, nt = series('MNQ')
        both = ref.index.intersection(r.index)
        print(f"baseline: lab {len(r)} sessions, regime_test {len(ref)}, common {len(both)}, max |diff| {(r[both] - ref[both]).abs().max():.3e}, "
              f"trades {int(nt.sum())} vs {int(NT[:, rt.A_DEF].sum())}")
        rv, ntv = series('MNQ', fn=fam_V)
        print(f"fam_V with no switch vs fam_A2: max |diff| {(rv - r).abs().max():.3e}, trades {ntv.sum():.1f} vs {int(nt.sum())}")
        rl, ntl = series('MNQ', direction='long')
        for lab, x in (('both', r), ('long', rl)):
            eq = np.cumsum(x.values) * 55751; dd = (np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()
            print(f"   {lab}: ${x.mean() * 252 * 55751:,.0f}/yr, Sharpe {x.mean() / x.std() * np.sqrt(252):.2f}, maxDD ${dd:,.0f}")
    else:
        print(__doc__)
