"""
Day-level simulator for the overnight-range fade, with variant switches.
Base settings reproduce sim.run(fade_on only, hold-to-close, stop 1.5 ATR, 2 bars, <=11:30, 1 ct, $150 budget).

All decisions use only information available at the bar close where they are made.
Fills: market entry at next bar open +1 tick; stop-market exit at stop -1 tick; time exit at bar close -1 tick;
limit targets at the limit price; a bar touching stop and target counts as stop.
"""
import numpy as np, pandas as pd
from dataclasses import dataclass, replace, asdict
from sim import load

TICK, PV = 0.25, 2.0

@dataclass(frozen=True)
class V:
    # ---- range ----
    range_def: str = "on"          # on | on_pure (excl. first RTH bar) | london | asia | pdhl | on_or_pdhl
    # ---- entry ----
    n_out: int = 2                 # consecutive closes outside before signal
    first_entry: int = 9*60+35
    last_entry: int = 11*60+30
    entry: str = "market"          # market | limit_better | reclaim
    limit_atr: float = 0.25        # limit_better: improve by x*ATR
    limit_bars: int = 3            # limit_better: bars the limit stays working
    min_exc_atr: float = 0.0       # require close at least x*ATR beyond the edge
    max_exc_atr: float = 99.0      # skip if excursion already beyond x*ATR (runaway)
    rearm: bool = True             # side re-arms when price returns inside the range
    max_per_day: int = 3
    direction: str = "both"        # both | long | short
    # ---- stop / sizing ----
    stop_atr: float = 1.5
    min_stop_ticks: int = 20
    max_stop_pts: float = 75.0     # $150 risk budget at 1 MNQ = 75 pts
    max_stop_pct: float = 0.0      # >0: skip if stop distance > x% of price (scale-free version of the cap)
    # ---- exit ----
    exit: str = "close"            # close | T1 | T2 | Rmult | be_t1 | trail
    exit_time: int = 15*60+55
    r_mult: float = 3.0
    trail_atr: float = 3.0
    trail_after_r: float = 1.0
    # ---- day / signal filters ----
    vol_regime: str = "any"        # any | low | mid | high   (prior RTH range vs 20-day avg, terciles)
    on_width: str = "any"          # any | narrow | wide      (ON range / ATR at open, median split)
    open_loc: str = "any"          # any | inside | outside   (RTH open vs pure ON range)
    trend: str = "any"             # any | with | against     (5-min EMA50 vs EMA200 at signal)
    vwap_filter: str = "any"       # any | far                (signal close > 1 ATR from session VWAP)
    first_bar_max_atr: float = 99.0  # skip day if 9:35 bar range > x*ATR (news-day proxy)
    dow_skip: tuple = ()           # e.g. (0,) skip Mondays
    # ---- multi-timeframe / context ----
    tf: int = 5                    # count closes outside on 5 / 15 / 30-minute closes
    htf: str = "any"               # any | with | against  (5-min close vs EMA(240) ~ 60-min EMA20)
    daily_trend: str = "any"       # any | with | against  (prior RTH close vs 20-day SMA of RTH closes)
    on_ret: str = "any"            # any | reversal | continuation (fade only against / with the overnight move)
    on_ret_def: str = "gap"        # gap = RTH open vs prior RTH close ; session = 9:30 close vs 18:00 open
    on_ret_min_atr: float = 0.0    # minimum size of the overnight move (ATR units) for it to count
    confluence: float = 0.0        # >0: ON edge must be within x*ATR of prior-day H/L on the same side
    candle: bool = False           # signal bar must close in the half of its range pointing back inside
    # ---- costs ----
    slip_ticks: int = 1
    comm_side: float = 0.50
    pv: float = 2.0                # $ per point: MNQ 2, MES 5


