r"""Zero-click paper forward test of the NoiseArea near misses (DAYCAP_PLAN.txt part B, pre-registered 2026-10-21 15:23 ET).
    python fwd_shadow.py update [--account Sim101]   append every completed session since 2026-10-22 not yet in the ledger, then report
    python fwd_shadow.py report                      the ledger summary only
    python fwd_shadow.py january                     the yearly decision: forward sessions appended to the 16-year series, D1 re-run
Source: the bars the live strategy records (agent_exports\NoiseArea_bars_MNQ_strategy_<account>.csv), run through nav_lab.fam_V
(== the live rules with no switch). Costs as in the backtests: 1 tick + $0.50 a side per micro. Writes ONLY research\fwd\:
shadow_ledger.csv (append-only; a session's row is never rewritten), live_bars_research_format.csv (scratch copy of the bars) and
bars_archive_MNQ_<account>.csv (2026-10-22: the strategy rewrites its bars file from NinjaTrader's history on every start, so the
bars it saw live were lost; every calendar day is now appended once, the first copy seen, as soon as a later day is in the file).
Also converts the TapeRecorder's finished days (D:\nt_recorder\<folder>\<date>_<stream>.csv) to verified parquet copies in
D:\nt_recorder\parquet\ (2026-10-22); the CSVs stay.
A session counts once the bars file holds a bar after 16:00 ET that day. Paper only: NinjaTrader is never touched.
"""
import os, sys, argparse, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd

EXPORTS = os.path.normpath(os.path.join(HERE, '..', '..'))
FWD = os.path.join(HERE, 'fwd'); LEDGER = os.path.join(FWD, 'shadow_ledger.csv')
START = pd.Timestamp('2026-10-22')
# fixed by the plan: no additions or removals without a new plan. Value = (fam_V switches, the D1 bar it was first graded at)
TRACKED = {'base': ({}, None), 'V2': (dict(w_short=0.5), 0.0056), 'V5': (dict(gate=4), 0.0056), 'V6': (dict(lunch=True), 0.0056),
           'V7': (dict(ema=True), 0.0056), 'K1': (dict(gate=4, lunch=True, ema=True), 0.0036), 'C1': (dict(cap=1), 0.01),
           'C2': (dict(cap=2), 0.01), 'C3': (dict(stop_losses=2), 0.01), 'C4': (dict(no_relose=True), 0.01)}
NAMES = {'base': 'live rules', 'V2': 'half-size shorts', 'V5': '20-min exit gate', 'V6': 'lunch pause', 'V7': 'EMA confirmation',
         'K1': 'gate+lunch+EMA', 'C1': '1 trade/day', 'C2': '2 trades/day', 'C3': 'stop after 2 losses', 'C4': 'no re-entry after loss'}


def usd(pts, n): return pts * 2.0 - n * 2 * 1.0          # $2 a point; $1 a side (1 tick $0.50 + $0.50 commission) per micro traded


def load_ledger():
    return pd.read_csv(LEDGER, parse_dates=['date']) if os.path.exists(LEDGER) else None


TAPE_DIR = r'D:\nt_recorder'      # TapeRecorder.cs output (outside OneDrive)


