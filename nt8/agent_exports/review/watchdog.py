r"""
watchdog.py - intraday watchdog for NoiseArea (2026-10-22; research\INSTITUTIONAL_OPS_20261022.txt item 1: the setup checked itself
only at 09:12 and 16:38, so a strategy that stopped at 11:00 - NinjaTrader closed, crashed or disconnected - was found at 16:38).

    python watchdog.py            one check (the Windows task "NoiseArea watchdog" runs it every 2 minutes with pythonw: no window)
    python watchdog.py --folder <exports> --now "YYYY-MM-DD HH:MM"   test against other files / another time

During a CME session (weekdays 09:25 ET to the close + 10 min; holidays and early closes from daily_review.py) it checks:
  NinjaTrader.exe is running; for every account in accounts.json: the heartbeat is younger than 11 minutes, connected, state
  "running", the account's position equals the strategy's; after the session end + 2 minutes the account must be flat.
A problem is recorded once when it starts, repeated every 15 minutes while it lasts, and marked resolved when it clears:
  agent_exports\records\watchdog_log.csv (plus an "ok" sample every 30 minutes = an uptime history) and
  agent_exports\NoiseArea_watchdog_status.json (current state; the daily review prints it).
Phone push: OFF until D:\nt_logs\watchdog_config.json exists with {"ntfy_topic": "<topic>"} (free ntfy.sh app) and/or
{"healthchecks_url": "<ping URL>"} (free healthchecks.io dead-man switch: alerts from outside when this PC or its internet dies).
Nothing is sent anywhere until that file exists. Never touches NinjaTrader.
"""
import os, sys, json, csv, argparse, subprocess, urllib.request, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
EXPORTS = os.path.normpath(os.path.join(HERE, '..'))
CONFIG = r'D:\nt_logs\watchdog_config.json'
STALE_MIN, REPEAT_MIN, OK_SAMPLE_MIN = 11, 15, 30
NOWIN = 0x08000000 if os.name == 'nt' else 0


def ninjatrader_running():
    p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq NinjaTrader.exe', '/NH'], capture_output=True, text=True, creationflags=NOWIN)
    return 'NinjaTrader.exe' in p.stdout


def session_window(now):
    """(in_session, after_close) for a US/Eastern naive datetime, using the review's CME calendar."""
    import daily_review as R
    d = now.date()
    if d.weekday() >= 5 or d in R.full_closures(d.year): return False, False
    end = R.early_closes(d.year).get(d, 16 * 60); m = now.hour * 60 + now.minute
    return (9 * 60 + 25 <= m <= end + 10), (end + 2 <= m <= end + 60)


def checks(folder, now, nt_running):
    reg = json.load(open(os.path.join(HERE, 'accounts.json'), encoding='utf-8'))['accounts']
    in_s, after = session_window(now); probs = {}
    if not (in_s or after): return probs, in_s or after
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
        if after and (acct != 0 or strat != 0):
            probs[f'{a}:notflat'] = f"{a}: NOT FLAT after the close - account {acct}, strategy {strat} (Bulenox: flat by 3:59 PM CT)"
    return probs, True


def push(cfg, title, msg, priority):
    sent = []
    if cfg.get('ntfy_topic'):
        try:
            req = urllib.request.Request(f"https://ntfy.sh/{cfg['ntfy_topic']}", data=msg.encode('utf-8'), method='POST',
                                         headers={'Title': title, 'Priority': str(priority), 'Tags': 'warning'})
            urllib.request.urlopen(req, timeout=10).read(); sent.append('ntfy')
        except Exception as e: sent.append(f'ntfy failed ({e})')
    return sent


def ping_healthchecks(cfg, fail=False):
    if cfg.get('healthchecks_url'):
        try: urllib.request.urlopen(cfg['healthchecks_url'] + ('/fail' if fail else ''), timeout=10).read()
        except Exception: pass


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--folder', default=EXPORTS); ap.add_argument('--now', default=None)
    ap.add_argument('--nt-running', choices=['yes', 'no'], default=None); a = ap.parse_args()
    now = dt.datetime.strptime(a.now, '%Y-%m-%d %H:%M') if a.now else dt.datetime.now()
    nt = (a.nt_running == 'yes') if a.nt_running else ninjatrader_running()
    cfg = {}
    if os.path.exists(CONFIG):
        try: cfg = json.load(open(CONFIG, encoding='utf-8'))
        except Exception: cfg = {}
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
    for k in [k for k in opened if k not in probs and active]:         # outside the session nothing was re-checked: keep it open
        rows.append((ts, 'resolved', k, f"cleared (since {opened[k]['since']})")); push(cfg, 'NoiseArea resolved', f"{k} cleared", 3); del opened[k]
    last_ok = prev.get('last_ok_sample', '')
    if active and not probs and (not last_ok or (now - dt.datetime.strptime(last_ok, '%Y-%m-%d %H:%M')).total_seconds() >= OK_SAMPLE_MIN * 60):
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
