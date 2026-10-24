"""
daily_review.py - end-of-day check of the NoiseArea strategy running in NinjaTrader.

    python3 daily_review.py <folder with the agent_exports files> [--account Sim101] [--date YYYY-MM-DD]
                            [--contracts N] [--eval 25K|50K|100K|150K --eval-start YYYY-MM-DD]
    --contracts   the micros this account is registered for (review\accounts.json; default 1). The size check, the strategy's
                  own Contracts setting (events file) and the 20-session band use it.
    --eval        a Bulenox Qualification account, Option 2 (end-of-day drawdown): prints the progress to the target, the
                  trailing threshold and the room left, from this account's trade logs since --eval-start (the purchase or
                  the last reset). Bulenox's dashboard is the authority; this is a cross-check.
    --master      a funded Bulenox Master: warns on Thursday, and flags from Friday, a week with no trade on the account
                  (Bulenox's inactivity rule: at least one trade per week).

Reads (all written by NoiseAreaMomentum / NoiseAreaStrategy into Documents\\NinjaTrader 8\\agent_exports):
    NoiseArea_status_MNQ_<account>.json                 heartbeat (rewritten every 5-minute bar while running)
    NoiseArea_trades_MNQ_<account>_<run>.csv            completed live trades, one file per strategy start (ALL files are used)
    NoiseArea_checks_MNQ_strategy_<account>.csv         decisions (rewritten from the historical replay on every start)
    NoiseArea_checks_live_MNQ_strategy_<account>.csv    decisions made in real time, append-only (preferred for today)
    NoiseArea_bars_MNQ_strategy_<account>.csv           every bar the strategy saw (ET)
Prints a short report and exits 0 (all good), 1 (warnings), 2 (needs attention). Never writes to the folder.
"""
import sys, os, glob, json, argparse, datetime as dt, calendar
sys.dont_write_bytecode = True   # 2026-10-20: importing monitor.py must not write __pycache__ into the review folder (read-only routine)
import numpy as np, pandas as pd

PV, TICK = 2.0, 0.25
# 20-session gate as a share of one micro's notional (price x $2), so it follows the price level. 5th..95th percentile of rolling
# 20-session P&L 2019-05 .. 2026-10 = -$938 .. +$2,228 at p_ref $27,875 (notional $55,751; research\MONITOR_RESULTS.txt section D).
# Until 2026-10-21 it was a fixed (-598, 1114) in 2019-2025 dollars, which an intact strategy at today's price breaches ~1/3 of the time.
BAND20_SHARE = (-938 / 55751, 2228 / 55751)


def band20(bars):
    """The 20-session band in dollars at the latest bar's price (falls back to the reference notional)."""
    try: notional = 2.0 * float(bars.close.iloc[-1]) if bars is not None and len(bars) else 55751.0
    except Exception: notional = 55751.0
    return round(BAND20_SHARE[0] * notional), round(BAND20_SHARE[1] * notional)
EXPECTED_QTY = 1                 # micros per position: 1 until the 60-clean-live-session gate (CLAUDE.md); change only with the sizing schedule
LOOK, VM = 14, 1.0
# 2026-10-21: Bulenox Qualification, Option 2, from bulenox.com/help-center/qualification and /accounts-pricing (read 2026-10-21):
# profit target, end-of-day drawdown (trails the closing high, no lock in qualification), daily loss limit; 30-day access.
BULENOX_EVAL = {'25K': dict(target=1500, dd=1500, dll=500), '50K': dict(target=3000, dd=2500, dll=1100),
                '100K': dict(target=6000, dd=3000, dll=2200), '150K': dict(target=9000, dd=4500, dll=3300)}
EVAL_DAYS = 30
BULENOX_COMM_SIDE = 0.61     # MNQ, per side per micro, all-in - Bulenox's published commission list (read 2026-10-20)


def strategy_setting(F, acct):
    """(time, contracts, daily loss stop) from the last 'live' line of NoiseArea_events_MNQ_<acct>.txt, or None."""
    p = os.path.join(F, f"NoiseArea_events_MNQ_{acct}.txt")
    if not os.path.exists(p): return None
    last = None
    for ln in open(p, encoding='utf-8', errors='replace'):
        f = ln.strip().split('|')
        if len(f) >= 4 and f[1] == 'live': last = f
    if last is None: return None
    import re
    c = re.search(r'contracts (\d+)', last[3]); s = re.search(r'daily loss stop ([\d.]+)', last[3])
    return last[0], int(c.group(1)) if c else None, float(s.group(1)) if s else None


STOP_EVENTS = ('live', 'halt', 'halt_restored', 'desync_stop', 'terminated', 'start_refused')


def stop_events(F, acct, day):
    """[(time, event)] of the day's start / halt / stop events from the events file, sorted."""
    p = os.path.join(F, f"NoiseArea_events_MNQ_{acct}.txt"); out = []
    if not os.path.exists(p): return out
    for ln in open(p, encoding='utf-8', errors='replace'):
        f = ln.strip().split('|')
        if len(f) >= 2 and f[0][:10] == day.isoformat() and f[1] in STOP_EVENTS:
            try: out.append((pd.Timestamp(f[0]), f[1]))
            except Exception: pass
    return sorted(out)


def not_trading(evts, T):
    """True when, at time T, the strategy was halted or stopped (the last start/stop event before T is not 'live')."""
    last = None
    for t, e in evts:
        if t <= T: last = e
    return last is not None and last != 'live'


RECORDER_ACCOUNTS = r'D:\nt_recorder\accounts'
RECORDER_ROOT = r'D:\nt_recorder'


def live_bars(day, inst=''):
    """2026-10-23: the TapeRecorder's live 5-minute bars of one session date (time_et = bar end), as built from the feed in real
    time - independent of NinjaTrader's history, which a restart reloads (10-23: a reload after a reconnect lost 10:00-10:15).
    Files are per UTC date of receipt, so the 16:00 ET bar sits in the next UTC day's file. None without recorder bars."""
    folder = inst.replace(' ', '_') if inst else '*'
    files = []
    for d in (day, day + dt.timedelta(days=1)):
        files += glob.glob(os.path.join(RECORDER_ROOT, folder, f"{d:%Y-%m-%d}_bars5min*.csv"))
    parts = []
    for f in files:
        try: parts.append(pd.read_csv(f, usecols=['time_et', 'open', 'high', 'low', 'close', 'volume']))
        except Exception: pass
    if not parts: return None
    b = pd.concat(parts, ignore_index=True).drop_duplicates('time_et', keep='last')
    b = b[pd.to_datetime(b.time_et).dt.date == day].sort_values('time_et').reset_index(drop=True)
    return b if len(b) else None


