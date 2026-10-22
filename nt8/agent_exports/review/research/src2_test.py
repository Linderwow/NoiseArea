r"""SOURCES2 program (SOURCES2_PLAN.txt): stage 1 (every catalogued strategy as published), stage 2 (self-improving refinement on the
discovery decade, deflated Sharpe over every variant tried, then validation and a single holdout look).
    python src2_test.py smoke          3 random sessions' trades for every strategy (hand check; no P&L totals)
    python src2_test.py stage1         -> src2_stage1.csv + text
    python src2_test.py stage2         -> src2_stage2.csv + text (needs stage1)
"""
import os, sys, math, itertools, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L

TICK = 0.25
P = None                                                   # the Panel, loaded once
STRATS = {}                                                # id -> dict(fn, params, pub, family, source, kind)


def strat(sid, family, source, pub, choices=None, **params):
    def deco(fn):
        STRATS[sid] = dict(fn=fn, params=params, pub=pub, family=family, source=source, choices=choices or {}); return fn
    return deco


# ------------------------------------------------------------------------------------------------ fill helpers (1-minute bars)
def walk(mk, i0, i_last, dr, stp=None, tgt=None, check_first=True):
    """Exit (price, index) from bar i0 to i_last: stop first (its level, or the open if gapped through after the first bar), target as a
    limit traded through by a tick, else the close of i_last."""
    for i in range(i0, i_last + 1):
        if i > i0 or check_first:
            if stp is not None:
                if dr > 0 and mk.l[i] <= stp: return (stp if (i == i0 or mk.o[i] > stp) else mk.o[i]), i
                if dr < 0 and mk.h[i] >= stp: return (stp if (i == i0 or mk.o[i] < stp) else mk.o[i]), i
            if tgt is not None and ((dr > 0 and mk.h[i] >= tgt + TICK) or (dr < 0 and mk.l[i] <= tgt - TICK)): return tgt, i
    return mk.c[i_last], i_last


def at(mk, i0, i1, minute):
    """Index of the bar starting at `minute` (minutes after midnight) in session [i0, i1], or None."""
    k = i0 + (minute - 570)
    if i0 <= k <= i1 and mk.m[k] == minute: return k
    w = np.where(mk.m[i0:i1 + 1] == minute)[0]; return int(w[0]) + i0 if len(w) else None


def vwap(mk, i0, i1):
    tp = (mk.h[i0:i1 + 1] + mk.l[i0:i1 + 1] + mk.c[i0:i1 + 1]) / 3; v = mk.v[i0:i1 + 1]
    return np.cumsum(tp * v) / np.maximum(np.cumsum(v), 1)


FILT = None                                                # stage-2 filter: None / 'am' / 'pm' / 'with5' / 'against5'


def r5_open(k):
    """5-session return known at session k's open (sign only is used); 0 across the join."""
    D = P.daily
    if k < 6 or D.seg.iat[k - 6] != D.seg.iat[k]: return 0.0
    return D.aC.iat[k - 1] - D.aC.iat[k - 6]


def keep(dr, k, minute, at_close=False):
    if FILT is None: return True
    if FILT == 'am': return minute is not None and minute < 720
    if FILT == 'pm': return minute is not None and minute >= 720
    r = (P.daily.aC.iat[k] - P.daily.aC.iat[k - 5]) if at_close and k >= 5 and P.daily.seg.iat[k - 5] == P.daily.seg.iat[k] else (0.0 if at_close else r5_open(k))
    return np.sign(dr) == (np.sign(r) if FILT == 'with5' else -np.sign(r)) and r != 0


def intraday(fn, **kw):
    """Run fn(k, mk, i0, i1, D, **kw) -> list of (dir, entry_px, exit_px, sides[, entry_minute]) over every session; returns (gross,
    sides) series. The stage-2 filter (FILT) drops trades by entry time or by the 5-session return's sign."""
    D = P.daily; out = []
    for k, (d, mk) in enumerate(P.sess):
        i0, i1 = mk.days[d]
        for t in (fn(k, mk, i0, i1, D, **kw) or []):
            dr, e, x, n = t[:4]; minute = t[4] if len(t) > 4 else None
            if keep(dr, k, minute): out.append((k, dr * (x - e) / D.px.iat[k], n))
    return L.series(P, out)


