import os, sys, glob, math, time
from collections import deque
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2

TICK = 0.25
N_USD = L2.N_USD
JOIN = L2.JOIN
START = pd.Timestamp('2010-07-01')
DISC = (pd.Timestamp('2010-07-01'), JOIN)
VAL = (pd.Timestamp('2019-05-06'), pd.Timestamp('2022-12-31'))
HOLD = (pd.Timestamp('2023-01-01'), pd.Timestamp('2026-12-31'))
K = 11; P_BAR = 0.05 / K
OUT = os.path.join(HERE, 'ONEMIN_RESULTS.txt')
LINES = []; T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def finish(mk):
    tt = pd.DatetimeIndex(mk.t); mk.m = (tt.hour * 60 + tt.minute).values; mk.date = tt.normalize().values.astype('datetime64[D]')
    mk.ac = mk.c + mk.shift; mk.ah = mk.h + mk.shift; mk.al = mk.l + mk.shift; mk.ao = mk.o + mk.shift
    rth = (mk.m >= 570) & (mk.m < 960); idx = np.where(rth)[0]; dts = mk.date[idx]
    starts = np.r_[0, np.where(dts[1:] != dts[:-1])[0] + 1]; ends = np.r_[starts[1:] - 1, len(idx) - 1]
    mk.days = {}
    for s, e in zip(starts, ends):
        i0, i1 = idx[s], idx[e]
        if mk.m[i0] == 570 and i1 - i0 >= 200 and mk.iid[i0] == mk.iid[i1]: mk.days[pd.Timestamp(dts[s])] = (i0, i1)
    mk.dates = sorted(mk.days)
    return mk


def mins(name):
    return L2.Mins(name)


def mins_nt():
    import yt_backtest as yb
    files = sorted(glob.glob(os.path.join(yb.STORE, 'MNQ', '*.parquet')), key=lambda p: (os.path.basename(p)[3:5], os.path.basename(p)[:2]))
    fr = []
    for order, f in enumerate(files):
        d = pd.read_parquet(f, columns=['time', 'open', 'high', 'low', 'close', 'volume']); d['iid'] = order; fr.append(d)
    df = pd.concat(fr); df['time'] = pd.to_datetime(df.time, utc=True)
    df = df.sort_values(['time', 'iid']).drop_duplicates('time', keep='last').reset_index(drop=True)
    mk = L2.Mins.__new__(L2.Mins); mk.name = 'mnq_nt'
    mk.t = ((df.time - pd.Timedelta(minutes=1)).dt.tz_convert('America/New_York').dt.tz_localize(None)).values.astype('datetime64[m]')
    mk.iid = df.iid.values.astype(np.int64)
    mk.o, mk.h, mk.l, mk.c, mk.v = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close', 'volume'))
    chg = np.where(mk.iid[1:] != mk.iid[:-1])[0] + 1; cum = np.zeros(len(mk.c))
    for k in chg: cum[:k] += mk.o[k] - mk.c[k - 1]
    mk.shift = cum
    return finish(mk)


