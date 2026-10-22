"""Synthetic checks of the 2026-10-20 follow-up changes to daily_review.py / monitor.py / robustness.py.
    python test_followup.py "<review folder>" "<exports folder>" "<old review copy folder>"
Builds throw-away export folders under this script's directory; never writes to the real folders."""
import sys, os, json, shutil, subprocess, datetime as dt
import pandas as pd, numpy as np, ctypes, glob
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass

REV, EXP, OLD = sys.argv[1], sys.argv[2], sys.argv[3]
HERE = os.path.dirname(os.path.abspath(__file__)); WORK = os.path.join(HERE, 'tf_' + dt.datetime.now().strftime('%H%M%S')); os.makedirs(WORK)
HDR = "trade,entry_time_et,exit_time_et,direction,qty,entry_price,exit_price,profit_points,profit_currency,commission,mae_currency,mfe_currency,entry_name,exit_name\n"
results = []

def folder(name, status, trades=None, copy=('bars', 'checks', 'checks_live'), real_trades=False):
    f = os.path.join(WORK, name); os.makedirs(f)
    if real_trades:
        for t in glob.glob(os.path.join(EXP, 'NoiseArea_trades_MNQ_Sim101_*.csv')): shutil.copy(t, f)
    elif trades is None: open(os.path.join(f, 'NoiseArea_trades_MNQ_Sim101_20261015_145001.csv'), 'w').write(HDR)
    for c in copy:
        src = os.path.join(EXP, f"NoiseArea_{c}_MNQ_strategy_Sim101.csv")
        if os.path.exists(src): shutil.copy(src, f)
    json.dump(status, open(os.path.join(f, 'NoiseArea_status_MNQ_Sim101.json'), 'w'))
    for fname, rows in (trades or {}).items():
        open(os.path.join(f, fname), 'w').write(HDR + ''.join(r + '\n' for r in rows))
    return f

def st(**kw):
    s = dict(state='running', updated_et='2026-10-19 16:05:00', last_bar='2026-10-19 16:05:00', account='Sim101', instrument='MNQ 12-26',
             position='Flat', quantity=0, account_position=0, desired=0, trades_logged=0, trade_log='x.csv', max_daily_loss=0, halt_note='',
             connected=True, history_sessions=14, live_since='2026-10-20 07:51:22', session='2026-10-19')
    s.update(kw); return s

