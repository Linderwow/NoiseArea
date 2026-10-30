r"""WEEKEND program 2026-10-31 (WEEKEND_PLAN_20261031.txt, pre-registered): tracks A2 (second index), B (drawdown mechanics),
C (profit protection) and D (chandelier power). A1 = weekend_knn.py, A3 = weekend_flow.py.
    python weekend.py audit    the engine checks (a)-(e) of the plan; must print ALL PASS before anything else is read
    python weekend.py run      the battery -> weekend_output.txt, weekend_series.csv (daily $ per unit of every variant)
Engine: nav_lab.fam_V's loop (paper trail, 1 unit) with the new switches; identical to fam_V with none set, to strat3.run with ch=3,
and to fam_V(gate=4, lunch=True, ema=True) with the K1 switches. Below-normal priority, one core, headless.
"""
import os, sys, time
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import history_extend as HX                      # registers nav_lab.FILES['MNQX'] (frozen 16 years + the recorded sessions)
import navar as NV
import metalabel as ML
import strat3 as S3
import chval as CV
from research_engine import WARMUP

LOOK = 14; USD = ML.USD; JOIN = N.JOIN; K_TRIALS = 9; P_BAR = 0.05 / K_TRIALS
FRESH = CV.FRESH
PAIR = {'NQ': 'ES', 'MNQX': 'MES'}               # second index for A2 (same dates, same 5-minute grid)
OUT = os.path.join(HERE, 'weekend_output.txt')
out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)
def save(): open(OUT, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
CV.pr = pr                                       # chval.compare's lines go into this file too (the 10-30 run has them in weekend_run.log)
def below_normal():
    try:
        import ctypes; k = ctypes.windll.kernel32; k.SetPriorityClass(k.GetCurrentProcess(), 0x4000)
    except Exception:
        pass


# ------------------------------------------------------------------------------------------------ the engine
def engine(mk, first=600, last=930, step=30, lunch=False, ema=False, gate=0, ch=None, ch_arm=None, retrace=None, scaleout=None,
           conf=None, conf_mode=None, hazard=None, seed=0, log=None, worst_out=None):
    """nav_lab.fam_V (paper trail, size 1, vm 1, look 14) + the WEEKEND switches.
    ch        chandelier multiple of ATR(14) on the 5-minute close (strat3.run's rule), ch_arm = arm it only once the best close is
              ch_arm noise widths beyond the entry fill (width = 09:30 open x sigma at the entry check)
    retrace   (a, r): armed once the best close is a widths beyond the entry; exit at the next open when the close's open profit is
              below r x the best close's open profit
    scaleout  (k, frac): at the first close k widths beyond the entry, frac of the size exits at the next open; the rest trails
    conf      f(k, i, new) -> True / False / None (second-index confirmation); conf_mode 'skip' (entry only when True or None) or
              'size' (2 units when True, 1 otherwise)
    hazard    random early exit per bar while in a position (control), seed
    worst_out array of mk.n: receives each session's worst point of realized + open P&L marked at every bar's adverse extreme
              (intraday_worst.day_close_and_worst's marking; audited against intraday_worst_extended.csv in weekend_extra.py)
    Returns (daily points per unit, traded units / 2, acted = days on which a new switch changed anything, early = early exits)."""
    sig = mk.sigma(LOOK); checks = set(range(first, last + 1, step))
    pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n); acted = np.zeros(mk.n, bool); early = np.zeros(mk.n, int)
    E = N._ema20(mk) if ema else None
    rng = np.random.default_rng(seed) if hazard is not None else None
    if log is not None: log.setdefault('entries', [])
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, C, M, vw, nb, A, Hh, Ll = x.O, x.C, x.M, x.vw, x.nb, x.A, x.H, x.L
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        s = 0.0; ref = 0.0; p = 0.0; vol = 0.0; viol = 0; d = 0; worst = 0.0
        e1 = 0.0; ext = 0.0; w_in = 0.0; armed_ch = False; armed_rt = False; so_done = False; i_in = 0
        ent = None

        def fill(target, px, i):
            nonlocal s, ref, p, vol, d, e1, ext, w_in, armed_ch, armed_rt, so_done, i_in, ent
            nd = int(np.sign(target))
            if log is not None and d != 0 and nd != d and ent is not None:
                log['entries'].append(ent + (d * (px - e1),)); ent = None
            p += s * (px - ref); vol += abs(target - s); s = target; ref = px
            if nd != 0 and nd != d:
                e1 = px; ext = px; w_in = w_cur; armed_ch = armed_rt = so_done = False; i_in = i + 1
                if log is not None: ent = (k, i, nd, conf_cur)
            d = nd

        w_cur = 0.0; conf_cur = None
        for i in range(flat_i):
            ub = hi * (1 + sg[i]) if not np.isnan(sg[i]) else np.nan; lb = lo * (1 - sg[i]) if not np.isnan(sg[i]) else np.nan
            c = C[i]
            if s != 0: worst = min(worst, p + s * ((Ll[i] if s > 0 else Hh[i]) - ref))
            if d != 0:
                ext = max(ext, c) if d > 0 else min(ext, c)
                best = d * (ext - e1)
                if ch is not None:
                    if ch_arm is None or armed_ch or best >= ch_arm * w_in: armed_ch = True
                    if armed_ch and ((d > 0 and c < ext - ch * A[i]) or (d < 0 and c > ext + ch * A[i])):
                        fill(0.0, O[i + 1], i); acted[k] = True; early[k] += 1; viol = 0; continue
                if retrace is not None:
                    if best >= retrace[0] * w_in: armed_rt = True
                    if armed_rt and d * (c - e1) < retrace[1] * best:
                        fill(0.0, O[i + 1], i); acted[k] = True; early[k] += 1; viol = 0; continue
                if hazard is not None and i >= i_in and rng.random() < hazard:
                    fill(0.0, O[i + 1], i); acted[k] = True; early[k] += 1; viol = 0; continue
            if gate and d != 0 and not np.isnan(sg[i]):
                bad = (c < max(ub, vw[i])) if d > 0 else (c > min(lb, vw[i]))
                viol = viol + 1 if bad else 0
                if viol >= gate: fill(0.0, O[i + 1], i); viol = 0; continue
            is_check = M[i] in checks and not np.isnan(sg[i])
            if not is_check:
                if scaleout is not None and d != 0 and not so_done and d * (c - e1) >= scaleout[0] * w_in:
                    fill(d * abs(s) * (1 - scaleout[1]), O[i + 1], i); so_done = True; acted[k] = True
                continue
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
            size = 1.0; conf_cur = None
            if conf is not None and new != 0 and new != d:
                conf_cur = conf(k, i, new)
                if conf_mode == 'skip' and conf_cur is False: new = 0; acted[k] = True
                elif conf_mode == 'size':
                    size = 2.0 if conf_cur is True else 1.0
                    if conf_cur is True: acted[k] = True
            if new == d:
                tgt = s
                if scaleout is not None and d != 0 and not so_done and d * (c - e1) >= scaleout[0] * w_in:
                    tgt = d * abs(s) * (1 - scaleout[1]); so_done = True; acted[k] = True
            else:
                tgt = new * size; w_cur = O[0] * sg[i]
            if tgt != s: fill(tgt, O[i + 1], i)
        if s != 0:
            worst = min(worst, p + s * ((Ll[flat_i] if s > 0 else Hh[flat_i]) - ref)); fill(0.0, C[flat_i], flat_i)
        pnl[k] = p; nt[k] = vol / 2
        if worst_out is not None: worst_out[k] = min(worst, p)
    return pnl, nt, acted, early


