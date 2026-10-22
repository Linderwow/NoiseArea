"""Scenario test for watchdog.py's after-close flat check (2026-10-22): python watchdog_test.py. Temp folders only, never the live files."""
import os, sys, json, shutil, tempfile, datetime as dt
REVIEW = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REVIEW); sys.dont_write_bytecode = True
import watchdog as W

EXH = "recv_utc,exec_utc,account,instrument,exec_id,order_id,order_name,position,price,quantity,chart_bid,chart_ask"
# (name, heartbeat updated_et, account, strategy position, qty, now, recorder fills [(ET time, side, qty)] or None, expected alert text or None)
CASES = [
    ('today 16:02: 16:00 heartbeat before the exit fill', '2026-10-22 16:00:00', -1, 'Short', 1, '2026-10-22 16:02', None, None),
    ('16:06: 16:05 heartbeat shows flat',                 '2026-10-22 16:04:59', 0, 'Flat', 0, '2026-10-22 16:06', None, None),
    ('16:06: 16:05 heartbeat still short (real miss)',    '2026-10-22 16:04:59', -1, 'Short', 1, '2026-10-22 16:06', None, 'NOT FLAT'),
    ('16:08: no beat since 16:00, short, no recorder',    '2026-10-22 16:00:00', -1, 'Short', 1, '2026-10-22 16:08', None, 'NOT CONFIRMED'),
    ('16:08: no beat since 16:00, recorder shows cover',  '2026-10-22 16:00:00', -1, 'Short', 1, '2026-10-22 16:08', [('2026-10-22 16:00:01', 'Long', 1)], None),
    ('16:09: no beat since 16:00, last known flat',       '2026-10-22 16:00:00', 0, 'Flat', 0, '2026-10-22 16:09', None, None),
    ('16:30: strategy restarted flat',                    '2026-10-22 16:29:59', 0, 'Flat', 0, '2026-10-22 16:30', None, None),
    ('16:45: account short, strategy flat after close',   '2026-10-22 16:44:59', -1, 'Flat', 0, '2026-10-22 16:45', None, 'NOT FLAT'),
    ('11-27 close 13:15: 13:17 pre-fill heartbeat',       '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:17', None, None),
    ('11-27 13:24: no post-close beat, no recorder',      '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', None, 'NOT CONFIRMED'),
    ('11-27 13:24: recorder shows the 13:15:01 sell',     '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', [('2026-11-27 13:15:01', 'Short', 1)], None),
    ('11-27 13:24: recorder has no exit fill',            '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', [('2026-11-27 10:30:00', 'Long', 1)], 'NOT FLAT'),
    ('11-27 13:24: other account fill does not count',    '2026-11-27 13:15:00', 1, 'Long', 1, '2026-11-27 13:24', [('2026-11-27 13:15:01', 'Short', 1, 'Other')], 'NOT FLAT'),
]
bad = 0
for name, upd, acct, pos, q, now, fills, want in CASES:
    d = tempfile.mkdtemp(prefix='wdtest_'); rec = tempfile.mkdtemp(prefix='wdrec_'); W.RECORDER = rec
    hb = {'state': 'running', 'updated_et': upd, 'connected': True, 'position': pos, 'quantity': q, 'account_position': acct, 'instrument': 'MNQ 12-26'}
    json.dump(hb, open(os.path.join(d, 'NoiseArea_status_MNQ_Sim101.json'), 'w', encoding='utf-8'))
    if fills is not None:
        rows = {}
        for f in fills:
            t, side, n = f[:3]; acc = f[3] if len(f) > 3 else 'Sim101'
            u = dt.datetime.strptime(t, '%Y-%m-%d %H:%M:%S').astimezone(dt.timezone.utc)
            rows.setdefault(u.date(), []).append(f"{u:%Y-%m-%d %H:%M:%S}.000000,{u:%Y-%m-%d %H:%M:%S}.000000,{acc},MNQ 12-26,x,y,NA_Flat,{side},29000,{n},,")
        os.makedirs(os.path.join(rec, 'accounts'))
        for day, rs in rows.items():
            open(os.path.join(rec, 'accounts', f"{day:%Y-%m-%d}_executions.csv"), 'w', encoding='utf-8').write(EXH + '\n' + '\n'.join(rs) + '\n')
    probs, active = W.checks(d, dt.datetime.strptime(now, '%Y-%m-%d %H:%M'), True)
    msg = probs.get('Sim101:notflat')
    ok = (want is None and msg is None) or (want is not None and msg is not None and want in msg)
    bad += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name:52s} {msg or 'no alert'}"[:190])
    shutil.rmtree(d); shutil.rmtree(rec)
print('ALL PASS' if not bad else f'{bad} FAILED')
