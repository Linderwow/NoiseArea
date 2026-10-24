r"""FLEET (FLEET_PLAN.txt, pre-registered 2026-10-24): one funded Bulenox Master vs up to five, every account trading the same
NoiseArea sessions; and when to take payouts.
    python fleet.py   -> fleet_output.txt
Rules exactly as SIZES / MASTERSCALE (biglever.evaluation, master_qm with masterscale's cushion sizing); audited against them.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import biglever as B
import sizes as S
import masterscale as MS

P = S.P50; SEED = B.SEED_REPORT; ND = B.N_FUNDED; Q_EVAL = 6; EVAL_FEE, ACT_FEE = float(P['qual']), float(P['act'])
CAPS = (1, 2, 3, 5); HOLDS = (('H0 as soon as allowed', None), ('H1 keep $5,000 cushion', 5000.0), ('H2 keep $7,500 cushion', 7500.0))
out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
def size_rule(cu): return np.clip(np.floor(cu / 2500), 1, 4)


class Master:
    """One Master slot on every path - masterscale.master_dyn's day, step by step; hold = cushion kept after a payout."""
    def __init__(self, n, hold=None):
        self.hold = hold; self.alive = np.zeros(n, bool); self.start(np.zeros(n, bool))
    def start(self, m):
        for k, v in (('bal', 0.0), ('hi', 0.0), ('thr', -float(P['dd'])), ('ptot', 0.0), ('pbest', 0.0)):
            a = getattr(self, k, None); a = np.zeros(len(m)) if a is None else a; a[m] = v; setattr(self, k, a)
        for k in ('locked',):
            a = getattr(self, k, None); a = np.zeros(len(m), bool) if a is None else a; a[m] = False; setattr(self, k, a)
        for k in ('n_pay', 'days', 'wait'):
            a = getattr(self, k, None); a = np.zeros(len(m), int) if a is None else a; a[m] = 0; setattr(self, k, a)
        self.alive |= m
    def step(self, x1, w1, nt):
        q = size_rule(self.bal - self.thr)
        x, w = B.day(x1 * q, w1 * q, np.where(self.locked, np.inf, float(P['dll'])))
        x = np.where(self.alive, x, 0.0); w = np.where(self.alive, w, 0.0)
        br = self.alive & (self.bal + w < self.thr); self.alive &= ~br; self.bal += x
        self.hi = np.where(self.alive, np.maximum(self.hi, self.bal), self.hi)
        up = self.alive & ~self.locked; self.thr = np.where(up, np.maximum(self.thr, self.hi - P['dd']), self.thr)
        lk = up & (self.thr >= 100); self.thr[lk] = 100.0; self.locked |= lk
        ww = self.alive & (self.wait > 0); self.wait[ww] -= 1; act = self.alive & ~ww
        self.days += (act & (nt > 0)).astype(int); self.ptot = np.where(act, self.ptot + x, self.ptot); self.pbest = np.where(act, np.maximum(self.pbest, x), self.pbest)
        amt = np.minimum(np.where(self.n_pay < 3, float(P['cap']), 0.5 * self.bal - 1), self.bal - P['reserve'])
        if self.hold is not None: amt = np.minimum(amt, self.bal - self.thr - self.hold)
        el = act & (self.days >= 10) & (self.ptot > 0) & (self.pbest <= 0.4 * self.ptot) & (amt >= 1000)
        pay = np.zeros(len(x)); self.bal[el] -= amt[el]; pay[el] = 0.9 * amt[el]
        self.n_pay[el] += 1; self.days[el] = 0; self.ptot[el] = 0.0; self.pbest[el] = 0.0; self.wait[el] = 3
        return pay, br, q


class Evaluation:
    """One evaluation slot on every path - biglever.evaluation (with resets) session by session."""
    def __init__(self, n):
        self.active = np.zeros(n, bool); self.bal = np.zeros(n); self.hi = np.zeros(n); self.t = np.zeros(n, int)
    def start(self, m): self.active |= m; self.bal[m] = 0.0; self.hi[m] = 0.0; self.t[m] = 0
    def step(self, x1, w1, can_reset=True):
        x, w = B.day(x1 * Q_EVAL, w1 * Q_EVAL, P['dll']); a = self.active
        br = a & (self.bal + w < self.hi - P['dd']); self.bal = np.where(a & ~br, self.bal + x, self.bal)
        rs = br & ((B.N_WIN - 1 - self.t) >= B.RESET_MIN_LEFT) & can_reset; self.bal[rs] = 0.0; self.hi[rs] = 0.0; br &= ~rs
        self.active = a & ~br
        self.hi = np.where(self.active, np.maximum(self.hi, self.bal), self.hi)
        hit = self.active & (self.bal >= P['target']); self.active &= ~hit
        self.t[a] += 1; timeout = self.active & (self.t >= B.N_WIN); self.active &= ~timeout
        return hit, rs