# ------------------------------------------------------------------------------------------------ second index (A2)
_ES = {}
def es_state(name):
    """{date: array over the 78 slots} = the second index's own noise-area position at each 5-minute close: +1 above its upper band,
    -1 below its lower band, 0 inside, nan where its sigma is undefined (own 09:30 open / prior close / sigma(14))."""
    if name not in _ES:
        mk = N.market(name); sig = mk.sigma(LOOK); st = {}
        for k, x in enumerate(mk.days):
            if k < LOOK or np.isnan(mk.pc[k]): continue
            hi, lo = max(x.O[0], mk.pc[k]), min(x.O[0], mk.pc[k]); sg = sig[k, x.slot]
            arr = np.full(78, np.nan)
            ok = ~np.isnan(sg)
            v = np.where(x.C > hi * (1 + sg), 1.0, np.where(x.C < lo * (1 - sg), -1.0, 0.0))
            arr[x.slot[ok]] = v[ok]; st[x.date] = arr
        _ES[name] = st
    return _ES[name]


def make_conf(mk):
    st = es_state(PAIR[mk.name]); dates = [x.date for x in mk.days]; slots = [x.slot for x in mk.days]
    def conf(k, i, new):
        arr = st.get(dates[k])
        if arr is None: return None
        v = arr[slots[k][i]]
        if np.isnan(v): return None
        return bool(v == new)
    return conf


