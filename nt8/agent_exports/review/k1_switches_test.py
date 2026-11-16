import os, sys, tempfile, shutil, subprocess
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import daily_review as R

fails = 0
def check(name, ok, detail=''):
    global fails
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail and (not ok or len(detail) <= 160) else ''))
    if not ok: fails += 1


def rth(day, o930, closes, first_min=575):
    t0 = pd.Timestamp(day) + pd.Timedelta(minutes=first_min); rows = []; prev = o930
    for i, c in enumerate(closes):
        rows.append({'time_et': (t0 + pd.Timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M:%S'), 'open': prev,
                     'high': max(prev, c), 'low': min(prev, c), 'close': c, 'volume': 100})
        prev = c
    return rows


def overnight(day, px, n=185):
    t0 = pd.Timestamp(day) - pd.Timedelta(days=1) + pd.Timedelta(hours=18, minutes=5)
    return [{'time_et': (t0 + pd.Timedelta(minutes=5 * i)).strftime('%Y-%m-%d %H:%M:%S'), 'open': px, 'high': px, 'low': px,
             'close': px, 'volume': 10} for i in range(n)]


DAYS = pd.bdate_range('2026-03-02', periods=26)
MOVES = [0.03, -0.01, 0.02, -0.03] * 5


def frame(test_closes, last_hist=None, extra=None):
    rows = []
    for k in range(20):
        closes = [round(100 * (1 + MOVES[k]), 4)] * 78
        if k == 19 and last_hist is not None: closes = list(last_hist)
        rows += rth(DAYS[k], 100.0, closes)
    if extra: rows += extra
    rows += rth(DAYS[21], 100.0, test_closes)
    return pd.DataFrame(rows), DAYS[21].date()


def at(hhmm):
    h, m = int(hhmm[:2]), int(hhmm[3:])
    return (h * 60 + m - 575) // 5


def closes_with(base, changes):
    c = [base] * 78
    for k, v in changes.items(): c[at(k) if isinstance(k, str) else k] = v
    return c


def seq(base, spans):
    c = [base] * 78
    for a, b, v in spans:
        for i in range(at(a), at(b) + 1): c[i] = v
    return c


long_day = seq(100.0, [('10:00', '10:00', 105.0), ('10:05', '10:15', 101.0), ('10:20', '10:20', 106.0), ('10:25', '16:00', 101.0)])
B, d = frame(long_day)
off = R.recompute_checks(B)
g4 = []; on4 = R.recompute_checks(B, exit_gate=4, gate_out=g4)
g3 = []; on3 = R.recompute_checks(B, exit_gate=3, gate_out=g3)
check("A1 precondition, gate off: LONG at 10:00, the 10:30 check exits (close below max(upper|VWAP))",
      off.get((d, '10:00')) == 1 and off.get((d, '10:30')) == 0, f"{off.get((d, '10:00'))} {off.get((d, '10:30'))}")
check("A2 gate 4: three closes below the line, a close above resets the count, the 10:30 check HOLDS, the 4th close in a row (10:40) exits",
      on4.get((d, '10:00')) == 1 and on4.get((d, '10:30')) == 1 and on4.get((d, '10:40')) == 0 and g4 == [(d, '10:40')]
      and (d, '10:15') not in on4, f"10:30 {on4.get((d, '10:30'))} 10:40 {on4.get((d, '10:40'))} exits {g4}")
check("A3 gate 3: exits at the third close in a row (10:15), before the reset", on3.get((d, '10:15')) == 0 and g3[:1] == [(d, '10:15')], str(g3))
check("A4 after a gate exit the later checks may enter again (flat, close inside the band -> stays flat)",
      all(v == 0 for (dd, t), v in on4.items() if dd == d and t > '10:40'))

short_day = seq(100.0, [('10:00', '10:00', 93.0), ('10:05', '10:15', 96.0), ('10:20', '10:20', 92.0), ('10:25', '16:00', 96.0)])
Bs, ds = frame(short_day)
gs = []; ons = R.recompute_checks(Bs, exit_gate=4, gate_out=gs); offs = R.recompute_checks(Bs)
check("A5 short side mirrored: SHORT at 10:00, gate off exits at 10:30, gate 4 holds at 10:30 and exits at 10:40 (closes above min(lower|VWAP))",
      offs.get((ds, '10:00')) == -1 and offs.get((ds, '10:30')) == 0 and ons.get((ds, '10:30')) == -1 and gs == [(ds, '10:40')],
      f"{offs.get((ds, '10:30'))} {ons.get((ds, '10:30'))} {gs}")

rev_day = seq(100.0, [('10:00', '10:25', 105.0), ('10:30', '16:00', 93.0)])
Br, dr = frame(rev_day)
gr = []; onr = R.recompute_checks(Br, exit_gate=4, gate_out=gr); offr = R.recompute_checks(Br)
check("A6 gate 4: no reversal at a check - gate off reverses LONG->SHORT at 10:30, gate on holds the long and leaves through the gate at 10:45",
      offr.get((dr, '10:30')) == -1 and onr.get((dr, '10:30')) == 1 and gr == [(dr, '10:45')] and onr.get((dr, '10:45')) == 0,
      f"off {offr.get((dr, '10:30'))} on {onr.get((dr, '10:30'))} exits {gr}")
che = []; chg = []; both = R.recompute_checks(Br, exit_gate=4, chandelier=0.5, ch_out=che, gate_out=chg)
check("A7 the chandelier is checked first on every bar: with a tight chandelier the 10:30 bar leaves through it and the gate never fires",
      che[:1] == [(dr, '10:30')] and chg == [], f"chandelier {che} gate {chg}")

lunch_day = seq(100.0, [('12:00', '16:00', 105.0)])
Bl, dl = frame(lunch_day)
lo = []; onl = R.recompute_checks(Bl, lunch_pause=True, lunch_out=lo); offl = R.recompute_checks(Bl)
check("B1 lunch pause off: LONG from flat at 12:00", offl.get((dl, '12:00')) == 1, str(offl.get((dl, '12:00'))))
check("B2 lunch pause on: the entries at 12:00, 12:30, 13:00 are blocked, the 13:30 check enters",
      lo == [(dl, '12:00'), (dl, '12:30'), (dl, '13:00')] and onl.get((dl, '13:00')) == 0 and onl.get((dl, '13:30')) == 1, f"{lo} 13:30 {onl.get((dl, '13:30'))}")
lrev = seq(100.0, [('10:00', '11:55', 105.0), ('12:00', '16:00', 93.0)])
Blr, dlr = frame(lrev)
lr = []; onlr = R.recompute_checks(Blr, lunch_pause=True, lunch_out=lr)
check("B3 lunch pause never blocks a reversal or an exit: LONG 10:00, SHORT at 12:00 with the pause on",
      onlr.get((dlr, '10:00')) == 1 and onlr.get((dlr, '12:00')) == -1 and lr == [], f"{onlr.get((dlr, '12:00'))} {lr}")


def ema_ref(bars):
    b = bars.copy(); b['t'] = pd.to_datetime(b.time_et); b['m'] = b.t.dt.hour * 60 + b.t.dt.minute; b['d'] = b.t.dt.date
    r = b[(b.m > 570) & (b.m <= 960)]; e = None; out = {}
    for dd, g in r.groupby('d', sort=True):
        if len(g) < 30 or g.m.iloc[0] != 575: continue
        for t, c in zip(g.t, g.close.values):
            e = c if e is None else e * (1 - 2 / 21) + c * (2 / 21); out[t.strftime('%Y-%m-%d %H:%M')] = e
    return out


high_prev = [120.0] * 77 + [97.0]
Be, de = frame(closes_with(100.0, {'10:00': 105.0}), last_hist=high_prev)
E = ema_ref(Be)
eo = []; one = R.recompute_checks(Be, ema20=True, ema_out=eo); offe = R.recompute_checks(Be)
check("C1 precondition: LONG at 10:00 without the switch, and the close (105) is below EMA(20) there",
      offe.get((de, '10:00')) == 1 and 105.0 < E[f"{de} 10:00"], f"pos {offe.get((de, '10:00'))} EMA {E[f'{de} 10:00']:.4f}")
check("C2 EMA(20) confirmation blocks the long below the EMA and the day stays flat", one.get((de, '10:00')) == 0 and (de, '10:00') in eo
      and all(v == 0 for (dd, t), v in one.items() if dd == de), str(eo))
low_prev = [80.0] * 77 + [100.0]
Bes, des = frame(closes_with(100.0, {'10:00': 93.0}), last_hist=low_prev)
Es = ema_ref(Bes); eso = []; ones = R.recompute_checks(Bes, ema20=True, ema_out=eso); offes = R.recompute_checks(Bes)
check("C3 short side: SHORT at 10:00 without the switch; the close is above EMA(20), so the switch blocks it",
      offes.get((des, '10:00')) == -1 and 93.0 > Es[f"{des} 10:00"] and ones.get((des, '10:00')) == 0 and (des, '10:00') in eso,
      f"EMA {Es[f'{des} 10:00']:.4f} {eso}")
Bok, dok = frame(closes_with(100.0, {'10:00': 105.0}))
ok_ = R.recompute_checks(Bok, ema20=True, ema_out=(eok := []))
check("C4 a long above EMA(20) is taken (precondition: close 105 above the EMA)", ok_.get((dok, '10:00')) == 1 and eok == []
      and 105.0 > ema_ref(Bok)[f"{dok} 10:00"])
rv = seq(100.0, [('10:00', '10:00', 105.0), ('10:05', '10:25', 60.0), ('10:30', '16:00', 93.0)])
Brv, drv = frame(rv)
Erv = ema_ref(Brv); rvo = []; onrv = R.recompute_checks(Brv, ema20=True, ema_out=rvo); offrv = R.recompute_checks(Brv)
check("C5 a reversal against EMA(20) goes flat: LONG->SHORT at 10:30 without the switch, LONG->FLAT with it (close above the EMA)",
      offrv.get((drv, '10:30')) == -1 and onrv.get((drv, '10:30')) == 0 and (drv, '10:30') in rvo and 93.0 > Erv[f"{drv} 10:30"],
      f"{offrv.get((drv, '10:30'))} {onrv.get((drv, '10:30'))} EMA {Erv[f'{drv} 10:30']:.4f}")
noise = rth(DAYS[20], 100.0, [1000.0] * 70, first_min=580) + overnight(DAYS[21], 1000.0)
Bn, dn = frame(closes_with(100.0, {'10:00': 105.0}), last_hist=high_prev, extra=noise)
n_out = R.recompute_checks(Bn, ema20=True, ema_out=(no_ := []))
check("C6 EMA(20) uses RTH closes of complete sessions only: overnight bars at 1000 and a session starting 09:40 at 1000 change nothing",
      n_out == one and no_ == eo)
check("C7 EMA20 smoothing constant is 2/21 (= nav_lab._ema20)", R.EMA20_ALPHA == 2 / 21)

ch_a = []; tf_a = []; ch_b = []; tf_b = []
for nm, F_ in (('gate day', B), ('reversal day', Br), ('lunch day', Bl), ('EMA day', Be)):
    same = (R.recompute_checks(F_) == R.recompute_checks(F_, exit_gate=0, lunch_pause=False, ema20=False)
            and R.recompute_checks(F_, chandelier=3.0, trend_fast=5, trend_slow=20, ch_out=ch_a, tf_out=tf_a)
            == R.recompute_checks(F_, chandelier=3.0, trend_fast=5, trend_slow=20, ch_out=ch_b, tf_out=tf_b, exit_gate=0, lunch_pause=False, ema20=False))
    check(f"D1 all three switches off == the rules without them ({nm}, also with chandelier 3 + trend filter 5/20)", same and ch_a == ch_b and tf_a == tf_b)

with tempfile.TemporaryDirectory() as tmp:
    ev = os.path.join(tmp, 'NoiseArea_events_MNQ_X.txt')
    open(ev, 'w').write("2026-11-09 09:00:00|live|X|contracts 1, chandelier 3 x ATR(14), trend filter EMA 50/200 on, exit gate 4 bars, lunch pause on, EMA(20) confirmation on\n")
    a1 = R.strategy_k1(tmp, 'X')
    open(ev, 'a').write("2026-11-10 09:00:00|live|X|contracts 1, chandelier 3 x ATR(14), trend filter EMA 50/200 off, exit gate off, lunch pause off, EMA(20) confirmation on\n")
    a2 = R.strategy_k1(tmp, 'X')
    open(ev, 'a').write("2026-11-11 09:00:00|live|X|contracts 1, chandelier 3 x ATR(14), trend filter EMA 50/200 off\n")
    a3 = R.strategy_k1(tmp, 'X')
    check("E1 strategy_k1 reads the last 'live' event: 4/on/on, off/off/on, an older build without the switches, no file",
          a1 == (4, True, True, True) and a2 == (0, False, True, True) and a3 == (0, False, False, False) and R.strategy_k1(tmp, 'Y') is None,
          f"{a1} {a2} {a3}")
check("E2 k1_text names the registered switches", R.k1_text(4, 1, 1) == "the exit gate (4 bars), the lunch pause and EMA(20) confirmation"
      and R.k1_text(0, 1, 0) == "the lunch pause" and R.k1_text(4, 0, 1) == "the exit gate (4 bars) and EMA(20) confirmation", R.k1_text(4, 1, 1))

x3_day = seq(100.0, [('10:00', '10:00', 105.0), ('10:05', '10:15', 101.0), ('10:20', '10:20', 106.0), ('10:25', '11:55', 101.0), ('12:00', '16:00', 105.0)])
BX, dX = frame(x3_day)
gX, lX, eX = [], [], []
X = R.recompute_checks(BX, exit_gate=4, lunch_pause=True, ema20=True, gate_out=gX, lunch_out=lX, ema_out=eX)
check("E3 precondition for the review runs: gate exit at 10:40, the 12:00 / 12:30 / 13:00 re-entries blocked by the lunch pause, LONG at 13:30",
      gX == [(dX, '10:40')] and lX == [(dX, '12:00'), (dX, '12:30'), (dX, '13:00')] and X.get((dX, '13:30')) == 1, f"{gX} {lX} {X.get((dX, '13:30'))}")


def review(live_text, args, ind_gate=None):
    tmp = tempfile.mkdtemp(prefix='k1t_'); acct = 'T1'
    BX.to_csv(os.path.join(tmp, f'NoiseArea_bars_MNQ_strategy_{acct}.csv'), index=False)
    rows = []; prev = 0
    for (dd, hhmm), v in sorted(X.items()):
        if dd != dX or hhmm > '15:30': continue
        if (dd, hhmm) in gX: reason = 'exit: gate 4 bars beyond max(upper|VWAP)'
        elif (dd, hhmm) in lX: reason = 'above upper band - blocked: lunch pause'
        else: reason = 'hold' if v == prev else ('above upper band' if v == 1 else 'below lower band' if v == -1 else 'exit')
        if ind_gate is not None and (dd, hhmm) in gX: hhmm = ind_gate
        rows.append({'date': str(dd), 'time_et': hhmm, 'price': 0, 'upper': 0, 'lower': 0, 'sigma': 0, 'vwap': 0, 'pos_before': prev, 'pos_after': v, 'reason': reason})
        prev = v
    pd.DataFrame(rows).to_csv(os.path.join(tmp, f'NoiseArea_checks_MNQ_strategy_{acct}.csv'), index=False)
    with open(os.path.join(tmp, f'NoiseArea_events_MNQ_{acct}.txt'), 'w', encoding='utf-8') as fh:
        fh.write(f"{dX} 09:00:00|live|{acct}|contracts 1, daily loss stop 0, history sessions 14, chandelier 0 x ATR(14){live_text}\n")
    with open(os.path.join(tmp, f'NoiseArea_status_MNQ_{acct}.json'), 'w', encoding='utf-8') as fh:
        fh.write('{"state":"terminated","updated_et":"%s 16:10:00","position":"Flat","quantity":0,"account_position":0,"history_sessions":14}' % dX)
    p = subprocess.run([sys.executable, '-B', os.path.join(HERE, 'daily_review.py'), tmp, '--account', acct, '--date', str(dX)] + args,
                       capture_output=True, text=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
    shutil.rmtree(tmp, ignore_errors=True)
    return p.stdout + p.stderr


ALL = ['--exit-gate', '4', '--lunch-pause', '1', '--ema20-confirm', '1']
LIVE_ON = ', trend filter EMA 50/200 off, exit gate 4 bars, lunch pause on, EMA(20) confirmation on'
out = review(LIVE_ON, ALL)
good = ('exit gate 4 bars: 1 gate exit today at 10:40 - the independent recomputation agrees' in out
        and 'lunch pause: 3 entries blocked today at 12:00, 12:30, 13:00 - the independent recomputation agrees' in out
        and 'EMA(20) confirmation: no new leg blocked today' in out and 'decisions today match an independent recomputation' in out
        and 'differ' not in out and 'registered with' not in out)
check("F1 registered on + build on: the recomputation applies all three, decisions match, gate exit and lunch blocks confirmed", good, out[-1200:])
out = review(', trend filter EMA 50/200 off', ALL)
check("F2 registered on + a build without the switches: WARNING to deploy staged\\20261109_k1_switches, decisions compared without them",
      'registered with the exit gate (4 bars), the lunch pause and EMA(20) confirmation but the running build has none' in out and '20261109_k1_switches' in out,
      out[-900:])
out = review(', trend filter EMA 50/200 off, exit gate off, lunch pause on, EMA(20) confirmation on', ALL)
check("F3 registered gate 4 + build gate off: NEEDS ATTENTION naming the setting", 'NEEDS ATTENTION' in out
      and "runs with the exit gate off but T1 is registered with exit_gate 4" in out, out[-900:])
out = review(', trend filter EMA 50/200 off, exit gate 4 bars, lunch pause off, EMA(20) confirmation off', ALL)
check("F4 build lunch / EMA(20) off vs registered on: NEEDS ATTENTION for each", 'NEEDS ATTENTION' in out
      and 'runs with the lunch pause off but T1 is registered with lunch_pause 1' in out
      and 'runs with EMA(20) confirmation off but T1 is registered with ema20_confirm 1' in out, out[-900:])
out = review(LIVE_ON, [])
check("F5 build on + not registered: NEEDS ATTENTION (mismatch on all three)", 'NEEDS ATTENTION' in out and 'registered with exit_gate 0' in out
      and 'registered with lunch_pause 0' in out and 'registered with ema20_confirm 0' in out, out[-900:])
out = review(LIVE_ON, ALL, ind_gate='10:35')
check("F6 the indicator's gate exit at another bar than the rules': NEEDS ATTENTION 'gate exits differ'", 'NEEDS ATTENTION' in out
      and 'exit gate 4 bars: gate exits differ from the independent recomputation: indicator 10:35 vs rules 10:40' in out, out[-900:])
out = review(', trend filter EMA 50/200 off, exit gate off, lunch pause off, EMA(20) confirmation off', [])
check("F7 off on both sides: no exit-gate / lunch / EMA(20) line at all (the review reads as before)",
      'exit gate' not in out and 'lunch pause' not in out and 'EMA(20)' not in out and 'k1_switches' not in out, out[-600:])


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
    import weekend as WK
    b = pd.read_csv(src).drop_duplicates('time_et', keep='last').sort_values('time_et').reset_index(drop=True)
    tmp = os.path.join(tempfile.gettempdir(), 'k1_switches_test_bars.csv'); b.rename(columns={'time_et': 'time'}).to_csv(tmp, index=False)
    mk = Market(tmp, 1.0, 'K1LIVE'); fb = LS.full_bars(mk); q8 = LS.per_day(mk, fb, LS.ema_state(fb.close.values))
    conf = lambda k, i, new: bool(q8[k][i] == new)
    bb = b.copy(); bb['t'] = pd.to_datetime(bb.time_et); bb['m'] = bb.t.dt.hour * 60 + bb.t.dt.minute; bb['d'] = bb.t.dt.date
    rth_b = bb[(bb.m > 570) & (bb.m <= 960)]; groups = {dd: g for dd, g in rth_b.groupby('d', sort=True)}
    cases = (('X3: gate 4 + lunch + EMA(20) + chandelier 3 + trend 50/200', dict(gate=4, lunch=True, ema=True, ch=3.0, conf=conf, conf_mode='skip'),
              dict(exit_gate=4, lunch_pause=True, ema20=True, chandelier=3.0, trend_fast=50, trend_slow=200)),
             ('gate 4 + chandelier 3', dict(gate=4, ch=3.0), dict(exit_gate=4, chandelier=3.0)),
             ('lunch pause + chandelier 3', dict(lunch=True, ch=3.0), dict(lunch_pause=True, chandelier=3.0)),
             ('EMA(20) + chandelier 3', dict(ema=True, ch=3.0), dict(ema20=True, chandelier=3.0)))
    for label, ek, rk in cases:
        p_eng, n_eng = WK.engine(mk, **ek)[:2]; marks = {'g': [], 'l': [], 'e': []}
        out = R.recompute_checks(b, ch_period=14, gate_out=marks['g'], lunch_out=marks['l'], ema_out=marks['e'], **rk)
        worst = 0.0; n_cmp = 0; bad = []
        for kk, xd in enumerate(mk.days):
            if np.isnan(p_eng[kk]): continue
            p_rev, n_rev = pnl_from_decisions(groups[xd.date], out, xd.date); n_cmp += 1
            diff = abs(p_rev - p_eng[kk]); worst = max(worst, diff)
            if diff > 1e-9 or abs(n_rev - n_eng[kk]) > 1e-12: bad.append(f"{xd.date} review {p_rev:+.2f}/{n_rev:g} engine {p_eng[kk]:+.2f}/{n_eng[kk]:g}")
        check(f"G parity with research\\weekend.py engine() on the live bars file, {label}: {n_cmp} decidable sessions, points and trade counts equal",
              n_cmp > 0 and not bad, f"worst |diff| {worst:.1e} pts; gate exits {len(marks['g'])}, lunch blocks {len(marks['l'])}, EMA(20) blocks {len(marks['e'])}"
              + (f"; first differences: {'; '.join(bad[:4])}" if bad else ''))
    try: os.remove(tmp)
    except OSError: pass
except FileNotFoundError as e:
    print(f"SKIP  G parity: {e}")
except ImportError as e:
    print(f"SKIP  G parity: research modules not importable ({e})")

print("\nALL PASS" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
