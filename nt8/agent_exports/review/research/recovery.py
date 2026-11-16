import os, sys, time, json
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
import naplus2 as N2
from naplus3 import engine3

OUT = os.path.join(HERE, 'RECOVERY_RESULTS.txt')
TABLE = os.path.join(HERE, 'regime_work', 'dry_spells.json')
LIVE_TICKS = 2.35
USD = 55751.0
YEARS = 7.5
NDAY = int(YEARS * 252)
BLOCK = 21
REPS = 10_000
SEED = 20261109
CAL = 365.25 / 252
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def stretches(x):
    eq = np.cumsum(x.values); d = x.index; out = []; pk = 0.0; pt = d[0]
    for v, t in zip(eq, d):
        if v >= pk:
            if v > pk + 1e-12 and (t - pt).days > 0: out.append(((t - pt).days, pt, t))
            if v > pk + 1e-12: pk, pt = v, t
    out.append(((d[-1] - pt).days, pt, None))
    return sorted(out, key=lambda z: -z[0])


def longest(x):
    return stretches(x)[0][0]


def u_paths(R):
    out = []
    for lo in range(0, R.shape[0], 1000):
        r = R[lo:lo + 1000]
        eq = np.concatenate([np.zeros((r.shape[0], 1)), np.cumsum(r, axis=1)], axis=1)
        hi = eq >= np.maximum.accumulate(eq, axis=1)
        last = np.maximum.accumulate(np.where(hi, np.arange(eq.shape[1]), 0), axis=1)
        out.append((np.arange(eq.shape[1]) - last).max(axis=1) * CAL)
    return np.concatenate(out)


