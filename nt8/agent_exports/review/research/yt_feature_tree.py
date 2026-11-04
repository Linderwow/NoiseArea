r"""Last systematic pass on the entry filter: ~45 indicator features at every armed-direction breakout (taken vs let go),
depth-limited trees and a logistic regression, 5-fold cross-validated. If nothing beats the base rate by a clear margin,
the choice between taken and let-go breakouts is not a function of the chart's own data (OHLCV + any standard indicator),
and no amount of further indicator hunting will find it.

Features (all signed in the armed direction where that makes sense): RSI 7/14, MACD line/hist and their slopes, stochastic
14, Bollinger %B 20/2, Keltner position, ADX 14 and DI difference, CCI 20, Williams %R 14, ROC 3/5/10/20, EMA 9/21/50
slopes, close vs EMA 9/21/50/100/200 and vs VWAP, MAMA-FAMA gap and MAMA slope, Heikin-Ashi colour streak, same-colour
bar streak, inside/outside bar, bar body/range/ATR ratios, breakout size, bars since the day's high/low, volume ratios,
minutes since exit, distance from the exit price, hour, weekday, previous exit kind.
"""
from __future__ import annotations

import contextlib
import io
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_rule_diag as diag  # noqa: E402
import yt_rule_fit as fit  # noqa: E402
from mama_port import mama  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
sys.argv = [sys.argv[0], str(N)]
with contextlib.redirect_stdout(io.StringIO()):
    diag.main()
taken = [t for t in diag.TAKEN if not t.get("note") and t.get("prev_exit") not in ("-", None)
         and not (t.get("mins_since_exit") is not None and not np.isnan(t["mins_since_exit"]) and t["mins_since_exit"] <= 1.5)]
missed = list(diag.MISSED)


def ema(x, k):
    return fit.ema(x, k)


