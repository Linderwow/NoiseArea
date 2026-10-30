r"""
watchdog.py - watchdog for NoiseArea, run every 2 minutes by the Windows task "NoiseArea watchdog" (pythonw: no window).
2026-10-22: research\INSTITUTIONAL_OPS_20261022.txt item 1 (the setup checked itself only at 09:12 and 16:38), rebuilt the same
evening after an alert audit (goal: every failure reaches the phone, in plain words, with what to do).

    python watchdog.py              one check (what the task runs)
    python watchdog.py --test-push  one test message to the phone
    python watchdog.py --folder <exports> --now "YYYY-MM-DD HH:MM" [--nt-running yes|no] [--push]
                                    a test run against other files / another time: never pushes unless --push
    python watchdog_test.py         regression scenarios (temp folders, nothing sent)

Accounts = accounts.json plus any account whose NoiseArea heartbeat is less than 24 hours old (unregistered ones are an alert).
  In session (09:25 ET to the close; holidays / early closes from daily_review.py's CME calendar): NinjaTrader running; per
    account: heartbeat younger than 11 min, state "running", connected, account position = strategy position, at least 14
    sessions of history (else the strategy makes no decisions), position and Contracts setting within the registered size, no
    signal left unfilled on two heartbeats in a row; PC memory (commit) below 95%.
  After the close (close + 2 to close + 60 min): every account flat, judged from a heartbeat written after the close (the heartbeat
    is written at a bar close BEFORE that bar's exit fills) or from the TapeRecorder's live fills; nothing by close + 8 = alert.
  Outside those windows (nights, weekends, holidays): every account flat (Bulenox: no overnight positions).
  Always: accounts.json readable, OneDrive.exe not running (on 10-22 it overwrote the NinjaTrader folder), more than 10 GB free on
    C: and D:, and (live runs) a PC restart or a gap of more than 10 minutes between checks (PC off or asleep, task stopped).
  2026-10-26: in session, how late NinjaTrader receives market data (the TapeRecorder's receive time minus the exchange time of
    the newest ticks; that morning it ran 13-22 s late for 20 minutes and nothing noticed): alert at 5 s, cleared below 2 s,
    naming the busiest programs. Every run in and after the session appends data delay, CPU (whole PC, NinjaTrader, top 3 over
    the last 2 minutes) and memory to records\perf_log.csv, so a slow morning can be traced afterwards. For 20 minutes after the
    PC wakes or restarts, a stale backup / archive is not alerted (both catch up by themselves; 10-24 and 10-25 they alerted on
    every wake from a manual sleep).
  2026-10-27: the data ran 17 s late again after the open with the PC 2% busy, so "close programs" was wrong advice and the
    2-minute samples could not say where the data waits. From 09:26 to 10:20, 15:56 to 16:04 and on any run that finds data 2 s
    late, the rest of the run samples once a second (perfprobe.py -> records\perf_probe_<date>.csv; the daily review says
    whether the queue was in NinjaTrader or upstream). The alert's advice now depends on whether the PC is busy, and programs
    started between two runs are counted (Call of Duty, started 10:16, was missing from the 10:18 sample).
  2026-10-28: in session, no tick or quote on the tape for 2 minutes while the recorder says running = "market data stopped"
    (priority 5, reconnect advice; it replaces the frozen "still N s late" and the recorder's "no update" alert). That day the
    data stopped at 14:43 during the Fed press conference and the phone said "still 142 s late" and "tick recorder stopped".
An alert is pushed when it starts (plain words + what to do), every 15 minutes while it lasts, and when it clears; alerts left
open from an earlier day are raised fresh at the next session start. A push that fails is queued and retried on later runs. If the
watchdog itself fails, that is pushed too (at most hourly) and the status file says "broken".
Records: agent_exports\records\watchdog_log.csv (plus an "ok" sample every 30 minutes in session) and
agent_exports\NoiseArea_watchdog_status.json (the daily review prints it). It also measures the PC clock every 10 minutes.
Phone: D:\nt_logs\watchdog_config.json {"ntfy_server": ..., "ntfy_topic": ...} (since 2026-10-22 https://ntfy-me.com and a
private topic; never in the repo) and optionally {"healthchecks_url": "<ping URL>"}: a free healthchecks.io dead-man switch,
pinged on every good run, that alerts from outside when this PC, its internet or this task dies. Never touches NinjaTrader.
"""
import os, sys, json, csv, glob, time, shutil, ctypes, argparse, subprocess, traceback, urllib.request, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
EXPORTS = os.path.normpath(os.path.join(HERE, '..'))
ACCOUNTS = os.path.join(HERE, 'accounts.json')
CONFIG = r'D:\nt_logs\watchdog_config.json'
CONFIGS = (os.path.join(os.environ.get('LOCALAPPDATA') or r'C:\Users\linde\AppData\Local', 'NoiseArea', 'watchdog_config.json'), CONFIG)  # C: copy first: alerts survive a D: failure
ERRLOG = r'D:\nt_logs\watchdog_error.log'
RECORDER = r'D:\nt_recorder'           # TapeRecorder output; its live fills confirm flat when no heartbeat follows the close
STALE_MIN, REPEAT_MIN, OK_SAMPLE_MIN = 11, 15, 30
CLOCK_MIN = 10                      # clockprobe.py at most every 10 minutes (the hourly autopush probe was too coarse after a restart)
CONFIRM_MIN = 8                     # minutes after the close by which the first post-close heartbeat (the 16:05 bar) must show flat
HISTORY_MIN = 14                    # NoiseAreaStrategy LookbackDays: with fewer complete sessions loaded it makes no decisions
GAP_MIN = 10                        # minutes without a check that are reported (PC off or asleep, task stopped)
MEM_PCT, MEM_CLEAR = 95, 90                     # memory alert at 95% committed; once open it clears only below 90%
DISK_GB = 100                                   # 2026-10-23: the recorder writes ~3.5 GB a day - 100 GB is about a month's notice
LAG_S, LAG_CLEAR = 5.0, 2.0                     # market data this many seconds late = alert; once open it clears only below 2 s
STOP_S = 120                                    # in session, no tick or quote for this long while the recorder runs = data stopped
WAKE_GRACE_MIN = 20                             # after a wake / restart the hourly backup and nightly archive get this long to catch up
PROBE_WINDOWS = ((566, 620), (956, 964))        # ET minutes: 09:26-10:20 and 15:56-16:04 get one-second samples (perfprobe.py) ...
PROBE_LAG = 2.0                                 # ... and so does any run that finds data this many seconds late
PROBE_S = 108                                   # sampling ends this many seconds after the run started (next run: 120 s; IgnoreNew)
START = time.monotonic()
CPU_PREV = os.path.join(os.environ.get('LOCALAPPDATA') or r'C:\Users\linde\AppData\Local', 'NoiseArea', 'watchdog_cpu_prev.json')
FLAT_RULE = "Bulenox: flat by 4:59 PM ET (3:59 PM CT)"
NOWIN = 0x08000000 if os.name == 'nt' else 0
LABELS = {'nt_down': 'NinjaTrader not running', 'stale': 'strategy silent', 'state': 'strategy stopped', 'conn': 'connection down',
          'desync': 'position mismatch', 'nofile': 'no strategy report', 'history': 'not enough history', 'size': 'position too large',
          'setting': 'wrong Contracts setting', 'nofill': 'order not filled', 'notflat': 'not flat after the close',
          'overnight': 'position outside trading hours', 'unregistered': 'unregistered account', 'accounts_json': 'accounts.json broken',
          'onedrive': 'OneDrive running', 'disk_C': 'disk C: almost full', 'disk_D': 'disk D: almost full', 'memory': 'memory almost full',
          'restart': 'PC restarted', 'gap': 'checks missed', 'watchdog': 'watchdog broken', 'recorder': 'tick recorder stopped',
          'archive': 'nightly archive stopped', 'autopush': 'GitHub backup stopped', 'bars': 'strategy missing bars',
          'feedlag': 'market data late', 'nodata': 'market data stopped'}
