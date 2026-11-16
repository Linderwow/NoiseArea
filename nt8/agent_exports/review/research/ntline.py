import os, sys, time, math
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2
import trendline_port as TP
import mama_port as MP

OUT = os.path.join(HERE, 'NTLINE_RESULTS.txt'); LINES = []
COSTS = {'live': (2.35, 0.61), '1 tick': (1.0, 0.50), 'zero': (0.0, 0.0)}
DISC = (pd.Timestamp('2010-07-01'), pd.Timestamp('2019-05-03')); HOLD = (pd.Timestamp('2019-05-06'), pd.Timestamp('2026-10-15'))


def say(s=''):
    print(s, flush=True); LINES.append(s)


def indicators(name):
    f = os.path.join(HERE, 'regime_work', f'ntline_{name}.npz')
    z = np.load(L2.CACHE(name)); o, h, l, c, sh = z['o'], z['h'], z['l'], z['c'], z['shift']
    if os.path.exists(f) and os.path.getmtime(f) > os.path.getmtime(L2.CACHE(name)):
        y = np.load(f); return y['val'], y['kind'], y['mama'], y['fama']
    ao, ah, al, ac = o + sh, h + sh, l + sh, c + sh
    t0 = time.time(); bars = list(zip(ao.tolist(), ah.tolist(), al.tolist(), ac.tolist()))
    val, kinds, _ = TP.trend_lines(bars, 5)
    kind = np.array([1 if k == 'high' else -1 if k == 'low' else 0 for k in kinds], np.int8); val = np.array(val, float)
    M, Fm = MP.mama([(None, a, b, cc, d) for a, b, cc, d in bars], 0.5, 0.05)
    np.savez_compressed(f, val=val, kind=kind, mama=np.array(M), fama=np.array(Fm))
    say(f"   {name}: indicators on {len(bars):,} bars in {time.time() - t0:.0f} s")
    return val, kind, np.array(M), np.array(Fm)


class Data:
    def __init__(self, name):
        z = np.load(L2.CACHE(name)); self.__dict__.update({k: z[k] for k in z.files}); self.name = name
        tt = pd.DatetimeIndex(self.t); self.m = (tt.hour * 60 + tt.minute).values
        self.tday = (tt + pd.Timedelta(hours=6)).normalize().values.astype('datetime64[D]')
        self.val, self.kind, self.mama, self.fama = indicators(name)
        ac = self.c + self.shift; v0, v1 = self.val[1:], self.val[:-1]; c0, c1 = ac[1:], ac[:-1]; k = self.kind[1:]
        ok = np.isfinite(v0) & np.isfinite(v1)
        up = ok & (c1 <= v1) & (c0 > v0) & (k == 1); dn = ok & (c1 >= v1) & (c0 < v0) & (k == -1)
        self.sig = np.r_[0, np.where(up, 1, np.where(dn, -1, 0))].astype(np.int8)
        self.agree = np.where(self.sig == 1, self.mama > self.fama, np.where(self.sig == -1, self.mama < self.fama, False))

    def segments(self, rth):
        inwin = ((self.m >= 570) & (self.m < 960)) if rth else ~((self.m >= 16 * 60 + 58) & (self.m < 18 * 60))
        new = inwin.copy(); new[1:] &= ~inwin[:-1] | (self.tday[1:] != self.tday[:-1]) | (self.iid[1:] != self.iid[:-1])
        seg = np.where(inwin, np.cumsum(new) - 1, -1); last = np.full(seg.max() + 1, -1)
        idx = np.where(seg >= 0)[0]; last[seg[idx]] = idx
        sig_ok = inwin & ((self.m < 959) if rth else True)
        return seg, last, sig_ok