def fleet(X, W, NT, cap, wallet0=None):
    """wallet0 None: every fee is paid when due (part A). A number: the wallet ladder (amendment 1) - fees only from wallet0 + payouts."""
    n = X.shape[0]; ms = [Master(n) for _ in range(cap)]; ev = Evaluation(n); pending = np.zeros(n, bool); waited = np.zeros(n, int)
    fees = np.zeros(n); pays = np.zeros(n); cash_min = np.zeros(n); first = np.full(n, ND); open_sum = np.zeros(n); dry = np.zeros(n, bool)
    afford = (lambda fee: np.ones(n, bool)) if wallet0 is None else (lambda fee: wallet0 + pays - fees >= fee)
    for t in range(ND):
        if pending.any():                                          # a passed evaluation becomes a Master in a free slot
            for m in ms:
                go = pending & ~m.alive & afford(ACT_FEE)
                if go.any(): m.start(go); fees[go] += ACT_FEE; pending &= ~go
            waited[pending] += 1; lost = pending & (waited > 5); pending &= ~lost; dry |= lost
        waited[~pending] = 0
        n_open = sum(m.alive.astype(int) for m in ms)
        want = ~ev.active & ~pending & (n_open < cap); new = want & afford(EVAL_FEE); dry |= want & ~new
        if new.any(): ev.start(new); fees[new] += EVAL_FEE
        for m in ms:
            p, _, _ = m.step(X[:, t], W[:, t], NT[:, t]); pays += p
            first = np.where((p > 0) & (first == ND), t, first)
        hit, rs = ev.step(X[:, t], W[:, t], afford(B.RESET_FEE)); fees[rs] += B.RESET_FEE; pending |= hit
        open_sum += sum(m.alive.astype(int) for m in ms); cash_min = np.minimum(cash_min, pays - fees)
    return dict(pay=pays, fees=fees, net=pays - fees, oop=-cash_min, first=first, avg_open=open_sum / ND,
                open_end=sum(m.alive.astype(int) for m in ms), dry=dry)


