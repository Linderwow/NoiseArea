import os, sys, math, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import strat3 as S3
import biglever as BL
import bulenox_routes as BR
from research_engine import WARMUP
from merge_lab import boot_p

OUT = os.path.join(HERE, 'LIVESTACK_RESULTS.txt')
USD = 55751.0
P_BAR = 0.005
LOOK = 14
CHECKS = set(range(600, 931, 30))
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def full_bars(mk):
    df = pd.read_csv(mk.path); df['time'] = pd.to_datetime(df.time)
    return df.sort_values('time').reset_index(drop=True)


def ema_state(x, a=50, b=200):
    s = pd.Series(x)
    return np.sign(s.ewm(span=a, adjust=False).mean() - s.ewm(span=b, adjust=False).mean()).to_numpy()


def per_day(mk, df, arr):
    t = df.time.values.astype('datetime64[m]').astype('int64'); out = []
    for x in mk.days:
        ts = (np.datetime64(pd.Timestamp(x.date).strftime('%Y-%m-%dT00:00'), 'm').astype('int64') + x.M.astype(np.int64))
        j = np.searchsorted(t, ts, side='right') - 1
        out.append(np.where(j >= 0, arr[np.clip(j, 0, None)], 0))
    return out


def participation(mk):
    NS = 78; CV = np.full((mk.n, NS), np.nan)
    for k, x in enumerate(mk.days): CV[k, x.slot] = np.cumsum(x.V)
    out = []
    for k, x in enumerate(mk.days):
        if k < LOOK: out.append(np.full(x.nb, np.nan)); continue
        with np.errstate(all='ignore'): mu = np.nanmean(CV[k - LOOK:k], axis=0)
        out.append(CV[k, x.slot] / mu[x.slot])
    return out


def run(mk, ch=None, filt=None, part=None, worst_out=None):
    sig = mk.sigma(LOOK); pnl = np.full(mk.n, np.nan); nt = np.zeros(mk.n)
    for k, x in enumerate(mk.days):
        if k < LOOK or np.isnan(mk.pc[k]): continue
        O, H, L, C, M, vw, nb, A = x.O, x.H, x.L, x.C, x.M, x.vw, x.nb, x.A
        hi, lo = max(O[0], mk.pc[k]), min(O[0], mk.pc[k]); sg = sig[k, x.slot]
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        st = dict(d=0, u=0, ref=0.0, p=0.0, vol=0, worst=0.0)
        ext = 0.0
        fk = filt[k] if filt is not None else None; pk = part[k] if part is not None else None

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
            if M[i] not in CHECKS or np.isnan(sg[i]): continue
            ub = hi * (1 + sg[i]); lb = lo * (1 - sg[i])
            if d == 0: new = 1 if c > ub else (-1 if c < lb else 0)
            elif d == 1: new = -1 if c < lb else (0 if c < max(ub, vw[i]) else 1)
            else: new = 1 if c > ub else (0 if c > min(lb, vw[i]) else -1)
            if new != 0 and new != d:
                if fk is not None and fk[i] != new: new = 0
                if pk is not None and not pk[i] >= 1.0: new = 0
            if new != d:
                change(new, O[i + 1])
                if new != 0: ext = O[i + 1]
        if st['d'] != 0: mark(flat_i); change(0, C[flat_i])
        pnl[k] = st['p']; nt[k] = st['vol'] / 2
        if worst_out is not None: worst_out[k] = min(st['worst'], st['p'])
    return pnl, nt


def series(cfg, ctx, ticks=1):
    raw = pd.read_csv(os.path.join(HERE, 'raw_open930.csv'), index_col=0, parse_dates=True).open
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    c = (0.25 * ticks + 0.25) / p_ref
    parts = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQ', lambda d: d > N.JOIN)):
        mk = N.market(nm); w = np.full(mk.n, np.nan)
        kw = dict(ch=3.0 if 'CH' in cfg else None, worst_out=w)
        if 'Q8' in cfg: kw['filt'] = ctx[nm]['q8']
        if cfg == 'V1': kw['filt'] = ctx[nm]['obv']
        if cfg == 'V2': kw['part'] = ctx[nm]['part']
        p, n = run(mk, **kw); d = mk.dates
        sel = (np.arange(len(d)) >= WARMUP) & keep(d)
        o = np.array([x.O[0] for x in mk.days]); px = raw.reindex(d[sel]).values; px = np.where(np.isnan(px), o[sel], px)
        parts.append(pd.DataFrame({'r_close': np.nan_to_num(p[sel]) / px - n[sel] * 2 * c,
                                   'r_min': np.nan_to_num(w[sel]) / px - n[sel] * 2 * c, 'nt': n[sel]}, index=d[sel]))
    return pd.concat(parts)


def sharpe(x):
    s = x.std(ddof=1); return float(x.mean() / s * math.sqrt(252)) if s > 0 else float('nan')


def stats(x):
    eq = np.cumsum(x.values) * USD; dd = float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()); yr = x.mean() * 252 * USD
    return yr, dd, yr / dd if dd > 0 else float('nan')


def bulenox(s):
    usd, usdm, nt = (s.r_close * USD).values, (s.r_min * USD).values, s.nt.values.astype(float)
    a = BR.SIZES['50K']
    e = BL.eval_stats(usd, usdm, nt, 6, BL.SEED_REPORT, a['dd'], a['dll'], a['target'], a['qual'], resets=True)
    f = {m: BL.funded_stats(BL.master_qm, usd, usdm, nt, a, m, BL.SEED_REPORT) for m in (1, 2, 3, 4)}
    return e, f


