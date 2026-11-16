"""trendline.py - TRENDLINE_PLAN.txt: the TradingView "1 Trendline Strategy" (egoigor1976) ported line for line and the Scribd
trendline rules (close breakout, retest, channel bounce), graded like ONEMIN on the src2_lib 1-minute caches. Mode run writes
TRENDLINE_RESULTS.txt."""
import argparse
import math
import os
import sys
import time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view
import onemin as om

TICK = 0.25
FLAT_M = 16 * 60 + 58
REOPEN_M = 18 * 60
K = 12
P_BAR = 0.05 / K
OUT = os.path.join(HERE, 'TRENDLINE_RESULTS.txt')
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True)
    LINES.append(s)


class Bars:
    pass


def make_bars(mk, k):
    if k == 1:
        ts = mk.t.astype('datetime64[m]')
        o, h, l, c, iid = mk.ao, mk.ah, mk.al, mk.ac, mk.iid
    else:
        df = pd.DataFrame({'t': mk.t.astype('datetime64[m]').astype(np.int64), 'iid': mk.iid, 'o': mk.ao, 'h': mk.ah,
                           'l': mk.al, 'c': mk.ac})
        df['k'] = df.t // k * k
        last_iid = df.groupby('k').iid.transform('last')
        df = df[df.iid == last_iid]
        g = df.groupby('k', sort=True).agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'),
                                            iid=('iid', 'last')).reset_index()
        ts = g.k.values.astype('datetime64[m]')
        o, h, l, c, iid = (g[x].to_numpy() for x in ('o', 'h', 'l', 'c', 'iid'))
    B = Bars()
    B.k = k
    B.ts = ts
    B.o, B.h, B.l, B.c = (np.asarray(x, float) for x in (o, h, l, c))
    B.iid = np.asarray(iid)
    tsi = ts.astype(np.int64)
    day = tsi // 1440
    sm = tsi % 1440
    em = sm + k
    nxt = np.r_[em[1:] + 1440 * (day[1:] > day[:-1]), 10 ** 9]
    B.flat = (em <= FLAT_M) & (nxt > FLAT_M)
    B.block = (em > FLAT_M) & (sm < REOPEN_M)
    B.roll = np.r_[B.iid[1:] != B.iid[:-1], True]
    B.sess = (tsi + 360) // 1440
    return B


def atr(B, n):
    pc = np.r_[B.c[0], B.c[:-1]]
    tr = np.maximum(B.h - B.l, np.maximum(np.abs(B.h - pc), np.abs(B.l - pc)))
    return pd.Series(tr).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy()


def pivots(h, l, L):
    n = len(h)
    ph = np.zeros(n, bool)
    pl = np.zeros(n, bool)
    if n < 2 * L + 2:
        return ph, pl
    hw = sliding_window_view(h, L).max(axis=1)
    lw = sliding_window_view(l, L).min(axis=1)
    c = np.arange(L, n - L)
    top = (h[c] > hw[c - L]) & (h[c] >= hw[c + 1])
    btm = (l[c] < lw[c - L]) & (l[c] <= lw[c + 1])
    ph[c[top] + L] = True
    pl[c[btm] + L] = True
    return ph, pl


def htf_allow(B, minutes=240, span=250):
    tsi = B.ts.astype(np.int64)
    bucket = (tsi - REOPEN_M) // minutes
    last = np.flatnonzero(np.r_[bucket[1:] != bucket[:-1], True])
    ema = pd.Series(B.c[last]).ewm(span=span, adjust=False).mean().to_numpy()
    slope = np.r_[np.nan, np.diff(ema)]
    pos = np.searchsorted(last, np.arange(len(B.c)), side='right') - 1
    ok = pos >= 0
    e = np.where(ok, ema[np.clip(pos, 0, None)], np.nan)
    s = np.where(ok, slope[np.clip(pos, 0, None)], np.nan)
    return (B.c > e) & (s > 0), (B.c < e) & (s < 0)


