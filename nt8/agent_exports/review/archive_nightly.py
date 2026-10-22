r"""
archive_nightly.py - keep every NoiseArea data stream archived and on two disks (2026-10-22, "no gaps").

    python archive_nightly.py          one run (the Windows task "NoiseArea nightly archive" runs it with pythonw at 18:20 ET)

Runs without the Claude app and without a window. Every step is idempotent, appends or copies only, and never deletes:
  1. bars     each account's NoiseArea_bars_*.csv (rewritten by the strategy on every start) -> research\fwd\bars_archive_MNQ_<acct>.csv
  2. tape     TapeRecorder's finished UTC days (D:\nt_recorder\<folder>\<date>_<stream>.csv) -> verified parquet copies
  3. ticks    NinjaTrader's own tick / minute store (db\tick, db\minute, read-only copies) -> D:\nt_ticks (research\ncd_export.py)
  4. ntlogs   NinjaTrader deletes its log\ and trace\ files after 20 days (Config.xml LogsMaintainedDays) - they are the only
              millisecond record of every order and fill before the TapeRecorder -> D:\nt_logs\log, D:\nt_logs\trace
  5. mirror   D:\databento_archive, D:\nt_ticks (minus its _work scratch), D:\nt_logs, D:\nt_recorder -> C:\NoiseAreaData_mirror\
              (C: and D: are separate SSDs; files removed at the source are kept in the mirror)
  6. context  CBOE VIX / VIX3M daily history -> research\event_calendars\
  2026-10-22 (logging audit + institutional data research, research\LOGGING_AUDIT_20261022.txt / INSTITUTIONAL_DATA_20261022.txt):
  7. ntdb     consistent copy of NinjaTrader's database (sqlite backup API, read-only source, integrity-checked) -> D:\nt_db\, and
              append-only ledgers of every execution, order and order update (a Sim101 reset can no longer erase them) plus a
              daily account-item snapshot (balances etc., NinjaTrader's raw AccountItem codes) -> agent_exports\records\
  8. winlog   Windows shutdown / restart / crash / sleep / update events of the last 3 days -> records\windows_events.csv
  9. manifest one row per day: SHA-256 of the strategy, indicator, recorder and compiled DLL, NinjaTrader version, each account's
              settings at its last start, the package commit, the clock offset -> records\daily_manifest.csv
 10. review   the evening review's full text -> records\reviews\<date>.txt, and on CME trading days its verdict, trades and
              warnings to the phone (ntfy, same config as watchdog.py; once a day);  11. clock: one SNTP measurement (clockprobe.py)
  The mirror (5) now verifies every copy by SHA-256 and keeps a manifest per source; on Sundays it re-hashes the whole mirror.
  Paid Databento downloads on the OneDrive Desktop (on C:) are mirrored to D:, everything on D: to C:.
Status: agent_exports\NoiseArea_archive_status.json (shown by review_all.py); log: D:\nt_logs\archive_nightly.log.
"""
import os, sys, json, shutil, hashlib, sqlite3, subprocess, datetime as dt, traceback, contextlib, io
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); RESEARCH = os.path.join(HERE, 'research'); sys.path.insert(0, RESEARCH)
NT8 = os.path.normpath(os.path.join(HERE, '..', '..')); EXPORTS = os.path.join(NT8, 'agent_exports')
RECORDS = os.path.join(EXPORTS, 'records')
NT_LOGS = r'D:\nt_logs'; MIRROR = r'C:\NoiseAreaData_mirror'; NT_DB_DIR = r'D:\nt_db'; D_MIRROR = r'D:\NoiseAreaData_mirror'
MIRROR_PAIRS = [(r'D:\databento_archive', os.path.join(MIRROR, 'databento_archive')), (r'D:\nt_ticks', os.path.join(MIRROR, 'nt_ticks')),
                (NT_LOGS, os.path.join(MIRROR, 'nt_logs')), (r'D:\nt_recorder', os.path.join(MIRROR, 'nt_recorder')),
                (NT_DB_DIR, os.path.join(MIRROR, 'nt_db')),
                (os.path.join(os.environ.get('USERPROFILE', r'C:\Users\linde'), r'OneDrive\Desktop\Desktop\databento_data'),
                 os.path.join(D_MIRROR, 'databento_data'))]
