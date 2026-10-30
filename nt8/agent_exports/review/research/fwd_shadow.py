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


DUP_UNTIL = '2026-10-31'   # tape files dated up to this UTC day can hold every event twice (TapeRecorder bug found 2026-10-22: strict
                           # back-to-back pairs identical but for recv_utc); their parquet copy keeps the first row of each pair
_REC_HEADERS = {'tape': 'kind,feed_utc,recv_utc,price,size,bid,ask', 'bars': 'time_et,open,high,low,close,volume,recv_utc',
                'orders': 'recv_utc,event_utc,account,instrument,order_id,name,action,type,state,quantity,filled,avg_fill,limit,stop,'
                          'chart_bid,chart_ask,error,server,comment',
                'executions': 'recv_utc,exec_utc,account,instrument,exec_id,order_id,order_name,position,price,quantity,chart_bid,chart_ask',
                'connections': 'recv_utc,connection,status,price_status,previous_status,previous_price_status,error,native_error'}
_REC_F8 = {'price', 'bid', 'ask', 'open', 'high', 'low', 'close', 'avg_fill', 'limit', 'stop', 'chart_bid', 'chart_ask'}
_REC_I8 = {'size', 'volume', 'quantity', 'filled'}     # *_utc = timestamp[us]; every other column (ids included) = text


