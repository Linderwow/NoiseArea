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
os.makedirs(os.path.join(src, 'minute', 'MNQ')); mk(os.path.join('minute', 'MNQ', '09-25.parquet'), b'm' * 1_600_000, 7200); A.step_mirror()
os.makedirs(os.path.join(src, '_versions', '2026-10-23', 'minute', 'MNQ'))           # ncd_export.keep_previous's copy, then the re-export
shutil.copy2(os.path.join(src, 'minute', 'MNQ', '09-25.parquet'), os.path.join(src, '_versions', '2026-10-23', 'minute', 'MNQ', '09-25.parquet'))
mk(os.path.join('minute', 'MNQ', '09-25.parquet'), b'm' * 1_560_000, 3600)
try: r = A.step_mirror(); raised = None
except RuntimeError as e: raised = str(e)
ok('big re-export that kept its earlier version at the source: noted, not a failure', raised is None and 're-export' in r, (raised, r))
os.makedirs(os.path.join(src, 'MNQ_12-26')); os.makedirs(os.path.join(src, 'parquet', 'MNQ_12-26'))
mk(os.path.join('MNQ_12-26', '2026-10-20_tape.csv'), b'T,1\n' * 1000, 7200); mk(os.path.join('parquet', 'MNQ_12-26', '2026-10-20_tape.parquet'), b'PQ', 7200)
mk(os.path.join('MNQ_12-26', '2026-10-21_tape.csv'), b'T,2\n' * 1000, 7200)                     # no parquet yet
try: r = A.step_mirror(); raised = None
except RuntimeError as e: r, raised = '', str(e)
ok('raw tick CSV with a parquet copy: only the parquet is mirrored', not os.path.exists(os.path.join(dst, 'MNQ_12-26', '2026-10-20_tape.csv'))
   and os.path.exists(os.path.join(dst, 'parquet', 'MNQ_12-26', '2026-10-20_tape.parquet')) and 'mirrored as their parquet' in r, (r, raised))
ok('raw tick CSV without a parquet copy yet: mirrored as before', os.path.exists(os.path.join(dst, 'MNQ_12-26', '2026-10-21_tape.csv')))
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
vt = tempfile.mkdtemp(prefix='ncdv_'); real_v = X.VERSIONS; X.VERSIONS = os.path.join(vt, '_versions')
op = os.path.join(vt, 'minute', 'MNQ', '09-25.parquet'); os.makedirs(os.path.dirname(op)); open(op, 'wb').write(b'old export')
n1 = X.keep_previous(op); open(op, 'wb').write(b'new'); n2 = X.keep_previous(op)
kp = os.path.join(vt, '_versions', dt.date.today().isoformat(), 'minute', 'MNQ', '09-25.parquet')
ok('NinjaTrader cache re-export: the earlier export is kept once per day before it is overwritten',
   n1 == 1 and n2 == 0 and open(kp, 'rb').read() == b'old export' and X.keep_previous(os.path.join(vt, 'none.parquet')) == 0)
X.VERSIONS = real_v; shutil.rmtree(vt)
# 2026-10-29: a Historical Data download made after the close ends exactly at 17:00 - its last day must count as complete
import pandas as pd
vt = tempfile.mkdtemp(prefix='ncdc_'); end = pd.Timestamp('2026-10-29 17:00'); fl = ['202610291600.Last.ncd', '202610291700.Last.ncd']
for f in fl: open(os.path.join(vt, f), 'wb').write(b'x')
stamp = lambda t: os.utime(os.path.join(vt, fl[-1]), (dt.datetime.fromisoformat(t).timestamp(),) * 2)    # local (ET) wall time
stamp('2026-10-29 17:56'); after = X.day_complete(vt, fl, end, end, end)
stamp('2026-10-29 16:40'); during = X.day_complete(vt, fl, end, end, end)
ok('tick day ending at the close: complete when its last hour was written after the close, not while the session ran',
   after and not during and not X.day_complete(vt, fl, end, end - pd.Timedelta(hours=9), end)
   and X.day_complete(vt, fl, end, end + pd.Timedelta(hours=1), end + pd.Timedelta(hours=1)), (after, during))
