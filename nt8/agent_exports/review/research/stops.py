"""Stop-loss / profit-target overlay on the validated NoiseArea rules, per STOPS_PLAN.txt.  python3 stops.py"""
import itertools, sys
import numpy as np, pandas as pd
from research_engine import Market, fam_A, stats, WARMUP, TICK


def fam_A_st(mk, S=None, T=None, policy='reenter', fixed_pts=None, vm=1.0, look=14, slip=TICK, comm=0.5, tgt_mode='touch', stop_extra=0.0):
    """Per-bar rewrite of fam_A plus intrabar stop/target. S, T in units of the noise width at the entry check (None = off);
    fixed_pts = stop in points instead of noise units. Returns daily pnl, entries, and exit-type counts."""
    sig = mk.sigma(look); pv = mk.pv; checks = set(range(600, 931, 30)); tick_ = slip if slip > 0 else TICK
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n, int); ex = dict(stop=0, target=0, check=0, close=0)
    for k, x in enumerate(mk.days):
        if k < look or np.isnan(mk.pc[k]):
            continue
        O, H, L, C, M, vw, nb = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb
        hi = max(O[0], mk.pc[k]); lo = min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        pos = 0; entry = 0.0; p = 0.0; n_ = 0; stp = tgt = None; pending = 0; blocked = False
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        for i in range(flat_i + 1):
            # 1. a pending entry decided at the previous bar's close fills at this bar's open
            if pending != 0:
                pos = pending; entry = O[i] + slip * pos; p -= comm; n_ += 1; pending = 0
                w = hi * vm * sg[i - 1]
                sd = fixed_pts if fixed_pts is not None else (S * w if S is not None else None)
                stp = None if sd is None else (entry - sd if pos == 1 else entry + sd)
                tgt = None if T is None else (entry + T * w if pos == 1 else entry - T * w)
                first = True
            else:
                first = False
            if i == flat_i:
                break
            # 2. intrabar stop / target on this bar (stop wins a tie)
            if pos != 0 and (stp is not None or tgt is not None):
                hit = None
                thr = tick_ if tgt_mode == 'through' else 0.0
                if pos == 1:
                    if stp is not None and L[i] <= stp: hit = (stp if (first or O[i] > stp) else O[i]) - slip - stop_extra
                    elif tgt is not None and H[i] >= tgt + thr: hit = tgt - (slip if tgt_mode == 'market' else 0.0)
                else:
                    if stp is not None and H[i] >= stp: hit = (stp if (first or O[i] < stp) else O[i]) + slip + stop_extra
                    elif tgt is not None and L[i] <= tgt - thr: hit = tgt + (slip if tgt_mode == 'market' else 0.0)
                if hit is not None:
                    is_stop = (pos == 1 and stp is not None and L[i] <= stp) or (pos == -1 and stp is not None and H[i] >= stp)
                    p += (hit - entry) * pos * pv - comm; ex['stop' if is_stop else 'target'] += 1
                    pos = 0; stp = tgt = None
                    if policy == 'none': blocked = True
            # 3. the 30-minute check at this bar's close
            if M[i] in checks and not np.isnan(sg[i]):
                c = C[i]; ub = hi * (1 + vm * sg[i]); lb = lo * (1 - vm * sg[i])
                if pos == 0:
                    new = 0 if blocked else (1 if c > ub else (-1 if c < lb else 0))
                elif pos == 1:
                    new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else:
                    new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                if blocked and new != 0 and pos == 0: new = 0
                if new != pos:
                    if pos != 0:
                        p += (O[i + 1] - slip * pos - entry) * pos * pv - comm; ex['check'] += 1; pos = 0; stp = tgt = None
                    if new != 0 and not blocked: pending = new
                    elif new != 0 and blocked: pending = 0
        if pos != 0:
            p += (C[flat_i] - slip * pos - entry) * pos * pv - comm; ex['close'] += 1
        pnl[k] = p; nt[k] = n_
    return pnl, nt, ex


def run(mk, a, b, **kw):
    pnl, nt, ex = fam_A_st(mk, **kw); s = stats(mk, pnl, nt, a, b); s['ex'] = ex; return s


