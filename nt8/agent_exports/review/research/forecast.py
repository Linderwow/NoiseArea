import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import naplus2 as N2
import naplus3 as N3
import biglever as B
import bulenox_routes as BR
import masterscale as MSC
import inactivity as INA

OUT = os.path.join(HERE, 'FORECAST_RESULTS.txt')
USD = 55751.0
LIVE_TICKS = 2.35
SEED = 20261022
DAYS = 504
PUB = pd.Timestamp('2024-06-01')
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def cushion(cu):
    return np.clip(np.floor(cu / 2500.0), 1, 4)


def one_micro(cu):
    return np.ones(cu.shape)


def with_cost(df, ticks, p_ref):
    extra = (ticks - 1) * 0.25 / p_ref * 2 * df.nt
    return pd.DataFrame({'r_close': df.r_close - extra, 'r_min': df.r_min - extra, 'nt': df.nt})


def halved(df):
    m = df.r_close.mean() / 2
    return pd.DataFrame({'r_close': df.r_close - m, 'r_min': df.r_min - m, 'nt': df.nt})


def pct(a, q):
    return float(np.percentile(a, q))


def raw_year(df):
    usd = (df.r_close * USD).values; nt = df.nt.values.astype(float)
    X, _ = BR.boot_paths(252, B.N_PATHS, usd, nt, seed=SEED)
    tot = X.sum(1); eq = np.cumsum(X, 1); dd = (np.maximum.accumulate(np.maximum(eq, 0), 1) - eq).max(1)
    return dict(p10=pct(tot, 10), p25=pct(tot, 25), p50=pct(tot, 50), p75=pct(tot, 75), p90=pct(tot, 90), mean=float(tot.mean()),
                lose=float(np.mean(tot < 0)), dd50=pct(dd, 50), dd90=pct(dd, 90))


def master(df, rule):
    usd, usdm, nt = (df.r_close * USD).values, (df.r_min * USD).values, df.nt.values.astype(float)
    X, W, NT = B.paths(DAYS, usd, usdm, nt, 1, SEED)
    pay, ou, mic = MSC.master_dyn(X, W, NT, BR.SIZES['50K'], rule)
    y1, y2 = pay[:, :252].sum(1), pay[:, 252:].sum(1); n1 = (pay[:, :252] > 0).sum(1)
    alive1 = ou > 251
    return dict(y1=float(y1.mean()), y2=float(y2.mean()), y2_open=float(y2[alive1].mean()) if alive1.any() else 0.0,
                any1=float(np.mean(y1 > 0)), n1=float(n1.mean()), p10=pct(y1, 10), p50=pct(y1, 50), p90=pct(y1, 90),
                open1=float(np.mean(alive1)), open2=float(np.mean(ou > 503)), lost0=float(np.mean((ou < DAYS) & (pay.sum(1) == 0))),
                tot=float(pay.sum(1).mean()))


def evaluation(df):
    usd, usdm, nt = (df.r_close * USD).values, (df.r_min * USD).values, df.nt.values.astype(float)
    a = BR.SIZES['50K']
    e = B.eval_stats(usd, usdm, nt, 6, SEED, a['dd'], a['dll'], a['target'], a['qual'], resets=True)
    return e['p'], e['fees'] + a['act']


def dry(df):
    z = (df.nt.values == 0).astype(int); runs = []; r = 0
    for v in z:
        if v: r += 1
        else:
            if r: runs.append(r)
            r = 0
    if r: runs.append(r)
    runs = np.array(runs) if runs else np.array([0]); yrs = len(df) / 252.0
    return dict(max=int(runs.max()), r4=float((runs >= 4).sum() / yrs), r5=float((runs >= 5).sum() / yrs), zero=float(z.mean()))