WORDS = {1: 'LONG', -1: 'SHORT', 0: 'FLAT'}
_net = {'down': False}                 # after one failed push, the rest of this run queues instead of waiting on timeouts


def label(k):
    a, _, s = k.rpartition(':')
    return LABELS.get(s, s) + (f" ({a})" if a else '')


def ago(minutes):
    return f"{minutes:.0f} min" if minutes < 120 else f"{minutes / 60:.0f} h" if minutes < 2880 else f"{minutes / 1440:.0f} days"


DISPLAY = {}                                   # lower-case image name -> name as Windows shows it (for alert texts)


CPUT = {}                                      # pid -> [image name (lower case), CPU seconds used so far, start (Unix time)], filled by processes()


def proc_info(pid):
    """(committed private memory in bytes - what fills the commit limit -, CPU seconds used so far, start time as Unix time) of a
    process; None for what cannot be read."""
    class PMC(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('faults', ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in
                    ('pws', 'ws', 'qppp', 'qpp', 'qpnpp', 'qnpp', 'pf', 'ppf', 'private')]
    try:
        k32 = ctypes.windll.kernel32; k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x1000, False, int(pid))            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h: return None, None, None
        try:
            c = PMC(); c.cb = ctypes.sizeof(PMC)
            fn = ctypes.windll.psapi.GetProcessMemoryInfo; fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
            priv = c.private if fn(ctypes.c_void_p(h), ctypes.byref(c), c.cb) else None
            t = (ctypes.c_ulonglong * 4)()                       # creation, exit, kernel, user (100 ns units)
            gt = k32.GetProcessTimes; gt.argtypes = [ctypes.c_void_p] * 5
            ok_ = gt(ctypes.c_void_p(h), ctypes.byref(t, 0), ctypes.byref(t, 8), ctypes.byref(t, 16), ctypes.byref(t, 24))
            return priv, ((t[2] + t[3]) / 1e7 if ok_ else None), (t[0] / 1e7 - 11644473600 if ok_ else None)
        finally: k32.CloseHandle(ctypes.c_void_p(h))
    except Exception: return None, None, None


def private_bytes(pid): return proc_info(pid)[0]


def processes():
    """{image name (lower case): total committed private bytes (working set when unreadable)} of every running process; also
    refreshes CPUT for cpu_sample()."""
    p = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True, text=True, creationflags=NOWIN)
    out = {}; CPUT.clear()
    for row in csv.reader(p.stdout.splitlines()):
        if len(row) < 5: continue
        b, cpu, born = proc_info(row[1]) if row[1].isdigit() else (None, None, None)
        if b is None:
            try: b = int(''.join(ch for ch in row[4] if ch.isdigit()) or 0) * 1024
            except ValueError: b = 0
        k = row[0].lower(); out[k] = out.get(k, 0) + b; DISPLAY[k] = row[0]
        if cpu is not None: CPUT[row[1]] = [k, cpu, born]
    return out


def system_times():
    """(idle, busy) CPU seconds of the whole PC since boot, summed over all cores; None when unreadable."""
    try:
        t = (ctypes.c_ulonglong * 3)()
        if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(t, 0), ctypes.byref(t, 8), ctypes.byref(t, 16)):
            return t[0] / 1e7, (t[1] + t[2] - t[0]) / 1e7      # kernel time includes idle time
    except Exception: pass
    return None


def cpu_sample(path):
    """CPU use since the previous run (needs processes() first; the previous sample is kept in `path`):
    (whole PC busy %, [(% of the whole PC, program), ...] biggest first, NinjaTrader %). None on a first run or after a gap.
    2026-10-27: a program started since the previous run counts all its CPU (Call of Duty, started 10:16:18 with 74% of the PC,
    was missing from the 10:18 list); CPU no listed program accounts for shows as "other (unreadable)"."""
    now = time.time(); syst = system_times()
    try: prev = json.load(open(path, encoding='utf-8'))
    except Exception: prev = None
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as f: json.dump({'t': now, 'sys': syst, 'p': CPUT}, f)
        os.replace(path + '.tmp', path)
    except Exception: pass
    if not prev or not CPUT or not prev.get('p') or not 30 <= now - prev.get('t', 0) <= 900: return None
    wall = now - prev['t']; ncpu = os.cpu_count() or 1; per = {}
    for pid, v in CPUT.items():
        k, c = v[0], v[1]; born = v[2] if len(v) > 2 else None; q = prev['p'].get(pid)
        if q and q[0] == k and c >= q[1]: per[k] = per.get(k, 0.0) + (c - q[1]) / (wall * ncpu) * 100
        elif born is not None and born >= prev['t'] - 1: per[k] = per.get(k, 0.0) + c / (wall * ncpu) * 100
    tot = None
    if syst and prev.get('sys'):
        di, db = syst[0] - prev['sys'][0], syst[1] - prev['sys'][1]
        tot = 100.0 * db / (db + di) if db + di > 0 else None
    if tot is not None and tot - sum(per.values()) >= 5:
        per['other (unreadable)'] = tot - sum(per.values())
    name = lambda k: (lambda d: d[:-4] if d.lower().endswith('.exe') else d)(DISPLAY.get(k, k))
    top = sorted(((v, name(k)) for k, v in per.items() if k != 'system idle process'), reverse=True)[:5]
    return tot, top, per.get('ninjatrader.exe')


