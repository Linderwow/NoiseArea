"""Which simple 1-minute rule fires exactly on the streamer's four entries of 2026-11-04 and on none of the other bars
where the strategy was flat?  (The entries fill at the next bar's open, so the signal bars are 05:38 S, 06:02 L,
07:01 S, 07:20 L.  While it held a position its signals are unobservable, so only flat bars count as false alarms:
04:50-05:37, 05:55-06:01, 07:08-07:19.)  Bars: the recorder's MNQ 1-minute bars (ET)."""
import csv, math, sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trendline_port import trend_lines, crosses
from mama_port import mama

BASE = r"D:\nt_recorder\MNQ_12-26"
rows, seen = [], set()
for day in ("2026-11-03", "2026-11-04"):
    for r in csv.DictReader(open(os.path.join(BASE, f"{day}_bars1minute.csv"))):
        if r["time_et"] in seen:
            continue
        seen.add(r["time_et"])
        rows.append((r["time_et"], float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
ts = [r[0] for r in rows]
O, H, L, C = (np.array([r[i] for r in rows]) for i in (1, 2, 3, 4))
n = len(rows)
idx = {t[11:16]: i for i, t in enumerate(ts) if t.startswith("2026-11-04")}
SIG = {idx["05:38"]: -1, idx["06:02"]: +1, idx["07:01"]: -1, idx["07:20"]: +1}
FLAT = [i for h, i in idx.items() if "04:50" <= h <= "05:37" or "05:55" <= h <= "06:01" or "07:08" <= h <= "07:19"]
assert len(FLAT) == 48 + 7 + 12, len(FLAT)


def ema(x, k):
    a = 2 / (k + 1); out = np.empty_like(x); out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = a * x[i] + (1 - a) * out[i - 1]
    return out


def atr(k):
    tr = np.maximum(H[1:] - L[1:], np.maximum(abs(H[1:] - C[:-1]), abs(L[1:] - C[:-1])))
    return np.concatenate([[tr[0]], ema(tr, k)])


def cross_signal(a, b):
    """+1 where a crosses above b at bar t, -1 where below, 0 else."""
    s = np.zeros(n)
    s[1:][(a[:-1] <= b[:-1]) & (a[1:] > b[1:])] = 1
    s[1:][(a[:-1] >= b[:-1]) & (a[1:] < b[1:])] = -1
    return s


def channel(N, src_hi=H, src_lo=L):
    s = np.zeros(n)
    for t in range(N, n):
        if C[t] > src_hi[t - N:t].max():
            s[t] = 1
        elif C[t] < src_lo[t - N:t].min():
            s[t] = -1
    return s


rules = {}
for N in range(2, 61):
    rules[f"channel break of prior {N} highs/lows (close)"] = channel(N)
    rules[f"channel break of prior {N} closes"] = channel(N, C, C)
for k in range(3, 101):
    rules[f"close x EMA({k})"] = cross_signal(C, ema(C, k))
for a in (3, 5, 8, 9, 10, 12, 13, 20, 21):
    for b in (13, 20, 21, 26, 34, 50, 55, 89, 100, 200):
        if a < b:
            rules[f"EMA({a}) x EMA({b})"] = cross_signal(ema(C, a), ema(C, b))
M, F = mama([(t, o, h, l, c) for t, o, h, l, c in rows])
M, F = np.array(M), np.array(F)
rules["close x MAMA"] = cross_signal(C, M)
rules["close x FAMA"] = cross_signal(C, F)
rules["MAMA x FAMA"] = cross_signal(M, F)
vals, kinds, _ = trend_lines([r[1:] for r in rows], 5)
s = np.zeros(n)
for t, d in crosses([r[1:] for r in rows], vals):
    s[t] = 1 if d == "above" else -1
rules["close x Trend Lines(5)"] = s
A = atr(14)
for m in (1.0, 1.5, 2.0, 2.5, 3.0):
    s = np.zeros(n); s[(C - O) > m * A] = 1; s[(O - C) > m * A] = -1
    rules[f"bar body > {m} ATR(14)"] = s
    s = np.zeros(n); s[(H - L) > m * A] = np.sign(C - O)[(H - L) > m * A]
    rules[f"bar range > {m} ATR(14), bar direction"] = s
for X in (5, 10, 15, 20, 25, 30):
    s = np.zeros(n); s[C - C[np.r_[0, np.arange(n - 1)]] > X] = 1; s[C - C[np.r_[0, np.arange(n - 1)]] < -X] = -1
    rules[f"1-bar close change > {X} pts"] = s
for k in (10, 20):
    for m in (1.0, 1.5, 2.0):
        sma = np.convolve(C, np.ones(k) / k, "full")[:n]
        sd = np.array([C[max(0, t - k + 1):t + 1].std() for t in range(n)])
        s = np.zeros(n); s[C > sma + m * sd] = 1; s[C < sma - m * sd] = -1
        rules[f"close outside SMA({k}) +/- {m} sd"] = s

print(f"{len(rules)} rules; 4 signal bars, {len(FLAT)} flat bars where any signal is a false alarm\n")
scored = []
for name, s in rules.items():
    hits = sum(1 for t, d in SIG.items() if s[t] == d)
    wrong = sum(1 for t, d in SIG.items() if s[t] == -d)
    false = sum(1 for t in FLAT if s[t] != 0)
    scored.append((hits, -false, name, wrong))
scored.sort(reverse=True)
print(f"{'hits/4':>6} {'false':>5} {'wrong-side':>10}  rule")
for hits, nf, name, wrong in scored[:25]:
    print(f"{hits:>6} {-nf:>5} {wrong:>10}  {name}")
print("\nrules with 4/4:", [name for h, nf, name, w in scored if h == 4] or "none")
