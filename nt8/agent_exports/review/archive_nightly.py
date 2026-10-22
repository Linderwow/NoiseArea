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
Status: agent_exports\NoiseArea_archive_status.json (shown by review_all.py); log: D:\nt_logs\archive_nightly.log.
"""
import os, sys, json, shutil, datetime as dt, traceback, contextlib, io
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); RESEARCH = os.path.join(HERE, 'research'); sys.path.insert(0, RESEARCH)
NT8 = os.path.normpath(os.path.join(HERE, '..', '..')); EXPORTS = os.path.join(NT8, 'agent_exports')
NT_LOGS = r'D:\nt_logs'; MIRROR = r'C:\NoiseAreaData_mirror'
MIRROR_SRC = {r'D:\databento_archive': 'databento_archive', r'D:\nt_ticks': 'nt_ticks', NT_LOGS: 'nt_logs', r'D:\nt_recorder': 'nt_recorder'}
MIRROR_SKIP = {'_work', '_to_delete', '__pycache__'}
STATUS = os.path.join(EXPORTS, 'NoiseArea_archive_status.json')


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
    parts = []
    for src, name in MIRROR_SRC.items():
        if not os.path.isdir(src): parts.append(f"{name}: absent"); continue
        n, b = tree_copy(src, os.path.join(MIRROR, name), skip=MIRROR_SKIP)
        parts.append(f"{name} +{n} ({b / 1e6:.0f} MB)")
    return '; '.join(parts)


def step_context():
    import fwd_shadow as F
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): F.refresh_context()
    s = buf.getvalue().strip()
    if 'FAILED' in s: raise RuntimeError(s)
    return s


STEPS = (('bars', step_bars), ('tape', step_tape), ('ticks', step_ticks), ('ntlogs', step_ntlogs), ('mirror', step_mirror),
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
