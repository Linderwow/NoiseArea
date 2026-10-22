"""Regression scenarios for archive_nightly.py's 2026-10-22 fixes: python archive_nightly_test.py. Temp folders only; the phone push is
captured, never sent; the real mirror, status, logs and NinjaTrader folders are never touched."""
import os, sys, time, shutil, tempfile, datetime as dt
REVIEW = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REVIEW); sys.path.insert(0, os.path.join(REVIEW, 'research')); sys.dont_write_bytecode = True
import archive_nightly as A, watchdog as W

bad = 0; sent = []
def ok(name, cond, extra=''):
    global bad
    bad += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{extra}]" if not cond and extra else ''))
def fake_push(cfg, title, msg, pri, tags='warning', tries=2):
    if not cfg.get('ntfy_topic'): return []
    sent.append((title, msg, pri)); return ['ntfy']
W.push = fake_push; W.load_cfg = lambda: {'ntfy_topic': 't'}
A.NT_LOGS = tempfile.mkdtemp(prefix='arlogs_')

print('--- which session an evening review is about ---')
T = lambda s: dt.datetime.strptime(s, '%Y-%m-%d %H:%M')
for now, want in [('2026-10-22 18:20', '2026-10-22'), ('2026-10-22 16:04', '2026-10-21'), ('2026-10-23 07:00', '2026-10-22'),
                  ('2026-10-24 10:00', '2026-10-23'), ('2026-10-26 09:00', '2026-10-23'), ('2026-11-27 13:25', '2026-11-27'),
                  ('2026-12-25 18:20', '2026-12-24')]:
    got = A.last_closed_session(T(now)).isoformat(); ok(f"{now} -> {want}", got == want, got)

print('--- phone summary ---')
txt = "===== Sim101 =====\ntrades today: 1, P&L $420 (commission $0.00)\nlive sessions so far: 6, cumulative $533 (7 trades)\nAll good.\n\nWARNINGS:\n- something\n"
sent.clear(); r = A.phone_summary(1, txt, dt.date(2026, 10, 22))
ok('Thu warnings: one push, priority 4, titled with the session', r == '; phone summary: ntfy' and sent[0][0] == 'NoiseArea Thu 10-22: Warnings' and sent[0][2] == 4, sent)
ok('same session again: not re-sent', A.phone_summary(1, txt, dt.date(2026, 10, 22)).endswith('already sent for this session') and len(sent) == 1)
sent.clear(); A.phone_summary(0, txt, dt.date(2026, 10, 23))
ok('next session: sent (catch-up on Saturday works: no weekday filter)', len(sent) == 1 and sent[0][0] == 'NoiseArea Fri 10-23: All good', sent)
sent.clear(); A.phone_summary(1, '', dt.date(2026, 10, 26), 'Traceback (most recent call last):\n  ...\njson.decoder.JSONDecodeError: Expecting value: line 3')
ok('review crashed: NEEDS ATTENTION, priority 5, error line included', sent and sent[0][0].endswith('NEEDS ATTENTION') and sent[0][2] == 5 and 'JSONDecodeError' in sent[0][1], sent)

print('--- mirror ---')
src = tempfile.mkdtemp(prefix='arsrc_'); dstroot = tempfile.mkdtemp(prefix='ardst_'); dst = os.path.join(dstroot, 'm')
A.MIRROR_PAIRS = [(src, dst)]
def mk(name, data, age_s):
    p = os.path.join(src, name); open(p, 'wb').write(data); t = time.time() - age_s; os.utime(p, (t, t)); return p
mk('done.csv', b'a' * 2_000_000, 7200); mk('live_tape.csv', b'b' * 1000, 60)
r = A.step_mirror()
ok('finished file copied and hashed; file being written skipped', os.path.exists(os.path.join(dst, 'done.csv')) and not os.path.exists(os.path.join(dst, 'live_tape.csv')) and 'still being written' in r, r)
mk('done.csv', b'a' * 500, 3600)                                   # truncated (big file shrank)
try: A.step_mirror(); raised = None
except RuntimeError as e: raised = str(e)
vers = [os.path.join(rt, f) for rt, _, fs in os.walk(dst + '._versions') for f in fs]
ok('big file shrank: step fails AND the good copy is kept in _versions', raised and 'done.csv' in raised and any(os.path.getsize(v) == 2_000_000 for v in vers), (raised, vers))
mk('state.txt', b'2026-10-22', 7200); A.step_mirror(); mk('state.txt', b'x', 3600)
try: r = A.step_mirror(); raised = None
except RuntimeError as e: raised = str(e)
ok('small state file shrank: kept and noted, not a failure', raised is None and 'earlier copies kept' in r, (raised, r))
mk('old_restore.csv', b'c' * 100, 7200); A.step_mirror(); p = mk('old_restore.csv', b'c' * 100, 86400 * 60)
try: r = A.step_mirror(); raised = None
except RuntimeError as e: raised = str(e)
ok('older file restored under the same name: previous copy kept', 'old_restore.csv' in (r or '') + (raised or ''), (r, raised))
shutil.rmtree(src); shutil.rmtree(dstroot)

print('--- steps that used to fail silently ---')
import fwd_shadow as F, ncd_export as X
real_tape, real_db = F.archive_tape, X.NT_DB
F.archive_tape = lambda *a, **k: print("tape archive: nothing new | FAILED: MNQ_12-26/2026-10-23_tape.csv: MemoryError")
try: A.step_tape(); raised = False
except RuntimeError: raised = True
ok('tape conversion FAILED -> step fails', raised)
X.NT_DB = os.path.join(tempfile.gettempdir(), 'no_such_ninjatrader_db')
try: A.step_ticks(); raised = None
except RuntimeError as e: raised = str(e)
ok('NinjaTrader tick store missing -> step fails with the reason', raised and 'did the NinjaTrader folder move' in raised, raised)
F.archive_tape, X.NT_DB = real_tape, real_db
shutil.rmtree(A.NT_LOGS, ignore_errors=True)
print('ALL PASS' if not bad else f'{bad} FAILED')
sys.exit(1 if bad else 0)
