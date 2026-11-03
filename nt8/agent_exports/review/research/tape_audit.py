r"""Integrity audit of the TapeRecorder's tape (D:\nt_recorder\<instrument>\<UTC date>_tape.csv): one block per day and a summary.
    python tape_audit.py                 -> the newest two tape files
    python tape_audit.py 2026-11-02 ...  -> those UTC days
    python tape_audit.py --all           -> every tape file (~2 min per 3.5 GB day)
Writes research\TAPE_AUDIT_latest.txt. Streams each file with pyarrow (a 4 GB day in ~2 minutes, little memory).

Per day: rows by kind; feed->receipt lag (feed_utc is the provider's stamp, recv_utc this PC's clock: the lag IS the feed's lateness -
10-26 22.7 s, 10-27 17.8 s, 10-28 142 s at 14:43 before the FOMC-day restart); receipt gaps over 60 s classified as the 17:00-18:00 ET
halt, the weekend, or a HOLE; crossed quotes; prices off the 0.25 grid; trades printing away from the quote; receipt stamps going
backwards; the tape's trade volume per 5-minute bar against the recorder's own bar file (NinjaTrader's live bar: equal when every print
was captured). Rows identical to the previous one at the same feed millisecond are counted but are REAL separate 1-lot fills of a sweep
(2026-11-03: 30% of trades; with them the tape's volume equals NinjaTrader's bar volume exactly, without them it is 25% short).
First audit 2026-11-03 over 10 days / 24 GB / 289 M rows: 0 off-grid prices, 0 trades away from the quote, 0 stamps backwards,
0.002% crossed quotes; holes only where NinjaTrader was not running (10-23 06:24-06:41 and 10:19-10:20, 10-28 14:43-18:23,
10-29 12:08-12:10, plus the Sunday starts 10-25 19:14 and 11-01 20:03). The first file (10-22, 17:45-20:00 ET, build 2026-10-22a)
has every line twice (tape volume = 2 x NinjaTrader's); flagged below, halve it or skip it."""
import glob, os, sys, time
import numpy as np, pandas as pd
import pyarrow as pa, pyarrow.csv as pc

ROOT = r"D:\nt_recorder"
TICK = 0.25
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "TAPE_AUDIT_latest.txt")
out = []
def pr(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); out.append(s)
def et(t): return t.tz_localize("UTC").tz_convert("America/New_York").strftime("%a %m-%d %H:%M:%S")
def open_minutes(a, b):
    """Minutes between two UTC instants during which the market is open: not Fri 17:00 -> Sun 18:00 ET, not the 17:00-18:00 ET halt."""
    ae = a.tz_localize("UTC").tz_convert("America/New_York").tz_localize(None); be = b.tz_localize("UTC").tz_convert("America/New_York").tz_localize(None)
    if (be - ae).total_seconds() < 60: return 0.0
    secs = pd.date_range(ae.ceil("s"), be.floor("s"), freq="s", inclusive="left")
    if not len(secs): return 0.0
    wd = secs.weekday; h = secs.hour
    closed = (h == 17) | (wd == 5) | ((wd == 4) & (h >= 17)) | ((wd == 6) & (h < 18))
    return float((~closed).sum()) / 60.0
def expected_gap(a, b):
    """True when the gap holds less than a minute of open market (the 17:00-18:00 ET halt, the weekend)."""
    return open_minutes(a, b) < 1.0


