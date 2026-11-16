"""trendline_test.py - hand-made bars with known answers for trendline.py (pivots, Bulenox flags, fills, the TradingView port,
the Scribd rules). Exit code 1 on any FAIL."""
import argparse
import os
import sys
import types
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import numpy as np
import pandas as pd
import trendline as tl

FAILS = []


def check(name, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'}  {name}: got {got}, want {want}")
    if not ok:
        FAILS.append(name)


def mk_from(rows):
    t = np.array([np.datetime64(r[0].replace(' ', 'T'), 'm') for r in rows])
    o, h, l, c = (np.array([r[k] for r in rows], float) for k in (1, 2, 3, 4))
    return types.SimpleNamespace(t=t, ao=o, ah=h, al=l, ac=c, iid=np.ones(len(rows), np.int64))


def series(hl, closes=None, start='2024-03-05 10:00'):
    rows = []
    prev = None
    t0 = pd.Timestamp(start)
    for i, (h, l) in enumerate(hl):
        c = closes[i] if closes is not None else (h + l) / 2
        o = prev if prev is not None else c
        o = min(max(o, l), h)
        rows.append(((t0 + pd.Timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M'), o, h, l, c))
        prev = c
    return rows


def rounded(trades):
    return [(fb, xb, s, round(float(e), 4), round(float(x), 4), k) for fb, xb, s, e, x, *_, k in trades]


ph, pl = tl.pivots(np.array([1, 2, 5, 2, 1, 1, 1.0]), np.array([0, 1, 4, 1, 0.5, 0.5, 0.5]), 2)
check('pivot high at 2 confirmed at 4', list(np.flatnonzero(ph)), [4])
ph, _ = tl.pivots(np.array([1, 2, 5, 5, 1, 1, 1.0]), np.zeros(7), 2)
check('tie goes to the earlier bar', list(np.flatnonzero(ph)), [4])

day = []
for m in range(16 * 60 + 45, 17 * 60):
    day.append((f"2024-03-05 {m // 60:02d}:{m % 60:02d}", 100.0, 100.0, 100.0, 100.0))
for m in range(18 * 60, 18 * 60 + 10):
    day.append((f"2024-03-05 {m // 60:02d}:{m % 60:02d}", 100.0, 100.0, 100.0, 100.0))
B1 = tl.make_bars(mk_from(day), 1)
hm = [str(x)[11:16] for x in B1.ts]
check('1-min flat bar = the one ending 16:58', [hm[i] for i in np.flatnonzero(B1.flat)], ['16:57'])
check('1-min fills blocked 16:58-17:59', [hm[i] for i in np.flatnonzero(B1.block)], ['16:58', '16:59'])
B5 = tl.make_bars(mk_from(day), 5)
hm5 = [str(x)[11:16] for x in B5.ts]
check('5-min flat bar = the one ending 16:55', [hm5[i] for i in np.flatnonzero(B5.flat)], ['16:50'])
check('5-min fills blocked from the 16:55 bar', [hm5[i] for i in np.flatnonzero(B5.block)], ['16:55'])

g = series([(101, 100)] * 3 + [(101, 95)] + [(101, 100)] * 3)
Bg = tl.make_bars(mk_from(g), 1)
Bg.o[3] = 96.0
T = tl.Trader(Bg, True)
for b in range(len(Bg.c)):
    T.open_bar(b)
    T.manage(b)
    if b == 1:
        T.signal(1, 1, 97.0, 150.0)
check('gap below the stop fills at the open', rounded(T.trades), [(2, 3, 1, 100.5, 96.0, 'stop')])
Bs = tl.make_bars(mk_from(series([(101, 100), (101, 100), (110, 90), (101, 100)])), 1)
T = tl.Trader(Bs, True)
for b in range(len(Bs.c)):
    T.open_bar(b)
    T.manage(b)
    if b == 0:
        T.signal(0, 1, 95.0, 105.0)
check('stop before target inside one bar', rounded(T.trades), [(1, 2, 1, 100.5, 95.0, 'stop')])

UP = [(120, 119), (121, 120), (125, 124), (121, 120), (119, 118), (120, 119), (123, 122), (119, 118), (117, 116), (118, 117),
      (121, 120), (117, 116), (115, 114), (116, 115), (119, 118), (115, 114), (113, 112), (120.5, 119), (121, 120),
      (200, 120)] + [(121, 120)] * 6
Bu = tl.make_bars(mk_from(series(UP)), 1)
lines = []
tr = tl.run_tv(Bu, L=2, space=0.0, lines=lines)
check('TradingView: one resistance line, C(10,121)-D(14,119), made at bar 16', [(k, b, ln[:4]) for k, b, ln in lines],
      [('up', 16, (10, 121.0, 14, 119.0))])
a14 = tl.atr(Bu, 14)
tgt = round(float(Bu.c[17] + 20 * a14[17]), 4)
check('TradingView: low 119 > 118.5 at bar 17 -> long at the 18 open, target 20 ATR', rounded(tr),
      [(18, 19, 1, round(float(Bu.o[18]), 4), tgt, 'target')])
UPX = list(UP)
UPX[11] = (121, 120.8)
lines = []
tr = tl.run_tv(tl.make_bars(mk_from(series(UPX)), 1), L=2, space=0.0, lines=lines)
check('TradingView: low 120.8 above the back-projected 120.75 discards the line', (lines, tr), ([], []))
DN = [(200 - l, 200 - h) for h, l in UP]
Bd = tl.make_bars(mk_from(series(DN)), 1)
lines = []
tr = tl.run_tv(Bd, L=2, space=0.0, lines=lines)
check('TradingView mirrored: support line C-D at bar 16', [(k, b, ln[:4]) for k, b, ln in lines],
      [('dn', 16, (10, 79.0, 14, 81.0))])
tgt = round(float(Bd.c[17] - 20 * tl.atr(Bd, 14)[17]), 4)
check('TradingView mirrored: short at the 18 open, target 20 ATR', rounded(tr),
      [(18, 19, -1, round(float(Bd.o[18]), 4), tgt, 'target')])

SB_HL = [(104, 103), (106, 105), (110, 109), (106, 105), (104, 103), (105, 104), (108, 107), (104, 103), (101, 100), (102, 101),
         (103, 102), (106.5, 103), (107, 105), (118.25, 106)] + [(107, 106)] * 4
SB_C = [103.5, 105.5, 109.5, 105.5, 103.5, 104.5, 107.5, 103.5, 100.5, 101.5, 102.5, 106.0, 106.5, 107.0] + [106.5] * 4
Bb = tl.make_bars(mk_from(series(SB_HL, SB_C)), 1)
check('Scribd breakout: close 106 > line 105.5 at bar 11 -> long at 106, stop 100, target 118',
      rounded(tl.run_scribd(Bb, 'SB', N=2)), [(12, 13, 1, 106.0, 118.0, 'target')])
SR_HL = list(SB_HL)
SR_HL[12] = (106.5, 104.75)
SR_HL[13] = (115.25, 105.5)
SR_C = list(SB_C)
SR_C[12] = 106.0
Br = tl.make_bars(mk_from(series(SR_HL, SR_C)), 1)
check('Scribd retest: line 105.0 traded through at bar 12 -> long 105, stop 100, target 115',
      rounded(tl.run_scribd(Br, 'SR', N=2)), [(12, 13, 1, 105.0, 115.0, 'target')])
SR_T = list(SR_HL)
SR_T[12] = (106.5, 105.0)
SR_T[13] = (106.0, 104.5)
SR_TC = list(SR_C)
SR_TC[13] = 105.5
Bt = tl.make_bars(mk_from(series(SR_T, SR_TC)), 1)
check('Scribd retest: touches only, no fill', rounded(tl.run_scribd(Bt, 'SR', N=2)), [])

SS_HL = [(101, 100), (100.5, 99), (99, 96), (102, 98), (104, 100), (103, 101), (101, 98), (102, 99), (103, 100), (102.5, 99.25),
         (107.5, 100)] + [(104, 103)] * 4
SS_C = [100.5, 99.5, 97, 101, 103, 102, 99, 101.5, 102.5, 100, 107] + [103.5] * 4
Bc = tl.make_bars(mk_from(series(SS_HL, SS_C)), 1)
check('Scribd bounce: long at the support line 99.5, out at the channel line 107',
      rounded(tl.run_scribd(Bc, 'SS', N=2)), [(9, 10, 1, 99.5, 107.0, 'target')])
SS_X = list(SS_HL)
SS_X[10] = (100.5, 99.4)
SS_XC = list(SS_C)
SS_XC[10] = 99.5
Bx = tl.make_bars(mk_from(series(SS_X, SS_XC)), 1)
check('Scribd bounce: close 99.5 below the line 100 -> out at the next open (103)',
      rounded(tl.run_scribd(Bx, 'SS', N=2))[:1], [(9, 11, 1, 99.5, 103.0, 'line')])

print('ALL PASS' if not FAILS else f'{len(FAILS)} FAIL: {FAILS}')
if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    sys.exit(1 if FAILS else 0)
