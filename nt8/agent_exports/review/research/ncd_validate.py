"""Validate the decoded NinjaTrader tick/minute data against independent sources.

Usage (from this folder, after ncd_export.py has run):
    python ncd_validate.py            # all checks -> NCD_READER_RESULTS.txt (+ ncd_validate_*.csv)
    python ncd_validate.py --quick    # a few sessions only (smoke test)

Checks
  (a) every Jan-Apr 2026 session vs Databento trades (MNQH6 / MNQM6), trade by trade:
      counts, volumes, run-by-run price/volume sequence alignment, side, latency,
      timestamp resolution, plus hour-level detail for selected hours.
  (b) 5-minute OHLCV built from the decoded ticks (and from the decoded minute files)
      vs NoiseArea_bars_MNQ_strategy_Sim101.csv (NinjaTrader's own bars).
  (c) every 09:59-10:05 ET Databento window 2025-10..2026-10 vs the decoded ticks.
  (d) decoded minute files vs 1-minute bars rebuilt from the decoded ticks (all overlap days).
Inputs: D:\\nt_ticks\\MNQ\\<contract>\\*.parquet (ncd_export.py), D:\\nt_ticks\\minute\\MNQ\\*.parquet,
        D:\\databento_archive\\..., agent_exports\\NoiseArea_bars_MNQ_strategy_Sim101.csv.
The Databento .dbn.zst files are converted once to D:\\nt_ticks\\_work\\dbn\\*.parquet (cache).
"""
import sys
sys.dont_write_bytecode = True

import argparse
import glob
import os
import time

import numpy as np
import pandas as pd
import pyarrow.dataset as ds
import pyarrow.parquet as pq

HERE = os.path.dirname(os.path.abspath(__file__))
TICKS = r"D:\nt_ticks\MNQ"
MINUTE = r"D:\nt_ticks\minute\MNQ"
DBN_A = r"D:\databento_archive\mnq_trades_2026-01-01_2026-04-16.dbn.zst"
DBN_B = r"D:\databento_archive\mnq_trades_2026-04-10_2026-04-17.dbn.zst"
CACHE = r"D:\nt_ticks\_work\dbn"
WINDOWS = r"D:\databento_archive\mnq_trades_1000_windows_2019-2026"
NT_BARS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "NoiseArea_bars_MNQ_strategy_Sim101.csv")
RESULTS = os.path.join(HERE, "NCD_READER_RESULTS.txt")
ET = "America/New_York"
IID_H6, IID_M6 = 42004946, 42004936
DETAIL_HOURS = ["2026-01-13 16:00", "2026-01-14 03:00", "2026-01-14 09:00", "2026-02-05 10:00",
                "2026-03-04 14:00", "2026-03-24 09:00", "2026-04-08 15:00", "2026-04-14 09:00"]


# ----------------------------------------------------------------------------- data access
def cache_databento():
    """Convert the Databento trade files to parquet once (pure read of the archive)."""
    import databento as db
    import pyarrow as pa
    os.makedirs(CACHE, exist_ok=True)
    for src, tag in ((DBN_A, "a"), (DBN_B, "b")):
        out = os.path.join(CACHE, f"mnq_{tag}.parquet")
        if os.path.exists(out):
            continue
        store = db.DBNStore.from_file(src)
        writer = None
        for df in store.to_df(count=4_000_000, price_type="float", pretty_ts=False, map_symbols=False):
            df = df.reset_index()
            t = pa.Table.from_pandas(pd.DataFrame({
                "ts_event": df["ts_event"].astype("int64"), "ts_recv": df["ts_recv"].astype("int64"),
                "instrument_id": df["instrument_id"].astype("uint32"), "price": df["price"].astype("float64"),
                "size": df["size"].astype("uint32"), "side": df["side"].astype(str),
                "flags": df["flags"].astype("uint8"), "sequence": df["sequence"].astype("uint32")}), preserve_index=False)
            writer = writer or pq.ParquetWriter(out + ".tmp", t.schema)
            writer.write_table(t)
        writer.close()
        os.replace(out + ".tmp", out)


def session_bounds_utc(date):
    d = pd.Timestamp(date)
    s = (d - pd.Timedelta(hours=6)).tz_localize(ET).tz_convert("UTC")
    e = (d + pd.Timedelta(hours=17)).tz_localize(ET).tz_convert("UTC")
    return s, e


def load_db(src, iid, s, e):
    t = ds.dataset(src).to_table(filter=(ds.field("ts_event") >= s.value) & (ds.field("ts_event") < e.value)
                                 & (ds.field("instrument_id") == iid))
    return t.to_pandas()


def load_nt(contract, date):
    p = os.path.join(TICKS, contract, pd.Timestamp(date).strftime("%Y-%m-%d") + ".parquet")
    return pd.read_parquet(p) if os.path.exists(p) else None


# ----------------------------------------------------------------------------- run alignment
def runs(price, vol):
    """Collapse consecutive prints at the same price -> (key, start index, run volume)."""
    p = np.round(np.asarray(price, float) * 4).astype(np.int64)
    v = np.asarray(vol, np.int64)
    if len(p) == 0:
        return np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64)
    brk = np.flatnonzero(np.r_[True, p[1:] != p[:-1]])
    rv = np.add.reduceat(v, brk)
    return p[brk] * 10**9 + rv, brk, rv


