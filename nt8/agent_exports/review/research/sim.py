"""
Bar-level simulator replicating TradePlanLevels / TradePlanLevelsStrategy.
- 5-min bars, chart time == ET (verified from export).
- Signal on bar close -> market entry at NEXT bar open (+slippage).
- Stop: stop-market (filled at stop - slippage). Target: limit at T2 (or T1).
- Bar touching both stop and target -> counted as stop (conservative).
- Flat at FlatHHmm (market at that bar's close).
"""
import pandas as pd, numpy as np
from dataclasses import dataclass, field

POINT_VALUE = 2.0   # MNQ
TICK = 0.25

@dataclass
class P:
    orb_minutes: int = 30
    min_bars_outside: int = 2
    stop_atr: float = 1.0
    min_stop_ticks: int = 20
    min_rr: float = 1.5
    gap_max_atr: float = 2.0
    min_gap_ticks: int = 20
    first_entry: int = 9*60+35
    last_entry: int = 15*60
    flat_at: int = 15*60+55
    max_per_day: int = 3
    fade_orb: bool = True
    fade_on: bool = True
    gap_fill: bool = True
    reclaim: bool = False
    target: str = "T2"           # "T2" or "T1"
    direction: str = "both"      # both / long / short
    risk_dollars: float = 150.0
    max_contracts: int = 10
    slippage_ticks: int = 1
    commission_side: float = 0.50
    atr_len: int = 14
    be_after_t1: bool = False      # move stop to entry once T1 is touched
    be_after_t1_offset_ticks: int = 0

def load(path):
    df = pd.read_csv(path)
    df['time'] = pd.to_datetime(df.time)
    df = df.sort_values('time').reset_index(drop=True)
    # Wilder ATR like NinjaTrader's ATR(14)
    h, l, c = df.high.values, df.low.values, df.close.values
    tr = np.empty(len(df)); tr[0] = h[0]-l[0]
    tr[1:] = np.maximum.reduce([h[1:]-l[1:], np.abs(h[1:]-c[:-1]), np.abs(l[1:]-c[:-1])])
    atr = np.empty(len(df)); atr[0] = tr[0]
    n = 14
    for i in range(1, len(df)):
        atr[i] = ((n-1)*atr[i-1] + tr[i])/n if i >= n else tr[:i+1].mean()
    df['atr'] = atr
    t = df.time
    df['etmin'] = t.dt.hour*60 + t.dt.minute
    # Globex trading day: 18:00 -> 17:00 next day
    df['tday'] = np.where(df.etmin > 17*60, (t + pd.Timedelta(days=1)).dt.date, t.dt.date)
    df['in_rth'] = (df.etmin > 9*60+30) & (df.etmin <= 16*60)
    return df

def rt(x): return round(x / TICK) * TICK

