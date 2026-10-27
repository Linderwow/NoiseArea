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


def scenario(where, top=20.0, seed=1):
    """A synthetic open: the exchange sends 50 events/s, 160/s from 09:30 for 4 minutes, then 100/s; something lets through only
    130/s. where='upstream': the network bytes follow what NinjaTrader takes in; where='nt': they follow the exchange.
    Returns (folder, tape path)."""
    rnd = random.Random(seed); d = tempfile.mkdtemp(prefix='ppt_'); os.makedirs(os.path.join(d, 'records'))
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
print('ALL PASS' if not bad else f'{bad} FAILED')
sys.exit(1 if bad else 0)
