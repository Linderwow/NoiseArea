import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import ema_lab as E

FAIL = []


def check(name, ok, info=''):
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{info}]" if not ok and info else '')); ok or FAIL.append(name)


def synth(n_days=1):
    m1 = np.arange(E.FIRST, E.CLOSE + 1, 5); nb = len(m1); rows = []
    for d in range(n_days):
        day = pd.Timestamp('2024-03-04') + pd.Timedelta(days=d)
        for k, mm in enumerate(m1): rows.append((day + pd.Timedelta(minutes=int(mm)), mm, day))
    t = np.array([r[0] for r in rows], dtype='datetime64[ns]'); m = np.array([r[1] for r in rows]); date = np.array([r[2] for r in rows], dtype='datetime64[ns]')
    n = len(t); o = 100.0 + np.arange(n) * 0.1; c = o + 0.05; h = o + 0.5; l = o - 0.5
    D = dict(nm='T', t=t, m=m, date=date, o=o, h=h, l=l, c=c, v=np.full(n, 100.0), e20=c.copy(), e50=np.zeros(n), e200=np.zeros(n),
             atr=np.full(n, 1.0), adx=np.full(n, 30.0), rsi=np.full(n, 60.0), hist=np.ones(n), vavg=np.full(n, 100.0), vwap=c - 1,
             htf=np.ones(n), rth=np.ones(n, bool), e9=c.copy(), e21=c.copy(), pdi=np.full(n, 30.0), mdi=np.full(n, 10.0), daily20=np.ones(n))
    return D, nb


def R(D, spec, **kw):
    return E.run(E.derive(D), spec, **kw)


D, nb = synth()
D['e50'][:] = -1.0; D['e200'][:] = 0.0; D['e50'][10:40] = 1.0
tr = R(D, {'entry': 'cross', 'exit': 'flip'}, ticks=0)
check("T1 cross up at bar 10 -> long at open 11; cross down at 40 -> flip at open 41, short from open 41 to the close",
      len(tr) == 2 and tr.side.tolist() == [1, -1] and np.isclose(tr.entry.iloc[0], D['o'][11]) and np.isclose(tr.exit.iloc[0], D['o'][41])
      and np.isclose(tr.entry.iloc[1], D['o'][41]) and np.isclose(tr.exit.iloc[1], D['c'][nb - 1]) and tr.why.tolist() == ['flip', 'close'], tr.to_string())
check("T2 zero ticks: $ = side x (exit/entry - 1) x 55,751 - $1 commission round trip", np.allclose(tr.usd, tr.side * (tr.exit / tr.entry - 1) * E.USD - 1.0))
tr2 = R(D, {'entry': 'cross', 'exit': 'flip'}, ticks=2.35)
check("T3 live cost 2 x (0.5 x 2.35 + 0.5) = $3.35 per round trip: $2.35 more than zero ticks", np.allclose(tr.usd - tr2.usd, 2.35))
D2, _ = synth(); D2['e50'][:] = 1.0; D2['e200'][:] = 0.0
tr = R(D2, {'entry': 'cross', 'exit': 'flip'}, ticks=0)
check("T4 no cross inside the session: no trade (an overnight cross is not an entry)", len(tr) == 0, tr.to_string())
tr = R(D2, {'entry': 'state', 'exit': 'flip'}, ticks=0)
check("T5 state mode: long from the first bar's next open to the close, one trade", len(tr) == 1 and np.isclose(tr.entry.iloc[0], D2['o'][1])
      and tr.why.iloc[0] == 'close', tr.to_string())
D3, _ = synth(); D3['e50'][:] = 1.0; D3['e200'][:] = 0.0; D3['l'][20] = D3['o'][1] - 3.0
tr = R(D3, {'entry': 'state', 'exit': 'flip', 'stop': 1.5, 'reenter': False}, ticks=0)
check("T6 ATR stop 1.5 hit at bar 20 -> exit at the stop price, no re-entry when reenter is off",
      len(tr) == 1 and tr.why.iloc[0] == 'stop' and np.isclose(tr.exit.iloc[0], D3['o'][1] - 1.5), tr.to_string())
