import os, sys, math, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2
import onemin as om
from merge_lab import boot_p

N_USD = L2.N_USD
JOIN = L2.JOIN
OUT = os.path.join(HERE, 'NAPLUS_RESULTS.txt')
K = 8
P_BAR = 0.05 / K
TRIALS = {'Q1': dict(trend='filter'), 'Q2': dict(trend='size'), 'Q3': dict(late='all'), 'Q4': dict(late='winners'),
          'Q5': dict(ema=200), 'Q6': dict(ema=500), 'Q7': dict(cross=1), 'Q8': dict(cross=5)}
DESC = {'Q1': 'trend filter (200-session mean)', 'Q2': 'half size against the 200-session trend', 'Q3': 'hold to 16:55 ET',
        'Q4': 'hold winners to 16:55 ET', 'Q5': 'EMA(200) 1-minute filter', 'Q6': 'EMA(500) 1-minute filter',
        'Q7': 'EMA 50/200 crossover, 1-minute', 'Q8': 'EMA 50/200 crossover, 5-minute'}
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def trend_map(mk):
    closes = np.array([mk.ac[mk.days[d][1]] for d in mk.dates]); out = {}
    for k, d in enumerate(mk.dates):
        if k < 201: out[d] = 0; continue
        prev, mean = closes[k - 1], closes[k - 201:k - 1].mean()
        out[d] = 1 if prev > mean else (-1 if prev < mean else 0)
    return out


def ema(mk, n):
    return pd.Series(mk.ac).ewm(span=n, adjust=False).mean().to_numpy()


