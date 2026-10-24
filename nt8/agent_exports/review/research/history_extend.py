r"""HISTORY EXTEND (2026-10-24): the 16-year research history plus every session recorded since, rebuilt every night.
    python history_extend.py [--account Sim101] [--dry]
        -> scaling_inputs\mnq_bars_extended.csv, intraday_worst_extended.csv, history_extend_status.txt
           (archive_nightly.py step 'history' runs it every evening, after the bars archive and the minute-store export)
The research series (nav_lab.FILES['MNQ'] = the NinjaTrader Analyzer export to 2026-10-14, back-adjusted to MNQ 12-26) and
intraday_worst.csv stay FROZEN: every published result reproduces from them. This builds extended copies next to them.
BARS after the frozen end, per 5-minute timestamp:
  1. research\fwd\bars_archive_MNQ_<account>.csv - the first copy of each day the live strategy recorded (fwd_shadow.archive_bars)
  2. the strategy's own bars file: the days the archive does not hold yet (it takes a day once a later one exists, so today's session
     is added the same evening) and holes on days where the two agree
  3. NinjaTrader's minute store (server history; archive_nightly step 'ticks' exports it to D:\nt_ticks\minute), same contract,
     complete 5-minute groups: holes
  4. the TapeRecorder's live 5-minute bars (D:\nt_recorder\MNQ_<contract>\<UTC date>_bars5min*.csv), same contract: holes
  Sources 2-4 also replace a bar with materially less volume (MATERIAL: a partial bar - ticks missed around a restart, e.g.
  2026-10-23 09:55); one-contract differences are ticks stamped on a bar boundary and are left alone. An added bar must open within
  LEVEL of the close before it. The newest day waits until its session is complete.
CONTRACT ROLLS: every bar is tagged with the contract whose minute-store bar has the same close at that time (untagged bars carry the
  previous tag). After a change of contract the later bars are shifted by the new contract's close minus the old one's at the last
  minute both traded - NinjaTrader's own back-adjust offset (+299.75 points at the 2026-09 roll, reproduced from the minute store) -
  so the extended part stays on the frozen basis; a change whose offset cannot be measured fails the run. Returns of the added
  sessions are taken against their raw 09:30 open (raw_open930.csv first, as intraday_worst.build).
SERIES: intraday_worst.day_close_and_worst on the extended bars with intraday_worst.build's NQ part, warm-up and costs; every row up to
  2026-10-14 must equal intraday_worst.csv exactly (a mismatch fails the run); column 'source' tells where each session came from.
CHECKS (printed and kept in the status file): the archive against the frozen file on their overlap (bars, then each session's result
  recomputed from the archive alone), the minute store and the TapeRecorder against the bars used (same contract, before the roll
  shift), and every CME session after the frozen end: one missing, or missing more than MAX_HOLE day-session bars, within FRESH_DAYS
  of the newest fails the run (an alert; the watchdog already pushes bar holes live).
Read-only for every source; writes only the three files above (each through a .tmp file and an atomic replace).
Research use: `import history_extend` registers nav_lab.FILES['MNQX'] (nav_lab.series('MNQX') = the joined series, extended);
sizes.py --extended reads intraday_worst_extended.csv.
"""
import os, sys, io, glob, argparse, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
REVIEW = os.path.dirname(HERE)
import numpy as np, pandas as pd
import nav_lab as N

EXPORTS = os.path.normpath(os.path.join(HERE, '..', '..'))
FROZEN_BARS = N.FILES['MNQ']; FROZEN_WORST = os.path.join(HERE, 'intraday_worst.csv')
EXT_BARS = os.path.join(HERE, 'scaling_inputs', 'mnq_bars_extended.csv'); EXT_WORST = os.path.join(HERE, 'intraday_worst_extended.csv')
STATUS = os.path.join(HERE, 'history_extend_status.txt')
RECORDER = r'D:\nt_recorder'; NT_MINUTES = r'D:\nt_ticks\minute\MNQ'
COLS = ['time', 'open', 'high', 'low', 'close', 'volume']; PX = ['open', 'high', 'low', 'close']
FRESH_DAYS = 5                   # a CME session missing within this many days of the newest one fails the run (an alert); older: listed
MAX_HOLE = 3                     # ... as does one missing more day-session bars than this (a feed outage of a minute or two, already
                                 # pushed live by the watchdog, is listed and fills itself once NinjaTrader's history holds the bar)
