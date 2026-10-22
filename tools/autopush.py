r"""
autopush.py - keep the GitHub package current automatically (2026-10-22; Sashkin: "Committing, verifying and pushing upon
success should be happening automatically").

    python tools\autopush.py             one run (the hourly Windows task "NoiseArea autopush" runs it with pythonw: no window)
    python tools\autopush.py --dry-run   sync and verify only - no commit, no push

One run: lock -> sync from the live NinjaTrader folder (files removed there are git-removed here, at most 20 / 5% per run, else
the run is blocked) -> nothing changed? stop -> verify every change -> commit -> push -> confirm
GitHub has the commit. A failed check stops the run BEFORE the commit; the changes stay in the working tree and every later run
re-checks them (cumulatively, against the last pushed commit) until they pass.
Checks on the files that differ from HEAD:
  1. secrets: no keys / tokens / passwords, no Config*.xml, *.sqlite or *.db, nothing over 45 MB;
  2. every changed .py compiles, every changed .json parses;
  3. a changed NinjaScript (.cs) file -> the whole NinjaTrader.Custom project builds with the package's .cs files
     (tools\nt_build_check.ps1 on a temporary copy of the live bin\Custom) AND the canary build fails (the check can fail);
  4. a changed review / monitoring script or accounts.json -> review_all.py from the package runs against the live exports
     without crashing (its own verdict - all good / warnings / attention - is not a failure).
Status: <NinjaTrader 8>\agent_exports\NoiseArea_autopush_status.json (outside the package, shown by the daily review);
log: .autopush\autopush.log here (git-ignored). Never opens a window: every child process runs with CREATE_NO_WINDOW, and git
runs with GIT_TERMINAL_PROMPT=0 and GCM_INTERACTIVE=never, so an expired login fails the push instead of asking.
"""
import os, sys, re, json, time, shutil, tempfile, argparse, subprocess, py_compile, datetime as dt
sys.dont_write_bytecode = True

TOOLS = os.path.dirname(os.path.abspath(__file__)); REPO = os.path.dirname(TOOLS); sys.path.insert(0, TOOLS)
import sync_from_live as S

STATE = os.path.join(REPO, '.autopush'); LOG = os.path.join(STATE, 'autopush.log'); LOCK = os.path.join(STATE, 'lock')
NOWIN = 0x08000000 if os.name == 'nt' else 0
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never')
MAX_BYTES = 45e6
STRICT = re.compile(r"db-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
                    r"BEGIN [A-Z ]*PRIVATE KEY|Bearer [A-Za-z0-9._-]{20,}")
LOOSE = re.compile(r"(api[_-]?key|apikey|secret|passwd|password|token)[\"' ]*[:=][\"' ]*[A-Za-z0-9/+_-]{12,}", re.I)
LOOSE_OK = re.compile(r"DATABENTO_API_KEY", re.I)      # the env-var NAME is fine; its value never appears in a file


def log(msg):
    os.makedirs(STATE, exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f: f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def run(cmd, timeout=300, cwd=REPO, env=None):
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, creationflags=NOWIN,
                       encoding='utf-8', errors='replace')
    return p.returncode, (p.stdout or '') + (p.stderr or '')


def git(*args, timeout=300):
    return run(['git', *args], timeout=timeout, env=GIT_ENV)


def changed_files():
    """[(status, path)] staged vs HEAD after 'git add -A'."""
    git('add', '-A')
    rc, out = git('diff', '--cached', '--name-status', '--no-renames')
    return [(l.split('\t', 1)[0], l.split('\t', 1)[1]) for l in out.splitlines() if '\t' in l]


def check_secrets(paths):
    bad = []
    for p in paths:
        full = os.path.join(REPO, p); name = os.path.basename(p).lower()
        if re.match(r'config.*\.xml$', name) or name.endswith(('.sqlite', '.db')): bad.append(f"{p}: forbidden file type"); continue
        if os.path.getsize(full) > MAX_BYTES: bad.append(f"{p}: larger than 45 MB"); continue
        try: txt = open(full, encoding='utf-8', errors='strict').read()
        except (UnicodeDecodeError, ValueError): continue                          # binary: covered by type and size
        if STRICT.search(txt): bad.append(f"{p}: looks like a key or token")
        elif not p.lower().endswith('.csv'):
            for m in LOOSE.finditer(txt):
                if not LOOSE_OK.search(m.group(0)): bad.append(f"{p}: looks like a password/secret assignment"); break
    return bad


def check_python_json(paths):
    bad = []
    for p in paths:
        full = os.path.join(REPO, p)
        if p.endswith('.py'):
            try: py_compile.compile(full, cfile=os.path.join(tempfile.gettempdir(), 'autopush_check.pyc'), doraise=True)
            except py_compile.PyCompileError as e: bad.append(f"{p}: {str(e).splitlines()[-1][:200]}")
        elif p.endswith('.json'):
            try: json.load(open(full, encoding='utf-8'))
            except Exception as e: bad.append(f"{p}: invalid JSON ({e})")
    return bad


def check_ninjascript(live):
    """Whole-project build with the package's .cs files, then the canary must fail."""
    with tempfile.TemporaryDirectory(prefix='autopush_nt_') as tmp:
        custom = os.path.join(tmp, 'Custom')
        shutil.copytree(os.path.join(live, 'bin', 'Custom'), custom)
        src = os.path.join(REPO, 'nt8', 'bin', 'Custom')
        for root, _, files in os.walk(src):
            for f in files:
                if f.endswith('.cs'):
                    d = os.path.join(custom, os.path.relpath(os.path.join(root, f), src)); os.makedirs(os.path.dirname(d), exist_ok=True)
                    shutil.copy2(os.path.join(root, f), d)
        ps = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', os.path.join(TOOLS, 'nt_build_check.ps1'), '-Custom', custom]
        rc, out = run(ps, timeout=900)
        if 'build OK' not in out: return [f"NinjaScript build failed: {out.strip()[-600:]}"]
        rc, out = run(ps + ['-Canary'], timeout=900)
        if 'build FAILED' not in out or 'CS0101' not in out: return ["NinjaScript check is broken: the canary build did not fail"]
    return []


