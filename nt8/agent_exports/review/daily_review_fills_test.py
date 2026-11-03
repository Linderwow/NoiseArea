"""Tests for daily_review.py's account-fill P&L and halt-aware fill check (2026-10-22 code audit): python daily_review_fills_test.py.
Reads the live ledger read-only; synthetic cases use temp folders."""
import os, sys, tempfile, shutil, datetime as dt
REVIEW = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, REVIEW); sys.dont_write_bytecode = True
import pandas as pd, daily_review as R
ROOT0 = R.RECORDER_ROOT

bad = 0
def ok(name, cond, extra=''):
    global bad
    bad += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"   [{extra}]" if not cond and extra else ''))

EXPORTS = os.path.normpath(os.path.join(REVIEW, '..'))
f = R.account_fills(EXPORTS, 'Sim101'); d = R.realized_by_session(f)
day = d.get(dt.date(2026, 10, 22))
print(f"Sim101 fills on 10-22: {len(f[f.t.dt.date == dt.date(2026, 10, 22)])}, realized ${day:,.2f}" if day is not None else "no 10-22 fills")
log = 419.50 - 2 * R.BULENOX_COMM_SIDE
ok("real ledger, 10-22: the account's fills include the 09:08 hot-key round trip the trade log misses", day is not None and abs(day - log) > 1, (day, log))
f2 = f[(f.t >= pd.Timestamp('2026-10-22 10:00')) & (f.t.dt.date == dt.date(2026, 10, 22))]
ok("real ledger, 10-22 from 10:00 (strategy trade only): equals the trade log's $419.50 less commission", abs(R.realized_by_session(f2).sum() - log) < 0.01,
   R.realized_by_session(f2).sum())

T = pd.Timestamp
syn = pd.DataFrame([(T('2026-11-02 10:00'), 'MNQ 12-26', 'a', -1, 6, 25000.0),     # strategy short 6
                    (T('2026-11-02 11:10'), 'MNQ 12-26', 'b', 1, 6, 25091.0),      # Bulenox liquidation at -$1,092 (not in the log)
                    (T('2026-11-03 10:00'), 'MNQ 12-26', 'c', 1, 6, 25000.0),
                    (T('2026-11-03 16:00'), 'MNQ 12-26', 'd', -1, 6, 25050.0),
                    (T('2026-11-03 18:30'), 'MNQ 12-26', 'e', 1, 1, 25060.0),      # evening: counts for 11-04
                    (T('2026-11-04 10:00'), 'MNQ 12-26', 'f', -1, 1, 25070.0)], columns=['t', 'inst', 'id', 'side', 'qty', 'price'])
r = R.realized_by_session(syn); c6 = 2 * 6 * R.BULENOX_COMM_SIDE
ok('liquidation day: -$1,092 less commission', abs(r[dt.date(2026, 11, 2)] - (-91 * 2 * 6 - c6)) < 0.01, r.to_dict())
ok('long day: +$600 less commission', abs(r[dt.date(2026, 11, 3)] - (50 * 2 * 6 - c6)) < 0.01, r.to_dict())
ok('fill after 17:00 belongs to the next session', abs(r[dt.date(2026, 11, 4)] - (10 * 2 - 2 * R.BULENOX_COMM_SIDE)) < 0.01, r.to_dict())
roll = pd.DataFrame([(T('2026-12-10 10:00'), 'MNQ 12-26', 'a', 1, 1, 100.0), (T('2026-12-10 10:30'), 'MNQ 03-27', 'b', -1, 1, 300.0),
                     (T('2026-12-10 11:00'), 'MNQ 12-26', 'c', -1, 1, 110.0), (T('2026-12-10 11:30'), 'MNQ 03-27', 'd', 1, 1, 290.0)],
                    columns=['t', 'inst', 'id', 'side', 'qty', 'price'])
ok('two contracts keep separate books (roll)', abs(R.realized_by_session(roll).sum() - (20 + 20 - 4 * R.BULENOX_COMM_SIDE)) < 0.01)

tmp = tempfile.mkdtemp(prefix='drtest_')
open(os.path.join(tmp, 'NoiseArea_events_MNQ_X.txt'), 'w').write(
    "2026-11-02 09:09:00|live|X|contracts 6\n2026-11-02 11:10:05|halt|X|daily loss stop hit\n2026-11-02 13:00:00|live|X|contracts 6\n"
    "2026-11-02 13:00:01|halt_restored|X|restored\n2026-11-03 09:09:00|live|X|contracts 6\n")
