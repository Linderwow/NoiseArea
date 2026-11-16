import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import onemin as om

OUT = os.path.join(HERE, 'EUROPEN_RESULTS.txt')
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def at(mk, day, hhmm):
    ts = np.datetime64(pd.Timestamp(day).strftime('%Y-%m-%d') + 'T' + hhmm, 'm')
    j = np.searchsorted(mk.t, ts)
    return j if j < len(mk.t) and mk.t[j] == ts else None


def run(mk, keep, conditional):
    res = {}; dates = mk.dates
    for k in range(1, len(dates)):
        d = dates[k]
        if not keep(d): continue
        a, b = at(mk, d, '01:30'), at(mk, d, '03:29')
        if a is None or b is None or mk.iid[a] != mk.iid[b]: res[d] = (0.0, 0, 0, 0, 0); continue
        if conditional:
            p = dates[k - 1]; s, e = at(mk, p, '15:14'), at(mk, p, '16:14')
            if s is None or e is None or mk.iid[s] != mk.iid[e] or not mk.c[e] < mk.c[s]: res[d] = (0.0, 0, 0, 0, 0); continue
        pts = mk.c[b] - mk.o[a]
        res[d] = (pts / mk.o[mk.days[d][0]], 0, 2, 1, int(pts > 0))
    return om.to_frame(res, [d for d in dates if keep(d)])


def main():
    say(f"europen.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (EUROPEN_PLAN.txt)")
    M = {'NQ': om.mins('nq'), 'MNQ': om.mins('mnqx')}
    say(f"{'trial':<26}{'trades/yr':>10}{'win':>6}{'net $/yr':>10}{'t net':>7}{'t 0-cost':>9}  K1")
    frames = {}; alive = []
    for code, cond in (('E1 conditional', True), ('E2 unconditional', False)):
        df = pd.concat([run(M['NQ'], om.keep_disc, cond), run(M['MNQ'], om.keep_mnq, cond)]); frames[code] = (df, cond)
        dd = om.between(df, om.DISC); x = om.net(dd); xz = om.net(dd, gross=True)
        k1 = om.tstat(x) >= 2.0
        if k1: alive.append(code)
        say(f"{code:<26}{dd.ntr.sum() / (len(dd) / 252):>10.0f}{dd.wins.sum() / max(dd.ntr.sum(), 1):>6.0%}{om.usd_yr(x):>10,.0f}{om.tstat(x):>7.2f}"
            f"{om.tstat(xz):>9.2f}  {'pass' if k1 else 'DEAD'}")
    say("\nREPORTED FOR EVERY TRIAL (the NY Fed decay window and the later eras; net, $ per micro at today's price)")
    for code, (df, cond) in frames.items():
        x = om.net(df); r = {}
        for lab, a, b in (('2010-15', '2010-07-01', '2015-12-31'), ('2016-20', '2016-01-01', '2020-12-31'), ('2021-26', '2021-01-01', '2026-12-31')):
            v = x[(x.index >= a) & (x.index <= b)]; r[lab] = f"${om.usd_yr(v):,.0f}/yr t {om.tstat(v):.2f}"
        say(f"   {code:<24} " + " | ".join(f"{k} {v}" for k, v in r.items()))
    if alive:
        say("\nSTAGE 2 - K1 SURVIVORS")
        es, mes, nt = om.mins('es'), om.mins('mes'), om.mins_nt()
        for code in alive:
            df, cond = frames[code]; x = om.net(df); x2 = om.net(df, 2)
            v, h = om.between(x, om.VAL), om.between(x, om.HOLD)
            yrs = x.groupby(x.index.year).sum(); neg = int((yrs < 0).sum()); d5 = x.sum() - x.nlargest(5).sum()
            ges = om.net(run(es, om.keep_disc, cond), gross=True).mean(); gmes = om.net(run(mes, om.keep_mnq, cond), gross=True).mean()
            xn = om.net(run(nt, om.keep_mnq, cond)); sgn = np.sign(x.mean())
            k = {'K2': v.sum() > 0 and h.sum() > 0, 'K3': om.tstat(x) >= 3.0, 'K4': neg <= 3, 'K5': om.tstat(x2) >= 2.0, 'K6': d5 > 0,
                 'K7': np.sign(ges) == sgn and np.sign(gmes) == sgn, 'K8': np.sign(xn.mean()) == sgn and om.tstat(xn) >= 2.0}
            say(f"{code}: full ${om.usd_yr(x):,.0f}/yr t {om.tstat(x):.2f} | validation ${om.usd_yr(v):,.0f}/yr | holdout ${om.usd_yr(h):,.0f}/yr t {om.tstat(h):.2f} | "
                f"losing years {neg}/{len(yrs)} | 2 ticks t {om.tstat(x2):.2f} | ES {ges * om.N_USD * 252:+,.0f}/yr MES {gmes * om.N_USD * 252:+,.0f}/yr | "
                f"NinjaTrader ${om.usd_yr(xn):,.0f}/yr t {om.tstat(xn):.2f}")
            fails = [kk for kk, ok in k.items() if not ok]
            say(f"   VERDICT: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
