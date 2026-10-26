"""Regression scenarios for watchdog.py (2026-10-22): python watchdog_test.py. Temp folders only; pushes are captured, never sent;
the live status, log, records and clock files are never touched."""
import os, sys, json, shutil, tempfile, argparse, datetime as dt
REVIEW = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REVIEW); sys.dont_write_bytecode = True
import watchdog as W

EXH = "recv_utc,exec_utc,account,instrument,exec_id,order_id,order_name,position,price,quantity,chart_bid,chart_ask"
REAL_ACCOUNTS = W.ACCOUNTS
bad = 0
sent = []
def fake_push(cfg, title, msg, pri, tags='warning', tries=2):     # like push(): nothing goes out without a topic
    if not cfg.get('ntfy_topic'): return []
    sent.append((title, msg, pri)); return ['ntfy']
W.push = fake_push
W.commit_pct = lambda: 50.0


def ok(name, cond, extra=''):
    global bad
    bad += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{extra}]" if not cond and extra else ''))


def folder(hb=None, account='Sim101', events=None, recorder=None, accounts=None):
    d = tempfile.mkdtemp(prefix='wdtest_')
    if hb is not None: write_hb(d, hb, account)
    if events: open(os.path.join(d, f'NoiseArea_events_MNQ_{account}.txt'), 'w', encoding='utf-8').write(events)
    if recorder is not None: json.dump(recorder, open(os.path.join(d, 'TapeRecorder_status.json'), 'w', encoding='utf-8'))
    acc = os.path.join(d, 'accounts.json')
    open(acc, 'w', encoding='utf-8').write(accounts if accounts is not None else json.dumps({'accounts': [{'account': 'Sim101', 'contracts': 1}]}))
    W.ACCOUNTS = acc; W.RECORDER = tempfile.mkdtemp(prefix='wdrec_')
    return d


def write_hb(d, hb, account='Sim101'):
    base = {'state': 'running', 'connected': True, 'position': 'Flat', 'quantity': 0, 'account_position': 0, 'desired': 0,
            'history_sessions': 14, 'instrument': 'MNQ 12-26'}
    base.update(hb); json.dump(base, open(os.path.join(d, f'NoiseArea_status_MNQ_{account}.json'), 'w', encoding='utf-8'))