def prepare(path='bars.csv'):
    df = load(path)
    df['ema50'] = df.close.ewm(span=50, adjust=False).mean()
    df['ema200'] = df.close.ewm(span=200, adjust=False).mean()
    df['ema240'] = df.close.ewm(span=240, adjust=False).mean()
    days = []
    rth_hist = []   # (high, low, close) of previous RTH sessions
    for td, g in df.groupby('tday', sort=True):
        g = g.reset_index(drop=True)
        r = g[g.in_rth.values]
        if len(r) < 20:
            continue
        first = r.index[0]
        pre = g.iloc[:first]                       # everything before the first RTH bar
        if len(pre) < 10:
            continue
        et = pre.etmin.values
        d = dict(tday=td, g=g, first=first)
        d['on_pure'] = (pre.high.max(), pre.low.min())
        d['on'] = (max(pre.high.max(), g.high[first]), min(pre.low.min(), g.low[first]))
        lon = pre[(et > 180) & (et <= 480)]
        d['london'] = (lon.high.max(), lon.low.min()) if len(lon) else (np.nan, np.nan)
        asia = pre[(et > 17*60) | (et <= 120)]
        d['asia'] = (asia.high.max(), asia.low.min()) if len(asia) else (np.nan, np.nan)
        d['prior_close'] = rth_hist[-1][2] if rth_hist else np.nan
        closes = [c for _, _, c in rth_hist[-20:]]
        d['dtrend'] = (1 if rth_hist[-1][2] > np.mean(closes) else -1) if len(closes) >= 10 else 0
        a_ = g.atr[first]
        d['gap_atr'] = (g.open[first] - rth_hist[-1][2]) / a_ if (rth_hist and a_ > 0) else 0.0
        d['sess_atr'] = (g.close[first - 1] - g.open[0]) / a_ if a_ > 0 else 0.0
        if rth_hist:
            ph, pl, pc = rth_hist[-1]
            d['pdhl'] = (ph, pl)
            d['on_or_pdhl'] = (max(ph, d['on'][0]), min(pl, d['on'][1]))
            rngs = [h - l for h, l, _ in rth_hist[-20:]]
            d['prior_range_ratio'] = (ph - pl) / np.mean(rngs) if len(rngs) >= 5 else np.nan
        else:
            d['pdhl'] = d['on_or_pdhl'] = (np.nan, np.nan)
            d['prior_range_ratio'] = np.nan
        a0 = g.atr[first]
        d['on_width_atr'] = (d['on_pure'][0] - d['on_pure'][1]) / a0 if a0 > 0 else np.nan
        o = g.open[first]
        d['open_inside'] = d['on_pure'][1] <= o <= d['on_pure'][0]
        d['first_bar_atr'] = (g.high[first] - g.low[first]) / a0 if a0 > 0 else np.nan
        d['dow'] = pd.Timestamp(td).dayofweek
        # session VWAP from first RTH bar
        tp = (g.high + g.low + g.close) / 3
        v = g.volume.clip(lower=0)
        vw = np.full(len(g), np.nan)
        cpv = cv = 0.0
        for i in range(first, len(g)):
            cpv += tp[i] * v[i]; cv += v[i]
            vw[i] = cpv / cv if cv > 0 else g.close[i]
        d['vwap'] = vw
        days.append(d)
        rth_hist.append((r.high.max(), r.low.min(), r.close.iloc[-1]))
    # tercile / median cut points (fixed over the whole sample: a mild look-ahead, flagged in the report)
    prr = np.array([d['prior_range_ratio'] for d in days], float)
    ow = np.array([d['on_width_atr'] for d in days], float)
    q1, q2 = np.nanpercentile(prr, [33.3, 66.7]); owm = np.nanmedian(ow)
    for d in days:
        x = d['prior_range_ratio']
        d['vol'] = 'na' if np.isnan(x) else ('low' if x < q1 else 'mid' if x < q2 else 'high')
        d['onw'] = 'na' if np.isnan(d['on_width_atr']) else ('narrow' if d['on_width_atr'] < owm else 'wide')
    return days


def rt(x): return round(x / TICK) * TICK