MATERIAL = (1.005, 5)            # another source replaces a bar whose volume is below its volume / 1.005 - 5 contracts
LEVEL = 0.03                     # an added bar opens within 3% of the close before it (a bad print or a stray file is not added)
N.FILES['MNQX'] = EXT_BARS


def archive_path(account): return os.path.join(HERE, 'fwd', f'bars_archive_MNQ_{account}.csv')
def bars_file_path(account): return os.path.join(EXPORTS, f'NoiseArea_bars_MNQ_strategy_{account}.csv')


def read_bars(path, time_col='time'):
    b = pd.read_csv(path).rename(columns={time_col: 'time'})
    b['time'] = pd.to_datetime(b.time).astype('datetime64[ns]')
    return b


def session_complete(day_bars, end_min):
    """The calendar day's day session is in the file: a bar stamped at (or after) the close."""
    m = day_bars.time.dt.hour * 60 + day_bars.time.dt.minute
    return bool((m >= end_min).any())


def cme_sessions(a, b):
    """CME equity-index day sessions (date -> close minute ET) from a to b inclusive, from daily_review.py's calendar."""
    sys.path.insert(0, REVIEW); import daily_review as R
    out, d = {}, a
    while d <= b:
        if d.weekday() < 5 and d not in R.full_closures(d.year): out[d] = R.early_closes(d.year).get(d, 16 * 60)
        d += dt.timedelta(days=1)
    return out


def contract_store(root=NT_MINUTES, after=None):
    """{contract: (5-minute bars, minutes)} from NinjaTrader's minute store in ET, from 4 days before `after` on. The 5-minute bars are
    complete groups of 5 minutes stamped at their end like NinjaTrader's (minutes stamped 10:16 .. 10:20 -> the 10:20 bar)."""
    out = {}
    for f in sorted(glob.glob(os.path.join(root, '*.parquet'))):
        m = pd.read_parquet(f, columns=COLS)
        t = pd.to_datetime(m.time, utc=True).dt.tz_convert('America/New_York').dt.tz_localize(None).astype('datetime64[ns]')
        m = m.assign(time=t).sort_values('time')
        if len(m) and (m.time.iloc[-1].hour, m.time.iloc[-1].minute) != (17, 0): m = m.iloc[:-1]   # cut short (store loaded mid-minute)
        if after is not None: m = m[m.time > after - pd.Timedelta(days=4)]
        if not len(m): continue
        g = m.groupby(m.time.dt.ceil('5min'))
        b = g.agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'), close=('close', 'last'), volume=('volume', 'sum'), n=('open', 'size'))
        b = b[b.n == 5].drop(columns='n').rename_axis('time').reset_index()
        out[os.path.basename(f)[:-8]] = (b.assign(contract=os.path.basename(f)[:-8]), m[['time', 'close']].reset_index(drop=True))
    return out


def recorder_bars(root=RECORDER):
    """The TapeRecorder's 5-minute bars, with the contract from the folder name (MNQ_12-26 -> 12-26)."""
    parts = []
    for f in sorted(glob.glob(os.path.join(root, '*', '*_bars5min*.csv'))):
        folder = os.path.basename(os.path.dirname(f))
        if folder in ('parquet', '_to_delete', 'accounts'): continue
        parts.append(read_bars(f, 'time_et')[COLS].assign(contract=folder.split('_', 1)[-1]))
    if not parts: return None
    return pd.concat(parts).drop_duplicates(['time', 'contract'], keep='first').sort_values('time').reset_index(drop=True)


