import datetime as dt
import numpy as np, pandas as pd

MU0_BP = 3.373
K_BP = MU0_BP / 2
H_WATCH = 681.1
H_ALARM = 1296.7
RESET_DATE = None
COMM_SIDE = 0.50

TICK = 0.25
GATE = 1.5
MIN_SIDES = 20
C_ALARM = 2.286
C_WARN = 1.513


def _open930(bars):
    if bars is None or not len(bars): return {}
    t = pd.to_datetime(bars.time_et); m = t.dt.hour * 60 + t.dt.minute
    b = bars.assign(_t=t, _m=m, _d=t.dt.date)
    first = b[b._m == 575]
    return dict(zip(first._d, first.open.astype(float)))


def edge_series(tr, sessions, bars, anchor, today):
    if anchor is None: return []
    if RESET_DATE: anchor = max(anchor, dt.date.fromisoformat(RESET_DATE))
    o930 = _open930(bars)
    days = [d for d in sessions if anchor <= d <= today]
    out = []
    for d in days:
        bp = 0.0
        if tr is not None and len(tr):
            td = tr[tr.xt.dt.date == d]
            if len(td):
                q = td['qty'].astype(float).clip(lower=1) if 'qty' in td else pd.Series(1.0, index=td.index)
                pnl = td.profit_currency.astype(float)
                if 'commission' in td:
                    pnl = pnl - np.where(td.commission.astype(float) == 0, 2 * COMM_SIDE, 0.0) * q
                ent = td['et'] if 'et' in td else td.index.to_series()
                g = pd.DataFrame({'e': ent.values, 'dr': td.direction.values, 'p': pnl.values, 'q': q.values}).groupby(['e', 'dr'])
                px = o930.get(d, float(td.entry_price.iloc[0]))
                bp = (g.p.sum() / g.q.sum()).sum() / (2.0 * px) * 1e4
        out.append((d, bp))
    return out


def cusum(values):
    s = 0.0; path = []
    for v in values:
        s = max(0.0, s + K_BP - v); path.append(s)
    return path


def edge_report(series):
    path = cusum([v for _, v in series]); S = path[-1] if path else 0.0
    info = (f"edge monitor (CUSUM vs the 16-year record): {S:.0f} bp, watch at {H_WATCH:.0f}, alarm at {H_ALARM:.0f} "
            f"(false alarm about once a decade), {len(series)} live sessions")
    warn = issue = None
    if S >= H_ALARM:
        issue = (f"edge-decay alarm: CUSUM {S:.0f} bp >= {H_ALARM:.0f} - live results are well below the 16-year record; "
                 f"stop and review before continuing")
    elif S >= H_WATCH:
        warn = (f"edge watch: live results are running below the 16-year record (CUSUM {S:.0f} >= {H_WATCH:.0f} bp) - keep running; "
                f"review if it reaches {H_ALARM:.0f}")
    return info, warn, issue


def cost_sides(tr, bars, live_checks, live_since=None):
    if tr is None or not len(tr): return [], 0
    ref_live = {}
    if live_checks is not None and len(live_checks):
        for _, r in live_checks.iterrows():
            ref_live[(pd.Timestamp(r.date).date(), str(r.time_et)[:5])] = float(r.price)
    ref_bar = {}
    if bars is not None and len(bars):
        t = pd.to_datetime(bars.time_et)
        ref_bar = dict(zip(t, bars.close.astype(float)))
    ls = pd.Timestamp(live_since) if live_since is not None else None
    out = []; unmeasured = 0
    for _, r in tr.iterrows():
        long_ = r.direction == 'long'
        run = r.get('_run')
        run = None if run is None or pd.isna(run) else pd.Timestamp(run)
        for leg, (tm, px, buy) in enumerate(((r.et, r.entry_price, long_), (r.xt, r.exit_price, not long_))):
            if leg == 0 and run is not None and pd.Timestamp(tm) < run: unmeasured += 1; continue
            t = (pd.Timestamp(tm) + pd.Timedelta(seconds=2)).floor('min'); key = (t.date(), t.strftime('%H:%M'))
            ref = None if key[1] == '16:00' else ref_live.get(key)
            if ref is None and ls is not None and t - pd.Timedelta(minutes=5) >= ls:
                ref = ref_bar.get(t)
            if ref is None or np.isnan(ref): unmeasured += 1; continue
            out.append((key[0], key[1], (float(px) - ref) * (1 if buy else -1) / TICK))
    return out, unmeasured


def cost_report(sides, simulated, unmeasured=0):
    tag = (f"; {unmeasured} side(s) not measured (no live reference)" if unmeasured else "") + \
          (" - Sim101 fills are simulated" if simulated else "")
    if not sides:
        return f"fill cost (live sides vs decision price): no measured sides yet{tag}", None, None
    c = np.array([s for _, _, s in sides]); n = len(c); m = c.mean()
    ten = [s for _, hhmm, s in sides if hhmm == '10:00']
    ten_txt = f"; 10:00 sides avg {np.mean(ten):+.2f} over {len(ten)}" if ten else ""
    if n < MIN_SIDES:
        return (f"fill cost (live sides vs decision price): avg {m:+.2f} ticks/side over {n} sides{ten_txt}; "
                f"alarm from {MIN_SIDES} sides{tag}"), None, None
    se = c.std(ddof=1) / np.sqrt(n)
    info = (f"fill cost (live sides vs decision price): avg {m:+.2f} ticks/side over {n} sides "
            f"[95% {m - 1.96 * se:+.2f}..{m + 1.96 * se:+.2f}]{ten_txt}{tag}")
    warn = issue = None
    if m - C_ALARM * se > GATE:
        issue = (f"fill cost {m:.2f} ticks/side over {n} live sides is demonstrably above the {GATE} gate (lower bound {m - C_ALARM * se:.2f}, "
                 f"calibrated for daily re-testing) - check order routing and latency")
    elif m - C_WARN * se > GATE:
        warn = f"fill cost {m:.2f} ticks/side over {n} live sides is above the {GATE} gate (not yet conclusive: lower bound {m - C_WARN * se:.2f})"
    return info, warn, issue