def check_review(live):
    script = os.path.join(REPO, 'nt8', 'agent_exports', 'review', 'review_all.py')
    rc, out = run([sys.executable.replace('pythonw.exe', 'python.exe'), '-I', script, os.path.join(live, 'agent_exports')], timeout=600)
    if rc not in (0, 1, 2) or '=====' not in out or 'review script crashed' in out:
        return [f"review_all.py from the package failed (exit {rc}): {out.strip()[-600:]}"]
    return []


def status(live, **kw):
    p = os.path.join(live, 'agent_exports', 'NoiseArea_autopush_status.json')
    old = {}
    try: old = json.load(open(p, encoding='utf-8'))
    except Exception: pass
    st = {'state': kw.get('state'), 'time': f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", 'note': kw.get('note', ''),
          'commit': kw.get('commit', old.get('commit', '')), 'last_push': kw.get('last_push', old.get('last_push', '')),
          'files': kw.get('files', 0), 'checks': kw.get('checks', []), 'gone_from_live': kw.get('gone', [])}
    try:
        with open(p + '.tmp', 'w', encoding='utf-8') as f: json.dump(st, f, indent=1)
        os.replace(p + '.tmp', p)
    except Exception as e: log(f"status file not written: {e}")
    log(f"{st['state']}: {st['note']}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--dry-run', action='store_true'); a = ap.parse_args()
    live = S.default_src()
    if not live: log("NinjaTrader 8 folder not found"); sys.exit(1)
    os.makedirs(STATE, exist_ok=True)
    if os.path.exists(LOCK) and time.time() - os.path.getmtime(LOCK) < 2 * 3600: log("another run holds the lock - skipped"); return
    open(LOCK, 'w').write(str(os.getpid()))
    try:
        n, added, updated, same, gone = S.sync(live)
        if gone:
            # mirror removals (git keeps them in history) - but never mass deletions: a moved or empty source folder must not
            # wipe the package, so more than 20 vanished files (or more than 5% of the package) blocks the run instead
            if len(gone) > 20 or len(gone) > 0.05 * max(n, 1):
                status(live, state='blocked', note=f"{len(gone)} files vanished from the live folder - not mirrored; check the source",
                       gone=gone[:50]); sys.exit(2)
            for g in gone: git('rm', '-q', '--', os.path.join('nt8', g).replace('\\', '/'))
        ch = changed_files()
        if not ch:
            status(live, state='no_changes', note=f"synced {n} files, nothing new", gone=gone); return
        paths = [p for s, p in ch if s != 'D' and os.path.exists(os.path.join(REPO, p))]
        checks, problems = ['secrets'], check_secrets(paths)
        problems += check_python_json(paths); checks.append('python/json')
        if any(p.startswith('nt8/bin/Custom/') and p.endswith('.cs') for p in paths):
            checks.append('NinjaScript build + canary')
            if not problems: problems += check_ninjascript(live)
        if any(p.startswith('nt8/agent_exports/review/') and not p.startswith('nt8/agent_exports/review/research/')
               and p.endswith(('.py', '.json')) for p in paths):
            checks.append('review smoke test')
            if not problems: problems += check_review(live)
        if problems:
            status(live, state='blocked', note='; '.join(problems)[:1500], files=len(ch), checks=checks, gone=gone); sys.exit(2)
        if a.dry_run:
            status(live, state='dry_run_ok', note=f"{len(ch)} changed file(s) pass: {', '.join(checks)}", files=len(ch), checks=checks, gone=gone); return
        names = ', '.join(os.path.basename(p) for _, p in ch[:8]) + (f" (+{len(ch) - 8} more)" if len(ch) > 8 else '')
        msg = (f"Auto-sync from live: {len(ch)} file(s) - {names}\n\nVerified before commit: {', '.join(checks)}.\n"
               f"Committed by tools/autopush.py.")
        p = subprocess.run(['git', '-c', 'core.autocrlf=false', 'commit', '-q', '-F', '-'], cwd=REPO, env=GIT_ENV, input=msg, text=True,
                           capture_output=True, creationflags=NOWIN, timeout=120)
        if p.returncode != 0: status(live, state='error', note=f"commit failed: {(p.stdout + p.stderr)[-500:]}", files=len(ch), checks=checks); sys.exit(1)
        head = git('rev-parse', 'HEAD')[1].strip()
        rc, out = git('push', '-q', 'origin', 'main', timeout=900)
        remote = git('ls-remote', 'origin', 'refs/heads/main', timeout=120)[1].split('\t')[0].strip()
        if rc != 0 or remote != head:
            status(live, state='push_failed', note=f"committed {head[:7]} locally; push failed (exit {rc}): {out.strip()[-500:]}",
                   commit=head, files=len(ch), checks=checks, gone=gone); sys.exit(1)
        status(live, state='pushed', note=f"{len(ch)} file(s) pushed as {head[:7]}", commit=head,
               last_push=f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}", files=len(ch), checks=checks, gone=gone)
    except subprocess.TimeoutExpired as e:
        status(live, state='error', note=f"timed out: {e.cmd}"); sys.exit(1)
    except Exception as e:
        status(live, state='error', note=f"{type(e).__name__}: {e}"); sys.exit(1)
    finally:
        try: os.remove(LOCK)
        except OSError: pass


if __name__ == '__main__':
    main()
