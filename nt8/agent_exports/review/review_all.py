"""
review_all.py - runs daily_review.py for every account in accounts.json and flags NoiseArea heartbeats from accounts that are not
registered there (2026-10-21, for the Bulenox evaluation running beside Sim101).

    python review_all.py <folder with the agent_exports files> [--date YYYY-MM-DD]

Prints each account's review under its own header, then any unregistered account whose heartbeat was written in the last 3 days.
Exit code: the worst of the reviews (0 all good / 1 warnings / 2 needs attention); an unregistered running account is 2.
Never writes to the folder.
"""
import sys, os, json, glob, argparse, subprocess, datetime as dt
sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('folder'); ap.add_argument('--date', default=None)
    ap.add_argument('--registry', default=os.path.join(HERE, 'accounts.json'))      # tests point it elsewhere
    a = ap.parse_args()
    reg = json.load(open(a.registry, encoding='utf-8'))['accounts']
    worst = 0
    for i, r in enumerate(reg):
        cmd = [sys.executable, os.path.join(HERE, 'daily_review.py'), a.folder, '--account', r['account'], '--contracts', str(r.get('contracts', 1))]
        if a.date: cmd += ['--date', a.date]
        if r.get('eval'): cmd += ['--eval', r['eval'], '--eval-start', r['eval_start']]
        if r.get('master'): cmd += ['--master']           # 2026-10-24: funded Bulenox Master - inactivity check (rolling 5 trading days since 2026-10-30)
        if r.get('activity_after'):                       # 2026-10-30: the activity fallback registered for the account
            cmd += ['--activity-after', str(int(r['activity_after'])), '--activity-vm', str(float(r.get('activity_vm', 0.0)))]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if i: print()
        print(f"===== {r['account']} =====")
        print(p.stdout.rstrip())
        if p.stderr.strip(): print(p.stderr.rstrip())
        worst = max(worst, p.returncode if p.returncode in (0, 1, 2) else 2)
    known = {r['account'] for r in reg} | {'Backtest'}
    cutoff = dt.datetime.now() - dt.timedelta(days=3); extra = []
    for f in glob.glob(os.path.join(a.folder, 'NoiseArea_status_MNQ_*.json')):
        acct = os.path.basename(f)[len('NoiseArea_status_MNQ_'):-len('.json')]
        if acct in known or dt.datetime.fromtimestamp(os.path.getmtime(f)) < cutoff: continue
        try: st = json.load(open(f))
        except Exception: st = {}
        extra.append(f"UNREGISTERED ACCOUNT {acct}: NoiseArea heartbeat {st.get('updated_et', '?')} ET, state {st.get('state', '?')}, "
                     f"position {st.get('position', '?')} x{st.get('quantity', '?')} - not reviewed. Tell Claude what it is (e.g. the Bulenox "
                     f"evaluation: size and start date) so it is registered in review\\accounts.json")
    if extra:
        print("\nNEEDS ATTENTION:\n- " + "\n- ".join(extra)); worst = 2
    worst = max(worst, recorder_check(a.folder, a.date))
    worst = max(worst, autopush_check(a.folder))
    worst = max(worst, archive_check(a.folder))
    worst = max(worst, watchdog_check(a.folder, a.date))
    worst = max(worst, shutdown_check())
    sys.exit(worst)


def shutdown_check(hours=26):
    """2026-10-22: Windows 'rebooted without cleanly shutting down' (Kernel-Power 41) - 10 times in the 60 days to 10-22, 6 of them
    during market hours, none with a crash code (power cut, power supply or hard freeze). Any in the last 26 hours is a warning:
    after such a restart NinjaTrader needs its login and the strategy stays off until enabled by hand."""
    ps = (f"Get-WinEvent -FilterHashtable @{{LogName='System'; Id=41; StartTime=(Get-Date).AddHours(-{hours})}} -ErrorAction SilentlyContinue | "
          "ForEach-Object { $_.TimeCreated.ToString('yyyy-MM-dd HH:mm') }")
    try:
        p = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, timeout=60,
                           creationflags=0x08000000 if os.name == 'nt' else 0)
        times = [t for t in p.stdout.split() if t.strip()]
    except Exception as e:
        print(f"unexpected shutdowns: could not read the Windows log ({e})"); return 0
    times = sorted(set(' '.join(times[i:i + 2]) for i in range(0, len(times), 2)))
    print(f"unexpected shutdowns in the last {hours} h: {len(times)}" + (': ' + ', '.join(times) if times else ''))
    if times:
        print(f"\nWARNINGS:\n- this PC shut off without a clean shutdown at {', '.join(times)} (power cut, power supply or freeze): "
              f"check that NinjaTrader is running, connected and the strategy enabled; a UPS / the cause must be fixed before trading unattended")
        return 1
    return 0


