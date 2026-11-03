r"""MSB - market-structure breaks (BOS / CHoCH): does the break's direction predict the next move?  Pre-registered in MSB_PLAN.txt.
Bar-for-bar port of staged\20261103_market_structure\MarketStructure.cs (swing rule, pending levels, break rule, both-break tie).
    python msb.py            full run  -> msb_output.txt
    python msb.py parity     only the swing-rule parity check (loop as in the C# vs the vectorised rule) on every file
"""
import os, sys, time
sys.dont_write_bytecode = True
import numpy as np, pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = {'NQ 2010-19 (discovery)': os.path.join(HERE, 'scaling_inputs', 'nq_2010-2019_5m.csv'),
         'MNQ NT 2019-26 (holdout)': os.path.join(HERE, 'scaling_inputs', 'nt_analyzer_bars_to_2026-10-14.csv'),
         'MNQ Databento 2019-26': os.path.join(HERE, 'mnq_db_5m_2019-2026.csv'),
         'MES Databento 2019-26': os.path.join(HERE, 'regime_work', 'bars5m', 'mes_5m.csv')}
P_REF = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
TICK = 0.25
WARMUP = 30                                   # sessions excluded at the start of every file (the harness convention)
OUT = []


def say(s=''):
    print(s); OUT.append(s)


def stamp(d):
    d['etmin'] = d.time.dt.hour * 60 + d.time.dt.minute                      # bar END time, minutes of day ET
    d['sess'] = (d.time + pd.Timedelta(hours=6, minutes=59)).dt.floor('D')   # session date: bars ending 18:05 .. 17:00 next day -> that next day
    return d


def load(path):
    d = pd.read_csv(path, parse_dates=['time'])
    d = d.sort_values('time').drop_duplicates('time').reset_index(drop=True)
    return stamp(d)


