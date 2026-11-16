import os, sys, math, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import weekend as WK
from research_engine import WARMUP
from merge_lab import boot_p

OUT = os.path.join(HERE, 'NAPLUS2_RESULTS.txt')
USD = 55751.0
P_BAR = 0.05 / 3
LOOK = 14
CHECKS = set(range(600, 931, 30))
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def run2(mk, ch=None, filt=None, xexit=False, asym=None, worst_out=None):
    sig = mk.sigma(LOOK); pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n)
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, H, L, C, M, vw, nb, A = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb, x.A
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        st = dict(d=0, u=0, ref=0.0, p=0.0, vol=0, worst=0.0)
        ext = 0.0
        fk = filt[k] if filt is not None else None

        def mark(i):
            pos = st['d'] * st['u']
            if pos: st['worst'] = min(st['worst'], st['p'] + pos * ((L[i] if pos > 0 else H[i]) - st['ref']))

        def change(newpos, px):
            pos = st['d'] * st['u']; st['p'] += pos * (px - st['ref']); st['vol'] += abs(newpos - pos); st['ref'] = px
            st['d'] = int(np.sign(newpos)); st['u'] = abs(int(newpos))

        for i in range(flat_i):
            c = C[i]; d = st['d']; mark(i)
            if d != 0:
                ext = max(ext, c) if d > 0 else min(ext, c)
                if ch is not None and ((d > 0 and c < ext - ch * A[i]) or (d < 0 and c > ext + ch * A[i])): change(0, O[i + 1]); continue
                if xexit and fk is not None and fk[i] == -d: change(0, O[i + 1]); continue
            if M[i] not in CHECKS or np.isnan(sg[i]): continue
            if asym is not None and fk is not None:
                t = fk[i]; vu = asym[0] if t > 0 else (asym[1] if t < 0 else 1.0); vd = asym[0] if t < 0 else (asym[1] if t > 0 else 1.0)
            else:
                vu = vd = 1.0
            ub = hi * (1 + vu * sg[i]); lb = lo * (1 - vd * sg[i])
            if d == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif d == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if asym is None and new != 0 and new != d and fk is not None and fk[i] != new: new = 0
            if new != d:
                change(new, O[i + 1])
                if new != 0: ext = O[i + 1]
        if st['d'] != 0: mark(flat_i); change(0, C[flat_i])
        pnl[k] = st['p']; nt[k] = st['vol'] / 2
        if worst_out is not None: worst_out[k] = min(st['worst'], st['p'])
    return pnl, nt


def joined(fn, ticks=1):
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * ticks + 0.25) / p_ref; parts = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQ', lambda d: d > N.JOIN)):
        mk = N.market(nm); w = np.full(mk.n, np.nan); p, n = fn(mk, nm, w); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c,
                                   'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c, 'nt': n[sel]}, index=d[sel]))
    return pd.concat(parts)


def stats(x):
    eq = np.cumsum(x.values) * USD; dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()); yr = x.mean() * 252 * USD
    yrs = x.groupby(x.index.year).sum() * USD
    return dict(sh=LS.sharpe(x), yr=yr, dd=dd, mar=yr / dd if dd > 0 else float('nan'), worst=float(yrs.min()))