def perf_line(folder, day):
    """2026-10-26: one line on how late market data reached NinjaTrader today and what used the PC (the watchdog's 2-minute
    samples in records\\perf_log.csv; that morning data ran 13-22 s late and no line said so). 2026-10-27: also names Rithmic's
    market-data server (perfprobe.md_servers: a login on Sun 10-25 got one not seen in a month, and both opens on it ran 18-23 s
    late) and warns when data ran 5 s late or more (that day a chart order filled 14 points from the chart). Returns 1 for that
    warning, else 0."""
    import csv
    pl = os.path.join(folder, 'records', 'perf_log.csv')
    try: rows = [r for r in csv.DictReader(open(pl, encoding='utf-8')) if r['time_et'].startswith(day)]
    except Exception: rows = []
    lag = [(float(r['feed_lag_s']), r) for r in rows if r.get('feed_lag_s')]
    try:                                       # 2026-10-27: the one-second probe says where a late stretch was held up
        import perfprobe
        probe = perfprobe.report(folder, day); servers = perfprobe.md_servers(folder)
        late = [(v['lag_s'], t) for t, v in perfprobe.load(folder, day) if v['lag_s'] is not None]
    except Exception as e: probe = [f"one-second probe: report failed ({type(e).__name__}: {e})"]; servers = []; late = []
    for x, r in lag:
        try: late.append((x, dt.datetime.strptime(r['time_et'], '%Y-%m-%d %H:%M')))
        except ValueError: pass
    if not lag: print("data delay: no samples today (watchdog perf_log)" if rows else "data delay: not measured today")
    else:
        v = sorted(x for x, _ in lag); worst, wr = max(lag, key=lambda x: x[0]); cpu = [float(r['pc_cpu_pct']) for r in rows if r.get('pc_cpu_pct')]
        pct = lambda s: '; '.join(p + '%' if p[-1:].isdigit() else p for p in s.split('; '))      # "NinjaTrader 1; claude 0" -> with %
        print(f"data delay (NinjaTrader receive vs exchange, {len(v)} samples): median {v[len(v) // 2]:.2f} s, worst {worst:.1f} s at "
              f"{wr['time_et'][11:]}{' (busiest then: ' + pct(wr['top_cpu']) + ')' if wr.get('top_cpu') else ''}; samples over 5 s: "
              f"{sum(x >= 5 for x in v)}" + (f"; PC CPU median {sorted(cpu)[len(cpu) // 2]:.0f}%, max {max(cpu):.0f}%" if cpu else ''))
    for s in probe: print(s)
    top = max(late, key=lambda x: (x[0], -x[1].timestamp()), default=None)     # a frozen value repeats: name the first time
    when = top[1] if top else dt.datetime.combine(dt.date.fromisoformat(day), dt.time(16))
    sv = perfprobe.md_at(servers, when) if servers else None; note = perfprobe.md_note(servers, when) if sv else ''
    if note: print(f"Rithmic market-data server: {note}")
    stops = []                                 # 2026-10-28: the watchdog's "market data stopped" alerts (14:43 to the 14:54 reconnect)
    try:
        import re
        for r in csv.DictReader(open(os.path.join(folder, 'records', 'watchdog_log.csv'), encoding='utf-8')):
            if not r['time_et'].startswith(day) or r['key'] != 'nodata': continue
            if r['event'] == 'ALERT':
                m = re.search(r'last tick arrived at (\d\d:\d\d)', r['detail']); stops.append([m.group(1) if m else r['time_et'][11:], None])
            elif r['event'] == 'resolved' and stops and stops[-1][1] is None: stops[-1][1] = r['time_et'][11:]
    except Exception: pass
    span = ', '.join(f"{a} to {b or 'the last check'}" for a, b in stops)
    if span: print(f"market data stopped completely: {span} ET")
    if (not top or top[0] < 5) and not stops: return 0
    print("\nWARNINGS:")
    if top and top[0] >= 5:
        print(f"- market data ran up to {top[0]:.1f} s late ({top[1]:%H:%M}): an order sent off the chart can fill far from "
              "the chart's price." + (f" Rithmic's data server then: {note}. Rithmic picks the server at login: after the close, when flat, "
              f"disconnect and reconnect '{sv[3] or 'the Rithmic connection'}' in NinjaTrader, then check the strategy is still enabled." if note else ''))
    if stops:
        print(f"- no market data reached NinjaTrader at all from {span} ET (the connection can still show Connected): the chart and "
              f"the strategy stood still. The fix is to disconnect and reconnect '{(sv[3] if sv else '') or 'the Rithmic connection'}' "
              f"(the watchdog pushes 'market data stopped' after 2 minutes).")
    return 1