def day_features(day):
    bars = fit.day_bars(day)
    O, H, L, C = (bars[c].to_numpy(dtype=float) for c in ("open", "high", "low", "close"))
    V = bars["volume"].to_numpy(dtype=float) if "volume" in bars else np.ones(len(C))
    n = len(C)
    labels = [t.strftime("%H:%M") for t in bars.index]
    f = {}
    e = {k: ema(C, k) for k in (9, 21, 50, 100, 200)}
    tr = np.concatenate([[H[0] - L[0]], np.maximum(H[1:] - L[1:], np.maximum(abs(H[1:] - C[:-1]), abs(L[1:] - C[:-1])))])
    atr = ema(tr, 14)
    d = np.diff(C, prepend=C[0])
    up = ema(np.where(d > 0, d, 0.0), 14)
    dn = ema(np.where(d < 0, -d, 0.0), 14)
    rsi14 = 100 - 100 / (1 + up / np.maximum(dn, 1e-9))
    up7 = ema(np.where(d > 0, d, 0.0), 7)
    dn7 = ema(np.where(d < 0, -d, 0.0), 7)
    rsi7 = 100 - 100 / (1 + up7 / np.maximum(dn7, 1e-9))
    macd = ema(C, 12) - ema(C, 26)
    sig = ema(macd, 9)
    hist = macd - sig
    ll = np.array([L[max(0, t - 13):t + 1].min() for t in range(n)])
    hh = np.array([H[max(0, t - 13):t + 1].max() for t in range(n)])
    stoch = 100 * (C - ll) / np.maximum(hh - ll, 1e-9)
    sma20 = np.array([C[max(0, t - 19):t + 1].mean() for t in range(n)])
    sd20 = np.array([C[max(0, t - 19):t + 1].std() for t in range(n)])
    pctb = (C - (sma20 - 2 * sd20)) / np.maximum(4 * sd20, 1e-9)
    kelt = (C - e[21]) / np.maximum(atr, 1e-9)
    plus_dm = np.maximum(np.diff(H, prepend=H[0]), 0)
    minus_dm = np.maximum(-np.diff(L, prepend=L[0]), 0)
    plus_di = 100 * ema(plus_dm, 14) / np.maximum(atr, 1e-9)
    minus_di = 100 * ema(minus_dm, 14) / np.maximum(atr, 1e-9)
    dx = 100 * abs(plus_di - minus_di) / np.maximum(plus_di + minus_di, 1e-9)
    adx = ema(dx, 14)
    tp = (H + L + C) / 3
    tp_sma = np.array([tp[max(0, t - 19):t + 1].mean() for t in range(n)])
    tp_md = np.array([abs(tp[max(0, t - 19):t + 1] - tp_sma[t]).mean() for t in range(n)])
    cci = (tp - tp_sma) / np.maximum(0.015 * tp_md, 1e-9)
    wr = -100 * (hh - C) / np.maximum(hh - ll, 1e-9)
    cumv = np.cumsum(V)
    vwap = np.cumsum(tp * V) / np.maximum(cumv, 1e-9)
    M, F = mama([("", o, h, l, c) for o, h, l, c in zip(O, H, L, C)])
    M, F = np.array(M), np.array(F)
    ha_c = (O + H + L + C) / 4
    ha_o = np.empty(n)
    ha_o[0] = O[0]
    for t in range(1, n):
        ha_o[t] = (ha_o[t - 1] + ha_c[t - 1]) / 2
    ha_col = np.sign(ha_c - ha_o)
    col = np.sign(C - O)
    streak = np.zeros(n)
    ha_streak = np.zeros(n)
    for t in range(1, n):
        streak[t] = streak[t - 1] + 1 if col[t] == col[t - 1] and col[t] != 0 else 1
        ha_streak[t] = ha_streak[t - 1] + 1 if ha_col[t] == ha_col[t - 1] and ha_col[t] != 0 else 1
    inside = np.zeros(n)
    outside = np.zeros(n)
    inside[1:] = (H[1:] <= H[:-1]) & (L[1:] >= L[:-1])
    outside[1:] = (H[1:] >= H[:-1]) & (L[1:] <= L[:-1])
    day_hi_bar = np.zeros(n)
    day_lo_bar = np.zeros(n)
    for t in range(n):
        day_hi_bar[t] = t - int(np.argmax(H[:t + 1]))
        day_lo_bar[t] = t - int(np.argmin(L[:t + 1]))
    vavg = np.array([V[max(0, t - 20):t].mean() if t > 0 else V[0] for t in range(n)])
    wd = bars.index[0].weekday()

    def at(i, side):
        s = side
        chan = C[i - N:i].max() if s == 1 else C[i - N:i].min()
        return {
            "rsi7_s": (rsi7[i] - 50) * s, "rsi14_s": (rsi14[i] - 50) * s, "macd_s": macd[i] * s, "hist_s": hist[i] * s,
            "hist_slope_s": (hist[i] - hist[i - 1]) * s, "macd_slope_s": (macd[i] - macd[i - 1]) * s,
            "stoch_s": (stoch[i] - 50) * s, "pctb_s": (pctb[i] - 0.5) * s, "kelt_s": kelt[i] * s,
            "adx": adx[i], "di_diff_s": (plus_di[i] - minus_di[i]) * s, "cci_s": cci[i] * s, "wr_s": (wr[i] + 50) * s,
            "roc3_s": (C[i] - C[i - 3]) * s, "roc5_s": (C[i] - C[i - 5]) * s, "roc10_s": (C[i] - C[i - 10]) * s, "roc20_s": (C[i] - C[i - 20]) * s,
            "ema9_slope_s": (e[9][i] - e[9][i - 3]) * s, "ema21_slope_s": (e[21][i] - e[21][i - 3]) * s, "ema50_slope_s": (e[50][i] - e[50][i - 5]) * s,
            "vs_ema9_s": (C[i] - e[9][i]) * s, "vs_ema21_s": (C[i] - e[21][i]) * s, "vs_ema50_s": (C[i] - e[50][i]) * s,
            "vs_ema100_s": (C[i] - e[100][i]) * s, "vs_ema200_s": (C[i] - e[200][i]) * s, "vs_vwap_s": (C[i] - vwap[i]) * s,
            "mama_fama_s": (M[i] - F[i]) * s, "mama_slope_s": (M[i] - M[i - 3]) * s, "vs_mama_s": (C[i] - M[i]) * s, "vs_fama_s": (C[i] - F[i]) * s,
            "ha_streak_s": ha_streak[i] * (1 if ha_col[i] == s else -1), "streak_s": streak[i] * (1 if col[i] == s else -1),
            "inside_prev": inside[i - 1], "outside": outside[i], "body_atr_s": (C[i] - O[i]) * s / max(atr[i], 1e-9),
            "range_atr": (H[i] - L[i]) / max(atr[i], 1e-9), "atr": atr[i], "beyond_chan_s": (C[i] - chan) * s,
            "beyond_chan_atr_s": (C[i] - chan) * s / max(atr[i], 1e-9), "pos_in_range": (C[i] - L[i]) / max(H[i] - L[i], 1e-9),
            "bars_since_day_hi": day_hi_bar[i], "bars_since_day_lo": day_lo_bar[i], "vol_ratio": V[i] / max(vavg[i], 1e-9),
            "vol_prev": V[i] / max(V[i - 1], 1e-9), "hour": int(labels[i][:2]), "minute": int(labels[i][3:]), "weekday": wd,
        }
    idx = {lab: i for i, lab in enumerate(labels)}
    return idx, at