class Trader:
    def __init__(self, B, legal, log=None):
        self.B = B
        self.legal = legal
        self.log = log
        self.pos = 0
        self.pend = None
        self.exit_next = False
        self.trades = []

    def flat_now(self):
        return self.pos == 0 and self.pend is None

    def signal(self, b, side, stop, tgt=None, r_mult=None):
        self.pend = (b, side, stop, tgt, r_mult)

    def can_fill(self, b, sb):
        return self.B.iid[b] == self.B.iid[sb] and not (self.legal and self.B.block[b])

    def _set(self, b, side, entry, stop, tgt, nmkt, nlim, line=None):
        self.pos = side
        self.entry = entry
        self.stop = stop
        self.tgt = tgt
        self.fb = b
        self.nmkt = nmkt
        self.nlim = nlim
        self.line = line
        self.exit_next = False

    def open_bar(self, b):
        if self.pend is None:
            return
        sb, side, stop, tgt, r_mult = self.pend
        self.pend = None
        if not self.can_fill(b, sb):
            return
        e = self.B.o[b]
        if stop is not None and (stop - e) * side >= 0:
            return
        if r_mult is not None:
            tgt = e + side * r_mult * abs(e - stop)
        self._set(b, side, e, stop, tgt, 1, 0)

    def enter_limit(self, b, side, px, stop, tgt, line=None):
        self._set(b, side, px, stop, tgt, 0, 1, line)

    def tgt_at(self, b):
        return self.tgt(b) if callable(self.tgt) else self.tgt

    def close(self, b, px, kind, lim):
        s = self.pos
        nm = self.nmkt + (0 if lim else 1)
        nl = self.nlim + (1 if lim else 0)
        self.trades.append((self.fb, b, s, self.entry, px, nm, nl, kind))
        if self.log is not None:
            self.log.append((self.fb, b, s, self.entry, px, kind))
        self.pos = 0
        self.exit_next = False

    def manage(self, b):
        if not self.pos:
            return
        B = self.B
        s = self.pos
        if self.exit_next:
            self.close(b, B.o[b], 'line', False)
            return
        st = self.stop
        if st is not None:
            if s > 0 and B.o[b] <= st:
                self.close(b, B.o[b], 'stop', False)
                return
            if s > 0 and B.l[b] <= st:
                self.close(b, st, 'stop', False)
                return
            if s < 0 and B.o[b] >= st:
                self.close(b, B.o[b], 'stop', False)
                return
            if s < 0 and B.h[b] >= st:
                self.close(b, st, 'stop', False)
                return
        tg = self.tgt_at(b) if self.tgt is not None else None
        if tg is not None and not (b == self.fb and self.nlim > 0):
            if s > 0 and B.h[b] >= tg + TICK:
                self.close(b, tg, 'target', True)
                return
            if s < 0 and B.l[b] <= tg - TICK:
                self.close(b, tg, 'target', True)
                return
        if (self.legal and B.flat[b]) or B.roll[b]:
            self.close(b, B.c[b], 'flat', False)


def run_tv(B, L=6, space=4.0, filt=False, legal=True, sl_m=7.0, tp_m=20.0, log=None, lines=None):
    o, h, l, c = B.o, B.h, B.l, B.c
    n = len(c)
    a14 = atr(B, 14)
    a200 = atr(B, 200)
    ph, pl = pivots(h, l, L)
    lok, sok = htf_allow(B) if filt else (None, None)
    upbin, dnbin = [], []
    vu = vd = broken = False
    U = D = None
    T = Trader(B, legal, log)
    for b in range(n):
        T.open_bar(b)
        T.manage(b)
        plup = pldn = False
        if ph[b]:
            upbin.insert(0, (h[b - L], b - L))
            if len(upbin) > 1:
                cur, bef = upbin[0], upbin[1]
                if cur[0] < bef[0]:
                    if broken:
                        vu = True
                    else:
                        vu = False
                        if len(upbin) > 3 and upbin[1][0] < upbin[2][0] and upbin[1][0] < upbin[3][0] and \
                                upbin[0][0] < upbin[2][0] and upbin[0][0] < upbin[3][0]:
                            vu = True
                if vu:
                    x1, y1, x2, y2 = bef[1], bef[0], cur[1], cur[0]
                    rem = False
                    for i in range(b - bef[1] + 1):
                        s = (y2 - y1) / (x2 - x1)
                        x2 = b - i
                        y2 = y2 - s
                        if l[b - i] > y2:
                            rem = True
                            break
                    if rem:
                        U = None
                        upbin = []
                        broken = True
                    else:
                        U = [bef[1], bef[0], cur[1], cur[0], min(a200[b] * 0.1, c[b] * 0.001) * space]
                        upbin = []
                        broken = False
                        if lines is not None:
                            lines.append(('up', b, tuple(U)))
        if U is not None:
            if l[b] > U[3]:
                U = None
                broken = True
                upbin = []
                plup = True
            else:
                s = (U[3] - U[1]) / (U[2] - U[0])
                U[2] = b
                U[3] = U[3] + s
        if pl[b]:
            dnbin.insert(0, (l[b - L], b - L))
            if len(dnbin) > 1:
                cur, bef = dnbin[0], dnbin[1]
                if cur[0] > bef[0]:
                    if broken:
                        vd = True
                    else:
                        vd = False
                        if len(dnbin) > 3 and dnbin[1][0] > dnbin[2][0] and dnbin[1][0] > dnbin[3][0] and \
                                dnbin[0][0] > dnbin[2][0] and dnbin[0][0] > dnbin[3][0]:
                            vd = True
                if vd:
                    x1, y1, x2, y2 = bef[1], bef[0], cur[1], cur[0]
                    rem = False
                    for i in range(b - bef[1] + 1):
                        s = (y2 - y1) / (x2 - x1)
                        x2 = b - i
                        y2 = y2 - s
                        if h[b - i] < y2:
                            rem = True
                            break
                    if rem:
                        D = None
                        dnbin = []
                        broken = True
                    else:
                        D = [bef[1], bef[0], cur[1], cur[0], min(a200[b] * 0.1, c[b] * 0.001) * space]
                        dnbin = []
                        broken = False
                        if lines is not None:
                            lines.append(('dn', b, tuple(D)))
        if D is not None:
            if h[b] < D[3] + D[4]:
                D = None
                broken = True
                dnbin = []
                pldn = True
            else:
                s = (D[3] - D[1]) / (D[2] - D[0])
                D[2] = b
                D[3] = D[3] + s
        if T.flat_now():
            if plup and (lok is None or lok[b]):
                T.signal(b, 1, c[b] - a14[b] * sl_m, c[b] + a14[b] * tp_m)
            elif pldn and (sok is None or sok[b]):
                T.signal(b, -1, c[b] + a14[b] * sl_m, c[b] - a14[b] * tp_m)
    return T.trades


