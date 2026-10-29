r"""
tick_holes_test.py - regression scenarios for review_all.py's tick-hole check (tape_holes / tick_gaps / recorder_check), 2026-10-29.
Temp folders only.

    python tick_holes_test.py
"""
import os, sys, io, json, shutil, tempfile, contextlib, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import pandas as pd
import review_all as A

bad = 0
def ok(name, cond, extra=''):
    global bad
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f"  {extra}"))
    bad += 0 if cond else 1

INST = 'MNQ_12-26'


def session(day, close='17:00'):
    """Bar END times (ET) of a whole Globex session ending on `day`."""
    t0 = pd.Timestamp(day) - pd.Timedelta(hours=6)
    return list(pd.date_range(t0 + pd.Timedelta(minutes=1), pd.Timestamp(f"{day} {close}"), freq='min'))


def utc_day(t):
    return pd.Timestamp(t).tz_localize('America/New_York').tz_convert('UTC').date()


def write_bars(root, minutes):
    """The recorder's <UTC date>_bars1minute.csv files for these ET bar ends."""
    os.makedirs(os.path.join(root, INST), exist_ok=True)
    by = {}
    for m in minutes: by.setdefault(utc_day(m), []).append(m)
    for d, ms in by.items():
        p = os.path.join(root, INST, f'{d}_bars1minute.csv'); new = not os.path.exists(p)
        with open(p, 'a', encoding='utf-8') as f:
            if new: f.write('time_et,open,high,low,close,volume,recv_utc\n')
            for m in sorted(ms): f.write(f"{m:%Y-%m-%d %H:%M:%S},1,1,1,1,1,x\n")


def write_tape_parquet(root, minutes):
    """Parquet copies of the tape holding one trade inside each of these ET minutes (bar ends)."""
    os.makedirs(os.path.join(root, 'parquet', INST), exist_ok=True)
    rows = pd.DataFrame({'feed': [pd.Timestamp(m) - pd.Timedelta(seconds=30) for m in minutes]})
    rows['utc'] = rows.feed.dt.tz_localize('America/New_York').dt.tz_convert('UTC').dt.tz_localize(None)
    for d, g in rows.groupby(rows.utc.dt.date):
        pd.DataFrame({'kind': ['T'] * len(g), 'feed_utc': g.utc.values}).to_parquet(os.path.join(root, 'parquet', INST, f'{d}_tape.parquet'))


def folder(root, day, last_utc):
    """agent_exports with a running recorder status, and the day's tape CSV ending at last_utc."""
    ex = os.path.join(root, 'exports'); os.makedirs(ex, exist_ok=True)
    json.dump({'state': 'running', 'updated_utc': '2026-11-03 20:00:00', 'instrument': 'MNQ 12-26'}, open(os.path.join(ex, 'TapeRecorder_status.json'), 'w'))
    os.makedirs(os.path.join(root, INST), exist_ok=True)
    with open(os.path.join(root, INST, f'{day}_tape.csv'), 'w') as f:
        f.write('kind,feed_utc,recv_utc,price,size,bid,ask\n')
        f.write(f"T,{last_utc},{last_utc},29000,1,28999.75,29000\n")
    return ex


def check(root, ticks, day, now):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): rc = A.recorder_check(folder(root, day, f"{day} 21:00:00.000000"), day, root, ticks, now)
    return rc, buf.getvalue()


def fresh():
    r = tempfile.mkdtemp(prefix='tht_'); return r, os.path.join(r, 'nt_ticks')


DAY = dt.date(2026, 11, 3)                                          # Tuesday
AFTER = dt.datetime(2026, 11, 3, 18, 20)                            # the nightly archive's review

print("complete session")
r, tk = fresh(); write_bars(r, session('2026-11-02') + session('2026-11-03'))
rc, out = check(r, tk, '2026-11-03', AFTER)
ok("no hole, no warning, rc 0", rc == 0 and 'tick holes' not in out and 'WARNINGS' not in out, out)
ok("tape_holes is []", A.tape_holes(DAY, r, AFTER) == [])
shutil.rmtree(r)

print("market data stopped at 14:40 (2026-10-28 replay)")
r, tk = fresh(); s = session('2026-11-03'); write_bars(r, session('2026-11-02') + [m for m in s if m <= pd.Timestamp('2026-11-03 14:40')])
rc, out = check(r, tk, '2026-11-03', AFTER)
ok("rc 1 and the hole is named with its length", rc == 1 and 'tick data missing on 2026-11-03: 14:40-17:00 ET (140 min)' in out, out)
ok("the warning gives NinjaTrader's download with the dates", 'Historical Data' in out and 'MNQ 12-26, Tick, Last, 11/03/2026 to 11/03/2026' in out, out)
ok("the warning is a phone-summary line ('- ')", any(l.startswith('- tick data missing') for l in out.splitlines()), out)

print("same hole, NinjaTrader's own tick store has the session")
os.makedirs(os.path.join(tk, '12-26')); open(os.path.join(tk, '12-26', '2026-11-03.parquet'), 'w').close()
rc, out = check(r, tk, '2026-11-03', AFTER)
ok("covered: no warning, rc 0", rc == 0 and 'tick data missing' not in out, out)
shutil.rmtree(r)

