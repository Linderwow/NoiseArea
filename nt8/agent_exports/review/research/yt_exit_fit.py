from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_indicator_sweep as sw
from mama_port import mama

PATHS = Path(r"D:\stream_capture\DaCaptainTrader_\exit_paths.csv")
TOL = 0.5


def candidates(d: pd.DataFrame):
    O, H, L, C = (d[c].to_numpy(float) for c in ("open", "high", "low", "close"))
    med, typ = (H + L) / 2, (H + L + C) / 3
    for t, f in sw.MA_TYPES.items():
        for p in list(range(2, 31)) + [35, 40, 45, 50, 60]:
            if t == "KAMA" and p < 3:
                continue
            yield f"{t}(Low/High,{p})", f(L, p), f(H, p)
            yield f"{t}(Close,{p})", f(C, p), None
            yield f"{t}(Median,{p})", f(med, p), None
            yield f"{t}(Typical,{p})", f(typ, p), None
    for n in range(2, 41):
        yield f"MIN(Low,{n})/MAX(High,{n})", sw.rmin(L, n), sw.rmax(H, n)
    trr = np.maximum(H - L, np.maximum(np.abs(H - sw.shift(C, 1, C[0])), np.abs(L - sw.shift(C, 1, C[0]))))
    for p in (3, 5, 7, 10, 14, 20):
        atr = sw.wilder(trr, p)
        atrs = sw.sma(trr, p)
        for k in (1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0):
            for nm, a in (("ATR", atr), ("ATRsma", atrs)):
                yield f"Close -/+ {k}x{nm}({p})", C - k * a, C + k * a
                for n in (5, 10, 14, 22):
                    yield f"MAX(High,{n}) - {k}x{nm}({p}) / MIN(Low,{n}) +", sw.rmax(H, n) - k * a, sw.rmin(L, n) + k * a
                for q in (5, 10, 20):
                    e = sw.ema(C, q)
                    yield f"EMA({q}) -/+ {k}x{nm}({p})", e - k * a, e + k * a
    for acc in (0.01, 0.02, 0.03, 0.04, 0.05):
        for mx in (0.1, 0.2, 0.3):
            sar = sw.psar(H, L, acc, acc, mx)
            yield f"PSAR({acc},{mx})", sar, sar
    for p in (5, 10, 14, 20):
        mid, sd = sw.sma(C, p), pd.Series(C).rolling(p, min_periods=2).std(ddof=0).to_numpy()
        tpm, rng = sw.sma(typ, p), sw.sma(H - L, p)
        for k in (1.0, 1.5, 2.0, 2.5, 3.0):
            yield f"Bollinger({k},{p}) band", mid - k * sd, mid + k * sd
            yield f"Keltner({k},{p}) band", tpm - k * rng, tpm + k * rng
    rows = [("", o, h, l, c) for o, h, l, c in zip(O, H, L, C)]
    M, F = mama(rows)
    yield "MAMA", np.asarray(M, float), None
    yield "FAMA", np.asarray(F, float), None


def main() -> None:
    x = pd.read_csv(PATHS)
    x = x[x.in_bars & x.stop.notna()].copy()
    d = sw.load_series("2026-02-15")
    lab = d.time.dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d %H:%M")
    idx = {v: i for i, v in enumerate(lab)}
    x["i"] = x.bar_close_et.map(idx)
    x = x.dropna(subset=["i"])
    x["i"] = x.i.astype(int)
    x["sgn"] = np.where(x.side == "long", 1, -1)
    trades = list(x.groupby(["video", "trade"]))
    n_pts = len(x)
    print(f"stop paths: {len(trades)} trades, {n_pts} bar closes with a stop read")
    assert len(trades) >= 30, "too few trades with a stop path"
    results = []
    for name, lo, hi in candidates(d):
        if hi is None:
            C = d.close.to_numpy(float)
            hi = 2 * C - lo
        pred = np.full(n_pts, np.nan)
        pos = 0
        for (_, _), g in trades:
            sgn = g.sgn.iloc[0]
            line = (lo if sgn > 0 else hi)[g.i.to_numpy()]
            r = np.maximum.accumulate(line) if sgn > 0 else np.minimum.accumulate(line)
            pred[pos:pos + len(g)] = r
            pos += len(g)
        stop = np.concatenate([g.stop.to_numpy() for _, g in trades])
        k = np.concatenate([g.k.to_numpy() for _, g in trades])
        sgns = np.concatenate([g.sgn.to_numpy() for _, g in trades])
        res = (stop - pred) * sgns
        ok = np.isfinite(res)
        if ok.sum() < 0.5 * n_pts:
            continue
        off = np.round(np.median(res[ok]) * 4) / 4
        e = np.abs(res - off)
        exact = float(np.nanmean(e <= TOL))
        exact0 = float(np.nanmean(np.abs(res) <= TOL))
        first = float(np.nanmean(e[k == 1] <= TOL)) if (k == 1).any() else np.nan
        later = float(np.nanmean(e[k > 1] <= TOL)) if (k > 1).any() else np.nan
        results.append((exact, exact0, off, first, later, float(np.nanmedian(e)), name))
    results.sort(key=lambda r: -r[0])
    lines = ["", f"STOP RULE FIT  {datetime.now():%Y-%m-%d %H:%M}  ({len(trades)} trades, {n_pts} bar closes, tolerance {TOL} pt; "
             "his stop if >= 80 % exact with one global offset - pre-registered)",
             f"{'exact':>6} {'no-off':>6} {'offset':>7} {'k=1':>5} {'k>1':>5} {'med|e|':>7}  candidate (ratcheted)"]
    for r in results[:40]:
        lines.append(f"{r[0]:>6.0%} {r[1]:>6.0%} {r[2]:>+7.2f} {r[3]:>5.0%} {r[4]:>5.0%} {r[5]:>7.2f}  {r[6]}")
    text = "\n".join(lines)
    print(text)
    with open(sw.OUT, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    main()
