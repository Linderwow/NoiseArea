import os, sys, math, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import weekend as WK
import naplus2 as N2
from merge_lab import boot_p

OUT = os.path.join(HERE, 'NAPLUS3_RESULTS.txt')
USD = 55751.0
P_BAR = 0.05 / 3
LOOK = 14
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def quiet_days(mk, pct=20, window=250):
    sig = mk.sigma(LOOK); slot10 = (600 - 575) // 5; s = sig[:, slot10]; out = np.zeros(mk.n, bool)
    for k in range(mk.n):
        prev = s[max(0, k - window):k]; prev = prev[~np.isnan(prev)]
        if len(prev) >= window and not np.isnan(s[k]): out[k] = s[k] < np.percentile(prev, pct)
    return out


def engine3(mk, q, cap=None, escalate=None, quiet=None, worst_out=None, gate=4, lunch=True, ema=True, ch=3.0, first=600, last=930, step=30,
            pos_out=None, guard=None, vm=1.0):
    sig = mk.sigma(LOOK); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n)
    E = N._ema20(mk) if ema else None
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb, A, Hh, Ll = x.O, x.C, x.M, x.vw, x.nb, x.A, x.H, x.L
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        st = dict(s=0.0, ref=0.0, p=0.0, vol=0.0, d=0, e1=0.0, ext=0.0, opened=0, lost=False)
        viol = 0; worst = 0.0
        qk = q[k] if q is not None else None; skip_day = quiet is not None and quiet[k]
        pp = np.zeros(nb, np.int8) if pos_out is not None else None
        gd = guard.get(pd.Timestamp(x.date)) if guard is not None else None

        def fill(target, px):
            nd = int(np.sign(target))
            if st['d'] != 0 and nd != st['d'] and st['d'] * (px - st['e1']) <= 0: st['lost'] = True
            st['p'] += st['s'] * (px - st['ref']); st['vol'] += abs(target - st['s']); st['s'] = target; st['ref'] = px
            if nd != 0 and nd != st['d']: st['e1'] = px; st['ext'] = px; st['opened'] += 1
            st['d'] = nd

        for i in range(flat_i):
            if pp is not None and i > 0: pp[i - 1] = st['d']
            gv = int(gd[x.slot[i]]) if gd is not None else 0
            ub = hi * (1 + vm * sg[i]) if not np.isnan(sg[i]) else np.nan; lb = lo * (1 - vm * sg[i]) if not np.isnan(sg[i]) else np.nan
            c = C[i]; d = st['d']; s = st['s']
            if s != 0: worst = min(worst, st['p'] + s * ((Ll[i] if s > 0 else Hh[i]) - st['ref']))
            if d != 0:
                st['ext'] = max(st['ext'], c) if d > 0 else min(st['ext'], c)
                if ch is not None and ((d > 0 and c < st['ext'] - ch * A[i]) or (d < 0 and c > st['ext'] + ch * A[i])):
                    fill(0.0, O[i + 1]); viol = 0; continue
                if gv == -d:
                    fill(0.0, O[i + 1]); viol = 0; continue
            if gate and d != 0 and not np.isnan(sg[i]):
                bad = (c < max(ub, vw[i])) if d > 0 else (c > min(lb, vw[i]))
                viol = viol + 1 if bad else 0
                if viol >= gate: fill(0.0, O[i + 1]); viol = 0; continue
            if not (M[i] in checks and not np.isnan(sg[i])): continue
            if d == 0:
                new = 1 if c > ub else (-1 if c < lb else 0)
                if lunch and M[i] in (720, 750, 780): new = 0
            elif gate:
                new = d
            elif d == 1:
                new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else:
                new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if ema and new != 0 and new != d:
                if (new == 1 and not c > E[k][i]) or (new == -1 and not c < E[k][i]): new = 0
            if qk is not None and new != 0 and new != d and qk[i] != new: new = 0
            if new != 0 and new != d and gv == -new: new = 0
            if new != 0 and new != d:
                if skip_day: new = 0
                elif cap is not None and st['opened'] >= cap: new = 0
                elif escalate is not None and st['lost']:
                    ub2 = hi * (1 + escalate * sg[i]); lb2 = lo * (1 - escalate * sg[i])
                    if (new > 0 and not c > ub2) or (new < 0 and not c < lb2): new = 0
            tgt = st['s'] if new == d else float(new)
            if tgt != st['s']: fill(tgt, O[i + 1])
        if pp is not None and flat_i > 0: pp[flat_i - 1] = st['d']
        if st['s'] != 0:
            worst = min(worst, st['p'] + st['s'] * ((Ll[flat_i] if st['s'] > 0 else Hh[flat_i]) - st['ref'])); fill(0.0, C[flat_i])
        if pp is not None:
            arr = np.zeros(78, np.int8); arr[x.slot] = pp; pos_out[pd.Timestamp(x.date)] = arr
        pnl[k] = st['p']; nt[k] = st['vol'] / 2
        if worst_out is not None: worst_out[k] = min(worst, st['p'])
    return pnl, nt