print("bars missing but the tape has the trades (bars written only since 10-22 19:55)")
r, tk = fresh(); s = session('2026-11-03')
write_bars(r, session('2026-11-02') + [m for m in s if m > pd.Timestamp('2026-11-02 21:00')])
write_tape_parquet(r, [m for m in s if m <= pd.Timestamp('2026-11-02 21:00')])
ok("no hole after the tape lookup", A.tape_holes(DAY, r, AFTER) == [], A.tape_holes(DAY, r, AFTER))
os.remove(os.path.join(r, 'parquet', INST, '2026-11-03_tape.parquet'))      # 18:00-19:00 ET is UTC 11-02; 19:00-21:00 ET is UTC 11-03
h = A.tape_holes(DAY, r, AFTER)
ok("without that tape file the hole is back (11-02 19:00-21:00)", h and A.hole_text(h) == '11-02 19:00-21:00' and h[0][2] == 120, h)
shutil.rmtree(r)

print("short gaps are not holes")
r, tk = fresh(); s = session('2026-11-03'); gap = {pd.Timestamp('2026-11-03 03:11'), pd.Timestamp('2026-11-03 03:12')}
write_bars(r, session('2026-11-02') + [m for m in s if m not in gap])
ok("2 empty minutes: no hole", A.tape_holes(DAY, r, AFTER) == [])
gap.add(pd.Timestamp('2026-11-03 03:13')); shutil.rmtree(r)
r, tk = fresh(); write_bars(r, session('2026-11-02') + [m for m in s if m not in gap])
h = A.tape_holes(DAY, r, AFTER)
ok("3 empty minutes: a hole 03:10-03:13", h and A.hole_text(h) == '03:10-03:13' and h[0][2] == 3, h)
shutil.rmtree(r)

print("reviewed before the close (16:38): minutes still to come are not holes")
r, tk = fresh(); s = session('2026-11-03'); write_bars(r, session('2026-11-02') + [m for m in s if m <= pd.Timestamp('2026-11-03 16:37')])
rc, out = check(r, tk, '2026-11-03', dt.datetime(2026, 11, 3, 16, 38, 20))
ok("16:38 review: no hole", 'tick data missing' not in out, out)
rc, out = check(r, tk, '2026-11-03', AFTER)
ok("18:20 review of the same files: 16:37-17:00 is a hole", 'tick data missing on 2026-11-03: 16:37-17:00 ET (23 min)' in out, out)
shutil.rmtree(r)

print("Monday: NinjaTrader started after the Sunday open")
r, tk = fresh(); s = session('2026-11-02'); write_bars(r, [m for m in s if m > pd.Timestamp('2026-11-01 19:14')] + session('2026-11-03'))
h = A.tape_holes(dt.date(2026, 11, 2), r, AFTER)
ok("hole dated the evening before: 11-01 18:00-19:14", h and A.hole_text(h) == '11-01 18:00-19:14', h)

print("an older hole is listed until filled, without making a clean day a warning")
rc, out = check(r, tk, '2026-11-03', AFTER)
ok("clean reviewed day stays rc 0", rc == 0, out)
ok("older hole listed with the download", 'tick data still missing from earlier sessions: 11-01 18:00-19:14 ET' in out
   and '11/01/2026 to 11/01/2026' in out, out)
ok("weekend days are not checked", '10-31' not in out and '11-01 00:' not in out, out)
os.makedirs(os.path.join(tk, '12-26')); open(os.path.join(tk, '12-26', '2026-11-02.parquet'), 'w').close()
rc, out = check(r, tk, '2026-11-03', AFTER)
ok("filled by NinjaTrader's tick store: no longer listed", 'still missing' not in out, out)
shutil.rmtree(r)

print("before the recorder ever ran / a whole session missed")
r, tk = fresh(); write_bars(r, session('2026-11-03'))
ok("first recorded session complete: no hole",A.hole_text(A.tape_holes(DAY, r, AFTER)) == '', A.tape_holes(DAY, r, AFTER))
ok("a session before the first bars file: None", A.tape_holes(dt.date(2026, 10, 30), r, AFTER) is None)
write_bars(r, session('2026-11-05'))
h = A.tape_holes(dt.date(2026, 11, 4), r, dt.datetime(2026, 11, 5, 18, 20))
ok("a whole session with no bar after the recorder ran: one hole of 1,380 min", h and len(h) == 1 and h[0][2] == 1380, h)
shutil.rmtree(r)

print("early close (Fri 2026-11-27, 13:15 ET)")
r, tk = fresh(); write_bars(r, session('2026-11-26', '13:00') + session('2026-11-27', '13:15'))
h = A.tape_holes(dt.date(2026, 11, 27), r, dt.datetime(2026, 11, 27, 18, 20))
ok("no hole after the 13:15 close", h == [], h)
shutil.rmtree(r)

print("\nALL PASS" if not bad else f"\n{bad} FAILED")
sys.exit(1 if bad else 0)