# ------------------------------------------------------------------------------------------------ series and grading
def joined(res, ticks=1): return ML.joined(res, ticks)


def trades(res):
    return sum(float(res[nm][2][(np.arange(res[nm][0].n) >= WARMUP) & keep(res[nm][0].dates)].sum()) for nm, keep in ML.PARTS)


def run_pair(kw, mks, with_conf=False, log=None):
    res = {}
    for nm, _ in ML.PARTS:
        mk = mks[nm]; kw2 = dict(kw)
        if with_conf: kw2['conf'] = make_conf(mk)
        lg = None if log is None else log.setdefault(nm, {})
        p, n, acted, early = engine(mk, log=lg, **kw2); res[nm] = (mk, p, n, acted, early)
    return res


def blend(res_a, res_b):
    return {nm: (res_a[nm][0], 0.5 * (res_a[nm][1] + res_b[nm][1]), 0.5 * (res_a[nm][2] + res_b[nm][2]),
                 res_a[nm][3] | res_b[nm][3], res_a[nm][4] + res_b[nm][4]) for nm in res_a}


def per_year(rb, rc):
    worse = cnt = 0; rows = []
    for y in sorted(set(rb.index.year)):
        a, b = rb[rb.index.year == y] * USD, rc[rc.index.year == y] * USD; da, db = CV.mdd(a.values), CV.mdd(b.values)
        if da >= 500: cnt += 1; worse += db > da + 1e-9
        rows.append((y, a.sum(), b.sum(), da, db))
    return worse, cnt, rows