def _recorder_csv_to_parquet(src, dst, walk, drop, block=1 << 24):
    """Helper of archive_tape: one TapeRecorder CSV -> typed parquet, streamed block by block (peak memory a few hundred MB, whatever
    the size of the day). walk (tape files): run the duplicate-pair walk - from the first row, while row 2k equals row 2k+1 in every
    column but recv_utc it is a pair; the first row that breaks the pairing switches to single mode for the rest of the file; drop:
    also leave out the second row of each pair (else the pairs are only counted). Malformed rows (wrong column count, or a line cut
    short by a power cut: NUL bytes or no final line break) are dropped and counted; NUL bytes are counted; unreadable values become
    null and are counted.
    Writes <dst>.tmp, re-opens it and checks row count (= rows read - duplicates dropped - malformed), schema, first and last row
    and null counts, then renames it to dst; on any failure the .tmp is removed and the error raised. Returns the counts."""
    import io, re, functools, numpy as np, pyarrow as pa, pyarrow.compute as pc, pyarrow.csv as pcsv, pyarrow.parquet as pq
    stream = os.path.basename(src)[11:-4]
    known = _REC_HEADERS.get('bars' if stream.startswith('bars') else stream)
    s = dict(read=0, rows=0, malformed=0, dups=0, dropped=0, bad_ts=0, bad_num=0, nul=0, mode='pair', switch=None, switch_t='',
             orphan=False, single=0, repeats=0, header='ok', text=[])
    size = os.path.getsize(src)
    with open(src, 'rb') as f: head = f.read(1 << 16).replace(b'\0', b'\n')
    line0 = next((x.strip() for x in head.splitlines() if x.strip()), b'').decode('utf-8', 'replace')
    hdr = re.fullmatch(r'[A-Za-z_]\w*(,[A-Za-z_]\w*)*', line0) is not None
    end0 = head.find(b'\n', head.find(line0.encode('utf-8', 'replace'))) + 1 or len(head)     # first byte after the header line
    if hdr and not (known and line0 != known and known.startswith(line0)): cols, start = line0.split(','), end0
    elif known:                      # header cut short or missing (power cut before it was flushed), or an empty file: the recorder's columns
        cols, start = known.split(','), (end0 if hdr else 0); s['header'] = 'damaged' if hdr else 'missing' if line0 else 'empty'
    else: raise ValueError(f"no header line (starts {line0[:30]!r}) and stream '{stream}' has no known header")
    TYPES = {'ts': pa.timestamp('us'), 'f8': pa.float64(), 'i8': pa.int64(), 'str': pa.large_string()}
    kinds = ['ts' if c.endswith('_utc') else 'f8' if c in _REC_F8 else 'i8' if c in _REC_I8 else 'str' for c in cols]
    schema = pa.schema([(c, TYPES[k]) for c, k in zip(cols, kinds)])
    s['text'] = [c for c, k in zip(cols, kinds) if k == 'str' and c not in (known or '').split(',')]
    cmp = [c for c in cols if c != 'recv_utc']; tsc = [c for c, k in zip(cols, kinds) if k == 'ts']

    def row1(t, i): return pa.Table.from_pylist(t.slice(i, 1).to_pylist(), schema=t.schema)   # a copy, not a view of the block

    def same(x, y):                  # row by row: identical in every column but recv_utc (empty == empty)
        m = np.ones(x.num_rows, bool)
        for c in cmp:
            a, b = x.column(c), y.column(c)
            m &= pc.or_(pc.fill_null(pc.equal(a, b), False), pc.and_(pc.is_null(a), pc.is_null(b))).to_numpy()
        return m

    def switch(pos, t):
        s['mode'], s['switch'] = 'single', pos
        s['switch_t'] = str(t.column('recv_utc')[0].as_py()) if 'recv_utc' in cols else ''

    carry = prev = None              # carry: first row of a pair whose second row is in the next block; prev: last row (single mode)

    def pairs(t, g):                 # t = one block, g = position of its first row among the rows read; returns the rows to keep
        nonlocal carry, prev
        keep = []
        if s['mode'] == 'pair' and carry is not None:
            keep.append(carry)
            if same(carry, t.slice(0, 1))[0]: s['dups'] += 1; t, g = t.slice(1), g + 1
            else: switch(g - 1, carry)
            carry = None
        elif g == 0 and t.num_rows >= 7 and not same(t.slice(0, 1), t.slice(1, 1))[0] and same(t.take([1, 3, 5]), t.take([2, 4, 6])).all():
            keep.append(t.slice(0, 1)); t, g = t.slice(1), 1; s['orphan'] = True   # the day began with the second row of a pair
        rest = t                                                                     # split at 00:00 UTC: pairing starts at row 2
        if s['mode'] == 'pair':
            n = t.num_rows; m = n // 2
            ev, od = t.take(np.arange(0, 2 * m, 2)), t.take(np.arange(1, 2 * m, 2))
            ok = same(ev, od); k = m if ok.all() else int(np.argmin(ok))
            keep.append(ev.slice(0, k)); s['dups'] += k
            if k < m: switch(g + 2 * k, t.slice(2 * k, 1)); rest = t.slice(2 * k)
            else: rest = None; carry = row1(t, n - 1) if n % 2 else None
        if rest is not None and rest.num_rows:      # single mode keeps every row; counts rows that repeat the row before (information)
            n = rest.num_rows; s['single'] += n
            s['repeats'] += int(same(rest.slice(1), rest.slice(0, n - 1)).sum()) + (int(same(prev, rest.slice(0, 1))[0]) if prev is not None else 0)
            prev = row1(rest, n - 1); keep.append(rest)
        return pa.concat_tables(keep) if keep else t.slice(0, 0)

    def conv(col, k):
        if k == 'str': return col
        try: return pc.cast(col, TYPES[k])
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):      # a damaged value in this block: one by one, unreadable -> null
            def one(v):
                try: return None if v is None else pa.scalar(v, pa.large_string()).cast(TYPES[k]).as_py()
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError, ValueError, OverflowError): return None
            return pa.chunked_array([pa.array([one(v) for v in col.to_pylist()], TYPES[k])])

    nulls = [0] * len(cols); fr = lr = None

    def write(t):
        nonlocal fr, lr
        u = pa.Table.from_arrays([conv(t.column(c), k) for c, k in zip(cols, kinds)], schema=schema)
        for i, (c, k) in enumerate(zip(cols, kinds)):
            nulls[i] += u.column(i).null_count
            if k in ('f8', 'i8'): s['bad_num'] += u.column(i).null_count - t.column(c).null_count
        if tsc: s['bad_ts'] += int(functools.reduce(pc.or_, [pc.is_null(u.column(c)) for c in tsc]).to_numpy().sum())
        writer.write_table(u); s['rows'] += u.num_rows
        if fr is None: fr = row1(u, 0)
        lr = row1(u, u.num_rows - 1)

    def bad_row(r): s['malformed'] += 1; return 'skip'

    quoted = not (stream == 'tape' or stream.startswith('bars'))     # account files can hold quoted values with line breaks

    def blocks(raw):                 # line-aligned blocks of ~block bytes, read here (pyarrow's own streaming reader reads ahead
        rest = b''                   # without limit). A run of NUL bytes (power cut) ends a block; the line it cut short, and a
        while True:                  # last line without its line break, are dropped as malformed (counted), never parsed half
            d = raw.read(block); eof = not d
            if b'\0' in d: s['nul'] += d.count(b'\0'); *done, d = re.split(b'\0+', rest + d)
            else: done, d = [], rest + d
            if eof: done.append(d)
            for p in done:
                i = len(p) if p.endswith(b'\r') else p.rfind(b'\n') + 1
                if p[i:].strip(): s['malformed'] += 1
                if p[:i].strip(): yield p[:i]
            if eof: return
            cut = d.rfind(b'\n') + 1
            while cut and quoted and d.count(b'"', 0, cut) % 2: cut = d.rfind(b'\n', 0, cut - 1) + 1     # never inside quotes
            if not cut and len(d) > 4 * block: raise ValueError(f"a line longer than {4 * block >> 20} MB")
            if d[:cut].strip(): yield d[:cut]
            rest = d[cut:]

    ro = pcsv.ReadOptions(column_names=cols, use_threads=False)        # one core: NinjaTrader runs on this PC
    po = pcsv.ParseOptions(newlines_in_values=quoted, invalid_row_handler=bad_row)
    co = pcsv.ConvertOptions(column_types={c: pa.large_string() for c in cols}, strings_can_be_null=True, null_values=[''])
    tmp = dst + '.tmp'; writer = None
    try:
        with open(src, 'rb') as raw:
            s['nul'] += raw.read(start).count(b'\0')
            writer = pq.ParquetWriter(tmp, schema, compression='snappy')
            if size > start:
                for d in blocks(raw):
                    t = pcsv.read_csv(io.BytesIO(d), read_options=ro, parse_options=po, convert_options=co); del d
                    if not t.num_rows: continue
                    g = s['read']; s['read'] += t.num_rows
                    if walk and (drop or s['mode'] == 'pair'):
                        k = pairs(t, g)
                        if drop: t = k
                    if t.num_rows: write(t)
        if walk and drop and carry is not None: write(carry)        # the file ended inside a pair: its first row is kept
        writer.close(); writer = None
        s['dropped'] = s['dups'] if drop else 0
        exp = (s['read'] + s['malformed']) - s['dropped'] - s['malformed']
        with open(tmp, 'rb') as fh:
            pf = pq.ParquetFile(fh); md = pf.metadata
            if md.num_rows != exp or s['rows'] != exp:
                raise ValueError(f"row count after writing {md.num_rows:,} (written {s['rows']:,}, expected {exp:,})")
            if not pf.schema_arrow.equals(schema): raise ValueError("schema differs after writing")
            if exp:
                g0, gl = pf.read_row_group(0), pf.read_row_group(md.num_row_groups - 1)
                if not (g0.slice(0, 1).equals(fr) and gl.slice(gl.num_rows - 1).equals(lr)): raise ValueError("first/last row differ after writing")
            for i, c in enumerate(cols):
                st = [md.row_group(r).column(i).statistics for r in range(md.num_row_groups)]
                if all(x is not None and x.has_null_count for x in st) and sum(x.null_count for x in st) != nulls[i]:
                    raise ValueError(f"null count of {c} differs after writing")
        os.replace(tmp, dst)
    except BaseException:
        if writer is not None:
            try: writer.close()
            except Exception: pass
        try: os.remove(tmp)
        except OSError: pass
        raise
    return s