def _win_hash(key, W):
    n = len(key) - W + 1
    if n <= 0:
        return np.zeros(0, np.uint64)
    k = key.astype(np.uint64)
    h = np.zeros(n, np.uint64)
    with np.errstate(over="ignore"):
        for t in range(W):
            h = h * np.uint64(1000003) + k[t:t + n]
    return h


def align(A, B, W=10, S0=2000):
    """Greedy alignment of two run-key sequences. Returns exact blocks [(i, j, L)]."""
    HA, HB = _win_hash(A, W), _win_hash(B, W)
    i = j = 0
    nA, nB = len(A), len(B)
    blocks = []
    while i < nA and j < nB:
        L, c = 0, 512
        while i + L < nA and j + L < nB:
            m = min(c, nA - i - L, nB - j - L)
            eq = A[i + L:i + L + m] == B[j + L:j + L + m]
            if eq.all():
                L += m
                c *= 4
                continue
            L += int(np.argmin(eq))
            break
        if L:
            blocks.append((i, j, L))
            i += L
            j += L
        if i >= nA or j >= nB:
            break
        S = S0
        found = None
        while found is None:
            ha, hb = HA[i:i + S], HB[j:j + S]
            if len(ha) == 0 or len(hb) == 0:
                break
            _, ia, ib = np.intersect1d(ha, hb, assume_unique=False, return_indices=True)
            if len(ia):
                order = np.argsort(ia + ib, kind="stable")
                for k in order[:50]:
                    a, b = int(ia[k]), int(ib[k])
                    if (A[i + a:i + a + W] == B[j + b:j + b + W]).all():
                        found = (a, b)
                        break
            if found is None:
                if S >= max(nA - i, nB - j):
                    break
                S *= 4
        if found is None:
            break
        i += found[0]
        j += found[1]
    return blocks


def matched_mask(n, blocks, side):
    m = np.zeros(n, bool)
    for blk in blocks:
        s = blk[side]
        m[s:s + blk[2]] = True
    return m


