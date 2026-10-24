r"""FIRMS (FIRMS_PLAN.txt, 2026-10-24): NoiseArea under the rules of the prop firms that allow automated NinjaTrader strategies -
Bulenox, MyFundedFutures, Lucid, FundedNext - evaluation micros and funded sizing chosen per firm; a projection.
    python firms.py   -> firms_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import biglever as B
import sizes as S
import masterscale as MS

SEED = B.SEED_REPORT; H_EVAL = 252; ND = B.N_FUNDED
FIRMS = {
    'Bulenox 50K Option 2': dict(ev=dict(price=175.0, reset=78.0, window=21, reset_min_left=5, target=3000.0, dd=2500.0, lock=None, dll=1100.0, cons=None, min_days=0),
                                 fund=dict(mode='bulenox', act=148.0, dd=2500.0), slots=5),
    'MyFundedFutures Rapid EOD 50K': dict(ev=dict(price=135.0, reset=None, window=None, target=3000.0, dd=2000.0, lock=100.0, dll=None, cons=0.30, min_days=4),
                                          fund=dict(mode='mffu', act=0.0, dd=2000.0, buffer=2100.0, minpay=500.0, split=0.90), slots=3),
    'Lucid Flex 50K': dict(ev=dict(price=130.0, reset=80.75, window=None, target=3000.0, dd=2000.0, lock=100.0, dll=None, cons=0.40, min_days=0),
                           fund=dict(mode='cycle', act=0.0, dd=2000.0, winday=150.0, ndays=5, cyc_min=0.01, frac=0.5, cap=2000.0, minpay=500.0,
                                     split=0.90, max_pay=5), slots=5),
    'FundedNext Flex 50K': dict(ev=dict(price=84.99, reset=82.99, window=None, target=2500.0, dd=1500.0, lock=100.0, dll=None, cons=0.40, min_days=0),
                                fund=dict(mode='cycle', act=0.0, dd=1500.0, winday=200.0, ndays=5, cyc_min=500.0, frac=0.5, cap=1500.0, minpay=250.0,
                                          split=0.95, max_pay=5), slots=5)}
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def evaluation(X1, W1, NT, q, e, attempts=None):
    """Attempts one after another until a pass (within the horizon): end-of-day threshold trailing the closing high (capped at
    e['lock'] if set), breached intraday; DLL as biglever.day; the target counts only with the consistency rule (best day <=
    cons x profit) and the minimum trading days; a breach is reset (if e['reset'] and, with a window, >= reset_min_left sessions
    left) or the attempt ends; a new attempt is bought until `attempts` are used. Returns (passed, pass session, fees)."""
    n, h = X1.shape; fees = np.full(n, e['price']); used = np.ones(n, int)
    bal = np.zeros(n); hi = np.zeros(n); best = np.zeros(n); days = np.zeros(n, int); t_in = np.zeros(n, int)
    active = np.ones(n, bool); passed = np.zeros(n, bool); when = np.full(n, h)
    def restart(m, clock=True):                 # a reset clears profit and trading days but not the access window (Bulenox)
        bal[m] = 0.0; hi[m] = 0.0; best[m] = 0.0; days[m] = 0
        if clock: t_in[m] = 0
    for t in range(h):
        x, w = B.day(X1[:, t] * q, W1[:, t] * q, e['dll'])
        thr = hi - e['dd'] if e['lock'] is None else np.minimum(hi - e['dd'], e['lock'])
        br = active & (bal + w < thr); bal = np.where(active & ~br, bal + x, bal)
        ok_day = active & ~br; days += (ok_day & (NT[:, t] > 0)).astype(int); best = np.where(ok_day, np.maximum(best, x), best)
        if e['reset'] is not None:
            rs = br & ((e['window'] - 1 - t_in) >= e['reset_min_left'] if e['window'] else True)
            fees[rs] += e['reset']; restart(rs, clock=False); br &= ~rs
        hi = np.where(active & ~br, np.maximum(hi, bal), hi)
        hit = active & ~br & (bal >= e['target']) & (days >= e['min_days']) & ((best <= e['cons'] * bal) if e['cons'] else True)
        passed |= hit; when[hit] = t; active &= ~hit
        t_in[active] += 1
        over = br | (active & (t_in >= e['window'])) if e['window'] else br
        again = over & ((used < attempts) if attempts else True)
        fees[again] += e['price']; used[again] += 1; restart(again)
        active &= ~(over & ~again)
    return passed, when, fees


def funded(X1, W1, NT, f, sizing):
    """2-year payouts to the trader on one funded account; sizing(cushion) -> micros."""
    if f['mode'] == 'bulenox':
        pay, ou, _ = MS.master_dyn(X1, W1, NT, S.P50, sizing); return pay.sum(1), ou
    n, h = X1.shape; tot = np.zeros(n); ou = np.full(n, h)
    bal = np.zeros(n); hi = np.zeros(n); thr = np.full(n, -f['dd']); locked = np.zeros(n, bool); alive = np.ones(n, bool)
    n_pay = np.zeros(n, int); wins = np.zeros(n, int); cyc = np.zeros(n)
    for t in range(h):
        q = sizing(bal - thr); x, w = B.day(X1[:, t] * q, W1[:, t] * q, None)
        x = np.where(alive, x, 0.0); w = np.where(alive, w, 0.0)
        br = alive & (bal + w < thr); ou[br] = t; alive &= ~br; bal += x
        hi = np.where(alive, np.maximum(hi, bal), hi)
        up = alive & ~locked; thr = np.where(up, np.maximum(thr, hi - f['dd']), thr); lk = up & (thr >= 100); thr[lk] = 100.0; locked |= lk
        if f['mode'] == 'mffu':
            amt = bal - f['buffer']; el = alive & (amt >= f['minpay'])
        else:
            wins += (alive & (x >= f['winday'])).astype(int); cyc = np.where(alive, cyc + x, cyc)
            amt = np.minimum(f['frac'] * bal, f['cap']); el = alive & (wins >= f['ndays']) & (cyc >= f['cyc_min']) & (amt >= f['minpay'])
        bal[el] -= amt[el]; tot[el] += f['split'] * amt[el]; n_pay[el] += 1; wins[el] = 0; cyc[el] = 0.0
        thr[el] = np.maximum(thr[el], 100.0); locked |= el                     # the first payout fixes the threshold at +$100
        if f.get('max_pay'): done = alive & (n_pay >= f['max_pay']); ou[done] = t; alive &= ~done
    return tot, ou


def sizings(dd):
    return {'fixed 1': lambda cu: np.ones_like(cu), 'fixed 2': lambda cu: np.full_like(cu, 2.0),
            f'cushion 1 per ${dd:,.0f}': lambda cu, dd=dd: np.clip(np.floor(cu / dd), 1, 4)}


def main():
    x0, w0, nt0, idx = S.base_series('intraday_worst_extended.csv')
    pr(f"FIRMS (FIRMS_PLAN.txt): {len(x0)} sessions {idx[0].date()} .. {idx[-1].date()} (extended), MNQ 29,150, $0.61 a side for every firm; "
       f"{B.N_PATHS:,} paths, seed {SEED}; evaluation horizon {H_EVAL} sessions, funded {ND}")
    x, w = S.scenario('A base', x0, w0, nt0)
    X, W, NT = B.paths(B.N_WIN, x, w, nt0, 1, SEED); Xq, Wq, _ = B.paths(B.N_WIN, x, w, nt0, 6, SEED)
    e = dict(FIRMS['Bulenox 50K Option 2']['ev']); ok, nres = B.evaluation(Xq, Wq, e['dd'], e['dll'], e['target'], True)
    p, _, fees = evaluation(X, W, NT, 6, e, attempts=1)
    assert np.array_equal(p, ok) and np.allclose(fees, e['price'] + e['reset'] * nres), "evaluation audit failed"
    pr("audit: the evaluation engine with Bulenox's window, reset rule and no consistency == biglever.evaluation (passes and resets)")
    rows = {}
    for sc in ('A base', 'C half edge'):
        x, w = S.scenario(sc, x0, w0, nt0)
        XE, WE, NTE = B.paths(H_EVAL, x, w, nt0, 1, SEED); XF, WF, NTF = B.paths(ND, x, w, nt0, 1, SEED)
        for firm, F in FIRMS.items():
            evs = {}
            for q in range(1, 9):
                p, when, fees = evaluation(XE, WE, NTE, q, F['ev']); evs[q] = (p.mean(), fees.mean() / max(p.mean(), 1e-9), np.median(when[p]) if p.any() else np.nan)
            fus = {nm: funded(XF, WF, NTF, F['fund'], fn) for nm, fn in sizings(F['fund']['dd']).items()}
            rows[(sc, firm)] = (evs, {nm: (tot.mean(), np.mean(tot > 0), np.mean(ou > 251)) for nm, (tot, ou) in fus.items()})
    pr("\nBASE (16-year edge) - per firm: best evaluation size and funded sizing by value per account slot (2-yr payouts - fees per funded - activation)")
    pr(" firm                            eval micros  pass (252 sessions)  median sessions  fees/funded  funded sizing          2-yr payouts  paid   open 1 yr  VALUE/SLOT  half edge  slots  all slots")
    for firm, F in FIRMS.items():
        evs, fus = rows[('A base', firm)]
        best = max(((fus[s][0] - evs[q][1] - F['fund']['act'], q, s) for q in evs for s in fus))
        v, q, s = best; ev, fu = evs[q], fus[s]
        evh, fuh = rows[('C half edge', firm)]; vh = fuh[s][0] - evh[q][1] - F['fund']['act']
        pr(f" {firm:<32}    {q}            {ev[0] * 100:5.1f}%            {ev[2]:4.0f}          ${ev[1]:>6,.0f}    {s:<22} ${fu[0]:>7,.0f}   {fu[1] * 100:4.1f}%   {fu[2] * 100:5.1f}%    ${v:>6,.0f}    ${vh:>6,.0f}    {F['slots']}   ${v * F['slots']:>7,.0f}")
    pr("\nDETAIL (base): evaluation pass within 252 sessions / fees per funded account by micros")
    for firm in FIRMS:
        evs, fus = rows[('A base', firm)]
        pr(f" {firm:<32} " + '  '.join(f"{q}: {evs[q][0] * 100:4.1f}% ${evs[q][1]:,.0f}" for q in evs))
        pr(f" {'':<32} funded: " + '; '.join(f"{nm} ${v[0]:,.0f} ({v[1] * 100:.0f}% paid, {v[2] * 100:.0f}% open 1 yr)" for nm, v in fus.items()))
    with open(os.path.join(HERE, 'firms_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
