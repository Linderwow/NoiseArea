import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import src2_lib as L2
import ml_lab as ML
import ml_run as MR

OUT = os.path.join(HERE, 'GROWTH_RESULTS.txt')
BAR = 2.76
JOIN = pd.Timestamp('2019-05-05')
U = L2.N_USD
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def trade_x(T, pred, thr, c1, allow=None, stop_usd=None):
    out = []
    al = np.ones(len(T), bool) if allow is None else allow
    T = T.assign(pred=pred, allow=al)
    for k, g in T.groupby('k', sort=True):
        pos = 0; pnl = 0.0; sides = 0; o930 = g.open930.iat[0]; last = None; stopped = False
        for r in g.itertuples():
            if pos != 0: pnl += pos * (r.entry - last); last = r.entry
            if stop_usd is not None and not stopped and (pnl / o930 - sides * c1) * U <= -stop_usd: stopped = True
            if not np.isfinite(r.pred) and not stopped: continue
            new = 0 if (stopped or not r.allow) else (1 if r.pred > thr else (-1 if r.pred < -thr else 0))
            if new != pos: sides += abs(new - pos); pos = new; last = r.entry
        if pos != 0: pnl += pos * (g.exitp.iat[-1] - last); sides += 1
        if sides: out.append((g.date.iat[0], pnl / o930 - sides * c1))
    df = pd.DataFrame(out, columns=['date', 'net'])
    return pd.Series(df.net.values, index=pd.DatetimeIndex(df.date))


def vol_allow(T):
    piv = T.pivot_table(index='k', columns='dec', values='vol30')
    med = piv.rolling(60, min_periods=30).median().shift(1)
    m = med.stack().rename('med').reset_index()
    T2 = T[['k', 'dec', 'vol30']].merge(m, on=['k', 'dec'], how='left')
    return ~(T2.vol30.values > 1.5 * T2.med.values)


