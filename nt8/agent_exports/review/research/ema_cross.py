import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import ema_lab as E

OUT = os.path.join(HERE, 'EMACROSS_RESULTS.txt')
LIVE = 2.35
RULES = {
    'R0': ('trend state', {'entry': 'state', 'exit': 'flip'}),
    'R1': ('pure cross, reverse', {'entry': 'cross', 'exit': 'flip'}),
    'R2': ('cross + 15-bar swing stop', {'entry': 'cross', 'exit': 'flip', 'stop': ('swing', 15)}),
    'R3': ('cross + 2xATR bracket', {'entry': 'cross', 'exit': 'none', 'stop': 2.0, 'target': 2.0}),
    'R4': ('cross + ADX20 + daily SMA20', {'entry': 'cross', 'filters': ('adx20', 'daily20'), 'exit': 'flip', 'stop': ('e200atr', 1.5)}),
    'R5': ('EMA50 reclaim + ADX/DI', {'entry': 'reclaim', 'filters': ('adx20', 'di'), 'exit': 'none', 'stop': 1.5, 'target': 3.0}),
    'R6': ('pullback EMA50 + RSI50', {'entry': 'pullback', 'filters': ('rsi50',), 'exit': 'none', 'stop': 1.5, 'target': ('R', 2.0)}),
    'R7': ('armed pullback, trail EMA200', {'entry': 'pullback_armed', 'exit': 'none', 'stop': ('trail200',)}),
    'R8': ('first pullback, VWAP, 1.5R/3R', {'entry': 'firstpull', 'filters': ('vwap', 'e50side'), 'exit': 'fh', 'stop': ('e200tick',)}),
    'R9': ('9/21 in 50/200 trend + VWAP', {'entry': 'fast921', 'filters': ('vwap',), 'exit': 'fast'}),
    'R10': ('cross + 10-bar time exit', {'entry': 'cross', 'exit': 'time10'}),
    'R11': ('cross with band + delay', {'entry': 'sepcross', 'exit': 'flip'}),
    'R12': ('confluence pullback', {'entry': 'pullback', 'filters': ('stack', 'adx25', 'vwap', 'htf', 'macd'), 'exit': 'none', 'stop': 1.5, 'target': 3.0}),
}
BAR = NormalDist().inv_cdf(1 - 0.05 / len(RULES))
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def tstat(x):
    sd = x.std(ddof=1); return x.mean() / (sd / np.sqrt(len(x))) if sd > 0 else float('nan')


def main():
    say(f"ema_cross.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (EMACROSS_PLAN.txt)  live cost {LIVE} ticks + $0.50 a side; G1 bar t >= {BAR:.2f}")
    S = {}; G = {}; TR = {}
    for nm in ('NQ', 'MNQ', 'ES', 'MES'):
        D = E.load(nm); dd = E.days(D); rth = D['rth'] & (D['m'] > E.FIRST)
        cpd = float(D['cross'][rth].sum()) / len(dd)
        say(f"{nm}: {len(dd):,} RTH sessions {min(dd):%Y-%m-%d}..{max(dd):%Y-%m-%d}; 50/200 crosses per RTH session {cpd:.2f}")
        assert 0.2 <= cpd <= 2.0, cpd
        for r, (_, spec) in RULES.items():
            tr = E.run(D, spec, ticks=LIVE); TR[(nm, r)] = tr
            S[(nm, r)] = E.daily(tr, D); G[(nm, r)] = E.daily(tr, D, 'gross')
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close * E.USD
    say(); say("PER RULE (live cost, $ per micro at today's price):  NQ 2010-19 | MNQ 2019-26 holdout | ES / MES | gross before cost")
    survivors = []
    for r, (desc, _) in RULES.items():
        nq, mnq, es, mes = (S[(m, r)] for m in ('NQ', 'MNQ', 'ES', 'MES'))
        both = pd.concat([nq, mnq]); yrs = both.groupby(both.index.year).sum()
        d5 = both.drop(both.sort_values(ascending=False).index[:5])
        tn = TR[('NQ', r)]; tm = TR[('MNQ', r)]
        ntr = (len(tn) + len(tm)) / ((len(nq) + len(mnq)) / 252)
        gp = (G[('NQ', r)].sum() + G[('MNQ', r)].sum()) / max(1, (tn.qty.sum() + tm.qty.sum()))
        corr = both.reindex(x3.index).dropna().corr(x3.reindex(both.index).dropna()) if len(both) else float('nan')
        g = {'G1': tstat(nq) >= BAR, 'G2': mnq.mean() > 0 and tstat(mnq) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2,
             'G4': es.mean() > 0 and mes.mean() > 0, 'G5': tstat(d5) >= 2.0}
        st = E.stats(both)
        say(f"{r:3s} {desc:30s} NQ ${nq.mean() * 252:7,.0f}/yr t {tstat(nq):5.2f} | MNQ ${mnq.mean() * 252:7,.0f}/yr t {tstat(mnq):5.2f} | "
            f"ES ${es.mean() * 252:6,.0f} MES ${mes.mean() * 252:6,.0f} | {ntr:4.0f} trades/yr, gross ${gp:+6.2f}/trade vs $3.35 cost | "
            f"losing years {int((yrs < 0).sum())}/{len(yrs)} | DD ${st['dd']:,.0f} | corr X3 {corr:+.2f}")
        fails = [k for k, v in g.items() if not v]
        say(f"      VERDICT {r}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(r)
    say(); say("ZERO-COST DIAGNOSTIC (gross $/yr, NQ | MNQ): is there signal before the cost?")
    for r in RULES:
        gn, gm = G[('NQ', r)], G[('MNQ', r)]
        say(f"   {r:3s} NQ ${gn.mean() * 252:7,.0f} (t {tstat(gn):5.2f}) | MNQ ${gm.mean() * 252:7,.0f} (t {tstat(gm):5.2f})")
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