def tape_tail(folder, now):
    """(TapeRecorder status, now in UTC, [(receive UTC, delay s)] of the newest ~300 events of today's tape), or None."""
    try:
        rs = json.load(open(os.path.join(folder, 'TapeRecorder_status.json'), encoding='utf-8'))
        root = rs.get('output_dir') or RECORDER; inst = str(rs.get('instrument') or '').replace(' ', '_')
        u = now.astimezone(dt.timezone.utc).replace(tzinfo=None)              # this PC runs on US Eastern time
        with open(os.path.join(root, inst, f"{u:%Y-%m-%d}_tape.csv"), 'rb') as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - 65536)); lines = f.read().decode('utf-8', 'replace').splitlines()[1:]
    except Exception: return None
    def when(s):
        for fmt, w in (('%Y-%m-%d %H:%M:%S.%f', 26), ('%Y-%m-%d %H:%M:%S', 19)):
            try: return dt.datetime.strptime(s[:w], fmt)
            except ValueError: continue
        return None
    ev = []
    for line in lines[-300:]:
        p = line.split(',')
        if len(p) < 3 or p[0] not in ('T', 'A', 'B'): continue
        fu, ru = when(p[1]), when(p[2])
        if fu is None or ru is None: continue
        ev.append((ru, (ru - fu).total_seconds()))
    return rs, u, ev


