import os, sys, itertools, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import naplus2 as N2
import naplus3 as N3
from merge_lab import boot_p

OUT = os.path.join(HERE, 'WFSTACK_RESULTS.txt')
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def main():
    say(f"wfstack.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (WFSTACK_PLAN.txt)")
    Q = {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = LS.full_bars(mk); Q[nm] = LS.per_day(mk, df, LS.ema_state(df.close.values))
    grid = list(itertools.product((0.0, 3.0), (False, True), (0, 4), (False, True), (False, True)))
    S = {}
    for ch, cr, gate, lunch, ema in grid:
        def f(mk, nm, w, ch=ch, cr=cr, gate=gate, lunch=lunch, ema=ema):
            return N3.engine3(mk, Q[nm] if cr else None, worst_out=w, gate=gate, lunch=lunch, ema=ema, ch=ch if ch else None)
        S[(ch, cr, gate, lunch, ema)] = N2.joined(f).r_close
    name = lambda g: f"ch{int(g[0])} {'X' if g[1] else '-'} gate{g[2]} {'L' if g[3] else '-'} {'E' if g[4] else '-'}"
    X3 = (3.0, True, 4, True, True); CH = (3.0, False, 0, False, False); NA = (0.0, False, 0, False, False)
    parts = []; picks = []
    idx = S[X3].index
    for y in range(2012, idx[-1].year + 1):
        a, b = pd.Timestamp(f'{y}-01-01'), pd.Timestamp(f'{y + 1}-01-01')
        best = max(S, key=lambda g: LS.sharpe(S[g][S[g].index < a]))
        picks.append((y, best)); parts.append(S[best][(S[best].index >= a) & (S[best].index < b)])
    wf = pd.concat(parts)
    say("picks: " + "; ".join(f"{y} {name(g)}" for y, g in picks))
    say(f"chooser picked X3 in {sum(g == X3 for _, g in picks)} of {len(picks)} years; switch on in picks: chandelier {sum(g[0] > 0 for _, g in picks)}, "
        f"crossover {sum(g[1] for _, g in picks)}, gate {sum(g[2] > 0 for _, g in picks)}, lunch {sum(g[3] for _, g in picks)}, EMA20 {sum(g[4] for _, g in picks)}")
    say("\nOUT OF SAMPLE 2012-01..2026-10, same days ($ per micro):")
    for lab, s in (('walk-forward chooser', wf), ('X3 fixed', S[X3].reindex(wf.index)), ('CH (live)', S[CH].reindex(wf.index)), ('N (published)', S[NA].reindex(wf.index))):
        t = N2.stats(s); say(f"   {lab:<22} Sharpe {t['sh']:.2f} | ${t['yr']:,.0f}/yr | max DD ${t['dd']:,.0f} | MAR {t['mar']:.2f} | worst year ${t['worst']:,.0f}")
    say(f"bootstrap P: chooser vs CH {boot_p(wf, S[CH].reindex(wf.index)):.4f} | chooser vs X3 {boot_p(wf, S[X3].reindex(wf.index)):.4f} | X3 vs CH {boot_p(S[X3].reindex(wf.index), S[CH].reindex(wf.index)):.4f}")
    rank = sorted(S, key=lambda g: -LS.sharpe(S[g])); pos = rank.index(X3) + 1
    say(f"full-sample rank of X3 among the 32: {pos}; top 5: " + "; ".join(f"{name(g)} {LS.sharpe(S[g]):.2f}" for g in rank[:5]))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
