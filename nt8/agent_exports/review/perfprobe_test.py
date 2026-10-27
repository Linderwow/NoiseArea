r"""
perfprobe_test.py - regression scenarios for perfprobe.py (temp folders only; the live sampler reads this PC's counters but
writes only to a temp file and never touches NinjaTrader).

    python perfprobe_test.py
"""
import os, sys, csv, json, time, random, shutil, tempfile, threading, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import perfprobe as P

bad = 0
def ok(name, cond, extra=''):
    global bad
    print(('PASS ' if cond else 'FAIL ') + name + ('' if cond else f"  {extra}"))
    bad += 0 if cond else 1

DAY = '2026-10-28'
T0 = dt.datetime(2026, 10, 28, 9, 26)                 # ET, this PC's local time
utc = lambda t: t.astimezone(dt.timezone.utc).replace(tzinfo=None)


def scenario(where, top=20.0, seed=1, base=None):
    """A synthetic open: the exchange sends 50 events/s, 160/s from 09:30 for 4 minutes, then 100/s; something lets through only
    130/s. where='upstream': the network bytes follow what NinjaTrader takes in; where='nt': they follow the exchange.
    base: put the folder at base\\agent_exports (beside a fake trace folder). Returns (folder, tape path)."""
    rnd = random.Random(seed); d = os.path.join(base, 'agent_exports') if base else tempfile.mkdtemp(prefix='ppt_')
    os.makedirs(os.path.join(d, 'records'))
    rate = lambda s: 50 if s < 240 else 160 if s < 480 else 100
    events, q = [], []                                # (feed s, recv s) since T0; q = waiting events (feed times)
    for s in range(900):
        n = rate(s); q += [s + (i + 0.5) / n for i in range(n)]
        take = min(len(q), 130)
        events += [(f, s + (i + 0.5) / take) for i, f in enumerate(q[:take])]; q = q[take:]
    tape = os.path.join(d, 'tape.csv')
    with open(tape, 'w', newline='\n', encoding='utf-8') as f:
        f.write('kind,feed_utc,recv_utc,price,size,bid,ask\n')
        for fe, re_ in events:
            a, b = utc(T0 + dt.timedelta(seconds=fe)), utc(T0 + dt.timedelta(seconds=re_ + 0.05))
            f.write(f"T,{a:%Y-%m-%d %H:%M:%S.%f},{b:%Y-%m-%d %H:%M:%S.%f},29000,1,28999.75,29000\n")
    with open(os.path.join(d, 'records', f'perf_probe_{DAY}.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(P.COLS)
        for s in range(1, 900):
            got = [r - fe for fe, r in events if s - 1 <= r < s]
            lag = sorted(got)[len(got) // 2] + 0.05 if got else None
            intake = len(got); sent = rate(s - 1)
            net = 8 + 0.1 * (intake if where == 'upstream' else sent) * rnd.uniform(0.95, 1.05)
            late = lag is not None and lag >= 5
            w.writerow([(T0 + dt.timedelta(seconds=s)).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3], '' if lag is None else f"{lag:.2f}",
                        '' if lag is None else f"{lag + 0.1:.2f}", intake, f"{net:.1f}", int(net * 4), 0, 0.3,
                        f"{top if late else 15:.0f}", 3, "1.2.3.4=31;5.6.7.8=40" if s % 10 == 0 else ""])
    return d, tape


print('--- report: where the data was held up ---')
d, tape = scenario('upstream'); lines = P.report(d, DAY, tape=tape); txt = '\n'.join(lines)
ok('upstream: one late stretch found, starting near 09:30', len(lines) == 2 and 'late 09:30:' in lines[1], txt)
ok('upstream: verdict "reached this PC late"', 'reached this PC late' in txt, txt); print(txt)
ok('rates read back from the tape (exchange up to 160/s, intake up to 130/s)', 'up to 160 events/s' in txt and 'took in up to 130/s' in txt, txt)
shutil.rmtree(d, ignore_errors=True)
d, tape = scenario('nt'); txt = '\n'.join(P.report(d, DAY, tape=tape))
ok('inside NinjaTrader: verdict "waited inside NinjaTrader"', 'waited inside NinjaTrader' in txt, txt)
shutil.rmtree(d, ignore_errors=True)
d, tape = scenario('nt', top=100.0); txt = '\n'.join(P.report(d, DAY, tape=tape))
ok('a NinjaTrader thread at a full core: that verdict wins', 'used a full core' in txt, txt)
shutil.rmtree(d, ignore_errors=True)
d, tape = scenario('upstream'); os.remove(tape); txt = '\n'.join(P.report(d, DAY, tape=tape))
ok('tape gone: says it cannot be told (no crash)', 'cannot be told' in txt, txt)
shutil.rmtree(d, ignore_errors=True)
d = tempfile.mkdtemp(prefix='ppt_')
ok('no probe file: no lines', P.report(d, DAY) == [])
shutil.rmtree(d, ignore_errors=True)

print('--- sample(): live counters, a tape growing in a temp folder ---')
d = tempfile.mkdtemp(prefix='ppt_'); tape = os.path.join(d, 'tape.csv'); stop = threading.Event()
open(tape, 'w', encoding='utf-8').write('kind,feed_utc,recv_utc,price,size,bid,ask\n')
def writer():                                        # 200 events a second, each 3 s late
    while not stop.is_set():
        r = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None); f_ = r - dt.timedelta(seconds=3)
        with open(tape, 'a', encoding='utf-8') as f:
            f.write(''.join(f"A,{f_:%Y-%m-%d %H:%M:%S.%f},{r:%Y-%m-%d %H:%M:%S.%f},29000,1,28999.75,29000\n" for _ in range(20)))
        time.sleep(0.1)
th = threading.Thread(target=writer, daemon=True); th.start(); time.sleep(0.3)
out = os.path.join(d, 'probe.csv'); n = P.sample(d, seconds=5.5, pid=os.getpid(), tape=tape, out=out); stop.set(); th.join()
rows = list(csv.DictReader(open(out, encoding='utf-8')))
ok('5 rows a second apart, header written once', n == 5 and len(rows) == 5, (n, len(rows)))
lags = [float(r['lag_s']) for r in rows if r['lag_s']]
ok('delay read as 3 s', lags and all(abs(x - 3) < 0.05 for x in lags), lags)
ins = [float(r['intake_ev_s']) for r in rows]
ok('intake about 200 events/s', all(120 <= x <= 260 for x in ins), ins)
ok('network, CPU and thread columns filled', all(r['net_in_kb_s'] and r['nt_cores'] and r['nt_top_thread_pct'] and r['pc_busy_pct'] for r in rows), rows[0])
n2 = P.sample(d, seconds=1.5, pid=0, tape=os.path.join(d, 'missing.csv'), out=out)      # pid 0 = none (None = find NinjaTrader)
rows = list(csv.DictReader(open(out, encoding='utf-8')))
ok('no NinjaTrader, no tape: still writes rows (blank where unknown), appends without a second header', n2 == 1 and len(rows) == 6
   and rows[-1]['lag_s'] == '' and rows[-1]['nt_cores'] == '', rows[-1])
shutil.rmtree(d, ignore_errors=True)

print("--- md_servers(): Rithmic's market-data server at each login, from its own logs ---")
NEW, OLD = 'host-216-57-156-162.customer.veroxity.net', 'tonpz08020.theomne.net'
def rithmic_log(base, logins):
    """A fake NinjaTrader 8\\trace folder beside base\\agent_exports: (ET datetime, host, ip) market-data logins, each with the
    order and P&L lines Rithmic writes next to it (which must be ignored)."""
    tr = os.path.join(base, 'trace'); os.makedirs(tr, exist_ok=True)
    with open(os.path.join(tr, 'Rithmic.Bullenox.20261026.000'), 'w', encoding='utf-8', newline='\r\n') as f:
        f.write("1792936256.813499 [42144] [65] :  R | API+.NET 13.4.0.0 : The R | API+.NET log file has been opened.\n")
        for t, host, ip in logins:
            e = t.timestamp()
            f.write(f"{e:.6f} [42144] [65] :  R | API+.NET 13.4.0.0 : REngine::login() : sMdCnnctPt : login_agent_tp_paperc\n"
                    f"{e:.6f} [42144] [65] :  R | API+.NET 13.4.0.0 : login_agent_tp_paperc : Local Address : Big_Rig(192.168.0.43):59636\n"
                    f"{e:.6f} [42144] [65] :  R | API+.NET 13.4.0.0 : login_agent_tp_paperc : Peer Address : {host}({ip}):40707\n"
                    f"{e + 0.3:.6f} [42144] [85] :  R | API+.NET 13.4.0.0 : login_agent_op_paperc : Peer Address : tonpz02020.theomne.net(38.65.210.67):40253\n")
base = tempfile.mkdtemp(prefix='ppt_')
rithmic_log(base, [(T0 - dt.timedelta(days=9), OLD, '128.177.47.169'), (T0 - dt.timedelta(days=4), OLD, '128.177.47.169'),
                   (T0 - dt.timedelta(hours=14), NEW, '216.57.156.162'), (T0 + dt.timedelta(minutes=30), OLD, '128.177.47.169')])
d, tape = scenario('upstream', base=base); sv = P.md_servers(d)
ok('four market-data logins read, the order-server lines ignored, connection name from the file name',
   [s[1] for s in sv] == [OLD, OLD, NEW, OLD] and sv[2][2] == '216.57.156.162' and sv[2][3] == 'Bullenox', sv)
ok('md_at: the login before the time, None before the first',
   P.md_at(sv, T0)[1] == NEW and P.md_at(sv, T0 + dt.timedelta(hours=1))[1] == OLD and P.md_at(sv, T0 - dt.timedelta(days=30)) is None)
note = P.md_note(sv, T0)
ok('md_note: a server never given before says so', note.startswith(NEW + ' (216.57.156.162) since') and 'first time in 2 logins over 8 days' in note, note)
ok('md_note: a usual server says how often', 'given 2 time(s) in 3 logins' in P.md_note(sv, T0 + dt.timedelta(hours=1)), P.md_note(sv, T0 + dt.timedelta(hours=1)))
txt = '\n'.join(P.report(d, DAY, tape=tape))
ok('report names the data server of the late stretch', f"Rithmic data server {NEW}" in txt, txt)
ok('no trace folder: no servers, no crash', P.md_servers(tempfile.gettempdir() + r'\no_such_dir\x') == [] and P.md_note([], T0) == '')

print('--- review_all.perf_line(): the daily-review lines and warning ---')
import io, contextlib
import review_all as R
def perf(folder, lags):
    with open(os.path.join(folder, 'records', 'perf_log.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['time_et', 'feed_lag_s', 'pc_cpu_pct', 'nt_cpu_pct', 'memory_pct', 'nt_private_gb', 'top_cpu'])
        for hm, x in lags: w.writerow([f"{DAY} {hm}", x, 2, 1, 50, 1.6, 'NinjaTrader 1; claude 0'])
    os.remove(os.path.join(folder, 'records', f'perf_probe_{DAY}.csv')) if os.path.exists(os.path.join(folder, 'records', f'perf_probe_{DAY}.csv')) else None
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): rc = R.perf_line(folder, DAY)
    return rc, buf.getvalue()
rc, txt = perf(d, [('09:28', 0.2), ('09:32', 17.1), ('09:34', 0.3)])
ok('17 s late: warning (exit 1) naming the new server and the reconnect', rc == 1 and '- market data ran up to 17.1 s late (09:32)' in txt
   and f"data server then: {NEW}" in txt and 'first time in 2 logins' in txt and "reconnect 'Bullenox'" in txt, txt)
rc, txt = perf(d, [('09:28', 0.2), ('09:32', 0.4)])
ok('on time: no warning (exit 0), the server line still printed', rc == 0 and 'WARNINGS' not in txt and f"Rithmic market-data server: {NEW}" in txt, txt)
shutil.rmtree(os.path.join(base, 'trace'))
rc, txt = perf(d, [('09:32', 9.0)])
ok('no Rithmic logs: the warning without the server advice', rc == 1 and 'up to 9.0 s late' in txt and 'reconnect' not in txt, txt)
shutil.rmtree(base, ignore_errors=True)
print('ALL PASS' if not bad else f'{bad} FAILED')
sys.exit(1 if bad else 0)