ev = R.stop_events(tmp, 'X', dt.date(2026, 11, 2))
ok('10:30 before the halt: trading', not R.not_trading(ev, T('2026-11-02 10:30')))
ok('11:30 after the halt: not trading (no false "missing fill")', R.not_trading(ev, T('2026-11-02 11:30')))
ok('13:30 after a restart that restored the halt: not trading', R.not_trading(ev, T('2026-11-02 13:30')))
ok("next day's events do not leak in", R.not_trading(R.stop_events(tmp, 'X', dt.date(2026, 11, 3)), T('2026-11-03 10:30')) is False)
shutil.rmtree(tmp)
rec = tempfile.mkdtemp(prefix='drrec_'); os.makedirs(os.path.join(rec, 'MNQ_12-26')); R.RECORDER_ROOT = rec
H = 'time_et,open,high,low,close,volume,recv_utc\n'
open(os.path.join(rec, 'MNQ_12-26', '2026-11-02_bars5min.csv'), 'w').write(H + ''.join(
    f"2026-11-02 {h:02d}:{m:02d}:00,1,1,1,1,1,x\n" for h in range(9, 16) for m in range(0, 60, 5) if (h, m) >= (9, 35)) + "2026-11-01 23:55:00,1,1,1,1,1,x\n")
open(os.path.join(rec, 'MNQ_12-26', '2026-11-03_bars5min.csv'), 'w').write(H + "2026-11-02 16:00:00,1,1,1,1,1,x\n2026-11-02 16:05:00,1,1,1,1,1,x\n")
lb = R.live_bars(dt.date(2026, 11, 2), 'MNQ 12-26'); s = R.session_bar_times(lb)
ok('live bars: the 16:00 bar is read from the next UTC day file', '16:00' in s)
ok('live bars: session = 09:35..16:00 (78 bars), evening and previous-day bars excluded', len(s) == 78 and '16:05' not in s and '09:30' not in s, len(s))
ok('live bars: none for a day without recorder files', R.live_bars(dt.date(2026, 11, 9), 'MNQ 12-26') is None)
shutil.rmtree(rec)

print('--- Bulenox inactivity rule (2026-10-30: a trade at least every 5 trading days, rolling sessions; was weekly) ---')
fl = lambda *ts: pd.DataFrame({'t': pd.to_datetime(list(ts))})
none = pd.DataFrame({'t': pd.to_datetime([])})
ok('no fill and no activity file: unknown, information only', R.sessions_since_fill(none, [], dt.date(2026, 11, 4), True) == (None, None)
   and R.inactivity_text(None, None, 3, 0.0)[0] == 'line')
ok('a fill on Fri 10-30, Thu 11-05 after the close: 4 sessions -> warning', R.sessions_since_fill(fl('2026-10-30 10:05'), [], dt.date(2026, 11, 5), True)[0] == 4
   and R.inactivity_text(4, dt.date(2026, 10, 30), 3, 0.0)[0] == 'warn')
ok('Fri 11-06 after the close: 5 sessions -> needs attention', R.sessions_since_fill(fl('2026-10-30 10:05'), [], dt.date(2026, 11, 6), True)[0] == 5
   and R.inactivity_text(5, dt.date(2026, 10, 30), 3, 0.0)[0] == 'issue')
ok('a fill on Tue 11-03 resets the count (Fri after the close: 3)', R.sessions_since_fill(fl('2026-10-30 10:05', '2026-11-03 10:05'), [], dt.date(2026, 11, 6), True)[0] == 3)
ok('no text asks for a manual round trip', all('round trip' not in R.inactivity_text(n, dt.date(2026, 10, 30), k, 0.0)[1] for n in (3, 4, 5) for k in (0, 3)))
# 2026-11-02: tape_touch - the quote at a fill instant, by bisection on the recorder's tape (checked against a pandas read of the
# rows around the four 11-02 fills: 10:30 fill 28887.75 on the ask, 15:30 fill 28261.25 two ticks better than the bid 28260.75)
R.RECORDER_ROOT = ROOT0          # the synthetic cases above re-point the recorder folders
if R.tape_touch('2026-11-02 10:30:00.232585', 'MNQ 12-26') is not None:
    q1 = R.tape_touch('2026-11-02 10:30:00.232585', 'MNQ 12-26'); q2 = R.tape_touch('2026-11-02 15:30:00.218892', 'MNQ 12-26')
    ok('tape_touch 11-02 10:30 fill instant: bid 28887.25 / ask 28887.75', q1['bid'] == 28887.25 and q1['ask'] == 28887.75, q1)
    ok('tape_touch 11-02 15:30 fill instant: bid 28260.75 / ask 28261.00', q2['bid'] == 28260.75 and q2['ask'] == 28261.0, q2)
    ok('tape_touch before the first row of the tape: None', R.tape_touch('2026-11-01 19:00:00', 'MNQ 12-26') is None)
else: print('SKIP  tape_touch cases (the 2026-11-02 tape is not on this PC)')
ok('tape_touch without a tape: None', R.tape_touch('2019-01-02 10:30:00', 'MNQ 12-26') is None)
print('ALL PASS' if not bad else f'{bad} FAILED')
sys.exit(1 if bad else 0)
