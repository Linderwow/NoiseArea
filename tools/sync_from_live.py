import os, sys, json, shutil, argparse
sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(REPO, 'nt8')
NT_GENERATED = {'AssemblyInfo.cs', 'NinjaTrader.Vendor.cs', 'ResourceEnumConverter.cs', 'Resource.Designer.cs'}
SKIP_DIRS = {'__pycache__', '_to_delete', '_work', 'regime_work', 'recovery'}
SKIP_EXT = ('.pyc', '.npz', '.zst', '.dbn', '.tmp')
MAX_BYTES = 45e6
PRIVATE = ('agent_exports/records/windows_events.csv', 'agent_exports/review/research/BULENOX_AGREEMENT_')
SCRUB_FILES = (os.path.join(os.environ.get('LOCALAPPDATA', ''), 'NoiseArea', 'publish_scrub.json'), r'D:\nt_logs\publish_scrub.json')


def scrub_map():
    for p in SCRUB_FILES:
        try:
            m = json.load(open(p, encoding='utf-8')).get('replace', {})
            return {str(k): str(v) for k, v in m.items() if len(str(k)) >= 4}
        except Exception: continue
    return {}


def private(rel):
    r = rel.replace('\\', '/')
    return any(r.startswith(p) for p in PRIVATE)


def _drop_ws(out):
    while out and out[-1] in ' \t':
        out.pop()


def strip_cs(src):
    out, i, n = [], 0, len(src)
    while i < n:
        two = src[i:i + 2]
        if two == '//':
            j = src.find('\n', i)
            j = n if j < 0 else j
            ls = src.rfind('\n', 0, i) + 1
            _drop_ws(out)
            if src[ls:i].strip() == '':
                if out and out[-1] == '\r':
                    out.pop()
                i = j + 1 if j < n else n
                continue
            i = j - 1 if j > 0 and src[j - 1] == '\r' else j
            continue
        if two == '/*':
            j = src.find('*/', i + 2)
            j = n if j < 0 else j + 2
            ls = src.rfind('\n', 0, i) + 1
            le = src.find('\n', j)
            le = n if le < 0 else le
            if src[ls:i].strip() == '' and src[j:le].strip() == '':
                _drop_ws(out)
                if out and out[-1] == '\r':
                    out.pop()
                i = le + 1
            else:
                out.append(' ')
                i = j
            continue
        if src[i] == '"' or src.startswith(('@"', '$"', '$@"', '@$"'), i):
            k = i
            verbatim = False
            while src[k] != '"':
                verbatim = verbatim or src[k] == '@'
                k += 1
            out.append(src[i:k + 1])
            k += 1
            while k < n:
                if verbatim and src.startswith('""', k):
                    out.append('""')
                    k += 2
                    continue
                if not verbatim and src[k] == '\\':
                    out.append(src[k:k + 2])
                    k += 2
                    continue
                out.append(src[k])
                k += 1
                if src[k - 1] == '"':
                    break
            i = k
            continue
        if src[i] == '#' and src[src.rfind('\n', 0, i) + 1:i].strip() == '':
            j = src.find('\n', i)
            j = n if j < 0 else j
            out.append(src[i:j])
            i = j
            continue
        if src[i] == "'":
            j = i + 1
            while j < n and src[j] != "'":
                j += 2 if src[j] == '\\' else 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        out.append(src[i])
        i += 1
    return ''.join(out)


def scrubbed(path, subs):
    raw = open(path, 'rb').read()
    if path.lower().endswith('.cs'):
        bom = raw.startswith(b'\xef\xbb\xbf')
        raw = (b'\xef\xbb\xbf' if bom else b'') + strip_cs(raw.decode('utf-8-sig')).encode('utf-8')
    for k, v in subs.items():
        for enc in ('utf-8', 'utf-16-le'):
            raw = raw.replace(k.encode(enc), v.encode(enc))
    return raw


def documents_folder():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as k:
            return os.path.expandvars(winreg.QueryValueEx(k, 'Personal')[0])
    except Exception: return None


def default_src():
    up = os.environ.get('USERPROFILE', '')
    for base in (documents_folder(), os.path.join(up, 'Documents'), os.path.join(up, 'OneDrive', 'Documents')):
        p = os.path.join(base, 'NinjaTrader 8') if base else ''
        if p and os.path.isdir(p) and os.path.isdir(os.path.join(p, 'bin', 'Custom')): return p
    return None


