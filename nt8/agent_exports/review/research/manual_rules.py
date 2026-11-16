import os, sys, time, types
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import src2_lib as L2
import structlab as SL

OUT = os.path.join(HERE, 'MANUAL_RESULTS_B.txt')
TICK = 0.25
STOP = 0.001076
RR = 5.0
LIVE = 2.35
N_TRIALS = 14
BAR = NormalDist().inv_cdf(1 - 0.05 / N_TRIALS)
WIN = {'nq': ('2010-07-01', '2019-05-05'), 'mnqx': ('2019-05-06', '2026-10-15'), 'es': ('2010-07-01', '2019-05-05'), 'mes': ('2019-05-06', '2026-10-15')}
CTX = ('C1', 'C2', 'C3'); MODES = ('M1', 'M2'); EXITS = ('X1', 'X2')
DESC = {'C1': 'above both EMAs', 'C2': 'above both + EMA50 > EMA200', 'C3': 'above both, EMA50 about to cross up',
        'M1': 'confirmed bounce, next hour open', 'M2': 'touch of the EMA, limit fill', 'X1': 'stop 30 / target 150', 'X2': 'stop 30 / BE at +30 / target 150'}
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def tt(x):
    x = np.asarray(x, float); s = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / (s / np.sqrt(len(x)))) if s > 0 else 0.0


def mirror(mk, br, side):
    if side == 'L':
        return dict(ao=mk.ao, ah=mk.ah, al=mk.al, ac=mk.ac, H=br.H, L=br.L, C=br.C, e50=br.e50, e200=br.e200)
    return dict(ao=-mk.ao, ah=-mk.al, al=-mk.ah, ac=-mk.ac, H=-br.L, L=-br.H, C=-br.C, e50=-br.e50, e200=-br.e200)


def contexts(a, atr):
    with np.errstate(invalid='ignore'):
        top = np.maximum(a['e50'], a['e200']); above = a['C'] > top
        gap = a['e200'] - a['e50']
        c3 = above & (a['e50'] < a['e200']) & (gap < atr) & (gap < SL.sh(gap, 5))
        return {'C1': above, 'C2': above & (a['e50'] > a['e200']), 'C3': c3}, top


def candidates(mk, br, a, ctx, top, mode):
    n = br.n; k = np.arange(1, n)
    ok = ctx[k - 1] & br.link[k - 1] & np.isfinite(top[k - 1])
    lvl = top[k - 1]
    with np.errstate(invalid='ignore'):
        if mode == 'M1':
            hit = ok & (a['L'][k] <= lvl) & (a['C'][k] > lvl) & br.link[k]
            kk = k[hit]; kk = kk[kk + 1 < n]
            j = br.b[kk + 1]; px = a['ao'][j]; first_stop_only = np.zeros(len(j), bool)
        else:
            hit = ok & (a['L'][k] <= lvl - TICK)
            kk = k[hit]; lv = top[kk - 1]
            j = np.empty(len(kk), np.int64)
            for q, (b0, e0, L_) in enumerate(zip(br.b[kk], br.e[kk], lv)):
                j[q] = b0 + int(np.argmax(a['al'][b0:e0 + 1] <= L_ - TICK))
            px = np.minimum(lv, a['ao'][j]); first_stop_only = np.ones(len(j), bool)
    win = (mk.m[j] >= 570) & (mk.m[j] < 930)
    return j[win], px[win], first_stop_only[win]


def exit_one(al, ah, ao, ac, j, i1, px, S, first_stop_only, be):
    stop = px - S; tgt = px + RR * S
    if first_stop_only:
        if al[j] <= stop: return stop, j
        s0 = j + 1
    else:
        s0 = j
    if s0 > i1: return ac[i1], i1
    L_, H_, O_ = al[s0:i1 + 1], ah[s0:i1 + 1], ao[s0:i1 + 1]
    W = len(L_); BIG = W + 9
    st = np.full(W, stop)
    if be:
        trig = (H_ - px) >= S
        if trig.any(): st[int(np.argmax(trig)) + 1:] = px
    hs = L_ <= st; ht = H_ >= tgt
    fs = int(np.argmax(hs)) if hs.any() else BIG; ft = int(np.argmax(ht)) if ht.any() else BIG
    if fs < BIG and fs <= ft: return min(st[fs], O_[fs]), s0 + fs
    if ft < BIG: return tgt, s0 + ft
    return ac[i1], i1


