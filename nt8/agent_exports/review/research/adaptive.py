"""Adaptive-tuning experiment per ADAPTIVE_PLAN.txt."""
import itertools, sys, numpy as np, pandas as pd
from research_engine import Market, fam_A, WARMUP

GRID = [dict(vm=vm, look=L, step=st, trail=tr) for vm, L, st, tr in
        itertools.product((0.5, 0.75, 1.0, 1.25, 1.5, 2.0), (7, 14, 21, 30), (15, 30, 60), ('paper', 'band', 'vwap'))]
BASE = GRID.index(dict(vm=1.0, look=14, step=30, trail='paper'))
rng = np.random.default_rng(2026)


def matrix(path, name):
    mk = Market(path, 2.0, name)
    P = np.column_stack([np.nan_to_num(fam_A(mk, **g)[0]) for g in GRID])
    P = P[WARMUP:]; dates = mk.dates[WARMUP:]
    return pd.DataFrame(P, index=dates)


def tstat(x):
    x = np.asarray(x, float); return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def metric(block, how):
    return block.apply(tstat) if how == 't' else block.sum()


def mdd(x):
    eq = np.cumsum(x); return (np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]).max()


def periods(idx, start, freq):
    """List of (period_start, period_end_exclusive) covering idx from start."""
    s = pd.Timestamp(start); out = []
    while s <= idx[-1]:
        e = (s + pd.DateOffset(months=1 if freq == 'M' else 3))
        out.append((s, e)); s = e
    return out


def scheme_A(P, start, window_m, freq, how):
    """Walk-forward re-optimisation. Returns the traded daily P&L series and the chosen variant per period."""
    out = []; choices = []
    for s, e in periods(P.index, start, freq):
        w0 = s - pd.DateOffset(months=window_m)
        hist = P[(P.index >= w0) & (P.index < s)]        # strictly before the traded period
        if len(hist) < 40: continue
        v = int(metric(hist, how).idxmax())
        blk = P[(P.index >= s) & (P.index < e)]
        out.append(blk[v]); choices.append((s, v, blk))
    return pd.concat(out), choices


def scheme_C(P, start, freq='M'):
    out = []; choices = []
    for s, e in periods(P.index, start, freq):
        hist = P[P.index < s]
        v = int(metric(hist, 't').idxmax()); blk = P[(P.index >= s) & (P.index < e)]
        out.append(blk[v]); choices.append((s, v, blk))
    return pd.concat(out), choices


def oracle(P, start, freq='Q'):
    out = []
    for s, e in periods(P.index, start, freq):
        blk = P[(P.index >= s) & (P.index < e)]
        if len(blk): out.append(blk[int(blk.sum().idxmax())])
    return pd.concat(out)


def random_control(P, start, n=2000, freq='Q'):
    pers = [(s, e) for s, e in periods(P.index, start, freq)]
    blocks = [P[(P.index >= s) & (P.index < e)] for s, e in pers]
    nets = np.empty(n); ts = np.empty(n)
    for i in range(n):
        x = pd.concat([b[int(rng.integers(0, P.shape[1]))] for b in blocks if len(b)])
        nets[i] = x.sum(); ts[i] = tstat(x)
    return nets, ts


def gating(P, start, n_sess, invert):
    base = P[BASE]; roll = base.rolling(n_sess).sum().shift(1)      # known at the previous close
    m = (roll <= 0) if invert else (roll > 0)
    x = base.where(m, 0.0); x = x[x.index >= pd.Timestamp(start)]
    return x, int(m[x.index].sum())


def rank_of_choice(choices):
    """Average percentile rank (0 = worst, 1 = best) of the chosen variant within the period it was traded."""
    r = []
    for s, v, blk in choices:
        if len(blk) < 5: continue
        tot = blk.sum(); r.append((tot < tot[v]).mean())
    return float(np.mean(r)) if r else np.nan