def simulate(D, mode, use_mama, rth, pts=None):
    seg, last, sig_ok = D.segments(rth); n = len(D.c)
    s = D.sig.astype(int) * (D.agree if use_mama else 1)
    cand = np.where((s != 0) & sig_ok)[0]; cand = cand[cand + 1 < n]
    cand = cand[(seg[cand + 1] >= 0) & (seg[cand + 1] == seg[cand])]
    tr = []
    if mode == 'SAR':
        pos = 0; ent = 0.0; ei = -1; fx = -1
        for t in cand:
            e = t + 1; side = int(s[t])
            if pos and e > fx:
                tr.append((ei, fx, pos, ent, D.c[fx], 0)); pos = 0
            if pos == side: continue
            if pos:
                tr.append((ei, e, pos, ent, D.o[e], 0)); pos = 0
            pos, ent, ei, fx = side, D.o[e], e, last[seg[e]]
        if pos: tr.append((ei, fx, pos, ent, D.c[fx], 0))
    else:
        R = pts / L2.P_REF; busy = -1
        for t in cand:
            if t < busy: continue
            e = t + 1; side = int(s[t]); ent = D.o[e]; fx = last[seg[e]]
            tp = ent * (1 + side * R); sl = ent * (1 - side * R); ex = None; tie = 0
            for j in range(e, fx + 1):
                if (D.l[j] <= min(sl, tp) and D.h[j] >= max(sl, tp)): tie = 1
                if side == 1:
                    if D.l[j] <= sl: ex = (j, min(D.o[j], sl) if j > e else sl); break
                    if D.h[j] >= tp: ex = (j, max(D.o[j], tp) if j > e else tp); break
                else:
                    if D.h[j] >= sl: ex = (j, max(D.o[j], sl) if j > e else sl); break
                    if D.l[j] <= tp: ex = (j, min(D.o[j], tp) if j > e else tp); break
            if ex is None: ex = (fx, D.c[fx])
            tr.append((e, ex[0], side, ent, ex[1], tie)); busy = ex[0]
    a = np.array(tr, float) if tr else np.zeros((0, 6))
    return pd.DataFrame(dict(day=D.tday[a[:, 1].astype(int)] if len(a) else [], side=a[:, 2], ret=a[:, 2] * (a[:, 4] - a[:, 3]) / a[:, 3], tie=a[:, 5]))


def grade(T, D, win, cost):
    days = pd.DatetimeIndex(np.unique(D.tday[(D.tday >= np.datetime64(win[0].date())) & (D.tday <= np.datetime64(win[1].date()))]))
    tt = T[(T.day >= win[0]) & (T.day <= win[1])]
    c1 = (0.5 * cost[0] + cost[1]) / L2.N_USD
    usd = (tt.ret - 2 * c1) * L2.N_USD
    d = usd.groupby(tt.day).sum().reindex(days, fill_value=0.0)
    tstat = lambda x: x.mean() / x.std() * math.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else 0.0
    yrs = d.groupby(d.index.year).sum(); eq = d.cumsum(); dd = float((eq.cummax() - eq).max())
    d5 = d.drop(d.nlargest(5).index)
    return dict(n=len(tt), per_yr=len(tt) / (len(days) / 252), win=float((tt.ret > 2 * c1).mean()) if len(tt) else 0, pts=float(tt.ret.mean() * L2.P_REF) if len(tt) else 0,
                usd_yr=float(d.mean() * 252), t=tstat(d), t5=tstat(d5), yrs=yrs, neg=int((yrs < 0).sum()), dd=dd)