# ----------------------------------------------------------------------------- (a) Databento sessions
def compare_session(nt, db):
    """Trade-by-trade comparison of one session. Returns (stats dict, per-run frames for hour detail)."""
    kn, bn, vn = runs(nt.price.values, nt.volume.values)
    kd, bd, vd = runs(db.price.values, db["size"].values)
    blocks = align(kn, kd)
    mn = matched_mask(len(kn), blocks, 0)
    md = matched_mask(len(kd), blocks, 1)
    nt_vol, db_vol = int(nt.volume.sum()), int(db["size"].sum())
    st = dict(db_trades=len(db), db_vol=db_vol, nt_prints=len(nt), nt_vol=nt_vol,
              vol_diff=nt_vol - db_vol, prints_per_trade=round(len(nt) / max(len(db), 1), 3),
              runs_db=len(kd), runs_nt=len(kn),
              match_vol_db=round(100 * vd[md].sum() / max(db_vol, 1), 4),
              match_vol_nt=round(100 * vn[mn].sum() / max(nt_vol, 1), 4),
              gaps=max(len(blocks) - 1, 0),
              db_only_vol=int(vd[~md].sum()), nt_only_vol=int(vn[~mn].sum()),
              nt_sub_ms=int(nt.sub_ms.sum()))
    # per-print unmatched mask -> are the artifact-flagged prints NT-only?
    run_id = np.repeat(np.arange(len(kn)), np.diff(np.r_[bn, len(nt)]))
    unmatched_print = ~mn[run_id]
    art = nt.artifact.values if "artifact" in nt else np.zeros(len(nt), bool)
    st["nt_artifact"] = int(art.sum())
    st["nt_artifact_unmatched"] = int((art & unmatched_print).sum())
    # per-print arrays
    nt_ns = nt.time.values.astype("datetime64[ns]").astype(np.int64)
    db_ns = db.ts_event.values.astype(np.int64)
    db_recv = db.ts_recv.values.astype(np.int64)
    nt_ask = (nt.side.astype(str).values == "A")
    db_buy = (db.side.values == "B")
    st["db_sideN_vol"] = int(db["size"].values[db.side.values == "N"].sum())
    # boundary test, side and latency inside matched blocks
    bnd_tot = bnd_ok = 0
    side_tot = side_ok = 0
    lat, lat_r = [], []
    bn_end = np.r_[bn[1:], len(nt)]
    bd_end = np.r_[bd[1:], len(db)]
    nv, dv = nt.volume.values.astype(np.int64), db["size"].values.astype(np.int64)
    for (i, j, L) in blocks:
        n0, n1 = bn[i], bn_end[i + L - 1]
        d0, d1 = bd[j], bd_end[j + L - 1]
        cn = np.cumsum(nv[n0:n1])
        cd = np.cumsum(dv[d0:d1])
        ok = np.isin(cd, cn)
        bnd_tot += len(cd)
        bnd_ok += int(ok.sum())
        # side per run: NT at-ask volume vs DB buyer-aggressor volume
        rn = np.add.reduceat(nv[n0:n1] * nt_ask[n0:n1], bn[i:i + L] - n0)
        rd = np.add.reduceat(dv[d0:d1] * db_buy[d0:d1], bd[j:j + L] - d0)
        rv = vn[i:i + L]
        side_tot += int(rv.sum())
        side_ok += int(rv[rn == rd].sum())
        lat.append(nt_ns[bn[i:i + L]] - db_ns[bd[j:j + L]])
        lat_r.append(nt_ns[bn[i:i + L]] - db_recv[bd[j:j + L]])
    lat = np.concatenate(lat) / 1e6 if lat else np.zeros(0)
    lat_r = np.concatenate(lat_r) / 1e6 if lat_r else np.zeros(0)
    st["db_bound_in_nt_pct"] = round(100 * bnd_ok / max(bnd_tot, 1), 4)
    st["side_runs_agree_volpct"] = round(100 * side_ok / max(side_tot, 1), 3)
    if len(lat):
        q = np.percentile(lat, [1, 50, 99])
        st.update(lat_p01_ms=round(q[0], 1), lat_med_ms=round(q[1], 1), lat_p99_ms=round(q[2], 1),
                  lat_min_ms=round(lat.min(), 1), lat_recv_med_ms=round(float(np.median(lat_r)), 1))
    st["nt_on_4ms_grid_pct"] = round(100 * float(np.mean(nt_ns % 4_000_000 == 0)), 5) if len(nt) else np.nan
    st["nt_time_backwards"] = int((np.diff(nt_ns) < 0).sum())
    # discrepancy events (unmatched stretches between / around the exact blocks) for the CSV
    gaps = []
    edges = [(0, 0, 0)] + list(blocks) + [(len(kn), len(kd), 0)]
    sub = nt.sub_ms.values
    for prev, blk in zip(edges[:-1], edges[1:]):
        ni = range(prev[0] + prev[2], blk[0])
        dj = range(prev[1] + prev[2], blk[1])
        if len(ni) == 0 and len(dj) == 0:
            continue
        ev = dict(nt_runs=len(ni), db_runs=len(dj), nt_vol=int(vn[ni.start:ni.stop].sum()),
                  db_vol=int(vd[dj.start:dj.stop].sum()), nt_sub_ms_prints=0)
        if len(ni):
            p0, p1 = bn[ni.start], bn_end[ni.stop - 1]
            ev["nt_t0"] = pd.Timestamp(nt_ns[p0], tz="UTC").tz_convert(ET)
            ev["nt_t1"] = pd.Timestamp(nt_ns[p1 - 1], tz="UTC").tz_convert(ET)
            ev["nt_sub_ms_prints"] = int(sub[p0:p1].sum())
        if len(dj):
            q0, q1 = bd[dj.start], bd_end[dj.stop - 1]
            ev["db_t0"] = pd.Timestamp(db_ns[q0], tz="UTC").tz_convert(ET)
            ev["db_t1"] = pd.Timestamp(db_ns[q1 - 1], tz="UTC").tz_convert(ET)
        t_ref = ev.get("db_t0", ev.get("nt_t0"))
        ev["time_et"] = t_ref
        tn = ev.get("nt_t0")
        if ev["db_vol"] >= 50 and ev["nt_vol"] <= 0.1 * ev["db_vol"]:
            kind = "NT hole (prints missing in NinjaTrader)"
        elif ev["nt_vol"] >= 50 and ev["db_vol"] <= 0.1 * ev["nt_vol"]:
            kind = "Databento hole / tape edge"
        elif tn is not None and tn.weekday() == 4 and tn.hour == 15 and tn.minute == 59 and tn.second == 59:
            kind = "Friday 15:59:59.99x end-of-week prints (NT only)"
        elif ev["nt_sub_ms_prints"] > 0:
            kind = "client-stamped (sub-ms) prints"
        elif abs(ev["nt_vol"] - ev["db_vol"]) <= 5:
            kind = "small (<=5 contracts) split/order difference"
        else:
            kind = "other"
        ev["kind"] = kind
        gaps.append(ev)
    # per-run frames for hour detail
    rn_df = pd.DataFrame({"t": pd.to_datetime(nt_ns[bn], utc=True), "vol": vn, "matched": mn, "price": kn // 10**9})
    rd_df = pd.DataFrame({"t": pd.to_datetime(db_ns[bd], utc=True), "vol": vd, "matched": md, "price": kd // 10**9})
    return st, gaps, rn_df, rd_df


def section_a(quick, out):
    cache_databento()
    sessions = []
    for d in pd.bdate_range("2026-01-02", "2026-04-16"):
        if d <= pd.Timestamp("2026-03-13"):
            sessions.append((d, "03-26", IID_H6, "a"))
        elif pd.Timestamp("2026-03-23") <= d <= pd.Timestamp("2026-04-09"):
            sessions.append((d, "06-26", IID_M6, "a"))
        elif d >= pd.Timestamp("2026-04-10"):
            sessions.append((d, "06-26", IID_M6, "b"))
    if quick:
        sessions = [s for s in sessions if s[0] in (pd.Timestamp("2026-01-14"), pd.Timestamp("2026-03-24"))]
    rows, gap_rows, hour_rows = [], [], []
    detail = {pd.Timestamp(h) for h in DETAIL_HOURS}
    for d, contract, iid, tag in sessions:
        nt = load_nt(contract, d)
        if nt is None or len(nt) == 0:
            continue
        s, e = session_bounds_utc(d)
        db = load_db(os.path.join(CACHE, f"mnq_{tag}.parquet"), iid, s, e)
        if len(db) == 0:
            rows.append(dict(date=d.date(), contract=contract, note="no Databento data"))
            continue
        t0 = time.time()
        st, gaps, rn_df, rd_df = compare_session(nt, db)
        st = dict(date=d.date(), contract=contract, **st)
        db_last = pd.to_datetime(db.ts_event.iloc[-1], utc=True)
        db_first = pd.to_datetime(db.ts_event.iloc[0], utc=True)
        st["note"] = ("Databento tape ends early (truncated)" if db_last < e - pd.Timedelta(hours=1) else
                      "Databento file starts after the session open" if db_first > s + pd.Timedelta(hours=1) else "")
        rows.append(st)
        for g in gaps:
            gap_rows.append(dict(date=d.date(), contract=contract, **g))
        # hour detail
        for frame, lab in ((rn_df, "nt"), (rd_df, "db")):
            frame["hour"] = frame.t.dt.tz_convert(ET).dt.floor("h").dt.tz_localize(None)
        for h in sorted(detail):
            if not (s.tz_convert(ET).tz_localize(None) <= h < e.tz_convert(ET).tz_localize(None)):
                continue
            a = rn_df[rn_df.hour == h]
            b = rd_df[rd_df.hour == h]
            hs, he = h.tz_localize(ET).tz_convert("UTC"), (h + pd.Timedelta(hours=1)).tz_localize(ET).tz_convert("UTC")
            ntp = nt[(nt.time >= hs) & (nt.time < he)]
            dbt = db[(db.ts_event >= hs.value) & (db.ts_event < he.value)]
            same_seq = bool((~b.matched).sum() == 0 and (~a.matched).sum() == 0)
            hour_rows.append(dict(hour_et=h, contract=contract, db_trades=len(dbt), nt_prints=len(ntp),
                                  db_vol=int(dbt["size"].sum()), nt_vol=int(ntp.volume.sum()),
                                  db_runs=len(b), nt_runs=len(a), unmatched_db_runs=int((~b.matched).sum()),
                                  unmatched_nt_runs=int((~a.matched).sum()), price_runs_identical=same_seq,
                                  nt_ms_multiple_4=bool((ntp.time.values.astype("datetime64[ns]").astype(np.int64) % 4_000_000 == 0).all())))
        print(f"  (a) {d.date()} {contract}: db {st['db_vol']:,} nt {st['nt_vol']:,} match {st['match_vol_db']}% "
              f"gaps {st['gaps']} ({time.time() - t0:.1f}s)", flush=True)
    A = pd.DataFrame(rows)
    A.to_csv(os.path.join(HERE, "ncd_validate_sessions.csv"), index=False)
    pd.DataFrame(gap_rows).to_csv(os.path.join(HERE, "ncd_validate_gaps.csv"), index=False)
    H = pd.DataFrame(hour_rows)
    return A, pd.DataFrame(gap_rows), H


# ----------------------------------------------------------------------------- (b) NoiseArea 5-min bars
def bars_from_ticks(nt, freq="5min"):
    loc = nt.time.dt.tz_convert(ET).dt.tz_localize(None)
    end = loc.dt.floor(freq) + pd.Timedelta(freq)   # [start, end) by NinjaTrader time; label = bar END
    g = nt.groupby(end.values)
    return pd.DataFrame({"open": g.price.first(), "high": g.price.max(), "low": g.price.min(),
                         "close": g.price.last(), "volume": g.volume.sum()})


def compare_bars(ref, mine):
    j = ref.join(mine, rsuffix="_n", how="inner")
    res = dict(bars=len(j))
    allok = np.ones(len(j), bool)
    for c in ("open", "high", "low", "close", "volume"):
        ok = (j[c].values == j[c + "_n"].values)
        allok &= ok
        res[f"{c}_match_pct"] = round(100 * ok.mean(), 3) if len(j) else np.nan
        res[f"{c}_maxabs"] = float(np.abs(j[c].values - j[c + "_n"].values).max()) if len(j) else np.nan
    res["all_match_pct"] = round(100 * allok.mean(), 3) if len(j) else np.nan
    return res, j[~allok]


def section_b(quick, out):
    csv = pd.read_csv(NT_BARS, parse_dates=["time_et"]).set_index("time_et")
    exported = sorted(glob.glob(os.path.join(TICKS, "12-26", "*.parquet")))
    days = [pd.Timestamp(os.path.basename(p)[:10]) for p in exported]
    days = [d for d in days if d.weekday() < 5]
    if quick:
        days = days[-3:]
    mins = pd.read_parquet(os.path.join(MINUTE, "12-26.parquet")) if os.path.exists(os.path.join(MINUTE, "12-26.parquet")) else None
    per_day, bad = [], []
    tick_all, min_all = [], []
    for d in days:
        nt = load_nt("12-26", d)
        s, e = session_bounds_utc(d)
        lo, hi = s.tz_convert(ET).tz_localize(None), e.tz_convert(ET).tz_localize(None)
        ref = csv[(csv.index > lo) & (csv.index <= hi)]
        if len(ref) == 0 or nt is None:
            continue
        raw_r, _ = compare_bars(ref, bars_from_ticks(nt))
        tb = bars_from_ticks(nt[~(nt.sub_ms | nt.artifact)])
        r, miss = compare_bars(ref, tb)
        r.update(date=d.date(), csv_bars=len(ref), tick_bars=len(tb), raw_all_match_pct=raw_r["all_match_pct"],
                 tick_bars_missing_in_csv=len(tb.index.difference(ref.index)))
        tick_all.append(ref.join(tb, rsuffix="_n", how="inner"))
        if mins is not None:
            mm = mins[(mins.time > s) & (mins.time <= e)].copy()
            loc = mm.time.dt.tz_convert(ET).dt.tz_localize(None)
            end = loc.dt.ceil("5min")
            g = mm.groupby(end.values)
            mb = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                               "close": g.close.last(), "volume": g.volume.sum()})
            r2, _ = compare_bars(ref, mb)
            r["minfile_all_match_pct"] = r2["all_match_pct"]
            min_all.append(ref.join(mb, rsuffix="_n", how="inner"))
        per_day.append(r)
        if len(miss):
            miss = miss.copy()
            miss["date"] = d.date()
            bad.append(miss)
        print(f"  (b) {d.date()}: {r['bars']} bars, all-field exact {r['all_match_pct']}%", flush=True)
    B = pd.DataFrame(per_day)
    B.to_csv(os.path.join(HERE, "ncd_validate_bars5m.csv"), index=False)
    T = pd.concat(tick_all) if tick_all else pd.DataFrame()
    M = pd.concat(min_all) if min_all else pd.DataFrame()
    bad = pd.concat(bad) if bad else pd.DataFrame()
    # back-adjustment probe on older contracts: constant offset CSV - ticks per day
    probe = []
    if not quick:
        for c in ("12-25", "03-26", "06-26", "09-26"):
            files = sorted(glob.glob(os.path.join(TICKS, c, "*.parquet")))
            for p in files[2:-2:5]:
                d = pd.Timestamp(os.path.basename(p)[:10])
                nt = pd.read_parquet(p)
                s, e = session_bounds_utc(d)
                lo, hi = s.tz_convert(ET).tz_localize(None), e.tz_convert(ET).tz_localize(None)
                ref = csv[(csv.index > lo) & (csv.index <= hi)]
                tb = bars_from_ticks(nt)
                j = ref.join(tb, rsuffix="_n", how="inner")
                if len(j) < 50:
                    continue
                off = float(np.median(j.close - j.close_n))
                ok = np.ones(len(j), bool)
                for col in ("open", "high", "low", "close"):
                    ok &= (j[col] - off).values == j[col + "_n"].values
                ok &= j.volume.values == j.volume_n.values
                probe.append(dict(contract=c, date=d.date(), bars=len(j), offset=off, match_after_offset_pct=round(100 * ok.mean(), 2)))
    return B, T, M, bad, pd.DataFrame(probe)


# ----------------------------------------------------------------------------- (c) 10:00 windows
def section_c(quick, out):
    files = sorted(glob.glob(os.path.join(WINDOWS, "chunk_*.parquet")))
    parts = []
    for f in files:
        t = pq.read_table(f, columns=["win", "start_et", "ts", "price", "size", "side", "iid"])
        df = t.to_pandas()
        df = df[df.start_et >= pd.Timestamp("2025-10-26")]
        if len(df):
            parts.append(df)
    W = pd.concat(parts, ignore_index=True)
    rows = []
    wins = sorted(W.win.unique())
    if quick:
        wins = wins[-3:]
    for w in wins:
        x = W[W.win == w].sort_values("ts", kind="stable")
        st_et = x.start_et.iloc[0]
        d = st_et.normalize()
        s = st_et.tz_localize(ET).tz_convert("UTC")
        e = s + pd.Timedelta(minutes=6)
        cands = []
        for c in sorted(os.listdir(TICKS)):
            p = os.path.join(TICKS, c, d.strftime("%Y-%m-%d") + ".parquet")
            if os.path.isdir(os.path.join(TICKS, c)) and os.path.exists(p):
                nt = pd.read_parquet(p)
                nt = nt[(nt.time >= s - pd.Timedelta(seconds=2)) & (nt.time < e + pd.Timedelta(seconds=2))]
                if len(nt):
                    cands.append((abs(nt.price.median() - x.price.median()), c, nt))
        r = dict(window_et=st_et, iid=int(x.iid.iloc[0]), db_trades=len(x), db_vol=int(x["size"].sum()))
        if not cands:
            r["note"] = "no NT tick file for this date"
            rows.append(r)
            continue
        cands.sort(key=lambda z: z[0])
        dist, c, nt = cands[0]
        r["contract"] = c
        if dist > 20:
            r["note"] = f"NT contract differs (price gap {dist:.0f} pts; Databento front month vs NT roll)"
            rows.append(r)
            continue
        kn, bn, vn = runs(nt.price.values, nt.volume.values)
        kd, bd, vd = runs(x.price.values, x["size"].values)
        blocks = align(kn, kd)
        md = matched_mask(len(kd), blocks, 1)
        mn = matched_mask(len(kn), blocks, 0)
        inside = (nt.time >= s) & (nt.time < e)
        r.update(nt_prints_matched=int(np.add.reduceat(np.ones(len(nt), int), bn)[mn].sum()) if len(bn) else 0,
                 nt_vol_in_window_by_nt_time=int(nt.volume[inside].sum()),
                 matched_db_vol=int(vd[md].sum()), match_pct=round(100 * vd[md].sum() / max(r["db_vol"], 1), 3),
                 gaps_inside=max(0, sum(1 for b in blocks if b[2] >= 3) - 1), note="")
        rows.append(r)
    C = pd.DataFrame(rows)
    C.to_csv(os.path.join(HERE, "ncd_validate_windows.csv"), index=False)
    print(f"  (c) {len(C)} windows", flush=True)
    return C


# ----------------------------------------------------------------------------- (d) minute files vs ticks
def section_d(quick, out):
    rows = []
    for c in sorted(os.listdir(TICKS)):
        mp = os.path.join(MINUTE, c + ".parquet")
        if not os.path.isdir(os.path.join(TICKS, c)) or not os.path.exists(mp):
            continue
        mins = pd.read_parquet(mp).set_index("time")
        files = sorted(glob.glob(os.path.join(TICKS, c, "*.parquet")))
        if quick:
            files = files[-2:]
        for p in files:
            nt = pd.read_parquet(p)
            if len(nt) == 0:
                continue
            nt = nt[~(nt.sub_ms | nt.artifact)]
            e1 = nt.time.dt.floor("1min") + pd.Timedelta("1min")
            g = nt.groupby(e1.values)
            tb = pd.DataFrame({"open": g.price.first(), "high": g.price.max(), "low": g.price.min(),
                               "close": g.price.last(), "volume": g.volume.sum()})
            tb.index = pd.DatetimeIndex(tb.index).tz_localize("UTC") if tb.index.tz is None else tb.index
            ref = mins[(mins.index >= tb.index.min()) & (mins.index <= tb.index.max())]
            j = ref.join(tb, rsuffix="_t", how="outer")
            ok = np.ones(len(j), bool)
            for col in ("open", "high", "low", "close", "volume"):
                ok &= (j[col].values == j[col + "_t"].values)
            both = j.open.notna().values & j.open_t.notna().values
            px = np.ones(len(j), bool)
            for col in ("open", "high", "low", "close"):
                px &= (j[col].values == j[col + "_t"].values)
            dv = np.abs(j.volume.values - j.volume_t.values)
            rows.append(dict(contract=c, date=os.path.basename(p)[:10], bars=len(j), exact=int(ok.sum()),
                             only_minute=int(j.open_t.isna().sum()), only_ticks=int(j.open.isna().sum()),
                             both=int(both.sum()), price_diff=int((both & ~px).sum()),
                             volume_only_diff=int((both & px & ~ok).sum()),
                             vol_absdiff_sum=float(np.nansum(dv[both])), vol_sum=float(j.volume.values[both].sum())))
    D = pd.DataFrame(rows)
    D.to_csv(os.path.join(HERE, "ncd_validate_minute_vs_ticks.csv"), index=False)
    print(f"  (d) {len(D)} days", flush=True)
    return D


# ----------------------------------------------------------------------------- report
def fmt_pct(x):
    return f"{x:.4f}%" if pd.notna(x) else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    A, G, H = section_a(a.quick, None)
    B, T, M, bad, probe = section_b(a.quick, None)
    C = section_c(a.quick, None)
    D = section_d(a.quick, None)
    L = []
    w = L.append
    w("NCD READER VALIDATION RESULTS" + ("  (QUICK RUN)" if a.quick else ""))
    w(f"generated {pd.Timestamp.now():%Y-%m-%d %H:%M} by ncd_validate.py; decoder ncd_reader.py; data from ncd_export.py")
    w("")
    w("FORMAT (decoded and confirmed below)")
    w("  28-byte header: int32 1, double tick size, double base price, int64 .NET ticks of the first record in")
    w("  LOCAL US/Eastern wall-clock time. Records: mask1, mask2, then optional big-endian fields: time delta")
    w("  (0/1/2/4/8 bytes of 100 ns ticks or 1 byte of seconds), price delta in ticks (in mask2 low 5 bits,")
    w("  1 byte, or 4 bytes), distance to the opposite quote ('spread', with at-ask/at-bid side flag), volume")
    w("  (1 byte, x100/x500/x1000, 2/4/8 bytes). Minute files: same header; mask-coded time/open/high/low/close/")
    w("  volume deltas, open relative to the previous bar's open. Exact layout in the ncd_reader.py docstring.")
    w("")
    # ---- (a)
    w("(a) TRADE-BY-TRADE vs DATABENTO (every full session, Jan 2 - Apr 16 2026)")
    good = A[A.get("note", "").fillna("") == ""] if "note" in A else A
    trunc = A[A.get("note", "").fillna("") != ""] if "note" in A else A.iloc[0:0]
    w(f"  sessions compared: {len(A)}  (03-26 vs MNQH6 to Mar 13; 06-26 vs MNQM6 from Mar 23; Mar 16-20 skipped:")
    w("  the Databento continuous file still carries MNQH6 there while NinjaTrader had rolled to 06-26)")
    if len(good):
        w(f"  full-tape sessions: {len(good)}; Databento trades {good.db_trades.sum():,} ({good.db_vol.sum():,} contracts);"
          f" NT prints {good.nt_prints.sum():,} ({good.nt_vol.sum():,} contracts)")
        w(f"  volume NT - Databento: total {good.vol_diff.sum():+,} contracts ({100 * good.vol_diff.sum() / good.db_vol.sum():+.4f}%);"
          f" per session median {good.vol_diff.median():+.0f}, min {good.vol_diff.min():+,}, max {good.vol_diff.max():+,}")
        w(f"  volume-weighted share of Databento volume inside exactly matching price/volume run sequences:"
          f" {100 * (good.match_vol_db * good.db_vol).sum() / good.db_vol.sum() / 100:.4f}%  (worst session {good.match_vol_db.min():.3f}%)")
        w(f"  same for NT volume: {100 * (good.match_vol_nt * good.nt_vol).sum() / good.nt_vol.sum() / 100:.4f}%;"
          f" discrepancy events per session: median {good.gaps.median():.0f}, max {good.gaps.max():.0f};"
          f" Databento-only {good.db_only_vol.sum():,} / NT-only {good.nt_only_vol.sum():,} contracts")
        w(f"  sessions with a 100% identical run sequence: {(good.gaps == 0).sum()} of {len(good)}")
        w(f"  NT prints per Databento trade: {good.nt_prints.sum() / good.db_trades.sum():.3f}. Inside matched stretches"
          f" {(good.db_bound_in_nt_pct * good.db_trades).sum() / good.db_trades.sum():.4f}% of Databento trade boundaries are also NT print")
        w("  boundaries -> NinjaTrader does NOT aggregate trades; it keeps every individual fill (a Databento trade of")
        w("  size 8 typically appears as several 1-2 lot NT prints at the same price and time).")
        w(f"  side: NT at-ask volume == Databento buyer-aggressor volume per matched run for"
          f" {(good.side_runs_agree_volpct * good.nt_vol).sum() / good.nt_vol.sum():.3f}% of matched volume")
        w(f"  time: stored as US/Eastern local time (converted to UTC by the reader, DST handled - Jan and Apr agree).")
        w(f"  stored resolution: whole milliseconds from NinjaTrader's history server; {(good.nt_on_4ms_grid_pct * good.nt_prints).sum() / good.nt_prints.sum():.3f}%"
          f" of NT prints sit on a 4 ms grid (sessions <99% on it: {', '.join(str(d) for d in good[good.nt_on_4ms_grid_pct < 99].date) or 'none'}).")
        early = good[pd.to_datetime(good.date) < pd.Timestamp("2026-01-15")]
        late = good[pd.to_datetime(good.date) >= pd.Timestamp("2026-01-15")]
        w(f"  NT time - CME exchange time (ts_event) per matched run: sessions from 2026-01-15 median {late.lat_med_ms.median():+.1f} ms"
          f" (p1 {late.lat_p01_ms.median():+.1f}, p99 {late.lat_p99_ms.median():+.1f} ms);")
        w(f"  Jan 2-14 sessions median {early.lat_med_ms.min():+.1f}..{early.lat_med_ms.max():+.1f} ms. So NT stamps are NOT exchange")
        w("  timestamps: they are NinjaTrader feed times at 4 ms granularity, near the exchange time in calm markets and up")
        w("  to ~100-600 ms late in bursts (session p99). Use them for bars/sequence, not for latency-sensitive work.")
        w(f"  NT time never goes backwards: {int(good.nt_time_backwards.sum())} backward steps.")
        w(f"  NT prints with sub-ms (100 ns) client stamps, i.e. recorded by the local NT client: {good.nt_sub_ms.sum():,}")
        w(f"  prints flagged 'artifact' (Friday 15:59:59.9xx end-of-week rule): {int(good.nt_artifact.sum())}, of which"
          f" {int(good.nt_artifact_unmatched.sum())} are NT-only (absent from Databento)")
    if len(trunc):
        w(f"  sessions excluded from the totals above: {', '.join(f'{r.date} ({r.note})' for r in trunc.itertuples())}")
        for r in trunc.itertuples():
            if pd.notna(getattr(r, "db_vol", np.nan)):
                w(f"    {r.date}: Databento {int(r.db_vol):,} vs NT {int(r.nt_vol):,} contracts; matched {r.match_vol_db:.3f}% of Databento volume")
    if len(G):
        g = G[G.date.astype(str).isin(set(good.date.astype(str)))].copy() if len(good) else G.copy()
        w(f"  discrepancy events in full-tape sessions: {len(g)} (DB-only {g.db_vol.sum():,} / NT-only {g.nt_vol.sum():,} contracts), by kind:")
        for kind, grp in g.groupby("kind"):
            w(f"    {kind}: {len(grp)} events, DB {grp.db_vol.sum():,} / NT {grp.nt_vol.sum():,} contracts")
        holes = g[g.kind.str.startswith("NT hole")].copy()
        if len(holes):
            holes["secs"] = (pd.to_datetime(holes.db_t1) - pd.to_datetime(holes.db_t0)).dt.total_seconds()
            for r in holes.sort_values("db_vol", ascending=False).head(8).itertuples():
                w(f"      hole {pd.Timestamp(r.db_t0):%Y-%m-%d %H:%M:%S} ET, {r.secs:.0f}s: {r.db_vol:,} contracts in Databento, {r.nt_vol} in NT")
    if len(H):
        w("  hour detail (ET hour start; runs = consecutive prints at one price):")
        w("    hour              db_trades nt_prints   db_vol   nt_vol  db_runs nt_runs unmatched(db/nt) all-matched 4ms-grid")
        for r in H.itertuples():
            w(f"    {r.hour_et:%Y-%m-%d %H:%M}  {r.db_trades:9,} {r.nt_prints:9,} {r.db_vol:8,} {r.nt_vol:8,} {r.db_runs:8,} {r.nt_runs:7,}"
              f"   {r.unmatched_db_runs:4}/{r.unmatched_nt_runs:<4}      {str(r.price_runs_identical):5} {r.nt_ms_multiple_4}")
    w("")
    # ---- (b)
    w("(b) 5-MINUTE OHLCV vs NoiseArea_bars_MNQ_strategy_Sim101.csv (NinjaTrader's own bars, MNQ 12-26 sessions)")
    if len(B):
        w(f"  days {len(B)} ({B.date.min()} .. {B.date.max()}), bars compared {B.bars.sum():,}")
        w(f"  (ticks with sub_ms/artifact rows dropped; keeping them: {(B.raw_all_match_pct * B.bars).sum() / B.bars.sum():.3f}% all-field exact)")
        for lab, J in (("ticks -> 5-min", T), ("minute files -> 5-min", M)):
            if len(J) == 0:
                continue
            r, _ = compare_bars(J[["open", "high", "low", "close", "volume"]],
                                J[["open_n", "high_n", "low_n", "close_n", "volume_n"]].rename(columns=lambda c: c[:-2]))
            w(f"  {lab}: all five fields exact {r['all_match_pct']}%; open {r['open_match_pct']}% high {r['high_match_pct']}%"
              f" low {r['low_match_pct']}% close {r['close_match_pct']}% volume {r['volume_match_pct']}%;"
              f" max |diff| O {r['open_maxabs']} H {r['high_maxabs']} L {r['low_maxabs']} C {r['close_maxabs']} V {r['volume_maxabs']:.0f}")
        w(f"  days with every bar exact (ticks): {(B.all_match_pct == 100).sum()} of {len(B)}; 5-min bars present in the ticks but"
          f" missing from the CSV: {int(B.tick_bars_missing_in_csv.sum())} (NinjaTrader's own minute data has holes there)")
        if len(bad):
            w(f"  non-matching bars (ticks): {len(bad)}; first few:")
            for ts_, r in bad.head(5).iterrows():
                w(f"    {ts_}  csv O{r.open} H{r.high} L{r.low} C{r.close} V{r.volume} | ticks O{r.open_n} H{r.high_n} L{r.low_n} C{r.close_n} V{r.volume_n}")
        w("  bar convention: a print at exactly HH:MM:00.000 (NT time) belongs to the bar that STARTS then; label = bar end.")
    if len(probe):
        w("  older contracts: the CSV is a back-adjusted merged series; constant per-day offset (CSV - ticks) and match")
        w("  after removing it:")
        for c, grp in probe.groupby("contract", sort=False):
            w(f"    {c}: {len(grp)} sampled days, offset(s) {sorted(set(grp.offset.round(2)))}, all-field match after offset"
              f" median {grp.match_after_offset_pct.median():.2f}% (min {grp.match_after_offset_pct.min():.2f}%)")
    w("")
    # ---- (c)
    w("(c) 09:59-10:05 ET DATABENTO WINDOWS (2025-10-27 .. 2026-10-12) vs decoded ticks")
    if len(C):
        cmp_ = C[C.note.fillna("") == ""]
        w(f"  windows in range: {len(C)}; compared: {len(cmp_)}; skipped: {len(C) - len(cmp_)}")
        sk = C[C.note.fillna("") != ""]
        for r in sk.itertuples():
            w(f"    skipped {r.window_et:%Y-%m-%d}: {r.note}")
        if len(cmp_):
            w(f"  Databento volume matched run-by-run: {100 * cmp_.matched_db_vol.sum() / cmp_.db_vol.sum():.4f}% of {cmp_.db_vol.sum():,} contracts;"
              f" windows 100% matched: {(cmp_.match_pct == 100).sum()} of {len(cmp_)}; worst {cmp_.match_pct.min():.3f}%")
            worst = cmp_.sort_values("match_pct").head(4)
            for r in worst.itertuples():
                w(f"    {r.window_et:%Y-%m-%d} {r.contract}: matched {r.match_pct}% of {r.db_vol:,}")
    w("")
    # ---- (d)
    w("(d) MINUTE FILES vs 1-MINUTE BARS REBUILT FROM THE TICKS (every overlapping day; sub_ms/artifact rows excluded)")
    if len(D):
        w(f"  days {len(D)}, bars {D.bars.sum():,}, exact OHLCV {100 * D.exact.sum() / D.bars.sum():.4f}%; days fully identical"
          f" {(D.exact == D.bars).sum()} of {len(D)}")
        w(f"  bars in both: {D.both.sum():,}; of these OHLC differs in {D.price_diff.sum():,}, only volume differs in"
          f" {D.volume_only_diff.sum():,}; total |volume diff| {D.vol_absdiff_sum.sum():,.0f} of {D.vol_sum.sum():,.0f} contracts"
          f" ({100 * D.vol_absdiff_sum.sum() / D.vol_sum.sum():.4f}%)")
        w(f"  bars only in the minute file {D.only_minute.sum():,} / only in the ticks {D.only_ticks.sum():,} (download holes in one store;")
        w("  e.g. 06-26 2026-06-11 10:33-11:20 missing in the minute file, 12-26 minute file 2026-10-15 stops at 23:04 ET)")
        worst = D.assign(p=100 * D.exact / D.bars).sort_values("p").head(3)
        for r in worst.itertuples():
            w(f"    lowest: {r.contract} {r.date}: {r.exact}/{r.bars} exact ({r.only_minute} only-minute, {r.only_ticks} only-tick bars)")
    w("")
    w(f"detail CSVs: ncd_validate_sessions.csv, ncd_validate_gaps.csv, ncd_validate_bars5m.csv, ncd_validate_windows.csv,"
      f" ncd_validate_minute_vs_ticks.csv  (runtime {time.time() - t0:.0f}s)")
    text = "\n".join(L) + "\n"
    with open(RESULTS + (".quick.txt" if a.quick else ""), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