def gc():
    say(); say("=" * 124); say(f"G-C  growth_cd.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  the 15-min model with a risk layer; G1 bar t >= {BAR:.2f}"); say("=" * 124)
    nq, mq, es, ms = (ML.Sessions(n) for n in ('nq', 'mnqx', 'es', 'mes'))
    T, pred = MR.family(nq, mq, es, ms, 15, 15); Te, pe = MR.family(es, ms, nq, mq, 15, 15)
    c1 = MR.SIDE(2.35); base = 4 * c1; surv = []
    for name, desc, kw in (('C1', 'only forecasts beyond 4 x round trip', dict(thr=8 * c1)),
                           ('C2', 'daily stop at -$400 per micro', dict(thr=base, stop_usd=400)),
                           ('C3', 'stand aside when 30-min volatility > 1.5 x its 60-session median', dict(thr=base, allow='vol'))):
        def run(TT, pp):
            a = vol_allow(TT) if kw.get('allow') == 'vol' else None
            s = trade_x(TT, pp, kw['thr'], c1, a, kw.get('stop_usd')); return s[s.index.year >= 2013]
        s = run(T, pred); se = run(Te, pe)
        disc, hold = s[s.index <= JOIN], s[s.index > JOIN]; yrs = s.groupby(s.index.year).sum(); d5 = s.drop(s.sort_values(ascending=False).index[:5])
        h = hold * U; eq = h.cumsum(); mdd = float((eq.cummax() - eq).max()); worst = float(h.min())
        g = {'G1': MR.tt(disc) >= BAR, 'G2': hold.mean() > 0 and MR.tt(hold) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2,
             'G4': se[se.index <= JOIN].mean() > 0 and se[se.index > JOIN].mean() > 0, 'G5': MR.tt(d5) >= 2.0, 'G6': mdd < 2500 and worst > -1100}
        say(f"{name} {desc}: NQ 2013-19 ${disc.mean() * U * 252:7,.0f}/yr t {MR.tt(disc):+5.2f} | MNQ 2019-26 ${hold.mean() * U * 252:7,.0f}/yr t {MR.tt(hold):+5.2f} | "
            f"losing yrs {int((yrs < 0).sum())}/{len(yrs)} | drop5 t {MR.tt(d5):+.2f} | 2019-26 max DD ${mdd:,.0f}, worst day ${worst:,.0f} | {time.time() - T0:.0f} s")
        fails = [k for k, v in g.items() if not v]
        say(f"     VERDICT {name}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: surv.append(name)
    say("G-C SURVIVORS: " + (", ".join(surv) if surv else "none"))


def pair_days(A, B):
    ia = {d: k for k, d in enumerate(A.D.date)}; out = []
    for kb, d in enumerate(B.D.date):
        ka = ia.get(d)
        if ka is not None: out.append((d, ka, kb))
    return out


def gd():
    say(); say("=" * 124); say(f"G-D  Nasdaq vs S&P relative value (dollar-neutral, both legs costed; research only - Bulenox hedging rule not on file)"); say("=" * 124)
    sets = {'disc': (ML.Sessions('nq'), ML.Sessions('es'), lambda d: d <= JOIN), 'hold': (ML.Sessions('mnqx'), ML.Sessions('mes'), lambda d: d > JOIN)}
    es_cost = lambda t: U / (7590.5 * 5) * (t * 0.25 * 5 + 0.61) / U
    surv = []
    for name, mode in (('D1', 'rev'), ('D2', 'mom')):
        res = {}
        for t_ in (2.35, 'x2'):
            c_nq = MR.SIDE(2.35 if t_ == 2.35 else 4.7); c_es = es_cost(1.0 if t_ == 2.35 else 2.0); rows = []
            for key, (A, B, keep) in sets.items():
                for d, ka, kb in pair_days(A, B):
                    if not keep(d): continue
                    lr = np.log(A.C[ka]) - np.log(B.C[kb]); s_ = pd.Series(lr)
                    z = ((s_ - s_.rolling(60, min_periods=30).mean()) / s_.rolling(60, min_periods=30).std()).values
                    pos = 0; pnl = 0.0; sides = 0; ea = eb = 0.0
                    for p_ in range(30, 361, 5):
                        zz = z[p_ - 1]
                        if not np.isfinite(zz): continue
                        if pos == 0:
                            if abs(zz) > 2: pos = (-1 if zz > 0 else 1) if mode == 'rev' else (1 if zz > 0 else -1); ea, eb = A.O[ka, p_], B.O[kb, p_]; sides += 1
                        else:
                            ex = (np.sign(zz) != np.sign(z[p_ - 6]) and np.sign(zz) != 0) if mode == 'rev' else (abs(zz) < 0.5)
                            if ex:
                                pnl += pos * (np.log(A.O[ka, p_] / ea) - np.log(B.O[kb, p_] / eb)); pos = 0; sides += 1
                    if pos != 0: pnl += pos * (np.log(A.C[ka, -1] / ea) - np.log(B.C[kb, -1] / eb)); sides += 1
                    if sides: rows.append((d, pnl - sides * (c_nq + c_es)))
            res[t_] = pd.Series([r[1] for r in rows], index=pd.DatetimeIndex([r[0] for r in rows])).sort_index()
        s = res[2.35]; s2 = res['x2']; disc, hold = s[s.index <= JOIN], s[s.index > JOIN]
        yrs = s.groupby(s.index.year).sum(); d5 = s.drop(s.sort_values(ascending=False).index[:5])
        g = {'G1': MR.tt(disc) >= BAR, 'G2': hold.mean() > 0 and MR.tt(hold) >= 2.0, 'G3': int((yrs < 0).sum()) <= 2, 'G4': MR.tt(s2) >= 1.5, 'G5': MR.tt(d5) >= 2.0}
        say(f"{name} {'mean reversion' if mode == 'rev' else 'momentum'} |z| > 2: NQ/ES 2010-19 ${disc.sum() * U / 8.85:7,.0f}/yr t {MR.tt(disc):+5.2f} | "
            f"MNQ/MES 2019-26 ${hold.sum() * U / 7.05:7,.0f}/yr t {MR.tt(hold):+5.2f} | traded days {len(s)} | losing yrs {int((yrs < 0).sum())}/{len(yrs)} | "
            f"2 ticks t {MR.tt(s2):+.2f} | drop5 t {MR.tt(d5):+.2f} | {time.time() - T0:.0f} s")
        fails = [k for k, v in g.items() if not v]
        say(f"     VERDICT {name}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: surv.append(name)
    say("G-D SURVIVORS: " + (", ".join(surv) if surv else "none"))


if __name__ == '__main__':
    gc(); gd()
    with open(OUT, 'a', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"appended to {OUT}")