def session_bar_times(b):
    """'HH:MM' of the regular-session bars (09:35 .. 16:00) in a bars frame."""
    t = pd.to_datetime(b.time_et); m = t.dt.hour * 60 + t.dt.minute
    return set(t[(m > 570) & (m <= 960)].dt.strftime('%H:%M'))


def account_fills(F, acct):
    """2026-10-22 (code audit): every fill of the account in MNQ, whoever sent it - the nightly NinjaTrader ledger
    (records\\nt_executions.csv), plus the tick recorder's real-time files for fills newer than the ledger. Columns t (ET), inst,
    side (+1 buy / -1 sell), qty, price. The strategy's trade log misses manual, copied and broker (daily-loss liquidation) fills."""
    parts = []; ledger_end = None
    p = os.path.join(F, 'records', 'nt_executions.csv')
    if os.path.exists(p):
        x = pd.read_csv(p, usecols=['time_utc', 'account_name', 'instrument_name', 'ExecutionId', 'MarketPosition', 'Price', 'Quantity'])
        if len(x): ledger_end = pd.to_datetime(x.time_utc, errors='coerce').max()
        x = x[(x.account_name == acct) & x.instrument_name.astype(str).str.startswith('MNQ')]
        parts.append(pd.DataFrame({'t': x.time_utc, 'inst': x.instrument_name, 'id': x.ExecutionId.astype(str),
                                   'side': np.where(x.MarketPosition == 0, 1, -1), 'qty': x.Quantity.astype(int), 'price': x.Price.astype(float)}))
    for f in sorted(glob.glob(os.path.join(RECORDER_ACCOUNTS, '*_executions.csv'))):
        try: y = pd.read_csv(f, dtype=str)
        except Exception: continue
        y = y[(y.account == acct) & y.instrument.astype(str).str.startswith('MNQ')]
        if ledger_end is not None: y = y[pd.to_datetime(y.exec_utc, errors='coerce') > ledger_end]
        if len(y): parts.append(pd.DataFrame({'t': y.exec_utc, 'inst': y.instrument, 'id': y.exec_id.astype(str), 'side': np.where(y.position == 'Long', 1, -1),
                                             'qty': y.quantity.astype(int), 'price': y.price.astype(float)}))
    if not parts: return pd.DataFrame(columns=['t', 'inst', 'id', 'side', 'qty', 'price'])
    a = pd.concat(parts, ignore_index=True).drop_duplicates('id', keep='first')     # NinjaTrader's db repeats some execution rows
    a['t'] = pd.to_datetime(a.t, errors='coerce').dt.tz_localize('UTC').dt.tz_convert('America/New_York').dt.tz_localize(None)
    return a.dropna(subset=['t']).sort_values('t', kind='mergesort').reset_index(drop=True)


def master_inactivity(fills, today):
    """2026-10-24: Bulenox's Master "Inactivity Rule" - at least one trade per week (help center, Master Account). NoiseArea has a
    week with no entry about 1-2 times a year (16 years: 16 of 842 weeks, never two in a row). None when the account has a fill
    this week (Monday .. today); else ('warn', text) on Thursday - a day left to act - and ('issue', text) from Friday on."""
    mon = today - dt.timedelta(days=today.weekday())
    if today.weekday() < 3 or (len(fills) and ((fills.t.dt.date >= mon) & (fills.t.dt.date <= today)).any()): return None
    if today.weekday() == 3:
        return 'warn', ("no trade on this account since Monday - Bulenox's Master inactivity rule needs at least one trade a week: if "
                        "NoiseArea makes no entry by Friday's 15:30 decision, place one 1-micro round trip after its 16:00 flat and before "
                        "16:59 ET (flat by 3:59 PM CT)")
    return 'issue', ("no trade on this account this week - Bulenox's Master inactivity rule (at least one trade a week) can put the "
                     "account on hold: check the Bulenox dashboard")


def realized_by_session(fills, comm_side=BULENOX_COMM_SIDE):
    """FIFO realized P&L in $ per CME session (fills after 17:00 ET count for the next day), one book per contract, the
    commission per side per micro deducted on every fill."""
    out = {}; books = {}
    for r in fills.itertuples():
        b = books.setdefault(r.inst, []); q = int(r.qty)
        day = (r.t + pd.Timedelta(days=1)).date() if r.t.hour >= 17 else r.t.date()
        out[day] = out.get(day, 0.0) - comm_side * q
        while q and b and b[0][0] != r.side:
            lot = b[0]; m = min(q, lot[1])
            out[day] += (r.price - lot[2]) * lot[0] * m * PV
            lot[1] -= m; q -= m
            if lot[1] == 0: b.pop(0)
        if q: b.append([int(r.side), q, float(r.price)])
    return pd.Series(out, dtype=float).sort_index()


# ---------------------------------------------------------------- CME equity-index partial sessions (derived, not typed)
def _nth_weekday(year, month, weekday, n):
    d = dt.date(year, month, 1); first = (weekday - d.weekday()) % 7
    return d + dt.timedelta(days=first + 7 * (n - 1))

def _last_weekday(year, month, weekday):
    d = dt.date(year, month, calendar.monthrange(year, month)[1]); return d - dt.timedelta(days=(d.weekday() - weekday) % 7)

def _observed(d):
    if d.weekday() == 5: return d - dt.timedelta(days=1)
    if d.weekday() == 6: return d + dt.timedelta(days=1)
    return d