def archive_tape(root=TAPE_DIR):
    """Every finished UTC day the TapeRecorder wrote (<root>\\<folder>\\<date>_<stream>.csv) gets a typed, verified parquet copy in
    <root>\\parquet\\<folder>\\ (the CSV stays; nothing is removed). A day still being written (today, UTC) is left alone."""
    import glob
    if not os.path.isdir(root): print("tape archive: no recorder output yet"); return
    today = dt.datetime.utcnow().strftime('%Y-%m-%d'); made = []; bad = []
    for src in sorted(glob.glob(os.path.join(root, '*', '*.csv'))):
        folder = os.path.basename(os.path.dirname(src)); name = os.path.basename(src)
        if folder in ('parquet', '_to_delete') or name[:10] >= today: continue
        dst = os.path.join(root, 'parquet', folder, name[:-4] + '.parquet')
        if os.path.exists(dst): continue
        try:
            df = pd.read_csv(src, low_memory=False)
            for c in df.columns:
                if c.endswith('_utc'): df[c] = pd.to_datetime(df[c], errors='coerce')
            os.makedirs(os.path.dirname(dst), exist_ok=True); df.to_parquet(dst + '.tmp', index=False)
            if len(pd.read_parquet(dst + '.tmp', columns=[df.columns[0]])) != len(df): raise ValueError('row count differs after writing')
            os.replace(dst + '.tmp', dst); made.append(f"{folder}/{name[:-4]} ({len(df):,} rows)")
        except Exception as e:
            bad.append(f"{folder}/{name}: {e}")
    print("tape archive: " + (f"{len(made)} day file(s) converted: " + ', '.join(made[:6]) + (' ...' if len(made) > 6 else '') if made else "nothing new")
          + (f" | FAILED: {'; '.join(bad)}" if bad else ''))


