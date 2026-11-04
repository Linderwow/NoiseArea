r"""Fit Da Captain Trader's ENTRY rule from the ledger: which 1-minute rule fires on the bars where he entered and stays
quiet on the bars where he sat flat and did nothing?

Data: D:\stream_capture\DaCaptainTrader_\trades.csv (only entries verified on the tape, not those already open when a
video started). Signal bar = the bar whose label equals the fill minute (bars are close-stamped; his fills land on a
bar's close). Flat bars = minutes between a verified exit and the next entry of the same day/video, excluding the first
and last minute of each gap and anything inside a capture gap. Bars come from tape_seconds.minute_bars (recorder since
2026-10-22, NinjaTrader's minute store before).

Rules: channel breaks, close x EMA(k), EMA pairs, MAMA/FAMA, NT Trend Lines break, bar-size rules, bands, time-of-day
filters; then every AND pair of the best singles. Score per rule: hits (signal bars where it fires in the trade's
direction), wrong-side hits, false alarms (flat bars where it fires). Writes YT_RULE_FIT_<date>.txt next to this file.
"""
from __future__ import annotations

import csv
import itertools
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tape_seconds as tape  # noqa: E402
from mama_port import mama  # noqa: E402
from trendline_port import trend_lines, crosses  # noqa: E402

LEDGER = Path(r"D:\stream_capture\DaCaptainTrader_\trades.csv")


def load_trades() -> list[dict]:
    rows = list(csv.DictReader(open(LEDGER, encoding="utf-8")))
    return rows


def day_bars(day: str):
    df = tape.minute_bars(day)
    if df is None or not len(df):
        return None
    return df


def signal_and_flat(rows: list[dict]):
    """Per day: signal bars {bar_label: side} and flat bar labels."""
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    signals: dict[str, dict[str, int]] = defaultdict(dict)
    flats: dict[str, dict[str, int]] = defaultdict(dict)  # flat bar -> the direction that was "armed" (opposite of the last trade)
    for day, trs in by_day.items():
        trs.sort(key=lambda r: r["entry_et"] or r["entry_bar"])
        for r in trs:
            if r.get("entry_ok") in ("yes", "fixed") and r.get("side") in ("long", "short") and r.get("entry_bar"):
                signals[day][r["entry_bar"]] = 1 if r["side"] == "long" else -1
        # flat windows: verified exit -> next entry (same video), both from the ledger's ET times. He alternates
        # direction (36 of 38 next entries were the opposite side, 11-04), so only a breakout in the OPPOSITE direction
        # of the trade just closed counts as a false alarm.
        for a, b in zip(trs, trs[1:]):
            if a.get("exit_ok") != "yes" or not a.get("exit_et") or not b.get("entry_et"):
                continue
            if a.get("video") != b.get("video"):
                continue
            armed = -1 if a.get("side") == "long" else 1
            t0 = datetime.strptime(f"{day} {a['exit_et']}", "%Y-%m-%d %H:%M:%S") + timedelta(minutes=2)
            t1 = datetime.strptime(f"{day} {b['entry_et']}", "%Y-%m-%d %H:%M:%S") - timedelta(minutes=1)
            t = t0.replace(second=0)
            while t <= t1:
                lab = t.strftime("%H:%M")
                if lab not in signals[day]:
                    flats[day][lab] = armed
                t += timedelta(minutes=1)
    return signals, flats