def run_day(d, v: V):
    g = d['g']; O, H, L, C, A = g.open.values, g.high.values, g.low.values, g.close.values, g.atr.values
    ET, RTH = g.etmin.values, g.in_rth.values
    E50, E200, VW, E240 = g.ema50.values, g.ema200.values, d['vwap'], g.ema240.values
    hi, lo = d[v.range_def]
    trades = []
    if np.isnan(hi) or np.isnan(lo) or hi <= lo:
        return trades
    # day filters
    if v.vol_regime != 'any' and d['vol'] != v.vol_regime: return trades
    if v.on_width != 'any' and d['onw'] != v.on_width: return trades
    if v.open_loc == 'inside' and not d['open_inside']: return trades
    if v.open_loc == 'outside' and d['open_inside']: return trades
    if d['first_bar_atr'] > v.first_bar_max_atr: return trades
    if d['dow'] in v.dow_skip: return trades

    slip = v.slip_ticks * TICK
    n = 0; ext = 0.0; armL = armS = True; plans = 0
    pending = None; pos = None
    mid = (hi + lo) / 2
    for i in range(d['first'], len(g)):
        if not RTH[i] and pos is None and pending is None:
            break
        # pending entry
        if pending is not None and pos is None:
            pdir = pending['dir']
            if pending['type'] == 'market':
                fill = O[i] + slip * pdir; ok = True
            else:  # limit_better
                lim = pending['limit']
                if pdir > 0 and L[i] <= lim: fill, ok = min(O[i], lim), True
                elif pdir < 0 and H[i] >= lim: fill, ok = max(O[i], lim), True
                else: ok = False
            if ok:
                pos = dict(dir=pdir, entry=fill, stop=pending['stop'], risk=abs(pending['plan_entry'] - pending['stop']),
                           t1=lo if pdir > 0 else hi, bar=i, best=fill, t1hit=False, kind=pending['kind'])
                pending = None
            else:
                pending['left'] -= 1
                if pending['left'] <= 0: pending = None
        # manage
        if pos is not None:
            dd = pos['dir']; px = None; how = None
            stop_hit = L[i] <= pos['stop'] if dd > 0 else H[i] >= pos['stop']
            if i == pos['bar'] and ((dd > 0 and O[i] <= pos['stop']) or (dd < 0 and O[i] >= pos['stop'])): stop_hit = True
            tgt = None
            if v.exit == 'T1': tgt = pos['t1']
            elif v.exit == 'T2': tgt = mid
            elif v.exit == 'Rmult': tgt = pos['entry'] + dd * v.r_mult * pos['risk']
            tgt_hit = tgt is not None and ((H[i] >= tgt) if dd > 0 else (L[i] <= tgt))
            if stop_hit: px, how = pos['stop'] - slip * dd, 'stop'
            elif tgt_hit: px, how = tgt, 'target'
            elif ET[i] >= v.exit_time or not RTH[i]: px, how = C[i] - slip * dd, 'time'
            if px is None:
                t1_hit = (H[i] >= pos['t1']) if dd > 0 else (L[i] <= pos['t1'])
                if t1_hit and not pos['t1hit']:
                    pos['t1hit'] = True
                    if v.exit == 'be_t1': pos['stop'] = pos['entry'] if dd > 0 else pos['entry']
                if v.exit == 'trail':
                    pos['best'] = max(pos['best'], C[i]) if dd > 0 else min(pos['best'], C[i])
                    if (pos['best'] - pos['entry']) * dd >= v.trail_after_r * pos['risk']:
                        ns = pos['best'] - dd * v.trail_atr * A[pos['bar']]
                        pos['stop'] = max(pos['stop'], ns) if dd > 0 else min(pos['stop'], ns)
            else:
                pts = (px - pos['entry']) * dd
                trades.append(dict(tday=d['tday'], dir=dd, entry=pos['entry'], exit=px, how=how, pts=pts,
                                   R=pts / pos['risk'] if pos['risk'] > 0 else 0.0, risk=pos['risk'],
                                   net=pts * v.pv - 2 * v.comm_side, kind=pos['kind']))
                pos = None
        busy = pos is not None or pending is not None
        # excursion state (always updated in RTH)
        reclaim = 0; reclaim_ext = 0.0
        tf_close = v.tf == 5 or ET[i] % v.tf == 0
        if not tf_close:
            if n > 0: ext = max(ext, H[i])
            elif n < 0: ext = min(ext, L[i])
        elif C[i] > hi:
            if n <= 0: n, ext = 1, H[i]
            else: n += 1; ext = max(ext, H[i])
        elif C[i] < lo:
            if n >= 0: n, ext = -1, L[i]
            else: n -= 1; ext = min(ext, L[i])
        else:
            if n != 0:
                if abs(n) >= v.n_out: reclaim, reclaim_ext = (1 if n < 0 else -1), ext   # was below -> long
                if v.rearm: armL = armS = True
            n = 0
        can = RTH[i] and v.first_entry <= ET[i] <= v.last_entry and A[i] > 0 and not busy and plans < v.max_per_day
        if not can:
            continue
        atr = A[i]
        sig = None
        if v.entry == 'reclaim':
            if reclaim != 0: sig = (reclaim, reclaim_ext)
        else:
            if n <= -v.n_out and armL: sig = (1, ext)
            elif n >= v.n_out and armS: sig = (-1, ext)
        if sig is None: continue
        dd, extreme = sig
        if v.entry != 'reclaim':
            exc = (lo - C[i]) / atr if dd > 0 else (C[i] - hi) / atr
            if exc < v.min_exc_atr: continue                    # wait for a deeper push (side stays armed)
            if v.candle:                                        # wait for a rejection bar (side stays armed)
                rng = H[i] - L[i]
                pib = (C[i] - L[i]) / rng if rng > 0 else 0.5
                if (dd > 0 and pib < 0.5) or (dd < 0 and pib > 0.5): continue
        else:
            exc = 0.0
        # signal-level filters that consume the side
        consume_only = False
        if exc > v.max_exc_atr: consume_only = True
        if v.trend == 'with' and np.sign(E50[i] - E200[i]) != dd: consume_only = True
        if v.trend == 'against' and np.sign(E50[i] - E200[i]) == dd: consume_only = True
        if v.vwap_filter == 'far' and abs(C[i] - VW[i]) < atr: consume_only = True
        hslope = np.sign(E240[i] - E240[i - 12]) if i >= 12 else 0      # ~60-min EMA20 rising / falling over the last hour
        if v.htf == 'with' and hslope != dd: consume_only = True
        if v.htf == 'against' and hslope == dd: consume_only = True
        if v.daily_trend == 'with' and d['dtrend'] != dd: consume_only = True
        if v.daily_trend == 'against' and d['dtrend'] == dd: consume_only = True
        mv = d['gap_atr'] if v.on_ret_def == 'gap' else d['sess_atr']
        mvs = np.sign(mv) if abs(mv) >= v.on_ret_min_atr else 0
        if v.on_ret == 'reversal' and mvs != -dd: consume_only = True
        if v.on_ret == 'continuation' and mvs != dd: consume_only = True
        if v.confluence > 0:
            ph, pl = d['pdhl']
            lvl = pl if dd > 0 else ph
            if np.isnan(lvl) or abs((lo if dd > 0 else hi) - lvl) > v.confluence * atr: consume_only = True

        stop = rt(extreme - dd * v.stop_atr * atr)
        entry = rt(C[i])
        risk = (entry - stop) * dd
        if risk < v.min_stop_ticks * TICK:
            stop = entry - dd * v.min_stop_ticks * TICK; risk = v.min_stop_ticks * TICK
        plans += 1
        if dd > 0: armL = False
        else: armS = False
        if v.direction == 'long' and dd < 0: consume_only = True
        if v.direction == 'short' and dd > 0: consume_only = True
        if v.max_stop_pct > 0 and risk > v.max_stop_pct / 100.0 * C[i]: consume_only = True
        if consume_only or risk > v.max_stop_pts:
            continue
        if v.entry == 'limit_better':
            pending = dict(type='limit', dir=dd, limit=rt(C[i] - dd * v.limit_atr * atr), stop=stop, plan_entry=entry,
                           left=v.limit_bars, kind='on_fade')
        else:
            pending = dict(type='market', dir=dd, stop=stop, plan_entry=entry, kind='on_fade')
    if pos is not None:   # early-close session (holiday): flat at the session's last bar, like ExitOnSessionClose
        dd = pos['dir']; px = C[len(g) - 1] - slip * dd; pts = (px - pos['entry']) * dd
        trades.append(dict(tday=d['tday'], dir=dd, entry=pos['entry'], exit=px, how='time', pts=pts,
                           R=pts / pos['risk'] if pos['risk'] > 0 else 0.0, risk=pos['risk'],
                           net=pts * v.pv - 2 * v.comm_side, kind=pos['kind']))
    return trades