def main():
    t0 = time.time()
    say("=" * 150); say(f"NTLINE  {pd.Timestamp.now():%Y-%m-%d %H:%M}  NinjaTrader Trend lines(5) breaks on 1-minute bars (NTLINE_PLAN.txt); 1 micro, $ at MNQ {L2.P_REF:,.0f}; "
        f"cost per side live 2.35 ticks + $0.61"); say("=" * 150)
    NQ = Data('nq')
    sigs = NQ.sig[(NQ.tday >= np.datetime64('2010-07-01')) & (NQ.tday <= np.datetime64('2019-05-03'))]
    say(f"discovery NQ: break signals on {np.mean(sigs != 0) * 100:.2f}% of 1-minute bars (up {np.sum(sigs == 1):,}, down {np.sum(sigs == -1):,}); "
        f"MAMA agrees on {np.mean(NQ.agree[NQ.sig != 0]) * 100:.0f}% of them")
    trials = [('S1', 'SAR', False, False, None), ('S2', 'SAR', True, False, None), ('S3', 'SAR', False, True, None), ('S4', 'SAR', True, True, None),
              ('B1', 'BR', False, False, 5.0), ('B2', 'BR', True, False, 5.0), ('B3', 'BR', False, False, 10.0), ('B4', 'BR', True, False, 10.0)]
    desc = {'S1': 'stop-and-reverse, full session', 'S2': 'stop-and-reverse, full session, MAMA agrees', 'S3': 'stop-and-reverse, 09:30-16:00',
            'S4': 'stop-and-reverse, 09:30-16:00, MAMA agrees', 'B1': 'bracket 5 / 5 pts, full session', 'B2': 'bracket 5 / 5 pts, MAMA agrees',
            'B3': 'bracket 10 / 10 pts, full session', 'B4': 'bracket 10 / 10 pts, MAMA agrees'}
    say(); say("DISCOVERY NQ 2010-07 .. 2019-05 (kill: t < 2.7 at live cost, > 1 losing year, drop-5 t < 2.0)")
    say("   trial  rule                                         trades/yr  wins   pts/trade gross | $/yr live  t live | t 1 tick | t zero cost | losing yrs | drop-5 t | max DD")
    surv = []
    for code, mode, mm, rth, pts in trials:
        T = simulate(NQ, mode, mm, rth, pts); g = grade(T, NQ, DISC, COSTS['live']); g1 = grade(T, NQ, DISC, COSTS['1 tick']); g0 = grade(T, NQ, DISC, COSTS['zero'])
        ok = g['t'] >= 2.7 and g['neg'] <= 1 and g['t5'] >= 2.0 and g['per_yr'] >= 100
        say(f"   {code:5s}  {desc[code]:44s} {g['per_yr']:9,.0f}  {g['win'] * 100:4.0f}%  {g['pts']:+8.2f}        | ${g['usd_yr']:+9,.0f}  {g['t']:+6.2f} | {g1['t']:+8.2f} | {g0['t']:+11.2f} | "
            f"{g['neg']:4d} of {len(g['yrs'])} | {g['t5']:+8.2f} | ${g['dd']:,.0f}  {'PASS' if ok else 'dead'}")
        if ok: surv.append((code, mode, mm, rth, pts))
    if surv:
        say(); say("HOLDOUT MNQ 2019-05 .. 2026-10 (survivors only)"); MQ = Data('mnqx')
        for code, mode, mm, rth, pts in surv:
            T = simulate(MQ, mode, mm, rth, pts); g = grade(T, MQ, HOLD, COSTS['live'])
            say(f"   {code}: {g['per_yr']:,.0f} trades/yr, ${g['usd_yr']:+,.0f}/yr, t {g['t']:+.2f}, losing years {g['neg']} of {len(g['yrs'])}, max DD ${g['dd']:,.0f}")
    else:
        say(); say("HOLDOUT not opened: no trial passed discovery.")
    say(); say("DIAGNOSTIC (not a trial): brackets with every 'stop and target in the same bar' trade scored 0 instead of a stop, at zero cost")
    for code, mm, pts in (('B1', False, 5.0), ('B3', False, 10.0), ('B2', True, 5.0), ('B4', True, 10.0)):
        T = simulate(NQ, 'BR', mm, False, pts); T2 = T.assign(ret=np.where(T.tie > 0, 0.0, T.ret)); g = grade(T2, NQ, DISC, COSTS['zero'])
        say(f"   {code}: same-bar trades {T.tie.mean() * 100:.0f}% | the rest {g['pts']:+.2f} pts/trade gross, zero-cost t {g['t']:+.2f}; live cost on {g['per_yr']:,.0f} trades/yr = ${g['per_yr'] * 2 * (0.5 * 2.35 + 0.61):,.0f}/yr")
    say(f"run {time.time() - t0:.0f} s")
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n\n")


if __name__ == '__main__':
    main()