def line_val(ln, b):
    i1, y1, i2, y2 = ln
    return y2 + (y2 - y1) / (i2 - i1) * (b - i2)


def run_scribd(B, rule, N=5, legal=True, r_mult=2.0, retest_bars=20, log=None):
    o, h, l, c = B.o, B.h, B.l, B.c
    n = len(c)
    ph, pl = pivots(h, l, N)
    sh, sl = [], []
    R = S = None
    r_spent = s_spent = True
    r_off = s_off = 0.0
    arm = None
    T = Trader(B, legal, log)
    for b in range(n):
        T.open_bar(b)
        if rule == 'SR' and arm is not None and T.pos == 0:
            side, ln, sb, exp = arm
            if b > exp or not T.can_fill(b, sb):
                arm = None
            else:
                px = line_val(ln, b)
                if (side > 0 and l[b] <= px - TICK) or (side < 0 and h[b] >= px + TICK):
                    pool = sl if side > 0 else sh
                    stop = next((p for p, _ in reversed(pool) if (p - px) * side < 0), None)
                    if stop is not None:
                        T.enter_limit(b, side, px, stop, px + side * r_mult * abs(px - stop))
                    arm = None
        if rule == 'SS' and T.flat_now() and b > 0:
            if S is not None and c[b - 1] > line_val(S, b - 1) and T.can_fill(b, b - 1):
                px = line_val(S, b)
                ln, off = S, s_off
                if l[b] <= px - TICK and off >= 2 * TICK:
                    T.enter_limit(b, 1, px, None, (lambda bb, ln=ln, off=off: line_val(ln, bb) + off), ln)
            if T.pos == 0 and R is not None and c[b - 1] < line_val(R, b - 1) and T.can_fill(b, b - 1):
                px = line_val(R, b)
                ln, off = R, r_off
                if h[b] >= px + TICK and -off >= 2 * TICK:
                    T.enter_limit(b, -1, px, None, (lambda bb, ln=ln, off=off: line_val(ln, bb) + off), ln)
        T.manage(b)
        if ph[b]:
            sh.append((h[b - N], b - N))
            if len(sh) >= 2 and sh[-1][0] < sh[-2][0]:
                R = (sh[-2][1], sh[-2][0], sh[-1][1], sh[-1][0])
                lo = l[sh[-2][1] + 1:sh[-1][1]]
                if len(lo):
                    j = sh[-2][1] + 1 + int(np.argmin(lo))
                    r_off = l[j] - line_val(R, j)
                else:
                    r_off = 0.0
                r_spent = False
            else:
                R = None
        if pl[b]:
            sl.append((l[b - N], b - N))
            if len(sl) >= 2 and sl[-1][0] > sl[-2][0]:
                S = (sl[-2][1], sl[-2][0], sl[-1][1], sl[-1][0])
                hi = h[sl[-2][1] + 1:sl[-1][1]]
                if len(hi):
                    j = sl[-2][1] + 1 + int(np.argmax(hi))
                    s_off = h[j] - line_val(S, j)
                else:
                    s_off = 0.0
                s_spent = False
            else:
                S = None
        if R is not None and not r_spent and c[b] > line_val(R, b):
            r_spent = True
            if rule == 'SB' and T.flat_now() and sl:
                T.signal(b, 1, sl[-1][0], r_mult=r_mult)
            elif rule == 'SR' and T.pos == 0 and arm is None:
                arm = (1, R, b, b + retest_bars)
        if S is not None and not s_spent and c[b] < line_val(S, b):
            s_spent = True
            if rule == 'SB' and T.flat_now() and sh:
                T.signal(b, -1, sh[-1][0], r_mult=r_mult)
            elif rule == 'SR' and T.pos == 0 and arm is None:
                arm = (-1, S, b, b + retest_bars)
        if rule == 'SS' and T.pos and T.line is not None and not T.exit_next:
            if (T.pos > 0 and c[b] < line_val(T.line, b)) or (T.pos < 0 and c[b] > line_val(T.line, b)):
                T.exit_next = True
    return T.trades