def daily_hold(pos):
    """pos[k] in {-1,0,1}: position held from the RTH close of session k to the RTH close of k+1 (decided at k's close). Booked on k+1;
    never across the NQ/MNQ join (forced flat there). Sides booked when the position changes (at k's close)."""
    D = P.daily; aC, px, seg = D.aC.values, D.px.values, D.seg.values; n = len(D); out = []
    pos = np.asarray(pos, float).copy(); pos[np.r_[seg[1:] != seg[:-1], False]] = 0
    if FILT in ('am', 'pm'): return None
    if FILT: pos = np.array([p if p == 0 or keep(p, k, None, at_close=True) else 0.0 for k, p in enumerate(pos)])
    prev = 0.0
    for k in range(n - 1):
        if pos[k] != prev: out.append((k, 0.0, abs(pos[k] - prev)))
        if pos[k] != 0: out.append((k + 1, pos[k] * (aC[k + 1] - aC[k]) / px[k + 1], 0))
        prev = pos[k]
    if prev != 0: out.append((n - 1, 0.0, abs(prev)))
    return L.series(P, out)


def overnight(sig):
    """sig[k] in {-1,0,1}: hold from session k's RTH close to session k+1's RTH open (2 sides). Never across the join."""
    D = P.daily; aC, aO, px, seg = D.aC.values, D.aO.values, D.px.values, D.seg.values; out = []
    if FILT in ('am', 'pm'): return None
    for k in range(len(D) - 1):
        if sig[k] != 0 and seg[k] == seg[k + 1] and keep(sig[k], k, None, at_close=True): out.append((k + 1, sig[k] * (aO[k + 1] - aC[k]) / px[k + 1], 2 * abs(sig[k])))
    return L.series(P, out)


# ------------------------------------------------------------------------------------------------ strategies (appended per catalog)
@strat('REF_BH', 'reference', 'buy-and-hold (reference row, not a candidate)', None)
def ref_bh():
    return daily_hold(np.ones(len(P.daily)))


@strat('REF_ON', 'overnight', 'overnight-only long (Cliff, Cooper & Gulen 2008; reference)', '2008-01-01')
def ref_on():
    return overnight(np.ones(len(P.daily)))


@strat('REF_ID', 'intraday', 'intraday-only long, open to close (reference)', None)
def ref_id():
    def f(k, mk, i0, i1, D): return [(1, mk.o[i0], mk.c[i1], 2)]
    return intraday(f)


# ------------------------------------------------------------------------------------------------ stages
def noisearea():
    import regime_test as rt
    dts, R, *_ = rt.joined('MNQ'); return pd.Series(R[:, rt.A_DEF], index=dts)


def run(sid, **over):
    s = STRATS[sid]; kw = {**s['params'], **over}; return s['fn'](**kw)


def holm_bar(k, alpha=0.05):
    from scipy.stats import norm
    return float(norm.ppf(1 - alpha / k))


def market_ret():
    """The market's own close-to-close return per session (0 at the NQ/MNQ join) - the beta benchmark."""
    D = P.daily; r = np.r_[0.0, np.diff(D.aC.values) / D.px.values[1:]]; r[np.r_[False, D.seg.values[1:] != D.seg.values[:-1]]] = 0
    return pd.Series(r, index=P.dates)


def alpha_t(net, mkt):
    """t-stat of the intercept of net ~ a + b * market (OLS, plain standard errors)."""
    x = mkt.reindex(net.index).values; y = net.values; X = np.c_[np.ones(len(x)), x]
    b, *_ = np.linalg.lstsq(X, y, rcond=None); e = y - X @ b; s2 = e @ e / (len(y) - 2)
    cov = s2 * np.linalg.inv(X.T @ X); return float(b[0] / np.sqrt(cov[0, 0])), float(b[1])


GATES = {'B11'}                                            # gate type: the series is the change to NoiseArea; judged by the ADDITION test only


def _stage1_one(sid):
    s = STRATS[sid]; na = noisearea(); mkt = market_ret()
    g, n = run(sid); sc, net = L.evaluate(sid, g, n, na, pub=s['pub']); sc.update(family=s['family'], source=s['source'])
    sc['t_alpha'], sc['beta'] = alpha_t(net, mkt); return sc