def rec_ok(now):
    return {'state': 'running', 'updated_utc': dt.datetime.strptime(now, '%Y-%m-%d %H:%M').astimezone(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S.000000')}


def set_rec(d, now): json.dump(rec_ok(now), open(os.path.join(d, 'TapeRecorder_status.json'), 'w', encoding='utf-8'))


def fills(rec, rows):
    os.makedirs(os.path.join(rec, 'accounts'), exist_ok=True); by = {}
    for t, side, n, *acc in rows:
        u = dt.datetime.strptime(t, '%Y-%m-%d %H:%M:%S').astimezone(dt.timezone.utc)
        by.setdefault(u.date(), []).append(f"{u:%Y-%m-%d %H:%M:%S}.000000,{u:%Y-%m-%d %H:%M:%S}.000000,{acc[0] if acc else 'Sim101'},MNQ 12-26,x,y,NA,{side},29000,{n},,")
    for day, rs in by.items(): open(os.path.join(rec, 'accounts', f"{day:%Y-%m-%d}_executions.csv"), 'w', encoding='utf-8').write(EXH + '\n' + '\n'.join(rs) + '\n')


def chk(d, now, nt=True, mem=None):
    return W.checks(d, dt.datetime.strptime(now, '%Y-%m-%d %H:%M'), nt, mem)[0]


def cleanup(*ds):
    for x in ds: shutil.rmtree(x, ignore_errors=True)
    shutil.rmtree(W.RECORDER, ignore_errors=True)


print('--- after the close (the heartbeat is written before the exit fills) ---')
AFTER = [
    ('today 16:02: 16:00 heartbeat before the exit fill', '2026-10-22 16:00:00', -1, 'Short', 1, '2026-10-22 16:02', None, None),
    ('16:06: 16:05 heartbeat shows flat',                 '2026-10-22 16:04:59', 0, 'Flat', 0, '2026-10-22 16:06', None, None),
    ('16:06: 16:05 heartbeat still short (real miss)',    '2026-10-22 16:04:59', -1, 'Short', 1, '2026-10-22 16:06', None, 'NOT FLAT'),
    ('16:08: no beat since 16:00, short, no recorder',    '2026-10-22 16:00:00', -1, 'Short', 1, '2026-10-22 16:08', None, 'NOT CONFIRMED'),
    ('16:08: no beat since 16:00, recorder shows cover',  '2026-10-22 16:00:00', -1, 'Short', 1, '2026-10-22 16:08', [('2026-10-22 16:00:01', 'Long', 1)], None),
    ('16:09: no beat since 16:00, last known flat',       '2026-10-22 16:00:00', 0, 'Flat', 0, '2026-10-22 16:09', None, None),
    ('16:45: account short, strategy flat after close',   '2026-10-22 16:44:59', -1, 'Flat', 0, '2026-10-22 16:45', None, 'NOT FLAT'),
    ('11-27 close 13:15: 13:17 pre-fill heartbeat',       '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:17', None, None),
    ('11-27 13:24: no post-close beat, no recorder',      '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', None, 'NOT CONFIRMED'),
    ('11-27 13:24: recorder shows the 13:15:01 sell',     '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', [('2026-11-27 13:15:01', 'Short', 1)], None),
    ('11-27 13:24: recorder has no exit fill',            '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', [('2026-11-27 10:30:00', 'Long', 1)], 'NOT FLAT'),
    ('11-27 13:24: other account fill does not count',    '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', [('2026-11-27 13:15:01', 'Short', 1, 'Other')], 'NOT FLAT'),
]
for name, upd, acct, pos, q, now, fl, want in AFTER:
    d = folder({'updated_et': upd, 'account_position': acct, 'position': pos, 'quantity': q})
    if fl: fills(W.RECORDER, fl)
    msg = chk(d, now).get('Sim101:notflat')
    ok(name, (want is None and msg is None) or (want is not None and msg is not None and want in msg), msg or 'no alert'); cleanup(d)

print('--- in session ---')
N = '2026-10-22 10:02'; U = '2026-10-22 10:00:00'
CASES = [
    ('healthy: nothing',                         {}, None, True, set()),
    ('NinjaTrader closed at 15:59: alert',       {'updated_et': '2026-10-22 15:55:00'}, '2026-10-22 15:59', False, {'nt_down'}),
    ('NinjaTrader closed at 16:05: no alert',    {'updated_et': '2026-10-22 16:00:00'}, '2026-10-22 16:05', False, set()),
    ('heartbeat 15 min old: strategy silent',    {'updated_et': '2026-10-22 09:47:00'}, None, True, {'Sim101:stale'}),
    ('state terminated',                         {'state': 'terminated'}, None, True, {'Sim101:state'}),
    ('connection down',                          {'connected': False}, None, True, {'Sim101:conn'}),
    ('account 0, strategy short: desync',        {'position': 'Short', 'quantity': 1, 'desired': -1}, None, True, {'Sim101:desync'}),
    ('3 sessions of history',                    {'history_sessions': 3}, None, True, {'Sim101:history'}),
    ('account long 6, registered 1: size',       {'position': 'Long', 'quantity': 6, 'account_position': 6, 'desired': 1}, None, True, {'Sim101:size'}),
]
for name, hb, now, nt, want in CASES:
    h = {'updated_et': U}; h.update(hb); now = now or N
    d = folder(h, recorder=rec_ok(now)); p = set(chk(d, now, nt))
    ok(name, p == want, f"got {sorted(p)}"); cleanup(d)
d = folder({'updated_et': U, 'state': 'halted', 'halt_note': 'daily loss stop hit'}, recorder=rec_ok(N)); p = chk(d, N)
ok('halted: alert at priority 4', 'Sim101:state' in p and W.priority('Sim101:state', p['Sim101:state']) == 4, p); cleanup(d)
d = folder({'updated_et': U}, events='2026-10-22 09:09:34|live|Sim101|contracts 4, daily loss stop 0, history sessions 14\n', recorder=rec_ok(N))
ok('started with Contracts 4, registered 1: setting', set(chk(d, N)) == {'Sim101:setting'}); cleanup(d)
d = folder({'updated_et': U}, accounts='{"accounts": [{"account": "Sim101", "contracts": 1},]}', recorder=rec_ok(N)); p = set(chk(d, N))
ok('accounts.json with a trailing comma: alert, no crash, no false unregistered', p == {'accounts_json'}, f"got {sorted(p)}"); cleanup(d)
d = folder({'updated_et': U}, recorder=rec_ok(N)); write_hb(d, {'updated_et': U, 'position': 'Short', 'quantity': 6, 'account_position': -6, 'desired': -1}, 'BX49277')
p = set(chk(d, N)); ok('unregistered account short 6: alert', p == {'BX49277:unregistered'}, f"got {sorted(p)}"); cleanup(d)
d = folder({'updated_et': '2026-10-22 16:00:00'}); write_hb(d, {'updated_et': '2026-10-22 16:04:59', 'position': 'Short', 'quantity': 6, 'account_position': -6}, 'BX49277')
p = set(chk(d, '2026-10-22 16:06')); ok('unregistered account short 6 at 16:06: not flat too', {'BX49277:unregistered', 'BX49277:notflat'} <= p, f"got {sorted(p)}"); cleanup(d)
d = folder({'updated_et': U}, recorder={'state': 'running', 'updated_utc': '2026-10-22 13:50:00.000000'})
ok('recorder status 12 min old: alert', set(chk(d, N)) == {'recorder'}); cleanup(d)
d = folder({'updated_et': U}, recorder={'state': 'failed', 'note': 'disk full', 'updated_utc': rec_ok(N)['updated_utc']})
p = chk(d, N); ok('recorder failed: alert with its note', 'recorder' in p and 'disk full' in p['recorder'], p); cleanup(d)
d = folder({'updated_et': U}); ok('recorder status missing: alert', 'recorder' in chk(d, N)); cleanup(d)

print('--- memory alert: hysteresis and the biggest users ---')
real_pct = W.commit_pct; P = {'wow.exe': 11.6e9, 'pythonw.exe': 7.1e9, 'chrome.exe': 3.9e9, 'ninjatrader.exe': 1.9e9}
W.DISPLAY.update({'wow.exe': 'Wow.exe', 'pythonw.exe': 'pythonw.exe', 'chrome.exe': 'chrome.exe', 'ninjatrader.exe': 'NinjaTrader.exe'})
W.commit_pct = lambda: 96.0; m = W.system_checks(True, P).get('memory', '')
ok('96%: alert names the three biggest users', 'Wow 11.6 GB' in m and 'pythonw 7.1 GB' in m and 'chrome 3.9 GB' in m and 'ninjatrader' not in m, m)
W.commit_pct = lambda: 92.0
ok('92% while open: stays open (no flapping)', 'memory' in W.system_checks(True, P, mem_open=True))
ok('92% while closed: no new alert', 'memory' not in W.system_checks(True, P, mem_open=False))
W.commit_pct = lambda: 89.0
ok('89% while open: clears', 'memory' not in W.system_checks(True, P, mem_open=True))
W.commit_pct = lambda: 99.0
ok('outside the session: no memory alert', 'memory' not in W.system_checks(False, P))
W.commit_pct = real_pct = (lambda: 50.0)

print('--- strategy missing bars (history hole after a restart) ---')
def bar_files(d, strat_times, live_times, day='2026-10-22'):
    H = 'time_et,open,high,low,close,volume\n'
    open(os.path.join(d, 'NoiseArea_bars_MNQ_strategy_Sim101.csv'), 'w').write(H + ''.join(f"{day} {t}:00,1,1,1,1,1\n" for t in strat_times))
    os.makedirs(os.path.join(W.RECORDER, 'MNQ_12-26'), exist_ok=True)
    open(os.path.join(W.RECORDER, 'MNQ_12-26', f'{day}_bars5min.csv'), 'w').write(H.strip() + ',recv_utc\n' + ''.join(f"{day} {t}:00,1,1,1,1,1,x\n" for t in live_times))
T = ['09:35', '09:40', '09:45', '09:50', '09:55', '10:00', '10:05', '10:10', '10:15', '10:20']
d = folder({'updated_et': '2026-10-22 10:20:00'}, recorder=rec_ok('2026-10-22 10:21'))
bar_files(d, [t for t in T if t not in ('10:00', '10:05')], T); p = chk(d, '2026-10-22 10:21')
ok('two bars missing after a restart: alert naming them, priority 4', 'Sim101:bars' in p and '10:00, 10:05' in p['Sim101:bars'] and W.priority('Sim101:bars', p['Sim101:bars']) == 4, p.get('Sim101:bars'))
cleanup(d)
d = folder({'updated_et': '2026-10-22 10:20:00'}, recorder=rec_ok('2026-10-22 10:21')); bar_files(d, T, T)
ok('complete bars: no alert', 'Sim101:bars' not in chk(d, '2026-10-22 10:21')); cleanup(d)
d = folder({'updated_et': '2026-10-22 10:20:00'}, recorder=rec_ok('2026-10-22 10:21')); bar_files(d, T, T[4:])
ok('recorder started late (fewer live bars): no false alert', 'Sim101:bars' not in chk(d, '2026-10-22 10:21')); cleanup(d)
ok('bars alert repeats at most daily', W.repeat_min('Sim101:bars') == 1440)

print('--- order not filled (two heartbeats in a row) ---')
d = folder({'updated_et': '2026-10-22 10:00:00', 'desired': 1}, recorder=rec_ok('2026-10-22 10:06')); mem = {}
ok('signal at 10:00 (pre-fill): no alert yet', 'Sim101:nofill' not in chk(d, '2026-10-22 10:01', mem=mem))
ok('same heartbeat at 10:03: still no alert', 'Sim101:nofill' not in chk(d, '2026-10-22 10:03', mem=mem))
write_hb(d, {'updated_et': '2026-10-22 10:05:00', 'desired': 1})
ok('10:05 heartbeat still flat: ALERT', 'Sim101:nofill' in chk(d, '2026-10-22 10:06', mem=mem))
ok('same heartbeat at 10:08: alert stays', 'Sim101:nofill' in chk(d, '2026-10-22 10:08', mem=mem))
write_hb(d, {'updated_et': '2026-10-22 10:10:00', 'desired': 1, 'position': 'Long', 'quantity': 1, 'account_position': 1})
ok('10:10 filled: cleared', 'Sim101:nofill' not in chk(d, '2026-10-22 10:11', mem=mem)); cleanup(d)
d = folder({'updated_et': '2026-10-22 10:00:00', 'desired': 1, 'state': 'halted'}, recorder=rec_ok('2026-10-22 10:06')); mem = {}
chk(d, '2026-10-22 10:01', mem=mem); write_hb(d, {'updated_et': '2026-10-22 10:05:00', 'desired': 1, 'state': 'halted'})
ok('halted (desired kept, no order by design): no "not filled"', 'Sim101:nofill' not in chk(d, '2026-10-22 10:06', mem=mem)); cleanup(d)

print('--- outside trading hours ---')
d = folder({'updated_et': '2026-10-22 19:55:00', 'account_position': 1})
ok('20:00 account long 1: overnight alert', set(chk(d, '2026-10-22 20:00')) == {'Sim101:overnight'}); cleanup(d)
d = folder({'updated_et': '2026-10-23 16:55:00'})
ok('Saturday, flat: nothing', set(chk(d, '2026-10-24 12:00')) == set()); cleanup(d)
d = folder({'updated_et': '2026-10-22 16:00:00', 'position': 'Short', 'quantity': 1, 'account_position': -1}); fills(W.RECORDER, [('2026-10-22 16:00:01', 'Long', 1)])
ok('night, last report pre-fill but recorder shows the cover: nothing', set(chk(d, '2026-10-22 22:00')) == set()); cleanup(d)
d = folder({'updated_et': '2026-10-23 08:55:00', 'account_position': -1})
ok('08:58 next morning account short: overnight alert', 'Sim101:overnight' in chk(d, '2026-10-23 08:58')); cleanup(d)

print('--- alert life cycle through run() (pushes captured) ---')
CFG = {'ntfy_topic': 't'}
def run(d, now, nt='yes'):
    sent.clear(); W.run(argparse.Namespace(folder=d, now=now, nt_running=nt, push=False, test_push=False), CFG, False)
    st = json.load(open(os.path.join(d, 'NoiseArea_watchdog_status.json'), encoding='utf-8'))
    lp = os.path.join(d, 'records', 'watchdog_log.csv')
    return st, (open(lp, encoding='utf-8').read() if os.path.exists(lp) else ''), list(sent)
d = folder({'updated_et': '2026-10-22 09:45:00'}, recorder=rec_ok('2026-10-22 10:00'))
st, log, s = run(d, '2026-10-22 10:00'); ok('10:00 stale: one push, priority 5, plain title', len(s) == 1 and s[0][2] == 5 and s[0][0] == 'NoiseArea: strategy silent (Sim101)', s)
set_rec(d, '2026-10-22 10:10'); st, log, s = run(d, '2026-10-22 10:10'); ok('10:10 still stale: no repeat yet', s == [], s)
set_rec(d, '2026-10-22 10:16'); st, log, s = run(d, '2026-10-22 10:16')
ok('10:16: "still" push at priority 4', len(s) == 1 and s[0][0].startswith('NoiseArea still') and s[0][2] == 4, s)
write_hb(d, {'updated_et': '2026-10-22 10:20:00'}); set_rec(d, '2026-10-22 10:21'); st, log, s = run(d, '2026-10-22 10:21')
ok('10:21 fixed: "OK" push at priority 3, alert closed', len(s) == 1 and s[0][0].startswith('NoiseArea OK') and s[0][2] == 3 and not st['open'], s)
cleanup(d)
d = folder({'updated_et': '2026-10-22 09:45:00'}, recorder=rec_ok('2026-10-22 10:02'))
real = W.push; W.push = lambda *a, **k: ['ntfy failed (no network)']
st, log, s = run(d, '2026-10-22 10:00'); W.push = real
ok('push fails: logged push_failed and queued', 'push_failed' in log and len(st['pending']) == 1, st.get('pending'))
st, log, s = run(d, '2026-10-22 10:02')
ok('next run: queued alert sent late, queue empty', 'pushed_late' in log and st['pending'] == [] and any('[sent late' in m for _, m, _ in s), s)
cleanup(d)
d = folder({'updated_et': '2026-10-22 16:00:00'}, recorder=rec_ok('2026-10-23 09:30'))
json.dump({'open': {'nt_down': {'since': '2026-10-22 15:58', 'last_alert': '2026-10-22 15:58'}}, 'checked_et': '2026-10-23 09:28'},
          open(os.path.join(d, 'NoiseArea_watchdog_status.json'), 'w'))
st, log, s = run(d, '2026-10-23 09:30', nt='no')
ok("yesterday's open alert raised fresh (priority 5, not 'still')", 'expired' in log and any(p == 5 and t.startswith('NoiseArea: NinjaTrader') for t, _, p in s), s)
cleanup(d)

print('--- the watchdog itself breaks ---')
d = folder({'updated_et': '2026-10-22 10:00:00'}, recorder=rec_ok('2026-10-22 10:02'))
real_checks, real_cfg = W.checks, W.load_cfg
def crash(*a, **k): raise RuntimeError('simulated crash')
W.checks = crash; W.load_cfg = lambda: dict(CFG)
sys.argv = ['watchdog.py', '--folder', d, '--now', '2026-10-22 10:02', '--nt-running', 'yes', '--push']; sent.clear(); code = None
try: W.main()
except SystemExit as e: code = e.code
st = json.load(open(os.path.join(d, 'NoiseArea_watchdog_status.json'), encoding='utf-8'))
ok('crash: exit 1, status "broken", BROKEN logged, phone told', code == 1 and 'simulated crash' in st.get('broken', '') and
   'BROKEN' in open(os.path.join(d, 'records', 'watchdog_log.csv'), encoding='utf-8').read() and any('WATCHDOG BROKEN' in t for t, _, _ in sent), sent)
W.checks = real_checks; sent.clear()
try: W.main()
except SystemExit: pass
st = json.load(open(os.path.join(d, 'NoiseArea_watchdog_status.json'), encoding='utf-8'))
ok('next good run: "working again" pushed, broken cleared', not st.get('broken') and any('working again' in t for t, _, _ in sent), sent)
W.load_cfg = lambda: dict(CFG, healthchecks_url='u')
sys.argv = ['watchdog.py', '--folder', d, '--now', '2026-10-22 10:04', '--nt-running', 'yes']; sent.clear(); write_hb(d, {'updated_et': '2026-10-22 09:40:00'})
try: W.main()
except SystemExit: pass
ok('test run without --push: alert logged but nothing pushed', sent == [] and 'Sim101:stale' in open(os.path.join(d, 'records', 'watchdog_log.csv'), encoding='utf-8').read(), sent)
W.load_cfg = real_cfg; cleanup(d)

print('--- PC restart / gap (live path, everything external stubbed) ---')
d = folder({'updated_et': '2026-10-22 10:20:00'}, recorder=rec_ok('2026-10-22 10:22'))
W.processes = lambda: {'ninjatrader.exe'}; W.ping_healthchecks = lambda *a, **k: None
W.boot_time = lambda: dt.datetime(2026, 10, 22, 14, 15)          # UTC = 10:15 ET
json.dump({'checked_et': '2026-10-22 09:58', 'boot_utc': '2026-10-21 11:00', 'last_clock_probe': '2026-10-22 10:22'}, open(os.path.join(d, 'NoiseArea_watchdog_status.json'), 'w'))
sent.clear(); W.run(argparse.Namespace(folder=d, now='2026-10-22 10:22', nt_running=None, push=False, test_push=False), CFG, True)
ok('restart detected in session: priority 5 push naming 10:15 ET', any(t == 'NoiseArea: PC restarted' and p == 5 and '10:15' in m for t, m, p in sent), sent)
json.dump({'checked_et': '2026-10-22 10:00', 'boot_utc': '2026-10-22 14:15', 'last_clock_probe': '2026-10-22 10:40'}, open(os.path.join(d, 'NoiseArea_watchdog_status.json'), 'w'))
write_hb(d, {'updated_et': '2026-10-22 10:40:00'}); set_rec(d, '2026-10-22 10:40')
sent.clear(); W.run(argparse.Namespace(folder=d, now='2026-10-22 10:40', nt_running=None, push=False, test_push=False), CFG, True)
ok('40-min gap in session, same boot: "checks missed" push', any(t == 'NoiseArea: checks missed' for t, _, _ in sent), sent)
json.dump({'checked_et': '2026-10-22 22:57', 'boot_utc': '2026-10-22 14:15', 'last_clock_probe': '2026-10-23 06:57'}, open(os.path.join(d, 'NoiseArea_watchdog_status.json'), 'w'))
write_hb(d, {'updated_et': '2026-10-23 06:55:00'})
sent.clear(); W.run(argparse.Namespace(folder=d, now='2026-10-23 06:57', nt_running=None, push=False, test_push=False), CFG, True)
ok('overnight sleep 22:57-06:57: logged, not pushed', not any(t == 'NoiseArea: checks missed' for t, _, _ in sent) and
   'GAP' in open(os.path.join(d, 'records', 'watchdog_log.csv'), encoding='utf-8').read(), sent)
# 2026-10-26: waking from a manual sleep alerted 'GitHub backup stopped' (10-24 10:49, 10-25 07:28) although it catches up itself
stp = os.path.join(d, 'NoiseArea_watchdog_status.json')
json.dump({'state': 'pushed', 'time': '2026-10-24 23:07:00'}, open(os.path.join(d, 'NoiseArea_autopush_status.json'), 'w'))
json.dump({'checked_et': '2026-10-24 23:18', 'boot_utc': '2026-10-22 14:15', 'last_clock_probe': '2026-10-25 07:24'}, open(stp, 'w'))
write_hb(d, {'updated_et': '2026-10-23 16:05:00'})
sent.clear(); W.run(argparse.Namespace(folder=d, now='2026-10-25 07:24', nt_running=None, push=False, test_push=False), CFG, True)
st = json.load(open(stp, encoding='utf-8'))
ok('woke 07:24 with the backup 8 h old: no backup alert yet, wake time kept', not any('GitHub backup' in t for t, _, _ in sent)
   and st.get('woke_et') == '2026-10-25 07:24', (sent, st.get('woke_et')))
st['checked_et'] = '2026-10-25 07:44'; json.dump(st, open(stp, 'w'))
sent.clear(); W.run(argparse.Namespace(folder=d, now='2026-10-25 07:46', nt_running=None, push=False, test_push=False), CFG, True)
ok('22 min after waking, backup still not run: alert', any(t == 'NoiseArea: GitHub backup stopped' for t, _, _ in sent), sent)
cleanup(d)

print('--- market data late (tail of the TapeRecorder tape) ---')
def tape(root, now, lag_s, n=120):
    u = dt.datetime.strptime(now, '%Y-%m-%d %H:%M').astimezone(dt.timezone.utc).replace(tzinfo=None)
    os.makedirs(os.path.join(root, 'MNQ_12-26'), exist_ok=True); rows = ['kind,feed_utc,recv_utc,price,size,bid,ask']
    for i in range(n):
        r = u - dt.timedelta(seconds=(n - i) * 0.2); f = r - dt.timedelta(seconds=lag_s)
        rows.append(f"T,{f:%Y-%m-%d %H:%M:%S.%f},{r:%Y-%m-%d %H:%M:%S.%f},29000,1,28999.75,29000")
    open(os.path.join(root, 'MNQ_12-26', f"{u:%Y-%m-%d}_tape.csv"), 'w', encoding='utf-8').write('\n'.join(rows) + '\n')
def rec_at(d, now, root):
    r = rec_ok(now); r.update(output_dir=root, instrument='MNQ 12-26'); json.dump(r, open(os.path.join(d, 'TapeRecorder_status.json'), 'w', encoding='utf-8'))
root = tempfile.mkdtemp(prefix='wdtape_'); d = folder({'updated_et': '2026-10-26 09:35:00'})
rec_at(d, '2026-10-26 09:36', root); tape(root, '2026-10-26 09:36', 12.0); st, log, s = run(d, '2026-10-26 09:36')
ok('12 s late: "market data late" at priority 4 naming 12 s', any(t == 'NoiseArea: market data late' and p == 4 and '12 s late' in m for t, m, p in s), s)
ok('perf_log.csv row carries the delay', '12.00' in open(os.path.join(d, 'records', 'perf_log.csv'), encoding='utf-8').read())
rec_at(d, '2026-10-26 09:38', root); tape(root, '2026-10-26 09:38', 3.0); st, log, s = run(d, '2026-10-26 09:38')
ok('3 s while open: stays open, nothing pushed (no flapping)', 'feedlag' in st['open'] and s == [], s)
rec_at(d, '2026-10-26 09:40', root); tape(root, '2026-10-26 09:40', 0.2); st, log, s = run(d, '2026-10-26 09:40')
ok('0.2 s: cleared with an OK push', 'feedlag' not in st['open'] and any(t == 'NoiseArea OK: market data late' for t, _, _ in s), s)
rec_at(d, '2026-10-26 09:42', root); tape(root, '2026-10-26 09:42', 3.0); st, log, s = run(d, '2026-10-26 09:42')
ok('3 s while closed: no alert', 'feedlag' not in st['open'] and s == [], s)
tape(root, '2026-10-26 09:20', 30.0)          # same UTC day, written 25 minutes before "now": not current
ok('tape older than 10 minutes: not judged', W.feed_lag(d, dt.datetime(2026, 10, 26, 9, 45)) is None)
cleanup(d); shutil.rmtree(root, ignore_errors=True)

print('--- CPU use between two runs ---')
cdir = tempfile.mkdtemp(prefix='wdcpu_'); cp = os.path.join(cdir, 'prev.json'); ncpu = os.cpu_count() or 1
real_time, real_sys = W.time.time, W.system_times
W.CPUT.clear(); W.CPUT.update({'10': ['ninjatrader.exe', 100.0], '20': ['python.exe', 50.0]})
W.time.time = lambda: 1000.0; W.system_times = lambda: (1000.0, 1000.0); first = W.cpu_sample(cp)
W.CPUT.update({'10': ['ninjatrader.exe', 100.0 + 0.5 * 120 * ncpu], '20': ['python.exe', 50.0 + 0.1 * 120 * ncpu]})
W.time.time = lambda: 1120.0; W.system_times = lambda: (1060.0, 1060.0); tot, top, ntp = W.cpu_sample(cp)
W.time.time, W.system_times = real_time, real_sys
ok('first run: no figure; then NinjaTrader 50% of the PC listed first, python 10%, PC 50% busy',
   first is None and abs(ntp - 50) < 1e-6 and top[0][1].lower() == 'ninjatrader' and abs(top[1][0] - 10) < 1e-6 and abs(tot - 50) < 1e-6, (first, tot, top, ntp))
shutil.rmtree(cdir, ignore_errors=True)
W.ACCOUNTS = REAL_ACCOUNTS
print('ALL PASS' if not bad else f'{bad} FAILED')
sys.exit(1 if bad else 0)
