r"""
sync_from_live.py - refresh this repository's nt8\ folder from the live NinjaTrader 8 user folder (2026-10-22).

    python tools\\sync_from_live.py [--src "C:\\Users\\<you>\\OneDrive\\Documents\\NinjaTrader 8"]

Copies (adds / updates only; never deletes - files that disappeared from the live folder are listed for you to git rm):
  CLAUDE.md                              the working brief
  bin\\Custom\\<folder>\\*.cs / *.xaml     every custom NinjaScript file (NinjaTrader's own @*.cs and generated files skipped)
  templates\\**                           ATM, chart, commission, trading-hours ... templates
  workspaces\\*.xml                       the workspace(s) ("* - Copy" files skipped)
  agent_exports\\NoiseArea_trades_* / checks_live_* / events_* / days_*   the live run outputs (2026-10-22)
  agent_exports\\records\\**               nightly ledgers (orders, fills, balances), Windows events, manifests, reviews, clock offsets
  agent_exports\\review\\**                review / monitoring / research code, results, routines, registry and the core
                                         5-minute data (skips __pycache__, _to_delete, _work, regime_work caches, *.npz, *.zst,
                                         *.dbn, files > 45 MB, the fwd\\ scratch copy)
  tools\\nt_build_check.ps1               from D:\\dev\\trading\\MNQPropBook\\tools when present
Never copied: Config.xml (holds the NinjaTrader login and password), the db\\ folder (accounts, orders, local market data),
logs, vendor DLLs (Replikanto is licensed per machine), agent_exports run outputs (trade logs, heartbeats).
"""
import os, sys, shutil, filecmp, argparse
sys.dont_write_bytecode = True

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(REPO, 'nt8')
NT_GENERATED = {'AssemblyInfo.cs', 'NinjaTrader.Vendor.cs', 'ResourceEnumConverter.cs', 'Resource.Designer.cs'}
SKIP_DIRS = {'__pycache__', '_to_delete', '_work', 'regime_work', 'recovery'}
SKIP_EXT = ('.pyc', '.npz', '.zst', '.dbn', '.tmp')
MAX_BYTES = 45e6


def default_src():
    for base in (os.path.join(os.environ.get('USERPROFILE', ''), 'OneDrive', 'Documents'), os.path.join(os.environ.get('USERPROFILE', ''), 'Documents')):
        p = os.path.join(base, 'NinjaTrader 8')
        if os.path.isdir(p): return p
    return None


def wanted(src):
    """(source path, path relative to nt8\\) for every file the package carries."""
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
    # 2026-10-22 (logging audit: nothing left this PC - OneDrive is not running): the live records go offsite too.
    # Run outputs that are appended or kept per run; the heartbeats, the rewritten checks/bars files and daystate are left out
    # (they change every 5 minutes and are archived elsewhere).
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
            if rel.replace('\\', '/').endswith('research/fwd/live_bars_research_format.csv'): continue
            out.append(rel)
    return out


def sync(src):
    """Copy every wanted file that is new or different; returns (n files, added, updated, unchanged, files gone from live)."""
    files = wanted(src); added = changed = same = 0
    for rel in files:
        s = os.path.join(src, rel); d = os.path.join(DEST, rel)
        if os.path.exists(d) and filecmp.cmp(s, d, shallow=False): same += 1; continue
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.exists(d): changed += 1
        else: added += 1
        shutil.copy2(s, d)
    tool = r'D:\dev\trading\MNQPropBook\tools\nt_build_check.ps1'
    if os.path.exists(tool):
        d = os.path.join(REPO, 'tools', 'nt_build_check.ps1')
        if not (os.path.exists(d) and filecmp.cmp(tool, d, shallow=False)): shutil.copy2(tool, d); changed += 1
    keep = {os.path.normcase(os.path.join(DEST, r)) for r in files}
    gone = sorted(os.path.relpath(os.path.join(r, f), DEST) for r, _, fs in os.walk(DEST) for f in fs
                  if os.path.normcase(os.path.join(r, f)) not in keep)
    return len(files), added, changed, same, gone


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--src', default=default_src()); a = ap.parse_args()
    if not a.src or not os.path.isdir(a.src): sys.exit(f"NinjaTrader 8 folder not found: {a.src}")
    n, added, changed, same, gone = sync(a.src)
    print(f"source {a.src}: {n} files - {added} added, {changed} updated, {same} unchanged")
    if gone: print("in the repo but no longer in the live folder (git rm them if intended):\n  " + "\n  ".join(gone))


if __name__ == '__main__':
    main()
