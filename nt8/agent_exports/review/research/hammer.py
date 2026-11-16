import os, sys, time, types
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2

OUT = os.path.join(HERE, 'HAMMER_RESULTS.txt')
TICK = 0.25
DISC = (pd.Timestamp('2010-07-01'), pd.Timestamp('2019-05-05'))
HOLD = (pd.Timestamp('2019-05-06'), pd.Timestamp('2026-10-16'))
BAR = 2.64
ONE_H = np.timedelta64(1, 'h')
TRIALS = [(s, e, x) for s in ('L', 'S') for e in ('M', 'C') for x in ('H1', 'H3', 'R2')]
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


class Hours:
    def __init__(self, mk):
        hk = mk.t.astype('datetime64[h]')
        b = np.r_[0, np.where(hk[1:] != hk[:-1])[0] + 1]; e = np.r_[b[1:] - 1, len(hk) - 1]
        hi = np.maximum.reduceat(mk.ah, b); lo = np.minimum.reduceat(mk.al, b)
        keep = ((e - b + 1) >= 30) & (mk.iid[b] == mk.iid[e])
        self.mk = mk; self.b, self.e = b[keep], e[keep]; self.hour = hk[b][keep]; self.n = len(self.b)
        self.O, self.C, self.H, self.L = mk.ao[self.b], mk.ac[self.e], hi[keep], lo[keep]
        self.iid_b, self.iid_e = mk.iid[self.b], mk.iid[self.e]
        self.hh = ((self.hour - self.hour.astype('datetime64[D]')) / ONE_H).astype(int)
        self.sess = (self.hour + np.timedelta64(6, 'h')).astype('datetime64[D]')
        pc = np.r_[np.nan, self.C[:-1]]
        tr = np.nanmax(np.vstack([self.H - self.L, np.abs(self.H - pc), np.abs(self.L - pc)]), axis=0)
        self.atr = pd.Series(tr).rolling(14).mean().values
        self.low10 = pd.Series(self.L).rolling(10).min().values; self.high10 = pd.Series(self.H).rolling(10).max().values
        R = self.H - self.L; B = np.abs(self.C - self.O)
        lw = np.minimum(self.O, self.C) - self.L; uw = self.H - np.maximum(self.O, self.C)
        big = (R >= 0.5 * self.atr) & (R > 0)
        self.ctx_low = big & (self.L <= self.low10) & (self.hh != 16)
        self.ctx_high = big & (self.H >= self.high10) & (self.hh != 16)
        self.hammer = self.ctx_low & (lw >= 2 * B) & (uw <= 0.25 * R)
        self.star = self.ctx_high & (uw >= 2 * B) & (lw <= 0.25 * R)

    def chain(self, k, n):
        last = None
        for j in range(k + 1, min(k + n, self.n - 1) + 1):
            p = j - 1
            if self.hh[p] == 16: break
            if self.iid_b[j] != self.iid_e[p]: return None
            if self.hour[j] != self.hour[p] + ONE_H: break
            last = j
        return last


def simulate(hs, side, entry, exit_, lo, hi, ticks=1, mask=None, log=None):
    mk = hs.mk; d = 1 if side == 'L' else -1
    pat = mask if mask is not None else (hs.hammer if side == 'L' else hs.star)
    sel = pat & (hs.sess >= np.datetime64(lo.date())) & (hs.sess <= np.datetime64(hi.date()))
    cost = 2 * L2.side_cost(ticks); busy = -1; rows = []
    for k in np.where(sel)[0]:
        if hs.e[k] < busy or k + 1 >= hs.n: continue
        last = hs.chain(k, 6 if exit_ == 'R2' else int(exit_[1]))
        if last is None: continue
        fb, fe = hs.b[k + 1], hs.e[k + 1]
        if entry == 'M':
            j0, px = fb, mk.ao[fb]
        else:
            stop = hs.H[k] + TICK if d > 0 else hs.L[k] - TICK; j0 = None
            for j in range(fb, fe + 1):
                if (d > 0 and mk.ah[j] >= stop) or (d < 0 and mk.al[j] <= stop):
                    j0 = j; px = max(stop, mk.ao[j]) if d > 0 else min(stop, mk.ao[j]); break
            if j0 is None: continue
        assert j0 > hs.e[k]
        jx = hs.e[last]; xp = mk.ac[jx]
        if exit_ == 'R2':
            sl = hs.L[k] - TICK if d > 0 else hs.H[k] + TICK
            if d * (px - sl) <= 0: continue
            tp = px + 2 * (px - sl)
            for j in range(j0, hs.e[last] + 1):
                first_c = entry == 'C' and j == j0
                if (d > 0 and mk.al[j] <= sl) or (d < 0 and mk.ah[j] >= sl):
                    jx = j; xp = sl if first_c or j == j0 else (min(sl, mk.ao[j]) if d > 0 else max(sl, mk.ao[j])); break
                if not first_c and ((d > 0 and mk.ah[j] >= tp) or (d < 0 and mk.al[j] <= tp)):
                    jx = j; xp = tp; break
        raw = px - mk.shift[j0]
        g = d * (xp - px) / raw
        rows.append((pd.Timestamp(hs.sess[k]), pd.Timestamp(mk.t[hs.b[k]]), pd.Timestamp(mk.t[j0]), pd.Timestamp(mk.t[jx]), d, px, xp, g, g - cost,
                     int(hs.hh[k])))
        busy = jx
    return pd.DataFrame(rows, columns=['sess', 'bar', 'entry', 'exit', 'dir', 'px', 'xp', 'gross', 'net', 'hh'])