def watchdog_check(folder, date=None):
    """2026-10-22: the 'NoiseArea watchdog' Windows task (review\\watchdog.py, every 2 minutes in session): today's alerts and
    anything still open. An open alert, or a watchdog that stopped checking during a session day, is a warning."""
    p = os.path.join(folder, 'NoiseArea_watchdog_status.json'); lg = os.path.join(folder, 'records', 'watchdog_log.csv')
    if not os.path.exists(p): print("watchdog: has not run yet"); return 1
    try: st = json.load(open(p, encoding='utf-8'))
    except Exception as e: print(f"watchdog: status unreadable ({e})"); return 1
    day = date or dt.date.today().isoformat(); alerts = []
    if os.path.exists(lg):
        import csv
        alerts = [r for r in csv.DictReader(open(lg, encoding='utf-8')) if r['time_et'].startswith(day) and r['event'] == 'ALERT']
    print(f"watchdog: last check {st.get('checked_et')}, phone push {st.get('push')}; {len(alerts)} alert(s) on {day}"
          + (': ' + '; '.join(f"{r['time_et'][11:]} {r['detail']}" for r in alerts[:5]) if alerts else ''))
    pc = perf_line(folder, day)
    if st.get('broken'):
        print(f"\nNEEDS ATTENTION:\n- the watchdog itself is failing since {st.get('broken_since')} ({st['broken']}): nothing is being checked"); return 2
    if st.get('open'):
        print("\nWARNINGS:\n- watchdog alert(s) still open: " + '; '.join(f"{k} since {v.get('since')}" for k, v in st['open'].items())); return 1
    try: age_h = (dt.datetime.now() - dt.datetime.strptime(st.get('checked_et', ''), '%Y-%m-%d %H:%M')).total_seconds() / 3600
    except Exception: age_h = 99
    if age_h > 1 and dt.date.today().weekday() < 5 and date is None:
        print(f"\nWARNINGS:\n- the watchdog last checked {age_h:.1f} h ago - check the Windows task 'NoiseArea watchdog'"); return 1
    return pc