shutil.rmtree(vt)

print('--- research history extension (research\\history_extend.py, 2026-10-24) ---')
import numpy as np, pandas as pd, history_extend as H
def bars(start, n, px=29000.0, vol=1000):
    t = pd.date_range(start, periods=n, freq='5min'); o = px + np.arange(n) * 0.25
    return pd.DataFrame({'time': t, 'open': o, 'high': o + 5, 'low': o - 5, 'close': o + 0.25, 'volume': vol})
none = pd.DataFrame({'time': pd.to_datetime([]), 'close': []})
base = bars('2026-11-04 09:35', 12); alt = base.copy()
alt.loc[5, ['close', 'volume']] = [29010.0, 1200]                     # base's 5th bar is partial (fewer contracts)
alt.loc[6, ['close', 'volume']] = [29011.0, 1001]                     # one contract more: a tick on the bar boundary
alt.loc[8, 'open'] = base.open[8] * 1.05                              # a hole whose only candidate opens 5% away
# 2026-10-29: these two lines unpacked into `bad`, the failure counter, which erased every failure before them (found when a
# new tick-export check FAILED and the run still said ALL PASS)
b2, f, r, refused, sk = H.patch(base.drop([3, 8]).assign(src='archive'), alt, 'recorder', none)
ok('hole filled, partial bar replaced, boundary tick left, 5%-off bar refused',
   (f, r, refused, sk) == (1, 1, 1, 0) and len(b2) == 11 and b2.set_index('time').close[alt.time[5]] == 29010.0
   and b2.set_index('time').close[alt.time[6]] == base.close[6], (f, r, refused, sk))
alt2 = alt.assign(close=alt.close + 300, open=alt.open + 300)          # the same day on another contract's prices
ok('a day where the source disagrees with the archive is skipped', H.patch(base.drop([3]).assign(src='archive'), alt2, 'bars_file', none)[1:] == (0, 0, 0, 1))
bc = base.drop([3, 4]).assign(src='archive', contract='12-26')
ac = pd.concat([alt.loc[[3]].assign(contract='03-27'), alt.loc[[4]].assign(contract='12-26')])
b3, f, r, refused, sk = H.patch(bc, ac, 'recorder', none)
ok('a contract-tagged source fills only from the contract the history holds', f == 1 and alt.time[4] in set(b3.time) and alt.time[3] not in set(b3.time), (f, r))
# contract change: later bars shifted by the new contract's close minus the old one's at the last minute both traded
roll = pd.concat([bars('2026-12-11 16:35', 6).assign(contract='12-26'), bars('2026-12-13 18:05', 6, 29300.0).assign(contract='03-27')]).reset_index(drop=True)
mins = lambda t, c: pd.DataFrame({'time': pd.to_datetime(t), 'close': c})
store = {'12-26': (None, mins(['2026-12-11 16:59', '2026-12-11 17:00'], [29001.0, 29002.0])),
         '03-27': (None, mins(['2026-12-11 17:00', '2026-12-13 18:01'], [29300.75, 29310.0]))}
al, notes = H.align_rolls(roll, '12-26', store)
ok('roll: later bars shifted by -298.75 (offset at the last common minute 17:00), earlier bars unchanged',
   np.allclose(al.close[6:].values, roll.close[6:].values - 298.75) and al.close[:6].equals(roll.close[:6]) and '+298.75' in notes[0], notes)