def stage1(workers=4, only_new=False):
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from scipy.stats import norm
    ids = list(STRATS); rows = []; csv = os.path.join(HERE, 'src2_stage1.csv'); todo = ids
    if only_new and os.path.exists(csv):
        old = pd.read_csv(csv); keepc = [c for c in old.columns if c not in ('holm_bar', 'holm_pass', 'EDGE', 'ADDITION', 'GATE_PASS')]
        rows = old[old.label.isin(ids)][keepc].to_dict('records'); todo = [i for i in ids if i not in set(old.label)]
        print(f"  {len(rows)} strategies kept from the earlier stage-1 run, {len(todo)} new", flush=True)
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(None,)) as ex:
        fut = {ex.submit(_stage1_one, sid): sid for sid in todo}
        for f_ in as_completed(fut):
            sc = f_.result(); rows.append(sc)
            print(f"  {sc['label']:6s} ${sc['usd_yr']:+8,.0f}/yr t {sc['t']:+.2f} | disc {sc['t_disc']:+.2f} MNQ {sc['t_mnq']:+.2f} post {sc.get('t_post', 0):+.2f}"
                  f" | 2tk {sc['t_2tick']:+.2f} yrs+ {sc['yrs_pos'] * 100:.0f}% alpha {sc['t_alpha']:+.2f} | +NA {sc['sh_combo']:.2f} vs {sc['sh_na']:.2f}"
                  f" P {sc['p_add']:.3f}", flush=True)
    df = pd.DataFrame(rows).set_index('label').loc[ids].reset_index()
    cand = df[~df.label.str.startswith('REF') & ~df.label.isin(GATES)].copy(); k = len(cand)
    cand = cand.sort_values('t', ascending=False); holm = []; alive = True
    for i, r in enumerate(cand.itertuples()):
        bar = max(2.5, float(norm.ppf(1 - 0.05 / (k - i)))); alive = alive and r.t >= bar; holm.append((r.label, bar, alive))
    hb = {a: (b, c) for a, b, c in holm}
    df['holm_bar'] = df.label.map(lambda x: hb.get(x, (np.nan, False))[0]); df['holm_pass'] = df.label.map(lambda x: hb.get(x, (np.nan, False))[1])
    df['EDGE'] = df.holm_pass & (df.t_mnq >= 2.0) & (df.yrs_pos >= 0.6) & (df.t_2tick >= 1.5) & (df.t_alpha >= 2.0)
    e = df[df.EDGE].sort_values('p_add'); m = len(e); add = {}
    for i, r in enumerate(e.itertuples()):
        ok = r.sh_combo > r.sh_na and r.p_add < 0.01 / (m - i); add[r.label] = ok
        if not ok: break
    df['ADDITION'] = df.label.map(lambda x: add.get(x, False))
    df['GATE_PASS'] = df.label.isin(GATES) & (df.sh_combo > df.sh_na) & (df.p_add < 0.01)
    df.to_csv(os.path.join(HERE, 'src2_stage1.csv'), index=False)
    L_ = [f"=== STAGE 1: {k} catalogued strategies as published (+ references and {len(GATES)} gate), NQ 2010-2019 + MNQ 2019-2026, 1-min, today's costs ===",
          f"    Holm bar for the best: t >= {cand.iloc[0:1].label.map(lambda x: hb[x][0]).iloc[0] if k else 0:.2f}; EDGE also needs MNQ t >= 2.0, >= 60% years +, t >= 1.5 at 2 ticks, alpha t >= 2.0",
          "    id      $/yr      t    disc   MNQ   post   2tick  yrs+  alpha  NA+it vs NA   P_add  sides/d  verdict"]
    for r in df.sort_values('t', ascending=False).itertuples():
        v = 'REF' if r.label.startswith('REF') else ('GATE ' + ('PASS' if r.GATE_PASS else 'fail') if r.label in GATES else
             ('EDGE+ADDITION' if r.ADDITION else ('EDGE' if r.EDGE else ('holm-pass' if r.holm_pass else '-'))))
        L_.append(f"    {r.label:6s} {r.usd_yr:+8,.0f} {r.t:+6.2f} {r.t_disc:+6.2f} {r.t_mnq:+6.2f} {getattr(r, 't_post', 0):+6.2f} {r.t_2tick:+6.2f} "
                  f"{r.yrs_pos * 100:4.0f}% {r.t_alpha:+6.2f}  {r.sh_combo:5.2f} vs {r.sh_na:4.2f} {r.p_add:6.3f} {r.sides_day:6.1f}  {v}")
    txt = "\n".join(L_); print(txt); open(os.path.join(HERE, 'src2_stage1.txt'), 'w').write(txt + "\n"); return df