def archive_tape(root=TAPE_DIR, today=None, settle_s=300):
    """Every finished UTC day the TapeRecorder wrote (<root>\\<folder>\\<date>_<stream>.csv) gets a typed, verified parquet copy in
    <root>\\parquet\\<folder>\\ (the CSV stays; nothing is removed). A day still being written (today in UTC, or a file changed in the
    last settle_s seconds) is left alone; an existing parquet is not redone. 2026-10-22: streamed in blocks (memory stays bounded on
    the live PC whatever the size of the day); tape files dated up to DUP_UNTIL lose the second row of each duplicate pair (the CSV
    keeps them); malformed rows / NUL bytes / unreadable values are counted in the report. One line 'tape archive: ...'; any failure
    says FAILED (archive_nightly.py raises on it)."""
    import glob, time
    if not os.path.isdir(root): print("tape archive: no recorder output yet"); return
    today = today or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d'); made = []; bad = []; notes = []
    for src in sorted(glob.glob(os.path.join(root, '*', '*.csv'))):
        folder = os.path.basename(os.path.dirname(src)); name = os.path.basename(src); tag = f"{folder}/{name[:-4]}"
        if folder in ('parquet', '_to_delete') or name[:10] >= today: continue
        dst = os.path.join(root, 'parquet', folder, name[:-4] + '.parquet')
        if os.path.exists(dst): continue
        if time.time() - os.path.getmtime(src) < settle_s: notes.append(f"{tag} changed in the last {settle_s} s - left for the next run"); continue
        if os.path.exists(dst + '.tmp') and time.time() - os.path.getmtime(dst + '.tmp') < 3600:
            notes.append(f"{tag} is being converted by another run"); continue
        walk = folder != 'accounts' and name[11:-4] == 'tape'; drop = walk and name[:10] <= DUP_UNTIL; t0 = time.time()
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True); s = _recorder_csv_to_parquet(src, dst, walk, drop)
        except Exception as e:
            bad.append(f"{tag}: {type(e).__name__}: {e}"); continue
        x = f"{tag} ({s['rows']:,} rows"
        if drop:
            x += f"; {s['dups']:,} duplicate row(s) dropped, " + ("pairs to the end" if s['mode'] == 'pair' else
                 f"single mode from data row {s['switch'] + 1:,} (recv {s['switch_t']}), then {s['repeats']:,} of {s['single']:,} rows "
                 f"({100 * s['repeats'] / max(s['single'], 1):.1f}%) repeat the row before")
            if s['orphan']: x += "; first row unpaired (pair split at 00:00 UTC)"
        elif walk and s['dups'] >= 100: x += f"; WARNING {s['dups']:,} leading duplicate pairs kept (dated after DUP_UNTIL {DUP_UNTIL})"
        if s['malformed']: x += f"; {s['malformed']:,} malformed row(s) dropped"
        if s['bad_ts']: x += f"; {s['bad_ts']:,} row(s) with an unreadable or empty timestamp kept as null"
        if s['bad_num']: x += f"; {s['bad_num']:,} unreadable number(s) kept as null"
        if s['nul']: x += f"; {s['nul']:,} NUL bytes (power cut) skipped"
        if s['header'] == 'empty' and not s['rows']: x += "; empty file"
        elif s['header'] != 'ok': x += f"; header {s['header'].replace('empty', 'missing')} - read with the recorder's columns"
        if s['text']: x += f"; column(s) not in the recorder's header kept as text: {', '.join(s['text'])}"
        made.append(x + f"; {time.time() - t0:.0f} s)")
    print("tape archive: " + (f"FAILED {len(bad)} file(s): {'; '.join(bad)} | " if bad else '')
          + (f"{len(made)} day file(s) converted: " + ', '.join(made) if made else "nothing new") + (f" | {'; '.join(notes)}" if notes else ''))


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
    # 2026-10-24 later: the full validation (CHVAL_PLAN / CHVAL_RESULTS.txt) KILLED the chandelier exit (K1 discovery MNQ 2019-22
    # worse, K2 deeper drawdown in 7 of 17 years, K4 2 of 5 unseen series). 2026-10-30: Sashkin adopted it on Sim101 anyway - his
    # decision, not a validation - so its paper ledger runs again: every review shows the rules WITH the exit (what Sim101 trades from
    # the 20261030_chandelier build) against WITHOUT (the main ledger's 'base' column), on the same bars.
    try: update_strat3(mk, t, src)
    except Exception as e: print(f"chandelier paper ledger: error {e}")
    report()