def run(df, p: P, verbose=False):
    O, H, L, C, A = df.open.values, df.high.values, df.low.values, df.close.values, df.atr.values
    ET, TD, RTH, T = df.etmin.values, df.tday.values, df.in_rth.values, df.time.values
    n = len(df)
    trades = []
    # state
    cur_day = None
    on_hi = on_lo = 0.0; on_done = False
    rth_started = False; orb_done = False; rth_first = -1
    rth_open = orb_hi = orb_lo = 0.0
    prior_rth_close = 0.0; cur_rth_close = 0.0
    gap_checked = False
    ex = {'orb': dict(n=0, extreme=0.0, last=0.0, armL=True, armS=True, side=0),
          'on':  dict(n=0, extreme=0.0, last=0.0, armL=True, armS=True, side=0)}
    plans_today = 0
    active = None       # dict(kind, dir, entry_sig, stop, t1, t2, sig_bar)
    pending = None      # plan waiting for next-bar-open fill
    pos = None          # dict(kind, dir, qty, entry, stop, target, t1, bar)
    slip = p.slippage_ticks * TICK

    def upd_ex(st, valid, hi, lo, i):
        if not valid or hi <= 0: st['n'] = 0; return
        if C[i] > hi:
            if st['n'] <= 0: st['n'] = 1; st['extreme'] = H[i]
            else: st['n'] += 1; st['extreme'] = max(st['extreme'], H[i])
            st['side'] = 1; st['last'] = st['extreme']
        elif C[i] < lo:
            if st['n'] >= 0: st['n'] = -1; st['extreme'] = L[i]
            else: st['n'] -= 1; st['extreme'] = min(st['extreme'], L[i])
            st['side'] = -1; st['last'] = st['extreme']
        else:
            if st['n'] != 0: st['armL'] = True; st['armS'] = True
            st['n'] = 0

    def try_create(kind, d, entry, stop, t1, t2, i):
        nonlocal active, plans_today
        entry, stop, t1, t2 = rt(entry), rt(stop), rt(t1), rt(t2)
        risk = entry - stop if d > 0 else stop - entry
        if risk < p.min_stop_ticks*TICK:
            stop = entry - p.min_stop_ticks*TICK if d > 0 else entry + p.min_stop_ticks*TICK
            risk = p.min_stop_ticks*TICK
        r1 = t1 - entry if d > 0 else entry - t1
        r2 = t2 - entry if d > 0 else entry - t2
        if r1 <= 0 or r2 <= 0: return False
        rr_reward = r2 if p.target == "T2" else r1 if p.target == "T1" else None
        if rr_reward is not None and rr_reward / risk < p.min_rr: return False
        active = dict(kind=kind, dir=d, entry=entry, stop=stop, t1=t1, t2=t2, bar=i)
        plans_today += 1
        return True

    for i in range(n):
        # ---- day roll ----
        if TD[i] != cur_day:
            cur_day = TD[i]
            on_hi, on_lo, on_done = H[i], L[i], False
            rth_started = orb_done = gap_checked = False
            for st in ex.values(): st.update(n=0, armL=True, armS=True, side=0)
            plans_today = 0
            in_rth_prev = False
        if not on_done: on_hi, on_lo = max(on_hi, H[i]), min(on_lo, L[i])
        in_rth = bool(RTH[i])
        in_orb = (9*60+30) < ET[i] <= (9*60+30+p.orb_minutes)

        # ---- fills for a pending plan at this bar's open ----
        if pending is not None and pos is None:
            d = pending['dir']
            fill = O[i] + slip*d
            risk = abs(pending['entry'] - pending['stop'])
            per_ct = risk * POINT_VALUE
            qty = min(int(p.risk_dollars // per_ct) if per_ct > 0 else 0, p.max_contracts)
            if qty >= 1:
                tgt = pending['t2'] if p.target == "T2" else pending['t1'] if p.target == "T1" else (1e12 if d > 0 else -1e12)
                pos = dict(kind=pending['kind'], dir=d, qty=qty, entry=fill, stop=pending['stop'], target=tgt,
                           t1=pending['t1'], bar=i, risk=risk, plan_entry=pending['entry'], t1hit=False)
            pending = None

        # ---- RTH boundaries ----
        if in_rth and not in_rth_prev:
            rth_started, on_done, rth_first = True, True, i
            rth_open, orb_hi, orb_lo = O[i], H[i], L[i]
            if cur_rth_close > 0: prior_rth_close = cur_rth_close
        in_rth_prev = in_rth
        if in_rth:
            cur_rth_close = C[i]
            if in_orb: orb_hi, orb_lo = max(orb_hi, H[i]), min(orb_lo, L[i])
            elif not orb_done: orb_done = True

        # ---- manage open position on this bar ----
        if pos is not None:
            d = pos['dir']; exit_px = None; how = None
            stop_hit = L[i] <= pos['stop'] if d > 0 else H[i] >= pos['stop']
            tgt_hit  = H[i] >= pos['target'] if d > 0 else L[i] <= pos['target']
            t1_hit   = H[i] >= pos['t1'] if d > 0 else L[i] <= pos['t1']
            if i == pos['bar']:
                # entry bar: gap-through-stop at open
                if (d > 0 and O[i] <= pos['stop']) or (d < 0 and O[i] >= pos['stop']):
                    stop_hit = True
            if stop_hit:
                exit_px = pos['stop'] - slip*d; how = 'stop'
            elif tgt_hit:
                exit_px = pos['target']; how = 'target'
            elif ET[i] >= p.flat_at or not in_rth:
                exit_px = C[i] - slip*d; how = 'time'
            if t1_hit and not pos['t1hit']:
                pos['t1hit'] = True
                if p.be_after_t1 and exit_px is None:
                    pos['stop'] = pos['entry'] + p.be_after_t1_offset_ticks*TICK*d
            if exit_px is not None:
                pnl_pts = (exit_px - pos['entry']) * d
                gross = pnl_pts * POINT_VALUE * pos['qty']
                comm = p.commission_side * 2 * pos['qty']
                trades.append(dict(time=T[pos['bar']], exit_time=T[i], kind=pos['kind'], dir=d, qty=pos['qty'],
                                   entry=pos['entry'], exit=exit_px, how=how, pts=pnl_pts,
                                   R=pnl_pts/pos['risk'] if pos['risk'] > 0 else 0, gross=gross, net=gross-comm,
                                   t1hit=pos['t1hit'], bars=i-pos['bar']))
                pos = None
            else:
                active = None  # keep plan slot occupied by position
        # plan is 'active' only until it becomes a position; mirror indicator: one plan at a time
        busy = pos is not None or pending is not None

        can_enter = in_rth and p.first_entry <= ET[i] <= p.last_entry and A[i] > 0 and not busy and plans_today < p.max_per_day
        upd_ex(ex['orb'], orb_done, orb_hi, orb_lo, i)
        upd_ex(ex['on'], on_done and rth_started, on_hi, on_lo, i)

        active = None
        if can_enter:
            atr = A[i]
            if p.gap_fill and not gap_checked and rth_started and i == rth_first and prior_rth_close > 0:
                gap_checked = True
                gap = rth_open - prior_rth_close
                filled_first = L[i] <= prior_rth_close <= H[i]
                if not filled_first and p.min_gap_ticks*TICK <= abs(gap) <= p.gap_max_atr*atr:
                    d = -1 if gap > 0 else 1
                    stop = L[i] - p.stop_atr*atr if d > 0 else H[i] + p.stop_atr*atr
                    try_create('gap', d, C[i], stop, prior_rth_close, prior_rth_close, i)
            if active is None and p.fade_orb and orb_done and not in_orb:
                st = ex['orb']; mid = (orb_hi+orb_lo)/2
                if st['n'] <= -p.min_bars_outside and st['armL']:
                    if try_create('orb_fade', 1, C[i], st['extreme'] - p.stop_atr*atr, orb_lo, mid, i): st['armL'] = False
                elif st['n'] >= p.min_bars_outside and st['armS']:
                    if try_create('orb_fade', -1, C[i], st['extreme'] + p.stop_atr*atr, orb_hi, mid, i): st['armS'] = False
            if active is None and p.fade_on and on_done:
                st = ex['on']; mid = (on_hi+on_lo)/2
                if st['n'] <= -p.min_bars_outside and st['armL']:
                    if try_create('on_fade', 1, C[i], st['extreme'] - p.stop_atr*atr, on_lo, mid, i): st['armL'] = False
                elif st['n'] >= p.min_bars_outside and st['armS']:
                    if try_create('on_fade', -1, C[i], st['extreme'] + p.stop_atr*atr, on_hi, mid, i): st['armS'] = False
            if active is None and p.reclaim and orb_done and not in_orb and ex['orb']['n'] == 0 and ex['orb']['side'] != 0:
                st = ex['orb']; d = -st['side']; mid = (orb_hi+orb_lo)/2
                stop = st['last'] - p.stop_atr*atr if d > 0 else st['last'] + p.stop_atr*atr
                try_create('reclaim', d, C[i], stop, mid, orb_hi if d > 0 else orb_lo, i)
                st['side'] = 0
            if active is not None:
                if (p.direction == 'long' and active['dir'] < 0) or (p.direction == 'short' and active['dir'] > 0):
                    active = None
                else:
                    pending = active
    return pd.DataFrame(trades)

def summarize(tr, label=""):
    if len(tr) == 0: return dict(label=label, n=0)
    net = tr.net.values
    eq = np.cumsum(net); dd = (np.maximum.accumulate(eq) - eq).max()
    wins = net[net > 0].sum(); losses = -net[net <= 0].sum()
    return dict(label=label, n=len(tr), win=(net > 0).mean(), avgR=tr.R.mean(), net=net.sum(),
                pf=(wins/losses if losses > 0 else np.inf), maxdd=dd, per_trade=net.mean(),
                t1=tr.t1hit.mean(), stop=(tr.how == 'stop').mean())

def fmt(s):
    if s.get('n', 0) == 0: return f"{s['label']:28s} no trades"
    return (f"{s['label']:28s} n={s['n']:4d} win {s['win']:5.1%} avgR {s['avgR']:+.3f} net ${s['net']:9,.0f} "
            f"PF {s['pf']:.2f} maxDD ${s['maxdd']:7,.0f} $/trade {s['per_trade']:+6.1f} T1 {s['t1']:4.0%} stop {s['stop']:4.0%}")