def main():
    x0, w0, nt0, idx = S.base_series('intraday_worst_extended.csv')
    pr(f"FLEET (FLEET_PLAN.txt): {len(x0)} sessions {idx[0].date()} .. {idx[-1].date()} (extended history), MNQ 29,150, $0.61 a side; "
       f"{B.N_PATHS:,} paths x {ND} sessions, seed {SEED}; every account trades the same sessions")
    x, w = S.scenario('A base', x0, w0, nt0); X, W, NT = B.paths(ND, x, w, nt0, 1, SEED)
    # audits
    m = Master(X.shape[0]); m.start(np.ones(X.shape[0], bool))
    mine = np.stack([m.step(X[:, t], W[:, t], NT[:, t])[0] for t in range(ND)], axis=1)
    assert np.array_equal(mine, MS.master_dyn(X, W, NT, P, size_rule)[0]), "Master audit failed"     # every payout, every day
    Xe, We, _ = B.paths(B.N_WIN, x, w, nt0, Q_EVAL, SEED); ok, nres = B.evaluation(Xe, We, P['dd'], P['dll'], P['target'], True)
    X1, W1, _ = B.paths(B.N_WIN, x, w, nt0, 1, SEED); e = Evaluation(X1.shape[0]); e.start(np.ones(X1.shape[0], bool))
    passed = np.zeros(X1.shape[0], bool); rsn = np.zeros(X1.shape[0])
    for t in range(B.N_WIN): h, r = e.step(X1[:, t], W1[:, t]); passed |= h; rsn += r
    assert np.array_equal(passed, ok) and np.array_equal(rsn, nres), "Evaluation audit failed"
    pr("audit: one Master from day 0 == masterscale.master_dyn and one evaluation from day 0 == biglever.evaluation, exactly")

    pr("\n(A) HOW MANY FUNDED ACCOUNTS (Bulenox allows 5), 2 years; one evaluation at a time")
    for sc in ('A base', 'C half edge', 'D no edge'):
        x, w = S.scenario(sc, x0, w0, nt0); X, W, NT = B.paths(ND, x, w, nt0, 1, SEED)
        pr(f"=== {sc}: ${x.mean() * 252:,.0f}/yr a micro ===")
        pr(" cap  payouts   fees    net     median net  P(net>0)  P(net<-$1k)  max out of pocket (median / 90th pct)  avg Masters open  open at end  median sessions to 1st payout")
        for cap in CAPS:
            f = fleet(X, W, NT, cap); fp = f['first'][f['first'] < ND]
            pr(f"  {cap}   ${f['pay'].mean():>7,.0f}  ${f['fees'].mean():>5,.0f}  ${f['net'].mean():>7,.0f}  ${np.median(f['net']):>8,.0f}   {np.mean(f['net'] > 0) * 100:5.1f}%     "
               f"{np.mean(f['net'] < -1000) * 100:5.1f}%       ${np.median(f['oop']):>5,.0f} / ${np.quantile(f['oop'], 0.9):>5,.0f}                    "
               f"{f['avg_open'].mean():4.2f}            {f['open_end'].mean():4.2f}        {np.median(fp) if len(fp) else float('nan'):4.0f}")
    pr("\n(A2) WALLET LADDER (amendment 1, after part A was seen): fees only from a starting budget + payouts")
    for sc in ('A base', 'C half edge', 'D no edge'):
        x, w = S.scenario(sc, x0, w0, nt0); X, W, NT = B.paths(ND, x, w, nt0, 1, SEED)
        pr(f"=== {sc} ===")
        pr(" budget  cap  payouts   fees    net     median net  P(net>0)  P(lost the whole budget)  avg Masters open  open at end  wallet ever too short")
        for u in (500.0, 1000.0):
            for cap in (1, 5):
                f = fleet(X, W, NT, cap, u)
                pr(f"  ${u:,.0f}   {cap}   ${f['pay'].mean():>7,.0f}  ${f['fees'].mean():>5,.0f}  ${f['net'].mean():>7,.0f}  ${np.median(f['net']):>8,.0f}   "
                   f"{np.mean(f['net'] > 0) * 100:5.1f}%        {np.mean(f['net'] <= -u + 1e-9) * 100:5.1f}%                 {f['avg_open'].mean():4.2f}            "
                   f"{f['open_end'].mean():4.2f}        {np.mean(f['dry']) * 100:5.1f}%")
    pr("\n(B) PAYOUT POLICY on one Master (cushion sizing), 2 years")
    pr(" policy                    scenario     payouts  any payout  open after 1 yr  avg micros")
    res = {}
    for sc in ('A base', 'C half edge'):
        x, w = S.scenario(sc, x0, w0, nt0); X, W, NT = B.paths(ND, x, w, nt0, 1, SEED)
        for name, hold in HOLDS:
            m = Master(X.shape[0], hold); m.start(np.ones(X.shape[0], bool)); tot = np.zeros(X.shape[0]); alive1 = None; qs = []
            for t in range(ND):
                p, _, q = m.step(X[:, t], W[:, t], NT[:, t]); tot += p; qs.append(np.where(m.alive, q, np.nan))
                if t == 251: alive1 = m.alive.copy()
            res[(sc, name)] = (tot.mean(), np.mean(tot > 0)); avgq = np.nanmean(np.array(qs))
            pr(f" {name:<25} {sc:<12} ${tot.mean():>7,.0f}    {np.mean(tot > 0) * 100:5.1f}%       {alive1.mean() * 100:5.1f}%        {avgq:4.2f}")
    pr("DECISION (plan): adopt H1/H2 only if payouts higher in BOTH base and half edge and any-payout not lower by > 2 points")
    best = None
    for name, hold in HOLDS[1:]:
        ok = all(res[(sc, name)][0] > res[(sc, HOLDS[0][0])][0] and (res[(sc, name)][1] - res[(sc, HOLDS[0][0])][1]) * 100 >= -2.0 for sc in ('A base', 'C half edge'))
        pr(f"   {name}: {'ELIGIBLE' if ok else 'no'}")
        if ok and (best is None or res[('C half edge', name)][0] > res[('C half edge', best)][0]): best = name
    pr(f"-> {'ADOPT ' + best if best else 'payouts stay as soon as allowed'}")
    with open(os.path.join(HERE, 'fleet_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
