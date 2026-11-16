import os, sys, shutil, tempfile, subprocess
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))

FAIL = []
DAY = '2026-11-10'


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{info}]" if not ok and info else '')); ok or FAIL.append(name)


def review(start, events=(), wait=None, state='running', hhmm='11:20:00'):
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, 'NoiseArea_events_MNQ_T1.txt'), 'w', encoding='utf-8') as fh:
        fh.write(f"{DAY} 11:06:43|live|T1|contracts 1, daily loss stop 0, history sessions 14, exit on session close on (120 s), start {start}, "
                 f"on error StopCancelClose, chandelier 0 x ATR(14)\n")
        for e in events: fh.write(e + "\n")
    st = '{"state":"%s","updated_et":"%s %s","position":"Long","quantity":1,"account_position":0,"desired":1,"history_sessions":14,"instrument":"MNQ 12-26"%s}' % (
        state, DAY, hhmm, '' if wait is None else ',"start_wait":%s' % ('true' if wait else 'false'))
    with open(os.path.join(tmp, 'NoiseArea_status_MNQ_T1.json'), 'w', encoding='utf-8') as fh: fh.write(st)
    p = subprocess.run([sys.executable, '-B', os.path.join(HERE, 'daily_review.py'), tmp, '--account', 'T1', '--date', DAY],
                       capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    shutil.rmtree(tmp, ignore_errors=True)
    return p.stdout + p.stderr


out = review('ImmediatelySubmitSynchronizeAccount')
check("S1 'Immediately submit' start: WARNING naming the staged fix", "Start behavior is 'ImmediatelySubmitSynchronizeAccount'" in out
      and '20261109_start_wait' in out and 'Traceback' not in out, out[-900:])
out = review('WaitUntilFlatSynchronizeAccount')
check("S2 'Wait until flat' start: no start-behavior warning", 'Start behavior is' not in out and 'Traceback' not in out, out[-900:])
out = review('WaitUntilFlatSynchronizeAccount', wait=True)
check("S3 heartbeat start_wait true: says it waits for the next entry", 'waiting until the rules are flat (late start)' in out, out[-900:])
out = review('WaitUntilFlatSynchronizeAccount', wait=False)
check("S4 heartbeat start_wait false: no waiting note", 'waiting until the rules are flat' not in out, out[-900:])
ev = [f"{DAY} 11:06:43|start_wait|T1|the rules already hold Long x1 from 27785.25: waiting until they are flat",
      f"{DAY} 13:40:00|start_wait_end|T1|the rules are flat at 13:40 ET: the account trades from the next entry",
      f"{DAY} 12:10:00|chandelier|T1|LONG x1 at 12:10 ET: exit: chandelier 3 x ATR(14)"]
out = review('WaitUntilFlatSynchronizeAccount', ev)
check("S5 events: start_wait / start_wait_end / chandelier each under its own label, none as 'activity latch'",
      '11:06 late start, waiting until flat: the rules already hold Long x1' in out and '13:40 wait over: the rules are flat' in out
      and '12:10 chandelier exit: LONG x1' in out and 'activity latch' not in out, out[-1200:])
out = review('AdoptAccountPosition')
check("S6 other start modes: no warning (only 'Immediately' joins mid-trade at market)", 'Start behavior is' not in out, out[-600:])
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
sys.exit(1 if FAIL else 0)
