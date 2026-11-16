import os, sys, itertools, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import naplus2 as N2
import naplus3 as N3
from research_engine import WARMUP
from merge_lab import boot_p

OUT = os.path.join(HERE, 'OPT2_RESULTS.txt')
USD = 55751.0
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def states(mk, a, b):
    df = LS.full_bars(mk); return LS.per_day(mk, df, LS.ema_state(df.close.values, a, b))


def part_a():
    say("=" * 118); say("A. WALK-FORWARD PARAMETER CHOOSER (27 configurations; lunch pause and EMA(20) on)"); say("=" * 118)
    spans = ((40, 160), (50, 200), (60, 240))
    Q = {(nm, sp): states(N.market(nm), *sp) for nm in ('NQ', 'MNQ') for sp in spans}
    grid = list(itertools.product((3, 4, 6), (2.0, 3.0, 4.0), spans))
    S = {g: N2.joined(lambda mk, nm, w, g=g: N3.engine3(mk, Q[(nm, g[2])], gate=g[0], ch=g[1], worst_out=w)).r_close for g in grid}
    X3 = (4, 3.0, (50, 200)); name = lambda g: f"gate{g[0]} ch{g[1]:g} {g[2][0]}/{g[2][1]}"
    parts = []; picks = []; idx = S[X3].index
    for y in range(2012, idx[-1].year + 1):
        a, b = pd.Timestamp(f'{y}-01-01'), pd.Timestamp(f'{y + 1}-01-01')
        best = max(S, key=lambda g: LS.sharpe(S[g][S[g].index < a]))
        picks.append((y, best)); parts.append(S[best][(S[best].index >= a) & (S[best].index < b)])
    wf = pd.concat(parts); x3 = S[X3].reindex(wf.index)
    say("picks: " + "; ".join(f"{y} {name(g)}" for y, g in picks))
    say(f"X3's settings picked in {sum(g == X3 for _, g in picks)} of {len(picks)} years")
    tw, tx = N2.stats(wf), N2.stats(x3); p = boot_p(wf, x3)
    say(f"OOS 2012-2026: chooser Sharpe {tw['sh']:.2f}, ${tw['yr']:,.0f}/yr, max DD ${tw['dd']:,.0f}, MAR {tw['mar']:.2f} | X3 fixed Sharpe {tx['sh']:.2f}, "
        f"${tx['yr']:,.0f}/yr, max DD ${tx['dd']:,.0f}, MAR {tx['mar']:.2f} | P(chooser <= X3) {p:.4f}")
    adopt = p < 0.05 and tw['dd'] <= tx['dd']
    rank = sorted(S, key=lambda g: -LS.sharpe(S[g]))
    say(f"full-sample rank of X3 among 27: {rank.index(X3) + 1}; top 3 (in-sample, never adopted): " +
        "; ".join(f"{name(g)} {LS.sharpe(S[g]):.2f}" for g in rank[:3]))
    say(f"VERDICT A: {'ADOPT the chooser' if adopt else 'X3 settings stay (the chooser does not beat them out of sample)'}")


def leg_series(mk, pnl, nt, px_ref, ticks, pv_cost):
    d = mk.dates; sel = np.arange(len(d)) >= WARMUP
    o = np.array([x.O[0] for x in mk.days])
    c = (0.25 * ticks + pv_cost) / px_ref
    return pd.Series(np.nan_to_num(pnl[sel]) / o[sel] - nt[sel] * 2 * c, index=d[sel])