def run_F(mk, keep, step, look=14, vm=1.0):
    res = {}; hist = []; prev_c = None
    for d in mk.dates:
        i0, i1 = mk.days[d]; sl = mk.m[i0:i1 + 1] - 570
        ao, ah, al, ac, v = (a[i0:i1 + 1] for a in (mk.ao, mk.ah, mk.al, mk.ac, mk.v))
        O = ao[0]; mv = np.full(390, np.nan); mv[sl] = np.abs(ac / O - 1)
        if keep(d) and len(hist) >= look and prev_c is not None:
            H = np.array(hist[-look:]); cnt = np.sum(~np.isnan(H), 0)
            with np.errstate(all='ignore'): sg = np.nanmean(H, 0)
            sg[cnt < (look + 1) // 2] = np.nan
            hi, lo = max(O, prev_c), min(O, prev_c)
            vw = np.cumsum((ah + al + ac) / 3 * v) / np.maximum(np.cumsum(v), 1)
            n = len(ac); pos = 0; entry = 0.0; p = 0.0; sides = 0; ent = 0; wins = 0
            for i in range(n - 1):
                e = 571 + sl[i]
                if e < 600 or e > 930 or (e - 600) % step: continue
                s = sg[sl[i]]
                if s != s: continue
                c = ac[i]; ub = hi * (1 + vm * s); lb = lo * (1 - vm * s)
                if pos == 0: new = 1 if c > ub else (-1 if c < lb else 0)
                elif pos == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                if new != pos:
                    px = ao[i + 1]
                    if pos: p += (px - entry) * pos; sides += 1; wins += (px - entry) * pos > 0
                    if new: entry = px; sides += 1; ent += 1
                    pos = new
            if pos: p += (ac[n - 1] - entry) * pos; sides += 1; wins += (ac[n - 1] - entry) * pos > 0
            res[d] = (p / mk.o[i0], 0, sides, ent, wins)
        hist.append(mv); prev_c = ac[-1]
    return res


def run_R(mk, keep, z, tgt_frac, tstop, log=None):
    res = {}; buf = deque(); bsum = 0.0
    for d in mk.dates:
        i0, i1 = mk.days[d]
        ao, ah, al, ac = (a[i0:i1 + 1] for a in (mk.ao, mk.ah, mk.al, mk.ac))
        endm = mk.m[i0:i1 + 1] + 1; n = len(ac); trade = keep(d)
        g = 0.0; lim = mkt = ntr = wins = 0; state = 0; side = 0; Lp = entry = tgt = stp = 0.0; fj = exp_j = 0
        for j in range(n):
            if trade and state == 1 and j > exp_j:
                state = 0
            if trade and state == 1:
                if (side > 0 and al[j] <= Lp - TICK) or (side < 0 and ah[j] >= Lp + TICK):
                    entry = Lp; state = 2; fj = j; lim += 1
                    if (side > 0 and al[j] <= stp) or (side < 0 and ah[j] >= stp):
                        px = min(stp, ao[j]) if side > 0 else max(stp, ao[j])
                        r = side * (px - entry); g += r; mkt += 1; ntr += 1; wins += r > 0; state = 0
                        if log is not None: log.append((d, j, j, side, entry, px, 'market'))
            elif trade and state == 2 and j > fj:
                px = None; is_lim = False
                if side > 0:
                    if ao[j] <= stp: px = ao[j]
                    elif al[j] <= stp: px = stp
                    elif tgt_frac and ah[j] >= tgt + TICK: px = tgt; is_lim = True
                else:
                    if ao[j] >= stp: px = ao[j]
                    elif ah[j] >= stp: px = stp
                    elif tgt_frac and al[j] <= tgt - TICK: px = tgt; is_lim = True
                if px is None and j >= fj + tstop: px = ac[j]
                if px is None and j == n - 1: px = ac[j]
                if px is not None:
                    r = side * (px - entry); g += r; ntr += 1; wins += r > 0; state = 0
                    if is_lim: lim += 1
                    else: mkt += 1
                    if log is not None: log.append((d, fj, j, side, entry, px, 'limit' if is_lim else 'market'))
            if trade and state == 2 and j == n - 1:
                r = side * (ac[j] - entry); g += r; mkt += 1; ntr += 1; wins += r > 0; state = 0
                if log is not None: log.append((d, fj, j, side, entry, ac[j], 'market'))
            if j >= 3:
                d3 = ac[j] - ac[j - 3]; a3 = abs(d3)
                if trade and state == 0 and 585 <= endm[j] <= 945 and len(buf) >= 200 and d3 != 0 and a3 >= z * bsum / len(buf):
                    side = -1 if d3 > 0 else 1
                    Lp = ac[j] - side * TICK
                    tgt = Lp + side * max(TICK, round(tgt_frac * a3 / TICK) * TICK) if tgt_frac else 0.0
                    stp = Lp - side * max(2 * TICK, round(a3 / TICK) * TICK)
                    exp_j = j + 3; state = 1
                buf.append(a3); bsum += a3
                if len(buf) > 390: bsum -= buf.popleft()
        if trade: res[d] = (g / mk.o[i0], lim, mkt, ntr, wins)
    return res


def run_S(mk, keep, z, tstop, log=None):
    res = {}; buf = deque(); bsum = 0.0
    for d in mk.dates:
        i0, i1 = mk.days[d]
        ao, ah, al, ac = (a[i0:i1 + 1] for a in (mk.ao, mk.ah, mk.al, mk.ac))
        endm = mk.m[i0:i1 + 1] + 1; n = len(ac); trade = keep(d)
        g = 0.0; mkt = ntr = wins = 0; state = 0; side = 0; entry = stp = 0.0; fj = 0; a3s = 0.0
        for j in range(n):
            if trade and state == 1:
                entry = ao[j]; fj = j; state = 2; mkt += 1
                stp = entry - side * max(2 * TICK, round(a3s / TICK) * TICK)
            if trade and state == 2:
                px = None
                if side > 0:
                    if ao[j] <= stp: px = ao[j]
                    elif al[j] <= stp: px = stp
                else:
                    if ao[j] >= stp: px = ao[j]
                    elif ah[j] >= stp: px = stp
                if px is None and (j >= fj + tstop or j == n - 1): px = ac[j]
                if px is not None:
                    r = side * (px - entry); g += r; mkt += 1; ntr += 1; wins += r > 0; state = 0
                    if log is not None: log.append((d, fj, j, side, entry, px, 'market'))
            if j >= 3:
                d3 = ac[j] - ac[j - 3]; a3 = abs(d3)
                if trade and state == 0 and j < n - 1 and 585 <= endm[j] <= 945 and len(buf) >= 200 and d3 != 0 and a3 >= z * bsum / len(buf):
                    side = 1 if d3 > 0 else -1; a3s = a3; state = 1
                buf.append(a3); bsum += a3
                if len(buf) > 390: bsum -= buf.popleft()
        if trade: res[d] = (g / mk.o[i0], 0, mkt, ntr, wins)
    return res


def run_G(mk, keep, which):
    res = {}; t = mk.t
    for d in mk.dates:
        if not keep(d): continue
        base = np.datetime64(d.strftime('%Y-%m-%dT00:00'), 'm')
        if which == 'L': a = base + np.timedelta64(180, 'm'); z = base + np.timedelta64(569, 'm')
        else: a = base - np.timedelta64(360, 'm'); z = base + np.timedelta64(179, 'm')
        r_end = a + np.timedelta64(15 if which == 'L' else 30, 'm')
        ia, ir, iz = np.searchsorted(t, a), np.searchsorted(t, r_end), np.searchsorted(t, z)
        if ir - ia < (10 if which == 'L' else 20) or iz - ir < 30 or mk.iid[ia] != mk.iid[iz - 1]:
            res[d] = (0.0, 0, 0, 0, 0); continue
        rh, rl = mk.h[ia:ir].max(), mk.l[ia:ir].min()
        c = mk.c[ir:iz - 1]; brk = np.where((c > rh) | (c < rl))[0]
        if not len(brk):
            res[d] = (0.0, 0, 0, 0, 0); continue
        j = ir + brk[0]; side = 1 if mk.c[j] > rh else -1; entry = mk.o[j + 1]; stp = rl if side > 0 else rh; px = None
        for k in range(j + 1, iz):
            if side > 0 and mk.o[k] <= stp: px = mk.o[k]
            elif side > 0 and mk.l[k] <= stp: px = stp
            elif side < 0 and mk.o[k] >= stp: px = mk.o[k]
            elif side < 0 and mk.h[k] >= stp: px = stp
            if px is not None: break
        if px is None: px = mk.c[iz - 1]
        r = side * (px - entry); i0 = mk.days[d][0]
        res[d] = (r / mk.o[i0], 0, 2, 1, int(r > 0))
    return res


TRIALS = {'F1': ('F', dict(step=1)), 'F5': ('F', dict(step=5)), 'F15': ('F', dict(step=15)),
          'R1': ('R', dict(z=3, tgt_frac=0.5, tstop=10)), 'R2': ('R', dict(z=4, tgt_frac=0.5, tstop=10)),
          'R3': ('R', dict(z=3, tgt_frac=0, tstop=5)), 'R4': ('R', dict(z=4, tgt_frac=0, tstop=5)),
          'G1': ('G', dict(which='L')), 'G2': ('G', dict(which='A')),
          'S3': ('S', dict(z=3, tstop=5)), 'S4': ('S', dict(z=4, tstop=5))}
RUN = {'F': run_F, 'R': run_R, 'G': run_G, 'S': run_S}
K1_BAR = {'S3': 2.5, 'S4': 2.5}
DESC = {'F1': 'NoiseArea band decided every 1 minute', 'F5': 'NoiseArea band decided every 5 minutes',
        'F15': 'NoiseArea band decided every 15 minutes', 'R1': 'spike fade z3, target half back + stop, 10 min',
        'R2': 'spike fade z4, target half back + stop, 10 min', 'R3': 'spike fade z3, stop, 5 min', 'R4': 'spike fade z4, stop, 5 min',
        'G1': 'London 03:00-03:15 range breakout, out 09:29', 'G2': 'Asia 18:00-18:30 range breakout, out 02:59',
        'S3': 'follow the spike z3, stop, 5 min (post-hoc)', 'S4': 'follow the spike z4, stop, 5 min (post-hoc)'}


def to_frame(res, dates):
    df = pd.DataFrame.from_dict(res, orient='index', columns=['g', 'lim', 'mkt', 'ntr', 'wins']).reindex(dates).fillna(0.0)
    return df


def net(df, ticks=1, gross=False):
    if gross: return df.g
    return df.g - (df.lim * 0.5 + df.mkt * (0.5 + 0.5 * ticks)) / N_USD


def tstat(x):
    return L2.tstat(np.asarray(x, float))


def sharpe(x):
    s = x.std(ddof=1); return float(x.mean() / s * math.sqrt(252)) if s > 0 else float('nan')


def usd_yr(x):
    return float(x.mean() * 252 * N_USD) if len(x) else float('nan')


def between(x, a):
    return x[(x.index >= a[0]) & (x.index <= a[1])]


def maxdd(x):
    eq = (x * N_USD).cumsum(); return float((eq.cummax() - eq).max())


def run_trial(code, mk_a, mk_b, a_keep, b_keep):
    fam, kw = TRIALS[code]
    ra = RUN[fam](mk_a, a_keep, **kw); rb = RUN[fam](mk_b, b_keep, **kw)
    return pd.concat([to_frame(ra, [d for d in mk_a.dates if a_keep(d)]), to_frame(rb, [d for d in mk_b.dates if b_keep(d)])])


def keep_disc(d): return START <= d <= JOIN
def keep_mnq(d): return d > JOIN


def main(mode):
    say(f"onemin.py {mode}  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (ONEMIN_PLAN.txt)")
    nq, mnq = mins('nq'), mins('mnqx')
    say(f"[{time.time() - T0:4.0f} s] NQ {len(nq.dates)} sessions, MNQ Databento {len(mnq.dates)} sessions")
    import nav_lab as nl
    na, _ = nl.series('MNQDB')
    ref =pd.concat([to_frame(run_F(nq, keep_disc, 30), [d for d in nq.dates if keep_disc(d)]),
                     to_frame(run_F(mnq, keep_mnq, 30), [d for d in mnq.dates if keep_mnq(d)])])
    rn = net(ref); common = rn.index.intersection(na.index)
    cor = float(np.corrcoef(rn.reindex(common), na.reindex(common))[0, 1])
    ua, ub = usd_yr(rn.reindex(common)), usd_yr(na.reindex(common))
    ok = cor >= 0.95 and abs(ua / ub - 1) <= 0.15
    say("=" * 118); say("ENGINE CHECK: F30 (published NoiseArea on the 1-minute engine) vs nav_lab NoiseArea (NQ + MNQ Databento)"); say("=" * 118)
    say(f"   {len(common)} common sessions, daily correlation {cor:.3f} (>= 0.95), ${ua:,.0f}/yr vs ${ub:,.0f}/yr "
        f"({ua / ub - 1:+.1%}, within 15 %) -> {'PASS' if ok else 'FAIL'}")
    if mode == 'check' or not ok:
        return
    say(); say("=" * 118); say("STAGE 1 - DISCOVERY, NQ 2010-07 .. 2019-05 (1 tick on market fills, $0.50 commission every side; $ per micro "
                               "at today's price)"); say("=" * 118)
    say(f"{'trial':<5} {'rule':<48} {'trades/day':>10} {'win':>5} {'net $/yr':>9} {'t net':>6} {'t 0-cost':>8}  K1")
    frames = {}; alive = []
    for code in TRIALS:
        df = run_trial(code, nq, mnq, keep_disc, keep_mnq); frames[code] = df
        dd = between(df, DISC); x = net(dd); xz = net(dd, gross=True)
        tr = dd.ntr.sum(); w = dd.wins.sum() / tr if tr else float('nan')
        k1 = tstat(x) >= K1_BAR.get(code, 2.0)
        if k1: alive.append(code)
        say(f"{code:<5} {DESC[code]:<48} {tr / len(dd):>10.2f} {w:>5.0%} {usd_yr(x):>9,.0f} {tstat(x):>6.2f} {tstat(xz):>8.2f}  "
            f"{'pass' if k1 else 'DEAD'}   ({time.time() - T0:.0f} s)")
    say(f"K1 survivors: {', '.join(alive) if alive else 'none'}")
    survivors = []
    if alive:
        say(); say("=" * 118); say("STAGE 2 - K1 SURVIVORS: validation, holdout and every other kill"); say("=" * 118)
        es, mes = mins('es'), mins('mes'); nt = mins_nt()
        say(f"[{time.time() - T0:4.0f} s] ES {len(es.dates)}, MES {len(mes.dates)}, MNQ NinjaTrader {len(nt.dates)} sessions")
        for code in alive:
            df = frames[code]; x = net(df); x2 = net(df, 2)
            v, h = between(x, VAL), between(x, HOLD)
            yrs = x.groupby(x.index.year).sum(); neg = int((yrs < 0).sum())
            d5 = x.sum() - x.nlargest(5).sum()
            sec = run_trial(code, es, mes, keep_disc, keep_mnq); g = net(sec, gross=True)
            ges, gmes = between(g, DISC).mean(), g[g.index > JOIN].mean(); sgn = np.sign(x.mean())
            fam, kw = TRIALS[code]
            ntf = to_frame(RUN[fam](nt, keep_mnq, **kw), [d for d in nt.dates if keep_mnq(d)]); xn = net(ntf)
            k = {'K2': v.sum() > 0 and h.sum() > 0, 'K3': tstat(x) >= 3.0, 'K4': neg <= 3, 'K5': tstat(x2) >= 2.0, 'K6': d5 > 0,
                 'K7': np.sign(ges) == sgn and np.sign(gmes) == sgn, 'K8': np.sign(xn.mean()) == sgn and tstat(xn) >= 2.0}
            common = x.index.intersection(na.index); a = na.reindex(common); b = x.reindex(common)
            from merge_lab import boot_p
            p_add = boot_p(a + b, a)
            extra = ''
            if fam == 'F':
                r30 = rn.reindex(x.index); p_rep = boot_p(x, r30); k['REPLACE'] = p_rep < P_BAR
                extra = f"; Sharpe vs F30 {sharpe(x):.2f}/{sharpe(r30):.2f}, P {p_rep:.4f}"
            say(f"{code} {DESC[code]}")
            say(f"   full 2010-2026: net ${usd_yr(x):,.0f}/yr, t {tstat(x):.2f}, Sharpe {sharpe(x):.2f}, max DD ${maxdd(x):,.0f}, "
                f"{df.ntr.sum() / len(df):.2f} trades/day, win {df.wins.sum() / max(df.ntr.sum(), 1):.0%}")
            say(f"   validation 2019-22 ${usd_yr(v):,.0f}/yr t {tstat(v):.2f} | holdout 2023-26 ${usd_yr(h):,.0f}/yr t {tstat(h):.2f} | "
                f"losing years {neg}/{len(yrs)} | 2 ticks t {tstat(x2):.2f} | drop-5 ${d5 * N_USD:,.0f} | ES gross {ges * N_USD * 252:+,.0f}/yr, "
                f"MES gross {gmes * N_USD * 252:+,.0f}/yr | NinjaTrader MNQ ${usd_yr(xn):,.0f}/yr t {tstat(xn):.2f}{extra}")
            say("   per year $: " + ", ".join(f"{y} {v_ * N_USD:+,.0f}" for y, v_ in yrs.items()))
            say(f"   added to NoiseArea: Sharpe {sharpe(a + b):.2f} vs {sharpe(a):.2f}, P {p_add:.4f} (bar {P_BAR:.4f})")
            fails = [kk for kk, ok_ in k.items() if not ok_ and kk != 'REPLACE']
            verdict = 'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'
            if fam == 'F' and not fails and not k['REPLACE']: verdict = 'passes alone, does not beat the published 30-minute rule'
            say(f"   VERDICT {code}: {verdict}")
            if not fails: survivors.append(code)
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    main(mode)
    if mode == 'run':
        with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
        print(f"wrote {OUT}")
