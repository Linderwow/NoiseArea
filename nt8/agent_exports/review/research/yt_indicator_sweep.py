from __future__ import annotations

import csv
import glob
import math
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_rule_fit as fit
from mama_port import mama

STORE = Path(r"D:\nt_ticks\minute\MNQ")
RECORDER = Path(r"D:\nt_recorder\MNQ_12-26")
ET = "America/New_York"
OUT = HERE / f"YT_INDICATOR_SWEEP_{datetime.now():%Y%m%d}.txt"


def load_series(start: str = "2026-02-15") -> pd.DataFrame:
    parts = []
    files = sorted(STORE.glob("*.parquet"), key=lambda p: (p.name[3:5], p.name[:2]))
    for order, f in enumerate(files):
        d = pd.read_parquet(f, columns=["time", "open", "high", "low", "close", "volume"])
        d["time"] = pd.to_datetime(d["time"], utc=True)
        d = d[d.time >= pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=3)]
        if len(d):
            d["order"] = order
            parts.append(d)
    raw = pd.concat(parts)
    orders = sorted(raw.order.unique())
    offsets = {orders[-1]: 0.0}
    for a, b in zip(orders[-2::-1], orders[:0:-1]):
        A = raw[raw.order == a].set_index("time").close
        B = raw[raw.order == b].set_index("time").close
        common = A.index.intersection(B.index)
        gap = float((B[common] - A[common]).mean()) if len(common) else 0.0
        offsets[a] = offsets[b] + gap
    raw = raw.sort_values(["time", "order"]).drop_duplicates("time", keep="last")
    for col in ("open", "high", "low", "close"):
        raw[col] = raw[col] + raw.order.map(offsets)
    raw = raw.drop(columns="order")
    rec = []
    for f in sorted(RECORDER.glob("*_bars1minute.csv")):
        r = pd.read_csv(f, usecols=["time_et", "open", "high", "low", "close", "volume"])
        t = pd.to_datetime(r.pop("time_et")).dt.tz_localize(ET, ambiguous="NaT", nonexistent="NaT")
        r["time"] = t.dt.tz_convert("UTC")
        rec.append(r.dropna(subset=["time"]))
    rec = pd.concat(rec).drop_duplicates("time", keep="last")
    first_rec = rec.time.min()
    d = pd.concat([raw[raw.time < first_rec], rec]).sort_values("time").drop_duplicates("time", keep="last")
    d = d[d.time >= pd.Timestamp(start, tz="UTC")].reset_index(drop=True)
    et = d.time.dt.tz_convert(ET)
    d["day"] = et.dt.strftime("%Y-%m-%d")
    d["lab"] = et.dt.strftime("%H:%M")
    return d


def sma(x, p):
    return pd.Series(x).rolling(p, min_periods=1).mean().to_numpy()


def ema(x, p):
    return pd.Series(x).ewm(span=p, adjust=False).mean().to_numpy()


def wilder(x, p):
    return pd.Series(x).ewm(alpha=1.0 / p, adjust=False).mean().to_numpy()


def _conv(x, w):
    return np.convolve(x, w, "full")[: len(x)]


def wma(x, p):
    w = np.arange(p, 0, -1, dtype=float)
    out = _conv(x, w) / w.sum()
    out[: p - 1] = sma(x, p)[: p - 1]
    return out