def grade(name, res, base, mks, kw, exit_rule, with_conf=False, unseen_fn=None, n_early=None):
    """The plan's kill set. Returns dict of verdicts and the joined series."""
    rb, rc = joined(base), joined(res); rb2, rc2 = joined(base, 2), joined(res, 2)
    g = {}
    dm, t = CV.compare(f"{name}: whole period", rc, rb, boot=False)
    P = NV.boot_mar(rc, rb); g['P'] = P
    pr(f"   bootstrap P(MAR change <= 0) = {P:.4f} (bar {P_BAR:.4f}); Sharpe bootstrap P {CV.boot_sharpe(rc, rb):.3f}")
    disc = (rb.index > JOIN) & (rb.index.year <= 2022)
    d1, t1 = CV.compare("   MNQ discovery 2019-05 .. 2022", rc[disc], rb[disc], boot=False)
    CV.compare("   NQ 2010-07 .. 2019-05", rc[~(rb.index > JOIN)], rb[~(rb.index > JOIN)], boot=False)
    CV.compare("   MNQ validation 2023 .. 2026-10", rc[rb.index.year >= 2023], rb[rb.index.year >= 2023], boot=False)
    g['K1'] = d1 >= 0 and t1 >= -1.0
    worse, cnt, rows = per_year(rb, rc); g['K2'] = worse <= cnt / 3
    pr(f"   K1 discovery: MAR change {d1:+.3f}, diff t {t1:+.2f} -> {'pass' if g['K1'] else 'KILL'}")
    pr(f"   K2 years with a drawdown >= $500: {cnt}, variant deeper in {worse} -> {'pass' if g['K2'] else 'KILL'}")
    pr("      year  live $   variant $   live DD   variant DD")
    for y, a, b, da, db in rows: pr(f"      {y}  {a:>7,.0f}   {b:>7,.0f}    {da:>7,.0f}     {db:>7,.0f}")
    g['K3'] = NV.mar(rc2) >= NV.mar(rb2)
    pr(f"   K3 at 2 ticks: MAR {NV.mar(rc2):.3f} vs {NV.mar(rb2):.3f} -> {'pass' if g['K3'] else 'KILL'}")
    signs = []
    for nm, spec in FRESH.items():
        if with_conf and nm not in ('MNQDB',):
            pr(f"   {nm}: no second-index pairing (A2 needs ES/MES beside the Nasdaq series) - not counted"); continue
        mk = N.market(nm)
        if unseen_fn is not None:
            p0, n0, p1, n1 = unseen_fn(mk)
        else:
            kw2 = dict(kw)
            if with_conf: kw2['conf'] = make_conf_mnqdb(mk)
            p0, n0, _, _ = engine(mk); p1, n1, _, _ = engine(mk, **kw2)
        sb, sc = CV.series_other(mk, p0, n0, spec), CV.series_other(mk, p1, n1, spec)
        dmm, _ = CV.compare(f"   unseen {nm} {sb.index[0].date()} .. {sb.index[-1].date()}", sc, sb, usd=1.0, boot=False); signs.append(dmm > 0)
    g['K4'] = sum(not s_ for s_ in signs) < 2
    pr(f"   K4 MAR change positive on {sum(signs)} of {len(signs)} unseen series -> {'pass' if g['K4'] else 'KILL'}")
    if exit_rule:
        n_x = int(sum(res[nm][4].sum() for nm in res)) if n_early is None else n_early
        tot_bars = sum(sum(max(0, x.nb - 1) for x in mks[nm].days) for nm, _ in ML.PARTS)
        base_kw = {kk: v for kk, v in kw.items() if kk in ('lunch', 'ema', 'gate', 'first', 'last')}   # the control sits on the same base
        def count_rand(hz, sd=0):
            r_ = {}; n_ = 0
            for nm, _ in ML.PARTS:
                p, nn, ac, ea = engine(mks[nm], hazard=hz, seed=sd, **base_kw); r_[nm] = (mks[nm], p, nn, ac, ea); n_ += int(ea.sum())
            return r_, n_
        hz = n_x / (0.35 * tot_bars)
        for _ in range(6):
            _, n_ = count_rand(hz); hz *= n_x / max(n_, 1)
        ref = joined(run_pair(base_kw, mks)) if base_kw else rb
        gains = []
        for sd in range(50):
            r_, n_ = count_rand(hz, sd + 1); gains.append(NV.mar(joined(r_)) - NV.mar(ref))
        gv = NV.mar(rc) - NV.mar(ref); q95 = float(np.quantile(gains, 0.95)); g['K5'] = gv > q95
        pr(f"   K5 random-exit control: rule's early exits {n_x:,}; hazard {hz:.5f}/bar (~{n_:,} per seed); random MAR change median "
           f"{np.median(gains):+.3f}, 95th pct {q95:+.3f}, max {max(gains):+.3f}; rule {gv:+.3f} vs its own base -> {'pass' if g['K5'] else 'KILL'}")
    else:
        g['K5'] = True; pr("   K5 not applicable (no exits added)")
    drop5 = NV.mar(rc.drop(rc.nlargest(5).index)); g['K8'] = drop5 > NV.mar(rb)
    pr(f"   K8 drop-5: MAR {drop5:.3f} vs live {NV.mar(rb):.3f} -> {'pass' if g['K8'] else 'KILL'}")
    g['acted'] = int(sum(res[nm][3].sum() for nm in res))
    allk = all(g[kk] for kk in ('K1', 'K2', 'K3', 'K4', 'K5', 'K8'))
    verdict = 'ADOPTION CANDIDATE' if allk and P < P_BAR else ('FORWARD-TEST CANDIDATE' if allk else 'DEAD')
    g['verdict'] = verdict
    pr(f"   >>> {name}: {verdict}  (kills: {', '.join(kk for kk in ('K1', 'K2', 'K3', 'K4', 'K5', 'K8') if not g[kk]) or 'none'}; "
       f"P {P:.4f}; days on which the rule acted {g['acted']:,})")
    save()
    return g, rc


_ES_DB = None
def make_conf_mnqdb(mk):
    """MNQ Databento paired with MES (same dates, 2019-05 .. 2026-05)."""
    st = es_state('MES'); dates = [x.date for x in mk.days]; slots = [x.slot for x in mk.days]
    def conf(k, i, new):
        arr = st.get(dates[k])
        if arr is None: return None
        v = arr[slots[k][i]]
        return None if np.isnan(v) else bool(v == new)
    return conf


