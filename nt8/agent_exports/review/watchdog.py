r"""
watchdog.py - intraday watchdog for NoiseArea (2026-10-22; research\INSTITUTIONAL_OPS_20261022.txt item 1: the setup checked itself
only at 09:12 and 16:38, so a strategy that stopped at 11:00 - NinjaTrader closed, crashed or disconnected - was found at 16:38).

    python watchdog.py            one check (the Windows task "NoiseArea watchdog" runs it every 2 minutes with pythonw: no window)
    python watchdog.py --folder <exports> --now "YYYY-MM-DD HH:MM"   test against other files / another time

During a CME session (weekdays 09:25 ET to the close + 10 min; holidays and early closes from daily_review.py) it checks:
  NinjaTrader.exe is running; for every account in accounts.json: the heartbeat is younger than 11 minutes, connected, state
  "running", the account's position equals the strategy's; after the session end + 2 minutes the account must be flat (judged
  from a heartbeat written after the close; none by the close + 8 minutes with a position last known open is also an alert).
A problem is recorded once when it starts, repeated every 15 minutes while it lasts, and marked resolved when it clears:
  agent_exports\records\watchdog_log.csv (plus an "ok" sample every 30 minutes = an uptime history) and
  agent_exports\NoiseArea_watchdog_status.json (current state; the daily review prints it).
    python watchdog.py --test-push  one test message to the phone
Phone push: OFF until D:\nt_logs\watchdog_config.json exists with {"ntfy_topic": "<topic>"} (free ntfy app; "ntfy_server" for a
server other than https://ntfy.sh - since 2026-10-22 https://ntfy-me.com, the server of the app on his phone) and/or
{"healthchecks_url": "<ping URL>"} (free healthchecks.io dead-man switch: alerts from outside when this PC or its internet dies).
Nothing is sent anywhere until that file exists. Never touches NinjaTrader.
"""
import os, sys, json, csv, argparse, subprocess, urllib.request, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
EXPORTS = os.path.normpath(os.path.join(HERE, '..'))
CONFIG = r'D:\nt_logs\watchdog_config.json'
RECORDER = r'D:\nt_recorder'           # TapeRecorder output; its live fills confirm flat when no heartbeat follows the close
STALE_MIN, REPEAT_MIN, OK_SAMPLE_MIN = 11, 15, 30
CONFIRM_MIN = 8                     # minutes after the close by which the first post-close heartbeat (the 16:05 bar) must show flat
NOWIN = 0x08000000 if os.name == 'nt' else 0


def ninjatrader_running():
    p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq NinjaTrader.exe', '/NH'], capture_output=True, text=True, creationflags=NOWIN)
    return 'NinjaTrader.exe' in p.stdout


def session_end(d):
    """Close of the day session in minutes after midnight ET (early closes included), None on weekends and full closures."""
    import daily_review as R
    if d.weekday() >= 5 or d in R.full_closures(d.year): return None
    return R.early_closes(d.year).get(d, 16 * 60)


def session_window(now):
    """(in_session, after_close) for a US/Eastern naive datetime, using the review's CME calendar."""
    end = session_end(now.date()); m = now.hour * 60 + now.minute
    if end is None: return False, False
    return (9 * 60 + 25 <= m <= end + 10), (end + 2 <= m <= end + 60)


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


def checks(folder, now, nt_running):
    reg = json.load(open(os.path.join(HERE, 'accounts.json'), encoding='utf-8'))['accounts']
    in_s, after = session_window(now); probs = {}
    if not (in_s or after): return probs, in_s or after
    close = dt.datetime.combine(now.date(), dt.time()) + dt.timedelta(minutes=session_end(now.date()))
    if in_s and not nt_running: probs['nt_down'] = "NinjaTrader is not running during the session"
    for r in reg:
        a = r['account']; p = os.path.join(folder, f"NoiseArea_status_MNQ_{a}.json")
        try: st = json.load(open(p, encoding='utf-8'))
        except Exception: probs[f'{a}:nofile'] = f"{a}: no readable heartbeat file"; continue
        try: upd = dt.datetime.strptime(st.get('updated_et') or st.get('updated_local'), '%Y-%m-%d %H:%M:%S')
        except Exception: upd = dt.datetime(1900, 1, 1)
        age = (now - upd).total_seconds() / 60
        pos = str(st.get('position', 'Flat')).lower(); q = int(st.get('quantity') or 0)
        strat = q if pos == 'long' else (-q if pos == 'short' else 0); acct = int(st.get('account_position') or 0)
        if in_s:
            if age > STALE_MIN: probs[f'{a}:stale'] = f"{a}: heartbeat {age:.0f} min old (last {upd:%H:%M}) - strategy not running"
            elif str(st.get('state')) != 'running': probs[f'{a}:state'] = f"{a}: strategy state '{st.get('state')}' {st.get('halt_note') or ''}".strip()
            if st.get('connected') is False: probs[f'{a}:conn'] = f"{a}: account connection down"
            if acct != strat: probs[f'{a}:desync'] = f"{a}: account position {acct} but strategy {strat}"
        # 2026-10-22: the heartbeat is written at a bar close BEFORE that bar's exit order fills (16:00:00 still shows the trade, the
        # fill is 16:00:01, the next heartbeat 16:05), so only a heartbeat written after the close can show the account flat.
        if after and (acct != 0 or strat != 0):
            if upd >= close + dt.timedelta(minutes=1):
                probs[f'{a}:notflat'] = f"{a}: NOT FLAT after the close - account {acct}, strategy {strat} (Bulenox: flat by 3:59 PM CT)"
            elif now >= close + dt.timedelta(minutes=CONFIRM_MIN):
                live = recorder_position(a, st.get('instrument', ''), upd, acct)     # early closes: no bar (heartbeat) after the close
                if live is None:
                    probs[f'{a}:notflat'] = (f"{a}: flat NOT CONFIRMED after the close - no heartbeat since {upd:%H:%M}, last known account "
                                             f"{acct}, strategy {strat} (Bulenox: flat by 3:59 PM CT)")
                elif live != 0:
                    probs[f'{a}:notflat'] = f"{a}: NOT FLAT after the close - account {live} by the recorder's live fills (Bulenox: flat by 3:59 PM CT)"
    return probs, True