def hma(x, p):
    return wma(2 * wma(x, max(1, p // 2)) - wma(x, p), max(1, int(math.sqrt(p))))


def dema(x, p):
    e = ema(x, p)
    return 2 * e - ema(e, p)


def tema(x, p):
    e1 = ema(x, p)
    e2 = ema(e1, p)
    return 3 * e1 - 3 * e2 + ema(e2, p)


def tma(x, p):
    a = int(math.ceil((p + 1) / 2))
    return sma(sma(x, a), max(1, p + 1 - a))


def zlema(x, p):
    lag = (p - 1) // 2
    xs = np.concatenate([np.full(lag, x[0]), x[:-lag]]) if lag else x
    return ema(2 * x - xs, p)


def kama(x, p, fast=2, slow=30):
    fsc, ssc = 2.0 / (fast + 1), 2.0 / (slow + 1)
    out = np.empty_like(x)
    out[:p] = x[:p]
    change = np.abs(x[p:] - x[:-p])
    vol = pd.Series(np.abs(np.diff(x, prepend=x[0]))).rolling(p).sum().to_numpy()[p:]
    er = np.where(vol > 0, change / np.where(vol > 0, vol, 1), 0)
    sc = (er * (fsc - ssc) + ssc) ** 2
    v = x[p - 1]
    for k, i in enumerate(range(p, len(x))):
        v = v + sc[k] * (x[i] - v)
        out[i] = v
    return out


def vwma(c, v, p):
    return (pd.Series(c * v).rolling(p, min_periods=1).sum() / pd.Series(v).rolling(p, min_periods=1).sum().replace(0, np.nan)).ffill().to_numpy()


def _linreg_parts(x, p):
    s0 = pd.Series(x).rolling(p, min_periods=p).sum().to_numpy()
    s1 = _conv(x, np.arange(p - 1, -1, -1, dtype=float))
    sx = p * (p - 1) / 2
    sxx = (p - 1) * p * (2 * p - 1) / 6
    slope = (p * s1 - sx * s0) / (p * sxx - sx * sx)
    icpt = (s0 - slope * sx) / p
    return icpt, slope


def linreg(x, p):
    if p < 2:
        return x.copy()
    i, s = _linreg_parts(x, p)
    out = i + s * (p - 1)
    out[: p - 1] = x[: p - 1]
    return out


def tsf(x, p):
    if p < 2:
        return x.copy()
    i, s = _linreg_parts(x, p)
    out = i + s * p
    out[: p - 1] = x[: p - 1]
    return out


def t3(x, p, vf=0.7, n=3):
    y = x
    for _ in range(n):
        e = ema(y, p)
        y = e * (1 + vf) - ema(e, p) * vf
    return y


def rmax(x, p):
    return pd.Series(x).rolling(p, min_periods=1).max().to_numpy()


def rmin(x, p):
    return pd.Series(x).rolling(p, min_periods=1).min().to_numpy()


def shift(x, k=1, fill=np.nan):
    out = np.empty_like(x, dtype=float)
    out[:k] = fill
    out[k:] = x[:-k]
    return out


def cross(a, b):
    s = np.zeros(len(a), dtype=np.int8)
    a0, b0 = a[:-1], b[:-1] if np.ndim(b) else b
    a1, b1 = a[1:], b[1:] if np.ndim(b) else b
    ok = np.isfinite(a0) & np.isfinite(a1) & (np.isfinite(b0) & np.isfinite(b1) if np.ndim(b) else True)
    s[1:][ok & (a0 <= b0) & (a1 > b1)] = 1
    s[1:][ok & (a0 >= b0) & (a1 < b1)] = -1
    return s


def turn(m):
    d = np.sign(np.diff(m, prepend=m[0]))
    s = np.zeros(len(m), dtype=np.int8)
    s[1:][(d[1:] > 0) & (d[:-1] <= 0)] = 1
    s[1:][(d[1:] < 0) & (d[:-1] >= 0)] = -1
    return s


def levels(o, up, lo):
    return np.where(cross(o, np.full(len(o), up)) == 1, 1, np.where(cross(o, np.full(len(o), lo)) == -1, -1, 0)).astype(np.int8)


def reentry(o, up, lo):
    return np.where(cross(o, np.full(len(o), lo)) == 1, 1, np.where(cross(o, np.full(len(o), up)) == -1, -1, 0)).astype(np.int8)


def band_break(c, upper, lower):
    return np.where(cross(c, upper) == 1, 1, np.where(cross(c, lower) == -1, -1, 0)).astype(np.int8)


def band_reentry(c, upper, lower):
    return np.where(cross(c, lower) == 1, 1, np.where(cross(c, upper) == -1, -1, 0)).astype(np.int8)


def band_state(c, upper, lower):
    return np.where(c > upper, 1, np.where(c < lower, -1, 0)).astype(np.int8)


MA_TYPES = {"SMA": sma, "EMA": ema, "WMA": wma, "HMA": hma, "DEMA": dema, "TEMA": tema, "TMA": tma, "ZLEMA": zlema,
            "KAMA": kama, "LinReg": linreg, "TSF": tsf, "T3": t3}
PERIODS = (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 18, 20, 22, 25, 30, 35, 40, 50, 60, 80, 100, 150, 200)
FASTS = (2, 3, 4, 5, 6, 8, 9, 10, 12, 14)
OSC_P = (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 16, 20, 25, 30)


def rules(d: pd.DataFrame):
    O, H, L, C = (d[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    V = d["volume"].to_numpy(dtype=float)
    n = len(C)
    TP = (H + L + C) / 3
    for N in range(2, 61):
        hh, ll = shift(rmax(H, N)), shift(rmin(L, N))
        yield "Donchian", f"close breaks prior {N} highs/lows", band_state(C, hh, ll)
        yield "Donchian", f"close crosses prior {N} highs/lows", band_break(C, hh, ll)
        hc, lc = shift(rmax(C, N)), shift(rmin(C, N))
        yield "Donchian", f"close breaks prior {N} closes", band_state(C, hc, lc)
    cache = {}
    for t, f in MA_TYPES.items():
        for p in PERIODS:
            if t == "KAMA" and p < 3:
                continue
            m = f(C, p)
            cache[(t, p)] = m
            yield "MA", f"close x {t}({p})", cross(C, m)
            yield "MA", f"{t}({p}) turns", turn(m)
        for a in FASTS:
            for b in PERIODS:
                if b < 1.5 * a or (t, a) not in cache or (t, b) not in cache:
                    continue
                yield "MA pair", f"{t}({a}) x {t}({b})", cross(cache[(t, a)], cache[(t, b)])
    vw = {p: vwma(C, V, p) for p in PERIODS}
    for p in PERIODS:
        yield "MA", f"close x VWMA({p})", cross(C, vw[p])
        yield "MA", f"VWMA({p}) turns", turn(vw[p])
    rows = [("", o, h, l, c) for o, h, l, c in zip(O, H, L, C)]
    for fl, sl in ((0.5, 0.05), (0.3, 0.03), (0.5, 0.1), (0.7, 0.05), (0.25, 0.025)):
        M, F = mama(rows, fl, sl) if fl != 0.5 or sl != 0.05 else mama(rows)
        M, F = np.asarray(M, float), np.asarray(F, float)
        yield "MAMA", f"close x MAMA({fl},{sl})", cross(C, M)
        yield "MAMA", f"close x FAMA({fl},{sl})", cross(C, F)
        yield "MAMA", f"MAMA x FAMA({fl},{sl})", cross(M, F)
        yield "MAMA", f"MAMA({fl},{sl}) turns", turn(M)
    for p in (5, 8, 10, 12, 14, 16, 20, 25, 30, 40, 50):
        mid = sma(C, p)
        sd = pd.Series(C).rolling(p, min_periods=2).std(ddof=0).to_numpy()
        tpm = sma(TP, p)
        rng = sma(H - L, p)
        for k in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
            up, lo = mid + k * sd, mid - k * sd
            yield "Bollinger", f"close crosses Bollinger({k},{p})", band_break(C, up, lo)
            yield "Bollinger", f"close re-enters Bollinger({k},{p})", band_reentry(C, up, lo)
            yield "Bollinger", f"close outside Bollinger({k},{p})", band_state(C, up, lo)
            up, lo = tpm + k * rng, tpm - k * rng
            yield "Keltner", f"close crosses Keltner({k},{p})", band_break(C, up, lo)
            yield "Keltner", f"close re-enters Keltner({k},{p})", band_reentry(C, up, lo)
            yield "Keltner", f"close outside Keltner({k},{p})", band_state(C, up, lo)
        for typ, mf in (("SMA", sma), ("EMA", ema)):
            m = mf(C, p)
            for pct in (0.02, 0.05, 0.1, 0.2, 0.3):
                up, lo = m * (1 + pct / 100), m * (1 - pct / 100)
                yield "MAEnvelopes", f"close crosses MAEnvelopes({pct}%,{typ},{p})", band_break(C, up, lo)
                yield "MAEnvelopes", f"close re-enters MAEnvelopes({pct}%,{typ},{p})", band_reentry(C, up, lo)
                yield "MAEnvelopes", f"close outside MAEnvelopes({pct}%,{typ},{p})", band_state(C, up, lo)
    dC = np.diff(C, prepend=C[0])
    for p in OSC_P:
        up_, dn_ = wilder(np.maximum(dC, 0), p), wilder(np.maximum(-dC, 0), p)
        rsi = 100 - 100 / (1 + up_ / np.where(dn_ > 0, dn_, 1e-12))
        for sm in (1, 3):
            r = sma(rsi, sm) if sm > 1 else rsi
            for u, l_ in ((50, 50), (55, 45), (60, 40), (65, 35), (70, 30), (75, 25), (80, 20)):
                yield "RSI", f"RSI({p},{sm}) crosses {u}/{l_}", levels(r, u, l_)
                if u != l_:
                    yield "RSI", f"RSI({p},{sm}) back inside {u}/{l_}", reentry(r, u, l_)
        hh, ll = rmax(H, p), rmin(L, p)
        rngp = np.where(hh - ll > 0, hh - ll, np.nan)
        fastk = 100 * (C - ll) / rngp
        for smooth in (1, 3):
            K = sma(np.nan_to_num(fastk, nan=50), smooth) if smooth > 1 else np.nan_to_num(fastk, nan=50)
            D = sma(K, 3)
            yield "Stochastics", f"Stoch K({p},{smooth}) x D(3)", cross(K, D)
            for u, l_ in ((50, 50), (60, 40), (70, 30), (80, 20), (90, 10)):
                yield "Stochastics", f"Stoch K({p},{smooth}) crosses {u}/{l_}", levels(K, u, l_)
                if u != l_:
                    yield "Stochastics", f"Stoch K({p},{smooth}) back inside {u}/{l_}", reentry(K, u, l_)
        wr = -100 * (hh - C) / rngp
        wr = np.nan_to_num(wr, nan=-50)
        for u, l_ in ((-50, -50), (-30, -70), (-20, -80), (-10, -90)):
            yield "WilliamsR", f"WilliamsR({p}) crosses {u}/{l_}", levels(wr, u, l_)
            if u != l_:
                yield "WilliamsR", f"WilliamsR({p}) back inside {u}/{l_}", reentry(wr, u, l_)
        if p >= 3:
            win = np.lib.stride_tricks.sliding_window_view(np.concatenate([np.full(p - 1, TP[0]), TP]), p)
            md = np.abs(win - win.mean(axis=1, keepdims=True)).mean(axis=1)
            cci = (TP - sma(TP, p)) / (0.015 * np.where(md > 0, md, np.nan))
            cci = np.nan_to_num(cci)
            for lv in (0, 50, 100, 150, 200):
                yield "CCI", f"CCI({p}) crosses +/-{lv}", levels(cci, lv, -lv)
                if lv:
                    yield "CCI", f"CCI({p}) back inside +/-{lv}", reentry(cci, lv, -lv)
        mf = TP * V
        pos = pd.Series(np.where(TP > shift(TP, 1, TP[0]), mf, 0)).rolling(p, min_periods=1).sum().to_numpy()
        neg = pd.Series(np.where(TP < shift(TP, 1, TP[0]), mf, 0)).rolling(p, min_periods=1).sum().to_numpy()
        mfi = 100 * pos / np.where(pos + neg > 0, pos + neg, np.nan)
        mfi = np.nan_to_num(mfi, nan=50)
        for u, l_ in ((50, 50), (60, 40), (70, 30), (80, 20)):
            yield "MFI", f"MFI({p}) crosses {u}/{l_}", levels(mfi, u, l_)
            if u != l_:
                yield "MFI", f"MFI({p}) back inside {u}/{l_}", reentry(mfi, u, l_)
        su = pd.Series(np.maximum(dC, 0)).rolling(p, min_periods=1).sum().to_numpy()
        sd_ = pd.Series(np.maximum(-dC, 0)).rolling(p, min_periods=1).sum().to_numpy()
        cmo = 100 * (su - sd_) / np.where(su + sd_ > 0, su + sd_, np.nan)
        cmo = np.nan_to_num(cmo)
        for lv in (0, 20, 30, 40, 50):
            yield "CMO", f"CMO({p}) crosses +/-{lv}", levels(cmo, lv, -lv)
            if lv:
                yield "CMO", f"CMO({p}) back inside +/-{lv}", reentry(cmo, lv, -lv)
        mom = C - shift(C, p, C[0])
        yield "Momentum", f"Momentum({p}) crosses 0", levels(mom, 0, 0)
        yield "Momentum", f"Momentum({p}) turns", turn(sma(mom, 1))
        upm = H - shift(H, 1, H[0])
        dnm = shift(L, 1, L[0]) - L
        pdm = np.where((upm > dnm) & (upm > 0), upm, 0.0)
        ndm = np.where((dnm > upm) & (dnm > 0), dnm, 0.0)
        trr = np.maximum(H - L, np.maximum(np.abs(H - shift(C, 1, C[0])), np.abs(L - shift(C, 1, C[0]))))
        atr_ = wilder(trr, p)
        dip, din = 100 * wilder(pdm, p) / np.where(atr_ > 0, atr_, np.nan), 100 * wilder(ndm, p) / np.where(atr_ > 0, atr_, np.nan)
        yield "DMI", f"DI+({p}) x DI-({p})", cross(np.nan_to_num(dip), np.nan_to_num(din))
        if p >= 3:
            wh = np.lib.stride_tricks.sliding_window_view(np.concatenate([np.full(p, H[0]), H]), p + 1)
            wl = np.lib.stride_tricks.sliding_window_view(np.concatenate([np.full(p, L[0]), L]), p + 1)
            aup = 100 * (np.argmax(wh[:, ::-1], axis=1) * -1 + p) / p
            adn = 100 * (np.argmin(wl[:, ::-1], axis=1) * -1 + p) / p
            yield "Aroon", f"Aroon({p}) up x down", cross(aup, adn)
            osc = aup - adn
            for lv in (0, 50):
                yield "Aroon", f"AroonOscillator({p}) crosses +/-{lv}", levels(osc, lv, -lv)
        if p >= 3:
            val = np.zeros(n)
            fish = np.zeros(n)
            med = (H + L) / 2
            mx, mn = rmax(med, p), rmin(med, p)
            v = f_ = 0.0
            for i in range(n):
                rr = mx[i] - mn[i]
                v = 0.66 * ((med[i] - mn[i]) / rr - 0.5) + 0.67 * v if rr > 0 else 0.67 * v
                v = max(min(v, 0.999), -0.999)
                f_ = 0.5 * math.log((1 + v) / (1 - v)) + 0.5 * f_
                val[i] = v
                fish[i] = f_
            yield "Fisher", f"FisherTransform({p}) crosses 0", levels(fish, 0, 0)
            yield "Fisher", f"FisherTransform({p}) x its prior bar", cross(fish, shift(fish, 1, 0))
    for f in (3, 5, 6, 8, 10, 12):
        for s in (10, 13, 17, 20, 26, 34):
            if s <= f:
                continue
            line = ema(C, f) - ema(C, s)
            for g in (3, 5, 9):
                sig = ema(line, g)
                yield "MACD", f"MACD({f},{s},{g}) line x signal", cross(line, sig)
                yield "MACD", f"MACD({f},{s},{g}) histogram turns", turn(line - sig)
            yield "MACD", f"MACD({f},{s}) line crosses 0", levels(line, 0, 0)
    for p in (3, 4, 5, 6, 8, 10, 12, 14, 18, 20, 25, 30):
        e3 = ema(ema(ema(np.log(C), p), p), p)
        trix = 1e4 * np.diff(e3, prepend=e3[0])
        yield "TRIX", f"TRIX({p}) crosses 0", levels(trix, 0, 0)
        for g in (3, 5, 9):
            yield "TRIX", f"TRIX({p}) x signal({g})", cross(trix, ema(trix, g))
    for fa, sl in ((2, 5), (2, 8), (3, 8), (3, 14), (3, 20), (5, 14), (5, 25), (2, 14)):
        tsi = 100 * ema(ema(dC, sl), fa) / np.where(np.abs(ema(ema(np.abs(dC), sl), fa)) > 0, ema(ema(np.abs(dC), sl), fa), np.nan)
        tsi = np.nan_to_num(tsi)
        yield "TSI", f"TSI({fa},{sl}) crosses 0", levels(tsi, 0, 0)
        yield "TSI", f"TSI({fa},{sl}) x signal(7)", cross(tsi, ema(tsi, 7))
    pc = shift(C, 1, C[0])
    bp = C - np.minimum(L, pc)
    trr = np.maximum(H, pc) - np.minimum(L, pc)
    for a, b, c_ in ((7, 14, 28), (3, 7, 14), (5, 10, 20), (2, 4, 8)):
        def avg(k):
            return pd.Series(bp).rolling(k, min_periods=1).sum().to_numpy() / np.where(pd.Series(trr).rolling(k, min_periods=1).sum().to_numpy() > 0, pd.Series(trr).rolling(k, min_periods=1).sum().to_numpy(), np.nan)
        uo = np.nan_to_num(100 * (4 * avg(a) + 2 * avg(b) + avg(c_)) / 7, nan=50)
        for u, l_ in ((50, 50), (60, 40), (70, 30)):
            yield "UltimateOsc", f"UltimateOscillator({a},{b},{c_}) crosses {u}/{l_}", levels(uo, u, l_)
    for acc, mx_ in ((0.01, 0.1), (0.01, 0.2), (0.02, 0.1), (0.02, 0.2), (0.02, 0.3), (0.03, 0.2), (0.05, 0.2), (0.05, 0.5)):
        sar = psar(H, L, acc, acc, mx_)
        yield "ParabolicSAR", f"close x ParabolicSAR({acc},{mx_})", cross(C, sar)
    for k in (2, 3, 4, 5):
        up = np.ones(n, bool)
        dn = np.ones(n, bool)
        upx = np.ones(n, bool)
        dnx = np.ones(n, bool)
        for j in range(k):
            cj, oj = shift(C, j, np.nan) if j else C, shift(O, j, np.nan) if j else O
            up &= cj > oj
            dn &= cj < oj
            hj, hj1 = (shift(H, j, np.nan) if j else H), shift(H, j + 1, np.nan)
            lj, lj1 = (shift(L, j, np.nan) if j else L), shift(L, j + 1, np.nan)
            upx &= (cj > oj) & (hj > hj1) & (lj > lj1)
            dnx &= (cj < oj) & (hj < hj1) & (lj < lj1)
        yield "NBars", f"NBarsUp/Down({k}) up bars", _first(np.where(up, 1, np.where(dn, -1, 0)))
        yield "NBars", f"NBarsUp/Down({k}) up bars + higher highs/lows", _first(np.where(upx, 1, np.where(dnx, -1, 0)))
    hac = (O + H + L + C) / 4
    hao = np.empty(n)
    hao[0] = (O[0] + C[0]) / 2
    for i in range(1, n):
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2
    col = np.where(hac > hao, 1, -1)
    yield "HeikinAshi", "Heikin-Ashi colour flips", _first(col)
    for k in (2, 3):
        st = col.copy()
        for j in range(1, k):
            st = np.where(st == shift(col, j, 0), st, 0)
        yield "HeikinAshi", f"Heikin-Ashi {k} bars one colour", _first(st)
    for p in (1, 2, 3, 5, 8, 10):
        kup = (L < shift(rmin(L, p))) & (C > shift(C, 1, np.nan))
        kdn = (H > shift(rmax(H, p))) & (C < shift(C, 1, np.nan))
        yield "KeyReversal", f"KeyReversalUp/Down({p})", np.where(kup, 1, np.where(kdn, -1, 0)).astype(np.int8)
    obv = np.cumsum(np.sign(dC) * V)
    for p in (5, 10, 14, 20, 30, 50):
        yield "OBV", f"OBV x SMA(OBV,{p})", cross(obv, sma(obv, p))
        clv = ((C - L) - (H - C)) / np.where(H - L > 0, H - L, np.nan)
        cmf = pd.Series(np.nan_to_num(clv) * V).rolling(p, min_periods=1).sum().to_numpy() / np.where(pd.Series(V).rolling(p, min_periods=1).sum().to_numpy() > 0, pd.Series(V).rolling(p, min_periods=1).sum().to_numpy(), np.nan)
        yield "ChaikinMF", f"ChaikinMoneyFlow({p}) crosses 0", levels(np.nan_to_num(cmf), 0, 0)
    et_h = pd.to_datetime(d.time).dt.tz_convert(ET)
    sess = (et_h - pd.Timedelta(hours=18)).dt.strftime("%Y-%m-%d").to_numpy()
    g = pd.DataFrame({"s": sess, "pv": TP * V, "v": V})
    vwap = (g.groupby("s").pv.cumsum() / g.groupby("s").v.cumsum().replace(0, np.nan)).ffill().to_numpy()
    yield "VWAP", "close x session VWAP", cross(C, vwap)
    for strength in (1, 2, 3, 4, 5, 6, 8, 10, 12):
        yield "Swing", f"close breaks last Swing({strength}) high/low", swing_break(H, L, C, strength)


def _first(state):
    st = np.asarray(state, dtype=np.int8)
    prev = np.concatenate([[0], st[:-1]])
    return np.where((st != 0) & (st != prev), st, 0).astype(np.int8)


def psar(H, L, af0, step, afmax):
    n = len(H)
    out = np.empty(n)
    up = True
    af = af0
    ep = H[0]
    sar = L[0]
    for i in range(n):
        if i == 0:
            out[i] = sar
            continue
        sar = sar + af * (ep - sar)
        if up:
            sar = min(sar, L[i - 1], L[i - 2] if i >= 2 else L[i - 1])
            if L[i] < sar:
                up, sar, ep, af = False, ep, L[i], af0
            elif H[i] > ep:
                ep, af = H[i], min(af + step, afmax)
        else:
            sar = max(sar, H[i - 1], H[i - 2] if i >= 2 else H[i - 1])
            if H[i] > sar:
                up, sar, ep, af = True, ep, H[i], af0
            elif L[i] < ep:
                ep, af = L[i], min(af + step, afmax)
        out[i] = sar
    return out


def swing_break(H, L, C, s):
    n = len(H)
    hh = pd.Series(H).rolling(2 * s + 1, center=True).max().to_numpy()
    ll = pd.Series(L).rolling(2 * s + 1, center=True).min().to_numpy()
    is_hi = H == hh
    is_lo = L == ll
    last_hi = pd.Series(np.where(shift(is_hi.astype(float), s, 0) == 1, shift(H, s), np.nan)).ffill().to_numpy()
    last_lo = pd.Series(np.where(shift(is_lo.astype(float), s, 0) == 1, shift(L, s), np.nan)).ffill().to_numpy()
    state = np.where(C > last_hi, 1, np.where(C < last_lo, -1, 0))
    return _first(state)


class Scorer:
    def __init__(self, d: pd.DataFrame):
        rows = fit.load_trades()
        signals, flats = fit.signal_and_flat(rows)
        key = {(a, b): i for i, (a, b) in enumerate(zip(d["day"], d["lab"]))}
        si, ss, fi, fa = [], [], [], []
        for day, m in signals.items():
            for lab, side in m.items():
                i = key.get((day, lab))
                if i is not None and i >= 1:
                    si.append(i)
                    ss.append(side)
        for day, m in flats.items():
            for lab, armed in m.items():
                i = key.get((day, lab))
                if i is not None:
                    fi.append(i)
                    fa.append(armed)
        self.si, self.ss = np.array(si), np.array(ss, dtype=np.int8)
        self.fi, self.fa = np.array(fi), np.array(fa, dtype=np.int8)
        self.n_sig, self.n_flat = len(si), len(fi)

    def score(self, s: np.ndarray) -> tuple:
        a, b = s[self.si], s[self.si - 1]
        hit = (a == self.ss) | (b == self.ss)
        wrong = ~hit & ((a == -self.ss) | (b == -self.ss))
        false = s[self.fi] == self.fa
        h, w, f = int(hit.sum()), int(wrong.sum()), int(false.sum())
        rec = h / self.n_sig
        prec = h / max(1, h + f)
        f1 = 2 * rec * prec / max(1e-9, rec + prec)
        return h, w, f, rec, prec, f1


def entry_stage() -> None:
    t0 = time.time()
    d = load_series()
    sc = Scorer(d)
    head = (f"YT INDICATOR SWEEP - ENTRY  {datetime.now():%Y-%m-%d %H:%M}\nbars {len(d):,} ({d.day.iloc[0]} .. {d.day.iloc[-1]}), "
            f"his signal bars found {sc.n_sig}, flat armed bars {sc.n_flat}")
    print(head, flush=True)
    assert 780 <= sc.n_sig <= 900 and 4500 <= sc.n_flat <= 6500, "signal/flat bar counts outside the expected band"
    C = d.close.to_numpy(float)
    base = band_state(C, shift(rmax(C, 8)), shift(rmin(C, 8)))
    h, w, f, *_ = sc.score(base)
    print(f"baseline 'close breaks prior 8 closes': {h} hits / {w} wrong / {f} false = recall {h / sc.n_sig:.1%}, false rate "
          f"{f / sc.n_flat:.1%} (yt_rule_fit on per-day bars: 580 / 87 / 681 of 834 / 4962 = 69.5 %, 13.7 %)")
    assert abs(h / sc.n_sig - 580 / 834) <= 0.03 and abs(f / sc.n_flat - 681 / 4962) <= 0.02, \
        "baseline does not reproduce - scoring or bars are broken"
    res = []
    fam_n = defaultdict(int)
    for fam, name, s in rules(d):
        fire = float((s != 0).mean())
        for inv in (False, True):
            ss = -s if inv else s
            h, w, f, rec, prec, f1 = sc.score(ss)
            res.append((f1, rec, prec, h, w, f, fire, fam, name + (" [inverted]" if inv else "")))
        fam_n[fam] += 1
    n_rules = sum(fam_n.values())
    res.sort(key=lambda r: -r[0])
    lines = [head, f"rules {n_rules:,} (x2 with each inverted = {len(res):,} scored) in {time.time() - t0:.0f} s; by family: "
             + ", ".join(f"{k} {v}" for k, v in fam_n.items()),
             "a rule that is his would show recall >= 90 % AND precision >= 80 % (pre-registered)", ""]
    hdr = f"{'F1':>5} {'recall':>6} {'prec':>5} {'hits':>5} {'wrong':>5} {'false':>5} {'fires':>6}  {'family':<13} rule"
    fmt = lambda r: f"{r[0]:>5.2f} {r[1]:>6.0%} {r[2]:>5.0%} {r[3]:>5} {r[4]:>5} {r[5]:>5} {r[6]:>6.1%}  {r[7]:<13} {r[8]}"
    lines += ["TOP 60 BY F1", hdr] + [fmt(r) for r in res[:60]]
    his = [r for r in res if r[1] >= 0.90 and r[2] >= 0.80]
    lines += ["", f"RULES MEETING THE PRE-REGISTERED BAR (recall >= 90 %, precision >= 80 %): {len(his)}"] + [fmt(r) for r in his[:40]]
    lines += ["", "BEST PRECISION AMONG RULES WITH RECALL >= 50 %", hdr] + [fmt(r) for r in sorted([r for r in res if r[1] >= 0.5], key=lambda r: -r[2])[:25]]
    lines += ["", "BEST PER FAMILY (by F1)", hdr]
    seen = set()
    for r in res:
        if r[7] not in seen:
            seen.add(r[7])
            lines.append(fmt(r))
    text = "\n".join(lines)
    print(text)
    OUT.write_text(text + "\n", encoding="utf-8")
    pd.DataFrame(res, columns=["f1", "recall", "precision", "hits", "wrong", "false", "fires", "family", "rule"]).to_csv(
        HERE / "_sweep_entry_scores.csv", index=False)
    print(f"\nwritten {OUT.name} ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "entry"
    if stage == "entry":
        entry_stage()
    else:
        raise SystemExit("stage: entry | stop")