def smoke(ids=None):
    """Runs each strategy and prints 3 random traded sessions (date, gross points-equivalent, sides) for a hand check - no totals."""
    global P
    P = P or L.Panel(); rng = np.random.default_rng(3)
    for sid in (ids or STRATS):
        g, n = run(sid); on = np.where(n.values > 0)[0]
        pick = sorted(rng.choice(on, min(3, len(on)), replace=False)) if len(on) else []
        print(f"  {sid:6s} traded sessions {len(on):5d} ({len(on) / len(n) * 100:4.1f}%), mean sides {n.values[on].mean() if len(on) else 0:.1f} | " +
              "; ".join(f"{P.dates[k].date()} {g.iat[k] * P.daily.px.iat[k]:+.2f}pt {int(n.iat[k])}s" for k in pick), flush=True)


# ------------------------------------------------------------------------------------------------ stage 2 (self-improving refinement)
MULTS = (0.5, 0.75, 1.25, 1.5, 2.0)
FILTS = (None, 'am', 'pm', 'with5', 'against5')


def variants(sid):
    s = STRATS[sid]; base = s['params']; ch = s['choices']; singles = []
    for p, v in base.items():
        if p in ch: singles += [{p: c} for c in ch[p] if c != v]
        elif isinstance(v, bool): singles.append({p: not v})
        elif isinstance(v, int): singles += [{p: x} for x in sorted({max(1, int(round(v * m))) for m in MULTS} - {v})]
        elif isinstance(v, float): singles += [{p: round(v * m, 10)} for m in MULTS]
    out = [({}, f) for f in FILTS] + [(a, f) for a in singles for f in FILTS]
    out += [({**a, **b}, None) for a, b in itertools.combinations(singles, 2) if set(a).isdisjoint(b)]
    return out


def load_batches():
    import importlib
    for m in ('src2_strats_a', 'src2_strats_b', 'src2_strats_c'):
        try: importlib.import_module(m)
        except ModuleNotFoundError as e:
            if e.name != m: raise


def _init(only):
    global P
    load_batches(); P = L.Panel(only=only)


def _eval_disc(job):
    """Discovery-only statistics of one variant (per-day Sharpe on all discovery sessions, zero on no-trade days)."""
    global FILT
    sid, over, filt = job; FILT = filt
    try: res = run(sid, **over)
    finally: FILT = None
    if res is None: return None
    g, n = res; net = (g - n * L.side_cost(1)).values
    if net.std() == 0: return None
    from scipy.stats import skew, kurtosis
    return dict(sid=sid, over=json.dumps(over, sort_keys=True), filt=filt or '', sr=net.mean() / net.std(ddof=1), n=len(net),
                sk=float(skew(net)), ku=float(kurtosis(net, fisher=False)), t=L.tstat(net), usd_yr=net.sum() * L.N_USD / (len(net) / 252),
                days=int((n.values > 0).sum()))