MIRROR_SKIP = {'_work', '_to_delete', '__pycache__'}
STATUS = os.path.join(EXPORTS, 'NoiseArea_archive_status.json')
NOWIN = 0x08000000 if os.name == 'nt' else 0
TICKS_EPOCH = 621355968000000000          # .NET ticks at 1970-01-01


def sha256(p, bs=1 << 20):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(bs), b''): h.update(b)
    return h.hexdigest()


def net_ticks_to_utc(t):
    try: return (dt.datetime(1970, 1, 1) + dt.timedelta(microseconds=(int(t) - TICKS_EPOCH) // 10)).strftime('%Y-%m-%d %H:%M:%S.%f')
    except Exception: return ''


def append_new(path, header, rows, key):
    """Append rows (dicts) whose key is not in the file yet. Returns the number appended."""
    import csv
    os.makedirs(os.path.dirname(path), exist_ok=True)
    have = set()
    if os.path.exists(path):
        with open(path, newline='', encoding='utf-8') as f:
            for r in csv.DictReader(f): have.add(key(r))
    new = [r for r in rows if key({k: ('' if v is None else str(v)) for k, v in r.items()}) not in have]
    if new:
        exists = os.path.exists(path)
        with open(path, 'a', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction='ignore')
            if not exists: w.writeheader()
            for r in new: w.writerow({k: ('' if v is None else v) for k, v in r.items()})
    return len(new)


def copy_if_changed(src, dst):
    """Copy when the destination is missing, or differs in size or is older. Returns True when a copy was made."""
    try:
        s = os.stat(src)
        if os.path.exists(dst):
            d = os.stat(dst)
            if d.st_size == s.st_size and d.st_mtime >= s.st_mtime - 1: return False
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst + '.part'); os.replace(dst + '.part', dst)
        return True
    except FileNotFoundError:
        return False


def tree_copy(src_root, dst_root, skip=frozenset()):
    n = 0; b = 0
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [x for x in dirs if x not in skip]
        for f in files:
            if f.endswith('.part'): continue
            s = os.path.join(root, f); d = os.path.join(dst_root, os.path.relpath(s, src_root))
            if copy_if_changed(s, d): n += 1; b += os.path.getsize(s)
    return n, b


def step_bars():
    import pandas as pd, fwd_shadow as F
    reg = json.load(open(os.path.join(HERE, 'accounts.json'), encoding='utf-8'))['accounts']; out = []
    for r in reg:
        src = os.path.join(EXPORTS, f"NoiseArea_bars_MNQ_strategy_{r['account']}.csv")
        if not os.path.exists(src): out.append(f"{r['account']}: no bars file"); continue
        b = pd.read_csv(src).drop_duplicates('time_et', keep='last').rename(columns={'time_et': 'time'})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf): F.archive_bars(b, r['account'])
        out.append(buf.getvalue().strip())
    return '; '.join(out)


def step_tape():
    import fwd_shadow as F
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): F.archive_tape()
    return buf.getvalue().strip()


def step_ticks():
    import ncd_export as X
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        X.sync_copies(log=print); X.export_ticks(workers=4, log=print); X.export_minutes(workers=4, log=print)
    lines = [l for l in buf.getvalue().splitlines() if l.strip()]
    if any('FAIL' in l.upper() or 'ERROR' in l.upper() for l in lines): raise RuntimeError(' | '.join(lines[-5:]))
    return ' | '.join(lines[-3:])[:400]


