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
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter); ap.add_argument('folder'); ap.add_argument('--date', default=None)
    ap.add_argument('--registry', default=os.path.join(HERE, 'accounts.json'))
    a = ap.parse_args()
    reg = json.load(open(a.registry, encoding='utf-8'))['accounts']
    worst = 0
    for i, r in enumerate(reg):
        cmd = [sys.executable, os.path.join(HERE, 'daily_review.py'), a.folder, '--account', r['account'], '--contracts', str(r.get('contracts', 1))]
        if a.date: cmd += ['--date', a.date]
        if r.get('eval'): cmd += ['--eval', r['eval'], '--eval-start', r['eval_start']]
        if r.get('master'): cmd += ['--master']
        if r.get('activity_after'):
            cmd += ['--activity-after', str(int(r['activity_after'])), '--activity-vm', str(float(r.get('activity_vm', 0.0)))]
        if r.get('chandelier') is not None:
            cmd += ['--chandelier', str(float(r['chandelier']))]
        if r.get('trend_filter') is not None:
            cmd += ['--trend-filter', '1' if int(r['trend_filter']) else '0', '--trend-fast', str(int(r.get('trend_fast', 50))),
                    '--trend-slow', str(int(r.get('trend_slow', 200)))]
        if r.get('exit_gate') is not None:
            cmd += ['--exit-gate', str(int(r['exit_gate']))]
        if r.get('lunch_pause') is not None:
            cmd += ['--lunch-pause', '1' if int(r['lunch_pause']) else '0']
        if r.get('ema20_confirm') is not None:
            cmd += ['--ema20-confirm', '1' if int(r['ema20_confirm']) else '0']
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
                     f"position {st.get('position', '?')} x{st.get('quantity', '?')} - not reviewed. Note what it is (e.g. the Bulenox "
                     f"evaluation: size and start date) so it is registered in review\\accounts.json")
    if extra:
        print("\nNEEDS ATTENTION:\n- " + "\n- ".join(extra)); worst = 2
    worst = max(worst, recorder_check(a.folder, a.date))
    worst = max(worst, autopush_check(a.folder))
    worst = max(worst, archive_check(a.folder))
    worst = max(worst, watchdog_check(a.folder, a.date))
    worst = max(worst, shutdown_check())
    manual_check(a.folder, a.date)
    sys.exit(worst)


def manual_check(folder, date=None, live=None):
    try:
        import manual_book as MB
        day = date or dt.date.today().isoformat()
        lines = MB.scorecard(day, os.path.join(folder, 'records', 'nt_executions.csv'), live or MB.LIVE)
    except Exception as ex:
        lines = [f"manual Bulenox trading: not available ({type(ex).__name__}: {ex})"]
    print(); print("\n".join(lines))
    return 0


def shutdown_check(hours=26):
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
    import csv
    pl = os.path.join(folder, 'records', 'perf_log.csv')
    try: rows = [r for r in csv.DictReader(open(pl, encoding='utf-8')) if r['time_et'].startswith(day)]
    except Exception: rows = []
    lag = [(float(r['feed_lag_s']), r) for r in rows if r.get('feed_lag_s')]
    try:
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
        pct = lambda s: '; '.join(p + '%' if p[-1:].isdigit() else p for p in s.split('; '))
        print(f"data delay (NinjaTrader receive vs exchange, {len(v)} samples): median {v[len(v) // 2]:.2f} s, worst {worst:.1f} s at "
              f"{wr['time_et'][11:]}{' (busiest then: ' + pct(wr['top_cpu']) + ')' if wr.get('top_cpu') else ''}; samples over 5 s: "
              f"{sum(x >= 5 for x in v)}" + (f"; PC CPU median {sorted(cpu)[len(cpu) // 2]:.0f}%, max {max(cpu):.0f}%" if cpu else ''))
    for s in probe: print(s)
    top = max(late, key=lambda x: (x[0], -x[1].timestamp()), default=None)
    when = top[1] if top else dt.datetime.combine(dt.date.fromisoformat(day), dt.time(16))
    sv = perfprobe.md_at(servers, when) if servers else None; note = perfprobe.md_note(servers, when) if sv else ''
    if note: print(f"Rithmic market-data server: {note}")
    stops = []
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
NT_TICKS = r'D:\nt_ticks\MNQ'
HOLE_MIN = 3


def tape_holes(day, root=RECORDER_ROOT, now=None, hole_min=HOLE_MIN):
    import daily_review as R, pandas as pd
    close = R.early_closes(day.year).get(day, 17 * 60)
    t0 = pd.Timestamp(day) - pd.Timedelta(hours=6); t1 = pd.Timestamp(day) + pd.Timedelta(minutes=close)
    if now is not None: t1 = min(t1, pd.Timestamp(now).floor('min') - pd.Timedelta(minutes=1))
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
    if not have & set(want):
        before = [f for f in glob.glob(os.path.join(root, '*', '*_bars1minute.csv'))
                  if os.path.basename(f)[:10] < f'{days[0]}' and os.path.basename(os.path.dirname(f)) not in skip]
        return holes if before else None
    return holes


def tape_minutes(root, days):
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
    return bool(glob.glob(os.path.join(ticks_root, '*', f'{day}.parquet')))


def hole_text(holes, dated=False):
    return ', '.join((f"{a:%m-%d %H:%M}-" if dated or a.hour >= 18 or a.date() != b.date() else f"{a:%H:%M}-")
                     + (f"{b:%m-%d %H:%M}" if a.date() != b.date() else f"{b:%H:%M}") for a, b, _ in holes)


def tick_gaps(day, root=RECORDER_ROOT, ticks_root=NT_TICKS, now=None, back=30):
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
