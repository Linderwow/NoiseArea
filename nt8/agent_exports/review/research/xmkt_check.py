r"""Checks of xmkt.py (XMKT_PLAN.txt): a result of "nothing" must be shown not to come from the machinery.
    python xmkt_check.py  -> xmkt_check_output.txt
 1. Reference: the same pipeline on MNQ (NinjaTrader bars) must find NoiseArea's known edge (family A) in 2019-2022.
 2. Zero-cost diagnostic (skill section 3): discovery t of every rule gross of all costs - signal killed by costs vs no signal.
 3. LH re-implemented with pandas on the raw 5-minute file for MES and MGC - per-session P&L identical?
 4. Cells with 0 trades and markets with missing sessions: why.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import xmkt as X

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def main():
    from research_engine import Market
    import nav_lab as N
    mnq = Market(N.FILES['MNQ'], 1.0, 'MNQref')
    ref = {}
    for rule in ['LH'] + X.FAMS:
        s, n, f = X.rule_series(mnq, rule, (2.0, 0.25, 0.61)); d = s[s.index <= X.DISC_END]; ref[rule] = X.tstat(d.values)
    pr("1. MNQ reference (same pipeline, Bulenox costs), discovery t: " + ', '.join(f"{k} {v:+.2f}" for k, v in ref.items()))
    rows = []
    for nm, spec in X.SPEC.items():
        mk = Market(os.path.join(X.BARS, f'{nm.lower()}_5m.csv'), 1.0, nm)
        for rule in ['LH'] + X.FAMS:
            s, n, f = X.rule_series(mk, rule, (spec[0], spec[1], 0.0), ticks=0); d = s[s.index <= X.DISC_END]
            rows.append((nm, rule, X.tstat(d.values), int(n.sum())))
        # 3. LH with pandas
        if nm in ('MES', 'MGC'):
            b = pd.read_csv(os.path.join(X.BARS, f'{nm.lower()}_5m.csv'), parse_dates=['time']).set_index('time')
            lab, _ = X.lh(mk); own = {}
            for k, x in enumerate(mk.days):
                if np.isnan(mk.pc[k]) or x.M[-1] != 960: continue
                day = pd.Timestamp(x.date); c10 = b.close.get(day + pd.Timedelta(hours=10)); o35 = b.open.get(day + pd.Timedelta(hours=15, minutes=35))
                c16 = b.close.get(day + pd.Timedelta(hours=16))
                if c10 is None or o35 is None or c16 is None or np.isnan(c10): own[k] = 0.0; continue
                sgn = np.sign(c10 - mk.pc[k]); own[k] = sgn * (c16 - o35)
            dif = max(abs(own[k] - lab[k]) for k in own)
            pr(f"3. {nm} LH: {len(own)} sessions re-computed from the raw file, max |difference| {dif:.6g} points")
        pr(f"4. {nm}: {mk.n} sessions with a 09:30 first bar and >= 30 RTH bars; zero-trade rules: "
           f"{[r for (m_, r, t_, n_) in rows if m_ == nm and n_ == 0]}")
    g = pd.DataFrame(rows, columns=['market', 'rule', 't_gross', 'trades'])
    pr("2. Zero-cost discovery t (gross), best 12: " + ' | '.join(f"{r.market} {r.rule} {r.t_gross:+.2f}" for r in g.sort_values('t_gross', ascending=False).head(12).itertuples()))
    pr(f"   gross t >= 2.0: {int((g.t_gross >= 2.0).sum())} of {len(g)}; median gross t {g.t_gross.median():+.2f}")
    g.to_csv(os.path.join(HERE, 'xmkt_gross.csv'), index=False)
    with open(os.path.join(HERE, 'xmkt_check_output.txt'), 'w', encoding='utf-8') as fo: fo.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
