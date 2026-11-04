r"""Let a small decision tree look for the condition that separates the breakouts he took from the ones he let go
(features from yt_rule_diag: minutes since exit, previous exit kind, breakout size, bar body/range, ATR, close position,
hour, distance from EMA20/50, distance from the last exit price). Depth-limited, cross-validated, so a real rule shows
up as a short readable tree and noise shows up as chance-level accuracy."""
from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import yt_rule_diag as diag  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8
sys.argv = [sys.argv[0], str(N)]
ONLY_AFTER = 1.5
with contextlib.redirect_stdout(io.StringIO()):
    diag.main()
taken, missed = diag.TAKEN, diag.MISSED
taken = [t for t in taken if not t.get("note")]  # only entries where the trigger fired
n_fast = sum(1 for t in taken if t.get("mins_since_exit") is not None and not np.isnan(t["mins_since_exit"]) and t["mins_since_exit"] <= ONLY_AFTER)
taken = [t for t in taken if not (t.get("mins_since_exit") is not None and not np.isnan(t["mins_since_exit"]) and t["mins_since_exit"] <= ONLY_AFTER)]
print(f"{n_fast} entries within {ONLY_AFTER} min of the previous exit (stop-and-reverse) set aside; the tree looks at the rest")
# the let-go set only exists AFTER an exit; a taken entry with no previous exit that day has nothing to be compared with
n_first = sum(1 for t in taken if t.get("prev_exit") in ("-", None))
taken = [t for t in taken if t.get("prev_exit") not in ("-", None)]
print(f"{n_first} first-of-day entries (no previous exit to compare against) set aside")

from sklearn.model_selection import cross_val_score  # noqa: E402
from sklearn.tree import DecisionTreeClassifier, export_text  # noqa: E402

keys = ["mins_since_exit", "beyond_channel", "body", "range", "atr", "pos_in_range", "hour", "vs_ema20", "vs_ema50", "dist_from_exit",
        "pullback_since_exit", "vol_ratio", "vol_vs_prev"]
# nth_breakout is left out: a taken entry is by definition the last breakout of its window, let-go ones the earlier
# ones, so the count separates the two classes mechanically, not by anything the strategy could know in advance
# the first 90 s after an exit are never in the let-go set (the flat windows start 2 min after an exit), so that split
# would be an artifact: look only at entries that came later than that
ONLY_AFTER = 1.5
kinds = ["target", "stop", "other", "-"]


def row(t):
    x = [float(t[k]) if t.get(k) is not None and not (isinstance(t[k], float) and np.isnan(t[k])) else -1.0 for k in keys]
    x += [1.0 if t.get("prev_exit") == k else 0.0 for k in kinds]
    return x


X = np.array([row(t) for t in taken] + [row(t) for t in missed])
y = np.array([1] * len(taken) + [0] * len(missed))
names = keys + [f"prev_exit={k}" for k in kinds]
print(f"taken {len(taken)}  let go {len(missed)}  base rate (always say 'let go') = {max(y.mean(), 1 - y.mean()):.0%}")
for depth in (1, 2, 3, 4):
    clf = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=8, random_state=0)
    cv = cross_val_score(clf, X, y, cv=5)
    clf.fit(X, y)
    print(f"\ndepth {depth}: cross-validated accuracy {cv.mean():.0%} (+/- {cv.std():.0%}), on all data {clf.score(X, y):.0%}")
    print(export_text(clf, feature_names=names, max_depth=depth, show_weights=True))
