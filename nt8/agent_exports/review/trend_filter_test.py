import os, sys, tempfile
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import daily_review as R

fails = 0
def check(name, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ''))
    if not ok: fails += 1


def nt_ema_reference(x, n):
    out = []
    for i, v in enumerate(x):
        out.append(v if i == 0 else v * (2.0 / (1 + n)) + (1 - 2.0 / (1 + n)) * out[-1])
    return np.array(out)


rng = np.random.default_rng(20261108)
x = 20000 + np.cumsum(rng.normal(0, 5, 3000))
ref = nt_ema_reference(x, 50)
check("A1 nt_ema == NinjaTrader's @EMA.cs formula (Input x 2/(1+n) + (1 - 2/(1+n)) x previous, seeded with the first input)",
      np.array_equal(R.nt_ema(x, 50), ref), f"max |diff| {np.abs(R.nt_ema(x, 50) - ref).max():.1e}")
pdv = pd.Series(x).ewm(span=200, adjust=False).mean().values
check("A2 nt_ema == pandas ewm(span=n, adjust=False) (the research engine's EMA)", np.abs(R.nt_ema(x, 200) - pdv).max() < 1e-9,
      f"max |diff| {np.abs(R.nt_ema(x, 200) - pdv).max():.1e}")
t = pd.date_range('2026-01-05 18:05', periods=len(x), freq='5min')
fr = pd.DataFrame({'time_et': t.strftime('%Y-%m-%d %H:%M:%S'), 'close': x})
want = np.sign(nt_ema_reference(x, 50) - nt_ema_reference(x, 200))
shuf = fr.sample(frac=1.0, random_state=7)
got = pd.Series(R.ema_trend(shuf, 50, 200), index=shuf.index).sort_index().values
check("A3 ema_trend follows time order whatever the row order of the frame", np.array_equal(got, want))