def run(path, name, nt_check=None):
    P = matrix(path, name)
    print(f"\n==== {name}: {P.index[0].date()} .. {P.index[-1].date()}, {len(P)} sessions, {P.shape[1]} variants ====")
    if nt_check is not None:
        got = P[BASE].sum(); print(f"check 1: fixed-rules column total ${got:,.0f} vs Analyzer-derived ${nt_check:,.0f} on the same sessions -> {'OK' if abs(got-nt_check) < 1 else 'MISMATCH'}")
    rows = []
    for start, label in (('2020-07-01', 'from 2020-07'), ('2021-07-01', 'from 2021-07')):
        fixed = P[BASE][P.index >= pd.Timestamp(start)]
        rows.append(dict(period=label, scheme='FIXED published rules', net=fixed.sum(), t=tstat(fixed), mdd=mdd(fixed.values), switches=0, rank=np.nan))
        orc = oracle(P, start); rows.append(dict(period=label, scheme='ORACLE (look-ahead ceiling)', net=orc.sum(), t=tstat(orc), mdd=mdd(orc.values), switches=np.nan, rank=np.nan))
        rn, rt = random_control(P, start); rows.append(dict(period=label, scheme='RANDOM variant each quarter (median of 2,000)', net=np.median(rn), t=np.median(rt), mdd=np.nan, switches=np.nan, rank=np.nan))
        rows[-1]['rand_pct_fixed'] = (rn < fixed.sum()).mean()
        for w in ((6, 12) if start == '2020-07-01' else (24,)):
            for freq in ('M', 'Q'):
                for how in ('t', 'net'):
                    x, ch = scheme_A(P, start, w, freq, how)
                    sw = sum(1 for i in range(1, len(ch)) if ch[i][1] != ch[i-1][1])
                    rows.append(dict(period=label, scheme=f'A: re-opt {w}m window, {"monthly" if freq=="M" else "quarterly"}, by {"t-stat" if how=="t" else "net"}',
                                     net=x.sum(), t=tstat(x), mdd=mdd(x.values), switches=sw, rank=rank_of_choice(ch), rand_pct=(rn < x.sum()).mean()))
        if start == '2020-07-01':
            x, ch = scheme_C(P, start); sw = sum(1 for i in range(1, len(ch)) if ch[i][1] != ch[i-1][1])
            rows.append(dict(period=label, scheme='C: anchored (all history), monthly, by t-stat', net=x.sum(), t=tstat(x), mdd=mdd(x.values), switches=sw, rank=rank_of_choice(ch), rand_pct=(rn < x.sum()).mean()))
            for n_ in (20, 60):
                for inv in (False, True):
                    x, days = gating(P, start, n_, inv)
                    rows.append(dict(period=label, scheme=f'B: trade only when trailing {n_}-session P&L {"<= 0" if inv else "> 0"} ({days} of {len(x)} days)', net=x.sum(), t=tstat(x), mdd=mdd(x.values), switches=np.nan, rank=np.nan))
    df = pd.DataFrame(rows)
    pd.set_option('display.width', 220)
    print(df.to_string(index=False, float_format=lambda v: f'{v:,.2f}'))
    return df


if __name__ == '__main__':
    # Analyzer-derived check value: fixed rules on NT bars, same sessions as the matrix (after the 30-day warm-up)
    tr = pd.read_csv("/mnt/user-data/uploads/NinjaTrader 8/agent_exports/NoiseArea_trades_MNQ_Backtest_20261015_143532.csv")
    tr['xt'] = pd.to_datetime(tr.exit_time_et); tr['net'] = tr.profit_currency - 1.0
    mk = Market('nt_analyzer_bars.csv', 2.0, 'NT'); first = mk.dates[WARMUP]
    chk = tr[tr.xt.dt.normalize() >= first].net.sum()
    d1 = run('nt_analyzer_bars.csv', 'NinjaTrader MNQ bars', nt_check=chk)
    d2 = run('mnq_db_5m.csv', 'Databento MNQ bars')
    d1.to_csv('adaptive_results_nt.csv', index=False); d2.to_csv('adaptive_results_db.csv', index=False)