TRIALS = {'T1': ('tv', 1, dict(filt=False, legal=True)), 'T2': ('tv', 1, dict(filt=True, legal=True)),
          'T3': ('tv', 5, dict(filt=False, legal=True)), 'T4': ('tv', 5, dict(filt=True, legal=True)),
          'T5': ('tv', 1, dict(filt=False, legal=False)), 'T6': ('tv', 5, dict(filt=False, legal=False)),
          'B1': ('sc', 1, dict(rule='SB')), 'B5': ('sc', 5, dict(rule='SB')), 'R1': ('sc', 1, dict(rule='SR')),
          'R5': ('sc', 5, dict(rule='SR')), 'S1': ('sc', 1, dict(rule='SS')), 'S5': ('sc', 5, dict(rule='SS'))}
DESC = {'T1': 'TradingView defaults, 1 min, Bulenox-legal', 'T2': 'TradingView + 4h EMA filter, 1 min, legal',
        'T3': 'TradingView defaults, 5 min, Bulenox-legal', 'T4': 'TradingView + 4h EMA filter, 5 min, legal',
        'T5': 'TradingView defaults, 1 min, as published', 'T6': 'TradingView defaults, 5 min, as published',
        'B1': 'Scribd close breakout, 1 min', 'B5': 'Scribd close breakout, 5 min', 'R1': 'Scribd retest of the break, 1 min',
        'R5': 'Scribd retest of the break, 5 min', 'S1': 'Scribd channel bounce, 1 min', 'S5': 'Scribd channel bounce, 5 min'}


def run_trades(code, B):
    fam, k, kw = TRIALS[code]
    return run_tv(B, **kw) if fam == 'tv' else run_scribd(B, **kw)


def book(B, trades, mk, keep):
    dates = np.array([np.datetime64(d.date(), 'D') for d in mk.dates])
    px = np.array([mk.o[mk.days[d][0]] for d in mk.dates])
    res = {}
    for fb, xb, s, e, x, nm, nl, kind in trades:
        j = int(np.searchsorted(dates, np.datetime64(int(B.sess[xb]), 'D')))
        if j >= len(dates):
            continue
        d = mk.dates[j]
        if not keep(d):
            continue
        r = s * (x - e) / px[j]
        a = res.setdefault(d, [0.0, 0, 0, 0, 0])
        a[0] += r
        a[1] += nl
        a[2] += nm
        a[3] += 1
        a[4] += int(r > 0)
    return {d: tuple(v) for d, v in res.items()}


class Market:
    def __init__(self, mk):
        self.mk = mk
        self.bars = {}

    def bar(self, k):
        if k not in self.bars:
            self.bars[k] = make_bars(self.mk, k)
        return self.bars[k]


def frame(code, M, keep):
    k = TRIALS[code][1]
    B = M.bar(k)
    res = book(B, run_trades(code, B), M.mk, keep)
    return om.to_frame(res, [d for d in M.mk.dates if keep(d)])


def joined(code, Ma, Mb):
    return pd.concat([frame(code, Ma, om.keep_disc), frame(code, Mb, om.keep_mnq)])