def audit(f):
    t0 = time.time(); day = os.path.basename(f)[:10]; folder = os.path.dirname(f)
    n = {}; lat_samp = []; recv_neg = 0; crossed = 0; noquote = 0; offgrid = 0; away = 0; jumps = 0; same_ms = 0; zsize = 0; gaps = []
    first = last = None; last_recv = None; last_px = None; last_key = None; maxlat = 0.0; lat_over_1s = 0; total = 0; spread_samp = []
    vol = {}
    conv = pc.ConvertOptions(column_types={"kind": pa.string(), "feed_utc": pa.string(), "recv_utc": pa.string(), "price": pa.float64(),
                                           "size": pa.float64(), "bid": pa.float64(), "ask": pa.float64()})
    for batch in pc.open_csv(f, read_options=pc.ReadOptions(block_size=64 << 20), convert_options=conv):
        d = batch.to_pandas()
        if d.empty: continue
        total += len(d)
        for k, c in d["kind"].value_counts().items(): n[k] = n.get(k, 0) + int(c)
        feed = pd.to_datetime(d["feed_utc"], format="%Y-%m-%d %H:%M:%S.%f", errors="coerce")
        recv = pd.to_datetime(d["recv_utc"], format="%Y-%m-%d %H:%M:%S.%f", errors="coerce")
        if first is None: first = recv.iloc[0]
        last = recv.iloc[-1]
        lat = (recv - feed).dt.total_seconds().values; lat = lat[np.isfinite(lat)]
        if len(lat): maxlat = max(maxlat, float(lat.max())); lat_over_1s += int((lat > 1.0).sum()); lat_samp.append(lat[::50])
        rv = recv.values.astype("datetime64[ns]").astype("int64") / 1e9
        if last_recv is not None: rv = np.concatenate([[last_recv], rv])
        dr = np.diff(rv); recv_neg += int((dr < -0.001).sum())
        for i in np.where(dr > 60)[0]: gaps.append((pd.Timestamp(rv[i], unit="s"), pd.Timestamp(rv[i + 1], unit="s"), dr[i]))
        last_recv = rv[-1]
        q = d[d["bid"].notna() & d["ask"].notna()]
        noquote += int(len(d) - len(q)); crossed += int((q["bid"] > q["ask"]).sum()); spread_samp.append(((q["ask"] - q["bid"]) / TICK).values[::50])
        px = d["price"].values; okp = np.isfinite(px)
        offgrid += int((np.abs(px[okp] / TICK - np.round(px[okp] / TICK)) > 1e-6).sum())
        tr = d[d["kind"] == "T"]
        zsize += int((tr["size"] <= 0).sum())
        tq = tr[tr["bid"].notna() & tr["ask"].notna()]
        away += int(((tq["price"] < tq["bid"] - 2 * TICK) | (tq["price"] > tq["ask"] + 2 * TICK)).sum())
        tp = tr["price"].values
        if len(tp):
            tp2 = np.concatenate([[last_px], tp]) if last_px is not None else tp
            jumps += int((np.abs(np.diff(tp2)) > 20 * TICK).sum()); last_px = tp[-1]
            tet = feed[tr.index].dt.tz_localize("UTC").dt.tz_convert("America/New_York").dt.tz_localize(None)
            bar_end = tet.dt.floor("5min") + pd.Timedelta(minutes=5)
            for k, v in tr.groupby(bar_end.values)["size"].sum().items(): vol[k] = vol.get(k, 0.0) + float(v)
        key = d["kind"] + "|" + d["feed_utc"] + "|" + d["price"].astype(str) + "|" + d["size"].astype(str) + "|" + d["bid"].astype(str) + "|" + d["ask"].astype(str)
        kv = key.values
        if last_key is not None: kv = np.concatenate([[last_key], kv])
        same_ms += int((kv[1:] == kv[:-1]).sum()); last_key = key.values[-1]
    lat_all = np.concatenate(lat_samp) if lat_samp else np.array([0.0]); sp = np.concatenate(spread_samp) if spread_samp else np.array([np.nan])
    nt = n.get("T", 0)
    pr(f"\n{day}  {os.path.getsize(f) / 1e9:.2f} GB  {total:,} rows (trades {nt:,}, asks {n.get('A', 0):,}, bids {n.get('B', 0):,})  {time.time() - t0:.0f} s")
    pr(f"   receipt span {et(first)} -> {et(last)} ET")
    pr(f"   feed->receipt lag: median {np.median(lat_all) * 1000:.0f} ms, p99 {np.percentile(lat_all, 99) * 1000:.0f} ms, max {maxlat:.2f} s, rows over 1 s {lat_over_1s:,} ({lat_over_1s / max(total, 1) * 100:.3f}%)")
    pr(f"   quote spread (ticks): median {np.nanmedian(sp):.2f}, p99 {np.nanpercentile(sp, 99):.1f}; crossed bid>ask {crossed:,} ({crossed / max(total - nt, 1) * 100:.4f}%); rows without a quote {noquote:,}")
    pr(f"   prices off the 0.25 grid {offgrid:,}; trades > 2 ticks outside the quote {away:,}; trade-to-trade jumps > 20 ticks {jumps:,}; zero-size trades {zsize:,}; "
       f"receipt stamps backwards {recv_neg:,}; same-millisecond identical prints {same_ms:,} (real separate fills)")
    holes = [(a, b, s) for a, b, s in gaps if not expected_gap(a, b)]
    pr(f"   receipt gaps > 60 s: {len(gaps)} ({len(gaps) - len(holes)} halt / weekend); HOLES {len(holes)}" +
       (": " + "; ".join(f"{et(a)} -> {et(b)} ({open_minutes(a, b):.0f} min of open market)" for a, b, s in holes) if holes else ""))
    if nt and same_ms > 0.4 * total:
        pr(f"   !! {same_ms / total * 100:.0f}% of the rows repeat the previous row: every line written twice (the 2026-10-22a build, fixed in 2026-10-22b) - "
           f"halve the volume or drop every second identical row when using this day")
    # tape volume per 5-minute bar vs the recorder's own bar file (NinjaTrader's live bar), first and last bar of the file excluded
    bf = os.path.join(folder, f"{day}_bars5min.csv")
    if not os.path.exists(bf): bf = os.path.join(folder, f"{day}_bars5minute.csv")
    vmatch = None
    if os.path.exists(bf) and vol:
        b = pd.read_csv(bf); b["time"] = pd.to_datetime(b["time_et"]); bv = b.set_index("time")["volume"]
        keys = sorted(vol)[1:-1]; common = [k for k in keys if k in bv.index]
        if common:
            diff = np.array([vol[k] - bv[k] for k in common]); vmatch = float(np.mean(diff == 0))
            pr(f"   tape volume per 5-min bar vs NinjaTrader's live bar: {len(common)} bars, equal on {vmatch * 100:.1f}%, max |diff| {np.abs(diff).max():.0f} contracts "
               f"(tape {sum(vol[k] for k in common):,.0f} vs bars {sum(bv[k] for k in common):,.0f})")
    return dict(day=day, gb=os.path.getsize(f) / 1e9, rows=total, trades=nt, lat_med_ms=np.median(lat_all) * 1000, lat_max_s=maxlat, crossed=crossed,
                offgrid=offgrid, away=away, recv_neg=recv_neg, holes=len(holes), vol_equal_pct=None if vmatch is None else vmatch * 100), first, last