def tstat(x):
    x = np.asarray(x, float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0


def line(df, lo, hi):
    if len(df) == 0: return "0 trades"
    yrs = (hi - lo).days / 365.25; usd = df.net * L2.N_USD
    return (f"{len(df):5d} trades ({len(df) / yrs:5.1f}/yr) | net ${usd.mean():+7.2f}/trade, t {tstat(df.net):+5.2f}, ${usd.sum() / yrs:+8,.0f}/yr | "
            f"win {np.mean(df.net > 0) * 100:4.1f}% | before cost ${df.gross.mean() * L2.N_USD:+7.2f}/trade (t {tstat(df.gross):+5.2f})")


def audit():
    t0 = np.datetime64('2015-01-04T18:00'); n = 20 * 60
    t = t0 + np.arange(n).astype('timedelta64[m]')
    o = np.zeros(n); h = np.zeros(n); l = np.zeros(n); c = np.zeros(n)
    lvl = 100.0
    for k in range(20):
        s = slice(k * 60, (k + 1) * 60)
        if k < 15: base = 100 - k * 0.5; o[s] = base; c[s] = base - 0.25; h[s] = base + 0.25; l[s] = base - 0.5
        elif k == 15:
            base = 92.5; o[s] = base; c[s] = base; h[s] = base; l[s] = base
            l[k * 60 + 20] = 88.0; c[(k + 1) * 60 - 1] = 92.25; o[k * 60] = 92.5
        else: base = 92.5 + (k - 15); o[s] = base; c[s] = base + 0.5; h[s] = base + 0.75; l[s] = base - 0.25
    mk = types.SimpleNamespace(t=t.astype('datetime64[m]'), o=o, h=h, l=l, c=c, ao=o, ah=h, al=l, ac=c, iid=np.ones(n, np.int64), shift=np.zeros(n))
    hs = Hours(mk)
    ok = list(np.where(hs.hammer)[0]) == [15] and not hs.star.any()
    df = simulate(hs, 'L', 'M', 'H1', pd.Timestamp('2015-01-01'), pd.Timestamp('2015-12-31'))
    ok &= len(df) == 1 and df.px.iloc[0] == 93.5 and df.xp.iloc[0] == 94.0 and abs(df.gross.iloc[0] - 0.5 / 93.5) < 1e-12
    say(f"audit synthetic: hammer bars {list(np.where(hs.hammer)[0])} (planted 15), M/H1 entry {df.px.iloc[0] if len(df) else '-'} "
        f"(open of bar 16 = 93.5), exit {df.xp.iloc[0] if len(df) else '-'} (close of bar 16 = 94.0) -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    say(f"hammer.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (HAMMER_PLAN.txt)")
    if not audit(): say("audit FAILED - nothing graded"); return
    nq = Hours(L2.Mins('nq'))
    yrs = pd.Series(1, index=pd.DatetimeIndex(nq.sess)).groupby(lambda x: x.year).sum()
    full = yrs[(yrs.index >= 2011) & (yrs.index <= 2018)]
    ok = bool(((full >= 5000) & (full <= 6000)).all())
    say(f"audit bars per full year NQ 2011-2018: {full.min():,} .. {full.max():,} -> {'PASS' if ok else 'FAIL'}")
    if not ok: return
    rng = np.random.default_rng(7); ix = rng.choice(np.where(nq.hammer)[0], 3, replace=False)
    for k in sorted(ix):
        say(f"   hammer {pd.Timestamp(nq.mk.t[nq.b[k]]):%Y-%m-%d %H:%M} O {nq.O[k]:.2f} H {nq.H[k]:.2f} L {nq.L[k]:.2f} C {nq.C[k]:.2f} | ATR {nq.atr[k]:.2f} | "
            f"10-bar low {nq.low10[k]:.2f}")
    say(f"patterns in discovery: hammers {int((nq.hammer & (nq.sess <= np.datetime64(DISC[1].date()))).sum())}, shooting stars "
        f"{int((nq.star & (nq.sess <= np.datetime64(DISC[1].date()))).sum())}")
    say(); say("=" * 120); say("STAGE 1 - DISCOVERY NQ 2010-07 .. 2019-05 ($ at today's MNQ price, 1 tick + $0.50 a side)"); say("=" * 120)
    res = {}
    for tr in TRIALS:
        df = simulate(nq, *tr, *DISC); res[tr] = df
        say(f"{''.join(tr[:2])}-{tr[2]:2s} {'hammer long ' if tr[0] == 'L' else 'star short  '}{line(df, *DISC)}")
    best = max(TRIALS, key=lambda tr: tstat(res[tr].net) if len(res[tr]) else -9)
    tb = tstat(res[best].net)
    say(f"best discovery variant {''.join(best[:2])}-{best[2]}: net t {tb:+.2f} vs the bar {BAR}")
    say(); say("DIAGNOSTICS (not trials)")
    for side, m in (('L', nq.ctx_low), ('S', nq.ctx_high)):
        c = simulate(nq, side, 'M', 'H3', *DISC, mask=m)
        say(f"control {side} any shape at the 10-bar extreme, M-H3: {line(c, *DISC)}")
    for tr in (('L', 'M', 'H3'), ('S', 'M', 'H3')):
        df = res[tr]; cash = df.hh.between(10, 15)
        say(f"{''.join(tr[:2])}-{tr[2]} pattern bar 10:00-15:00: {line(df[cash], *DISC)}")
        say(f"{''.join(tr[:2])}-{tr[2]} pattern bar overnight:   {line(df[~cash], *DISC)}")
    if tb < BAR:
        say(); say(f"VERDICT: DEAD - no variant clears t {BAR} on discovery; the holdout (MNQ 2019-2026) was not opened"); return
    say(); say("=" * 120); say(f"STAGE 2 - HOLDOUT for {''.join(best[:2])}-{best[2]} only"); say("=" * 120)
    mq = Hours(L2.Mins('mnqx')); h = simulate(mq, *best, *HOLD); h2 = simulate(mq, *best, *HOLD, ticks=2)
    yr = h.groupby(h.sess.dt.year).net.sum()
    drop = h.drop(h.net.nlargest(5).index)
    es = simulate(Hours(L2.Mins('es')), *best, *DISC); mes = simulate(Hours(L2.Mins('mes')), *best, *HOLD)
    g = {'K1': tstat(h.net) >= 2.0, 'K2': int((yr < 0).sum()) <= 2, 'K3': drop.net.mean() > 0,
         'K4': len(es) > 0 and len(mes) > 0 and es.net.mean() > 0 and mes.net.mean() > 0, 'K5': h2.net.mean() > 0}
    say(f"MNQ holdout: {line(h, *HOLD)}"); say("per year $: " + "; ".join(f"{y} {v * L2.N_USD:+,.0f}" for y, v in yr.items()))
    say(f"drop 5 best: mean ${drop.net.mean() * L2.N_USD:+.2f}/trade | 2 ticks: {line(h2, *HOLD)}")
    say(f"ES 2010-19: {line(es, *DISC)}"); say(f"MES 2019-26: {line(mes, *HOLD)}")
    fails = [k for k, v in g.items() if not v]
    say(f"VERDICT: {'SURVIVOR - forward test only' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")


if __name__ == '__main__':
    main()
    say(f"run {time.time() - T0:.0f} s")
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