# 2026-10-24 (STRAT3_RESULTS.txt): the chandelier exit (3 x ATR, the pre-registered value) - rejected at STRAT3's significance bar
# but better on every other gate and every multiple tried - is paper-traded from the first session after its plan, in its own
# append-only ledger (the main ledger's columns are fixed by DAYCAP_PLAN part B).
STRAT3_LEDGER = os.path.join(FWD, 'shadow_ledger_strat3.csv'); STRAT3_START = pd.Timestamp('2026-10-26'); STRAT3_BAR = 0.01


def update_strat3(mk, t, src):
    import strat3 as S3
    done = [d for d in mk.dates if d >= STRAT3_START and (t > d + pd.Timedelta(hours=16)).any()]
    have = set(pd.read_csv(STRAT3_LEDGER, parse_dates=['date']).date) if os.path.exists(STRAT3_LEDGER) else set()
    new = [d for d in done if d not in have]
    if new:
        p, n, _ = S3.run(mk, ch=3.0); now = dt.datetime.now().strftime('%Y-%m-%d %H:%M')
        rows = [{'date': d.date(), 'open930': mk.days[k].O[0], 'CH_pts': round(float(np.nan_to_num(p[k])), 4), 'CH_n': float(n[k]), 'recorded': now,
                 'bars_file_mtime': dt.datetime.fromtimestamp(os.path.getmtime(src)).strftime('%Y-%m-%d %H:%M')}
                for d in new for k in [int(np.where(mk.dates == d)[0][0])]]
        pd.DataFrame(rows).to_csv(STRAT3_LEDGER, mode='a', header=not os.path.exists(STRAT3_LEDGER), index=False)
    print(f"chandelier paper ledger: {len(new)} session(s) added")