def rth(day, o930, closes):
    t0 = pd.Timestamp(day) + pd.Timedelta(hours=9, minutes=35); rows = []; prev = o930
    for i, c in enumerate(closes):
        rows.append({'time_et': (t0 + pd.Timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M:%S'), 'open': prev,
                     'high': max(prev, c), 'low': min(prev, c), 'close': c, 'volume': 100})
        prev = c
    return rows


def overnight(day, px, n=185):
    t0 = pd.Timestamp(day) - pd.Timedelta(days=1) + pd.Timedelta(hours=18, minutes=5)
    return [{'time_et': (t0 + pd.Timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M:%S'), 'open': px, 'high': px, 'low': px,
             'close': px, 'volume': 10} for i in range(n)]


days = pd.bdate_range('2026-03-02', periods=22); rows = []
moves = [0.03, -0.01, 0.02, -0.03] * 5
for k in range(20): rows += rth(days[k], 100.0, [round(100 * (1 + moves[k]), 4)] * 78)
rows += overnight(days[20], 130.0) + rth(days[20], 100.0, [100.0] * 5 + [105.0] * 73)
rows += overnight(days[21], 70.0) + rth(days[21], 100.0, [100.0] * 5 + [110.0] * 6 + [90.0] * 67)
B = pd.DataFrame(rows)
d1, d2 = days[20].date(), days[21].date()
base = R.recompute_checks(B)
blocked = []
filt = R.recompute_checks(B, trend_fast=5, trend_slow=20, tf_out=blocked)
tr = pd.Series(R.ema_trend(B, 5, 20), index=B.time_et)
def tr_at(d, hhmm): return int(tr[f"{d} {hhmm}:00"])
check("B1 precondition: EMA 5/20 is down at 10:00 on the entry day and up at 10:30 on the reversal day",
      tr_at(d1, '10:00') == -1 and tr_at(d2, '10:00') == 1 and tr_at(d2, '10:30') == 1 and tr_at(d2, '11:00') == -1,
      f"{tr_at(d1, '10:00')} {tr_at(d2, '10:00')} {tr_at(d2, '10:30')} {tr_at(d2, '11:00')}")
check("B2 filter off: LONG at 10:00 on the entry day", base.get((d1, '10:00')) == 1, str(base.get((d1, '10:00'))))
check("B3 filter on: the 10:00 entry against the trend is blocked and stays flat, every later check of that day too",
      filt.get((d1, '10:00')) == 0 and all(v == 0 for (d, _), v in filt.items() if d == d1) and (d1, '10:00') in blocked)
check("B4 filter off: LONG at 10:00, reversal to SHORT at 10:30 on the second day",
      base.get((d2, '10:00')) == 1 and base.get((d2, '10:30')) == -1, f"{base.get((d2, '10:00'))} {base.get((d2, '10:30'))}")
check("B5 filter on: LONG at 10:00 with the trend, the 10:30 reversal against it goes FLAT, SHORT at 11:00 once the trend turns down",
      filt.get((d2, '10:00')) == 1 and filt.get((d2, '10:30')) == 0 and filt.get((d2, '11:00')) == -1 and (d2, '10:30') in blocked
      and (d2, '11:00') not in blocked, f"{filt.get((d2, '10:00'))} {filt.get((d2, '10:30'))} {filt.get((d2, '11:00'))} blocked {[t for d, t in blocked if d == d2]}")
check("B6 exits are never blocked: the flat at 16:00 after the 11:00 short", filt.get((d2, '16:00')) == 0, str(filt.get((d2, '16:00'))))
check("B7 trend_fast / trend_slow 0 == the rules without the filter exactly", R.recompute_checks(B, trend_fast=0, trend_slow=0) == base)
ch_a, ch_b = [], []
check("B8 chandelier alone unchanged by the new arguments", R.recompute_checks(B, chandelier=3.0, ch_out=ch_a) ==
      R.recompute_checks(B, chandelier=3.0, ch_out=ch_b, trend_fast=0, trend_slow=0) and ch_a == ch_b)


def review(live_text, args):
    tmp = tempfile.mkdtemp(prefix='tft_'); acct = 'T1'
    B.to_csv(os.path.join(tmp, f'NoiseArea_bars_MNQ_strategy_{acct}.csv'), index=False)
    rows = []; prev = 0
    for (d, hhmm), v in sorted(filt.items()):
        if d != d2 or hhmm > '15:30': continue
        reason = 'hold' if v == prev else ('above upper band' if v == 1 else 'below lower band' if v == -1 else 'exit')
        if (d, hhmm) in blocked: reason = 'reverse: below lower band - blocked: EMA 5/20 up'
        rows.append({'date': str(d), 'time_et': hhmm, 'price': 0, 'upper': 0, 'lower': 0, 'sigma': 0, 'vwap': 0, 'pos_before': prev, 'pos_after': v, 'reason': reason})
        prev = v
    pd.DataFrame(rows).to_csv(os.path.join(tmp, f'NoiseArea_checks_MNQ_strategy_{acct}.csv'), index=False)
    with open(os.path.join(tmp, f'NoiseArea_events_MNQ_{acct}.txt'), 'w', encoding='utf-8') as fh:
        fh.write(f"{d2} 09:00:00|live|{acct}|contracts 1, daily loss stop 0, history sessions 14, chandelier 0 x ATR(14){live_text}\n")
    with open(os.path.join(tmp, f'NoiseArea_status_MNQ_{acct}.json'), 'w', encoding='utf-8') as fh:
        fh.write('{"state":"terminated","updated_et":"%s 16:10:00","position":"Flat","quantity":0,"account_position":0,"history_sessions":14}' % d2)
    import subprocess
    p = subprocess.run([sys.executable, '-B', os.path.join(HERE, 'daily_review.py'), tmp, '--account', acct, '--date', str(d2)] + args,
                       capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    import shutil; shutil.rmtree(tmp, ignore_errors=True)
    return p.stdout + p.stderr


with tempfile.TemporaryDirectory() as tmp:
    ev = os.path.join(tmp, 'NoiseArea_events_MNQ_X.txt')
    open(ev, 'w').write("2026-11-09 09:00:00|live|X|contracts 1, chandelier 3 x ATR(14), trend filter EMA 50/200 on\n")
    a1 = R.strategy_trend(tmp, 'X')
    open(ev, 'a').write("2026-11-10 09:00:00|live|X|contracts 1, chandelier 3 x ATR(14), trend filter EMA 40/160 off\n")
    a2 = R.strategy_trend(tmp, 'X')
    open(ev, 'a').write("2026-11-11 09:00:00|live|X|contracts 1, chandelier 3 x ATR(14)\n")
    a3 = R.strategy_trend(tmp, 'X')
    check("D1 strategy_trend reads the last 'live' event: on 50/200, off 40/160, an older build (no filter) and no file",
          a1 == (True, 50, 200, True) and a2 == (False, 40, 160, True) and a3 == (False, 50, 200, False) and R.strategy_trend(tmp, 'Y') is None,
          f"{a1} {a2} {a3}")
out = review(', trend filter EMA 5/20 on', ['--trend-filter', '1', '--trend-fast', '5', '--trend-slow', '20'])
good = ('trend filter EMA 5/20: blocked 1 new leg(s) today at 10:30 - the independent recomputation agrees' in out
        and 'decisions today match an independent recomputation' in out and 'differ' not in out)
check("D2 registered on + build on: the recomputation applies the filter, decisions match, the blocked reversal is confirmed", good, '' if good else out[-900:])
out = review('', ['--trend-filter', '1', '--trend-fast', '5', '--trend-slow', '20'])
check("D3 registered on + a build without the filter: a WARNING to deploy staged\\20261109_k1_switches",
      'registered with the trend filter (EMA 5/20) but the running build has none' in out and '20261109_k1_switches' in out)
out = review(', trend filter EMA 5/20 off', ['--trend-filter', '1', '--trend-fast', '5', '--trend-slow', '20'])
check("D4 registered on + build off: NEEDS ATTENTION (mismatch)", 'NEEDS ATTENTION' in out and 'runs with the trend filter off but T1 is registered with trend_filter 1' in out)
out = review(', trend filter EMA 5/20 on', [])
check("D5 build on + not registered: NEEDS ATTENTION (mismatch)", 'NEEDS ATTENTION' in out and 'runs with the trend filter on but T1 is registered with trend_filter 0' in out)
out = review(', trend filter EMA 50/200 on', ['--trend-filter', '1', '--trend-fast', '5', '--trend-slow', '20'])
check("D6 build on with other EMA lengths than registered: NEEDS ATTENTION", 'runs the trend filter with EMA 50/200 but T1 is registered with EMA 5/20' in out)
out = review(', trend filter EMA 5/20 off', [])
check("D7 off on both sides: no trend-filter line at all (the review reads as before)", 'trend filter' not in out.lower() and 'trend_filter' not in out)


def pnl_from_decisions(g, out, d):
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
    import livestack as LS
    b = pd.read_csv(src).drop_duplicates('time_et', keep='last').sort_values('time_et').reset_index(drop=True)
    tmp = os.path.join(tempfile.gettempdir(), 'trend_filter_test_bars.csv'); b.rename(columns={'time_et': 'time'}).to_csv(tmp, index=False)
    mk = Market(tmp, 1.0, 'LIVE'); q8 = LS.per_day(mk, LS.full_bars(mk), LS.ema_state(LS.full_bars(mk).close.values))
    bb = b.copy(); bb['t'] = pd.to_datetime(bb.time_et); bb['m'] = bb.t.dt.hour * 60 + bb.t.dt.minute; bb['d'] = bb.t.dt.date
    rth_b = bb[(bb.m > 570) & (bb.m <= 960)]
    for label, ch in (('EMA 50/200', None), ('EMA 50/200 + chandelier 3 x ATR(14)', 3.0)):
        p_eng, n_eng = LS.run(mk, ch=ch, filt=q8); stops = []
        out = R.recompute_checks(b, chandelier=ch or 0.0, ch_period=14, trend_fast=50, trend_slow=200, tf_out=stops)
        worst = 0.0; n_cmp = 0; bad = []
        for kk, xd in enumerate(mk.days):
            if np.isnan(p_eng[kk]): continue
            p_rev, n_rev = pnl_from_decisions(rth_b[rth_b.d == xd.date], out, xd.date); n_cmp += 1
            diff = abs(p_rev - p_eng[kk]); worst = max(worst, diff)
            if diff > 1e-9 or abs(n_rev - n_eng[kk]) > 1e-12: bad.append(f"{xd.date} review {p_rev:+.2f}/{n_rev:g} engine {p_eng[kk]:+.2f}/{n_eng[kk]:g}")
        check(f"C parity with research\\livestack.py run(filt=Q8) on the live bars file, {label}: {n_cmp} decidable sessions, P&L and trade counts equal",
              n_cmp > 0 and not bad, f"worst |diff| {worst:.1e} pts, {len(stops)} blocked legs" + (f"; first differences: {'; '.join(bad[:4])}" if bad else ''))
except FileNotFoundError as e:
    print(f"SKIP  C parity: {e}")
except ImportError as e:
    print(f"SKIP  C parity: research modules not importable ({e})")

print("\nALL PASS" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