def last_recv(path):
    """Receipt stamp of a tape file's last complete line (reads its tail only)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - 4096)); tail = fh.read().split(b"\n")
            for line in reversed(tail):
                p = line.rstrip(b"\r").split(b",")
                if len(p) >= 7 and p[0] in (b"A", b"B", b"T"): return pd.Timestamp(p[2].decode())
    except Exception: pass
    return None


all_files = []
def main(argv):
    global all_files
    files = sorted(glob.glob(os.path.join(ROOT, "*", "*_tape.csv")))
    files = [f for f in files if os.path.basename(os.path.dirname(f)) not in ("parquet", "_to_delete", "accounts")]
    all_files = list(files)
    if argv and argv[0] != "--all": files = [f for f in files if os.path.basename(f)[:10] in set(argv)]
    elif not argv: files = files[-2:]
    if not files: pr("no tape files"); return 1
    pr(f"TAPE AUDIT {pd.Timestamp.now():%Y-%m-%d %H:%M} - {len(files)} file(s) under {ROOT}")
    rows = []
    for f in files:
        r, first, last = audit(f)
        prev = [p for p in all_files if os.path.dirname(p) == os.path.dirname(f) and p < f]
        prev_last = last_recv(prev[-1]) if prev else None           # the file before this one in the same folder, audited or not
        if prev_last is not None:
            gap = (first - prev_last).total_seconds(); om = open_minutes(prev_last, first)
            pr(f"   continuity from {os.path.basename(prev[-1])[:10]}: {gap:.3f} s" + ("" if om < 1 else f"  <- HOLE between files: {om:.0f} min of open market ({et(prev_last)} -> {et(first)})"))
            if om >= 1: r["holes"] += 1
        rows.append(r)
    S = pd.DataFrame(rows)
    pr("\nSUMMARY"); pr(S.to_string(index=False, float_format=lambda x: f"{x:,.2f}"))
    pr(f"TOTAL {S.gb.sum():.1f} GB, {S.rows.sum():,} rows, {S.trades.sum():,} trades over {len(S)} day(s); off-grid {S.offgrid.sum():,}, away from the quote {S.away.sum():,}, "
       f"stamps backwards {S.recv_neg.sum():,}, crossed {S.crossed.sum():,}, holes {S.holes.sum():,}")
    verdict = "CLEAN" if (S.offgrid.sum() == 0 and S.away.sum() == 0 and S.recv_neg.sum() == 0 and S.holes.sum() == 0) else "CHECK the lines above"
    pr(f"VERDICT: {verdict}")
    open(OUT, "w", encoding="utf-8").write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
