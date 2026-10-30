r"""chandelier_test.py - the review's mirror of the chandelier exit (2026-10-30; the 20261030_chandelier build of NoiseAreaMomentum).
    python chandelier_test.py            every check prints PASS / FAIL; exit code 1 on any FAIL
A. wilder_atr == NinjaTrader's ATR formula (cumulative mean over the first n bars, Wilder after) on a hand-built series.
B. Synthetic sessions: a long entered at 10:00 is exited by the chandelier at 11:05 (not at the 11:00 check), re-entered at 11:30;
   with the exit off, or at an unreachable 1000 x ATR, the decisions are the validated rules' exactly.
C. Parity with the research engine (research\strat3.py run(), the engine CHVAL audited against chval.py): on the strategy's own bars
   file, the rules WITH (3 x ATR(14)) and WITHOUT the exit, P&L in points per session rebuilt from the review's decisions, must equal
   the engine's to 1e-6 on every decidable session. Skipped (reported) when the bars file or the research modules are missing.
Temp files only; nothing is sent anywhere.
"""
import os, sys, io, tempfile, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import daily_review as R

fails = 0
def check(name, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ''))
    if not ok: fails += 1


# ------------------------------------------------------------------ A. ATR formula
def nt_atr(h, l, c, n):
    """NinjaTrader's ATR (Indicators\\@ATR.cs): bar 0 = high - low; then ((min(bar+1, n) - 1) * prev + TR) / min(bar+1, n)."""
    out = []
    for i in range(len(h)):
        if i == 0: out.append(h[0] - l[0]); continue
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])); k = min(i + 1, n)
        out.append(((k - 1) * out[-1] + tr) / k)
    return np.array(out)

