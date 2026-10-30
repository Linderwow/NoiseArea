r"""Regression test for the 2026-10-30 Bulenox rules in daily_review.py: the activity-fallback recomputation (K dry sessions -> band
multiplier V until a trade, mirroring the staged NoiseAreaMomentum), the inactivity count in rolling trading days, its texts, and
the rule-event reader. Synthetic bars only; nothing is sent or written outside a temp folder.
    python activity_test.py
"""
import os, sys, tempfile, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import daily_review as D

bad = 0
def ok(cond, what):
    global bad
    print(('PASS ' if cond else 'FAIL ') + what); bad += (not cond)


def session(day, o930, close, n=78):
    """78 five-minute RTH bars 09:35 .. 16:00: the first bar opens at o930, every bar closes (and has high/low) at `close`."""
    t0 = pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=35)
    rows = []
    for i in range(n):
        o = o930 if i == 0 else close
        rows.append({'time_et': (t0 + pd.Timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M:%S'), 'open': o,
                     'high': max(o, close), 'low': min(o, close), 'close': close, 'volume': 100})
    return rows


def synthetic():
    """20 warm-up sessions with big moves (+3/-1/+2/-3 %, the last two -2 % then +5 % so the last one surely trades), then 8 quiet
    sessions that open at 100 and close at 100.1, 100.2 .. 100.8: too small for the base band (sigma > 1 %), but above the prior
    close - the activity band with multiplier 0 - from the second quiet session on."""
    days = pd.bdate_range('2026-03-02', periods=28); rows = []
    moves = [0.03, -0.01, 0.02, -0.03] * 5; moves[18] = -0.02; moves[19] = 0.05
    for k in range(20): rows += session(days[k], 100.0, round(100 * (1 + moves[k]), 4))
    for j in range(8): rows += session(days[20 + j], 100.0, round(100 + 0.1 * (j + 1), 4))
    return pd.DataFrame(rows), [d.date() for d in days]


def main():
    bars, days = synthetic(); Q = days[20:]
    base = D.recompute_checks(bars)
    ok(all(v == 0 for (d, _), v in base.items() if d in Q), "base rules: no position on any quiet session")
    ok(base.get((days[19], '10:00')) == 1, "base rules: the last warm-up session (+5 %) goes long at 10:00")

    info = {}; act = D.recompute_checks(bars, 3, 0.0, (), info)
    ok([info[d] for d in Q] == [(0, False), (1, False), (2, False), (3, True), (0, False), (1, False), (2, False), (3, True)],
       f"K=3: dry counts before the quiet sessions {[info[d] for d in Q]} - the band is on for the 4th and 8th")
    ok(act.get((Q[3], '10:00')) == 1 and act.get((Q[3], '15:30')) == 1 and act.get((Q[3], '16:00')) == 0,
       "K=3: the 4th quiet session goes long at 10:00 on the activity band, holds, flat at 16:00")
    ok(act.get((Q[7], '10:00')) == 1, "K=3: the 8th quiet session trades again (the count restarted after the 4th)")
    ok(all(act.get((d, '10:00')) == 0 for d in (Q[0], Q[1], Q[2], Q[4], Q[5], Q[6])), "K=3: the other quiet sessions stay flat")
    ok(all(act[k] == base[k] for k in base if k[0] not in Q), "K=3: every warm-up decision is unchanged")
    info0 = {}; same = D.recompute_checks(bars, 0, 0.0, (), info0)
    ok(same == base and all(not a for _, a in info0.values()), "K=0: identical to the base rules, no band anywhere")
    infoF = {}; forced = D.recompute_checks(bars, 3, 0.0, {Q[1]}, infoF)
    ok(infoF[Q[1]] == (1, True) and forced.get((Q[1], '10:00')) == 1 and [infoF[d] for d in Q[2:5]] == [(0, False), (1, False), (2, False)],
       f"account-side latch on the 2nd quiet session: it trades on the band and the count restarts ({[infoF[d] for d in Q[1:5]]})")
    # a half-band variant must sit between: V = 0.5 trades later than V = 0 (the band is wider) or not at all
    infoH = {}; half = D.recompute_checks(bars, 3, 0.5, (), infoH)
    ok(infoH[Q[3]] == (3, True) and half.get((Q[3], '10:00')) == 0, "K=3 V=0.5: the band is on for the 4th quiet session but a 0.4 % move does not cross half a band")
    # an invalid session (late first bar) counts as a dry one, like the indicator's StartSession
    late = bars[~((pd.to_datetime(bars.time_et).dt.date == Q[1]) & (pd.to_datetime(bars.time_et).dt.strftime('%H:%M') == '09:35'))]
    infoL = {}; D.recompute_checks(late, 3, 0.0, (), infoL)
    ok(Q[1] not in infoL and infoL[Q[2]] == (2, False) and infoL[Q[3]] == (3, True), f"an invalid session counts as dry ({infoL[Q[2]]}, {infoL[Q[3]]})")

    # sessions_since_fill: fills after 17:00 belong to the next session; weekends and full closures are skipped
    f = pd.DataFrame({'t': pd.to_datetime(['2026-10-26 10:00', '2026-10-27 17:30'])})
    n, last = D.sessions_since_fill(f, [], dt.date(2026, 10, 30), True)
    ok((n, last) == (2, dt.date(2026, 10, 28)), f"a 17:30 fill on Tue 10-27 is Wed's session; Fri after the close: {n} sessions since (Thu, Fri)")
    n, _ = D.sessions_since_fill(f, [], dt.date(2026, 10, 30), False)
    ok(n == 1, f"the same before Friday's close: {n} (Thursday only)")
    f2 = pd.DataFrame({'t': pd.to_datetime(['2026-12-24 11:00'])})
    n, _ = D.sessions_since_fill(f2, [], dt.date(2027, 1, 4), True)
    ok(n == 5, f"last fill Thu 12-24, Mon 01-04 after the close: {n} sessions (12-25 and 01-01 closed, 12-28..31, 01-04)")
    n, last = D.sessions_since_fill(pd.DataFrame({'t': pd.to_datetime([])}), [], dt.date(2026, 10, 30), True, dt.date(2026, 10, 27))
    ok((n, last) == (3, dt.date(2026, 10, 27)), f"no fill, the strategy's activity date 10-27 stands in: {n} sessions")
    ok(D.sessions_since_fill(pd.DataFrame({'t': pd.to_datetime([])}), [], dt.date(2026, 10, 30), True) == (None, None), "no fill and no activity date: unknown")

    # inactivity texts
    k = lambda n, K: D.inactivity_text(n, dt.date(2026, 10, 20), K, 0.0)[0]
    ok(k(None, 3) == 'line' and k(0, 3) == 'line' and k(2, 3) == 'line', "0-2 dry sessions: information only")
    ok(k(3, 3) == 'line' and k(3, 0) == 'warn', "3 dry sessions: a line with the fallback registered, a warning without")
    ok(k(4, 3) == 'warn' and k(4, 0) == 'warn' and k(5, 3) == 'issue' and k(7, 0) == 'issue', "4 dry sessions warn, 5 or more are an issue")
    ok('check-in' not in D.inactivity_text(4, dt.date(2026, 10, 20), 3, 0.0)[1] and 'round trip' not in D.inactivity_text(4, dt.date(2026, 10, 20), 0, 0.0)[1],
       "no text suggests a placeholder trade (Flipping)")

    # rule events
    with tempfile.TemporaryDirectory() as tmp:
        with open(os.path.join(tmp, 'NoiseArea_events_MNQ_BX1.txt'), 'w', encoding='utf-8') as fh:
            fh.write("2026-10-30 10:00:01|direction_block|BX1|LONG held back at 10:00 ET: BX2 is SHORT 2 MNQ 12-26\n"
                     "2026-10-30 10:07:12|direction_clear|BX1|sent at 10:07:12 ET (LONG held back ...)\n"
                     "2026-10-29 10:00:01|activity|BX1|account BX1: last fill session 2026-10-23, 3 sessions without a trade\n"
                     "2026-10-30 09:35:00|live|BX1|contracts 1\n")
        ev = D.rule_events(tmp, 'BX1', dt.date(2026, 10, 30))
        ok([e for _, e, _ in ev] == ['direction_block', 'direction_clear'] and 'BX2 is SHORT' in ev[0][2], f"rule events of the day, in order: {[e for _, e, _ in ev]}")
        ok(D.activity_seed(tmp, 'BX1') is None, "no activity file: no seed")
        open(os.path.join(tmp, 'NoiseArea_activity_MNQ_BX1.txt'), 'w').write('2026-10-27\n')
        ok(D.activity_seed(tmp, 'BX1') == dt.date(2026, 10, 27), "the activity file is read as the seed")

    print('ALL PASS' if bad == 0 else f'{bad} FAIL')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