def load_cfg():
    try: return json.load(open(CONFIG, encoding='utf-8')) if os.path.exists(CONFIG) else {}
    except Exception: return {}


def push(cfg, title, msg, priority, tags='warning'):
    """ntfy push to {"ntfy_server" (default https://ntfy.sh)}/{"ntfy_topic"}; also used by archive_nightly.py's evening summary."""
    sent = []
    if cfg.get('ntfy_topic'):
        url = f"{cfg.get('ntfy_server', 'https://ntfy.sh').rstrip('/')}/{cfg['ntfy_topic']}"
        try:
            req = urllib.request.Request(url, data=msg.encode('utf-8'), method='POST', headers={
                'Title': title, 'Priority': str(priority), 'Tags': tags, 'User-Agent': 'NoiseArea-watchdog/1.0'})
            urllib.request.urlopen(req, timeout=10).read(); sent.append('ntfy')
        except Exception as e: sent.append(f'ntfy failed ({e})')
    return sent


def ping_healthchecks(cfg, fail=False):
    if cfg.get('healthchecks_url'):
        try: urllib.request.urlopen(cfg['healthchecks_url'] + ('/fail' if fail else ''), timeout=10).read()
        except Exception: pass


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--folder', default=EXPORTS); ap.add_argument('--now', default=None)
    ap.add_argument('--nt-running', choices=['yes', 'no'], default=None)
    ap.add_argument('--test-push', action='store_true', help='send one test message to the phone and stop'); a = ap.parse_args()
    cfg = load_cfg()
    if a.test_push:
        r = push(cfg, 'NoiseArea test', f"Test from the NoiseArea watchdog at {dt.datetime.now():%Y-%m-%d %H:%M:%S} ET. Alerts reach this phone.", 3, 'white_check_mark')
        print(r or f"push is off: no ntfy_topic in {CONFIG}"); sys.exit(0 if r == ['ntfy'] else 1)
    now = dt.datetime.strptime(a.now, '%Y-%m-%d %H:%M') if a.now else dt.datetime.now()
    nt = (a.nt_running == 'yes') if a.nt_running else ninjatrader_running()
    statp = os.path.join(a.folder, 'NoiseArea_watchdog_status.json')
    try: prev = json.load(open(statp, encoding='utf-8'))
    except Exception: prev = {'open': {}, 'last_ok_sample': ''}
    probs, active = checks(a.folder, now, nt)
    log = os.path.join(a.folder, 'records', 'watchdog_log.csv'); os.makedirs(os.path.dirname(log), exist_ok=True)
    rows = []; opened = prev.get('open', {}); ts = now.strftime('%Y-%m-%d %H:%M')
    for k, msg in probs.items():
        o = opened.get(k)
        if o is None:
            opened[k] = {'since': ts, 'last_alert': ts}; rows.append((ts, 'ALERT', k, msg))
            sent = push(cfg, 'NoiseArea ALERT', msg, 5)
            if sent: rows.append((ts, 'pushed', k, ','.join(sent)))
        elif (now - dt.datetime.strptime(o['last_alert'], '%Y-%m-%d %H:%M')).total_seconds() >= REPEAT_MIN * 60:
            o['last_alert'] = ts; rows.append((ts, 'STILL', k, f"{msg} (since {o['since']})"))
            sent = push(cfg, 'NoiseArea still broken', f"{msg} (since {o['since']})", 4)
            if sent: rows.append((ts, 'pushed', k, ','.join(sent)))
    in_s, after = session_window(now)
    def rechecked(k):                       # 2026-10-22: clear an alert only when this run actually evaluated that check again
        if k == 'nt_down' or k.split(':')[-1] in ('stale', 'state', 'conn', 'desync', 'nofile'): return in_s
        if k.endswith(':notflat'): return after
        return active
    for k in [k for k in opened if k not in probs and rechecked(k)]:
        rows.append((ts, 'resolved', k, f"cleared (since {opened[k]['since']})")); push(cfg, 'NoiseArea resolved', f"{k} cleared", 3); del opened[k]
    last_ok = prev.get('last_ok_sample', '')
    if active and not probs and not opened and (not last_ok or (now - dt.datetime.strptime(last_ok, '%Y-%m-%d %H:%M')).total_seconds() >= OK_SAMPLE_MIN * 60):
        rows.append((ts, 'ok', '', f"NinjaTrader running, all registered accounts healthy")); last_ok = ts
    if rows:
        new = not os.path.exists(log)
        with open(log, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if new: w.writerow(['time_et', 'event', 'key', 'detail'])
            w.writerows(rows)
    ping_healthchecks(cfg, fail=bool(probs))
    st = {'checked_et': ts, 'in_session_window': active, 'ninjatrader_running': nt, 'open': opened, 'last_ok_sample': last_ok,
          'push': 'on' if cfg.get('ntfy_topic') else 'off (no D:\\nt_logs\\watchdog_config.json)'}
    with open(statp + '.tmp', 'w', encoding='utf-8') as f: json.dump(st, f, indent=1)
    os.replace(statp + '.tmp', statp)
    if sys.stdout is not None: print(json.dumps(st)); [print(','.join(r)) for r in rows]


if __name__ == '__main__':
    main()