def tag(bars, store, default=None, fill=True):
    """Contract of each bar: the minute-store contract with the same 5-minute close at that time; with `fill`, untagged bars carry the
    previous tag (then the next one, then `default`)."""
    lab = pd.Series([None] * len(bars), index=bars.index, dtype=object)
    for k, (b5, _) in store.items():
        c = bars[['time']].merge(b5[['time', 'close']], on='time', how='left').close.values
        lab[(c == bars.close.values) & lab.isna().values] = k
    return lab.ffill().bfill().fillna(default) if fill else lab


def patch(base, alt, name, ref_tail):
    """Bars of `alt` that fill a timestamp `base` lacks, or replace a base bar with materially less volume (MATERIAL). Used only: from
    the contract the base holds at that time (when `alt` names contracts); on days where `alt` and the base agree on the closes they
    share; opening within LEVEL of the close before. Returns (base, filled, replaced, refused bars, days skipped)."""
    alt = alt.sort_values('time').reset_index(drop=True)
    if 'contract' in alt and len(alt) and len(base):
        lab = pd.merge_asof(alt[['time']], base[['time', 'contract']].sort_values('time'), on='time', direction='nearest').contract.values
        alt = alt[alt.contract.values == lab]
    alt = alt.drop_duplicates('time', keep='first').reset_index(drop=True)
    if not len(alt): return base, 0, 0, 0, 0
    sh = alt[['time', 'close']].merge(base[['time', 'close']], on='time', suffixes=('', '_b'))
    off = (sh.close - sh.close_b).groupby(sh.time.dt.date).median(); skip = set(off[off != 0].index)
    alt = alt[~alt.time.dt.date.isin(skip)].reset_index(drop=True)
    if not len(alt): return base, 0, 0, 0, len(skip)
    ref_df = pd.concat([ref_tail[['time', 'close']], base[['time', 'close']]]).sort_values('time').drop_duplicates('time', keep='last')
    ref = pd.merge_asof(alt[['time']], ref_df, on='time', allow_exact_matches=False).close.values
    sane = np.abs(alt.open.values - ref) <= LEVEL * ref                     # NaN ref (nothing before it) -> False
    have = alt[['time']].merge(base[['time', 'volume']], on='time', how='left').volume.values
    more = ~np.isnan(have) & (alt.volume.values > have * MATERIAL[0] + MATERIAL[1])
    new = np.isnan(have) & sane; better = more & sane
    take = alt[new | better].assign(src=name)
    out = pd.concat([base[~base.time.isin(take.time)], take]).sort_values('time').reset_index(drop=True)
    return out, int(new.sum()), int(better.sum()), int(((np.isnan(have) | more) & ~sane).sum()), len(skip)


def align_rolls(base, basis, store):
    """Shift the bars after every change of contract onto `basis` (the frozen history's contract): by the new contract's close minus
    the old one's at the last minute both traded before the change (NinjaTrader's back-adjust offset). Returns (base, notes)."""
    c = base.contract.values; prev = np.r_[[basis], c[:-1]]; shift = np.zeros(len(base)); notes = []; tot = 0.0
    for i in np.flatnonzero(c != prev):
        a, b, t = prev[i], c[i], base.time.iloc[i]
        if a not in store or b not in store: raise RuntimeError(f"contract change {a} -> {b} at {t}: no minute-store data for both - offset unknown")
        j = store[a][1].merge(store[b][1], on='time', suffixes=('_a', '_b')); j = j[j.time < t]
        if not len(j): raise RuntimeError(f"contract change {a} -> {b} at {t}: the two contracts never traded in the same minute before it - offset unknown")
        off = float(j.close_b.iloc[-1] - j.close_a.iloc[-1]); tot += off; shift[i:] = tot
        notes.append(f"contract change {a} -> {b} at {t:%Y-%m-%d %H:%M}: offset {off:+.2f} pt (both at {j.time.iloc[-1]:%Y-%m-%d %H:%M}); "
                     f"later bars shifted by {-tot:+.2f} pt onto the {basis} basis")
    out = base.copy()
    for k in PX: out[k] = out[k].values - shift
    return out, notes