def main():
    say(f"naplus2.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (NAPLUS2_PLAN.txt)")
    Q = {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = LS.full_bars(mk); Q[nm] = LS.per_day(mk, df, LS.ema_state(df.close.values))
    conf = lambda nm: (lambda k, i, new: bool(Q[nm][k][i] == new))
    F = {
        'N': lambda mk, nm, w: run2(mk, worst_out=w),
        'CH': lambda mk, nm, w: run2(mk, ch=3.0, worst_out=w),
        'Q8CH': lambda mk, nm, w: run2(mk, ch=3.0, filt=Q[nm], worst_out=w),
        'X1': lambda mk, nm, w: run2(mk, ch=3.0, filt=Q[nm], xexit=True, worst_out=w),
        'X2': lambda mk, nm, w: run2(mk, ch=3.0, filt=Q[nm], asym=(0.75, 1.5), worst_out=w),
        'X3': lambda mk, nm, w: WK.engine(mk, gate=4, lunch=True, ema=True, ch=3.0, conf=conf(nm), conf_mode='skip', worst_out=w)[:2],
    }
    S = {k: joined(f) for k, f in F.items()}
    ok = True
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm)
        a = run2(mk, ch=3.0, filt=Q[nm]); b = LS.run(mk, ch=3.0, filt=Q[nm])
        d1 = max(np.nanmax(np.abs(a[0] - b[0])), np.nanmax(np.abs(a[1] - b[1])))
        c_ = WK.engine(mk, ch=3.0, conf=conf(nm), conf_mode='skip')
        d2 = max(np.nanmax(np.abs(a[0] - c_[0])), np.nanmax(np.abs(a[1] - c_[1])))
        good = d1 == 0 and d2 < 1e-9; ok &= good
        say(f"parity {nm}: run2(Q8CH) vs livestack.run {d1:.1e}; vs weekend.engine(ch=3, conf=Q8) {d2:.1e} -> {'PASS' if good else 'FAIL'}")
    s = stats(S['Q8CH'].r_close)
    good = abs(s['sh'] - 1.31) < 0.006 and abs(s['dd'] - 6099) < 1; ok &= good
    say(f"parity: Q8CH series Sharpe {s['sh']:.3f}, max DD ${s['dd']:,.0f} (LIVESTACK 1.31 / $6,099) -> {'PASS' if good else 'FAIL'}")
    if not ok: say("parity FAILED - nothing graded"); return
    say(); say("=" * 118); say("TRIALS vs Q8CH (joined NQ 2010-07..2019-05 + MNQ NinjaTrader to 2026-10-14; $ per micro, today's price and cost)")
    say("=" * 118)
    for c in ('N', 'CH', 'Q8CH'):
        t = stats(S[c].r_close)
        say(f"{c:5s} Sharpe {t['sh']:.2f} | ${t['yr']:,.0f}/yr | max DD ${t['dd']:,.0f} | MAR {t['mar']:.2f} | worst year ${t['worst']:,.0f}")
    base = S['Q8CH'].r_close; base2 = joined(F['Q8CH'], 2).r_close; survivors = []
    for c, desc in (('X1', 'crossover exit'), ('X2', 'trend-weighted bands 0.75 / 1.5'), ('X3', 'gate 4 + lunch + EMA20 (B2) + Q8')):
        x = S[c].r_close; x2 = joined(F[c], 2).r_close; t = stats(x); b = stats(base)
        p = boot_p(x, base); p2 = boot_p(x2, base2)
        era = [(LS.sharpe(x[x.index <= N.JOIN]), LS.sharpe(base[base.index <= N.JOIN])), (LS.sharpe(x[x.index > N.JOIN]), LS.sharpe(base[base.index > N.JOIN]))]
        era2 = all(LS.sharpe(x2[m]) > LS.sharpe(base2[m]) for m in (x2.index <= N.JOIN, x2.index > N.JOIN))
        dif = (x - base).sort_values(ascending=False); kp = ~x.index.isin(dif.index[:5])
        g = {'G1': p < P_BAR, 'G2': all(a > bb for a, bb in era), 'G3': p2 < P_BAR and era2, 'G4': LS.sharpe(x[kp]) > LS.sharpe(base[kp]),
             'G5': t['dd'] < b['dd'] and t['mar'] > b['mar']}
        say(f"{c} {desc}: Sharpe {t['sh']:.2f} | ${t['yr']:,.0f}/yr | max DD ${t['dd']:,.0f} | MAR {t['mar']:.2f} | worst year ${t['worst']:,.0f} | "
            f"P vs Q8CH {p:.4f} | NQ {era[0][0]:.2f}/{era[0][1]:.2f} MNQ {era[1][0]:.2f}/{era[1][1]:.2f} | 2 ticks P {p2:.4f} | "
            f"P vs CH {boot_p(x, S['CH'].r_close):.4f}")
        fails = [k for k, v in g.items() if not v]
        say(f"   VERDICT {c}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(c)
    say(); say("BULENOX 50K (biglever models as LIVESTACK): eval 6 micros pass/try | Master 2-yr payouts / open after 1 yr at 1 | 2 | 3 | 4 micros")
    for c in ('CH', 'Q8CH', 'X1', 'X2', 'X3'):
        e, f = LS.bulenox(S[c])
        say(f"   {c:5s} {e['p'] * 100:5.1f}% | " + " | ".join(f"${f[m]['pay']:,.0f} / {f[m]['open1'] * 100:.0f}%" for m in (1, 2, 3, 4)))
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none - Q8CH stays the best setup"))
    S['Q8CH'].to_csv(os.path.join(HERE, 'regime_work', 'q8ch_daily.csv'))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
