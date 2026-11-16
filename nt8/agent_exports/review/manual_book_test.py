import os, sys, shutil, tempfile, subprocess
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import pandas as pd
import manual_book as MB

FAIL = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{info}]" if not ok and info else '')); ok or FAIL.append(name)


def rec_rows(rows):
    return pd.DataFrame([{'time_utc': t, 'account_name': a, 'instrument_name': 'MNQ 12-26', 'ExecutionId': f'id{i}', 'OrderId': i, 'Name': n,
                          'MarketPosition': 0 if s > 0 else 1, 'Price': p, 'Quantity': q} for i, (t, a, s, p, q, n) in enumerate(rows)])


tmp = tempfile.mkdtemp(); live = os.path.join(tmp, 'live'); os.makedirs(live); os.makedirs(os.path.join(tmp, 'records'))
rows = [('2026-11-09 14:31:00', 'BX-M1', 1, 100.0, 2, 'Entry'), ('2026-11-09 14:35:00', 'BX-M1', -1, 110.0, 1, 'Target1'),
        ('2026-11-09 14:36:00', 'BX-M1', -1, 95.0, 3, 'Entry'), ('2026-11-09 14:40:00', 'BX-M1', 1, 90.0, 2, 'Stop1'),
        ('2026-11-09 14:31:00.5', 'BX9', 1, 100.0, 1, 'Entry'), ('2026-11-09 14:45:00', 'BX9', -1, 99.0, 1, 'Close'),
        ('2026-11-09 14:50:00', 'Sim101', 1, 100.0, 1, 'Entry')]
rec = os.path.join(tmp, 'records', 'nt_executions.csv'); rec_rows(rows).to_csv(rec, index=False)
pd.DataFrame([{'recv_utc': '2026-11-10 14:30:00', 'exec_utc': '2026-11-10 14:30:00', 'account': 'BX-M1', 'instrument': 'MNQ 12-26', 'exec_id': 'L1',
               'order_id': 1, 'order_name': 'Entry', 'position': 'Short', 'price': 200.0, 'quantity': 1, 'chart_bid': 0, 'chart_ask': 0},
              {'recv_utc': '2026-11-10 14:40:00', 'exec_utc': '2026-11-10 14:40:00', 'account': 'BX-M1', 'instrument': 'MNQ 12-26', 'exec_id': 'L2',
               'order_id': 2, 'order_name': 'Stop1', 'position': 'Long', 'price': 205.0, 'quantity': 1, 'chart_bid': 0, 'chart_ask': 0},
              {'recv_utc': '2026-11-10 14:40:00', 'exec_utc': '2026-11-09 14:45:00', 'account': 'BX9', 'instrument': 'MNQ 12-26', 'exec_id': 'id5',
               'order_id': 9, 'order_name': 'Close', 'position': 'Short', 'price': 99.0, 'quantity': 1, 'chart_bid': 0, 'chart_ask': 0}]
             ).to_csv(os.path.join(live, '2026-11-10_executions.csv'), index=False)
open(os.path.join(live, '2026-11-11_executions.csv'), 'w').close()

f = MB.fills(rec, live)
check("M1 Bulenox MNQ fills only (Sim101 dropped), recorder and records merged, duplicate exec id once", len(f) == 8 and set(f.acct) == {'BX-M1', 'BX9'}, len(f))
t = MB.roundtrips(f)
m = t[t.acct == 'BX-M1'].reset_index(drop=True)
exp = [(1, 1, 10.0), (1, 1, -5.0), (-1, 2, 5.0), (-1, 1, -5.0)]
got = [(int(r.dir), int(r.qty), float(r.pts)) for r in m.itertuples()]
check("M2 FIFO: 2 long, 1 out at target, the 3-lot sell closes the rest and opens 2 short, buy 2 closes them, next day short stopped",
      got == exp, got)
check("M3 fee: $2/pt x qty - $0.61 a side", abs(m.usd.iloc[0] - (10 * 2 - 1.22)) < 1e-9 and abs(t.usd.sum() - (20 - 10 + 20 - 10 - 2 - 6 * 1.22)) < 1e-9,
      round(t.usd.sum(), 2))
d = MB.decisions(t)
check("M4 decisions: the copied BX9 entry half a second later joins the BX-M1 entry", int(d.accts.max()) == 2 and len(d) == 3, len(d))
lines = MB.scorecard('2026-11-10', rec, live)
check("M5 scorecard: day line, 20-day line, funded line with its total", lines[0].startswith('manual Bulenox trading 2026-11-10: BX-M1 1 trades $-11.22')
      and 'last 2 trading days' in lines[1] and 'funded BX-M1 since 2026-11-09: 4 trades' in lines[2] and '$+13.90' in lines[2], lines)
p = subprocess.run([sys.executable, '-B', '-c', f"import sys; sys.path.insert(0, r'{HERE}'); import review_all as R; R.manual_check(r'{tmp}', '2026-11-10', r'{live}')"],
                   capture_output=True, text=True)
check("M6 review_all prints the scorecard", 'funded BX-M1' in p.stdout and 'Traceback' not in p.stderr, p.stdout + p.stderr)
p = subprocess.run([sys.executable, '-B', '-c', f"import sys; sys.path.insert(0, r'{HERE}'); import review_all as R; R.manual_check(r'{os.path.join(tmp, 'nope')}', '2026-11-10', r'{os.path.join(tmp, 'nope')}')"],
                   capture_output=True, text=True)
check("M7 no records: one line, no crash", 'no MNQ fills' in p.stdout and 'Traceback' not in p.stderr, p.stdout + p.stderr)
shutil.rmtree(tmp, ignore_errors=True)
print('ALL PASS' if not FAIL else 'FAILED: ' + ', '.join(FAIL))
sys.exit(1 if FAIL else 0)