def extended_bars(account, frozen=None, recorder_root=RECORDER, minutes_root=NT_MINUTES):
    """(bars on the frozen basis, raw bars after the frozen end with their contract, source per calendar date, raw 09:30 open per
    date, notes) - see the module text for the order of the sources."""
    frozen = read_bars(FROZEN_BARS) if frozen is None else frozen
    cut = frozen.time.max(); notes = []; tail = frozen[frozen.time > cut - pd.Timedelta(days=4)]
    if not os.path.exists(archive_path(account)): raise FileNotFoundError(f"bars archive missing: {archive_path(account)}")
    arc = read_bars(archive_path(account))[COLS]
    base = arc[arc.time > cut].drop_duplicates('time', keep='first').assign(src='archive').reset_index(drop=True)
    def note(name, r):
        f, x, bad, sk = r
        if f or x or bad or sk:
            notes.append(f"{name}: {f} bar(s) filled, {x} partial bar(s) replaced" + (f", {bad} refused (open > {LEVEL:.0%} from the close before)" if bad else '')
                         + (f", skipped on {sk} day(s) where its prices differ from the archive's" if sk else ''))
    bf = bars_file_path(account)
    if os.path.exists(bf):
        live = read_bars(bf, 'time_et')[COLS].drop_duplicates('time', keep='last')
        base, *r = patch(base, live[live.time > cut], 'bars_file', tail); note('bars_file', r)
    else: notes.append(f"no strategy bars file {os.path.basename(bf)}")
    store = contract_store(minutes_root, cut)
    hits = tag(tail, store, fill=False) if store else None                  # the frozen end's contract = the basis
    basis = hits.dropna().mode().iloc[0] if hits is not None and hits.notna().any() else None
    if basis is None:
        notes.append("minute store has no bars matching the frozen end - contract changes cannot be detected (assumed none)")
        base['contract'] = 'frozen'
    else:
        base['contract'] = tag(base, store, basis)
        allb = pd.concat([b5 for b5, _ in store.values()])
        base, *r = patch(base, allb[allb.time > cut], 'nt_minutes', tail); note('nt_minutes', r)
        base['contract'] = base.contract.ffill().bfill().fillna(basis)
    rec = recorder_bars(recorder_root)
    if rec is not None:
        if basis is None: rec = rec.drop(columns='contract')
        base, *r = patch(base, rec[rec.time > cut], 'recorder', tail); note('recorder', r)
        base['contract'] = base.contract.ffill().bfill().fillna(basis or 'frozen')
    if len(base):                                  # the newest day stays out until its session is complete
        last = base.time.dt.date.max(); cal = cme_sessions(last, last)
        if last in cal and not session_complete(base[base.time.dt.date == last], cal[last]):
            base = base[base.time.dt.date != last].reset_index(drop=True); notes.append(f"{last} still in progress - added once its session is complete")
    raw = base.copy()
    m = raw.time.dt.hour * 60 + raw.time.dt.minute
    raw_open = {d: float(g.open.iloc[0]) for d, g in raw[(m > 570) & (m <= 960)].groupby(raw.time.dt.date)}
    if basis is not None: base, rn = align_rolls(base, basis, store); notes += rn
    src = {d: '+'.join(k if k == g.src.mode()[0] else f"{k} {n}" for k, n in g.src.value_counts().items())
           for d, g in base.groupby(base.time.dt.date)}
    bars = pd.concat([frozen[COLS], base[COLS]]).sort_values('time', kind='stable').reset_index(drop=True)
    return bars, raw, src, raw_open, notes


def write_atomic_csv(df, path, **kw):
    df.to_csv(path + '.tmp', **kw); os.replace(path + '.tmp', path)