def step_ntlogs():
    n1, b1 = tree_copy(os.path.join(NT8, 'log'), os.path.join(NT_LOGS, 'log'))
    n2, b2 = tree_copy(os.path.join(NT8, 'trace'), os.path.join(NT_LOGS, 'trace'))
    kept = sum(len(fs) for _, _, fs in os.walk(NT_LOGS))
    return f"{n1 + n2} file(s) copied ({(b1 + b2) / 1e6:.1f} MB); {kept} files kept in {NT_LOGS}"


def step_mirror():
    """Copy new/changed files to the other disk, verify each copy by SHA-256, keep a manifest per source; Sundays re-hash all."""
    import csv
    parts = []; bad = []; full = dt.date.today().weekday() == 6
    for src, dst in MIRROR_PAIRS:
        name = os.path.basename(dst)
        if not os.path.isdir(src): parts.append(f"{name}: absent"); continue
        man = dst + '._manifest.csv'; known = {}
        if os.path.exists(man):
            with open(man, newline='', encoding='utf-8') as f:
                for r in csv.DictReader(f): known[r['path']] = r
        n = b = 0; rows = []
        for root, dirs, files in os.walk(src):
            dirs[:] = [x for x in dirs if x not in MIRROR_SKIP]
            for fn in files:
                if fn.endswith('.part'): continue
                s = os.path.join(root, fn); rel = os.path.relpath(s, src); d = os.path.join(dst, rel)
                st = os.stat(s)
                copied = copy_if_changed(s, d)
                k = known.get(rel)
                if copied or k is None or k['size'] != str(st.st_size) or float(k['mtime']) < st.st_mtime - 1:
                    hs = sha256(s); hd = sha256(d)
                    if hs != hd: bad.append(f"{name}/{rel}: copy differs from source")
                    rows.append({'path': rel, 'size': st.st_size, 'mtime': int(st.st_mtime), 'sha256': hs,
                                 'checked': dt.datetime.now().strftime('%Y-%m-%d %H:%M')})
                    if copied: n += 1; b += st.st_size
                elif full and os.path.exists(d) and sha256(d) != k['sha256']:
                    bad.append(f"{name}/{rel}: mirror copy no longer matches its recorded SHA-256")
        if rows:
            known.update({r['path']: r for r in rows})
            with open(man + '.tmp', 'w', newline='', encoding='utf-8') as f:
                w = csv.DictWriter(f, fieldnames=['path', 'size', 'mtime', 'sha256', 'checked']); w.writeheader()
                for r in known.values(): w.writerow(r)
            os.replace(man + '.tmp', man)
        parts.append(f"{name} +{n} ({b / 1e6:.0f} MB, {len(known)} files hashed)")
    if bad: raise RuntimeError('; '.join(bad[:10]))
    return '; '.join(parts) + (' | Sunday full re-hash: all match' if full else '')