def run(mk, br, side, c, mode, xmode, cands):
    a = cands['arr'][side]; j, px, fso = cands[(side, c, mode)]
    raw = mk.o; be = xmode == 'X2'
    out = []
    dates = mk.date[j]
    for q in range(len(j)):
        d = pd.Timestamp(dates[q]); rng = mk.days.get(d)
        if rng is None: continue
        i0, i1 = rng
        if not (i0 <= j[q] <= i1): continue
        S = STOP * raw[j[q]]
        xp, jx = exit_one(a['al'], a['ah'], a['ao'], a['ac'], j[q], i1, px[q], S, fso[q], be)
        out.append((j[q], jx, (xp - px[q]) / raw[j[q]], d, side))
    return out


def one_at_a_time(tr):
    tr = sorted(tr, key=lambda r: r[0]); keep = []; busy = -1
    for r in tr:
        if r[0] > busy: keep.append(r); busy = r[1]
    return keep


def daily(tr, ticks, lo, hi):
    c = 2 * L2.side_cost(ticks) if ticks else 0.0
    if not tr: return pd.Series(dtype=float), np.array([])
    df = pd.DataFrame(tr, columns=['j', 'jx', 'g', 'd', 's'])
    df = df[(df.d >= pd.Timestamp(lo)) & (df.d <= pd.Timestamp(hi))]
    net = df.g.values - c
    return pd.Series(net, index=df.d.values).groupby(level=0).sum(), net


def prepare(name):
    mk = L2.Mins(name); br = SL.Bars(mk, '1h')
    cands = {'arr': {}}
    for side in ('L', 'S'):
        a = mirror(mk, br, side); cands['arr'][side] = a
        ctx, top = contexts(a, br.atr)
        for c in CTX:
            for mode in MODES: cands[(side, c, mode)] = candidates(mk, br, a, ctx[c], top, mode)
    return mk, br, cands