# ------------------------------------------------------------------------------------------------ audit
def audit():
    below_normal(); ok = True
    for nm in ('NQ', 'MNQX', 'MNQDB'):
        mk = N.market(nm)
        p0, n0 = N.fam_V(mk); p1, n1, ac, ea = engine(mk)
        same = np.array_equal(np.nan_to_num(p0, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and np.array_equal(n0, n1)
        pr(f"(a) {nm}: engine() == fam_V on {mk.n} sessions: {'PASS' if same else 'FAIL'} (acted {ac.sum()}, early {ea.sum()})"); ok &= same
        p2, n2, _ = S3.run(mk, ch=3.0); p3, n3, ac3, ea3 = engine(mk, ch=3.0)
        same = np.allclose(np.nan_to_num(p2, nan=-9e9), np.nan_to_num(p3, nan=-9e9), atol=1e-9) and np.allclose(n2, n3)
        pr(f"(b) {nm}: engine(ch=3) == strat3.run(ch=3): {'PASS' if same else 'FAIL'} (early exits {ea3.sum()}, days acted {ac3.sum()})"); ok &= same
        p4, n4 = N.fam_V(mk, gate=4, lunch=True, ema=True); p5, n5, _, _ = engine(mk, gate=4, lunch=True, ema=True)
        same = np.array_equal(np.nan_to_num(p4, nan=-9e9), np.nan_to_num(p5, nan=-9e9)) and np.array_equal(n4, n5)
        pr(f"(c) {nm}: engine(K1 switches) == fam_V(gate=4, lunch, ema): {'PASS' if same else 'FAIL'}"); ok &= same
        p6, n6 = N.fam_V(mk, first=615, last=915); p7, n7, _, _ = engine(mk, first=615, last=915)
        same = np.array_equal(np.nan_to_num(p6, nan=-9e9), np.nan_to_num(p7, nan=-9e9)) and np.array_equal(n6, n7)
        pr(f"(d) {nm}: engine(10:15 .. 15:15 grid) == fam_V(first=615, last=915): {'PASS' if same else 'FAIL'} (trades {n7.sum():.0f} vs base {n1.sum():.0f})"); ok &= same
        for lab, kw in (('retrace (1, .5)', dict(retrace=(1.0, 0.5))), ('armed CH', dict(ch=3.0, ch_arm=1.0)), ('scale-out (2, .5)', dict(scaleout=(2.0, 0.5))),
                        ('unreachable retrace', dict(retrace=(1000.0, 0.5))), ('unreachable scale-out', dict(scaleout=(1000.0, 0.5)))):
            p8, n8, ac8, _ = engine(mk, **kw)
            q = ~ac8 & ~np.isnan(p1)
            same = np.allclose(p8[q], p1[q]) and np.allclose(n8[q], n1[q])
            if lab.startswith('unreachable'): same &= ac8.sum() == 0 and np.allclose(np.nan_to_num(p8, nan=-9e9), np.nan_to_num(p1, nan=-9e9))
            pr(f"(e) {nm}: {lab}: sessions untouched by the rule carry the base P&L exactly: {'PASS' if same else 'FAIL'} (acted on {ac8.sum()} of {q.sum() + ac8.sum()})"); ok &= same
    # A2 plumbing: ES states exist on the paired dates; a conf that always returns None reproduces the base
    for nm in ('NQ', 'MNQX'):
        mk = N.market(nm); st = es_state(PAIR[nm]); have = sum(1 for x in mk.days if x.date in st)
        p9, n9, ac9, _ = engine(mk, conf=lambda k, i, new: None, conf_mode='skip'); p1, n1, _, _ = engine(mk)
        same = np.array_equal(np.nan_to_num(p9, nan=-9e9), np.nan_to_num(p1, nan=-9e9)) and ac9.sum() == 0
        pr(f"(f) {nm}: second index {PAIR[nm]} states on {have} of {mk.n} sessions; conf=None reproduces the base: {'PASS' if same else 'FAIL'}"); ok &= same
    pr("ALL PASS" if ok else "AUDIT FAILED"); save(); return ok


# ------------------------------------------------------------------------------------------------ the battery
def main():
    below_normal(); t0 = time.time()
    pr(f"WEEKEND battery (WEEKEND_PLAN_20261031.txt) - {time.strftime('%Y-%m-%d %H:%M')} ET; K = {K_TRIALS} trials, bootstrap bar {P_BAR:.4f}")
    if not audit(): return
    mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    base = run_pair({}, mks); rb = joined(base)
    pr(f"\nlive rules {rb.index[0].date()} .. {rb.index[-1].date()}: ${rb.mean() * 252 * USD:,.0f}/yr, max DD ${CV.mdd(rb.values * USD):,.0f}, "
       f"MAR {NV.mar(rb):.3f}, Sharpe {NV.sharpe(rb):.2f}, trades {trades(base):,.0f}")
    series = {'live rules': rb * USD}; verdicts = {}

    def do(name, kw, exit_rule, with_conf=False, res=None, unseen_fn=None, n_early=None):
        pr(f"\n==== {name}")
        r = res if res is not None else run_pair(kw, mks, with_conf=with_conf)
        g, rc = grade(name, r, base, mks, kw, exit_rule, with_conf=with_conf, unseen_fn=unseen_fn, n_early=n_early)
        series[name] = rc * USD; verdicts[name] = g; return r, rc

    # ---- B1 phase ensemble
    shifted = run_pair(dict(first=615, last=915), mks); rs = joined(shifted)
    pr(f"\n[B1] shifted grid 10:15 .. 15:15 alone: ${rs.mean() * 252 * USD:,.0f}/yr, MAR {NV.mar(rs):.3f}, Sharpe {NV.sharpe(rs):.2f}, "
       f"max DD ${CV.mdd(rs.values * USD):,.0f}; correlation of daily returns with the live grid {np.corrcoef(rb.values, rs.values)[0, 1]:.3f}")
    def unseen_b1(mk):
        p0, n0, _, _ = engine(mk); pa, na, _, _ = engine(mk, first=615, last=915); return p0, n0, 0.5 * (p0 + pa), 0.5 * (n0 + na)
    do('B1 phase ensemble (half 10:00-grid + half 10:15-grid)', {}, False, res=blend(base, shifted), unseen_fn=unseen_b1)
    sh2 = run_pair(dict(first=615, last=945), mks); r2 = joined(blend(base, sh2))
    pr(f"   plateau neighbour (shift to 15:45): blend MAR {NV.mar(r2):.3f}, Sharpe {NV.sharpe(r2):.2f}, ${r2.mean() * 252 * USD:,.0f}/yr, "
       f"max DD ${CV.mdd(r2.values * USD):,.0f}")
    # ---- B2 K1 + chandelier
    k1 = run_pair(dict(gate=4, lunch=True, ema=True), mks); rk = joined(k1)
    pr(f"\n[B2] K1 alone (gate 4 + lunch + EMA): ${rk.mean() * 252 * USD:,.0f}/yr, MAR {NV.mar(rk):.3f}, max DD ${CV.mdd(rk.values * USD):,.0f}")
    do('B2 K1 + chandelier 3 x ATR', dict(gate=4, lunch=True, ema=True, ch=3.0), True)
    # ---- B3 scale-out
    do('B3 scale-out half at +2 widths', dict(scaleout=(2.0, 0.5)), True)
    # ---- C1 retrace trail
    do('C1 retrace trail (arm +1 width, keep 50% of best)', dict(retrace=(1.0, 0.5)), True)
    for lab, kw in (('arm +1.5 widths, keep 50%', dict(retrace=(1.5, 0.5))), ('arm +1 width, keep 60%', dict(retrace=(1.0, 0.6)))):
        rr = joined(run_pair(kw, mks))
        pr(f"   neighbour {lab}: MAR {NV.mar(rr):.3f}, Sharpe {NV.sharpe(rr):.2f}, ${rr.mean() * 252 * USD:,.0f}/yr, max DD ${CV.mdd(rr.values * USD):,.0f}")
    # ---- C2 armed chandelier
    do('C2 chandelier 3 x ATR armed at +1 width', dict(ch=3.0, ch_arm=1.0), True)
    ch_plain = run_pair(dict(ch=3.0), mks); rch = joined(ch_plain)
    pr(f"   reference, plain chandelier 3 x ATR (CHVAL): MAR {NV.mar(rch):.3f}, Sharpe {NV.sharpe(rch):.2f}, ${rch.mean() * 252 * USD:,.0f}/yr, "
       f"max DD ${CV.mdd(rch.values * USD):,.0f}")
    series['chandelier 3 x ATR (reference)'] = rch * USD
    # ---- A2 second index
    lg = {}
    base_logged = run_pair({}, mks, with_conf=True, log=lg)          # conf attached but no mode: logs each entry's confirmation
    ent = [e for nm in lg for e in lg[nm]['entries']]
    def stats(sel, lab):
        pts = np.array([e[4] for e in ent if sel(e)])
        if len(pts) == 0: pr(f"   {lab}: no entries"); return
        pr(f"   {lab}: {len(pts):,} entries, win {np.mean(pts > 1.0) * 100:.1f}%, mean {pts.mean():+.2f} pts, sum {pts.sum():,.0f} pts, t {pts.mean() / pts.std() * np.sqrt(len(pts)):+.2f}")
    pr("\n[A2] the live rules' entries by second-index confirmation at the entry check (information; 1.0 pt = the round trip cost):")
    stats(lambda e: e[3] is True, "S&P confirms (outside its band, same side)")
    stats(lambda e: e[3] is False, "S&P does not confirm")
    stats(lambda e: e[3] is None, "no S&P data")
    for part, pick in (('NQ 2010-2019', lambda e: True), ('10:00 entries only', lambda e: True)):
        pass
    y = np.array([1.0 if e[4] > 1.0 else 0.0 for e in ent if e[3] is not None]); f = np.array([1.0 if e[3] else 0.0 for e in ent if e[3] is not None])
    if len(y):
        auc = ((f[y == 1][:, None] > f[y == 0][None, :]).mean() + 0.5 * (f[y == 1][:, None] == f[y == 0][None, :]).mean())
        pr(f"   AUC of 'confirmed' for 'the trade wins' = {auc:.3f} (0.5 = none), on {len(y):,} entries with S&P data")
    do('A2a skip entries the S&P does not confirm', dict(conf_mode='skip'), False, with_conf=True)
    do('A2b 2 units when the S&P confirms, 1 otherwise', dict(conf_mode='size'), False, with_conf=True)

    # ---- D chandelier power
    dlt = (rch - rb) * USD; m, sd = dlt.mean(), dlt.std()
    m23 = dlt[dlt.index.year >= 2023].mean()
    pr(f"\n[D] WITH minus WITHOUT the chandelier, per session: mean {m:+.2f}, sd {sd:.1f} (16 years); 2023-26 mean {m23:+.2f}")
    for lab, mu in (('16-year mean', m), ('2023-26 mean', m23)):
        n = (2 * sd / mu) ** 2 if mu > 0 else float('inf')
        pr(f"   sessions for t = 2 at the {lab}: {n:,.0f} ({n / 252:,.1f} years)" if np.isfinite(n) else f"   sessions for t = 2 at the {lab}: never (mean <= 0)")

    pr("\n==== SUMMARY")
    pr(f" {'variant':<58} {'$/yr':>7} {'maxDD':>7} {'MAR':>6} {'Sharpe':>6}  P(MAR)  K1 K2 K3 K4 K5 K8  verdict")
    for nm_, g in verdicts.items():
        r = series[nm_] / USD
        pr(f" {nm_:<58} {r.mean() * 252 * USD:>7,.0f} {CV.mdd(r.values * USD):>7,.0f} {NV.mar(r):6.3f} {NV.sharpe(r):6.2f}  {g['P']:.4f}  "
           + ' '.join('ok' if g[kk] else '--' for kk in ('K1', 'K2', 'K3', 'K4', 'K5', 'K8')) + f"  {g['verdict']}")
    pd.DataFrame(series).to_csv(os.path.join(HERE, 'weekend_series.csv'))
    pr(f"\n{time.time() - t0:,.0f} s")
    save()


if __name__ == '__main__':
    if sys.argv[1:2] == ['audit']: audit()
    elif sys.argv[1:2] == ['run']: main()
    else: print(__doc__)