def main():
    say(f"naplus3.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (NAPLUS3_PLAN.txt)")
    Q = {}; QD = {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = LS.full_bars(mk); Q[nm] = LS.per_day(mk, df, LS.ema_state(df.close.values)); QD[nm] = quiet_days(mk)
    conf = lambda nm: (lambda k, i, new: bool(Q[nm][k][i] == new))
    ok = True
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm)
        a = engine3(mk, Q[nm]); b = WK.engine(mk, gate=4, lunch=True, ema=True, ch=3.0, conf=conf(nm), conf_mode='skip')
        dd = max(np.nanmax(np.abs(a[0] - b[0])), np.nanmax(np.abs(a[1] - b[1]))); good = dd < 1e-9; ok &= good
        say(f"parity {nm}: engine3 (switches off) vs weekend.engine X3 max |diff| {dd:.1e} -> {'PASS' if good else 'FAIL'}; quiet days {int(QD[nm].sum())} of {mk.n}")
    if not ok: say("parity FAILED - nothing graded"); return
    F = {'CH': lambda mk, nm, w: N2.run2(mk, ch=3.0, worst_out=w),
         'X3': lambda mk, nm, w: engine3(mk, Q[nm], worst_out=w),
         'W1': lambda mk, nm, w: engine3(mk, Q[nm], cap=2, worst_out=w),
         'W2': lambda mk, nm, w: engine3(mk, Q[nm], escalate=1.5, worst_out=w),
         'W4': lambda mk, nm, w: engine3(mk, Q[nm], quiet=QD[nm], worst_out=w)}
    S = {c: N2.joined(f) for c, f in F.items()}
    base = S['X3'].r_close; base2 = N2.joined(F['X3'], 2).r_close; b = N2.stats(base)
    say(f"X3 base: Sharpe {b['sh']:.2f} | ${b['yr']:,.0f}/yr | max DD ${b['dd']:,.0f} | MAR {b['mar']:.2f} | worst year ${b['worst']:,.0f} (NAPLUS2: 1.49 / $5,618 / $4,823)")
    survivors = []
    for c, desc in (('W1', 'entry cap 2 a day'), ('W2', 'escalated band 1.5 after a loss'), ('W4', 'quiet-day skip (sigma < 20th pct)')):
        x = S[c].r_close; x2 = N2.joined(F[c], 2).r_close; t = N2.stats(x)
        p = boot_p(x, base); p2 = boot_p(x2, base2)
        era = [(LS.sharpe(x[m]), LS.sharpe(base[m])) for m in (x.index <= N.JOIN, x.index > N.JOIN)]
        era2 = all(LS.sharpe(x2[m]) > LS.sharpe(base2[m]) for m in (x2.index <= N.JOIN, x2.index > N.JOIN))
        dif = (x - base).sort_values(ascending=False); kp = ~x.index.isin(dif.index[:5])
        g = {'G1': p < P_BAR, 'G2': all(a > bb for a, bb in era), 'G3': p2 < P_BAR and era2, 'G4': LS.sharpe(x[kp]) > LS.sharpe(base[kp]),
             'G5': t['dd'] < b['dd'] and t['mar'] > b['mar']}
        say(f"{c} {desc}: Sharpe {t['sh']:.2f} | ${t['yr']:,.0f}/yr | max DD ${t['dd']:,.0f} | MAR {t['mar']:.2f} | worst year ${t['worst']:,.0f} | "
            f"P vs X3 {p:.4f} | NQ {era[0][0]:.2f}/{era[0][1]:.2f} MNQ {era[1][0]:.2f}/{era[1][1]:.2f} | 2 ticks P {p2:.4f}")
        fails = [kk for kk, v in g.items() if not v]
        say(f"   VERDICT {c}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(c)
    say(); say("BULENOX 50K: eval 6 micros pass/try | Master 2-yr payouts / open after 1 yr at 1 | 2 | 3 | 4 micros")
    for c in S:
        e, f = LS.bulenox(S[c])
        say(f"   {c:4s} {e['p'] * 100:5.1f}% | " + " | ".join(f"${f[m]['pay']:,.0f} / {f[m]['open1'] * 100:.0f}%" for m in (1, 2, 3, 4)))
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none - X3 stays the best setup"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