def step_ntdb():
    """Consistent copy of NinjaTrader.sqlite + append-only ledgers of executions / orders / order updates + account items."""
    src = os.path.join(NT8, 'db', 'NinjaTrader.sqlite'); os.makedirs(NT_DB_DIR, exist_ok=True)
    day = dt.date.today().strftime('%Y%m%d'); dst = os.path.join(NT_DB_DIR, f'NinjaTrader_{day}.sqlite')
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=30)
    d = sqlite3.connect(dst + '.part'); s.backup(d); d.close()
    chk = sqlite3.connect(dst + '.part'); ok = chk.execute('pragma integrity_check').fetchone()[0]; chk.close()
    if ok != 'ok': raise RuntimeError(f"database copy failed its integrity check: {ok}")
    os.replace(dst + '.part', dst)
    c = s.cursor()
    names = {r[0]: r[1] for r in c.execute("select Id, Name from Accounts")}
    inst = {r[0]: (f"{r[1]} {net_ticks_to_utc(r[2])[5:7]}-{net_ticks_to_utc(r[2])[2:4]}" if r[2] else r[1])
            for r in c.execute("select i.Id, m.Name, i.Expiry from Instruments i join MasterInstruments m on i.MasterInstrument = m.Id")}
    def rows(sql):
        cur = c.execute(sql); cols = [x[0] for x in cur.description]
        for r in cur:
            r = dict(zip(cols, r))
            if 'Account' in r: r['account_name'] = names.get(r['Account'], '')
            if 'Instrument' in r: r['instrument_name'] = inst.get(r['Instrument'], '')
            if 'Time' in r: r['time_utc'] = net_ticks_to_utc(r['Time'])
            yield r
    ex = list(rows("select Id, Account, Instrument, ExecutionId, OrderId, Name, MarketPosition, Price, Quantity, Commission, Fee, Rate, "
                   "Exchange, IsEntry, IsExit, IsEntryStrategy, IsExitStrategy, Position, PositionStrategy, MaxPrice, MinPrice, ServerName, Time from Executions"))
    ex_h = ['time_utc', 'account_name', 'instrument_name', 'ExecutionId', 'OrderId', 'Name', 'MarketPosition', 'Price', 'Quantity', 'Commission',
            'Fee', 'Rate', 'Exchange', 'IsEntry', 'IsExit', 'IsEntryStrategy', 'IsExitStrategy', 'Position', 'PositionStrategy', 'MaxPrice',
            'MinPrice', 'ServerName', 'Id', 'Account', 'Instrument', 'Time']
    n1 = append_new(os.path.join(RECORDS, 'nt_executions.csv'), ex_h, ex, key=lambda r: (r['ExecutionId'], r['account_name'], r['time_utc']))
    od = list(rows("select Id, Account, Instrument, OrderId, Name, OrderAction, OrderType, OrderState, Quantity, Filled, AvgFillPrice, "
                   "LimitPrice, StopPrice, TimeInForce, Oco, Time from Orders"))
    od_h = ['time_utc', 'account_name', 'instrument_name', 'OrderId', 'Name', 'OrderAction', 'OrderType', 'OrderState', 'Quantity', 'Filled',
            'AvgFillPrice', 'LimitPrice', 'StopPrice', 'TimeInForce', 'Oco', 'Id', 'Account', 'Instrument', 'Time']
    n2 = append_new(os.path.join(RECORDS, 'nt_orders.csv'), od_h, od, key=lambda r: (r['OrderId'], r['account_name'], r['time_utc']))
    up = list(rows("select u.Nr, u.OrderId, u.OrderState, u.Quantity, u.Filled, u.AvgFillPrice, u.LimitPrice, u.StopPrice, u.Error, "
                   "u.Comment, u.ServerName, u.Time, o.Account as Account from OrderUpdates u left join Orders o on u.\"Order\" = o.Id"))
    up_h = ['time_utc', 'account_name', 'OrderId', 'Nr', 'OrderState', 'Quantity', 'Filled', 'AvgFillPrice', 'LimitPrice', 'StopPrice',
            'Error', 'Comment', 'ServerName', 'Account', 'Time']
    n3 = append_new(os.path.join(RECORDS, 'nt_order_updates.csv'), up_h, up, key=lambda r: (r['OrderId'], r['Nr'], r['time_utc']))
    snap = dt.datetime.now().strftime('%Y-%m-%d')
    ai = [dict(r, snapshot=snap) for r in rows("select Account, Currency, ItemType, Value, TimeUtc from AccountItems")]
    for r in ai: r['item_time_utc'] = net_ticks_to_utc(r.get('TimeUtc'))
    n4 = append_new(os.path.join(RECORDS, 'account_items.csv'), ['snapshot', 'account_name', 'Currency', 'ItemType', 'Value', 'item_time_utc'],
                    ai, key=lambda r: (r['snapshot'], r['account_name'], r['ItemType'], r['Currency']))
    s.close()
    return (f"database copied to {os.path.basename(dst)} ({os.path.getsize(dst) / 1e6:.1f} MB, integrity ok); ledgers +{n1} executions, "
            f"+{n2} orders, +{n3} order updates, +{n4} account items")


