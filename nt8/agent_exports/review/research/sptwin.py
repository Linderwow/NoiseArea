import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import weekend as WK
import naplus2 as N2
import src2_lib as L2
from research_engine import WARMUP
from merge_lab import boot_p

OUT = os.path.join(HERE, 'SPTWIN_RESULTS.txt')
P_BAR = 0.005
USD_MNQ = 55751.0
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def raw_open(name):
    m = L2.Mins(name); k = m.m == 570
    return pd.Series(m.o[k], index=pd.DatetimeIndex(m.date[k])).groupby(level=0).first(), float(m.c[-1]), pd.Timestamp(m.t[-1])


def sp_series(ticks, mes_usd):
    parts = []
    for nm, src, keep in (('ES', 'es', lambda d: d <= N.JOIN), ('MES', 'mes', lambda d: d > N.JOIN)):
        mk = N.market(nm); df = LS.full_bars(mk); Q = LS.per_day(mk, df, LS.ema_state(df.close.values))
        conf = lambda k, i, new, Q=Q: bool(Q[k][i] == new)
        p, n = WK.engine(mk, gate=4, lunch=True, ema=True, ch=3.0, conf=conf, conf_mode='skip')[:2]
        d = mk.dates; sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        ro, _, _ = raw_open(src); px = ro.reindex(d[sel]).values
        o = np.array([x.O[0] for x in mk.days])[sel]; ok = ~np.isnan(px)
        say(f"{nm}: {int(sel.sum())} sessions, raw 09:30 open found for {ok.mean():.1%} (else the bar open)")
        px = np.where(ok, px, o); c = (0.25 * ticks + 0.1) / px
        parts.append(pd.Series((np.nan_to_num(p[sel]) / px - n[sel] * 2 * c) * mes_usd, index=d[sel]))
    return pd.concat(parts)


def mar(x):
    eq = x.cumsum().values; dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max())
    return (x.mean() * 252) / dd if dd > 0 else float('nan'), dd


def main():
    say(f"sptwin.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (SPTWIN_PLAN.txt)")
    _, last, when = raw_open('mes'); mes_usd = 5 * last
    say(f"MES $ per micro = return x 5 x {last:,.2f} (last MES close {when:%Y-%m-%d}) = {mes_usd:,.0f}; MNQ = return x {USD_MNQ:,.0f}")
    ref = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close
    Q = {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = LS.full_bars(mk); Q[nm] = LS.per_day(mk, df, LS.ema_state(df.close.values))
    fx3 = lambda mk, nm, w: WK.engine(mk, gate=4, lunch=True, ema=True, ch=3.0, conf=lambda k, i, new: bool(Q[nm][k][i] == new),
                                      conf_mode='skip', worst_out=w)[:2]
    mq = {1: N2.joined(fx3).r_close * USD_MNQ, 2: N2.joined(fx3, 2).r_close * USD_MNQ}
    d1 = float(np.nanmax(np.abs(mq[1].reindex(ref.index).values / USD_MNQ - ref.values)))
    say(f"parity: MNQ X3 rebuilt vs regime_work\\x3_daily.csv max |diff| {d1:.1e} -> {'PASS' if d1 < 1e-12 else 'FAIL'}")
    if d1 >= 1e-12: say("parity FAILED - nothing graded"); return
    sp = {t: sp_series(t, mes_usd) for t in (1, 2)}
    idx = mq[1].index
    S = {t: sp[t].reindex(idx).fillna(0.0) for t in (1, 2)}
    era = (idx <= N.JOIN, idx > N.JOIN)
    s1 = S[1]
    say(); say("MES / ES X3 alone ($ per micro, 1 tick):")
    for lab, m in (('ES 2010-19', era[0]), ('MES 2019-26', era[1] & (idx <= pd.Timestamp(when.date())))):
        x = s1[m]; yrs = x.groupby(x.index.year).sum()
        say(f"   {lab}: ${x.mean() * 252:,.0f}/yr | Sharpe {LS.sharpe(x):.2f} | losing years {int((yrs < 0).sum())}/{len(yrs)}")
    say(f"   MNQ X3: ${mq[1].mean() * 252:,.0f}/yr | Sharpe {LS.sharpe(mq[1]):.2f} (2010-19 {LS.sharpe(mq[1][era[0]]):.2f}, 2019-26 {LS.sharpe(mq[1][era[1]]):.2f})")
    both = s1 != 0
    say(f"   daily correlation MNQ X3 vs S&P X3: all days {mq[1].corr(s1):+.2f}, days the S&P stream trades {mq[1][both].corr(s1[both]):+.2f}")
    w2 = float(mq[1][era[0]].std() / s1[era[0]].std())
    say(f"   S2 weight from 2010-19 only: w = {w2:.2f} MES micros per MNQ micro")
    say(); say("=" * 118); say("BOOK vs MNQ X3 alone (same days)"); say("=" * 118)
    survivors = []
    for lab, w in (('S1 1 MNQ + 1 MES', 1.0), (f'S2 1 MNQ + {w2:.2f} MES', w2)):
        bk = {t: mq[t] + w * S[t] for t in (1, 2)}
        x, b = bk[1], mq[1]; x2, b2 = bk[2], mq[2]
        p = boot_p(x, b); p2 = boot_p(x2, b2)
        e1 = [(LS.sharpe(x[m]), LS.sharpe(b[m])) for m in era]; e2 = all(LS.sharpe(x2[m]) > LS.sharpe(b2[m]) for m in era)
        dif = (x / x.std() - b / b.std()).sort_values(ascending=False); kp = ~x.index.isin(dif.index[:5])
        (mx, ddx), (mb, ddb) = mar(x), mar(b)
        g = {'G1': p < P_BAR, 'G2': all(a > c for a, c in e1), 'G3': p2 < P_BAR and e2, 'G4': LS.sharpe(x[kp]) > LS.sharpe(b[kp]), 'G5': mx > mb}
        say(f"{lab}: ${x.mean() * 252:,.0f}/yr | Sharpe {LS.sharpe(x):.2f} vs {LS.sharpe(b):.2f} | max DD ${ddx:,.0f} vs ${ddb:,.0f} | MAR {mx:.2f} vs {mb:.2f} | "
            f"P {p:.4f} | 2010-19 {e1[0][0]:.2f} vs {e1[0][1]:.2f}, 2019-26 {e1[1][0]:.2f} vs {e1[1][1]:.2f} | 2 ticks P {p2:.4f}")
        fails = [k for k, v in g.items() if not v]
        say(f"   VERDICT: {'PASS - needs S3 (no opposite positions) before any build' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(lab)
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none - MNQ X3 alone stays"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