def audit():
    ok = True
    n = 20; px = 100.0; S = 1.0
    base = lambda: (np.full(n, 100.2), np.full(n, 100.8), np.full(n, 100.5), np.full(n, 100.5))
    al, ah, ao, ac = base(); al[3], ah[3] = 98.9, 106.0
    a1 = exit_one(al, ah, ao, ac, 0, n - 1, px, S, False, False) == (99.0, 3); ok &= a1
    al, ah, ao, ac = base(); ah[2] = 101.2; al[4] = 99.9
    a2 = exit_one(al, ah, ao, ac, 0, n - 1, px, S, False, True) == (100.0, 4)
    a2 &= exit_one(al, ah, ao, ac, 0, n - 1, px, S, False, False) == (100.5, n - 1); ok &= a2
    al, ah, ao, ac = base(); ah[5] = 105.5
    a3 = exit_one(al, ah, ao, ac, 0, n - 1, px, S, False, False) == (105.0, 5); ok &= a3
    al, ah, ao, ac = base(); al[0] = 98.5
    a4 = exit_one(al, ah, ao, ac, 0, n - 1, px, S, True, False) == (99.0, 0); ok &= a4
    al, ah, ao, ac = base(); al[6] = 97.0; ao[6] = 97.5
    a5 = exit_one(al, ah, ao, ac, 0, n - 1, px, S, False, False) == (97.5, 6); ok &= a5
    al, ah, ao, ac = base()
    a6 = exit_one(al, ah, ao, ac, 0, n - 1, px, S, False, False) == (100.5, n - 1); ok &= a6
    nm = 6 * 60; t0 = np.datetime64('2015-01-05T09:00'); t = t0 + np.arange(nm).astype('timedelta64[m]')
    o = np.full(nm, 100.0); hh = o + 0.25; ll = o - 0.25; c = o.copy()
    for h_ in range(6): o[h_ * 60:(h_ + 1) * 60] += h_; hh[h_ * 60:(h_ + 1) * 60] += h_; ll[h_ * 60:(h_ + 1) * 60] += h_; c[h_ * 60:(h_ + 1) * 60] += h_
    mk = types.SimpleNamespace(t=t, o=o, h=hh, l=ll, c=c, ao=o, ah=hh, al=ll, ac=c, v=np.ones(nm), iid=np.ones(nm, np.int64), shift=np.zeros(nm))
    tm = pd.DatetimeIndex(t); mk.m = (tm.hour * 60 + tm.minute).values
    br = SL.Bars(mk, '1h'); a = mirror(mk, br, 'L')
    top = np.array([np.nan, 101.0, 101.0, 101.0, 101.0, 101.0]); ctx = np.array([False, True, True, False, False, False])
    a['al'] = a['al'].copy(); a['al'][2 * 60 + 17] = 100.5
    a['L'] = a['L'].copy(); a['L'][2] = 100.5
    j, p, f = candidates(mk, br, a, ctx, top, 'M2')
    a7 = list(j) == [2 * 60 + 17] and list(p) == [101.0] and list(f) == [True]; ok &= a7
    say(f"audit exits: stop {a1}, breakeven {a2}, target {a3}, touch-minute stop {a4}, gap through stop at the open {a5}, flat at the close {a6} | "
        f"planted touch-bounce found once, limit fill at the level, after the context bar {a7} -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    say(f"manual_rules.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (MANUAL_PLAN.txt part B)  stop {STOP * 100:.4f}% of price (30 pt at 27,880), "
        f"target {RR:.0f}x; cost {LIVE} ticks + $0.50 a side; G1 bar t >= {BAR:.2f}")
    if not audit(): say("audit FAILED - nothing graded"); return
    P = {}
    for name in ('nq', 'mnqx', 'es', 'mes'):
        P[name] = prepare(name)
        br = P[name][1]
        y = pd.Series(1, index=SL.year_of(br.sess)).groupby(level=0).sum()
        full = y[(y.index > y.index.min()) & (y.index < y.index.max())]
        say(f"{name}: hourly bars per full year {full.min():,}..{full.max():,} (band 5,000-6,100) -> {'PASS' if ((full >= 5000) & (full <= 6100)).all() else 'FAIL'}; "
            f"{time.time() - T0:.0f} s")
    x3 = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close
    say(); say("=" * 128)
    say("RULE (long + short pooled) | NQ 2010-19 | MNQ 2019-26 holdout | ES / MES | trades/yr | win | losing years | drop-5 t | gross 0-cost NQ/MNQ | corr X3")
    say("=" * 128)
    survivors = []
    for c in CTX:
        for mode in MODES:
            for xm in EXITS:
                res = {}
                for name in P:
                    mk, br, cands = P[name]
                    tr = one_at_a_time(run(mk, br, 'L', c, mode, xm, cands) + run(mk, br, 'S', c, mode, xm, cands))
                    res[name] = tr
                s = {nm: daily(res[nm], LIVE, *WIN[nm]) for nm in res}
                g0 = {nm: daily(res[nm], 0, *WIN[nm]) for nm in ('nq', 'mnqx')}
                nq, mq = s['nq'][0], s['mnqx'][0]
                both = pd.concat([nq, mq]); yrs = both.groupby(both.index.year).sum()
                d5 = both.drop(both.sort_values(ascending=False).index[:5]) if len(both) > 5 else both
                ntr = (len(s['nq'][1]) + len(s['mnqx'][1])) / (16.3)
                win = float(np.mean(np.r_[s['nq'][1], s['mnqx'][1]] > 0)) if ntr else 0.0
                corr = both.reindex(x3.index).fillna(0).corr(x3) if len(both) else float('nan')
                es_m = s['es'][1].mean() if len(s['es'][1]) else float('nan'); mes_m = s['mes'][1].mean() if len(s['mes'][1]) else float('nan')
                g = {'G1': tt(nq) >= BAR, 'G2': mq.mean() > 0 and tt(mq) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2,
                     'G4': es_m > 0 and mes_m > 0, 'G5': tt(d5) >= 2.0}
                lab = f"{c} {mode} {xm}"
                U = L2.N_USD
                say(f"{lab:9s} NQ ${nq.sum() * U / 8.85:7,.0f}/yr t {tt(nq):+5.2f} | MNQ ${mq.sum() * U / 7.45:7,.0f}/yr t {tt(mq):+5.2f} | "
                    f"ES ${es_m * U:+6.2f} MES ${mes_m * U:+6.2f}/trade | {ntr:4.0f}/yr | win {win:.0%} | losing yrs {int((yrs < 0).sum())}/{len(yrs)} | "
                    f"drop5 t {tt(d5):+5.2f} | 0-cost t {tt(g0['nq'][0]):+5.2f}/{tt(g0['mnqx'][0]):+5.2f} | corr {corr:+.2f}")
                fails = [k for k, v in g.items() if not v]
                say(f"          {DESC[c]}; {DESC[mode]}; {DESC[xm]} -> {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
                if not fails: survivors.append(lab)
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