def feed_lag(folder, now):
    """How late NinjaTrader receives market data right now: the median of (receive time - exchange time) over the newest ~300
    events of today's TapeRecorder tape, in seconds. None without a current tape (no recorder, file older than 10 min) or once
    nothing has arrived for STOP_S (data_stop() says so instead: a frozen delay is not a current one).
    2026-10-26: 13-22 s late at 09:31-09:37 and 10:00-10:12 with nothing alerting; Friday 10-23, a busier day, never above 2 s."""
    t = tape_tail(folder, now)
    if not t or len(t[2]) < 20 or (t[1] - t[2][-1][0]).total_seconds() > min(600, STOP_S): return None
    return float(sorted(x for _, x in t[2])[len(t[2]) // 2])


def data_stop(folder, now):
    """2026-10-28: during the Fed press conference the data fell 142 s behind, then nothing arrived from 14:43:03 until the user
    reconnected at 14:54 - NinjaTrader still said Connected, the watchdog kept pushing the frozen "142 s late" and its recorder
    alert pointed at the TapeRecorder chart. (minutes without data, last receive time ET, delay then) when the recorder says
    'running' but its tape has had no event for STOP_S seconds (MNQ quotes change several times a second in session), else None."""
    t = tape_tail(folder, now)
    if not t or not t[2] or t[0].get('state') != 'running': return None
    last, lag = t[2][-1]; idle = (t[1] - last).total_seconds()
    if idle < STOP_S: return None
    return idle / 60, last.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None), lag


def probe_due(now, lag):
    """Whether this run should sample once a second for the rest of its 2 minutes (perfprobe.py)."""
    m = now.hour * 60 + now.minute
    return any(a <= m < b for a, b in PROBE_WINDOWS) or (lag is not None and lag >= PROBE_LAG)


def session_end(d):
    """Close of the day session in minutes after midnight ET (early closes included), None on weekends and full closures."""
    import daily_review as R
    if d.weekday() >= 5 or d in R.full_closures(d.year): return None
    return R.early_closes(d.year).get(d, 16 * 60)


def session_window(now):
    """(in_session, after_close) for a US/Eastern naive datetime: 09:25 to the close, and close + 2 to close + 60 minutes."""
    end = session_end(now.date()); m = now.hour * 60 + now.minute
    if end is None: return False, False
    return (9 * 60 + 25 <= m <= end), (end + 2 <= m <= end + 60)


def recorder_position(account, instrument, since, start):
    """The account's position after `since` (US Eastern, this PC's clock) from the TapeRecorder's live fills: start + buys - sells.
    None without a fills file (recorder not on a chart). This PC runs on US Eastern time, so astimezone() converts UTC to ET."""
    pos, seen = start, False
    days = sorted({since.astimezone(dt.timezone.utc).date(), dt.datetime.now(dt.timezone.utc).date()})
    for d in days:
        p = os.path.join(RECORDER, 'accounts', f"{d:%Y-%m-%d}_executions.csv")
        if not os.path.exists(p): continue
        seen = True
        try:
            with open(p, newline='', encoding='utf-8') as f:
                for r in csv.DictReader(f):
                    if r['account'] != account or r['instrument'] != instrument: continue
                    t = dt.datetime.strptime(r['recv_utc'][:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
                    if t > since: pos += int(r['quantity']) * (1 if r['position'] == 'Long' else -1)
        except Exception: return None
    return pos if seen else None


def accounts(folder, now):
    """(registered {account: entry}, unregistered accounts with a heartbeat younger than 24 h, accounts.json error or None)."""
    err = None
    try: reg = {r['account']: r for r in json.load(open(ACCOUNTS, encoding='utf-8'))['accounts']}
    except Exception as e: reg, err = {}, f"{type(e).__name__}: {e}"
    extra = []
    for f in sorted(glob.glob(os.path.join(folder, 'NoiseArea_status_MNQ_*.json'))):
        a = os.path.basename(f)[len('NoiseArea_status_MNQ_'):-len('.json')]
        if a in reg or a == 'Backtest': continue
        try: u = dt.datetime.strptime(json.load(open(f, encoding='utf-8')).get('updated_et', ''), '%Y-%m-%d %H:%M:%S')
        except Exception: continue
        if abs((now - u).total_seconds()) <= 86400: extra.append(a)
    return reg, extra, err


def heartbeat(folder, a):
    st = json.load(open(os.path.join(folder, f"NoiseArea_status_MNQ_{a}.json"), encoding='utf-8'))
    try: upd = dt.datetime.strptime(st.get('updated_et') or st.get('updated_local'), '%Y-%m-%d %H:%M:%S')
    except Exception: upd = dt.datetime(1900, 1, 1)
    pos = str(st.get('position', 'Flat')).lower(); q = int(st.get('quantity') or 0)
    return st, upd, (q if pos == 'long' else -q if pos == 'short' else 0), q, int(st.get('account_position') or 0)


def started_contracts(folder, a):
    """Contracts at the strategy's last start on this account (the events file's last '|live|' line), or None."""
    p = os.path.join(folder, f"NoiseArea_events_MNQ_{a}.txt")
    try: lines = [l for l in open(p, encoding='utf-8', errors='replace').read().splitlines() if f"|live|{a}|" in l]
    except Exception: return None
    try: return int(lines[-1].split('contracts', 1)[1].split(',')[0]) if lines else None
    except Exception: return None


def bars_hole(folder, a, inst, now):
    """Session bars (HH:MM, bar end) the TapeRecorder built live but the strategy's bars file lacks, up to the strategy's last bar.
    2026-10-23: a reconnect + restart reloaded the strategy from NinjaTrader's history, which had no 10:00-10:15 bars - its VWAP
    and bands then ran on incomplete data. Reads only the tail of the strategy's file."""
    day = now.strftime('%Y-%m-%d')
    try:
        with open(os.path.join(folder, f"NoiseArea_bars_MNQ_strategy_{a}.csv"), 'rb') as f:
            f.seek(0, 2); f.seek(max(0, f.tell() - 65536)); tail = f.read().decode('utf-8', 'replace').splitlines()[1:]
        have = {l[11:16] for l in tail if l.startswith(day)}
        live = set()
        for p in glob.glob(os.path.join(RECORDER, (inst or 'MNQ').replace(' ', '_'), f"{now.astimezone(dt.timezone.utc):%Y-%m-%d}_bars5min*.csv")):
            live |= {l[11:16] for l in open(p, encoding='utf-8', errors='replace') if l.startswith(day)}
    except Exception: return []
    last = max(have) if have else '00:00'
    return sorted(x for x in live - have if '09:35' <= x <= min(last, '16:00'))


def checks(folder, now, nt_running, mem=None):
    """{key: plain message} for every problem now, and whether a session check window is open. mem (per-account heartbeat memory,
    updated in place and kept in the status file) lets 'order not filled' compare two heartbeats in a row."""
    mem = {} if mem is None else mem
    in_s, after = session_window(now); night = not (in_s or after); probs = {}
    reg, extra, err = accounts(folder, now)
    if err: probs['accounts_json'] = (f"accounts.json cannot be read ({err}). Only the strategy report files found are being checked "
                                      "- tell Claude to fix the file.")
    end = session_end(now.date())
    close = dt.datetime.combine(now.date(), dt.time()) + dt.timedelta(minutes=end) if end is not None else None
    if in_s and not nt_running:
        probs['nt_down'] = "NinjaTrader is not running during the trading session. Start it, connect Bulenox and enable NoiseArea on the MNQ chart."
    for a in list(reg) + extra:
        r = reg.get(a)
        try: st, upd, strat, q, acct = heartbeat(folder, a)
        except Exception:
            if in_s or after: probs[f'{a}:nofile'] = f"{a}: no readable strategy report file - is NoiseArea enabled on {a}?"
            continue
        state = str(st.get('state'))
        if r is None and not err:
            probs[f'{a}:unregistered'] = (f"{a}: NoiseArea is running on an account that is not registered (last report {upd:%m-%d %H:%M} ET, "
                                          f"{state}, position {acct:+d}). Tell Claude what it is (e.g. the Bulenox eval: size and start date).")
        if in_s:
            age = (now - upd).total_seconds() / 60
            if age > STALE_MIN:
                probs[f'{a}:stale'] = (f"{a}: no strategy report for {ago(age)} (last {upd:%H:%M} ET) - NinjaTrader may be frozen, closed or "
                                       f"without data. Check it; if a position is open and you cannot reach NinjaTrader, flatten in the Rithmic app.")
            elif state != 'running':
                if state == 'halted':
                    probs[f'{a}:state'] = f"{a}: strategy halted for the rest of the day by its daily loss stop ({st.get('halt_note') or ''}). No action needed unless you disagree."
                else:
                    probs[f'{a}:state'] = (f"{a}: strategy is not running (state '{state}'{': ' + st['halt_note'] if st.get('halt_note') else ''}). "
                                           f"Check the account position in NinjaTrader, then re-enable NoiseArea.")
            if st.get('connected') is False:
                probs[f'{a}:conn'] = f"{a}: the account connection is down. NinjaTrader normally reconnects within minutes; if not, reconnect Bulenox in NinjaTrader."
            if acct != strat:
                probs[f'{a}:desync'] = (f"{a}: the account holds {acct:+d} contracts but the strategy expects {strat:+d} (manual or copied trade?). "
                                        f"After 2 bars the strategy flattens the account's position in this instrument and stops itself (build 2026-10-22) - then reconcile and re-enable.")
            if state == 'running' and age <= STALE_MIN and int(st.get('history_sessions', HISTORY_MIN) or 0) < HISTORY_MIN:
                probs[f'{a}:history'] = (f"{a}: only {st.get('history_sessions')} days of history loaded ({HISTORY_MIN} needed) - the strategy will NOT "
                                         f"trade today. In NinjaTrader set 'Days to load' to 30 or more and re-enable it.")
            hole = bars_hole(folder, a, st.get('instrument', ''), now) if state == 'running' else []
            if hole:
                probs[f'{a}:bars'] = (f"{a}: the strategy is missing {len(hole)} bar(s) of today's session ({', '.join(hole[:6])}{' ...' if len(hole) > 6 else ''}) "
                                      f"after a restart - NinjaTrader's history has a hole, so VWAP and the bands are computed without them. When flat: "
                                      f"right-click the chart > Reload All Historical Data and re-enable NoiseArea; for good: Tools > Options > Data > "
                                      f"'Record live data as historical data'.")
            if r is not None:
                c = int(r.get('contracts', 1)); n = started_contracts(folder, a)
                if max(abs(acct), q) > c:
                    probs[f'{a}:size'] = f"{a}: position of {max(abs(acct), q)} contracts is above the {c} registered. Check the account and the strategy's Contracts setting now."
                if n is not None and n != c:
                    probs[f'{a}:setting'] = f"{a}: the strategy was started with Contracts {n}, but {a} is registered for {c}. Fix the setting (or tell Claude the new size)."
            mis = state == 'running' and int(st.get('desired') or 0) != (1 if strat > 0 else -1 if strat < 0 else 0)
            m0 = mem.get(a) or {}; u = st.get('updated_et') or ''
            if u != m0.get('u'): streak = (m0.get('streak', 0) + 1 if u[:10] == (m0.get('u') or '')[:10] else 1) if mis else 0
            else: streak = m0.get('streak', 0)
            mem[a] = {'u': u, 'streak': streak}
            if streak >= 2:
                want = int(st.get('desired') or 0)
                probs[f'{a}:nofill'] = (f"{a}: the strategy wants {WORDS.get(want, want)} but the position is still {WORDS[(strat > 0) - (strat < 0)]} after "
                                        f"two bars - the order may have been rejected or be stuck. Check NinjaTrader's Orders and Log tabs.")
        # the heartbeat is written at a bar close BEFORE that bar's exit fills (16:00:00 still shows the trade, the fill is 16:00:01,
        # the next heartbeat 16:05), so only a heartbeat written after the close can show the account flat
        if after and (acct != 0 or strat != 0):
            if upd >= close + dt.timedelta(minutes=1):
                probs[f'{a}:notflat'] = f"{a}: NOT FLAT after the close - account {acct:+d}, strategy {strat:+d}. Flatten now ({FLAT_RULE})."
            elif now >= close + dt.timedelta(minutes=CONFIRM_MIN):
                live = recorder_position(a, st.get('instrument', ''), upd, acct)     # early closes: no bar (heartbeat) after the close
                if live is None:
                    probs[f'{a}:notflat'] = (f"{a}: flat NOT CONFIRMED after the close - no strategy report since {upd:%H:%M} ET, last known "
                                             f"account {acct:+d}, strategy {strat:+d}. Check NinjaTrader or the Rithmic app now ({FLAT_RULE}).")
                elif live != 0:
                    probs[f'{a}:notflat'] = f"{a}: NOT FLAT after the close - account {live:+d} by the live fills record. Flatten now ({FLAT_RULE})."
        if night and acct != 0:
            live = recorder_position(a, st.get('instrument', ''), upd, acct)
            held = acct if live is None else live
            if held != 0:
                probs[f'{a}:overnight'] = (f"{a}: holding {held:+d} contracts outside trading hours (last report {upd:%m-%d %H:%M} ET). "
                                           f"Bulenox forbids overnight positions - flatten in NinjaTrader or the Rithmic app.")
    if in_s and nt_running:                  # the tick recorder (status rewritten at least every 60 s while market data arrives)
        try:
            rs = json.load(open(os.path.join(folder, 'TapeRecorder_status.json'), encoding='utf-8'))
            ru = dt.datetime.strptime(rs.get('updated_utc', '')[:19], '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
            bad = f"state '{rs.get('state')}' {rs.get('note') or ''}".strip() if rs.get('state') != 'running' else (
                f"no update for {ago((now - ru).total_seconds() / 60)}" if (now - ru).total_seconds() > 300 else '')
        except Exception as e: bad = f"no readable status file ({type(e).__name__})"
        if bad: probs['recorder'] = (f"The tick recorder is not recording ({bad}) - ticks and quotes for today are being lost. "
                                     f"Check that TapeRecorder is on the MNQ chart in NinjaTrader (and save the workspace).")
    return probs, in_s or after


def commit_pct():
    class M(ctypes.Structure):
        _fields_ = [('len', ctypes.c_ulong), ('load', ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                    ('tphys', 'aphys', 'tpage', 'apage', 'tvirt', 'avirt', 'aext')]
    try:
        m = M(); m.len = ctypes.sizeof(M)
        return 100.0 * (1 - m.apage / m.tpage) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)) and m.tpage else None
    except Exception: return None


def boot_time():
    """Windows boot time in UTC (naive; UTC so the DST change cannot look like a restart). The tick count runs on through sleep."""
    try:
        f = ctypes.windll.kernel32.GetTickCount64; f.restype = ctypes.c_ulonglong
        return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(milliseconds=f())
    except Exception: return None


def system_checks(in_s, procs, mem_open=False):
    """mem_open: the memory alert is already open - it then clears only below MEM_CLEAR (2026-10-23: at ~95% it flapped
    alert / all-clear four times in six minutes)."""
    probs = {}
    if 'onedrive.exe' in procs:
        probs['onedrive'] = ("OneDrive is running. On 10-22 it overwrote the NinjaTrader folder with August files - quit it now "
                             "(tray icon > Settings > Pause/Quit); NinjaTrader lives inside OneDrive Documents.")
    for d in ('C', 'D'):
        try: free = shutil.disk_usage(d + ':\\').free / 1e9
        except Exception: continue
        if free < DISK_GB: probs[f'disk_{d}'] = (f"Disk {d}: has only {free:.0f} GB free - the tick recorder writes about 3.5 GB a day, so logs, "
                                                 f"ticks and backups start failing within weeks. Free some space (or tell Claude to move old raw tick files).")
    pct = commit_pct() if in_s else None
    if pct is not None and pct >= (MEM_CLEAR if mem_open else MEM_PCT):
        name = lambda k: (lambda d: d[:-4] if d.lower().endswith('.exe') else d)(DISPLAY.get(k, k))
        top = sorted(((v, name(k)) for k, v in procs.items() if isinstance(v, (int, float))), reverse=True)[:3]
        probs['memory'] = (f"PC memory is {pct:.0f}% used during the session - NinjaTrader could run out of memory. Biggest users: "
                           + (', '.join(f"{n} {v / 1e9:.1f} GB" for v, n in top) if top else 'unknown') + ". Close what you can spare.")
    return probs


def one_line(note, n=300):
    """A blocked backup's note on one line: a failed test keeps its FAIL lines. 2026-10-29 07:34 the note was the test's last
    500 characters, cut to 300 - the phone got a half word and three PASS lines, and the FAIL line was cut off."""
    lines = [l.strip() for l in str(note or '').splitlines() if l.strip()]
    s = ' '.join(lines)
    if len(lines) > 1:
        fails = [l for l in lines if l.startswith('FAIL')]
        s = (lines[0].split(': ', 1)[0] if ': ' in lines[0] else lines[0]) + ': ' + ' | '.join(fails or lines[-1:])
    s = ' '.join(s.split())
    return s if len(s) <= n else s[:n - 3] + '...'


def pipeline_checks(folder, now):
    """The nightly archive and the hourly GitHub push: stopped, or blocked by a gate (2026-10-22 audit: nothing told the phone)."""
    probs = {}
    try:
        st = json.load(open(os.path.join(folder, 'NoiseArea_archive_status.json'), encoding='utf-8'))
        age = (now - dt.datetime.strptime(st['finished'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
        if age > 26: probs['archive'] = f"The nightly data archive last ran {ago(age * 60)} ago - check the Windows task 'NoiseArea nightly archive'."
    except Exception: pass
    try:
        st = json.load(open(os.path.join(folder, 'NoiseArea_autopush_status.json'), encoding='utf-8'))
        age = (now - dt.datetime.strptime(st['time'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
        if st.get('state') not in ('pushed', 'no_changes', 'dry_run_ok'):
            probs['autopush'] = f"The hourly GitHub backup was blocked ({st.get('state')}): {one_line(st.get('note'))} - tell Claude."
        elif age > 3: probs['autopush'] = f"The hourly GitHub backup last ran {ago(age * 60)} ago - check the Windows task 'NoiseArea autopush'."
    except Exception: pass
    return probs


def repeat_min(k): return 360 if k in ('archive', 'autopush') else 1440 if (k.endswith(':bars') or k.startswith('disk_')) else REPEAT_MIN


def rechecked(k, in_s, after):
    """Clear an alert only when this run evaluated that check again."""
    s = k.rpartition(':')[2]
    if s in ('nt_down', 'stale', 'state', 'conn', 'desync', 'nofile', 'history', 'size', 'setting', 'nofill', 'memory', 'recorder', 'bars',
             'feedlag', 'nodata'): return in_s
    if s == 'notflat': return after
    if s == 'overnight': return not (in_s or after)
    return True


def priority(k, msg):
    s = k.rpartition(':')[2]
    return 4 if s in ('disk_C', 'disk_D', 'memory', 'gap', 'recorder', 'archive', 'autopush', 'bars', 'feedlag') or 'daily loss stop' in msg else 5


def load_cfg():
    for p in CONFIGS:
        try:
            if os.path.exists(p): return json.load(open(p, encoding='utf-8'))
        except Exception: continue
    return {}


def push(cfg, title, msg, priority, tags='warning', tries=2):
    """ntfy push to {"ntfy_server" (default https://ntfy.sh)}/{"ntfy_topic"}; also used by archive_nightly.py's evening summary.
    Returns ['ntfy'] when sent, ['ntfy failed (...)'] when not, [] when push is off."""
    if not cfg.get('ntfy_topic'): return []
    url = f"{cfg.get('ntfy_server', 'https://ntfy.sh').rstrip('/')}/{cfg['ntfy_topic']}"; err = None
    for i in range(1 if _net['down'] else tries):
        try:
            req = urllib.request.Request(url, data=msg.encode('utf-8'), method='POST', headers={
                'Title': title.encode('ascii', 'replace').decode(), 'Priority': str(priority), 'Tags': tags, 'User-Agent': 'NoiseArea-watchdog/1.0'})
            urllib.request.urlopen(req, timeout=8).read(); _net['down'] = False
            return ['ntfy']
        except Exception as e:
            err = e
            if i + 1 < tries and not _net['down']: time.sleep(3)
    _net['down'] = True
    return [f'ntfy failed ({err})']


def ping_healthchecks(cfg, fail=False):
    if cfg.get('healthchecks_url'):
        try: urllib.request.urlopen(cfg['healthchecks_url'].rstrip('/') + ('/fail' if fail else ''), timeout=8).read()
        except Exception: pass


def parse(s): return dt.datetime.strptime(s, '%Y-%m-%d %H:%M')


def send(cfg, st, rows, ts, key, title, msg, pri, tags='warning'):
    """Push and record the outcome; a failed push is queued in the status file and retried on later runs."""
    r = push(cfg, title, msg, pri, tags)
    if r == ['ntfy']: rows.append((ts, 'pushed', key, title))
    elif r:
        rows.append((ts, 'push_failed', key, r[0][:200]))
        st['pending'] = (st.get('pending') or [])[-19:] + [{'since': ts, 'key': key, 'title': title, 'msg': msg, 'pri': pri, 'tags': tags}]


def retry_pending(cfg, st, rows, ts, now):
    keep = []
    for p in st.get('pending') or []:
        if (now - parse(p['since'])).total_seconds() > 12 * 3600: rows.append((ts, 'push_dropped', p['key'], f"{p['title']} (since {p['since']})")); continue
        if _net['down']: keep.append(p); continue
        r = push(cfg, p['title'], f"[sent late, raised {p['since']} ET] {p['msg']}", p['pri'], p['tags'], tries=1)
        if r == ['ntfy']: rows.append((ts, 'pushed_late', p['key'], f"{p['title']} (raised {p['since']})"))
        else: keep.append(p)
    st['pending'] = keep


def run(a, cfg, live):
    now = dt.datetime.strptime(a.now, '%Y-%m-%d %H:%M') if a.now else dt.datetime.now()
    procs = processes() if (live or a.nt_running is None) else {}
    nt = (a.nt_running == 'yes') if a.nt_running else 'ninjatrader.exe' in procs
    statp = os.path.join(a.folder, 'NoiseArea_watchdog_status.json')
    try: prev = json.load(open(statp, encoding='utf-8'))
    except Exception: prev = {}
    st = {'pending': prev.get('pending') or []}
    opened = prev.get('open') or {}; mem = prev.get('hb') or {}
    rows = []; ts = now.strftime('%Y-%m-%d %H:%M')
    retry_pending(cfg, st, rows, ts, now)
    in_s, after = session_window(now)
    if prev.get('broken'):
        rows.append((ts, 'resolved', 'watchdog', f"working again (broken since {prev.get('broken_since')}: {prev['broken']})"))
        send(cfg, st, rows, ts, 'watchdog', 'NoiseArea OK: watchdog working again', f"The watchdog runs again (it failed since {prev.get('broken_since')}).", 3, 'white_check_mark')
    if in_s:                                 # alerts left open from an earlier day are raised fresh (priority 5) if still true
        for k in [k for k in opened if opened[k]['since'][:10] < ts[:10]]:
            rows.append((ts, 'expired', k, f"open since {opened[k]['since']}; checked fresh today")); del opened[k]
    probs, active = checks(a.folder, now, nt, mem)
    probs.update(system_checks(in_s, procs, 'memory' in opened)); probs.update(pipeline_checks(a.folder, now))
    real = os.path.normcase(os.path.abspath(a.folder)) == os.path.normcase(EXPORTS)
    cpu = cpu_sample(CPU_PREV if real else os.path.join(a.folder, 'records', 'watchdog_cpu_prev.json')) if live else None
    lag = feed_lag(a.folder, now) if (in_s and nt) else None
    if lag is not None and lag >= (LAG_CLEAR if 'feedlag' in opened else LAG_S):
        who = ''
        if cpu and cpu[1]:
            who = (" Busiest programs over the last 2 minutes: " + ', '.join(f"{n} {v:.0f}%" for v, n in cpu[1][:3])
                   + (f" (whole PC {cpu[0]:.0f}% busy)." if cpu[0] is not None else "."))
        if cpu and cpu[0] is not None and cpu[0] < 50:      # 2026-10-27: at 2% busy, "close programs" was the wrong advice
            todo = (" The PC is not busy, so closing programs will not help: the data is held up on its way in (Rithmic's data "
                    "server, the internet line or NinjaTrader's data handling - a one-second probe is recording which). Until it "
                    "clears, an order sent off the chart's price can fill far from it.")
        else: todo = " Close heavy charts, indicators or other programs; if it lasts, restart NinjaTrader when flat."
        probs['feedlag'] = (f"NinjaTrader is receiving market data {lag:.0f} s late - its prices, the strategy's decisions and any manual "
                            f"orders are working on {lag:.0f}-second-old prices.{who}{todo}")
    stop = data_stop(a.folder, now) if (in_s and nt) else None
    if stop:
        try:
            import perfprobe; sv = perfprobe.md_servers(a.folder); conn = sv[-1][3] if sv and sv[-1][3] else ''
        except Exception: conn = ''
        probs['nodata'] = (f"No market data has reached NinjaTrader for {ago(stop[0])}: the last tick arrived at {stop[1]:%H:%M:%S} ET"
                           + (f", already {stop[2]:.0f} s late" if stop[2] >= LAG_S else '') + ". The connection can still show Connected. "
                           f"Look at the MNQ chart: if the price is not moving, disconnect and reconnect '{conn or 'the Rithmic connection'}' "
                           f"in NinjaTrader (if a position is open, check it in the Rithmic app first), then re-enable NoiseArea. If the "
                           f"price is moving, only the tick recorder stopped: check TapeRecorder on its chart.")
        probs.pop('recorder', None)          # the recorder's "no update" is the same silence: one alert, not two
        for k in ('feedlag', 'recorder'):
            if k in opened:                  # no "OK: market data late" push while nothing arrives at all
                rows.append((ts, 'superseded', k, f"replaced by 'nodata' (since {opened[k]['since']})")); del opened[k]
    boot = boot_time() if live else None
    woke = prev.get('woke_et', '')
    if live and prev.get('checked_et'):
        pc = parse(prev['checked_et']); gap = (now - pc).total_seconds() / 60
        if boot and prev.get('boot_utc') and abs((boot - parse(prev['boot_utc'])).total_seconds()) > 180:
            end = session_end(now.date()); soon = end is not None and now.hour * 60 + now.minute < end
            local = boot.replace(tzinfo=dt.timezone.utc).astimezone().replace(tzinfo=None)
            msg = (f"This PC restarted at {local:%H:%M} ET (last check before that {prev['checked_et']}). After a restart NinjaTrader must be "
                   f"started, connected to Bulenox and NoiseArea enabled by hand{' before 09:25' if soon and now.hour < 9 else ''}.")
            rows.append((ts, 'RESTART', 'restart', msg)); send(cfg, st, rows, ts, 'restart', 'NoiseArea: PC restarted', msg, 5 if soon else 4)
            woke = ts
        elif gap > GAP_MIN:
            msg = (f"No checks ran from {prev['checked_et']} to {ts} ET ({ago(gap)}) - the PC was asleep or off, or the watchdog task stopped. "
                   f"Check that NinjaTrader and NoiseArea are still running.")
            rows.append((ts, 'GAP', 'gap', msg))
            if in_s or after or any(session_window(pc + dt.timedelta(minutes=i))[0] for i in range(0, int(gap), 10)):
                send(cfg, st, rows, ts, 'gap', 'NoiseArea: checks missed', msg, 4)
            woke = ts
    if woke and (now - parse(woke)).total_seconds() < WAKE_GRACE_MIN * 60:      # just woke / restarted: the hourly backup and the
        for k in ('autopush', 'archive'):                                          # nightly archive catch up on their own
            if k in probs and k not in opened and 'last ran' in probs[k]: del probs[k]
    for k, msg in probs.items():
        o = opened.get(k)
        if o is None:
            opened[k] = {'since': ts, 'last_alert': ts}; rows.append((ts, 'ALERT', k, msg))
            send(cfg, st, rows, ts, k, f"NoiseArea: {label(k)}", msg, priority(k, msg))
        elif (now - parse(o['last_alert'])).total_seconds() >= repeat_min(k) * 60:
            o['last_alert'] = ts; rows.append((ts, 'STILL', k, f"{msg} (since {o['since']})"))
            send(cfg, st, rows, ts, k, f"NoiseArea still: {label(k)}", f"{msg} (since {o['since']} ET)", 4)
    for k in [k for k in opened if k not in probs and rechecked(k, in_s, after)]:
        rows.append((ts, 'resolved', k, f"cleared (since {opened[k]['since']})"))
        send(cfg, st, rows, ts, k, f"NoiseArea OK: {label(k)}", f"Back to normal: {label(k)} (from {opened[k]['since'][11:]} to {ts[11:]} ET).", 3, 'white_check_mark')
        del opened[k]
    last_ok = prev.get('last_ok_sample', '')
    if active and not probs and not opened and (not last_ok or (now - parse(last_ok)).total_seconds() >= OK_SAMPLE_MIN * 60):
        rows.append((ts, 'ok', '', "NinjaTrader running, all accounts healthy")); last_ok = ts
    log = os.path.join(a.folder, 'records', 'watchdog_log.csv'); os.makedirs(os.path.dirname(log), exist_ok=True)
    if in_s or after:                        # 2026-10-26: one performance row per run, so a slow morning can be traced afterwards
        pl = os.path.join(a.folder, 'records', 'perf_log.csv')
        try:
            new = not os.path.exists(pl); cp = commit_pct(); ntb = procs.get('ninjatrader.exe') if isinstance(procs, dict) else None
            with open(pl, 'a', newline='', encoding='utf-8') as f:
                w = csv.writer(f)
                if new: w.writerow(['time_et', 'feed_lag_s', 'pc_cpu_pct', 'nt_cpu_pct', 'memory_pct', 'nt_private_gb', 'top_cpu'])
                w.writerow([ts, '' if lag is None else f"{lag:.2f}", '' if not cpu or cpu[0] is None else f"{cpu[0]:.0f}",
                            '' if not cpu or cpu[2] is None else f"{cpu[2]:.0f}", '' if cp is None else f"{cp:.0f}",
                            '' if not ntb else f"{ntb / 1e9:.2f}", '' if not cpu else '; '.join(f"{n} {v:.0f}" for v, n in cpu[1][:3])])
        except Exception: pass
    if rows:
        new = not os.path.exists(log)
        with open(log, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new: w.writerow(['time_et', 'event', 'key', 'detail'])
            w.writerows(rows)
    if live: ping_healthchecks(cfg)
    last_clock = prev.get('last_clock_probe', '')
    probe = live and (not last_clock or (now - parse(last_clock)).total_seconds() >= CLOCK_MIN * 60)
    st.update({'checked_et': ts, 'in_session_window': active, 'ninjatrader_running': nt, 'open': opened, 'last_ok_sample': last_ok,
               'last_clock_probe': ts if probe else last_clock, 'hb': mem, 'boot_utc': f"{boot:%Y-%m-%d %H:%M}" if boot else prev.get('boot_utc', ''),
               'woke_et': woke, 'feed_lag_s': None if lag is None else round(lag, 2),
               'push': 'on' if cfg.get('ntfy_topic') else 'off (no D:\\nt_logs\\watchdog_config.json, or a test run)',
               'healthchecks': 'on' if cfg.get('healthchecks_url') else 'off'})
    with open(statp + '.tmp', 'w', encoding='utf-8') as f: json.dump(st, f, indent=1)
    os.replace(statp + '.tmp', statp)                 # status first: the clock probe below may take seconds
    if probe:
        try: subprocess.run([sys.executable.replace('pythonw.exe', 'python.exe'), os.path.join(HERE, 'clockprobe.py')],
                            capture_output=True, timeout=30, creationflags=NOWIN)
        except Exception: pass
    if live and nt and (in_s or after) and probe_due(now, lag):   # 2026-10-27: one-second samples of where data gets held up
        try:
            import perfprobe
            pid = next((int(p) for p, v in CPUT.items() if v[0] == 'ninjatrader.exe'), None)
            perfprobe.sample(a.folder, until=START + PROBE_S, pid=pid)
        except Exception: pass
    if sys.stdout is not None: print(json.dumps({k: v for k, v in st.items() if k != 'hb'})); [print(','.join(r)) for r in rows]


def broken(a, cfg, live, e):
    """The watchdog itself failed: say so in the status file, the log and (at most hourly) on the phone."""
    now = dt.datetime.now(); ts = now.strftime('%Y-%m-%d %H:%M'); err = f"{type(e).__name__}: {e}"[:300]
    statp = os.path.join(a.folder, 'NoiseArea_watchdog_status.json')
    try: st = json.load(open(statp, encoding='utf-8'))
    except Exception: st = {}
    rows = [(ts, 'BROKEN', 'watchdog', err)]
    if not st.get('broken_pushed') or (now - parse(st['broken_pushed'])).total_seconds() >= 3600:
        r = push(cfg, 'NoiseArea: WATCHDOG BROKEN', f"The NoiseArea watchdog failed: {err}. Problems are NOT being checked until this is fixed - tell Claude.", 5)
        rows.append((ts, 'pushed' if r == ['ntfy'] else 'push_failed', 'watchdog', (r or ['push off'])[0][:200]))
        if r == ['ntfy']: st['broken_pushed'] = ts
    st.update({'broken': err, 'broken_since': st.get('broken_since') or ts, 'broken_last': ts})
    log = os.path.join(a.folder, 'records', 'watchdog_log.csv')
    def write_log():
        os.makedirs(os.path.dirname(log), exist_ok=True); new = not os.path.exists(log)
        with open(log, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new: w.writerow(['time_et', 'event', 'key', 'detail'])
            w.writerows(rows)
    def write_status():
        with open(statp, 'w', encoding='utf-8') as f: json.dump(st, f, indent=1)
    def write_err():
        with open(ERRLOG, 'a', encoding='utf-8') as f: f.write(f"{ts}\n{traceback.format_exc()}\n")
    for f in (write_status, write_log, write_err):
        try: f()
        except Exception: pass
    if live: ping_healthchecks(cfg, fail=True)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--folder', default=EXPORTS); ap.add_argument('--now', default=None)
    ap.add_argument('--nt-running', choices=['yes', 'no'], default=None)
    ap.add_argument('--test-push', action='store_true', help='send one test message to the phone and stop')
    ap.add_argument('--push', action='store_true', help='let a test run (--folder / --now) push to the phone'); a = ap.parse_args()
    cfg = load_cfg()
    live = os.path.normcase(os.path.abspath(a.folder)) == os.path.normcase(EXPORTS) and not a.now and not a.nt_running
    if not (live or a.push or a.test_push):           # a test run must never buzz the phone or ping healthchecks
        cfg = {k: v for k, v in cfg.items() if k not in ('ntfy_topic', 'healthchecks_url')}
    if a.test_push:
        r = push(cfg, 'NoiseArea test', f"Test from the NoiseArea watchdog at {dt.datetime.now():%Y-%m-%d %H:%M:%S} ET. Alerts reach this phone.", 3, 'white_check_mark')
        print(r or f"push is off: no ntfy_topic in {CONFIG}"); sys.exit(0 if r == ['ntfy'] else 1)
    try: run(a, cfg, live)
    except Exception as e:
        broken(a, cfg, live, e)
        if sys.stdout is not None: print(f"WATCHDOG BROKEN: {type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
