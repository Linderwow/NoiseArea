import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import weekend as WK
import naplus2 as N2
from merge_lab import boot_p

OUT = os.path.join(HERE, 'MANUAL_RESULTS_C.txt')
P_BAR = 0.005
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def hourly_ctx(df):
    t = pd.DatetimeIndex(df.time); c = df.close.values
    hend = (t - pd.Timedelta(minutes=5)).floor('h') + pd.Timedelta(hours=1)
    g = pd.DataFrame({'c': c, 'h': hend}).groupby('h').c.agg(['last', 'size'])
    g = g[g['size'] >= 6]
    e50 = g['last'].ewm(span=50, adjust=False).mean().values; e200 = g['last'].ewm(span=200, adjust=False).mean().values
    j = np.searchsorted(g.index.values, t.floor('h').values, side='right') - 1
    ok = j >= 0; j = np.clip(j, 0, None)
    top = np.where(ok, np.maximum(e50[j], e200[j]), np.nan); bot = np.where(ok, np.minimum(e50[j], e200[j]), np.nan)
    return np.where(c > top, 1, np.where(c < bot, -1, 0)), g, e50, e200


def audit_ctx():
    t = pd.date_range('2020-01-06 00:05', periods=12 * 300, freq='5min')
    c = 100 + np.arange(len(t)) * 0.01
    ctx, g, e50, e200 = hourly_ctx(pd.DataFrame({'time': t, 'close': c}))
    k = np.where(t == pd.Timestamp('2020-01-10 10:35'))[0][0]
    hk = np.where(g.index == pd.Timestamp('2020-01-10 10:00'))[0][0]
    a1 = g['last'].iloc[hk] == c[np.where(t == pd.Timestamp('2020-01-10 10:00'))[0][0]]
    a2 = ctx[k] == 1 and ctx[np.where(t == pd.Timestamp('2020-01-10 11:00'))[0][0]] == 1
    c2 = c.copy(); c2[k] = 0.0
    a3 = hourly_ctx(pd.DataFrame({'time': t, 'close': c2}))[0][k] == -1
    ok = bool(a1 and a2 and a3)
    say(f"audit hourly context: hour ending 10:00 closes on the 10:00 bar {a1}, uptrend = above both {a2}, a 5-min close below both = -1 {a3} -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    say(f"manual_filter.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (MANUAL_PLAN.txt part C)")
    if not audit_ctx(): say("audit FAILED - nothing graded"); return
    Q, H = {}, {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = LS.full_bars(mk)
        Q[nm] = LS.per_day(mk, df, LS.ema_state(df.close.values))
        H[nm] = LS.per_day(mk, df, hourly_ctx(df)[0])
        allv = np.concatenate([h for h in H[nm]]); say(f"{nm}: decision bars above both {np.mean(allv == 1):.0%}, below both {np.mean(allv == -1):.0%}, between {np.mean(allv == 0):.0%}")
    c_x3 = lambda nm: (lambda k, i, new: bool(Q[nm][k][i] == new))
    c_x3h = lambda nm: (lambda k, i, new: bool(Q[nm][k][i] == new and H[nm][k][i] == new))
    F = {'X3': lambda mk, nm, w: WK.engine(mk, gate=4, lunch=True, ema=True, ch=3.0, conf=c_x3(nm), conf_mode='skip', worst_out=w)[:2],
         'X3H': lambda mk, nm, w: WK.engine(mk, gate=4, lunch=True, ema=True, ch=3.0, conf=c_x3h(nm), conf_mode='skip', worst_out=w)[:2]}
    S = {k: N2.joined(f) for k, f in F.items()}; S2 = {k: N2.joined(f, 2) for k, f in F.items()}
    ref = pd.read_csv(os.path.join(HERE, 'regime_work', 'x3_daily.csv'), index_col=0, parse_dates=True).r_close
    d = float(np.nanmax(np.abs(S['X3'].r_close.reindex(ref.index).values - ref.values)))
    say(f"parity: X3 rebuilt vs regime_work\\x3_daily.csv max |diff| {d:.1e} -> {'PASS' if d < 1e-12 else 'FAIL'}")
    if d >= 1e-12: say("parity FAILED - nothing graded"); return
    x, b = S['X3H'].r_close, S['X3'].r_close; x2, b2 = S2['X3H'].r_close, S2['X3'].r_close
    tx, tb = N2.stats(x), N2.stats(b)
    for lab, t_, s_ in (('X3 (live)', tb, S['X3']), ('X3H (+ his hourly above/below both)', tx, S['X3H'])):
        say(f"{lab:38s} Sharpe {t_['sh']:.2f} | ${t_['yr']:,.0f}/yr | max DD ${t_['dd']:,.0f} | MAR {t_['mar']:.2f} | worst year ${t_['worst']:,.0f} | "
            f"trades/yr {s_.nt.sum() / (len(s_) / 252):.0f}")
    p = boot_p(x, b); p2 = boot_p(x2, b2)
    era = [(LS.sharpe(x[m]), LS.sharpe(b[m])) for m in (x.index <= N.JOIN, x.index > N.JOIN)]
    era2 = all(LS.sharpe(x2[m]) > LS.sharpe(b2[m]) for m in (x2.index <= N.JOIN, x2.index > N.JOIN))
    dif = (x - b).sort_values(ascending=False); kp = ~x.index.isin(dif.index[:5])
    g = {'G1': p < P_BAR, 'G2': all(a > c for a, c in era), 'G3': p2 < P_BAR and era2, 'G4': LS.sharpe(x[kp]) > LS.sharpe(b[kp]),
         'G5': tx['dd'] < tb['dd'] and tx['mar'] > tb['mar']}
    say(f"P(X3H <= X3) {p:.4f} (bar {P_BAR}) | Sharpe NQ 2010-19 {era[0][0]:.2f} vs {era[0][1]:.2f}, MNQ 2019-26 {era[1][0]:.2f} vs {era[1][1]:.2f} | "
        f"2 ticks P {p2:.4f}")
    yx = x.groupby(x.index.year).sum() * N2.USD; yb = b.groupby(b.index.year).sum() * N2.USD
    say("per year X3H - X3: " + "; ".join(f"{y} {yx[y] - yb[y]:+,.0f}" for y in yx.index))
    fails = [k for k, v in g.items() if not v]
    say(f"VERDICT X3H: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
