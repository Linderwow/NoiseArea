r"""WEEKEND independent audit: the new switches of weekend.engine re-implemented from the plan's text as plain per-day loops
(retrace trail, armed chandelier, scale-out, phase blend, second-index confirmation), compared day by day on NQ and MNQX.
    python weekend_check.py   -> weekend_check_output.txt   (ALL PASS or the first differences)
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX
import weekend as W

CHECKS = set(range(600, 931, 30)); out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)


def day_overlay(x, pc, sg, ch=None, arm=None, retrace=None, scaleout=None):
    """One session of the live rules with ONE overlay, written independently. Returns (points, units traded / 2, overlay exits)."""
    O, C, M, vw, A, nb = x.O, x.C, x.M, x.vw, x.A, x.nb
    hi, lo = max(O[0], pc), min(O[0], pc); flat_i = nb - 1 if M[-1] >= 960 else nb - 2
    pos = 0.0; ref = 0.0; cash = 0.0; traded = 0.0; entry = best = width = 0.0; armed = False; scaled = False; n_x = 0

    def trade(to, px):
        nonlocal pos, ref, cash, traded, entry, best, width, armed, scaled
        cash += pos * (px - ref); traded += abs(to - pos)
        if np.sign(to) != 0 and np.sign(to) != np.sign(pos):
            entry = px; best = px; armed = False; scaled = False
        pos = to; ref = px

    for i in range(flat_i):
        c = C[i]; sgn = np.sign(pos)
        if pos != 0:
            best = max(best, c) if sgn > 0 else min(best, c)
            gain_best = sgn * (best - entry); gain_now = sgn * (c - entry)
            if ch is not None:
                if arm is None or gain_best >= arm * width: armed = True
                if armed and sgn * (c - best) < -ch * A[i]:
                    trade(0.0, O[i + 1]); n_x += 1; continue
            if retrace is not None:
                if gain_best >= retrace[0] * width: armed = True
                if armed and gain_now < retrace[1] * gain_best:
                    trade(0.0, O[i + 1]); n_x += 1; continue
        if M[i] in CHECKS and not np.isnan(sg[i]):
            ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
            if sgn == 0: want = 1 if c > ub else (-1 if c < lb else 0)
            elif sgn > 0: want = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: want = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if want != sgn:
                w_new = O[0] * sg[i]; trade(float(want), O[i + 1])
                if want != 0: width = w_new
                continue
        if scaleout is not None and pos != 0 and not scaled and np.sign(pos) * (c - entry) >= scaleout[0] * width:
            trade(pos * (1 - scaleout[1]), O[i + 1]); scaled = True
    if pos != 0: trade(0.0, C[flat_i])
    return cash, traded / 2, n_x


def check_market(nm):
    mk = N.market(nm); sig = mk.sigma(14); ok = True
    for lab, kw_e, kw_c in (('retrace (1, 0.5)', dict(retrace=(1.0, 0.5)), dict(retrace=(1.0, 0.5))),
                            ('retrace (1.5, 0.5)', dict(retrace=(1.5, 0.5)), dict(retrace=(1.5, 0.5))),
                            ('armed chandelier', dict(ch=3.0, ch_arm=1.0), dict(ch=3.0, arm=1.0)),
                            ('plain chandelier', dict(ch=3.0), dict(ch=3.0)),
                            ('scale-out (2, 0.5)', dict(scaleout=(2.0, 0.5)), dict(scaleout=(2.0, 0.5)))):
        p_e, n_e, _, e_e = W.engine(mk, **kw_e); bad = []; nx = 0
        for k, x in enumerate(mk.days):
            if k < 14 or np.isnan(mk.pc[k]): continue
            p, n, n_x = day_overlay(x, mk.pc[k], sig[k, x.slot], **kw_c); nx += n_x
            if abs(p - p_e[k]) > 1e-9 or abs(n - n_e[k]) > 1e-9: bad.append((mk.dates[k].date(), p, p_e[k], n, n_e[k]))
        same = not bad and nx == int(e_e.sum())
        pr(f"{nm} {lab}: {'PASS' if same else 'FAIL'} - {len(bad)} differing sessions, overlay exits {nx} vs engine {int(e_e.sum())}"
           + (f"; first: {bad[:3]}" if bad else '')); ok &= same
    # phase blend = the average of two plain runs, recomputed with fam_V
    pa, na = N.fam_V(mk); pb, nb_ = N.fam_V(mk, first=615, last=915)
    r = W.blend({nm: (mk, pa, na, np.zeros(mk.n, bool), np.zeros(mk.n, int))}, {nm: (mk, pb, nb_, np.zeros(mk.n, bool), np.zeros(mk.n, int))})
    same = np.allclose(np.nan_to_num(r[nm][1], nan=-9e9), np.nan_to_num(0.5 * (pa + pb), nan=-9e9)) and np.allclose(r[nm][2], 0.5 * (na + nb_))
    pr(f"{nm} phase blend == average of the two fam_V legs: {'PASS' if same else 'FAIL'}"); ok &= same
    return ok


def check_second_index():
    """ES / MES bands recomputed with pandas on 60 random sessions and compared with weekend.es_state."""
    ok = True; rng = np.random.default_rng(1)
    for nm in ('ES', 'MES'):
        mk = N.market(nm); st = W.es_state(nm); bad = 0; n = 0
        MV = pd.DataFrame(mk.MV)
        for k in rng.choice(np.arange(20, mk.n), 60, replace=False):
            x = mk.days[k]; prev = MV.iloc[k - 14:k]; cnt = prev.notna().sum(); mu = prev.mean(); mu[cnt < 7] = np.nan
            hi, lo = max(x.O[0], mk.pc[k]), min(x.O[0], mk.pc[k]); arr = st.get(x.date)
            if arr is None: continue
            for i, s in enumerate(x.slot):
                sg = mu.iloc[s]; n += 1
                exp = np.nan if np.isnan(sg) else (1.0 if x.C[i] > hi * (1 + sg) else (-1.0 if x.C[i] < lo * (1 - sg) else 0.0))
                got = arr[s]
                if not ((np.isnan(exp) and np.isnan(got)) or exp == got): bad += 1
        pr(f"{nm} second-index state recomputed with pandas: {n:,} bar states on 60 sessions, {bad} mismatches: {'PASS' if bad == 0 else 'FAIL'}"); ok &= bad == 0
    return ok


if __name__ == '__main__':
    W.below_normal(); ok = True
    for nm in ('NQ', 'MNQX'): ok &= check_market(nm)
    ok &= check_second_index()
    pr("ALL PASS" if ok else "AUDIT FAILED")
    open(os.path.join(HERE, 'weekend_check_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')