rows, y = [], []
names = None
cache = {}
for grp, lst in (("taken", taken), ("missed", missed)):
    for t in lst:
        day = t["day"]
        if day not in cache:
            cache[day] = day_features(day)
        idx, at = cache[day]
        i = idx.get(t["bar"])
        if i is None or i < 25:
            continue
        f = at(i, t["side"])
        f["mins_since_exit"] = float(t["mins_since_exit"]) if not np.isnan(t["mins_since_exit"]) else -1.0
        f["dist_from_exit"] = float(t["dist_from_exit"]) if not (isinstance(t["dist_from_exit"], float) and np.isnan(t["dist_from_exit"])) else 0.0
        for k in ("target", "stop", "other"):
            f[f"prev_exit={k}"] = 1.0 if t["prev_exit"] == k else 0.0
        if names is None:
            names = list(f)
        rows.append([f[k] for k in names])
        y.append(1 if grp == "taken" else 0)
X = np.nan_to_num(np.array(rows, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
y = np.array(y)
base = max(y.mean(), 1 - y.mean())
print(f"taken {int(y.sum())}  let go {int((1-y).sum())}  features {X.shape[1]}  base rate {base:.0%}")

from sklearn.ensemble import RandomForestClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.model_selection import cross_val_score  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.tree import DecisionTreeClassifier, export_text  # noqa: E402

out = []
for depth in (1, 2, 3):
    clf = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=10, random_state=0)
    cv = cross_val_score(clf, X, y, cv=5)
    clf.fit(X, y)
    out.append(f"tree depth {depth}: cross-validated {cv.mean():.0%} +/- {cv.std():.0%} (base {base:.0%})")
    out.append(export_text(clf, feature_names=names, max_depth=depth, show_weights=True).rstrip())
lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.3))
cv = cross_val_score(lr, X, y, cv=5)
out.append(f"logistic regression (all features): cross-validated {cv.mean():.0%} +/- {cv.std():.0%} (base {base:.0%})")
rf = RandomForestClassifier(n_estimators=400, min_samples_leaf=5, random_state=0)
cv = cross_val_score(rf, X, y, cv=5)
rf.fit(X, y)
imp = sorted(zip(rf.feature_importances_, names), reverse=True)[:12]
out.append(f"random forest (all features): cross-validated {cv.mean():.0%} +/- {cv.std():.0%} (base {base:.0%})")
out.append("  most used features: " + ", ".join(f"{nme} {w:.2f}" for w, nme in imp))
text = "\n".join(out)
print(text)
(HERE / f"YT_FEATURE_TREE_{datetime.now():%Y%m%d_%H%M}.txt").write_text(
    f"taken {int(y.sum())} let go {int((1-y).sum())} base {base:.0%}\n" + text, encoding="utf-8")