rng = np.random.default_rng(7)
c = 100 + np.cumsum(rng.normal(0, 0.3, 60)); h = c + rng.uniform(0, 0.4, 60); l = c - rng.uniform(0, 0.4, 60)
t0 = pd.Timestamp('2026-10-01 09:35')
frame = pd.DataFrame({'time_et': [(t0 + pd.Timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M:%S') for i in range(60)],
                      'open': np.r_[c[0], c[:-1]], 'high': h, 'low': l, 'close': c, 'volume': 100})
check("A1 wilder_atr equals NinjaTrader's ATR(14) bar by bar", np.allclose(R.wilder_atr(frame, 14), nt_atr(h, l, c, 14), atol=1e-12))
check("A2 wilder_atr equals NinjaTrader's ATR(7)", np.allclose(R.wilder_atr(frame, 7), nt_atr(h, l, c, 7), atol=1e-12))
shuf = frame.sample(frac=1, random_state=3)
check("A3 row order does not matter (aligned to the frame's rows)", np.allclose(R.wilder_atr(shuf, 14), nt_atr(h, l, c, 14)[shuf.index.values], atol=1e-12))


# ------------------------------------------------------------------ B. synthetic sessions
def day(date, closes, o930=100.0, half=0.05):
    rows = []; prev = o930
    for i, cl in enumerate(closes):
        t = dt.datetime.combine(date, dt.time(9, 35)) + dt.timedelta(minutes=5 * i)
        o = o930 if i == 0 else prev
        rows.append(dict(time_et=t.strftime('%Y-%m-%d %H:%M:%S'), open=o, high=max(o, cl) + half, low=min(o, cl) - half, close=cl, volume=100.0))
        prev = cl
    return rows

NB = 78                                                    # 09:35 .. 16:00
dates = pd.bdate_range('2026-09-01', periods=16).date
rows = []
for d in dates[:15]: rows += day(d, [100.1] * NB)         # |close / open - 1| = 0.001 on every bar -> sigma 0.001, prior close 100.1
# day 16: 10:00 close 100.5 > upper 100.1 * 1.001 -> LONG (fill 10:05 open); rises to 101.7 by 11:00; 11:05 collapses to 99.0
# (far more than 3 ATR below the best close 101.7) -> chandelier exit; 101.5 from 11:10 -> back above the band at the 11:30 check -> LONG again
cl = [100.1] * 5 + [100.5] + [100.6 + 0.1 * k for k in range(12)] + [99.0] + [101.5] * (NB - 19)
assert len(cl) == NB and abs(cl[17] - 101.7) < 1e-9
rows += day(dates[15], cl)
B = pd.DataFrame(rows); D16 = dates[15]
atr = R.wilder_atr(B, 14); i_1105 = B.index[(B.time_et == f"{D16} 11:05:00")][0]
check("B0 the synthetic drop is more than 3 ATR below the best close", 99.0 < 101.7 - 3 * atr[i_1105], f"ATR at 11:05 {atr[i_1105]:.3f}")

ch_times = []
with_ch = R.recompute_checks(B, chandelier=3.0, ch_period=14, ch_out=ch_times)
base = R.recompute_checks(B)
far = R.recompute_checks(B, chandelier=1000.0)
k = lambda hhmm: (D16, hhmm)
check("B1 long at the 10:00 check", with_ch.get(k('10:00')) == 1 and base.get(k('10:00')) == 1)
check("B2 the 11:00 check holds the long (no exit yet)", with_ch.get(k('11:00')) == 1 and base.get(k('11:00')) == 1)
check("B3 chandelier exit at 11:05 (desired 0, reported in ch_out)", with_ch.get(k('11:05')) == 0 and ch_times == [k('11:05')], str(ch_times))
check("B4 no 11:05 decision without the exit", k('11:05') not in base and k('11:05') not in far)
check("B5 re-entry long at the 11:30 check after the exit", with_ch.get(k('11:30')) == 1)
check("B6 without the exit 11:30 is a hold (still long)", base.get(k('11:30')) == 1)
check("B7 both flat at 16:00", with_ch.get(k('16:00')) == 0 and base.get(k('16:00')) == 0)
check("B8 1000 x ATR == the validated rules exactly", far == base, f"{len(far)} vs {len(base)} decisions")
check("B9 the exit adds exactly one decision to the day", len(with_ch) == len(base) + 1)
# the first-bar guard: ext starts at the fill bar's OPEN - a close at the fill bar below open - 3 ATR exits at once
cl2 = [100.1] * 5 + [100.5] + [98.0] + [101.5] * (NB - 7)
B2 = pd.DataFrame(rows[:15 * NB] + day(dates[15], cl2)); t2 = []
w2 = R.recompute_checks(B2, chandelier=3.0, ch_out=t2)
check("B10 a collapse on the fill bar itself exits on that bar (best close = the fill bar's open)", t2 == [k('10:05')] and w2.get(k('10:05')) == 0, str(t2))
# a short: mirror image
cl3 = [100.1] * 5 + [99.5] + [99.4 - 0.1 * x for x in range(12)] + [101.0] + [98.5] * (NB - 19)
B3 = pd.DataFrame(rows[:15 * NB] + day(dates[15], cl3)); t3 = []
w3 = R.recompute_checks(B3, chandelier=3.0, ch_out=t3)
check("B11 short entered at 10:00, chandelier exit at 11:05, re-entered short at 11:30", w3.get(k('10:00')) == -1 and t3 == [k('11:05')] and w3.get(k('11:30')) == -1, f"{w3.get(k('10:00'))} {t3} {w3.get(k('11:30'))}")


# ------------------------------------------------------------------ C. parity with the research engine on the live bars
def pnl_from_decisions(g, out, d):
    """Points per session from the review's decisions: fills at the next bar's open, the flat bar's close at the end (chval.py's fills)."""
    M = g.m.values; O = g.open.values; C = g.close.values; T = [x.strftime('%H:%M') for x in g.t]
    end = int(M[-1]); flat_min = 960 if end >= 960 else end - 5
    pos = 0; ref = 0.0; p = 0.0; n = 0
    for i in range(len(g)):
        want = out.get((d, T[i]))
        if want is None or want == pos: continue
        px = C[i] if (M[i] >= flat_min or i == len(g) - 1) else O[i + 1]
        p += pos * (px - ref); n += abs(want - pos); ref = px; pos = want
    return p, n / 2

src = os.path.join(os.path.dirname(HERE), 'NoiseArea_bars_MNQ_strategy_Sim101.csv')
try:
    sys.path.insert(0, os.path.join(HERE, 'research'))
    from research_engine import Market
    import strat3 as S3
    b = pd.read_csv(src).drop_duplicates('time_et', keep='last').sort_values('time_et').reset_index(drop=True)
    tmp = os.path.join(tempfile.gettempdir(), 'chandelier_test_bars.csv'); b.rename(columns={'time_et': 'time'}).to_csv(tmp, index=False)
    mk = Market(tmp, 1.0, 'LIVE')
    bb = b.copy(); bb['t'] = pd.to_datetime(bb.time_et); bb['m'] = bb.t.dt.hour * 60 + bb.t.dt.minute; bb['d'] = bb.t.dt.date
    rth = bb[(bb.m > 570) & (bb.m <= 960)]
    for label, ch in (('without the exit', None), ('with 3 x ATR(14)', 3.0)):
        p_eng, n_eng, _ = S3.run(mk, ch=ch); exits = []
        out = R.recompute_checks(b, chandelier=ch or 0.0, ch_period=14, ch_out=exits)
        worst = 0.0; n_cmp = 0; bad = []
        for kk, x in enumerate(mk.days):
            if np.isnan(p_eng[kk]): continue
            g = rth[rth.d == x.date]
            p_rev, n_rev = pnl_from_decisions(g, out, x.date); n_cmp += 1
            diff = abs(p_rev - p_eng[kk]); worst = max(worst, diff)
            if diff > 1e-6 or abs(n_rev - n_eng[kk]) > 1e-9: bad.append(f"{x.date} review {p_rev:+.2f}/{n_rev:g} engine {p_eng[kk]:+.2f}/{n_eng[kk]:g}")
        check(f"C parity {label}: {n_cmp} decidable sessions, P&L and trade counts equal", n_cmp > 0 and not bad,
              f"worst |diff| {worst:.2e} pts" + (f"; first differences: {'; '.join(bad[:4])}" if bad else ''))
        if ch:
            days = sorted(set(d for d, _ in exits))
            print(f"      chandelier exits in the live bars file: {len(exits)} on {len(days)} session(s): "
                  + ', '.join(f"{d} {t}" for d, t in exits) if exits else "      no chandelier exit in the live bars file")
except FileNotFoundError as e:
    print(f"SKIP  C parity: {e}")
except ImportError as e:
    print(f"SKIP  C parity: research modules not importable ({e})")

print("\nALL PASS" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