def stage2(top=10, workers=3, prefix=None, prior=None):
    from concurrent.futures import ProcessPoolExecutor
    from scipy.stats import norm
    import rulemine as rm
    s1 = pd.read_csv(os.path.join(HERE, 'src2_stage1.csv')); s1 = s1[~s1.label.str.startswith('REF') & ~s1.label.isin(GATES)]
    if prefix: s1 = s1[s1.label.str.startswith(prefix)]
    pick = list(s1.sort_values('t_disc', ascending=False).label.head(top))
    jobs = [(sid, over, f) for sid in pick for over, f in variants(sid)]
    print(f"stage 2: refining {pick} - {len(jobs)} variants on discovery only", flush=True)
    with ProcessPoolExecutor(workers, initializer=_init, initargs=('disc',)) as ex:
        res = [r for r in ex.map(_eval_disc, jobs, chunksize=4) if r is not None]
    df = pd.DataFrame(res); pool = df.sr
    if prior:                                                                     # count every earlier stage-2 variant too
        old = pd.read_csv(os.path.join(HERE, prior)); pool = pd.concat([old.sr, df.sr]); print(f"  + {len(old)} earlier variants counted", flush=True)
    N = len(pool); var_sr = float(pool.var(ddof=1))
    df['dsr'] = [rm.dsr(r.sr, r.n, r.sk, r.ku, N, var_sr)[0] for r in df.itertuples()]
    sr0 = rm.dsr(0.0, 2000, 0, 3, N, var_sr)[1]
    df.sort_values('dsr', ascending=False).to_csv(os.path.join(HERE, 'src2_stage2_variants.csv'), index=False)
    L_ = [f"=== STAGE 2: {N} variants of {len(pick)} strategies on discovery (NQ 2010-2019); expected best Sharpe by luck "
          f"{sr0 * math.sqrt(252):.2f}/yr ===", "   best variant per strategy:"]
    best = df.sort_values('dsr', ascending=False).groupby('sid').head(1)
    for r in best.itertuples():
        L_.append(f"   {r.sid:7s} {r.over} {r.filt or '-':8s} Sharpe {r.sr * math.sqrt(252):+.2f}/yr t {r.t:+.2f} ${r.usd_yr:+,.0f}/yr DSR {r.dsr:.3f}")
    surv = best[best.dsr > 0.95]
    L_.append(f"   survivors (DSR > 0.95): {len(surv)}")
    if len(surv):
        global P
        P = L.Panel(); val = []
        for r in surv.itertuples():
            global FILT
            FILT = r.filt or None; g, n = run(r.sid, **json.loads(r.over)); FILT = None
            net = g - n * L.side_cost(1); v = net[(net.index >= L.VAL[0]) & (net.index <= L.VAL[1])]
            val.append((r, L.tstat(v.values), np.sign(r.sr)))
        m = len(val); order = sorted(val, key=lambda x: -x[1] * x[2]); passed = []
        for i, (r, tv, sg) in enumerate(order):
            bar = max(2.0, float(norm.ppf(1 - 0.05 / (m - i))))
            ok = tv * sg >= bar; L_.append(f"   validation {r.sid} {r.over} {r.filt or '-'}: t {tv:+.2f} (bar {bar:.2f}) -> {'PASS' if ok else 'fail'}")
            if not ok: break
            passed.append(r)
        L_.append(f"   validated: {len(passed)} -> holdout once")
        na = noisearea(); k_h = max(len(passed), 1)
        for r in passed[:10]:
            FILT = r.filt or None; g, n = run(r.sid, **json.loads(r.over)); FILT = None
            sc, net = L.evaluate(r.sid, g, n, na)
            h1 = net[(net.index >= L.HOLD[0])]; n2 = (g - n * L.side_cost(2)); h2 = n2[n2.index >= L.HOLD[0]]
            bar = max(2.0, float(norm.ppf(1 - 0.05 / k_h)))
            ta, _ = alpha_t(h1, market_ret())
            ok = L.tstat(h1.values) >= bar and L.tstat(h2.values) >= bar and ta >= 2.0          # amendment: holdout alpha t >= 2.0 too
            L_.append(f"   HOLDOUT {r.sid} {r.over} {r.filt or '-'}: t {L.tstat(h1.values):+.2f} (2 ticks {L.tstat(h2.values):+.2f}, bar {bar:.2f}) "
                      f"${h1.sum() * L.N_USD / (len(h1) / 252):+,.0f}/yr | +NA Sharpe {sc['sh_combo']:.2f} vs {sc['sh_na']:.2f} P {sc['p_add']:.3f} -> "
                      f"{'PASS' if ok else 'fail'}{' + ADDITION' if ok and sc['p_add'] < 0.01 else ''}")
    txt = "\n".join(L_); print(txt); open(os.path.join(HERE, 'src2_stage2.txt'), 'w').write(txt + "\n")