def ema(x, k):
    a = 2 / (k + 1)
    out = np.empty_like(x)
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def rules_for_day(bars) -> dict[str, np.ndarray]:
    """Each rule: array of +1 / -1 / 0 per bar (direction it would enter at that bar's close)."""
    O, H, L, C = (bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    n = len(C)
    out: dict[str, np.ndarray] = {}

    def cross(a, b):
        s = np.zeros(n)
        s[1:][(a[:-1] <= b[:-1]) & (a[1:] > b[1:])] = 1
        s[1:][(a[:-1] >= b[:-1]) & (a[1:] < b[1:])] = -1
        return s

    for N in (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 30, 45, 60, 100, 120, 200):
        s = np.zeros(n)
        for t in range(N, n):
            if C[t] > H[t - N:t].max():
                s[t] = 1
            elif C[t] < L[t - N:t].min():
                s[t] = -1
        out[f"close breaks prior {N} highs/lows"] = s
        s2 = np.zeros(n)
        for t in range(N, n):
            if C[t] > C[t - N:t].max():
                s2[t] = 1
            elif C[t] < C[t - N:t].min():
                s2[t] = -1
        out[f"close breaks prior {N} closes"] = s2
    for k in (5, 8, 9, 10, 13, 15, 20, 21, 26, 34, 50):
        out[f"close x EMA({k})"] = cross(C, ema(C, k))
    for a, b in ((5, 13), (8, 21), (9, 21), (10, 20), (13, 34), (20, 50)):
        out[f"EMA({a}) x EMA({b})"] = cross(ema(C, a), ema(C, b))
    rows = [("", o, h, l, c) for o, h, l, c in zip(O, H, L, C)]
    M, F = mama(rows)
    M, F = np.array(M), np.array(F)
    out["close x MAMA"] = cross(C, M)
    out["close x FAMA"] = cross(C, F)
    out["MAMA x FAMA"] = cross(M, F)
    vals, kinds, _ = trend_lines([(o, h, l, c) for o, h, l, c in zip(O, H, L, C)], 5)
    s = np.zeros(n)
    for t, d in crosses([(o, h, l, c) for o, h, l, c in zip(O, H, L, C)], vals):
        s[t] = 1 if d == "above" else -1
    out["close x Trend Lines(5)"] = s
    tr = np.maximum(H[1:] - L[1:], np.maximum(abs(H[1:] - C[:-1]), abs(L[1:] - C[:-1])))
    A = np.concatenate([[tr[0]], ema(tr, 14)])
    body = C - O
    for m in (0.5, 1.0, 1.5, 2.0):
        s = np.zeros(n)
        s[body > m * A] = 1
        s[body < -m * A] = -1
        out[f"bar body > {m} ATR(14)"] = s
    for X in (5, 10, 15, 20):
        s = np.zeros(n)
        s[body > X] = 1
        s[body < -X] = -1
        out[f"bar body > {X} pts"] = s
    for k in (10, 20):
        sma = np.convolve(C, np.ones(k) / k, "full")[:n]
        sd = np.array([C[max(0, t - k + 1):t + 1].std() for t in range(n)])
        for m in (1.0, 2.0):
            s = np.zeros(n)
            s[C > sma + m * sd] = 1
            s[C < sma - m * sd] = -1
            out[f"close outside SMA({k}) +/- {m} sd"] = s
    # two closes in a row in the same direction beyond the prior bar's range
    s = np.zeros(n)
    for t in range(2, n):
        if C[t] > H[t - 1] and C[t - 1] > H[t - 2]:
            s[t] = 1
        elif C[t] < L[t - 1] and C[t - 1] < L[t - 2]:
            s[t] = -1
    out["two consecutive range breaks"] = s
    # trend / position filters (fire every bar in the trend's direction; useful only inside AND-pairs)
    for k in (20, 50, 100, 200):
        e = ema(C, k)
        s = np.where(C > e, 1, -1).astype(float)
        out[f"filter: close above/below EMA({k})"] = s
    out["filter: close above/below FAMA"] = np.where(C > F, 1, -1).astype(float)
    out["filter: MAMA above/below FAMA"] = np.where(M > F, 1, -1).astype(float)
    rng = np.maximum(H - L, 1e-9)
    pos = (C - L) / rng
    s = np.zeros(n)
    s[pos >= 0.8] = 1
    s[pos <= 0.2] = -1
    out["bar closes in its top/bottom 20 %"] = s
    for k in (2, 3):
        s = np.zeros(n)
        for t in range(k, n):
            if all(C[t - j] > C[t - j - 1] for j in range(k)):
                s[t] = 1
            elif all(C[t - j] < C[t - j - 1] for j in range(k)):
                s[t] = -1
        out[f"{k} rising/falling closes in a row"] = s
    A100 = np.concatenate([[tr[0]], ema(tr, 100)])
    out["filter: ATR(14) > ATR(100) (expanding)"] = np.where(A > A100, 1, 0).astype(float)
    return out


def main() -> None:
    rows = load_trades()
    signals, flats = signal_and_flat(rows)
    days = sorted(set(signals) | set(flats))
    print(f"ledger {len(rows)} trades; days with usable entries: {len([d for d in days if signals[d]])}; "
          f"signal bars {sum(len(v) for v in signals.values())}, flat bars {sum(len(v) for v in flats.values())}")
    score: dict[str, dict[str, int]] = defaultdict(lambda: {"hit": 0, "wrong": 0, "false": 0})
    fired: dict[str, dict[tuple[str, str], int]] = defaultdict(dict)  # rule -> (day, bar) -> direction
    n_sig = n_flat = 0
    for day in days:
        bars = day_bars(day)
        if bars is None:
            print(f"  no bars for {day}")
            continue
        labels = [t.strftime("%H:%M") for t in bars.index]
        idx = {lab: i for i, lab in enumerate(labels)}
        rules = rules_for_day(bars)
        sig = signals.get(day, {})
        fl = flats.get(day, {})
        n_sig += sum(1 for lab in sig if lab in idx)
        n_flat += sum(1 for lab in fl if lab in idx)
        for name, s in rules.items():
            volatility_filter = name.startswith("filter: ATR")
            for lab, side in sig.items():
                i = idx.get(lab)
                if i is None:
                    continue
                # +/- one bar of tolerance: a video clock a few seconds off moves the fill into the neighbouring label
                vals = [s[i]] + ([s[i - 1]] if i >= 1 else [])
                if volatility_filter:
                    if any(v == 1 for v in vals):
                        score[name]["hit"] += 1
                elif any(v == side for v in vals):
                    score[name]["hit"] += 1
                elif any(v == -side for v in vals):
                    score[name]["wrong"] += 1
            for lab, armed in fl.items():
                i = idx.get(lab)
                if i is None:
                    continue
                if (volatility_filter and s[i] == 1) or (not volatility_filter and s[i] == armed):
                    score[name]["false"] += 1  # a breakout in the armed direction he did NOT take
            for i, lab in enumerate(labels):
                if s[i] != 0:
                    fired[name][(day, lab)] = int(s[i])
    lines = [f"YT RULE FIT {datetime.now():%Y-%m-%d %H:%M}  signal bars {n_sig}, flat bars {n_flat}, rules {len(score)}", ""]
    ranked = sorted(score.items(), key=lambda kv: (-(kv[1]["hit"] - kv[1]["false"] * n_sig / max(n_flat, 1)), kv[1]["false"]))
    lines.append(f"{'hits':>5} {'wrong':>5} {'false':>5} {'recall':>6} {'precision':>9}  rule   (precision = hits / (hits + false alarms))")
    for name, sc in ranked[:30]:
        prec = sc["hit"] / max(1, sc["hit"] + sc["false"])
        lines.append(f"{sc['hit']:>5} {sc['wrong']:>5} {sc['false']:>5} {sc['hit']/max(n_sig,1):>6.0%} {prec:>9.0%}  {name}")
    # AND-pairs: the 15 best triggers x every filter, plus trigger pairs (both fire, same direction; a filter agrees in
    # direction, the volatility filter just has to be on). Tolerance of one bar on the trigger as above.
    triggers = [name for name, _ in ranked if not name.startswith("filter:")][:15]
    filters = [name for name in score if name.startswith("filter:")]
    pair_scores = []

    def agree(name, d, day, lab, i_lab):
        f = fired[name]
        v = f.get((day, lab), 0)
        if name.startswith("filter: ATR"):
            return v == 1
        return v == d

    def prev_label(lab):
        h, m = int(lab[:2]), int(lab[3:])
        m -= 1
        if m < 0:
            m, h = 59, (h - 1) % 24
        return f"{h:02d}:{m:02d}"

    combos = [(a, b) for a in triggers for b in filters] + list(itertools.combinations(triggers, 2))
    for a, b in combos:
        hit = wrong = false = 0
        fa = fired[a]
        for day in days:
            for lab, side in signals.get(day, {}).items():
                for L in (lab, prev_label(lab)):
                    da = fa.get((day, L), 0)
                    if da and agree(b, da, day, L, None):
                        if da == side:
                            hit += 1
                        else:
                            wrong += 1
                        break
            for lab, armed in flats.get(day, {}).items():
                da = fa.get((day, lab), 0)
                if da == armed and agree(b, da, day, lab, None):
                    false += 1
        pair_scores.append((hit, wrong, false, f"{a}  AND  {b}"))
    pair_scores.sort(key=lambda x: (-(x[0] - x[2] * n_sig / max(n_flat, 1)), x[2]))
    lines += ["", "best AND-pairs of the top singles:", f"{'hits':>5} {'wrong':>5} {'false':>5}  pair"]
    for hit, wrong, false, name in pair_scores[:15]:
        lines.append(f"{hit:>5} {wrong:>5} {false:>5}  {name}")
    text = "\n".join(lines)
    print(text)
    (HERE / f"YT_RULE_FIT_{datetime.now():%Y%m%d_%H%M}.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