def step_winlog():
    ps = ("$ids=41,42,107,109,1074,1076,6005,6006,6008,6013,19,20,43,44,1,12,13; "
          "Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddDays(-3); Id=$ids} -ErrorAction SilentlyContinue | "
          "ForEach-Object { [pscustomobject]@{time_local=$_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id=$_.Id; provider=$_.ProviderName; "
          "message=(($_.Message -split \"`n\")[0]).Trim()} } | ConvertTo-Csv -NoTypeInformation")
    p = subprocess.run(['powershell', '-NoProfile', '-Command', ps], capture_output=True, text=True, timeout=120, creationflags=NOWIN)
    import csv
    rows = list(csv.DictReader(io.StringIO(p.stdout)))
    n = append_new(os.path.join(RECORDS, 'windows_events.csv'), ['time_local', 'id', 'provider', 'message'], rows,
                   key=lambda r: (r['time_local'], r['id'], r['provider']))
    return f"{len(rows)} power/restart/crash/update events in the last 3 days, {n} new"


def step_manifest():
    import glob, re
    cust = os.path.join(NT8, 'bin', 'Custom')
    f = {'strategy': os.path.join(cust, 'Strategies', 'NoiseAreaStrategy.cs'), 'indicator': os.path.join(cust, 'Indicators', 'NoiseAreaMomentum.cs'),
         'recorder': os.path.join(cust, 'Indicators', 'TapeRecorder.cs'), 'custom_dll': os.path.join(cust, 'NinjaTrader.Custom.dll')}
    row = {'date': dt.date.today().isoformat()}
    for k, p in f.items(): row[k + '_sha256'] = sha256(p)[:16] if os.path.exists(p) else ''
    row['custom_dll_mtime'] = dt.datetime.fromtimestamp(os.path.getmtime(f['custom_dll'])).strftime('%Y-%m-%d %H:%M:%S') if os.path.exists(f['custom_dll']) else ''
    ver = ''
    for lp in sorted(glob.glob(os.path.join(NT8, 'log', 'log.*.txt')), key=os.path.getmtime, reverse=True)[:3]:
        m = re.findall(r'Version ([0-9.]+)', open(lp, encoding='utf-8', errors='replace').read())
        if m: ver = m[-1]; break
    row['ninjatrader_version'] = ver
    sett = []
    for ev in glob.glob(os.path.join(EXPORTS, 'NoiseArea_events_*.txt')):
        last = [l.strip() for l in open(ev, encoding='utf-8', errors='replace') if '|live|' in l]
        if last: sett.append(last[-1].replace(',', ';'))
    row['last_starts'] = ' / '.join(sett)
    p = subprocess.run(['git', '-C', r'D:\dev\trading\NoiseArea', 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True, creationflags=NOWIN)
    row['package_commit'] = p.stdout.strip()
    co = os.path.join(RECORDS, 'clock_offset.csv')
    row['last_clock_offset_ms'] = open(co, encoding='utf-8').read().strip().splitlines()[-1].split(',')[1] if os.path.exists(co) else ''
    n = append_new(os.path.join(RECORDS, 'daily_manifest.csv'), list(row), [row], key=lambda r: (r['date'],))
    return f"{'written' if n else 'already written today'}: strategy {row['strategy_sha256']}, indicator {row['indicator_sha256']}, NT {ver}, package {row['package_commit']}"


def step_review():
    p = subprocess.run([sys.executable.replace('pythonw.exe', 'python.exe'), os.path.join(HERE, 'review_all.py'), EXPORTS],
                       capture_output=True, text=True, timeout=600, creationflags=NOWIN, encoding='utf-8', errors='replace')
    d = os.path.join(RECORDS, 'reviews'); os.makedirs(d, exist_ok=True)
    out = os.path.join(d, dt.date.today().isoformat() + '.txt')
    with open(out, 'w', encoding='utf-8') as f: f.write(f"review_all.py at {dt.datetime.now():%Y-%m-%d %H:%M:%S}, exit {p.returncode}\n\n{p.stdout}{p.stderr}")
    return f"saved {os.path.basename(out)} (exit {p.returncode})" + phone_summary(p.returncode, p.stdout)


def phone_summary(code, text, today=None):
    """2026-10-22: the evening review to the phone (ntfy, D:\\nt_logs\\watchdog_config.json), once per CME trading day: the verdict,
    each account's trades / P&L line and every warning. Also a daily sign of life: no message by ~18:30 = PC or pipeline down."""
    import watchdog as W
    cfg = W.load_cfg(); today = today or dt.date.today()
    if not cfg.get('ntfy_topic') or W.session_end(today) is None: return ''
    flag = os.path.join(NT_LOGS, 'phone_summary_last.txt')
    if os.path.exists(flag) and open(flag, encoding='utf-8').read().strip() == today.isoformat(): return '; phone summary already sent today'
    keep = [l.strip() for l in text.splitlines() if l.strip().startswith(('=====', 'trades today:', 'live sessions so far:', '- '))]
    verdict = {0: 'All good', 1: 'Warnings'}.get(code, 'NEEDS ATTENTION')
    sent = W.push(cfg, f"NoiseArea {today:%a %m-%d}: {verdict}", '\n'.join(keep)[:3500] or verdict, {0: 3, 1: 4}.get(code, 5),
                  {0: 'white_check_mark', 1: 'warning'}.get(code, 'rotating_light'))
    if sent == ['ntfy']:
        with open(flag, 'w', encoding='utf-8') as f: f.write(today.isoformat())
    return f"; phone summary: {', '.join(sent)}"


def step_clock():
    p = subprocess.run([sys.executable.replace('pythonw.exe', 'python.exe'), os.path.join(HERE, 'clockprobe.py')],
                       capture_output=True, text=True, timeout=60, creationflags=NOWIN)
    return p.stdout.strip() or p.stderr.strip()[-200:]


def step_context():
    import fwd_shadow as F
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): F.refresh_context()
    s = buf.getvalue().strip()
    if 'FAILED' in s: raise RuntimeError(s)
    return s