def archive_check(folder):
    """2026-10-22: the 'NoiseArea nightly archive' Windows task (review\\archive_nightly.py, 18:20 ET): bars, tape, NinjaTrader tick
    store, NinjaTrader logs (deleted by NinjaTrader after 20 days), mirror to C:, VIX. A failed step or no run for 30 h is a warning."""
    p = os.path.join(folder, 'NoiseArea_archive_status.json')
    if not os.path.exists(p): print("data archive: has not run yet"); return 1
    try: st = json.load(open(p, encoding='utf-8'))
    except Exception as e: print(f"data archive: status unreadable ({e})"); return 1
    bad = [f"{k}: {v.get('note')}" for k, v in st.get('steps', {}).items() if not v.get('ok')]
    print(f"data archive: last run {st.get('finished')} - {st.get('state')} ({', '.join(st.get('steps', {}))})")
    try: age_h = (dt.datetime.now() - dt.datetime.strptime(st.get('finished', ''), '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
    except Exception: age_h = 99
    if bad: print("\nWARNINGS:\n- data archive step(s) failed: " + ' | '.join(bad)); return 1
    if age_h > 30: print(f"\nWARNINGS:\n- the nightly data archive last ran {age_h:.0f} h ago - check the Windows task 'NoiseArea nightly archive'"); return 1
    return 0


def autopush_check(folder):
    """2026-10-22: the hourly 'NoiseArea autopush' Windows task (D:\\dev\\trading\\NoiseArea\\tools\\autopush.py) keeps the private
    GitHub package current; a blocked / failed run or no run for 3 hours is a warning."""
    p = os.path.join(folder, 'NoiseArea_autopush_status.json')
    if not os.path.exists(p): print("github package: autopush has not run yet"); return 1
    try: st = json.load(open(p, encoding='utf-8'))
    except Exception as e: print(f"github package: status unreadable ({e})"); return 1
    print(f"github package: last run {st.get('time')} - {st.get('state')}: {st.get('note')}; last push {st.get('last_push') or '-'} "
          f"({(st.get('commit') or '')[:7]})")
    try: age_h = (dt.datetime.now() - dt.datetime.strptime(st.get('time', ''), '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
    except Exception: age_h = 99
    if st.get('state') not in ('pushed', 'no_changes', 'dry_run_ok'):
        print(f"\nWARNINGS:\n- the GitHub package was NOT updated ({st.get('state')}): {st.get('note')}"); return 1
    if age_h > 3:
        print(f"\nWARNINGS:\n- the hourly GitHub sync last ran {age_h:.0f} h ago - check the Windows task 'NoiseArea autopush'"); return 1
    return 0


RECORDER_ROOT = r'D:\nt_recorder'
NT_TICKS = r'D:\nt_ticks\MNQ'                        # ncd_export.py: one parquet per COMPLETE session in NinjaTrader's own tick store
HOLE_MIN = 3                                         # minutes in a row without a single recorded trade inside the session = a hole


def tape_holes(day, root=RECORDER_ROOT, now=None, hole_min=HOLE_MIN):
    """2026-10-29: [(start ET, end ET, minutes)] of the Globex session that ends on `day` (18:00 ET the evening before - Sunday for
    a Monday - to 17:00 ET or the early close) with no 1-minute bar in the recorder's <UTC date>_bars1minute.csv files (bar END
    times). MNQ trades every minute of the session, so 3+ empty minutes are a recording hole: NinjaTrader closed, the PC
    restarted, the market data stopped. Minutes the bars files lack are looked up in the tape's parquet copy before they count
    (the recorder writes bars only since 10-22 19:55). None when the recorder has no bar in the session and never ran before it.
    Found 10-29: holes on 10-23 (PC restart), 10-26 (Sunday open before NinjaTrader started) and 10-28 (data stop, NinjaTrader
    closed) that no report named; NinjaTrader's own tick store stopped growing on 10-22, so nothing else held those ticks."""
    import daily_review as R, pandas as pd
    close = R.early_closes(day.year).get(day, 17 * 60)
    t0 = pd.Timestamp(day) - pd.Timedelta(hours=6); t1 = pd.Timestamp(day) + pd.Timedelta(minutes=close)
    if now is not None: t1 = min(t1, pd.Timestamp(now).floor('min') - pd.Timedelta(minutes=1))     # reviewed before the close
    skip = ('parquet', '_to_delete', 'accounts'); days = (day - dt.timedelta(days=1), day)
    have = set()
    for d in days:
        for f in glob.glob(os.path.join(root, '*', f'{d}_bars1minute.csv')):
            if os.path.basename(os.path.dirname(f)) in skip: continue
            try: have |= set(pd.to_datetime(pd.read_csv(f, usecols=['time_et']).time_et))
            except Exception: continue
    want = pd.date_range(t0 + pd.Timedelta(minutes=1), t1, freq='min')

    def find(have):
        holes, run = [], []
        for m in list(want) + [None]:
            if m is None or m in have:
                if len(run) >= hole_min: holes.append((run[0] - pd.Timedelta(minutes=1), run[-1], len(run)))
                run = []
            else: run.append(m)
        return holes
    holes = find(have)
    if holes:
        have |= tape_minutes(root, days); holes = find(have)
    if not have & set(want):                         # nothing at all: a hole only if the recorder was already running before
        before = [f for f in glob.glob(os.path.join(root, '*', '*_bars1minute.csv'))
                  if os.path.basename(f)[:10] < f'{days[0]}' and os.path.basename(os.path.dirname(f)) not in skip]
        return holes if before else None
    return holes


def tape_minutes(root, days):
    """Bar-END minutes (ET) with at least one trade in the parquet copies of the recorder's <UTC date>_tape.csv for `days`."""
    import pandas as pd
    out = set()
    for d in days:
        for f in glob.glob(os.path.join(root, 'parquet', '*', f'{d}_tape.parquet')):
            if os.path.basename(os.path.dirname(f)) == 'accounts': continue
            try:
                import pyarrow.parquet as pq, pyarrow.compute as pc
                t = pq.read_table(f, columns=['kind', 'feed_utc']); t = t.filter(pc.equal(t['kind'], 'T'))
                s = pd.Series(t['feed_utc'].to_pandas()).dt.tz_localize('UTC').dt.tz_convert('America/New_York').dt.tz_localize(None)
                out |= set(s.dt.ceil('min'))
            except Exception: continue
    return out


def nt_tick_day(day, ticks_root=NT_TICKS):
    """True when NinjaTrader's own tick store holds the whole session (ncd_export writes a day only once it is complete)."""
    return bool(glob.glob(os.path.join(ticks_root, '*', f'{day}.parquet')))


def hole_text(holes, dated=False):
    """'14:40-17:00'; a hole that starts the evening before the session day (or any, dated=True) carries its date ('10-25 18:00-19:14')."""
    return ', '.join((f"{a:%m-%d %H:%M}-" if dated or a.hour >= 18 or a.date() != b.date() else f"{a:%H:%M}-")
                     + (f"{b:%m-%d %H:%M}" if a.date() != b.date() else f"{b:%H:%M}") for a, b, _ in holes)


def tick_gaps(day, root=RECORDER_ROOT, ticks_root=NT_TICKS, now=None, back=30):
    """(today's uncovered holes or [], [(session, holes)] of the earlier sessions back to the first recorded one - at most `back`
    days - whose holes neither the recorder nor NinjaTrader's tick store covers)."""
    import daily_review as R
    def gaps(d, now=None):
        if d.weekday() >= 5 or d in R.full_closures(d.year) or nt_tick_day(d, ticks_root): return []
        return tape_holes(d, root, now) or []
    older = []
    for i in range(back, 0, -1):
        d = day - dt.timedelta(days=i); g = gaps(d)
        if g: older.append((d, g))
    return gaps(day, now), older


def fill_step(first, last, inst):
    return (f"NinjaTrader's servers still have these ticks: when flat (after 16:00), open the Historical Data window in NinjaTrader "
            f"(Control Center > New > Historical Data), choose Download, {inst or 'MNQ'}, Tick, Last, {first:%m/%d/%Y} to {last:%m/%d/%Y}, "
            f"press Download - the nightly archive then exports them to D:\\nt_ticks")


def recorder_day(day, root=RECORDER_ROOT):
    """(bytes, last feed time UTC) of the TapeRecorder's tape file for the session day - its UTC-date file, which holds the whole
    day session - the largest one if several instruments were recorded. (0, '') when there is none."""
    best = (0, '')
    for f in glob.glob(os.path.join(root, '*', f'{day}_tape.csv')):
        if os.path.basename(os.path.dirname(f)) in ('parquet', '_to_delete', 'accounts'): continue
        n = os.path.getsize(f)
        with open(f, 'rb') as h:
            h.seek(max(0, n - 8192)); tail = h.read().decode('utf-8', 'replace').splitlines()
        last = next((l.split(',')[1] for l in reversed(tail) if l.count(',') >= 2 and l[:1].isalpha() and not l.startswith('kind')), '')
        best = max(best, (n, last))
    return best


def recorder_check(folder, date=None, root=RECORDER_ROOT, ticks_root=NT_TICKS, now=None):
    """2026-10-22: TapeRecorder (tick tape, quotes, orders, executions) health from agent_exports\\TapeRecorder_status.json.
    Information only until it is first installed; afterwards a session day without data is a warning.
    2026-10-24: whether the day was recorded is read from the tape file itself (it must exist and reach the close). The status
    file's counters are rewritten when NinjaTrader closes ('terminated', zero counts) - on 2026-10-23 that raised a false "recorded
    no market data" for a day with 3.6 GB of tape.
    2026-10-29: holes inside the session (tape_holes) that NinjaTrader's own tick store does not cover either are a warning for the
    reviewed day, with the download that fills them; older ones stay listed until filled."""
    p = os.path.join(folder, 'TapeRecorder_status.json')
    print()
    if not os.path.exists(p):
        print("tape recorder: not running yet (TapeRecorder indicator not installed - agent_exports\\review\\research\\recorder\\TapeRecorder.cs)")
        return 0
    try: st = json.load(open(p))
    except Exception as e: print(f"tape recorder: status file unreadable ({e})"); return 1
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    print(f"tape recorder: state {st.get('state')}, updated {st.get('updated_utc')} UTC, {st.get('instrument')}, trades {st.get('trades')}, "
          f"quotes {st.get('quotes')}, orders {st.get('orders')}, executions {st.get('executions')}, accounts {st.get('accounts')}"
          + (f", note: {st.get('note')}" if st.get('note') else ''))
    import daily_review as R
    warns = []; inst = st.get('instrument') or ''
    if day.weekday() < 5 and day not in R.full_closures(day.year):
        import pandas as pd
        close = R.early_closes(day.year).get(day, 16 * 60)
        close_utc = (pd.Timestamp(day) + pd.Timedelta(minutes=close)).tz_localize('America/New_York').tz_convert('UTC').tz_localize(None)
        n, last = recorder_day(day, root)
        print(f"tape {day}: " + (f"{n / 1e9:.2f} GB, last print {last[:19]} UTC (close {close_utc:%H:%M} UTC)" if n else "no file"))
        if not n or not last:
            warns.append(f"tape recorder recorded no market data on {day} (no tape file in {root}): put TapeRecorder back on its own "
                         f"chart (state {st.get('state')})")
        elif last < f"{close_utc - pd.Timedelta(minutes=2):%Y-%m-%d %H:%M:%S}" and (now or dt.datetime.now()) >= dt.datetime.combine(day, dt.time()) + dt.timedelta(minutes=close):
            # 2026-10-30: only once the close has passed - a review run during the session read the live tape as "stopped"
            warns.append(f"tape recorder stopped before the close on {day}: last print {last[:19]} UTC, the close is "
                         f"{close_utc:%H:%M} UTC - check that TapeRecorder is still on its own chart (state {st.get('state')})")
    holes, older = tick_gaps(day, root, ticks_root, now or dt.datetime.now())
    if holes:
        mins = sum(h[2] for h in holes)
        print(f"tick holes {day}: {hole_text(holes)} ET ({mins} min) - in neither the recorder nor NinjaTrader's tick store")
        warns.append(f"tick data missing on {day}: {hole_text(holes)} ET ({mins} min), nothing was recorded then. "
                     + fill_step(holes[0][0].date(), holes[-1][1].date(), inst))
    if older:
        print("tick data still missing from earlier sessions: " + ', '.join(hole_text(h, True) for _, h in older)
              + f" ET - {fill_step(older[0][1][0][0].date(), older[-1][1][-1][1].date(), inst)}")
    if st.get('state') == 'failed': warns.append(f"tape recorder stopped itself: {st.get('note')}")
    if warns:
        print("\nWARNINGS:\n- " + "\n- ".join(warns)); return 1
    return 0


if __name__ == '__main__':
    main()