def main():
    say(f"trendline.py run  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (TRENDLINE_PLAN.txt)")
    nq, mnq = Market(om.mins('nq')), Market(om.mins('mnqx'))
    say(f"[{time.time() - T0:4.0f} s] NQ {len(nq.mk.dates)} sessions, MNQ Databento {len(mnq.mk.dates)} sessions")
    import nav_lab as nl
    na, _ = nl.series('MNQDB')
    say("=" * 118)
    say("STAGE 1 - DISCOVERY, NQ 2010-07 .. 2019-05 (1 tick on market fills, $0.50 commission every side; $ per micro at today's price)")
    say("=" * 118)
    say(f"{'trial':<5} {'rule':<46} {'trades/day':>10} {'win':>5} {'net $/yr':>9} {'t net':>6} {'t 0-cost':>8}  K1")
    frames = {}
    alive = []
    for code in TRIALS:
        df = joined(code, nq, mnq)
        frames[code] = df
        dd = om.between(df, om.DISC)
        x = om.net(dd)
        xz = om.net(dd, gross=True)
        tr = dd.ntr.sum()
        w = dd.wins.sum() / tr if tr else float('nan')
        k1 = om.tstat(x) >= 2.0
        if k1:
            alive.append(code)
        say(f"{code:<5} {DESC[code]:<46} {tr / len(dd):>10.2f} {w:>5.0%} {om.usd_yr(x):>9,.0f} {om.tstat(x):>6.2f} "
            f"{om.tstat(xz):>8.2f}  {'pass' if k1 else 'DEAD'}   ({time.time() - T0:.0f} s)")
    say(f"K1 survivors: {', '.join(alive) if alive else 'none'}")
    survivors = []
    if alive:
        say()
        say("=" * 118)
        say("STAGE 2 - K1 SURVIVORS: validation, holdout and every other kill")
        say("=" * 118)
        es, mes, nt = Market(om.mins('es')), Market(om.mins('mes')), Market(om.mins_nt())
        from merge_lab import boot_p
        for code in alive:
            df = frames[code]
            x = om.net(df)
            x2 = om.net(df, 2)
            v, hd = om.between(x, om.VAL), om.between(x, om.HOLD)
            yrs = x.groupby(x.index.year).sum()
            neg = int((yrs < 0).sum())
            d5 = x.sum() - x.nlargest(5).sum()
            g = om.net(joined(code, es, mes), gross=True)
            ges, gmes = om.between(g, om.DISC).mean(), g[g.index > om.JOIN].mean()
            sgn = np.sign(x.mean())
            xn = om.net(frame(code, nt, om.keep_mnq))
            k = {'K2': v.sum() > 0 and hd.sum() > 0, 'K3': om.tstat(x) >= 3.0, 'K4': neg <= 3, 'K5': om.tstat(x2) >= 2.0,
                 'K6': d5 > 0, 'K7': np.sign(ges) == sgn and np.sign(gmes) == sgn,
                 'K8': np.sign(xn.mean()) == sgn and om.tstat(xn) >= 2.0}
            common = x.index.intersection(na.index)
            a = na.reindex(common)
            bb = x.reindex(common)
            p_add = boot_p(a + bb, a)
            say(f"{code} {DESC[code]}")
            say(f"   full 2010-2026: net ${om.usd_yr(x):,.0f}/yr, t {om.tstat(x):.2f}, Sharpe {om.sharpe(x):.2f}, max DD "
                f"${om.maxdd(x):,.0f}, {df.ntr.sum() / len(df):.2f} trades/day, win {df.wins.sum() / max(df.ntr.sum(), 1):.0%}")
            say(f"   validation 2019-22 ${om.usd_yr(v):,.0f}/yr t {om.tstat(v):.2f} | holdout 2023-26 ${om.usd_yr(hd):,.0f}/yr "
                f"t {om.tstat(hd):.2f} | losing years {neg}/{len(yrs)} | 2 ticks t {om.tstat(x2):.2f} | drop-5 "
                f"${d5 * om.N_USD:,.0f} | ES gross {ges * om.N_USD * 252:+,.0f}/yr, MES gross {gmes * om.N_USD * 252:+,.0f}/yr | "
                f"NinjaTrader MNQ ${om.usd_yr(xn):,.0f}/yr t {om.tstat(xn):.2f}")
            say("   per year $: " + ", ".join(f"{y} {v_ * om.N_USD:+,.0f}" for y, v_ in yrs.items()))
            say(f"   added to NoiseArea: Sharpe {om.sharpe(a + bb):.2f} vs {om.sharpe(a):.2f}, P {p_add:.4f} (bar {P_BAR:.4f})")
            fails = [kk for kk, ok_ in k.items() if not ok_]
            say(f"   VERDICT {code}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
            if not fails:
                survivors.append(code)
    say()
    say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('mode', choices=['run'])
    ap.parse_args()
    main()
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
