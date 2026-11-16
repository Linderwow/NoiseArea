import os, sys, json, tempfile, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import pandas as pd
from types import SimpleNamespace
import daily_review as R

FAIL = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{info}]" if not ok and info else '')); ok or FAIL.append(name)


days = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(12)]
reg = lambda **k: SimpleNamespace(**{**dict(chandelier=3.0, trend_filter=1, exit_gate=4, lunch_pause=1, ema20_confirm=1), **k})
tmp = tempfile.mkdtemp(); p = os.path.join(tmp, 't.json')
json.dump({'X3': dict(longest=5, longest_from='2011-01-03', span='2010-07-21..2026-10-14', share_ge=[1.0, 0.5, 0.3, 0.2, 0.1, 0.05])}, open(p, 'w'))

line, warn = R.dry_spell(pd.Series([10, 20, 5, 30], index=days[:4], dtype=float), reg(), p)
check("new high on the last session: no dry spell, no warning", line.startswith('dry spell: none') and warn is None, line)
s = pd.Series([10, 50, -20, -5, 0, 10], index=days[:6], dtype=float)
line, warn = R.dry_spell(s, reg(), p)
check("4 sessions since the high on day 2: counted, depth $15, share 10%", '4 session(s)' in line and f"last high {days[1]}" in line and '$15 below' in line
      and 'on 10% of sessions' in line and 'full stack' in line and warn is None, line)
s = pd.Series([-10, 5, 1, 1, 1, 1, 0.5], index=days[:7], dtype=float)
line, warn = R.dry_spell(s, reg(), p)
check("never above the start: counted from the start", '7 session(s)' in line and 'no high above the start' in line, line)
check("longer than the backtest's longest: warning", warn is not None and 'longer than any dry spell' in (warn or ''), str(warn))
check("ties at the high count as at the high", R.dry_spell(pd.Series([10, 0, 0], index=days[:3], dtype=float), reg(), p)[0].startswith('dry spell: none'))
check("setup with no table: silent", R.dry_spell(s, reg(exit_gate=3), p) == (None, None))
check("missing table file: silent", R.dry_spell(s, reg(), os.path.join(tmp, 'none.json')) == (None, None))
T = json.load(open(R.DRY_TABLE, encoding='utf-8'))
check("real table has all five setups", all(k in T for k in ('N', 'CH', 'Q8CH', 'K1CH', 'X3')), str(list(T)))
check("real table shares fall from 1 to > 0", all(T[k]['share_ge'][0] == 1.0 and T[k]['share_ge'][-1] > 0 and
                                                   all(a >= b for a, b in zip(T[k]['share_ge'], T[k]['share_ge'][1:])) for k in ('N', 'CH', 'Q8CH', 'K1CH', 'X3')))
check("every setup the review maps has a table", all(v[0] in T for v in R.SETUPS.values()))
print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
sys.exit(1 if FAIL else 0)