def cross_state(mk, tf):
    if tf == 1:
        c = pd.Series(mk.ac)
        return np.sign(c.ewm(span=50, adjust=False).mean() - c.ewm(span=200, adjust=False).mean()).to_numpy()
    end = (mk.t + np.timedelta64(1, 'm')).astype('int64')
    bucket = ((end + tf - 1) // tf) * tf
    brk = np.r_[True, (bucket[1:] != bucket[:-1]) | (mk.iid[1:] != mk.iid[:-1])]
    grp = np.cumsum(brk) - 1
    last = np.r_[np.flatnonzero(brk)[1:], len(end)] - 1
    c = pd.Series(mk.ac[last])
    st = np.sign(c.ewm(span=50, adjust=False).mean() - c.ewm(span=200, adjust=False).mean()).to_numpy()
    return st[grp]


def late_index(mk, i1):
    j = i1; N = len(mk.ac)
    while j + 1 < N and mk.iid[j + 1] == mk.iid[i1] and mk.date[j + 1] == mk.date[i1] and mk.m[j + 1] <= 1014: j += 1
    return j


def run(mk, keep, trend=None, late=None, ema=None, tmap=None, earr=None, carr=None, look=14, vm=1.0, step=30):
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
            td = tmap.get(d, 0) if tmap is not None else 0
            n = len(ac); pos = 0; size = 0.0; entry = 0.0; p = 0.0; sides = 0.0; ent = 0; wins = 0
            for i in range(n - 1):
                e = 571 + sl[i]
                if e < 600 or e > 930 or (e - 600) % step: continue
                s = sg[sl[i]]
                if s != s: continue
                c = ac[i]; ub = hi * (1 + vm * s); lb = lo * (1 - vm * s)
                if pos == 0: new = 1 if c > ub else (-1 if c < lb else 0)
                elif pos == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
                else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
                if new != 0 and new != pos:
                    if trend == 'filter' and td != 0 and new != td: new = 0
                    if earr is not None:
                        ev = earr[i0 + i]
                        if (new > 0 and not c > ev) or (new < 0 and not c < ev): new = 0
                    if carr is not None and carr[i0 + i] != new: new = 0
                if new != pos:
                    px = ao[i + 1]
                    if pos: p += (px - entry) * pos * size; sides += size; wins += (px - entry) * pos > 0
                    if new:
                        entry = px; size = 0.5 if (trend == 'size' and td != 0 and new != td) else 1.0; sides += size; ent += 1
                    pos = new
            if pos:
                xp = ac[n - 1]
                if late == 'all' or (late == 'winners' and (xp - entry) * pos > 0): xp = mk.ac[late_index(mk, i1)]
                p += (xp - entry) * pos * size; sides += size; wins += (xp - entry) * pos > 0
            res[d] = (p / mk.o[i0], 0, sides, ent, wins)
        hist.append(mv); prev_c = ac[-1]
    return res


def series(mk, keep, code, ctx):
    kw = dict(TRIALS[code]) if code != 'N' else {}
    if 'trend' in kw: kw['tmap'] = ctx['tmap']
    if 'ema' in kw: kw['earr'] = ctx['ema'][kw['ema']]
    if 'cross' in kw: kw['carr'] = ctx['cross'][kw.pop('cross')]
    return om.to_frame(run(mk, keep, **kw), [d for d in mk.dates if keep(d)])


def context(mk):
    return {'tmap': trend_map(mk), 'ema': {200: ema(mk, 200), 500: ema(mk, 500)}, 'cross': {1: cross_state(mk, 1), 5: cross_state(mk, 5)}}


def sharpe(x):
    s = x.std(ddof=1); return float(x.mean() / s * math.sqrt(252)) if s > 0 else float('nan')


def mar(x):
    dd = om.maxdd(x); return om.usd_yr(x) / dd if dd > 0 else float('nan')


def main():
    say(f"naplus.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (NAPLUS_PLAN.txt)")
    M = {'NQ': om.mins('nq'), 'MNQ': om.mins('mnqx'), 'NT': om.mins_nt(), 'MES': om.mins('mes')}
    keep = {'NQ': om.keep_disc, 'MNQ': om.keep_mnq, 'NT': om.keep_mnq, 'MES': om.keep_mnq}
    ctx = {k: context(m) for k, m in M.items()}
    say(f"[{time.time() - T0:4.0f} s] data loaded: " + ", ".join(f"{k} {len(m.dates)} sessions" for k, m in M.items()))
    ok = True
    for k in ('NQ', 'MNQ'):
        a = om.to_frame(om.run_F(M[k], keep[k], 30), [d for d in M[k].dates if keep[k](d)])
        b = series(M[k], keep[k], 'N', ctx[k])
        diff = float((a - b).abs().to_numpy().max()); ok &= diff == 0
        say(f"parity {k}: naplus with no switch vs onemin.run_F(30): max |diff| {diff:.1e} -> {'PASS' if diff == 0 else 'FAIL'}")
    if not ok: say("parity FAILED - nothing graded"); return
    F = {c: {k: series(M[k], keep[k], c, ctx[k]) for k in M} for c in ['N'] + list(TRIALS)}
    def joined(c, ticks=1, gross=False):
        return pd.concat([om.net(F[c]['NQ'], ticks, gross), om.net(F[c]['MNQ'], ticks, gross)])
    n = joined('N'); yn = n.groupby(n.index.year).sum() * N_USD
    say(f"[{time.time() - T0:4.0f} s] NoiseArea on this engine: Sharpe {sharpe(n):.2f}, ${om.usd_yr(n):,.0f}/yr, max DD ${om.maxdd(n):,.0f}, "
        f"MAR {mar(n):.2f}, worst year ${yn.min():,.0f}")
    say(); say("=" * 118); say(f"RESULTS (k = {K}; G1 bar {P_BAR:.4f}; $ per micro at today's price)"); say("=" * 118)
    years = {'NoiseArea': yn}; survivors = []
    for c in TRIALS:
        m = joined(c); p = boot_p(m, n)
        eras = {}
        for k in ('NQ', 'MNQ', 'NT'):
            eras[k] = (sharpe(om.net(F[c][k])), sharpe(om.net(F['N'][k])))
        mes = (sharpe(om.net(F[c]['MES'], gross=True)), sharpe(om.net(F['N']['MES'], gross=True)))
        m2, n2 = joined(c, 2), joined('N', 2); p2 = boot_p(m2, n2)
        e2 = all(sharpe(om.net(F[c][k], 2)) > sharpe(om.net(F['N'][k], 2)) for k in ('NQ', 'MNQ', 'NT'))
        dif = (m - n).sort_values(ascending=False); kp = ~m.index.isin(dif.index[:5]); d5 = (sharpe(m[kp]), sharpe(n[kp]))
        g = {'G1': p < P_BAR, 'G2': all(a > b for a, b in eras.values()), 'G3': mes[0] > mes[1], 'G4': p2 < P_BAR and e2,
             'G5': d5[0] > d5[1], 'G6': mar(m) >= mar(n)}
        ym = m.groupby(m.index.year).sum() * N_USD; years[c] = ym
        tr = sum(F[c][k].ntr.sum() for k in ('NQ', 'MNQ')) / (len(m) / 252)
        say(f"{c} {DESC[c]}")
        say(f"   Sharpe {sharpe(m):.2f} vs {sharpe(n):.2f} | ${om.usd_yr(m):,.0f}/yr vs ${om.usd_yr(n):,.0f} | max DD ${om.maxdd(m):,.0f} vs "
            f"${om.maxdd(n):,.0f} | MAR {mar(m):.2f} vs {mar(n):.2f} | worst year ${ym.min():,.0f} vs ${yn.min():,.0f} | {tr:.0f} trades/yr")
        say(f"   G1 P {p:.4f} | G2 Sharpe NQ {eras['NQ'][0]:.2f}/{eras['NQ'][1]:.2f}, MNQ {eras['MNQ'][0]:.2f}/{eras['MNQ'][1]:.2f}, "
            f"NinjaTrader {eras['NT'][0]:.2f}/{eras['NT'][1]:.2f} | G3 MES gross {mes[0]:.2f}/{mes[1]:.2f} | G4 2 ticks P {p2:.4f} | "
            f"G5 drop-5 {d5[0]:.2f}/{d5[1]:.2f}")
        fails = [k for k, v in g.items() if not v]
        say(f"   VERDICT {c}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(c)
    say(); say("PER YEAR, $ per micro"); say(pd.DataFrame(years).round(0).to_string(float_format=lambda v: f"{v:,.0f}"))
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