def report():
    L = load_ledger()
    if L is None or not len(L): print(f"paper forward test: no sessions yet (first forward session {START.date()})"); return
    L = L.sort_values('date'); last = L.iloc[-1]
    tot = {v: usd(L[f'{v}_pts'], L[f'{v}_n']) for v in TRACKED}
    def mdd(x): eq = np.cumsum(x); return float((np.maximum.accumulate(np.r_[0.0, eq])[1:] - eq).max())
    print(f"paper forward test since {L.date.iloc[0].date()}: {len(L)} sessions; total $ per micro (max drawdown), paper fills at 1 tick:")
    print('   ' + ' | '.join(f"{v} {NAMES[v]} {tot[v].sum():+,.0f} ({mdd(tot[v].values):,.0f})" for v in TRACKED))
    print(f"   last session {last.date.date()}: " + ', '.join(f"{v} {usd(last[f'{v}_pts'], last[f'{v}_n']):+,.0f}" for v in TRACKED))
    if os.path.exists(STRAT3_LEDGER):
        C3 = pd.read_csv(STRAT3_LEDGER, parse_dates=['date']); both = L[L.date.isin(C3.date)]
        print(f"   chandelier exit (STRAT3) since {STRAT3_START.date()}: {len(C3)} sessions {usd(C3.CH_pts, C3.CH_n).sum():+,.0f} "
              f"(max drawdown {mdd(usd(C3.CH_pts, C3.CH_n).values):,.0f}) vs live rules on the same sessions {usd(both.base_pts, both.base_n).sum():+,.0f}")
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
    if os.path.exists(STRAT3_LEDGER):                         # STRAT3 chandelier exit: the same rule, its own bar (0.01)
        import metalabel as ML, strat3 as S3
        C3 = pd.read_csv(STRAT3_LEDGER, parse_dates=['date']).sort_values('date').set_index('date')
        f3 = C3.CH_pts / C3.open930 - C3.CH_n * 2 * c
        res = {nm: (N.market(nm),) + S3.run(N.market(nm), ch=3.0)[:2] for nm, _ in ML.PARTS}
        h3 = ML.joined(res); h3 = pd.concat([h3, f3[f3.index > h3.index[-1]]])
        b3 = ML.joined({nm: (N.market(nm),) + S3.run(N.market(nm))[:2] for nm, _ in ML.PARTS}); b3 = pd.concat([b3, fwd['base'][fwd['base'].index > b3.index[-1]]])
        p = NV.boot_mar(h3, b3); fm, bm = NV.mar(f3), NV.mar(fwd['base'].reindex(f3.index).dropna())
        print(f"   CH chandelier exit 3 x ATR: extended D1 P {p:.4f} (bar {STRAT3_BAR}) | forward MAR {fm:.3f} vs base {bm:.3f} -> "
              f"{'CANDIDATE: build, compile, Analyzer parity, then Sashkin decides' if p < STRAT3_BAR and fm >= bm else 'no'}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('cmd', choices=['update', 'report', 'january']); ap.add_argument('--account', default='Sim101')
    a = ap.parse_args()
    if a.cmd == 'update': update(a.account)
    elif a.cmd == 'report': report()
    else: january()