def early_closes(year):
    """{date: session end minute-of-day ET} for CME equity index futures: 13:00 on US cash-market holidays when Globex trades,
    13:15 on the day before Independence Day (when it is a weekday), the day after Thanksgiving and Dec 24 (weekday, Christmas not Saturday)."""
    E = {}
    hol = [_nth_weekday(year, 1, 0, 3), _nth_weekday(year, 2, 0, 3), _last_weekday(year, 5, 0),
           _observed(dt.date(year, 7, 4)), _nth_weekday(year, 9, 0, 1), _nth_weekday(year, 11, 3, 4)]
    if year >= 2022: hol.append(_observed(dt.date(year, 6, 19)))   # Juneteenth: cash market closed from 2022 on (full session 2019-2021)
    for d in hol:
        E[d] = 13 * 60
    j4 = dt.date(year, 7, 4)
    if j4.weekday() < 5 and j4.weekday() != 0: E[j4 - dt.timedelta(days=1)] = 13 * 60 + 15
    E[_nth_weekday(year, 11, 3, 4) + dt.timedelta(days=1)] = 13 * 60 + 15
    x = dt.date(year, 12, 24)
    if x.weekday() < 5 and dt.date(year, 12, 25).weekday() != 5: E[x] = 13 * 60 + 15
    return E


