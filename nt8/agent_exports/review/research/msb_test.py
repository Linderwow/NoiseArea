r"""msb_test.py - the market-structure rule on hand-made bars with known answers (the Python port mirrors MarketStructure.cs line
for line, so these are the indicator's rules): swing timing, ties, close-vs-level, BOS after CHoCH, the wick both-break tie, and
invariants on a random walk.   python msb_test.py  -> ALL PASS"""
import sys, os
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
from msb import swings_vectorised, swings_loop, structure

fails = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)


def bars(h, l=None, o=None, c=None):
    h = np.array(h, float); l = h - 1.0 if l is None else np.array(l, float)
    o = (h + l) / 2 if o is None else np.array(o, float); c = (h + l) / 2 if c is None else np.array(c, float)
    return o, h, l, c


# A. a swing high is confirmed exactly N bars after it forms, nowhere else
o, h, l, c = bars([10, 11, 12, 11, 10, 9, 9, 9]); n = 2
t, b = swings_vectorised(h, l, n)
check("A swing high at bar 2 confirmed at bar 4 only", list(np.flatnonzero(t)) == [4])
t2, b2 = swings_loop(h, l, n); check("A loop and vectorised agree", np.array_equal(t, t2) and np.array_equal(b, b2))

# B. equal highs: the earlier bar is the swing (strictly above the older bars, at or above the newer)
o, h, l, c = bars([9, 10, 12, 12, 11, 10, 9]); t, b = swings_vectorised(h, l, 2)
check("B tie goes to the earlier bar (bar 2, confirmed at 4)", list(np.flatnonzero(t)) == [4])
t2, b2 = swings_loop(h, l, 2); check("B loop agrees on ties", np.array_equal(t, t2) and np.array_equal(b, b2))

# C. a close AT the level is not a break; above it is; the first break is a CHoCH (state undefined before)
h = [10, 11, 12, 11, 10, 9, 9, 9, 9, 9]; l = [x - 1 for x in h]; c = [x - 0.5 for x in h]
c[7] = 12.0; c[8] = 12.25                       # bar 7 closes exactly on the swing high 12, bar 8 above it
h[7] = 12.0; h[8] = 12.5; l[7] = 11.0; l[8] = 11.0
o, h, l, c = bars(h, l, c=c)
sig, bias, lvl = structure(o, h, l, c, 2)
check("C close == level is no break", sig[7] == 0)
check("C close above the level breaks it: CHoCH up (+2), level 12", sig[8] == 2 and lvl[8] == 12.0 and bias[8] == 1)
check("C the swing cannot be broken on its own confirmation bar", all(sig[i] == 0 for i in range(4, 7)))

# D. BOS after CHoCH: a second, higher swing high broken while bullish = BOS (+1), the state stays bullish; then a swing low
#    broken = CHoCH down (-2)
h = [10, 11, 12, 11, 10, 9, 9, 9, 12.5, 13, 14, 13, 12.5, 12.5, 12.5, 14.5, 13, 12, 11.5, 11, 10.9, 10.8, 10.5]
l = [x - 1 for x in h]; c = [x - 0.5 for x in h]
c[8] = 12.25; c[15] = 14.25                      # bar 8 breaks 12 (CHoCH up); bar 15 breaks the swing high 14 at bar 10 (BOS up)
l[12] = 11.8; l[13] = 11.9; l[14] = 11.85        # swing low 11.8 at bar 12 (older 12.0, 12.0 above; newer 11.9, 11.85 above), confirmed at 14
c[17] = 12.0; c[18] = 11.7                       # bar 17 stays above 11.8 (its default mid-bar close would not), bar 18 closes below -> CHoCH down
o, h, l, c = bars(h, l, c=c)
sig, bias, lvl = structure(o, h, l, c, 2)
check("D CHoCH up at bar 8", sig[8] == 2)
check("D BOS up at bar 15 on the 14 swing", sig[15] == 1 and lvl[15] == 14.0 and bias[15] == 1)
check("D CHoCH down at bar 18 on the 11.8 swing low", sig[18] == -2 and lvl[18] == 11.8 and bias[18] == -1)
check("D no other signals", int((sig != 0).sum()) == 3)

# E. wick mode: one bar through both pending levels -> the bar's own direction decides, both levels retire
h = [10, 11, 12, 11, 10, 10, 10, 10, 10, 10, 10, 10, 10]; l = [x - 1 for x in h]
l[6] = 8.0; l[7] = 8.5; l[8] = 8.6; l[5] = 8.7   # swing low 8.0 at bar 6 (older 8.7, 9.0; newer 8.5, 8.6), confirmed at 8
h[10] = 12.5; l[10] = 7.5                        # bar 10 wicks through 12 and 8
o, h, l, c = bars(h, l)
o[10] = 10.0; c[10] = 9.5                         # closes down -> the bearish break wins
sig, bias, lvl = structure(o, h, l, c, 2, wicks=True)
check("E wick both-break: bearish by the bar's direction (-2 on 8.0)", sig[10] == -2 and lvl[10] == 8.0)
c2 = c.copy(); c2[10] = 10.5
sig2, bias2, lvl2 = structure(o, h, l, c2, 2, wicks=True)
check("E wick both-break: bullish when the bar closes up (+2 on 12)", sig2[10] == 2 and lvl2[10] == 12.0)
check("E both levels retired: no later signal without a new swing", int((sig[11:] != 0).sum()) == 0 and int((sig2[11:] != 0).sum()) == 0)
sig3, _, _ = structure(o, h, l, c, 2, wicks=False)
check("E close mode on the same bars: only the bearish close break (close 9.5 > 8.0 -> none at bar 10)", sig3[10] == 0)

# F. invariants on a random walk, N = 5: every break is beyond its level by the close, BOS keeps the state, CHoCH flips it,
#    levels broken are confirmed swing values, and loop == vectorised swings
rng = np.random.default_rng(7); N = 20000
c = 10000 + np.cumsum(rng.normal(0, 2, N)); o = np.r_[c[0], c[:-1]]
h = np.maximum(o, c) + rng.uniform(0, 2, N); l = np.minimum(o, c) - rng.uniform(0, 2, N)
t, b = swings_vectorised(h, l, 5); t2, b2 = swings_loop(h, l, 5)
check("F loop == vectorised on 20,000 random bars", np.array_equal(t, t2) and np.array_equal(b, b2))
sig, bias, lvl = structure(o, h, l, c, 5)
ok_lvl = all((c[i] > lvl[i]) if sig[i] > 0 else (c[i] < lvl[i]) for i in np.flatnonzero(sig))
check("F every break is a close beyond its level", ok_lvl)
prev = np.r_[0, bias[:-1]]
check("F CHoCH flips the state, BOS keeps it", all((abs(sig[i]) == 2) == (prev[i] != bias[i]) for i in np.flatnonzero(sig)) and
      all(prev[i] == bias[i] for i in np.flatnonzero(sig == 0)))
sw_vals = set(h[np.flatnonzero(t) - 5]) | set(l[np.flatnonzero(b) - 5])
check("F every broken level is a confirmed swing value", all(lvl[i] in sw_vals for i in np.flatnonzero(sig)))
check("F breaks happen (sample count band)", 50 < int((sig != 0).sum()) < 5000)

print(("ALL PASS" if not fails else f"{len(fails)} FAILED: " + ", ".join(fails)))
sys.exit(1 if fails else 0)