def part_b():
    say(); say("=" * 118); say("B. X3 ON NASDAQ + S&P, HALF SIZE EACH, SAME-DIRECTION GUARD (Nasdaq priority) vs NASDAQ ALONE"); say("=" * 118)
    p_mnq = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    mes_df = LS.full_bars(N.market('MES')); p_mes = float(mes_df.close.iloc[-1])
    say(f"cost reference prices: MNQ {p_mnq:,.0f} ($2/pt, $0.50 a side), MES {p_mes:,.0f} ($5/pt, $0.50 a side)")
    out = {}
    for ticks in (1, 2):
        legs = {'nas': [], 'sp': [], 'blend': [], 'sp_free': []}; guard_acts = 0; corr = []
        for nas, sp, lo, hi in (('NQ', 'ES', '2010-07-01', '2019-05-05'), ('MNQ', 'MES', '2019-05-06', '2026-12-31')):
            mn, ms = N.market(nas), N.market(sp)
            qn, qs = states(mn, 50, 200), states(ms, 50, 200)
            pos = {}; pn, nn = N3.engine3(mn, qn, pos_out=pos)
            ps, ns = N3.engine3(ms, qs, guard=pos); pf, nf = N3.engine3(ms, qs)
            a = leg_series(mn, pn, nn, p_mnq, ticks, 0.25); b = leg_series(ms, ps, ns, p_mes, ticks, 0.1); f = leg_series(ms, pf, nf, p_mes, ticks, 0.1)
            keep = lambda s: s[(s.index >= pd.Timestamp(lo)) & (s.index <= pd.Timestamp(hi))]
            a, b, f = keep(a), keep(b), keep(f)
            ix = a.index.union(b.index); a, b, f = a.reindex(ix, fill_value=0.0), b.reindex(ix, fill_value=0.0), f.reindex(ix, fill_value=0.0)
            legs['nas'].append(a); legs['sp'].append(b); legs['sp_free'].append(f); legs['blend'].append(0.5 * a + 0.5 * b)
            guard_acts += int(((ps - pf) != 0)[~np.isnan(ps - pf)].sum()); corr.append(float(np.corrcoef(a, f)[0, 1]))
        out[ticks] = {k: v for k, v in legs.items()}
        if ticks == 1:
            say(f"daily P&L correlation Nasdaq leg vs S&P leg (unguarded): 2010-19 {corr[0]:+.2f}, 2019-26 {corr[1]:+.2f}; sessions where the guard "
                f"changed the S&P leg's P&L: {guard_acts}")
    ok = True
    for ticks in (1, 2):
        nas = pd.concat(out[ticks]['nas']); bl = pd.concat(out[ticks]['blend']); sp = pd.concat(out[ticks]['sp'])
        tn, tb, ts = N2.stats(nas), N2.stats(bl), N2.stats(sp); p = boot_p(bl, nas)
        era = [(LS.sharpe(bl[m]), LS.sharpe(nas[m])) for m in (bl.index <= N.JOIN, bl.index > N.JOIN)]
        say(f"{ticks} tick(s): Nasdaq alone Sharpe {tn['sh']:.2f}, ${tn['yr']:,.0f}/yr, max DD ${tn['dd']:,.0f}, MAR {tn['mar']:.2f} | "
            f"S&P leg alone {ts['sh']:.2f} | BLEND {tb['sh']:.2f}, ${tb['yr']:,.0f}/yr, max DD ${tb['dd']:,.0f}, MAR {tb['mar']:.2f}, worst year "
            f"${tb['worst']:,.0f} vs ${tn['worst']:,.0f} | P(blend <= Nasdaq) {p:.4f} | eras 2010-19 {era[0][0]:.2f}/{era[0][1]:.2f}, 2019-26 {era[1][0]:.2f}/{era[1][1]:.2f}")
        ok &= p < 0.05 and all(x > y for x, y in era) and tb['dd'] < tn['dd'] and tb['mar'] > tn['mar']
    say(f"VERDICT B: {'PASS - X3 on both indices beats Nasdaq alone' if ok else 'DEAD - the blend does not beat Nasdaq alone by the registered bar'}")


if __name__ == '__main__':
    say(f"opt2.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (OPT2_PLAN.txt)")
    part_a(); part_b()
    say(f"run {time.time() - T0:.0f} s")
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