def _easter(year):
    a = year % 19; b, c = divmod(year, 100); d, e = divmod(b, 4); f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i, k = divmod(c, 4); l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    return dt.date(year, (h + l - 7 * m + 114) // 31, (h + l - 7 * m + 114) % 31 + 1)

def full_closures(year):
    """Weekdays with no CME equity-index session (2026-10-20, double verification: the review read them as a dead strategy):
    New Year's Day (Sunday -> Monday; a Saturday one is not observed), Good Friday, Christmas (Saturday -> Friday Dec 24,
    Sunday -> Monday Dec 26). CME has opened briefly on a Good Friday that fell on a jobs-report day (2021); such a day reads closed."""
    ny = dt.date(year, 1, 1); x = dt.date(year, 12, 25)
    out = {_easter(year) - dt.timedelta(days=2), x - dt.timedelta(days=1) if x.weekday() == 5 else x + dt.timedelta(days=1) if x.weekday() == 6 else x}
    if ny.weekday() < 5: out.add(ny)
    elif ny.weekday() == 6: out.add(ny + dt.timedelta(days=1))
    return out


def live_sessions(anchor, today, bar_sessions):
    """Sessions from anchor to today: every weekday that is not a full CME closure, plus any session in the bars file.
    2026-10-20 (double verification): the list came from the bars file alone, so a restart with fewer days loaded silently
    shortened the 20-session window and the edge CUSUM."""
    if anchor is None: return [d for d in bar_sessions if d <= today]
    out = {d for d in bar_sessions if anchor <= d <= today}; d = anchor
    while d < today:                     # today itself only once the bars file holds its session (as before)
        if d.weekday() < 5 and d not in full_closures(d.year): out.add(d)
        d += dt.timedelta(days=1)
    return sorted(out)


def run_start(path):
    """Strategy start time from 'NoiseArea_trades_<inst>_<acct>_<yyyyMMdd>_<HHmmss>.csv' (None if the name differs)."""
    try:
        p = os.path.basename(path)[:-4].split('_'); return dt.datetime.strptime(p[-2] + p[-1], '%Y%m%d%H%M%S')
    except Exception: return None


def third_friday(year, month): return _nth_weekday(year, month, 4, 3)


# ---------------------------------------------------------------- rules (identical to published.py S1 / the live indicator)
def recompute_checks(bars):
    """{(date, 'HH:MM'): desired position after that bar}. Partial sessions (last bar before 16:00): flat on the bar that
    closes 5 minutes before the end, no decision at or after it; no decision on the day's last bar."""
    b = bars.copy(); b['t'] = pd.to_datetime(b.time_et); b['m'] = b.t.dt.hour * 60 + b.t.dt.minute; b['d'] = b.t.dt.date
    r = b[(b.m > 570) & (b.m <= 960)]
    out = {}; hist = []; prev_close = None
    for d, g in r.groupby('d', sort=True):
        if len(g) < 30 or g.m.iloc[0] != 575: continue
        O, H, L, C, M, V = g.open.values, g.high.values, g.low.values, g.close.values, g.m.values, g.volume.values.astype(float)
        o930 = O[0]; mv = {int(mm): abs(c / o930 - 1) for mm, c in zip(M, C)}
        end = int(M[-1]); flat_min = 960 if end >= 960 else end - 5
        if len(hist) >= LOOK and prev_close is not None:
            tp = (H + L + C) / 3; vw = np.cumsum(tp * V) / np.maximum(np.cumsum(V), 1); pos = 0
            for i in range(len(g)):
                mm = int(M[i]); last = i == len(g) - 1
                if mm in range(600, 931, 30) and mm < flat_min and not last:
                    vals = [h[mm] for h in hist[-LOOK:] if mm in h]
                    if len(vals) < max(1, (LOOK + 1) // 2): continue
                    sig = float(np.mean(vals))
                    ub = max(o930, prev_close) * (1 + VM * sig); lb = min(o930, prev_close) * (1 - VM * sig); p = C[i]
                    if pos == 0: pos = 1 if p > ub else (-1 if p < lb else 0)
                    elif pos == 1: pos = -1 if p < lb else (0 if p < max(ub, vw[i]) else 1)
                    else: pos = 1 if p > ub else (0 if p > min(lb, vw[i]) else -1)
                    out[(d, g.t.iloc[i].strftime('%H:%M'))] = pos
                if (mm >= flat_min or last) and pos != 0: pos = 0; out[(d, g.t.iloc[i].strftime('%H:%M'))] = 0
        hist.append(mv); prev_close = C[-1]
    return out


def session_dates(bars):
    b = bars.copy(); b['t'] = pd.to_datetime(b.time_et); b['m'] = b.t.dt.hour * 60 + b.t.dt.minute
    r = b[(b.m > 570) & (b.m <= 960)]; g = r.groupby(r.t.dt.date).m.agg(['first', 'size'])
    return sorted(g[(g['first'] == 575) & (g['size'] >= 30)].index)


_OUT = {'lines': [], 'warns': [], 'issues': []}     # module-level so a crash can still print what was collected


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('folder'); ap.add_argument('--account', default='Sim101'); ap.add_argument('--date', default=None)
    ap.add_argument('--contracts', type=int, default=EXPECTED_QTY); ap.add_argument('--eval', choices=sorted(BULENOX_EVAL), default=None)
    ap.add_argument('--eval-start', default=None)
    ap.add_argument('--master', action='store_true')      # 2026-10-24: a funded Bulenox Master (accounts.json "master": true)
    a = ap.parse_args(); F = a.folder; acct = a.account; exp_qty = a.contracts
    if a.eval and not a.eval_start: ap.error('--eval needs --eval-start (the purchase or last reset date)')
    today = dt.date.fromisoformat(a.date) if a.date else dt.date.today()
    now = dt.datetime.now()                          # this PC runs on US Eastern time
    after_close = a.date is not None or (today == now.date() and now.hour * 60 + now.minute >= 16 * 60 + 5)
    issues, warns, lines = _OUT['issues'], _OUT['warns'], _OUT['lines']
    lines.append(f"NoiseArea daily review - {today} - account {acct}" + (f" - registered for {exp_qty} micros" if exp_qty != 1 else "")
                 + (f" - Bulenox {a.eval} Qualification since {a.eval_start}" if a.eval else ""))
    # 2026-10-21: the strategy's own Contracts / daily loss stop, as written at its last start, against the registration (a start
    # with the wrong Contracts was otherwise only noticed after its first trade)
    ss = strategy_setting(F, acct)
    if ss is not None:
        t0, c0, s0 = ss
        lines.append(f"strategy setting at its last start ({t0}): contracts {c0}, daily loss stop {s0:,.0f}" if s0 is not None else
                     f"strategy setting at its last start ({t0}): contracts {c0}")
        if c0 is not None and c0 != exp_qty:
            issues.append(f"the strategy was last started with Contracts {c0}, but {acct} is registered for {exp_qty} - set Contracts to {exp_qty} before the next start")
        if a.eval and s0 is not None:
            dll = BULENOX_EVAL[a.eval]['dll']
            if s0 <= 0: issues.append(f"the daily loss stop is OFF on the Bulenox account - set it just under Bulenox's ${dll:,} daily loss limit")
            elif s0 >= dll: warns.append(f"the daily loss stop ${s0:,.0f} is not below Bulenox's ${dll:,} daily loss limit - Bulenox's stop will fire first")
    EC = {**early_closes(today.year), **early_closes(today.year + 1)}
    FC = full_closures(today.year) | full_closures(today.year + 1)

    # 1. heartbeat
    sp = os.path.join(F, f"NoiseArea_status_MNQ_{acct}.json"); st = None; live_since = None
    if os.path.exists(sp):
        try: st = json.load(open(sp))
        except Exception as e: issues.append(f"status file unreadable: {e}")
    if st is None:
        issues.append("no heartbeat file: the strategy did not run (or ExportTrades is off)")
    else:
        upd = pd.Timestamp(st.get('updated_et') or st.get('updated_local') or '1900-01-01')
        state = str(st.get('state', '?'))
        hs = st.get('history_sessions')
        if upd.date() > today:
            # 2026-10-17: reviewing a past session (catch-up run, --date). The heartbeat describes a later moment, so it is
            # neither stale nor evidence about that day's close; its position/state checks would judge the wrong day.
            lines.append(f"past-session review: heartbeat checks skipped (the heartbeat is from {upd:%Y-%m-%d %H:%M}, after {today})")
        else:
            closed = today in FC and upd.date() < today
            end_min = EC.get(today, 16 * 60)
            flat = str(st.get('position', 'Flat')).lower() == 'flat' and int(st.get('account_position', 0) or 0) == 0
            # 2026-10-20 (double verification): closing NinjaTrader after the close is normal operation here (nothing restarts it;
            # the pre-flight checks the next morning), so a flat 'terminated' heartbeat written after the session end is reported,
            # not flagged. Before, every review run after a normal evening shutdown said NEEDS ATTENTION.
            stopped_after_close = state == 'terminated' and flat and (closed or (upd.date() == today and upd.hour * 60 + upd.minute >= end_min))
            if closed:
                lines.append(f"CME closed today ({today:%a %Y-%m-%d}, exchange holiday): no session to check; last heartbeat {upd:%Y-%m-%d %H:%M} ET")
            elif upd.date() != today: issues.append(f"heartbeat is stale: last update {upd}")
            elif upd.hour < 16 and str(today) not in {str(k) for k in EC}:
                # 2026-10-20 (double verification): after the close this means the strategy stopped during the session; it was
                # a warning, which the routine never pushes. Before the close (a manual mid-session run) it stays a warning.
                if after_close: issues.append(f"heartbeat stopped at {upd:%H:%M} ET, before the 16:00 close: the strategy was not running for the rest of the session")
                else: warns.append(f"heartbeat last updated {upd:%H:%M} ET, before the 16:00 close")
            if str(st.get('position', 'Flat')).lower() != 'flat': issues.append(f"strategy NOT FLAT after the close: {st.get('position')} x{st.get('quantity')}")
            if int(st.get('account_position', 0) or 0) != 0: issues.append(f"ACCOUNT NOT FLAT after the close: account position {st.get('account_position')}")
            if stopped_after_close:
                lines.append(f"strategy stopped after the close ({upd:%Y-%m-%d %H:%M} ET), flat: start NinjaTrader and enable the strategy before 09:35 ET next session")
            elif state != 'running': issues.append(f"strategy state is '{state}'" + (f": {st.get('halt_note')}" if st.get('halt_note') else ""))
            if st.get('connected') is False and not stopped_after_close: warns.append("account connection was down at the last heartbeat")
            if hs is not None and int(hs) < LOOK: issues.append(f"only {hs} sessions of history loaded (need {LOOK}): the strategy cannot decide - increase 'Days to load' and restart")
        if st.get('live_since'):
            try: live_since = pd.Timestamp(st['live_since'])
            except Exception: live_since = None
        inst = str(st.get('instrument', ''))
        lines.append(f"heartbeat {upd:%H:%M} ET state={state} position={st.get('position')} account={st.get('account_position', '?')} desired={st.get('desired')} "
                     f"trades_logged={st.get('trades_logged')} history={hs} instrument={inst or '?'}")
        try:   # contract roll: 'MNQ 12-26' -> expires the third Friday of Dec 2026, last trade 09:30 ET that day; NinjaTrader
               # moves to the next contract on the Monday of that week, so the roll is an evening Monday-Thursday of expiry week.
               # 2026-10-20 (double verification): it was a warning (never pushed) up to and including expiry day, when the
               # contract has already stopped trading; now the last evening before expiry and expiry day itself need attention.
            mm, yy = inst.split()[1].split('-'); exp = third_friday(2000 + int(yy), int(mm)); roll = exp - dt.timedelta(days=4)
            days = (exp - today).days
            if days < 0: issues.append(f"the strategy is on an EXPIRED contract ({inst}, expired {exp}); re-enable it on the current one")
            elif days == 0: issues.append(f"{inst} stopped trading at 09:30 ET today ({exp}); the strategy cannot trade it - enable it on the next contract")
            elif days == 1: issues.append(f"ROLL TONIGHT: {inst} stops trading at 09:30 ET tomorrow ({exp}) - after 16:15 ET disable this strategy and enable it on the next contract")
            elif today >= roll: warns.append(f"contract roll due: NinjaTrader moved to the next contract on {roll}; after 16:15 ET on an evening before {exp} disable this strategy and enable it on the next contract")
            elif days <= 11: warns.append(f"contract roll next week: {inst} expires {exp}; roll after 16:15 ET on an evening {roll} .. {exp - dt.timedelta(days=1)}")
        except Exception: pass

    # 2. bars + decisions: recompute the rules independently and compare
    bp = os.path.join(F, f"NoiseArea_bars_MNQ_strategy_{acct}.csv"); cp = os.path.join(F, f"NoiseArea_checks_MNQ_strategy_{acct}.csv")
    lp = os.path.join(F, f"NoiseArea_checks_live_MNQ_strategy_{acct}.csv")
    bars = chk = ct = None; sessions = []
    if os.path.exists(bp) and os.path.exists(cp):
        try:
            bars = pd.read_csv(bp).drop_duplicates('time_et', keep='last'); chk = pd.read_csv(cp)
            if os.path.exists(lp):
                # 2026-10-20 (double verification): prefer the real-time rows for the reviewed day and keep the replayed rows for
                # any time the live log lacks. Before, a day missing from the live log lost all its decisions ('no indicator
                # decisions logged'), and a restart mid-session narrowed the comparison to the live part.
                live = pd.read_csv(lp)
                lt = live[pd.to_datetime(live.date).dt.date == today] if len(live) else live
                if len(lt):
                    md = pd.to_datetime(chk.date).dt.date; mt = chk[md == today]
                    mt = mt[~mt.time_et.astype(str).str[:5].isin(set(lt.time_et.astype(str).str[:5]))]
                    chk = pd.concat([chk[md != today], mt, lt], ignore_index=True)
            sessions = session_dates(bars)
        except Exception as e:
            issues.append(f"bars/checks file unreadable: {e}"); bars = chk = None
    if chk is not None:
        chk['d'] = pd.to_datetime(chk.date).dt.date
        ct = chk[chk.d == today]
        if len(ct) == 0 and today in FC: lines.append("no indicator decisions today (exchange holiday)")
        elif len(ct) == 0: warns.append("no indicator decisions logged today")
        else:
            # 2026-10-23: recompute on the recorder's live bars for today when they cover the session from 09:35 - an independent
            # source; the strategy's bars file is NinjaTrader's history reloaded at each start, holes included (10-23: 10:00-10:15
            # missing after a reconnect + restart). Missing bars are reported: VWAP and the bands were computed without them.
            try: inst_f = inst
            except NameError: inst_f = ''
            lb = live_bars(today, inst_f); rc_src = "the strategy's own bars"; bars_rc = bars
            if lb is not None:
                sb_today = bars[pd.to_datetime(bars.time_et).dt.date == today]
                lsess = session_bar_times(lb); ssess = session_bar_times(sb_today)
                last_s = max(ssess) if ssess else '00:00'
                missing = sorted(x for x in lsess - ssess if x <= last_s)
                if missing:
                    warns.append(f"the strategy's bars miss {len(missing)} bar(s) of today's session ({', '.join(missing[:8])}"
                                 f"{' ...' if len(missing) > 8 else ''}) - NinjaTrader's history had a hole after a restart, so its VWAP and "
                                 f"bands used incomplete data; turn on Tools > Options > Data > 'Record live data as historical data'")
                if '09:35' in lsess:
                    bars_rc = pd.concat([bars[pd.to_datetime(bars.time_et).dt.date != today], lb], ignore_index=True)
                    rc_src = "the recorder's live bars"
            rc = recompute_checks(bars_rc); mism = []; n_cmp = 0
            for _, r in ct.iterrows():
                k = (today, str(r.time_et)[:5]); want = rc.get(k)
                if want is None: continue
                n_cmp += 1
                if int(want) != int(r.pos_after): mism.append(f"{k[1]} indicator {int(r.pos_after)} vs rules {int(want)}")
            if n_cmp == 0 and not after_close:
                lines.append(f"today's {len(ct)} decision(s) are recomputed after the close (fewer than 30 session bars so far)")
            elif n_cmp == 0: issues.append(f"none of today's {len(ct)} decisions could be independently recomputed (bars file has fewer than {LOOK + 1} valid sessions, or the day is not a valid session)")
            elif mism:
                # 2026-10-17 fix: this branch used to be "elif mism and bars is not None", which is always true here (checks are
                # only loaded together with bars), so a mismatch was never reported. A file that ends before 16:00 today makes the
                # recomputation treat the session as partial, so the mismatch is a warning there; on a complete session it is an issue.
                lastbar = pd.to_datetime(bars.time_et).max()
                msg = "indicator decisions differ from an independent recomputation: " + "; ".join(mism[:5])
                if lastbar.date() == today and (lastbar.hour * 60 + lastbar.minute) < 960:
                    warns.append(msg + f" (last bar in the file {lastbar:%H:%M} - partial session or data gap)")
                else: issues.append(msg)
            else: lines.append(f"{n_cmp} of {len(ct)} decisions today match an independent recomputation of the rules (on {rc_src})")
            changes = ct[ct.pos_before != ct.pos_after]
            lines.append("signals today: " + ("; ".join(f"{str(r.time_et)[:5]} {int(r.pos_before)}->{int(r.pos_after)} ({r.reason})" for _, r in changes.iterrows()) if len(changes) else "none"))
            if today in EC:
                cut = EC[today] - 5
                late = ct[ct.time_et.astype(str).str[:5].map(lambda s: int(s[:2]) * 60 + int(s[3:5])) >= cut]
                if len(late): issues.append(f"early-close day: the indicator still made decisions at or after {cut // 60:02d}:{cut % 60:02d} - check the Trading Hours template and EarlyCloseDates")
    elif bars is None and not any('unreadable' in i for i in issues):
        warns.append("bars/checks export missing (indicator ExportCsv off?) - no independent recomputation possible")

    # 3. trades: all run files for the account, deduplicated
    tfiles = sorted(glob.glob(os.path.join(F, f"NoiseArea_trades_MNQ_{acct}_*.csv")), key=os.path.getmtime)
    tr = pd.DataFrame()
    for f in tfiles:
        try:
            t = pd.read_csv(f)
            if len(t): t['_file'] = os.path.basename(f); t['_run'] = run_start(f); tr = pd.concat([tr, t], ignore_index=True)
        except Exception as e: issues.append(f"trade log {os.path.basename(f)} unreadable: {e}")
    if not tfiles: warns.append("no trade log file found")
    if len(tr):
        ecol = 'entry_time_et' if 'entry_time_et' in tr else 'entry_time'; xcol = 'exit_time_et' if 'exit_time_et' in tr else 'exit_time'
        # 2026-10-20 (double verification): identical rows WITHIN one run file are separate fills (a 2-lot filled 1+1 is two
        # NinjaTrader trades) and are kept; only copies of a row across files are dropped.
        key = [ecol, xcol, 'direction', 'entry_price', 'exit_price'] + (['qty'] if 'qty' in tr else [])
        tr['_k'] = tr.groupby(['_file'] + key).cumcount()
        tr = tr.drop_duplicates(key + ['_k'])
        tr['et'] = pd.to_datetime(tr[ecol]); tr['xt'] = pd.to_datetime(tr[xcol])
        tt = tr[tr.xt.dt.date == today]
        cut_hhmm = (EC[today] + 5) if today in EC else 16 * 60 + 5
        late = tt[(tt.xt.dt.hour * 60 + tt.xt.dt.minute) > cut_hhmm]
        if len(late): issues.append(f"{len(late)} trade(s) exited after the session cut-off ({cut_hhmm // 60:02d}:{cut_hhmm % 60:02d} ET)")
        if len(tt) and 'qty' in tt:
            # 2026-10-20 (double verification, gaps pass): nothing checked the size; a wrong 'Contracts' setting traded unnoticed
            pq = tt.groupby([tt.et, tt.direction]).qty.sum()
            odd = sorted({int(x) for x in pq if int(x) != exp_qty})
            if odd: issues.append(f"position size {odd} today differs from the registered {exp_qty} micro(s) - check the strategy's Contracts setting")
        # 2026-10-23: slippage from the ACCOUNT's real fills against the recorder's live bars when both exist. The trade log is
        # wrong after a restart mid-trade: the adopted entry carries the replay's time and price (10-23: 11:05:00 at 29,292 for a
        # real 11:00:00.244 fill at 29,294.25), which read as +70 ticks/side. Fills not at a strategy decision are not measured.
        lbo = None
        try:
            if lb is not None and '09:35' in session_bar_times(lb): lbo = lb.assign(t=pd.to_datetime(lb.time_et)).set_index('t').open
        except NameError: lbo = None
        dec = set()
        if ct is not None and len(ct):
            dec = {pd.Timestamp(f"{today} {str(r.time_et)[:5]}") for _, r in ct[ct.pos_before != ct.pos_after].iterrows()}
            fm = (EC[today] - 5) if today in EC else 16 * 60          # the flat at the close is not logged as a decision row
            dec.add(pd.Timestamp(today) + pd.Timedelta(minutes=fm))
        try:
            af = account_fills(F, acct); af = af[af.t.dt.date == today]
        except Exception: af = pd.DataFrame()
        if lbo is not None and len(af) and dec:
            slips = []; other = 0
            for r in af.itertuples():
                d0 = r.t.floor('5min')
                if d0 not in dec or (r.t - d0).total_seconds() > 10: other += 1; continue
                ref = lbo.get(d0 + pd.Timedelta(minutes=5))
                if ref is not None and not np.isnan(ref): slips.append((r.price - ref) * r.side / TICK)
            if other: lines.append(f"fill slippage: {other} fill(s) not at a strategy decision (manual / copied) - not measured")
            if slips:
                s = np.mean(slips); lines.append(f"fill slippage today: avg {s:+.2f} ticks/side over {len(slips)} sides (account fills vs the recorder's live "
                                                 f"bar opens; model assumes 1; Sim101 fills are simulated)")
                if s > 2.0: issues.append(f"slippage {s:.2f} ticks/side is above 2 - check the data feed / order routing")
                elif s > 1.5: warns.append(f"slippage {s:.2f} ticks/side is above the 1.5 gate")
        elif bars is not None and len(tt):
            bars['t'] = pd.to_datetime(bars.time_et); bo = bars.set_index('t').open
            slips = []; replayed = 0
            def ref_bar(tm):
                # 2026-10-20: +2 s, so a fill stamped just before a 5-minute boundary by a slightly slow PC clock still maps to the bar
                # that starts there (Sim101 fills land about 0.17 s after the bar close, research\LATENCY_NOTE.txt)
                t = pd.Timestamp(tm) + pd.Timedelta(seconds=2); c = t.ceil('5min')
                if c == t: c = t + pd.Timedelta(minutes=5)
                return c
            for _, r in tt.iterrows():
                dr = 1 if r.direction == 'long' else -1
                for leg, (tm, px, sgn) in enumerate(((r.et, r.entry_price, dr), (r.xt, r.exit_price, -dr))):
                    c = ref_bar(tm)
                    # 2026-10-18: every strategy start rewrites the bars file from NinjaTrader's historical data, whose bars differ
                    # from the ones recorded live (Fri 2026-10-16: 14 of 197 bars, one open by 11 ticks; the same fills read +1.50
                    # then -5.00 ticks/side). Measure only against bars that began after the strategy went live (this covers an
                    # entry re-adopted from the replay too).
                    if live_since is not None and c - pd.Timedelta(minutes=5) < live_since: replayed += 1; continue
                    ref = bo.get(c)
                    if ref is not None and not np.isnan(ref): slips.append((px - ref) * sgn / TICK)
            if replayed:
                lines.append(f"fill slippage: {replayed} side(s) not measured - their bars were re-read from history when the strategy "
                             f"restarted (live since {live_since:%Y-%m-%d %H:%M}), not recorded live")
            if slips:
                s = np.mean(slips); lines.append(f"fill slippage today: avg {s:+.2f} ticks/side over {len(slips)} sides (model assumes 1; Sim101 fills are simulated)")
                if s > 2.0: issues.append(f"slippage {s:.2f} ticks/side is above 2 - check the data feed / order routing")
                elif s > 1.5: warns.append(f"slippage {s:.2f} ticks/side is above the 1.5 gate")
        lines.append(f"trades today: {len(tt)}, P&L ${tt.profit_currency.sum():,.0f}" + (f" (commission ${tt.commission.sum():,.2f})" if 'commission' in tt else ""))
        daily = tr.groupby(tr.xt.dt.date).profit_currency.sum()
        if sessions:
            # anchor the live window to the FIRST strategy start (earliest run file), not the latest restart
            runs = [r for r in map(run_start, tfiles) if r is not None]
            anchor = min(runs).date() if runs else (live_since.date() if live_since is not None else None)
            lived = live_sessions(anchor, today, sessions)
            if anchor is not None and min(sessions) > anchor:
                lines.append(f"the bars file starts {min(sessions)}, after the first live session {anchor}: earlier live sessions are counted from the calendar")
            series = pd.Series(0.0, index=lived); series.loc[[d for d in daily.index if d in series.index]] = daily.reindex(series.index).fillna(0).loc[[d for d in daily.index if d in series.index]]
            if len(series) >= 20:
                b20 = tuple(v * exp_qty for v in band20(bars))     # 2026-10-21: the band is per micro; scaled to the registered size
                s20 = series.tail(20).sum(); lines.append(f"last 20 sessions: ${s20:,.0f} (backtest 90% band {b20[0]:,} .. {b20[1]:,} at today's price"
                                                          + (f", {exp_qty} micros)" if exp_qty != 1 else ")"))
                if s20 < b20[0]: issues.append(f"20-session P&L ${s20:,.0f} is below the backtest band - stop and review before continuing")
            else: lines.append(f"live sessions so far: {len(series)}, cumulative ${series.sum():,.0f} ({int((tr.xt.dt.date <= today).sum())} trades)")
        else: lines.append(f"cumulative ${daily.sum():,.0f} over {len(daily)} sessions with trades")
    elif tfiles: lines.append("trade logs exist, no live trades yet")

    # 3b. every live position change must have a fill (2026-10-20, double verification: a logged signal with no trade in the
    # log read 'All good'). Checked only after the close (an open position has no closed trade yet) and only for decisions
    # made after the strategy went live (earlier ones are replayed history and send no order).
    if after_close and ct is not None and len(ct):
        tt2 = tr[(tr.et.dt.date == today) | (tr.xt.dt.date == today)] if len(tr) and 'et' in tr else pd.DataFrame(columns=['et', 'xt', 'direction'])
        missing = []; evts = stop_events(F, acct, today)
        for _, r in ct[ct.pos_before != ct.pos_after].iterrows():
            T = pd.Timestamp(f"{today} {str(r.time_et)[:5]}")
            if live_since is not None and T < live_since: continue
            if not_trading(evts, T): continue      # 2026-10-22 audit: halted or stopped - the skipped signal is not a missing fill
            lo_, hi_ = T - pd.Timedelta(minutes=1), T + pd.Timedelta(minutes=3)
            pb, pa = int(r.pos_before), int(r.pos_after)
            if pb != 0 and not ((tt2.xt >= lo_) & (tt2.xt <= hi_)).any():
                missing.append(f"{str(r.time_et)[:5]} exit of {'long' if pb > 0 else 'short'}")
            if pa != 0 and not ((tt2.et >= lo_) & (tt2.et <= hi_) & (tt2.direction == ('long' if pa > 0 else 'short'))).any():
                missing.append(f"{str(r.time_et)[:5]} {'long' if pa > 0 else 'short'} entry")
        if missing: issues.append("signal(s) with no matching fill in the trade log: " + "; ".join(missing) + " - check NinjaTrader's orders and the trade log")

    # 3c. Bulenox Qualification progress (2026-10-21, --eval): the account's own closed trades since the purchase / last reset
    if a.eval:
        R = BULENOX_EVAL[a.eval]; e0 = dt.date.fromisoformat(a.eval_start); day_n = (today - e0).days + 1
        te = tr[(tr.xt.dt.date >= e0) & (tr.xt.dt.date <= today)].copy() if len(tr) and 'xt' in tr else pd.DataFrame()
        comm_note = ''
        if len(te) and ('commission' not in te or float(te.commission.abs().sum()) == 0):
            # 2026-10-22 (logging audit): NinjaTrader logs $0 commission on these accounts, Bulenox charges its published MNQ rate
            q = te['qty'].astype(float) if 'qty' in te else pd.Series(1.0, index=te.index)
            te['profit_currency'] = te.profit_currency.astype(float) - 2 * BULENOX_COMM_SIDE * q
            comm_note = f" (commission not in the log - Bulenox's ${BULENOX_COMM_SIDE:.2f}/side/micro deducted: ${2 * BULENOX_COMM_SIDE * q.sum():,.2f})"
        dly = te.groupby(te.xt.dt.date).profit_currency.sum() if len(te) else pd.Series(dtype=float)
        # 2026-10-22 (code audit): Bulenox counts every fill on the account, the strategy's log only its own trades - a daily-loss
        # liquidation, a manual or a copied trade would overstate the balance. The account's own fills win when they exist.
        try:
            acc = realized_by_session(account_fills(F, acct))
            acc = acc[(acc.index >= e0) & (acc.index <= today)]
        except Exception as ex:
            acc = pd.Series(dtype=float); warns.append(f"the account's own fills could not be read ({ex}) - the tracker uses the strategy's trade log")
        if len(acc):
            gap = [d for d in sorted(set(acc.index) | set(dly.index)) if abs(float(acc.get(d, 0.0)) - float(dly.get(d, 0.0))) > 1.0]
            if gap: warns.append(f"on {', '.join(str(d) for d in gap[:5])} the account's own fills differ from the strategy's trade log (a manual, "
                                 f"copied or broker trade, e.g. a daily-loss liquidation) - the tracker uses the account's fills")
            dly = acc; comm_note = f" (from the account's own fills, Bulenox's ${BULENOX_COMM_SIDE:.2f}/side/micro deducted)"
        bal = float(dly.sum()); best = max(0.0, float(dly.cumsum().max())) if len(dly) else 0.0
        thr = best - R['dd']; room = bal - thr
        lines.append(f"BULENOX {a.eval} QUALIFICATION day {day_n} of {EVAL_DAYS} (since {e0}): net ${bal:+,.0f} of the ${R['target']:,} target "
                     f"(${max(0.0, R['target'] - bal):,.0f} to go); end-of-day threshold ${thr:+,.0f} (best close ${best:+,.0f} - ${R['dd']:,}); "
                     f"room ${room:,.0f}; daily loss limit ${R['dll']:,}; {len(te)} trades{comm_note}. Bulenox's dashboard is the authority.")
        if bal >= R['target']:
            issues.append(f"PROFIT TARGET REACHED (${bal:+,.0f} of ${R['target']:,}): Bulenox reviews the account automatically - disable NoiseArea on {acct} so it adds no risk, and watch your email")
        elif room <= 0:
            issues.append(f"BELOW THE THRESHOLD (${bal:+,.0f} vs ${thr:+,.0f}): Bulenox has closed the account. Reset ($78) only if 5+ sessions remain "
                          f"(day {day_n} of {EVAL_DAYS}); after a reset the registration's eval start must become the reset date")
        elif room < R['dll']:
            warns.append(f"less than one daily loss limit (${R['dll']:,}) between the balance and the threshold (room ${room:,.0f})")
        if day_n > EVAL_DAYS and bal < R['target']:
            warns.append(f"the {EVAL_DAYS}-day access has ended (started {e0}) without the target")

    # 3d. Bulenox Master inactivity rule (2026-10-24, --master): at least one trade a week
    if a.master:
        try:
            mi = master_inactivity(account_fills(F, acct), today)
            if mi: (warns if mi[0] == 'warn' else issues).append(mi[1])
        except Exception as ex:
            warns.append(f"the weekly-trade check could not read the account's fills ({ex})")

    # 4. tomorrow
    nxt = today + dt.timedelta(days=1)
    while nxt.weekday() >= 5: nxt += dt.timedelta(days=1)
    if nxt in FC: lines.append(f"no session on {nxt:%a %Y-%m-%d} (CME holiday)")
    if nxt in EC:
        e = EC[nxt]; warns.append(f"EARLY CLOSE {nxt} ({e // 60:02d}:{e % 60:02d} ET): the strategy must be flat on the {(e - 5) // 60:02d}:{(e - 5) % 60:02d} bar; confirm the Trading Hours template or EarlyCloseDates has it, or disable for the day")

    # 5. flag-only alarms (2026-10-19, research\MONITOR_PLAN.txt, monitor.py): edge decay (CUSUM vs the 16-year record) and fill-cost
    # drift. Added after every existing check so that nothing above changes; they only add lines, warnings or issues.
    # 2026-10-20 (double verification): each alarm in its own try, so a failure in one cannot discard the other's verdict.
    mon = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 2026-10-20: found even when run with python -I
        import monitor as mon
        runs = [r for r in map(run_start, tfiles) if r is not None]
        anchor = min(runs).date() if runs else (live_since.date() if live_since is not None else None)
        trl = tr if len(tr) and 'xt' in tr else None
        if trl is not None and anchor is not None: trl = trl[(trl.xt.dt.date >= anchor) & (trl.xt.dt.date <= today)]
    except Exception as e:
        mon = None; warns.append(f"alarm monitor failed to load: {e}")
    if mon is not None:
        try:
            info, w, i = mon.edge_report(mon.edge_series(trl, live_sessions(anchor, today, sessions), bars, anchor, today))
            lines.append(info); warns.extend([w] if w else []); issues.extend([i] if i else [])
        except Exception as e: warns.append(f"edge-decay alarm failed: {e}")
        try:
            sides, unm = mon.cost_sides(trl, bars, pd.read_csv(lp) if os.path.exists(lp) else None, live_since)
            info, w, i = mon.cost_report(sides, simulated=acct.lower().startswith('sim'), unmeasured=unm)
            lines.append(info); warns.extend([w] if w else []); issues.extend([i] if i else [])
        except Exception as e: warns.append(f"fill-cost alarm failed: {e}")

    print("\n".join(lines))
    if warns: print("\nWARNINGS:\n- " + "\n- ".join(warns))
    if issues: print("\nNEEDS ATTENTION:\n- " + "\n- ".join(issues))
    if not warns and not issues: print("\nAll good.")
    sys.exit(2 if issues else (1 if warns else 0))


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        # 2026-10-20 (double verification): an uncaught exception used to exit with code 1 (read as 'warnings', never pushed)
        # and print nothing, losing every issue found so far. Now it prints what was collected and needs attention.
        L, W, I = _OUT['lines'], _OUT['warns'], _OUT['issues']
        I.append(f"the review script crashed ({type(e).__name__}: {e}); the checks after that point did not run - look at NinjaTrader and the files by hand")
        print("\n".join(L) if L else "NoiseArea daily review - crashed before the first line")
        if W: print("\nWARNINGS:\n- " + "\n- ".join(W))
        print("\nNEEDS ATTENTION:\n- " + "\n- ".join(I))
        sys.exit(2)