def block_index(n, rng):
    nb = -(-NDAY // BLOCK); st = rng.integers(0, n - BLOCK, (REPS, nb))
    return (st[:, :, None] + np.arange(BLOCK)).reshape(REPS, -1)[:, :NDAY]


def mkt_returns():
    parts = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQ', lambda d: d > N.JOIN)):
        mk = N.market(nm); r = np.array([x.C[-1] / mk.pc[k] - 1 if not np.isnan(mk.pc[k]) else np.nan for k, x in enumerate(mk.days)])
        s = pd.Series(r, index=mk.dates); parts.append(s[keep(s.index)])
    return pd.concat(parts)


def sizes(base, rm, kind):
    eq = base.cumsum()
    if kind == 'F1':
        ok = (eq.shift(1) >= eq.shift(1).rolling(50).mean()).astype(float)
        return ok.where(eq.shift(1).rolling(50).count() >= 50, 1.0)
    if kind == 'F2':
        hi = np.maximum.accumulate(np.r_[0.0, eq.values])[1:]; at = eq.values >= hi - 1e-15; run = np.zeros(len(eq)); c = 0
        for i, a in enumerate(at):
            c = 0 if a else c + 1; run[i] = c
        prev = pd.Series(run, index=eq.index).shift(1).fillna(0)
        return pd.Series(np.where(prev > 60, 0.5, 1.0), index=eq.index)
    if kind == 'F3':
        r = rm.reindex(base.index); v = r.rolling(20, min_periods=20).std().shift(1).values; s = np.ones(len(v))
        for i in range(len(v)):
            past = v[:i]; past = past[~np.isnan(past)]
            if len(past) >= 250 and not np.isnan(v[i]): s[i] = float(np.clip(np.median(past) / v[i], 0.5, 2.0))
        return pd.Series(s, index=base.index)
    raise ValueError(kind)


def era_masks(x):
    return x.index <= N.JOIN, x.index > N.JOIN


def since_high(v):
    eq = np.r_[0.0, np.cumsum(v)]; hi = eq >= np.maximum.accumulate(eq)
    last = np.maximum.accumulate(np.where(hi, np.arange(len(eq)), 0))
    return (np.arange(len(eq)) - last)[1:]


def write_table(Q):
    F = {'N': lambda mk, nm, w: N2.run2(mk, worst_out=w), 'CH': lambda mk, nm, w: N2.run2(mk, ch=3.0, worst_out=w),
         'Q8CH': lambda mk, nm, w: N2.run2(mk, ch=3.0, filt=Q[nm], worst_out=w), 'K1CH': lambda mk, nm, w: engine3(mk, None, worst_out=w),
         'X3': lambda mk, nm, w: engine3(mk, Q[nm], worst_out=w)}
    T = {'_comment': f"recovery.py {pd.Timestamp.now():%Y-%m-%d}: sessions without a new equity high in the 2010-26 backtest (NQ joined to MNQ, "
                     f"{LIVE_TICKS} ticks + $0.50 a side, $ at today's price). share_ge[n] = share of backtest sessions that were at least n sessions "
                     f"past the last equity high. Read by daily_review.py.", 'live_ticks': LIVE_TICKS}
    say(); say(f"DRY-SPELL TABLE for the nightly review ({LIVE_TICKS} ticks a side): setup -> longest | share of sessions >= 20 / 60 / 120 / 250 sessions past a high")
    for k, fn in F.items():
        x = N2.joined(fn, LIVE_TICKS).r_close; s = since_high(x.values); L = int(s.max())
        share = [round(float((s >= n).mean()), 4) for n in range(L + 1)]
        T[k] = dict(longest=L, longest_from=str(x.index[int(np.argmax(s)) - L].date()) if L else '', span=f"{x.index[0]:%Y-%m-%d}..{x.index[-1]:%Y-%m-%d}",
                    share_ge=share)
        say(f"   {k:5s} {L} sessions | " + " / ".join(f"{share[n] * 100:.0f}%" if n <= L else "0%" for n in (20, 60, 120, 250)))
    with open(TABLE, 'w', encoding='utf-8') as f: json.dump(T, f, indent=1)
    say(f"   wrote {os.path.relpath(TABLE, HERE)}")


def main():
    say(f"recovery.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (RECOVERY_PLAN.txt)")
    Q = {}
    for nm in ('NQ', 'MNQ'):
        mk = N.market(nm); df = LS.full_bars(mk); Q[nm] = LS.per_day(mk, df, LS.ema_state(df.close.values))
    fb = lambda mk, nm, w: engine3(mk, None, worst_out=w)
    fx = lambda mk, nm, w: engine3(mk, Q[nm], worst_out=w)
    B1 = N2.joined(fb).r_close; B2 = N2.joined(fb, 2).r_close; X1 = N2.joined(fx).r_close
    assert len(B1) == len(X1) and (B1.index == X1.index).all()
    n_mnq = int((B1.index > N.JOIN).sum()); n_nq = int((B1.index <= N.JOIN).sum())
    say(f"days: NQ {n_nq} ({B1.index[0]:%Y-%m-%d}..{N.JOIN:%Y-%m-%d}), MNQ {n_mnq} (..{B1.index[-1]:%Y-%m-%d})")
    assert 2000 < n_nq < 2400 and 1800 < n_mnq < 2000, (n_nq, n_mnq)
    b = N2.stats(B1); x = N2.stats(X1)
    say(f"BASE (his run: chandelier 3, exit gate 4, lunch pause, EMA(20), no trend filter): Sharpe {b['sh']:.2f} | ${b['yr']:,.0f}/yr | "
        f"max DD ${b['dd']:,.0f} | worst year ${b['worst']:,.0f}")
    say(f"X3   (the same + trend filter):                                               Sharpe {x['sh']:.2f} | ${x['yr']:,.0f}/yr | "
        f"max DD ${x['dd']:,.0f} | worst year ${x['worst']:,.0f}")
    say(); say("Q1a LONGEST STRETCHES WITHOUT A NEW EQUITY HIGH (calendar days; * = still open at the end of the era)")
    for nm, s in (('BASE', B1), ('X3', X1)):
        for era, m in zip(('NQ 2010-19', 'MNQ 2019-26'), era_masks(s)):
            top = stretches(s[m])[:3]
            say(f"   {nm:4s} {era:11s}: " + " | ".join(f"{d} d {a:%Y-%m-%d}->{(z.strftime('%Y-%m-%d') if z is not None else 'open*')}" for d, a, z in top))
    rng = np.random.default_rng(SEED); I = block_index(len(B1), rng)
    UB = u_paths(B1.values[I]); pct294 = float((UB < 294).mean() * 100)
    say(); say(f"Q1b BASE bootstrap, {REPS:,} paths of {YEARS} years (21-day blocks of 2010-26 days): longest stretch median {np.median(UB):.0f} d, "
               f"10th-90th pct {np.percentile(UB, 10):.0f}-{np.percentile(UB, 90):.0f} d; 294 d sits at the {pct294:.0f}th percentile "
               f"({100 - pct294:.0f}% of 7.5-year paths have a stretch at least that long)")
    say(); say("Q1c THEORY: normal days, 7.5 years: annual Sharpe -> median longest stretch | share of paths with one >= 290 days")
    rng2 = np.random.default_rng(SEED + 1)
    for s in (1.0, 1.25, 1.5, 2.0, 2.5, 3.0):
        R = rng2.standard_normal((REPS, NDAY)) + s / np.sqrt(252); u = u_paths(R)
        say(f"   Sharpe {s:4.2f}: {np.median(u):4.0f} days | {(u >= 290).mean() * 100:3.0f}%")
    say(f"   (BASE's own Sharpe here: {b['sh']:.2f})")
    rm = mkt_returns()
    say(); say("Q2 FIXES (G1 shorter in both eras | G2 shorter in >= 80% of paired paths | G3 Sharpe >= BASE - 0.05 both eras and DD <= BASE | G4 G1 at 2 ticks)")
    eb = [longest(B1[m]) for m in era_masks(B1)]; sb = [LS.sharpe(B1[m]) for m in era_masks(B1)]; eb2 = [longest(B2[m]) for m in era_masks(B2)]
    say(f"   BASE: longest NQ {eb[0]} d / MNQ {eb[1]} d | Sharpe NQ {sb[0]:.2f} / MNQ {sb[1]:.2f} | DD ${b['dd']:,.0f} | 2 ticks: {eb2[0]} / {eb2[1]} d")
    survivors = []
    for kind, desc in (('F1', 'equity-curve filter (own P&L >= its 50-day mean)'), ('F2', 'half size after 60 days without a new high'),
                       ('F3', 'volatility targeting (median / 20-day vol, 0.5-2.0)')):
        z = sizes(B1, rm, kind); F = B1 * z; z2 = sizes(B2, rm, kind); F2 = B2 * z2
        t = N2.stats(F); ef = [longest(F[m]) for m in era_masks(F)]; sf = [LS.sharpe(F[m]) for m in era_masks(F)]; ef2 = [longest(F2[m]) for m in era_masks(F2)]
        UF = u_paths(F.values[I]); share = float((UF < UB).mean())
        g = {'G1': ef[0] < eb[0] and ef[1] < eb[1], 'G2': share >= 0.80, 'G3': sf[0] >= sb[0] - 0.05 and sf[1] >= sb[1] - 0.05 and t['dd'] <= b['dd'],
             'G4': ef2[0] < eb2[0] and ef2[1] < eb2[1]}
        say(f"   {kind} {desc}: mean size {z.mean():.2f} | longest NQ {ef[0]} d / MNQ {ef[1]} d | shorter in {share * 100:.0f}% of paths "
            f"(median {np.median(UF):.0f} vs {np.median(UB):.0f} d) | Sharpe NQ {sf[0]:.2f} / MNQ {sf[1]:.2f} | ${t['yr']:,.0f}/yr | DD ${t['dd']:,.0f} | "
            f"2 ticks {ef2[0]} / {ef2[1]} d")
        fails = [k for k, v in g.items() if not v]
        say(f"      VERDICT {kind}: {'PASS' if not fails else 'DEAD (' + ', '.join(fails) + ')'}")
        if not fails: survivors.append(kind)
    ex = [longest(X1[m]) for m in era_masks(X1)]; UX = u_paths(X1.values[I])
    say(f"   F4 (info) X3 = trend filter on: longest NQ {ex[0]} d / MNQ {ex[1]} d | shorter in {(UX < UB).mean() * 100:.0f}% of paths "
        f"(median {np.median(UX):.0f} d) | Sharpe NQ {LS.sharpe(X1[X1.index <= N.JOIN]):.2f} / MNQ {LS.sharpe(X1[X1.index > N.JOIN]):.2f}")
    say(); say("SURVIVORS: " + (", ".join(survivors) if survivors else "none"))
    write_table(Q)
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