def run(days, v: V):
    out = []
    for d in days:
        out += run_day(d, v)
    return pd.DataFrame(out)


def stats(tr, split=pd.Timestamp('2026-02-01')):
    if len(tr) == 0:
        return dict(n=0)
    t = pd.to_datetime(tr.tday)
    R = tr.R.values; net = tr.net.values
    eq = np.cumsum(net); mdd = (np.maximum.accumulate(eq) - eq).max()
    w = net[net > 0].sum(); l = -net[net <= 0].sum()
    q = tr.assign(q=t.dt.to_period('Q')).groupby('q').net.sum()
    h1 = R[(t < split).values]; h2 = R[(t >= split).values]
    srt = np.sort(R)
    return dict(n=len(tr), win=(net > 0).mean(), avgR=R.mean(), t=R.mean() / (R.std(ddof=1) / np.sqrt(len(R))) if len(R) > 2 else np.nan,
                net=net.sum(), pf=w / l if l > 0 else np.inf, mdd=mdd, qpos=f"{int((q > 0).sum())}/{len(q)}",
                h1=h1.mean() if len(h1) else np.nan, h2=h2.mean() if len(h2) else np.nan,
                exTop1=srt[:-1].mean() if len(srt) > 1 else np.nan, exTop3=srt[:-3].mean() if len(srt) > 3 else np.nan)