def main():
    say(f"livestack.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (LIVESTACK_PLAN.txt)")
    ctx = {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = full_bars(mk)
        obv = np.cumsum(df.volume.values * np.sign(np.r_[0, np.diff(df.close.values)]))
        ctx[nm] = {'q8': per_day(mk, df, ema_state(df.close.values)), 'obv': per_day(mk, df, ema_state(obv)), 'part': participation(mk)}
        a, b = run(mk), S3.run(mk)[:2]; a2, b2 = run(mk, ch=3.0), S3.run(mk, ch=3.0)[:2]
        d1 = max(np.nanmax(np.abs(a[0] - b[0])), np.nanmax(np.abs(a[1] - b[1]))); d2 = max(np.nanmax(np.abs(a2[0] - b2[0])), np.nanmax(np.abs(a2[1] - b2[1])))
        say(f"parity {nm}: no switch vs strat3.run max |diff| {d1:.1e}; chandelier 3 vs strat3.run(ch=3) {d2:.1e} -> {'PASS' if d1 == 0 and d2 == 0 else 'FAIL'}")
    ref = pd.read_csv(os.path.join(HERE, 'intraday_worst.csv'), index_col=0, parse_dates=True)
    S = {c: series(c, ctx) for c in ('N', 'CH', 'Q8', 'Q8CH', 'V1', 'V2')}
    dc = float((S['N'].r_close - ref.r_close).abs().max()); dm = float((S['N'].r_min - ref.r_min).abs().max())
    say(f"parity: N series vs intraday_worst.csv: r_close {dc:.1e}, r_min {dm:.1e} -> {'PASS' if dc < 1e-12 and dm < 1e-12 else 'FAIL'}")
    say(); say("=" * 118); say("CONFIGURATIONS (joined NQ 2010-07..2019-05 + MNQ NinjaTrader to 2026-10-14; $ per micro at today's price and cost)")
    say("=" * 118)
    for c in ('N', 'CH', 'Q8', 'Q8CH'):
        x = S[c].r_close; yr, dd, m = stats(x); yrs = x.groupby(x.index.year).sum() * USD
        say(f"{c:5s} Sharpe {sharpe(x):.2f} | ${yr:,.0f}/yr | max DD ${dd:,.0f} | MAR {m:.2f} | worst year ${yrs.min():,.0f} | worst day ${S[c].r_min.min() * USD:,.0f}")
    say(f"Q8 on the live setup: Q8CH vs CH bootstrap P(Sharpe <= CH) {boot_p(S['Q8CH'].r_close, S['CH'].r_close):.4f}; "
        f"Q8 vs N {boot_p(S['Q8'].r_close, S['N'].r_close):.4f}")
    say(); say("VOLUME TRIALS (vs N; G1 bar 0.005)")
    n = S['N'].r_close; n2 = series('N', ctx, 2).r_close
    for c, desc in (('V1', 'OBV EMA 50/200 trend filter'), ('V2', 'participation: session volume so far >= its 14-session mean')):
        x = S[c].r_close; x2 = series(c, ctx, 2).r_close
        p = boot_p(x, n); p2 = boot_p(x2, n2)
        era = [(sharpe(x[x.index <= N.JOIN]), sharpe(n[n.index <= N.JOIN])), (sharpe(x[x.index > N.JOIN]), sharpe(n[n.index > N.JOIN]))]
        dif = (x - n).sort_values(ascending=False); kp = ~x.index.isin(dif.index[:5])
        g = {'G1': p < P_BAR, 'G2': all(a > b for a, b in era), 'G3': p2 < P_BAR, 'G4': sharpe(x[kp]) > sharpe(n[kp]), 'G5': stats(x)[2] >= stats(n)[2]}
        yr, dd, m = stats(x)
        say(f"{c} {desc}: Sharpe {sharpe(x):.2f} vs {sharpe(n):.2f} | ${yr:,.0f}/yr | max DD ${dd:,.0f} | MAR {m:.2f} | P {p:.4f} | "
            f"NQ {era[0][0]:.2f}/{era[0][1]:.2f} MNQ {era[1][0]:.2f}/{era[1][1]:.2f} | 2 ticks P {p2:.4f}")
        fails = [k for k, v in g.items() if not v]
        say(f"   VERDICT {c}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: S[c + '_pass'] = S[c]
    say(); say("=" * 118); say("BULENOX 50K (biglever models, Option 2, intraday worst points, reporting seed)"); say("=" * 118)
    say(f"{'config':<7}{'eval 6 micros: pass/try':>24}{'fees per funded':>17}   Master 2-yr payouts / open after 1 yr at 1 | 2 | 3 | 4 micros")
    for c in [c for c in ('N', 'CH', 'Q8', 'Q8CH') + tuple(k[:-5] for k in S if k.endswith('_pass'))]:
        e, f = bulenox(S[c])
        say(f"{c:<7}{e['p'] * 100:>23.1f}%{e['fees']:>16,.0f}   " + " | ".join(f"${f[m]['pay']:,.0f} / {f[m]['open1'] * 100:.0f}%" for m in (1, 2, 3, 4)), )
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
