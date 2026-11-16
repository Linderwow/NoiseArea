import os, sys, math, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2
import onemin as om
import msb

TICK = 0.25
N_USD = L2.N_USD
JOIN = L2.JOIN
N_SWING = 5
OUT = os.path.join(HERE, 'MSBTRADE_RESULTS.txt')
LINES = []
T0 = time.time()
TRIALS = [(tf, k, s) for tf in (1, 5) for k in (1, 2, 3) for s in ('rth', 'legal')]


def say(s=''):
    print(s, flush=True); LINES.append(s)


def code(tr):
    return f"{tr[0]}m {tr[1]}R {tr[2]}"


def bars(mk, tf):
    end = mk.t + np.timedelta64(1, 'm')
    if tf == 1:
        o, h, l, c, iid, endt = mk.ao, mk.ah, mk.al, mk.ac, mk.iid, end
    else:
        e = end.astype('int64')
        bucket = ((e + tf - 1) // tf) * tf
        brk = np.r_[True, (bucket[1:] != bucket[:-1]) | (mk.iid[1:] != mk.iid[:-1])]
        idx = np.flatnonzero(brk); last = np.r_[idx[1:], len(e)] - 1
        o, c = mk.ao[idx], mk.ac[last]
        h, l = np.maximum.reduceat(mk.ah, idx), np.minimum.reduceat(mk.al, idx)
        iid, endt = mk.iid[idx], bucket[idx].astype('datetime64[m]')
    em = endt.astype('int64') % 1440
    day = (endt + np.timedelta64(419, 'm')).astype('datetime64[D]')
    return dict(o=o, h=h, l=l, c=c, iid=iid, endm=em, day=day)


def structure_levels(O, H, L, C, n):
    isTop, isBtm = msb.swings_vectorised(H, L, n)
    N = len(C)
    sig = np.zeros(N, np.int8); ptop = np.full(N, np.nan); pbtm = np.full(N, np.nan)
    topP = btmP = np.nan; topX = btmX = True; bias = 0
    for i in range(2 * n, N):
        if isTop[i]: topP = H[i - n]; topX = False
        if isBtm[i]: btmP = L[i - n]; btmX = False
        bu = (not topX) and C[i] > topP
        bd = (not btmX) and C[i] < btmP
        if bu and bd:
            if C[i] >= O[i]: bd = False
            else: bu = False
            topX = btmX = True
        s = 0
        if bu:
            s = 1 if bias == 1 else 2; topX = True; bias = 1
        elif bd:
            s = -1 if bias == -1 else -2; btmX = True; bias = -1
        sig[i] = s
        if not topX: ptop[i] = topP
        if not btmX: pbtm[i] = btmP
    return sig, ptop, pbtm


def simulate(B, sig, ptop, pbtm, k, session, log=None):
    O, H, L, C, iid, em, day = B['o'], B['h'], B['l'], B['c'], B['iid'], B['endm'], B['day']
    N = len(C)
    if session == 'rth':
        allow = (em >= 9 * 60 + 35) & (em <= 15 * 60 + 30); flat = em == 16 * 60
    else:
        allow = (em >= 18 * 60 + 5) | (em <= 16 * 60 + 30); flat = em == 16 * 60 + 55
    trades = []; pos = 0; pend = 0; pstop = 0.0; entry = stop = tgt = R = 0.0; ei = 0
    n_sig = n_nolevel = n_badentry = 0
    for i in range(1, N):
        if pend:
            if iid[i] != iid[i - 1] or day[i] != day[i - 1]:
                pend = 0
            else:
                side = pend; pend = 0; e = O[i]; r = side * (e - pstop)
                if r <= 0:
                    n_badentry += 1
                else:
                    pos = side; entry = e; stop = pstop; R = r; ei = i
                    tgt = entry + side * max(1, round(k * R / TICK)) * TICK
        if pos:
            px = None; kind = None
            if pos > 0:
                if O[i] <= stop: px, kind = O[i], 'stop'
                elif L[i] <= stop: px, kind = stop, 'stop'
                elif H[i] >= tgt + TICK: px, kind = tgt, 'target'
            else:
                if O[i] >= stop: px, kind = O[i], 'stop'
                elif H[i] >= stop: px, kind = stop, 'stop'
                elif L[i] <= tgt - TICK: px, kind = tgt, 'target'
            if px is None and (flat[i] or i == N - 1 or day[i + 1] != day[i] or iid[i + 1] != iid[i]):
                px, kind = C[i], 'session'
            if px is not None:
                pts = pos * (px - entry)
                trades.append((day[i], pts, 1 if kind == 'target' else 0, 1 if kind == 'target' else 2, pts / R, kind))
                if log is not None: log.append((ei, i, pos, entry, stop, tgt, px, kind))
                pos = 0
        if pos == 0 and not pend and sig[i] != 0 and allow[i] and i < N - 1:
            n_sig += 1
            side = 1 if sig[i] > 0 else -1
            lv = pbtm[i] if side > 0 else ptop[i]
            if lv != lv:
                n_nolevel += 1
            else:
                pend = side; pstop = lv
    return trades, dict(signals=n_sig, no_level=n_nolevel, bad_entry=n_badentry)


def frame(mk, trades, keep):
    px = {d: mk.o[mk.days[d][0]] for d in mk.dates}
    res = {}; dropped = 0
    for d64, pts, lim, mkt, r, kind in trades:
        d = pd.Timestamp(d64)
        if d not in px: dropped += 1; continue
        g, a, b, n, w = res.get(d, (0.0, 0, 0, 0, 0))
        res[d] = (g + pts / px[d], a + lim, b + mkt, n + 1, w + (pts > 0))
    dates = [d for d in mk.dates if keep(d)]
    return om.to_frame({d: v for d, v in res.items() if keep(d)}, dates), dropped


class Market:
    def __init__(self, mk):
        self.mk = mk; self.cache = {}

    def run(self, tf, k, session):
        if tf not in self.cache:
            B = bars(self.mk, tf); self.cache[tf] = (B, structure_levels(B['o'], B['h'], B['l'], B['c'], N_SWING))
        B, (sig, ptop, pbtm) = self.cache[tf]
        return simulate(B, sig, ptop, pbtm, k, session)


def r_stats(trades):
    if not trades: return float('nan'), float('nan')
    r = np.array([t[4] for t in trades]); return float(np.mean(r)), float(np.mean(r > 0))


def check():
    say("=" * 118); say("PARITY: structure_levels vs msb.structure (the chart's rules, 473/473 on the live chart)"); say("=" * 118)
    ok = True
    for nm in ('nq', 'mnqx'):
        mk = om.mins(nm)
        for tf in (1, 5):
            B = bars(mk, tf); n = min(len(B['c']), 400_000)
            O, H, L, C = (B[x][:n] for x in ('o', 'h', 'l', 'c'))
            s1, _, _ = structure_levels(O, H, L, C, N_SWING); s2, _, _ = msb.structure(O, H, L, C, N_SWING)
            same = bool((s1 == s2).all()); ok &= same
            say(f"   {nm} {tf}-minute, first {n:,} bars: {int((s1 != 0).sum()):,} breaks, identical to msb.structure: {'PASS' if same else 'FAIL'}")
    return ok


def main():
    say(f"msbtrade.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (MSBTRADE_PLAN.txt)")
    if not check():
        say("parity FAILED - nothing graded"); return
    nq, mnq = Market(om.mins('nq')), Market(om.mins('mnqx'))
    say(); say("=" * 118)
    say("STAGE 1 - DISCOVERY, NQ 2010-07 .. 2019-05 ($0.50 commission every side + 1 tick per market fill; $ per micro at today's price)")
    say("=" * 118)
    say(f"{'trial':<14} {'trades/day':>10} {'win':>5} {'avg R gross':>11} {'net $/yr':>9} {'t net':>6} {'t 0-cost':>8} {'skipped':>8}  K1")
    frames = {}; alive = []
    for tr in TRIALS:
        ta, sa = nq.run(*tr); tb, sb = mnq.run(*tr)
        fa, da = frame(nq.mk, ta, om.keep_disc); fb, db = frame(mnq.mk, tb, om.keep_mnq)
        df = pd.concat([fa, fb]); frames[tr] = df
        dd = om.between(df, om.DISC); x = om.net(dd); xz = om.net(dd, gross=True)
        disc_trades = [t for t in ta if om.keep_disc(pd.Timestamp(t[0]))]
        ar, wr = r_stats(disc_trades)
        skip = (sa['no_level'] + sa['bad_entry']) / max(sa['signals'], 1)
        k1 = om.tstat(x) >= 2.0
        if k1: alive.append(tr)
        say(f"{code(tr):<14} {dd.ntr.sum() / len(dd):>10.2f} {wr:>5.0%} {ar:>+11.3f} {om.usd_yr(x):>9,.0f} {om.tstat(x):>6.2f} "
            f"{om.tstat(xz):>8.2f} {skip:>8.0%}  {'pass' if k1 else 'DEAD'}   ({time.time() - T0:.0f} s)")
    say(f"K1 survivors: {', '.join(code(t) for t in alive) if alive else 'none'}")
    survivors = []
    if alive:
        say(); say("=" * 118); say("STAGE 2 - K1 SURVIVORS"); say("=" * 118)
        es, mes, nt = Market(om.mins('es')), Market(om.mins('mes')), Market(om.mins_nt())
        for tr in alive:
            df = frames[tr]; x = om.net(df); x2 = om.net(df, 2)
            v, h = om.between(x, om.VAL), om.between(x, om.HOLD)
            yrs = x.groupby(x.index.year).sum(); neg = int((yrs < 0).sum()); d5 = x.sum() - x.nlargest(5).sum()
            g1, _ = frame(es.mk, es.run(*tr)[0], om.keep_disc); g2, _ = frame(mes.mk, mes.run(*tr)[0], om.keep_mnq)
            ges, gmes = om.net(g1, gross=True).mean(), om.net(g2, gross=True).mean(); sgn = np.sign(x.mean())
            fn, _ = frame(nt.mk, nt.run(*tr)[0], om.keep_mnq); xn = om.net(fn)
            k = {'K2': v.sum() > 0 and h.sum() > 0, 'K3': om.tstat(x) >= 3.0, 'K4': neg <= 3, 'K5': om.tstat(x2) >= 2.0, 'K6': d5 > 0,
                 'K7': np.sign(ges) == sgn and np.sign(gmes) == sgn, 'K8': np.sign(xn.mean()) == sgn and om.tstat(xn) >= 2.0}
            say(f"{code(tr)}: full ${om.usd_yr(x):,.0f}/yr t {om.tstat(x):.2f} | validation ${om.usd_yr(v):,.0f}/yr | holdout ${om.usd_yr(h):,.0f}/yr | "
                f"losing years {neg}/{len(yrs)} | 2 ticks t {om.tstat(x2):.2f} | drop-5 ${d5 * N_USD:,.0f} | ES {ges * N_USD * 252:+,.0f}/yr "
                f"MES {gmes * N_USD * 252:+,.0f}/yr | NinjaTrader ${om.usd_yr(xn):,.0f}/yr t {om.tstat(xn):.2f}")
            fails = [kk for kk, ok_ in k.items() if not ok_]
            say(f"   VERDICT: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
            if not fails: survivors.append(tr)
    say(); say("SURVIVORS: " + (", ".join(code(t) for t in survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    if sys.argv[1:2] == ['check']:
        check()
    else:
        main()
        with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
        print(f"wrote {OUT}")