CONTEXT = {'vix_history_cboe_latest.csv': 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv',
           'vix3m_history_cboe_latest.csv': 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX3M_History.csv'}


def refresh_context(timeout=20):
    """2026-10-22: keep the free daily context series current (CBOE VIX / VIX3M full histories) in research\\event_calendars\\.
    A download replaces the file only if it parses and is at least as long as the copy it replaces; any failure is reported."""
    import urllib.request, io
    d = os.path.join(HERE, 'event_calendars'); os.makedirs(d, exist_ok=True); msgs = []
    for name, url in CONTEXT.items():
        p = os.path.join(d, name)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r: raw = r.read()
            new = pd.read_csv(io.BytesIO(raw)); old_n = len(pd.read_csv(p)) if os.path.exists(p) else 0
            if len(new) < max(old_n, 1000): raise ValueError(f"only {len(new)} rows (had {old_n})")
            with open(p + '.tmp', 'wb') as f: f.write(raw)
            os.replace(p + '.tmp', p); msgs.append(f"{name.split('_')[0].upper()} to {new.iloc[-1, 0]} ({len(new):,} days)")
        except Exception as e:
            msgs.append(f"{name} FAILED ({e})")
    print("context data: " + '; '.join(msgs))


def archive_bars(b, account):
    """Append every calendar day of the bars file that is complete (a later day exists) and not yet archived; never rewrites."""
    arc = os.path.join(FWD, f'bars_archive_MNQ_{account}.csv')
    t = pd.to_datetime(b.time); d = t.dt.date
    have = set(pd.to_datetime(pd.read_csv(arc, usecols=['time']).time).dt.date) if os.path.exists(arc) else set()
    new = b[(d < d.max()) & ~d.isin(have)].copy()
    if len(new):
        new['recorded'] = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
        new.to_csv(arc, mode='a', header=not os.path.exists(arc), index=False)
    n_days = len(set(pd.to_datetime(new.time).dt.date)) if len(new) else 0
    tot = len(have) + n_days
    print(f"bars archive ({os.path.basename(arc)}): {n_days} day(s) added, {tot} calendar days kept")


def update(account):
    import nav_lab as N
    from research_engine import Market
    src = os.path.join(EXPORTS, f'NoiseArea_bars_MNQ_strategy_{account}.csv')
    if not os.path.exists(src): print(f"paper forward test: no bars file {os.path.basename(src)} - nothing recorded"); return
    os.makedirs(FWD, exist_ok=True); tmp = os.path.join(FWD, 'live_bars_research_format.csv')
    b = pd.read_csv(src).drop_duplicates('time_et', keep='last').rename(columns={'time_et': 'time'}); b.to_csv(tmp, index=False)
    archive_bars(b, account)
    try: archive_tape()
    except Exception as e: print(f"tape archive: error {e}")
    try: refresh_context()
    except Exception as e: print(f"context data: error {e}")
    t = pd.to_datetime(b.time); mk = Market(tmp, 1.0, 'LIVE')
    done = [d for d in mk.dates if d >= START and (t > d + pd.Timedelta(hours=16)).any()]
    old = load_ledger(); have = set(old.date) if old is not None else set()
    new = [d for d in done if d not in have]
    if new:
        res = {v: N.fam_V(mk, **kw) for v, (kw, _) in TRACKED.items()}
        rows = []
        for d in new:
            k = int(np.where(mk.dates == d)[0][0]); r = {'date': d.date(), 'open930': mk.days[k].O[0]}
            for v, (p, n) in res.items(): r[f'{v}_pts'] = round(float(np.nan_to_num(p[k])), 4); r[f'{v}_n'] = float(n[k])
            r['recorded'] = dt.datetime.now().strftime('%Y-%m-%d %H:%M'); r['bars_file_mtime'] = dt.datetime.fromtimestamp(os.path.getmtime(src)).strftime('%Y-%m-%d %H:%M')
            rows.append(r)
        df = pd.DataFrame(rows)
        df.to_csv(LEDGER, mode='a', header=not os.path.exists(LEDGER), index=False)
    print(f"paper forward test: {len(new)} session(s) added" + (f" ({', '.join(str(d.date()) for d in new)})" if new else ''))
    report()


def report():
    L = load_ledger()
    if L is None or not len(L): print(f"paper forward test: no sessions yet (first forward session {START.date()})"); return
    L = L.sort_values('date'); last = L.iloc[-1]
    tot = {v: usd(L[f'{v}_pts'], L[f'{v}_n']) for v in TRACKED}
    def mdd(x): eq = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    print(f"paper forward test since {L.date.iloc[0].date()}: {len(L)} sessions; total $ per micro (max drawdown), paper fills at 1 tick:")
    print('   ' + ' | '.join(f"{v} {NAMES[v]} {tot[v].sum():+,.0f} ({mdd(tot[v].values):,.0f})" for v in TRACKED))
    print(f"   last session {last.date.date()}: " + ', '.join(f"{v} {usd(last[f'{v}_pts'], last[f'{v}_n']):+,.0f}" for v in TRACKED))
    print("   (no decision before January: a year of sessions cannot separate these; the yearly test is 'fwd_shadow.py january')")


def january():
    import nav_lab as N, navar as NV
    L = load_ledger()
    if L is None or not len(L): print("no forward sessions yet"); return
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 + 0.25) / p_ref; L = L.sort_values('date').set_index('date')
    fwd = {v: L[f'{v}_pts'] / L.open930 - L[f'{v}_n'] * 2 * c for v in TRACKED}
    r0, _ = N.series('MNQ', fn=N.fam_V); r0 = pd.concat([r0, fwd['base'][fwd['base'].index > r0.index[-1]]])
    print(f"YEARLY DECISION (DAYCAP_PLAN part B): 16-year series + {len(L)} forward sessions {L.index[0].date()} .. {L.index[-1].date()}")
    for v, (kw, bar) in TRACKED.items():
        if v == 'base': continue
        rv, _ = N.series('MNQ', fn=N.fam_V, **kw); rv = pd.concat([rv, fwd[v][fwd[v].index > rv.index[-1]]])
        p = NV.boot_mar(rv, r0); fm, bm = NV.mar(fwd[v]), NV.mar(fwd['base'])
        ok = p < bar and fm >= bm
        print(f"   {v} {NAMES[v]}: extended D1 P {p:.4f} (bar {bar}) | forward MAR {fm:.3f} vs base {bm:.3f} -> "
              f"{'CANDIDATE: build, compile, Analyzer parity, then Sashkin decides' if ok else 'no'}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['update', 'report', 'january']); ap.add_argument('--account', default='Sim101')
    a = ap.parse_args()
    if a.cmd == 'update': update(a.account)
    elif a.cmd == 'report': report()
    else: january()