store['03-27'] = (None, mins(['2026-12-13 18:01'], [29310.0]))
try: H.align_rolls(roll, '12-26', store); raised = None
except RuntimeError as e: raised = str(e)
ok('roll whose offset cannot be measured -> fails with the reason', raised and 'offset unknown' in raised, raised)
# the whole merge on temp files: archive day with a hole, newer days only in the bars file, today's session still in progress
tmp = tempfile.mkdtemp(prefix='hist_'); rec = os.path.join(tmp, 'rec', 'MNQ_12-26'); os.makedirs(rec); os.makedirs(os.path.join(tmp, 'min'))
day = lambda d: bars(f'{d} 09:35', 78)
frozen = pd.concat([day('2026-11-02'), day('2026-11-03')]).reset_index(drop=True)
arc = day('2026-11-04'); hole = arc.time[9]; arc.drop([9]).assign(recorded='x').to_csv(os.path.join(tmp, 'arc.csv'), index=False)
live = pd.concat([day('2026-11-04').drop([9]), day('2026-11-05'), bars('2026-11-06 09:35', 30)]).rename(columns={'time': 'time_et'})
live.to_csv(os.path.join(tmp, 'live.csv'), index=False)
day('2026-11-04').loc[[9]].rename(columns={'time': 'time_et'}).assign(recv_utc='x').to_csv(os.path.join(rec, '2026-11-04_bars5min.csv'), index=False)
real_ap, real_bp = H.archive_path, H.bars_file_path
H.archive_path = lambda a: os.path.join(tmp, 'arc.csv'); H.bars_file_path = lambda a: os.path.join(tmp, 'live.csv')
try:
    bx, raw, src, ro, notes = H.extended_bars('T', frozen, os.path.join(tmp, 'rec'), os.path.join(tmp, 'min'))
    days = set(bx.time.dt.date.astype(str))
    ok('merge: archive hole filled from the TapeRecorder, newer day from the bars file, in-progress day left out',
       hole in set(bx.time) and '2026-11-05' in days and '2026-11-06' not in days and any('still in progress' in n for n in notes)
       and src[pd.Timestamp('2026-11-04').date()] == 'archive+recorder 1' and src[pd.Timestamp('2026-11-05').date()] == 'bars_file', (sorted(days), src, notes))
finally:
    H.archive_path, H.bars_file_path = real_ap, real_bp; shutil.rmtree(tmp, ignore_errors=True)
real_run = H.run
H.run = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("CME session(s) missing or incomplete in the history: 2026-11-04 (gap in the recorded bars)"))
try: A.step_history(); raised = None
except RuntimeError as e: raised = str(e)
ok('history step: a fresh gap fails the step with the date', raised and '2026-11-04' in raised, raised)
H.run = lambda *a, **k: ['history extended: 4,092 frozen sessions ... + 7 recorded session(s)', '   recorded sessions ...', '   CME sessions 2026-10-15 .. 2026-10-23: 7, complete in the history 7, none missing']
ok('history step: ok note = summary + session coverage', A.step_history().endswith('none missing') and 'recorded sessions' not in A.step_history())
H.run = real_run
if os.path.exists(H.archive_path('Sim101')):
    # 2026-10-29: the GitHub package (autopush runs this test there) has no strategy bars file, so the newest session not yet
    # archived read as a gap (10-28) and blocked a push; use this PC's live file. A real gap in the data is the nightly step's
    # alarm, not a code regression: it passes here when reported as such (before, "and lines" failed every reported gap).
    if not os.path.exists(H.bars_file_path('Sim101')):
        live_bars = os.path.join(os.path.expanduser('~'), 'Documents', 'NinjaTrader 8', 'agent_exports', 'NoiseArea_bars_MNQ_strategy_Sim101.csv')
        if os.path.exists(live_bars): H.bars_file_path = lambda a: live_bars
    try: lines = H.run(write=False, log=lambda s: None); raised = None
    except RuntimeError as e: lines, raised = [], str(e)
    ok('real data: the frozen 4,092 sessions reproduce exactly and recorded sessions are added (or a data gap is reported)',
       (raised is None and lines and 'reproduced' in lines[0] and 'recorded session' in lines[0])
       or (raised is not None and 'missing or incomplete' in raised), (raised, lines[:1]))
shutil.rmtree(A.NT_LOGS, ignore_errors=True)
print('ALL PASS' if not bad else f'{bad} FAILED')
sys.exit(1 if bad else 0)