def main():
    say(f"forecast.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  per-account yearly forecast, live setup (CH) vs staged stack (X3)")
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    Q = {nm: (lambda mk: LS.per_day(mk, LS.full_bars(mk), LS.ema_state(LS.full_bars(mk).close.values)))(N.market(nm)) for nm in ('NQ', 'MNQ')}
    base = {'CH': N2.joined(lambda mk, nm, w: N2.run2(mk, ch=3.0, worst_out=w)),
            'X3': N2.joined(lambda mk, nm, w: N3.engine3(mk, Q[nm], worst_out=w))}
    for c, ref in (('CH', (1.13, 4865)), ('X3', (1.49, 5618))):
        t = N2.stats(base[c].r_close)
        ok = abs(t['sh'] - ref[0]) < 0.01 and abs(t['yr'] - ref[1]) < 5
        say(f"check {c} at 1 tick: Sharpe {t['sh']:.2f}, ${t['yr']:,.0f}/yr (recorded {ref[0]:.2f} / ${ref[1]:,}) -> {'PASS' if ok else 'FAIL'}")
        if not ok: raise SystemExit("series do not match the recorded results")
    S = {}
    for c in ('CH', 'X3'):
        S[(c, 'model')] = base[c]; S[(c, 'live')] = with_cost(base[c], LIVE_TICKS, p_ref); S[(c, 'half')] = halved(S[(c, 'live')])
    say(f"price reference MNQ {p_ref:,.0f}; $ per micro; costs: model 1 tick + $0.50 a side, live {LIVE_TICKS} ticks + $0.50 a side "
        f"(Sim101 measured, fill-cost review 2026-11-02)")
    say("scenarios: model = backtest as recorded; live = backtest at live fill cost; half = live with the average day's profit halved "
        "(stress case: live edge half the backtest's)")
    say(); say("=" * 118); say("1. ONE MICRO, NO PROP RULES: CALENDAR YEARS AT LIVE COST ($)"); say("=" * 118)
    yt = {c: S[(c, 'live')].r_close.groupby(S[(c, 'live')].index.year).sum() * USD for c in ('CH', 'X3')}
    nd = S[('CH', 'live')].r_close.groupby(S[('CH', 'live')].index.year).size()
    say("year   sessions      CH       X3")
    for y in yt['CH'].index:
        say(f"{y}   {nd[y]:5d}   {yt['CH'][y]:7,.0f}  {yt['X3'][y]:7,.0f}")
    for c in ('CH', 'X3'):
        x = S[(c, 'live')].r_close; full = x[x.index.year < x.index[-1].year]; rec = x[x.index >= PUB]
        say(f"{c}: full years {int((full.groupby(full.index.year).sum() < 0).sum())} losing of {full.index.year.nunique()}; since "
            f"{PUB:%Y-%m} (after the method was published) ${rec.mean() * 252 * USD:,.0f}/yr, Sharpe {LS.sharpe(rec):.2f} vs whole history "
            f"${x.mean() * 252 * USD:,.0f}/yr, Sharpe {LS.sharpe(x):.2f}")
    say(); say("=" * 118); say("2. ONE MICRO, NO PROP RULES: NEXT 12 MONTHS (20,000 resampled years, 21-session blocks)"); say("=" * 118)
    say("setup scenario   P10      P25      median   P75      P90      mean   P(losing year)  max DD in the year: median / P90")
    for c in ('CH', 'X3'):
        for sc in ('model', 'live', 'half'):
            r = raw_year(S[(c, sc)])
            say(f"{c:5s} {sc:6s} {r['p10']:8,.0f} {r['p25']:8,.0f} {r['p50']:8,.0f} {r['p75']:8,.0f} {r['p90']:8,.0f} {r['mean']:8,.0f}"
                f"   {r['lose'] * 100:5.1f}%        ${r['dd50']:,.0f} / ${r['dd90']:,.0f}")
    say(); say("=" * 118)
    say("3. BULENOX 50K MASTER, PER ACCOUNT (Option 2 rules, intraday breaches, 90% split, first 3 payouts capped $1,500)")
    say("=" * 118)
    say("sizing: cushion = 1 micro per $2,500 of balance above the threshold, max 4 (MASTERSCALE); fixed = 1 micro")
    say("setup scenario sizing  | year 1: expected  P10 / median / P90  payouts  P(any) | open after 1 yr | year 2 expected (if open) | open 2 yr | lost before any payout")
    M = {}
    for c in ('CH', 'X3'):
        for sc in ('model', 'live', 'half'):
            for nm, rule in (('cushion', cushion), ('fixed1', one_micro)):
                r = master(S[(c, sc)], rule); M[(c, sc, nm)] = r
                say(f"{c:5s} {sc:6s} {nm:8s}| ${r['y1']:6,.0f}  ${r['p10']:,.0f} / ${r['p50']:,.0f} / ${r['p90']:,.0f}   {r['n1']:.1f}  "
                    f"{r['any1'] * 100:4.0f}% | {r['open1'] * 100:4.0f}%  | ${r['y2']:6,.0f} (${r['y2_open']:,.0f}) | {r['open2'] * 100:4.0f}% | {r['lost0'] * 100:4.0f}%")
    say(); say("=" * 118); say("4. GETTING A NEW 50K MASTER (evaluation at 6 micros with $78 resets, plus the $148 activation)"); say("=" * 118)
    for c in ('CH', 'X3'):
        for sc in ('live', 'half'):
            p, fees = evaluation(S[(c, sc)])
            m = M[(c, sc, 'cushion')]
            say(f"{c:5s} {sc:6s}: pass per try {p * 100:4.1f}% | expected fees per funded Master ${fees:,.0f} | 2-yr payouts ${m['tot']:,.0f} "
                f"-> net per new account ${m['tot'] - fees:,.0f} over 2 years")
    say(); say("=" * 118); say("5. BULENOX ACTIVITY RULE (a trade at least every 5 trading days): dry spells in the backtest"); say("=" * 118)
    for c in ('CH', 'X3'):
        d = dry(S[(c, 'model')])
        say(f"{c}: sessions with no trade {d['zero'] * 100:.0f}% | longest dry run {d['max']} sessions | runs of >= 4 dry sessions "
            f"{d['r4']:.1f}/yr | runs of >= 5 {d['r5']:.1f}/yr (each one would breach without the activity fallback)")
    say(); say("=" * 118)
    say("6. X3 WITH THE ACTIVITY FALLBACK (after 3 dry sessions the band multiplier is 0 until a trade; a Master closes at 5 dry sessions)")
    say("=" * 118)
    say("staged = the 20261109_k1_switches build: on fallback sessions the crossover, EMA(20) and lunch filters still apply; "
        "exempt = fallback sessions skip those three entry filters (exits unchanged)")
    fbs = {'staged': N2.joined(lambda mk, nm, w: N3.engine3(mk, Q[nm], worst_out=w, vm=0.0)),
           'exempt': N2.joined(lambda mk, nm, w: N3.engine3(mk, None, worst_out=w, vm=0.0, lunch=False, ema=False))}
    b0 = base['X3']; yrs = len(b0) / 252.0
    for f in fbs.values(): assert f.index.equals(b0.index)
    for nm_, f in fbs.items():
        fb, took = INA.walk(b0.nt.values, f.nt.values, 3); rr = INA.runs(took)
        fl = with_cost(f, LIVE_TICKS, p_ref).r_close.values * USD; bl = S[('X3', 'live')].r_close.values * USD
        dif = fl[fb] - bl[fb]; t = dif.mean() / (dif.std(ddof=1) / np.sqrt(len(dif))) if len(dif) > 1 else float('nan')
        say(f"{nm_}: fallback sessions {fb.sum() / yrs:.1f}/yr, of which traded {np.mean(f.nt.values[fb] > 0) * 100:.0f}% | dry runs >= 5 left "
            f"{(rr >= 5).sum() / yrs:.2f}/yr, longest {rr.max()} | fallback session vs X3 same day at live cost ${dif.mean():+,.1f} (t {t:+.2f})")
    a = BR.SIZES['50K']
    say("Master 50K, cushion sizing        | year 1 expected  P(any)  | year 2 expected | open 1 yr | open 2 yr | closed by inactivity 1 yr / 2 yr")
    for sc in ('live', 'half'):
        bf = S[('X3', sc)]; cut = 0.0 if sc == 'live' else S[('X3', 'live')].r_close.mean() / 2
        X, W, NT = B.paths(DAYS, (bf.r_close * USD).values, (bf.r_min * USD).values, bf.nt.values.astype(float), 1, SEED)
        rows = [('no fallback', None, None)] + [(k, 3, fbs[k]) for k in fbs]
        for nm_, K, f in rows:
            if f is None: XF, WF, NTF = X, W, NT
            else:
                fc = with_cost(f, LIVE_TICKS, p_ref)
                XF, WF, NTF = B.paths(DAYS, ((fc.r_close - cut) * USD).values, ((fc.r_min - cut) * USD).values, fc.nt.values.astype(float), 1, SEED)
            pay, ou, mic, inact, fbd, traded, which = INA.master_inact(X, W, NT, XF, WF, NTF, a, cushion, K, 5)
            y1, y2 = pay[:, :252].sum(1), pay[:, 252:].sum(1)
            say(f"X3 {sc:4s} {nm_:12s}              | ${y1.mean():6,.0f}   {np.mean(y1 > 0) * 100:4.0f}%   | ${y2.mean():6,.0f}        | {np.mean(ou > 251) * 100:4.0f}%     | "
                f"{np.mean(ou > 503) * 100:4.0f}%     | {np.mean(inact & (ou <= 251)) * 100:5.1f}% / {np.mean(inact) * 100:5.1f}%")
    say(); say("=" * 118)
    say("7. X3 PER MICRO OVER THE SIM101 FORWARD TEST: what 1 / 3 / 6 / 12 months should look like (20,000 resampled paths, live cost)")
    say("=" * 118)
    xl = S[('X3', 'live')]
    zero = pd.DataFrame({'r_close': xl.r_close - xl.r_close.mean(), 'r_min': xl.r_min, 'nt': xl.nt})
    say("scenario     months  P10       median    P90       P(below zero)  worst dip median / P90")
    for nm_, df_ in (('edge real', xl), ('half edge', S[('X3', 'half')]), ('no edge', zero)):
        usd = (df_.r_close * USD).values; ntv = df_.nt.values.astype(float)
        for mo, days in ((1, 21), (3, 63), (6, 126), (12, 252)):
            X, _ = BR.boot_paths(days, B.N_PATHS, usd, ntv, seed=SEED); tot = X.sum(1); eq = np.cumsum(X, 1)
            dd = (np.maximum.accumulate(np.maximum(eq, 0), 1) - eq).max(1)
            say(f"{nm_:11s}  {mo:4d}   {pct(tot, 10):+8,.0f}  {pct(tot, 50):+8,.0f}  {pct(tot, 90):+8,.0f}   {np.mean(tot < 0) * 100:5.1f}%        "
                f"${pct(dd, 50):,.0f} / ${pct(dd, 90):,.0f}")
    say(); say("=" * 118)
    say("8. HOW MANY MICROS ON A 50K MASTER: X3 + staged activity fallback, live cost, fixed size vs the cushion rule (2 years)")
    say("=" * 118)
    say("scenario   sizing                         | year 1   year 2   | any payout yr 1 | open 1 yr / 2 yr | lost before any payout")
    a = BR.SIZES['50K']; fst = fbs['staged']
    rules = [(f'fixed {m}', (lambda cu, m=m: np.full(cu.shape, float(m)))) for m in (1, 2, 3, 4)] + \
            [('cushion: +1 per $2,500, max 4', cushion), ('cushion: +1 per $2,500, max 2', lambda cu: np.clip(np.floor(cu / 2500.0), 1, 2))]
    for sc in ('live', 'half'):
        bf = S[('X3', sc)]; cut = 0.0 if sc == 'live' else S[('X3', 'live')].r_close.mean() / 2; fc = with_cost(fst, LIVE_TICKS, p_ref)
        X, W, NT = B.paths(DAYS, (bf.r_close * USD).values, (bf.r_min * USD).values, bf.nt.values.astype(float), 1, SEED)
        XF, WF, NTF = B.paths(DAYS, ((fc.r_close - cut) * USD).values, ((fc.r_min - cut) * USD).values, fc.nt.values.astype(float), 1, SEED)
        for nm_, rule in rules:
            pay, ou, mic, inact, fbd, traded, which = INA.master_inact(X, W, NT, XF, WF, NTF, a, rule, 3, 5)
            y1, y2 = pay[:, :252].sum(1), pay[:, 252:].sum(1)
            say(f"{sc:9s}  {nm_:30s} | ${y1.mean():6,.0f}  ${y2.mean():6,.0f}  | {np.mean(y1 > 0) * 100:5.0f}%          | "
                f"{np.mean(ou > 251) * 100:4.0f}% / {np.mean(ou > 503) * 100:3.0f}%       | {np.mean((ou < DAYS) & (pay.sum(1) == 0)) * 100:4.0f}%")
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