def run(script, f, date):
    p = subprocess.run([sys.executable, '-B', script, f, '--account', 'Sim101', '--date', date], capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr

def check(name, f, date, must=(), must_not=(), code=None, compare_old=True):
    rc, out = run(os.path.join(REV, 'daily_review.py'), f, date)
    ok = all(m in out for m in must) and not any(m in out for m in must_not) and (code is None or rc == code)
    old = ''
    if compare_old:
        orc, oout = run(os.path.join(OLD, 'daily_review.py'), f, date)
        old = f" | old script: exit {orc}" + (" - " + "; ".join(l.strip('- ') for l in oout.splitlines() if l.startswith('- ')) if orc else '')
    results.append((name, ok, f"new exit {rc}{old}"))
    if not ok: print(f"--- {name} FAILED, output:\n{out}")

# 1. full CME holiday (Fri 2026-12-25): heartbeat from the Dec 24 early close
f = folder('holiday', st(updated_et='2026-12-24 13:20:00', state='running', instrument='MNQ 03-27'))
check('holiday: no stale-heartbeat alarm on 2026-12-25', f, '2026-12-25', must=('CME closed today',), must_not=('stale',), code=0)
check('holiday: a non-holiday stale heartbeat still needs attention', f, '2026-12-28', must=('heartbeat is stale',), code=2, compare_old=False)
f = folder('holiday_pos', st(updated_et='2026-12-24 13:20:00', position='Long', quantity=1, account_position=1, instrument='MNQ 03-27'), copy=())
check('holiday: an open position is still flagged', f, '2026-12-25', must=('NOT FLAT',), code=2, compare_old=False)

# 2. NinjaTrader closed normally after the close (10-19 16:22, as happened)
f = folder('shutdown', st(state='terminated', updated_et='2026-10-19 16:22:37', connected=False), real_trades=True)
check('normal post-close shutdown is reported, not flagged', f, '2026-10-19', must=('stopped after the close',), must_not=("state is 'terminated'",), code=0)
f = folder('midday', st(state='terminated', updated_et='2026-10-19 13:02:00'), real_trades=True)
check('a mid-session stop still needs attention', f, '2026-10-19', must=('heartbeat stopped at 13:02', "state is 'terminated'"), code=2, compare_old=False)
f = folder('shutdown_long', st(state='terminated', updated_et='2026-10-19 16:22:37', position='Long', quantity=1, account_position=1), real_trades=True)
check('a shutdown while not flat still needs attention', f, '2026-10-19', must=('NOT FLAT', "state is 'terminated'"), code=2, compare_old=False)

# 3. contract roll escalation for MNQ 12-26 (expiry Fri 2026-12-18, NinjaTrader rolls Mon 12-14)
for date, must, code in (('2026-12-08', 'contract roll next week', None), ('2026-12-14', 'contract roll due', None),
                         ('2026-12-17', 'ROLL TONIGHT', 2), ('2026-12-18', 'stopped trading at 09:30 ET today', 2), ('2026-12-21', 'EXPIRED contract', 2)):
    f = folder('roll_' + date, st(updated_et=date + ' 16:05:00'), copy=())
    check(f'roll message on {date}', f, date, must=(must,), code=code, compare_old=(date in ('2026-12-17', '2026-12-18')))

# 4. split fill: a 2-lot filled 1+1 logged as two identical NinjaTrader trades in one run file, plus a stray copy in a second file
rows = ["5,2026-10-19 11:30:00,2026-10-19 12:30:00,long,1,29951,29857.25,-93.75,-187.50,0.00,217.00,64.50,NA_Long,NA_Flat",
        "6,2026-10-19 11:30:00,2026-10-19 12:30:00,long,1,29951,29857.25,-93.75,-187.50,0.00,217.00,64.50,NA_Long,NA_Flat"]
f = folder('split', st(), {'NoiseArea_trades_MNQ_Sim101_20261015_145001.csv': [], 'NoiseArea_trades_MNQ_Sim101_20261018_163602.csv': rows,
                           'NoiseArea_trades_MNQ_Sim101_20261019_170000.csv': rows[:1]})
check('split fill: both fills kept, the cross-file copy dropped', f, '2026-10-19', must=('trades today: 2, P&L $-375',))

# 5. monitor directly: per-position edge, re-adopted entry, clock skew
sys.path.insert(0, REV); import monitor as mon
bars = pd.read_csv(os.path.join(EXP, 'NoiseArea_bars_MNQ_strategy_Sim101.csv'))
tr = pd.read_csv(os.path.join(f, 'NoiseArea_trades_MNQ_Sim101_20261018_163602.csv')); tr['et'] = pd.to_datetime(tr.entry_time_et); tr['xt'] = pd.to_datetime(tr.exit_time_et)
d = dt.date(2026, 10, 19)
one = mon.edge_series(tr.iloc[:1], [d], bars, d, d)[0][1]; two = mon.edge_series(tr, [d], bars, d, d)[0][1]
results.append(('edge: a 1+1 split counts as one micro position', abs(one - two) < 1e-9, f"one row {one:.2f} bp, two rows {two:.2f} bp"))
live = pd.DataFrame({'date': ['2026-10-19', '2026-10-19'], 'time_et': ['11:30:00', '12:30:00'], 'price': [29950.0, 29858.0]})
t1 = tr.iloc[:1].copy(); t1['_run'] = pd.Timestamp('2026-10-19 12:00:00')
s, u = mon.cost_sides(t1, bars, live, pd.Timestamp('2026-10-19 12:00:00'))
results.append(('cost: an entry re-adopted from the replay is not measured', len(s) == 1 and u == 1 and s[0][1] == '12:30', f"sides {s}, unmeasured {u}"))
t2 = tr.iloc[:1].copy(); t2['et'] = pd.Timestamp('2026-10-19 11:29:59'); t2['_run'] = pd.Timestamp('2026-10-18 16:36:02')
s, u = mon.cost_sides(t2, bars, live, pd.Timestamp('2026-10-18 16:36:03'))
results.append(('cost: a fill stamped 1 s early keeps its decision minute', any(x[1] == '11:30' for x in s), f"sides {s}, unmeasured {u}"))

# 6. robustness.py: commission and split handling
rb = os.path.join(WORK, 'rb.csv')
bt = pd.read_csv(sorted(p for p in (os.path.join(EXP, x) for x in os.listdir(EXP)) if 'trades_MNQ_Backtest' in p)[-1])
bt = bt[pd.to_datetime(bt.exit_time_et) < '2026-10-15']
def rob(df):
    df.to_csv(rb, index=False); out = subprocess.run([sys.executable, '-B', os.path.join(REV, 'robustness.py'), rb], capture_output=True, text=True).stdout
    return out.splitlines()[0]
a = rob(bt)                                                           # as logged: $1.30 commission already inside profit_currency
g = bt.assign(profit_currency=bt.profit_currency + bt.commission, commission=0.0); b = rob(g)   # commission off: +$1 added
results.append(('robustness: $1.30-commission log not charged again', 'net $23,671' in a, a[:60]))
results.append(('robustness: commission-off log gets $1/RT (doc basis)', 'net $24,170' in b or 'net $24,171' in b, b[:60]))
dup = pd.concat([g, g.iloc[:1].assign(trade=0)], ignore_index=True)   # one position split into two identical rows
c = rob(dup)
results.append(('robustness: a split row does not add a trade', c.split()[2] == b.split()[2], f"{b[:40]} vs {c[:40]}"))

print(f"\n{sum(ok for _, ok, _ in results)} of {len(results)} checks passed (work folder {WORK})")
for n, ok, info in results: print(f"  {'PASS' if ok else 'FAIL'}  {n}  [{info}]")