D4, _ = synth(); D4['e50'][:] = 1.0; D4['e200'][:] = 0.0; D4['o'][20] = D4['o'][1] - 5.0; D4['l'][20] = D4['o'][20] - 0.5
tr = R(D4, {'entry': 'state', 'exit': 'flip', 'stop': 1.5, 'reenter': False}, ticks=0)
check("T7 gap through the stop -> filled at the bar's open, not the stop", np.isclose(tr.exit.iloc[0], D4['o'][20]), tr.to_string())
D5, _ = synth(); D5['e50'][:] = 1.0; D5['e200'][:] = 0.0; D5['h'][15] = D5['o'][1] + 10; D5['l'][15] = D5['o'][1] - 10
tr = R(D5, {'entry': 'state', 'exit': 'flipbracket', 'stop': 1.5, 'target': 3.0, 'reenter': False}, ticks=0)
check("T8 stop and target in the same bar -> the stop counts (conservative)", tr.why.iloc[0] == 'stop', tr.to_string())
D6, _ = synth(); D6['e50'][:] = 1.0; D6['e200'][:] = 0.0; D6['adx'][:] = 10.0
check("T9 ADX filter blocks entries when ADX <= 25", len(R(D6, {'entry': 'state', 'filters': ('adx25',)}, ticks=0)) == 0)
D7, _ = synth(); D7['e50'][:] = 1.0; D7['e200'][:] = 0.0; D7['m'] = D7['m'].copy()
tr = R(D7, {'entry': 'cross', 'exit': 'flip'}, ticks=0); D7['e50'][:] = -1.0; D7['e50'][70:] = 1.0
tr = R(D7, {'entry': 'cross', 'exit': 'flip'}, ticks=0)
check("T10 no new entry after the 15:30 decision (cross at bar 70 = 15:25? no: bar 70 ends 15:25+) -> entry only if <= 15:30",
      all(D7['m'][np.searchsorted(D7['o'], e) - 1] <= E.LAST_ENTRY for e in tr.entry), tr.to_string())

D8, _ = synth(); D8['e50'][:] = -1.0; D8['e200'][:] = 0.0; D8['e50'][5:] = 1.0
D8['l'][8] = 0.0; D8['c'][8] = 1000.0; D8['l'][12] = 0.0; D8['c'][12] = 1000.0
D8E = E.derive(D8)
check("T11 first pullback after the cross: only the first touch-and-close-above counts (bar 8, not 12)",
      bool(D8E['firstpull'][8]) and not bool(D8E['firstpull'][12]))
D9, _ = synth(); D9['e50'][:] = 1.0; D9['e200'][:] = 0.0; D9['e50'][:3] = -1.0; D9['e200'][:] = D9['o'] - 2.0
D9['e50'][3:] = D9['o'][3:] + 100
tr = R(D9, {'entry': 'cross', 'exit': 'fh', 'stop': ('e200tick',)}, ticks=0)
r = tr.groupby('why').qty.sum().to_dict() if len(tr) else {}
check("T12 half at 1.5R then breakeven: two half legs, the first at its 1.5R target", len(tr) == 2 and abs(tr.qty.sum() - 1.0) < 1e-9
      and 'target' in r, tr.to_string())
D10, _ = synth(); D10['e200'] = D10['o'] - 1.0; D10['e50'] = D10['e200'] + 1.0; D10['e50'][:3] = D10['e200'][:3] - 1.0; D10['l'][30] = D10['e200'][29] - 5
tr = R(D10, {'entry': 'cross', 'exit': 'none', 'stop': ('trail200',)}, ticks=0)
check("T13 trailing stop 2 ticks under the previous bar's EMA200: exits at bar 30 at that level",
      len(tr) == 1 and tr.why.iloc[0] == 'stop' and np.isclose(tr.exit.iloc[0], D10['e200'][29] - 0.5), tr.to_string())
D11, _ = synth(); D11['e50'][:] = 1.0; D11['e200'][:] = 0.0; D11['e50'][:3] = -1.0
tr = R(D11, {'entry': 'cross', 'exit': 'time10'}, ticks=0)
check("T14 time exit: the cross at bar 3 enters at open 4 and leaves at the open after 10 bars held",
      len(tr) == 1 and tr.why.iloc[0] == 'time' and np.isclose(tr.exit.iloc[0], D11['o'][14]), tr.to_string())
D12, _ = synth(); D12['e50'][:] = 1.0; D12['e200'][:] = 0.0; D12['e50'][:20] = -1.0; D12['l'][10] = 50.0
tr = R(D12, {'entry': 'cross', 'exit': 'flip', 'stop': ('swing', 15)}, ticks=0)
check("T15 swing stop = lowest low of the last 15 bars at the signal (bar 10's 50.0 is inside bars 6-20)",
      len(tr) == 1 and tr.why.iloc[0] == 'close', tr.to_string())

print("ALL PASS" if not FAIL else f"{len(FAIL)} FAILED: {FAIL}")
sys.exit(1 if FAIL else 0)