def compare_bars(a, b, label, on=('time',)):
    """OHLC agreement of two bar sets on their common keys (RTH 09:35-16:00 and all hours)."""
    on = list(on); j = a[COLS + on[1:]].merge(b[COLS + on[1:]], on=on, suffixes=('_a', '_b'))
    if not len(j): return f"{label}: no common bars", j
    m = j.time.dt.hour * 60 + j.time.dt.minute; rth = ((m > 570) & (m <= 960)).values
    same = np.all([j[f'{c}_a'].values == j[f'{c}_b'].values for c in PX], axis=0)
    dif = max(float((j[f'{c}_a'] - j[f'{c}_b']).abs().max()) for c in PX)
    vol = float(np.mean(j.volume_a.values == j.volume_b.values))
    s = (f"{label}: {len(j):,} common bars, OHLC identical {same.mean() * 100:.2f}% (RTH {same[rth].mean() * 100 if rth.any() else float('nan'):.2f}% "
         f"of {int(rth.sum()):,}), largest price difference {dif:g} pt, volume identical {vol * 100:.1f}%")
    return s, j


def worst_rows(mk, raw_open=None):
    """intraday_worst.build's MNQ rows for a market (date > nav_lab.JOIN, after the warm-up); a date missing from raw_open930.csv is
    priced at raw_open[date] when given, else at the session's first open (as build)."""
    import intraday_worst as IW
    from research_engine import WARMUP
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * 1 + 0.25) / p_ref; raw_open = raw_open or {}
    p, w, n = IW.day_close_and_worst(mk); d = mk.dates
    sel = (np.arange(len(d)) >= WARMUP) & (d > N.JOIN)
    o = np.array([raw_open.get(x.date, x.O[0]) for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
    s = pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c,
                      'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c, 'nt': n[sel]}, index=d[sel])
    s.index.name = 'date'
    return s, pd.Series(p, index=d)


def market_from(bars, name):
    from research_engine import Market
    buf = io.StringIO(); bars[COLS].to_csv(buf, index=False, date_format='%Y-%m-%d %H:%M:%S'); text = buf.getvalue()
    return Market(io.StringIO(text), 1.0, name), text


def run(account='Sim101', write=True, log=print):
    lines = []
    def say(s): lines.append(s); log(s)
    frozen_bars = read_bars(FROZEN_BARS)
    bars, raw, src, raw_open, notes = extended_bars(account, frozen_bars)
    cut = frozen_bars.time.max()
    # 1. the extended series; the frozen rows must reproduce intraday_worst.csv exactly
    mk, text = market_from(bars, 'MNQX'); N._MK['MNQX'] = mk
    mnq, pts = worst_rows(mk, raw_open)
    frozen = pd.read_csv(FROZEN_WORST, index_col=0, parse_dates=True)
    fm = frozen[frozen.index > N.JOIN]; old = mnq[mnq.index <= cut]
    if not old.index.equals(fm.index):
        raise AssertionError(f"extended series: {len(old)} sessions to {cut.date()} vs {len(fm)} in intraday_worst.csv - the frozen part changed")
    diff = float(np.abs(old[['r_close', 'r_min', 'nt']].values - fm[['r_close', 'r_min', 'nt']].values).max())
    if diff > 1e-12: raise AssertionError(f"extended series: frozen sessions differ from intraday_worst.csv by {diff:.3e}")
    new = mnq[mnq.index > frozen.index.max()].copy(); new['source'] = [src.get(d.date(), '?') for d in new.index]
    ext = pd.concat([frozen.assign(source='history'), new])
    say(f"history extended: {len(frozen):,} frozen sessions ({frozen.index[0].date()} .. {frozen.index[-1].date()}, reproduced: max diff {diff:.1e}) "
        f"+ {len(new)} recorded session(s)" + (f" {new.index[0].date()} .. {new.index[-1].date()}" if len(new) else ''))
    if len(new):
        say("   recorded sessions ($ per micro at MNQ 29,150, after 1 tick + $0.50 a side): " + ', '.join(
            f"{d:%m-%d} {r.r_close * 58300:+,.0f}{'' if r.source == 'archive' else f' ({r.source})'}" for d, r in new.iterrows()))
    # 2. every CME session after the frozen end is in the history with all its day-session bars (09:35 .. the close)
    have = {x.date: x.nb for x in mk.days}
    last_day = max(have) if have else cut.date()
    cal = cme_sessions(cut.date() + dt.timedelta(days=1), last_day)
    missing = [d for d in cal if d not in have]
    short = {d: (have[d], (e - 575) // 5 + 1) for d, e in cal.items() if d in have and have[d] < (e - 575) // 5 + 1}
    fresh = [d for d in missing + [d for d, (n, e) in short.items() if e - n > MAX_HOLE] if (last_day - d).days <= FRESH_DAYS]
    say(f"   CME sessions {cut.date() + dt.timedelta(days=1)} .. {last_day}: {len(cal)}, complete in the history {len(cal) - len(missing) - len(short)}"
        + (f"; MISSING {', '.join(str(d) for d in missing)}" if missing else '')
        + (f"; INCOMPLETE {', '.join(f'{d} ({n} of {e} bars)' for d, (n, e) in short.items())}" if short else '')
        + ('' if missing or short else ', none missing'))
    for s in notes: say('   ' + s)
    # 3. archive vs frozen on their overlap: bars, then each session's result from the archive alone
    arc = read_bars(archive_path(account))[COLS].drop_duplicates('time', keep='first')
    ov = arc[arc.time <= cut]
    if len(ov):
        fz = frozen_bars[frozen_bars.time >= ov.time.min()]
        s, _ = compare_bars(ov, fz, f"   archive vs frozen {ov.time.min().date()} .. {ov.time.max().date()}")
        say(s + f"; bars only in the frozen file {len(set(fz.time) - set(ov.time)):,}, only in the archive {len(set(ov.time) - set(fz.time)):,}")
        import intraday_worst as IW
        ma, _ = market_from(ov, 'ARCH')
        pa = pd.Series(IW.day_close_and_worst(ma)[0], index=ma.dates).iloc[30:].dropna()
        both = pa.index.intersection(pts.dropna().index)
        if len(both):
            d_pts = (pa[both] - pts[both]).abs()
            say(f"   sessions recomputed from the archive alone: {len(both)} (after its own 30-day warm-up), same result {np.mean(d_pts < 1e-9) * 100:.1f}%, "
                f"total |difference| ${d_pts.sum() * 2:,.0f} per micro (frozen total ${pts[both].sum() * 2:,.0f})")
    # 4. NinjaTrader's minute store and the TapeRecorder's live bars vs the bars used (same contract, before any roll shift)
    store = contract_store(NT_MINUTES, cut)
    if store:
        allb = pd.concat([b5 for b5, _ in store.values()])
        say(compare_bars(allb[allb.time > cut], raw, "   NinjaTrader minute store vs the added bars", on=('time', 'contract'))[0])
    else: say("   NinjaTrader minute store: none found")
    rec = recorder_bars()
    if rec is None: say("   TapeRecorder bars: none found")
    else:
        s, j = compare_bars(rec, raw, "   TapeRecorder live bars vs the added bars", on=('time', 'contract'))
        say(s if len(j) else f"   TapeRecorder live bars: {len(rec)} ({rec.time.min()} .. {rec.time.max()}), none among the added bars yet")
    if write:
        with open(EXT_BARS + '.tmp', 'w', encoding='utf-8', newline='') as f: f.write(text)
        os.replace(EXT_BARS + '.tmp', EXT_BARS)
        write_atomic_csv(ext, EXT_WORST)
        with open(STATUS + '.tmp', 'w', encoding='utf-8') as f:
            f.write(f"history_extend.py at {dt.datetime.now():%Y-%m-%d %H:%M:%S} (account {account})\n" + '\n'.join(lines) + '\n')
        os.replace(STATUS + '.tmp', STATUS)
    if fresh: raise RuntimeError(f"CME session(s) missing or incomplete in the history: {', '.join(str(d) for d in fresh)} (gap in the recorded bars)")
    return lines


if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--account', default='Sim101'); ap.add_argument('--dry', action='store_true')
    a = ap.parse_args()
    run(a.account, write=not a.dry)