if __name__ == '__main__':
    out = []
    for lab, csv in (('NT', 'nt_analyzer_bars.csv'), ('DB', 'mnq_db_5m.csv')):
        mk = Market(csv, 2.0, 'MNQ')
        # correctness: no stop, no target must equal fam_A to the cent
        p0, n0 = fam_A(mk, vm=1.0, slip=TICK, comm=0.5); p1, n1, _ = fam_A_st(mk)
        d = np.nan_to_num(p0) - np.nan_to_num(p1)
        print(f"{lab}: overlay engine vs validated engine with no stop/target: max |diff| ${np.abs(d).max():.4f}, entries equal {bool((n0 == n1).all())}")
        assert np.abs(d).max() < 0.005 and (n0 == n1).all()
        end = str(mk.dates[-1].date())
        base_d = run(mk, '2019-01-01', '2022-12-31'); base_v = run(mk, '2023-01-01', end)
        out.append(dict(ds=lab, S='none', T='none', policy='base', d_net=base_d['net'], d_t=base_d['t'], v_net=base_v['net'], v_t=base_v['t'], v_mdd=base_v['mdd'], v_trades=base_v['trades'], ex=base_v['ex']))
        for S, T, pol in itertools.product([0.25, 0.5, 0.75, 1.0, 1.5, 2.0, None], [0.5, 1.0, 1.5, 2.0, 3.0, None], ['reenter', 'none']):
            if S is None and T is None: continue
            dd = run(mk, '2019-01-01', '2022-12-31', S=S, T=T, policy=pol); vv = run(mk, '2023-01-01', end, S=S, T=T, policy=pol)
            out.append(dict(ds=lab, S=S if S is not None else 'none', T=T if T is not None else 'none', policy=pol, d_net=dd['net'], d_t=dd['t'], v_net=vv['net'], v_t=vv['t'], v_mdd=vv['mdd'], v_trades=vv['trades'], ex=vv['ex']))
        for pts in (50, 100, 200):
            dd = run(mk, '2019-01-01', '2022-12-31', fixed_pts=pts); vv = run(mk, '2023-01-01', end, fixed_pts=pts)
            out.append(dict(ds=lab, S=f'${pts*2}', T='none', policy='reenter', d_net=dd['net'], d_t=dd['t'], v_net=vv['net'], v_t=vv['t'], v_mdd=vv['mdd'], v_trades=vv['trades'], ex=vv['ex']))
    df = pd.DataFrame(out); df.to_csv('stops_results.csv', index=False)
    for lab in ('NT', 'DB'):
        g = df[df.ds == lab]; b = g[g.policy == 'base'].iloc[0]
        print(f"\n=== {lab}: baseline discovery ${b.d_net:,.0f} (t {b.d_t:.2f}) | validation ${b.v_net:,.0f} (t {b.v_t:.2f}) maxDD ${b.v_mdd:,.0f} trades {b.v_trades} exits {b.ex}")
        g = g[g.policy != 'base'].copy()
        g['beats_all'] = (g.d_net > b.d_net) & (g.d_t > b.d_t) & (g.v_net > b.v_net) & (g.v_t > b.v_t)
        print(f"variants beating the baseline on net AND t in discovery AND validation: {int(g.beats_all.sum())} of {len(g)}")
        print("top 8 by validation t:"); print(g.sort_values('v_t', ascending=False).head(8)[['S', 'T', 'policy', 'd_net', 'd_t', 'v_net', 'v_t', 'v_mdd', 'v_trades']].to_string(index=False))
        print("stop-only variants (T none, reenter):"); print(g[(g['T'] == 'none') & (g.policy == 'reenter')][['S', 'd_net', 'd_t', 'v_net', 'v_t', 'v_mdd', 'v_trades', 'ex']].to_string(index=False))
        print("target-only variants (S none, reenter):"); print(g[(g.S == 'none') & (g.policy == 'reenter')][['T', 'd_net', 'd_t', 'v_net', 'v_t', 'v_mdd', 'v_trades', 'ex']].to_string(index=False))