STEPS = (('bars', step_bars), ('tape', step_tape), ('ticks', step_ticks), ('ntlogs', step_ntlogs), ('ntdb', step_ntdb),
         ('winlog', step_winlog), ('clock', step_clock), ('manifest', step_manifest), ('review', step_review), ('mirror', step_mirror),
         ('context', step_context))


def main():
    os.makedirs(NT_LOGS, exist_ok=True)
    logf = open(os.path.join(NT_LOGS, 'archive_nightly.log'), 'a', encoding='utf-8')
    started = dt.datetime.now(); res = {}
    for name, fn in STEPS:
        t0 = dt.datetime.now()
        try:
            res[name] = {'ok': True, 'note': fn()}
        except Exception as e:
            res[name] = {'ok': False, 'note': f"{type(e).__name__}: {e}"[:600]}
            logf.write(traceback.format_exc())
        res[name]['seconds'] = round((dt.datetime.now() - t0).total_seconds(), 1)
        logf.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {name} {'ok' if res[name]['ok'] else 'FAILED'}: {res[name]['note']}\n"); logf.flush()
    st = {'state': 'ok' if all(r['ok'] for r in res.values()) else 'failed', 'started': f"{started:%Y-%m-%d %H:%M:%S}",
          'finished': f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", 'steps': res}
    with open(STATUS + '.tmp', 'w', encoding='utf-8') as f: json.dump(st, f, indent=1)
    os.replace(STATUS + '.tmp', STATUS)
    logf.close()
    if sys.stdout is not None:
        print(json.dumps(st, indent=1))
    sys.exit(0 if st['state'] == 'ok' else 1)


if __name__ == '__main__':
    main()