def wanted(src):
    out = []
    if os.path.exists(os.path.join(src, 'CLAUDE.md')): out.append('CLAUDE.md')
    custom = os.path.join(src, 'bin', 'Custom')
    for d in sorted(os.listdir(custom)):
        full = os.path.join(custom, d)
        if not os.path.isdir(full) or d in SKIP_DIRS: continue
        for f in sorted(os.listdir(full)):
            if f.endswith(('.cs', '.xaml')) and not f.startswith('@') and f not in NT_GENERATED and os.path.isfile(os.path.join(full, f)):
                out.append(os.path.join('bin', 'Custom', d, f))
    for top in ('templates',):
        for root, dirs, files in os.walk(os.path.join(src, top)):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            out += [os.path.relpath(os.path.join(root, f), src) for f in files]
    ws = os.path.join(src, 'workspaces')
    out += [os.path.join('workspaces', f) for f in sorted(os.listdir(ws)) if f.endswith('.xml') and ' - Copy' not in f]
    ae = os.path.join(src, 'agent_exports')
    for f in sorted(os.listdir(ae)):
        if f.startswith(('NoiseArea_trades_', 'NoiseArea_checks_live_', 'NoiseArea_events_', 'NoiseArea_days_')) and \
                os.path.isfile(os.path.join(ae, f)) and os.path.getsize(os.path.join(ae, f)) <= MAX_BYTES:
            out.append(os.path.join('agent_exports', f))
    for root, dirs, files in os.walk(os.path.join(ae, 'records')):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        out += [os.path.relpath(os.path.join(root, f), src) for f in files if os.path.getsize(os.path.join(root, f)) <= MAX_BYTES]
    for root, dirs, files in os.walk(os.path.join(src, 'agent_exports', 'review')):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for f in files:
            p = os.path.join(root, f); rel = os.path.relpath(p, src)
            if f.endswith(SKIP_EXT) or os.path.getsize(p) > MAX_BYTES: continue
            if rel.replace('\\', '/').endswith(('research/fwd/live_bars_research_format.csv',
                                                'research/scaling_inputs/mnq_bars_extended.csv')): continue
            out.append(rel)
    return [r for r in out if not private(r)]


def put(s, d, subs):
    data = scrubbed(s, subs)
    if os.path.exists(d) and open(d, 'rb').read() == data: return False
    os.makedirs(os.path.dirname(d), exist_ok=True)
    with open(d, 'wb') as f: f.write(data)
    shutil.copystat(s, d)
    return True


def sync(src):
    files = wanted(src); subs = scrub_map(); added = changed = same = 0
    for rel in files:
        d = os.path.join(DEST, rel); existed = os.path.exists(d)
        if not put(os.path.join(src, rel), d, subs): same += 1
        elif existed: changed += 1
        else: added += 1
    tool = r'D:\dev\trading\MNQPropBook\tools\nt_build_check.ps1'
    if os.path.exists(tool) and put(tool, os.path.join(REPO, 'tools', 'nt_build_check.ps1'), subs): changed += 1
    keep = {os.path.normcase(os.path.join(DEST, r)) for r in files}
    gone = sorted(rel for rel in (os.path.relpath(os.path.join(r, f), DEST) for r, _, fs in os.walk(DEST) for f in fs)
                  if os.path.normcase(os.path.join(DEST, rel)) not in keep and not os.path.exists(os.path.join(src, rel)))
    return len(files), added, changed, same, gone


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--src', default=default_src()); a = ap.parse_args()
    if not a.src or not os.path.isdir(a.src): sys.exit(f"NinjaTrader 8 folder not found: {a.src}")
    if not scrub_map(): sys.exit("publish_scrub.json not found (" + " or ".join(SCRUB_FILES) + "): account numbers would be copied")
    n, added, changed, same, gone = sync(a.src)
    print(f"source {a.src}: {n} files - {added} added, {changed} updated, {same} unchanged")
    if gone: print("in the repo but no longer in the live folder (git rm them if intended):\n  " + "\n  ".join(gone))


if __name__ == '__main__':
    main()