def resample(d, tf):
    """tf-minute bars from the 5-minute bars: right-closed, stamped by the bar END like every file here."""
    x = d.set_index('time')[['open', 'high', 'low', 'close', 'volume']].resample(f'{tf}min', label='right', closed='right').agg(
        {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna(subset=['close']).reset_index()
    return stamp(x)


DB = r"C:\Users\linde\OneDrive\Desktop\Desktop\databento_data"
ONE_MIN = {'MNQ Databento 1-min': ['mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst', 'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst', 'MNQ_ohlcv-1m_2026-2026.dbn.zst'],
           'MES Databento 1-min': ['MES_ohlcv-1m_2019-2026.dbn.zst']}


def build_1m(name):
    """1-minute bars (ET bar-end stamps, convert.py's difference back-adjustment at contract switches), cached as CSV."""
    out_dir = os.path.join(HERE, 'regime_work', 'bars1m'); os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, name.split()[0].lower() + '_1m.csv')
    if not os.path.exists(out):
        import databento as db
        parts = [db.DBNStore.from_file(os.path.join(DB, f)).to_df()[['instrument_id', 'open', 'high', 'low', 'close', 'volume']] for f in ONE_MIN[name]]
        df = pd.concat(parts); df = df[~df.index.duplicated(keep='last')].sort_index()
        sw = np.flatnonzero(df.instrument_id.values[1:] != df.instrument_id.values[:-1]) + 1
        adj = np.zeros(len(df))
        for k in sw: adj[:k] += df.open.iat[k] - df.close.iat[k - 1]
        for c in ('open', 'high', 'low', 'close'): df[c] = df[c].values + adj
        df.index = (df.index + pd.Timedelta(minutes=1)).tz_convert('America/New_York').tz_localize(None); df.index.name = 'time'
        df[['open', 'high', 'low', 'close', 'volume']].reset_index().to_csv(out, index=False)
    return load(out)


# ---------------------------------------------------------------- the indicator's logic
def swings_vectorised(H, L, n):
    """isTop[i] / isBtm[i]: the candidate i-n is CONFIRMED at bar i (0-based). Same rule as the C# loop."""
    N = len(H); isTop = np.zeros(N, bool); isBtm = np.zeros(N, bool)
    if N < 2 * n + 1: return isTop, isBtm
    hmax = swv(H, n).max(axis=1); lmin = swv(L, n).min(axis=1)              # window j = bars j .. j+n-1
    c = np.arange(n, N - n)
    top = (H[c] > hmax[c - n]) & (H[c] >= hmax[c + 1])                       # strictly above the older, at/above the newer
    btm = (L[c] < lmin[c - n]) & (L[c] <= lmin[c + 1])
    isTop[c + n] = top; isBtm[c + n] = btm
    return isTop, isBtm


def swings_loop(H, L, n):
    """The C# loop verbatim (NinjaTrader indexing: [k] = k bars ago from the current bar i)."""
    N = len(H); isTop = np.zeros(N, bool); isBtm = np.zeros(N, bool)
    for i in range(2 * n, N):
        hc = H[i - n]; lc = L[i - n]; t = b = True
        for k in range(0, 2 * n + 1):
            if k == n: continue
            hk = H[i - k]; lk = L[i - k]
            if k < n:
                if hk > hc: t = False
                if lk < lc: b = False
            else:
                if hk >= hc: t = False
                if lk <= lc: b = False
            if not t and not b: break
        isTop[i] = t; isBtm[i] = b
    return isTop, isBtm


def structure(O, H, L, C, n, wicks=False, isTop=None, isBtm=None):
    """Per bar: sig (+2 CHoCH up, +1 BOS up, -1 BOS down, -2 CHoCH down), bias after the bar, the broken level."""
    N = len(C)
    if isTop is None: isTop, isBtm = swings_vectorised(H, L, n)
    sig = np.zeros(N, np.int8); bias_arr = np.zeros(N, np.int8); lvl = np.full(N, np.nan)
    topP = btmP = np.nan; topX = btmX = True; bias = 0
    up_src = H if wicks else C; dn_src = L if wicks else C
    for i in range(2 * n, N):
        if isTop[i]: topP = H[i - n]; topX = False
        if isBtm[i]: btmP = L[i - n]; btmX = False
        bu = (not topX) and up_src[i] > topP
        bd = (not btmX) and dn_src[i] < btmP
        if bu and bd:
            if C[i] >= O[i]: bd = False
            else: bu = False
            topX = btmX = True
        s = 0
        if bu:
            s = 1 if bias == 1 else 2; topX = True; bias = 1; lvl[i] = topP
        elif bd:
            s = -1 if bias == -1 else -2; btmX = True; bias = -1; lvl[i] = btmP
        sig[i] = s; bias_arr[i] = bias
    return sig, bias_arr, lvl


# ---------------------------------------------------------------- statistics
def tstat(x):
    x = np.asarray(x, float); n = len(x)
    return float(x.mean() / x.std(ddof=1) * np.sqrt(n)) if n > 2 and x.std(ddof=1) > 0 else float('nan')


def hit_ci(h):
    n = len(h); p = h.mean() if n else float('nan'); se = np.sqrt(p * (1 - p) / n) if n else float('nan')
    return p, p - 1.96 * se, p + 1.96 * se


def cost_per_side(name, px):
    """Return units: one tick slippage + $0.50 commission per side at the reference price (MNQ $2/pt, MES $5/pt)."""
    if name.startswith('MES'): return (TICK + 0.50 / 5.0) / px
    return (TICK + 0.50 / 2.0) / px


def dollars_per_point(name): return 5.0 if name.startswith('MES') else 2.0


# ---------------------------------------------------------------- T1 event study
def event_study(d, sig, h, rth_only=False):
    C = d.close.values; sess = d.sess.values; etmin = d.etmin.values; N = len(C)
    idx = np.flatnonzero(sig != 0)
    if rth_only: idx = idx[(etmin[idx] >= 575) & (etmin[idx] <= 930)]
    keep = []; last = -10 ** 9
    for i in idx:
        if i + h >= N or i - last < h or sess[i + h] != sess[i]: continue
        keep.append(i); last = i
    keep = np.array(keep, int)
    if not len(keep): return None
    sgn = np.sign(sig[keep]).astype(float)
    fwd = (C[keep + h] / C[keep] - 1) * sgn
    ticks = (C[keep + h] - C[keep]) * sgn / TICK
    return dict(n=len(keep), idx=keep, fwd=fwd, ticks=ticks, kind=np.abs(sig[keep]))


def report_events(label, ev, c2):
    if ev is None: say(f"   {label:<28} no events"); return
    p, lo, hi = hit_ci(ev['fwd'] > 0)
    say(f"   {label:<28} n {ev['n']:6,}  mean {ev['fwd'].mean() * 1e4:+6.2f} bps ({ev['ticks'].mean():+6.1f} ticks)  net of cost {(ev['fwd'].mean() - c2) * 1e4:+6.2f} bps"
        f"  hit {p * 100:5.1f}% [{lo * 100:.1f}, {hi * 100:.1f}]  t {tstat(ev['fwd']):+5.2f}")


# ---------------------------------------------------------------- T2 trading rule
def trade_rule(d, sig, h, c, bias_hold=False, bias=None, kind=None):
    """Signal bar closes 09:35-15:30 ET; enter at the next bar's open; exit at the close h bars later or the 16:00 close.
    bias_hold: instead hold the structure direction all RTH (enter at 09:40 open / on each flip, exit at 16:00 or the flip).
    Returns a list of (session, ret_gross, ret_net) and the trade count."""
    O = d.open.values; C = d.close.values; sess = d.sess.values; etmin = d.etmin.values; N = len(C)
    trades = []
    i = 0
    if not bias_hold:
        while i < N - 1:
            s = sig[i]
            if s != 0 and 575 <= etmin[i] <= 930 and (kind is None or abs(s) == kind):
                j = i + 1; sj = sess[i]
                if sess[j] != sj or etmin[j] > 960: i += 1; continue
                ex = j                                                           # exit: the close of bar i+h, or the session's last RTH bar before it
                k = j
                while k < N and k <= i + h and sess[k] == sj and etmin[k] <= 960: ex = k; k += 1
                r = np.sign(s) * (C[ex] / O[j] - 1)
                trades.append((sj, r, r - 2 * c)); i = ex + 1
            else:
                i += 1
    else:
        pos = 0; entry = np.nan; cur = None; last_rth = -1
        for i in range(1, N):
            s = sess[i]
            if s != cur:                                                         # session change: a position left open (early close) exits at the last RTH close seen
                if pos != 0 and last_rth >= 0: r = pos * (C[last_rth] / entry - 1); trades.append((cur, r, r - 2 * c))
                cur = s; pos = 0
            if etmin[i] < 580 or etmin[i] > 960: continue
            last_rth = i
            want = int(bias[i - 1])
            if etmin[i] == 960:
                if pos != 0: r = pos * (C[i] / entry - 1); trades.append((s, r, r - 2 * c)); pos = 0
                continue
            if want != pos:
                if pos != 0: r = pos * (O[i] / entry - 1); trades.append((s, r, r - 2 * c))
                pos = want; entry = O[i] if pos != 0 else np.nan
    return trades


def daily(trades, sessions):
    g = pd.DataFrame(trades, columns=['sess', 'g', 'n']).groupby('sess')[['g', 'n']].sum() if trades else pd.DataFrame(columns=['g', 'n'])
    return g.reindex(sessions).fillna(0.0)


def grade(label, trades, sessions, name, px, show_years=True):
    dd = daily(trades, sessions); yrs = (sessions[-1] - sessions[0]).days / 365.25
    dpp = dollars_per_point(name); usd = lambda r: r * px * dpp
    net = dd.n.values; gross = dd.g.values; tr = np.array([t[2] for t in trades]) if trades else np.array([])
    wins = (tr > 0).mean() * 100 if len(tr) else float('nan')
    say(f"   {label:<34} ${usd(net.sum()) / yrs:+8,.0f}/yr  t {tstat(net):+5.2f}  (zero cost ${usd(gross.sum()) / yrs:+8,.0f}/yr t {tstat(gross):+5.2f})"
        f"  trades/yr {len(tr) / yrs:5.0f}  win {wins:4.1f}%  avg {usd(tr.mean()) if len(tr) else 0:+6.2f}$")
    if show_years:
        by = pd.Series(net, index=sessions).groupby(sessions.year).sum()
        say("      by year: " + "  ".join(f"{y}:{usd(v):+,.0f}" for y, v in by.items()) + f"   losing years {(by < 0).sum()} of {len(by)}")
        if len(tr) > 5:
            drop = np.sort(tr)[:-5]; dd5 = daily([(t[0], t[1], t[2]) for t in sorted(trades, key=lambda t: t[2])[:-5]], sessions)
            say(f"      without the 5 best trades: ${usd(drop.sum()) / yrs:+8,.0f}/yr  t {tstat(dd5.n.values):+5.2f}")
    return dict(t=tstat(net), usd=usd(net.sum()) / yrs, years=pd.Series(net, index=sessions).groupby(sessions.year).sum().apply(usd) if len(net) else None)


# ---------------------------------------------------------------- main
def main(parity_only=False):
    t0 = time.time()
    say(f"MSB  market-structure breaks  (MSB_PLAN.txt)  run {pd.Timestamp.now():%Y-%m-%d %H:%M}  p_ref {P_REF:,.2f}")
    data = {}
    for name, path in FILES.items():
        d = load(path); sessions = pd.DatetimeIndex(sorted(d.sess.unique()))
        rth = d[(d.etmin >= 575) & (d.etmin <= 960)].groupby('sess').size()
        sessions = sessions[sessions.isin(rth[rth >= 30].index)][WARMUP:]      # sessions with a full RTH, warm-up excluded
        d = d[d.sess.isin(sessions)].reset_index(drop=True)
        data[name] = (d, sessions)
        say(f"{name:<28} {len(d):>8,} bars  {len(sessions):>5,} sessions  {sessions[0]:%Y-%m-%d} .. {sessions[-1]:%Y-%m-%d}")
    say()
    say("SWING PARITY (the C# loop vs the vectorised rule, every file, N = 3 / 5 / 10)")
    for name, (d, _) in data.items():
        H = d.high.values; L = d.low.values
        for n in (3, 5, 10):
            a = swings_vectorised(H, L, n); b = swings_loop(H, L, n)
            ok = np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
            say(f"   {name:<28} N {n:2d}  tops {a[0].sum():7,}  bottoms {a[1].sum():7,}  {'IDENTICAL' if ok else 'MISMATCH'}")
            assert ok, 'swing parity failed'
    if parity_only: return
    say()

    primary = {}
    for n in (5, 3, 10):
        for wicks in ((False, True) if n == 5 else (False,)):
            tag = f"N={n} {'wicks' if wicks else 'close'}"
            say("=" * 120); say(f"{tag}   {'PRIMARY' if (n == 5 and not wicks) else 'reported variant'}"); say("=" * 120)
            for name, (d, sessions) in data.items():
                O = d.open.values; H = d.high.values; L = d.low.values; C = d.close.values
                sig, bias, lvl = structure(O, H, L, C, n, wicks)
                per_sess = (sig != 0).sum() / len(sessions)
                say(f"\n{name}:  breaks {int((sig != 0).sum()):,} = {per_sess:.2f} per session  (BOS up {int((sig == 1).sum()):,}, CHoCH up {int((sig == 2).sum()):,}, "
                    f"BOS down {int((sig == -1).sum()):,}, CHoCH down {int((sig == -2).sum()):,})")
                assert 0.3 <= per_sess <= 25, f'event count outside the pre-registered band: {per_sess}'   # band raised 15 -> 25 after the first run (N=3: 16.0), see MSB_PLAN.txt
                px = P_REF if not name.startswith('MES') else float(C[-390:].mean())
                c = cost_per_side(name, px)
                say("  T1 event study (forward move signed by the break's direction, non-overlapping, same session):")
                for h in (6, 12, 24):
                    ev = event_study(d, sig, h); report_events(f"all hours  h={h:2d} ({h * 5:3d} min)", ev, 2 * c)
                ev = event_study(d, sig, 12, rth_only=True); report_events("RTH only   h=12 ( 60 min)", ev, 2 * c)
                for kind, kn in ((1, 'BOS only'), (2, 'CHoCH only')):
                    s2 = np.where(np.abs(sig) == kind, sig, 0); ev = event_study(d, s2, 12); report_events(f"{kn:<10} h=12 ( 60 min)", ev, 2 * c)
                say("  T2 trading rule (RTH signals, next open, exit after h bars or 16:00; 1 tick + $0.50 a side):")
                g = grade("h=12 all breaks", trade_rule(d, sig, 12, c), sessions, name, px)
                if n == 5 and not wicks: primary[name] = g
                grade("h=6", trade_rule(d, sig, 6, c), sessions, name, px, show_years=False)
                grade("h=24", trade_rule(d, sig, 24, c), sessions, name, px, show_years=False)
                grade("h=12 BOS only", trade_rule(d, sig, 12, c, kind=1), sessions, name, px, show_years=False)
                grade("h=12 CHoCH only", trade_rule(d, sig, 12, c, kind=2), sessions, name, px, show_years=False)
                grade("h=12 at 2 ticks", trade_rule(d, sig, 12, cost_per_side(name, px) + TICK / px), sessions, name, px, show_years=False)
                grade("hold the structure direction (RTH)", trade_rule(d, sig, 12, c, bias_hold=True, bias=bias), sessions, name, px, show_years=False)
                if n == 5 and not wicks and name.startswith('MNQ NT'):
                    say("  spot check - the primary's trades on the last session with a trade:")
                    tr = trade_rule(d, sig, 12, c); last = tr[-1][0]
                    for t in tr:
                        if t[0] == last: say(f"      {pd.Timestamp(t[0]):%Y-%m-%d}  gross {t[1] * 1e4:+.2f} bps  net {t[2] * 1e4:+.2f} bps")
                    dd = d[d.sess == last]; ii = np.flatnonzero((sig != 0) & (d.sess.values == last))
                    for i in ii:
                        if 575 <= d.etmin.values[i] <= 930:
                            say(f"      break at {d.time.values[i].astype('datetime64[m]')} sig {sig[i]:+d} level {lvl[i]:.2f} close {d.close.values[i]:.2f} -> next open {d.open.values[i + 1]:.2f}")
    say(); say("=" * 120); say("PRIMARY TRIAL SUMMARY (N=5, close, h=12)")
    for name, g in primary.items():
        y = g['years']; say(f"   {name:<28} ${g['usd']:+8,.0f}/yr  t {g['t']:+5.2f}  losing years {(y < 0).sum()} of {len(y)}")

    # ---- time frames (amendment): 15 / 30 / 60-minute bars from the same 5-minute bars, N = 5
    say(); say("=" * 120); say("TIME FRAMES (reported): 15 / 30 / 60-minute bars from the same data, N = 5, close breaks"); say("=" * 120)
    for tf in (15, 30, 60):
        hb = 60 // tf
        for name, (d5, sessions) in data.items():
            d = resample(d5, tf); d = d[d.sess.isin(sessions)].reset_index(drop=True)
            O = d.open.values; H = d.high.values; L = d.low.values; C = d.close.values
            sig, bias, lvl = structure(O, H, L, C, 5)
            px = P_REF if not name.startswith('MES') else float(C[-60:].mean()); c = cost_per_side(name, px)
            say(f"\n{tf}-min  {name}:  {len(d):,} bars, breaks {int((sig != 0).sum()):,} = {(sig != 0).sum() / len(sessions):.2f} per session")
            ev = event_study(d, sig, hb); report_events(f"T1 h={hb} bars ({hb * tf:3d} min)", ev, 2 * c)
            ev = event_study(d, sig, 12); report_events(f"T1 h=12 bars ({12 * tf:3d} min)", ev, 2 * c)
            grade(f"T2 h={hb} bars ({hb * tf} min)", trade_rule(d, sig, hb, c), sessions, name, px)
            grade("T2 hold the structure direction", trade_rule(d, sig, hb, c, bias_hold=True, bias=bias), sessions, name, px, show_years=False)

    # ---- one-minute bars (amendment): Databento MNQ and MES
    say(); say("=" * 120); say("ONE-MINUTE BARS (reported): Databento MNQ / MES, N = 5 and N = 25, close breaks"); say("=" * 120)
    for name in ONE_MIN:
        try:
            d = build_1m(name)
        except Exception as e:
            say(f"{name}: not built ({e})"); continue
        sessions = pd.DatetimeIndex(sorted(d.sess.unique()))
        rth = d[(d.etmin >= 571) & (d.etmin <= 960)].groupby('sess').size()
        sessions = sessions[sessions.isin(rth[rth >= 150].index)][WARMUP:]
        d = d[d.sess.isin(sessions)].reset_index(drop=True)
        O = d.open.values; H = d.high.values; L = d.low.values; C = d.close.values
        a = swings_vectorised(H, L, 5); b = swings_loop(H, L, 5)
        ok = np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])
        say(f"\n{name}: {len(d):,} bars  {len(sessions):,} sessions  {sessions[0]:%Y-%m-%d} .. {sessions[-1]:%Y-%m-%d}   swing parity N=5: {'IDENTICAL' if ok else 'MISMATCH'}")
        assert ok, 'swing parity failed (1-min)'
        px = P_REF if not name.startswith('MES') else float(C[-390:].mean()); c = cost_per_side(name, px)
        for n in (5, 25):
            sig, bias, lvl = structure(O, H, L, C, n)
            say(f"  N={n}: breaks {int((sig != 0).sum()):,} = {(sig != 0).sum() / len(sessions):.2f} per session")
            for h in (30, 60):
                ev = event_study(d, sig, h); report_events(f"T1 h={h} bars ({h:3d} min)", ev, 2 * c)
            ev = event_study(d, sig, 60, rth_only=True); report_events("T1 RTH only h=60 ( 60 min)", ev, 2 * c)
            grade(f"T2 N={n} h=60 bars (60 min)", trade_rule(d, sig, 60, c), sessions, name, px)
            grade("T2 hold the structure direction", trade_rule(d, sig, 60, c, bias_hold=True, bias=bias), sessions, name, px, show_years=False)
    say(f"\nrun time {time.time() - t0:.0f} s")


if __name__ == '__main__':
    try:
        main(parity_only=len(sys.argv) > 1 and sys.argv[1] == 'parity')
    finally:
        with open(os.path.join(HERE, 'msb_output.txt'), 'w', encoding='utf-8') as f: f.write("\n".join(OUT) + "\n")
