import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from statistics import NormalDist
import manual_trades as M

OUT = os.path.join(HERE, 'MANUAL_RESULTS_A.txt')
MASTER = 'ACCOUNT-104'
HZ = (5, 15, 30, 60)
RNG = np.random.default_rng(20261112)
LINES = []


def say(s=''):
    print(s, flush=True); LINES.append(s)


def tt(x):
    x = np.asarray(x, float); s = x.std(ddof=1)
    return x.mean() / (s / np.sqrt(len(x))) if len(x) > 1 and s > 0 else float('nan')


def price_at(m, ts):
    j = m.index.searchsorted(ts, side='right') - 1
    return np.where(j >= 0, m.c.values[np.clip(j, 0, None)], np.nan), j


def enrich(d):
    rows = []
    for inst, g in d.groupby('inst'):
        m = M.minutes(inst); h = M.hourly(m)
        lo, hi = m.index.min(), m.index.max()
        g = g[(g.entry_et > lo + pd.Timedelta(days=9)) & (g.entry_et < hi - pd.Timedelta(minutes=60))].copy()
        for hz in HZ:
            p, _ = price_at(m, g.entry_et + pd.Timedelta(minutes=hz)); g[f'f{hz}'] = (p - g.entry.values) * g.dir.values
        jh = h.index.searchsorted(g.entry_et, side='right') - 1
        g['e50'] = h.e50.values[jh]; g['e200'] = h.e200.values[jh]; g['hend'] = h.index[jh]
        up = g.entry > np.maximum(g.e50, g.e200); dn = g.entry < np.minimum(g.e50, g.e200)
        g['ctx'] = np.where(up, 'above', np.where(dn, 'below', 'between'))
        g['wth'] = ((g.dir == 1) & up) | ((g.dir == -1) & dn)
        g['agn'] = ((g.dir == 1) & dn) | ((g.dir == -1) & up)
        g['dist'] = np.where(g.dir == 1, g.entry - np.maximum(g.e50, g.e200), np.minimum(g.e50, g.e200) - g.entry)
        g['cross_gap'] = g.e50 - g.e200
        tf = []
        for r in g.itertuples():
            ok = []
            for _ in range(40):
                off = pd.Timedelta(minutes=int(RNG.integers(20, 61)) * (1 if RNG.random() < 0.5 else -1))
                t0 = r.entry_et + off
                if t0.date() != r.entry_et.date() or not (lo < t0 < hi - pd.Timedelta(minutes=31)): continue
                p0, _ = price_at(m, pd.DatetimeIndex([t0])); p1, _ = price_at(m, pd.DatetimeIndex([t0 + pd.Timedelta(minutes=30)]))
                ok.append((p1[0] - p0[0]) * r.dir)
            tf.append(np.nanmean(ok) if ok else np.nan)
        g['rand30'] = tf
        br = []
        for r in g.itertuples():
            end = min(r.entry_et + pd.Timedelta(hours=6), pd.Timestamp(r.entry_et.date()) + pd.Timedelta(hours=16) if r.entry_et.hour < 16 else r.entry_et + pd.Timedelta(hours=6))
            w = m.loc[(m.index > r.entry_et) & (m.index <= end)]
            if w.empty: br.append(np.nan); continue
            s, tp = r.entry - 30 * r.dir, r.entry + 150 * r.dir
            hit_s = (w.l <= s) if r.dir == 1 else (w.h >= s); hit_t = (w.h >= tp) if r.dir == 1 else (w.l <= tp)
            i_s = np.argmax(hit_s.values) if hit_s.any() else 10 ** 9; i_t = np.argmax(hit_t.values) if hit_t.any() else 10 ** 9
            px = s if i_s <= i_t and i_s < 10 ** 9 else (tp if i_t < 10 ** 9 else w.c.values[-1])
            br.append((px - r.entry) * r.dir * M.PV - 2 * M.FEE)
        g['bracket_usd'] = br
        rows.append(g)
    return pd.concat(rows).sort_values('entry_et')


def main():
    f = M.fills(); t = M.roundtrips(f); d = M.decisions(t)
    say(f"manual_grade.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (MANUAL_PLAN.txt part A)")
    say(f"{len(f):,} MNQ fills, {f.acct.nunique()} Bulenox accounts, {f.et.min():%Y-%m-%d} .. {f.et.max():%Y-%m-%d %H:%M}; {len(t):,} round trips; "
        f"{len(d):,} decisions; account total ${t.usd.sum():,.2f}")
    mt = t[t.acct == MASTER]
    say(f"MASTER {MASTER}: {len(mt)} trades, ${mt.usd.sum():,.2f} (his screen 11-12: net liquidation $51,209.56 = +$1,209.56)")
    say(); say("=" * 120); say("A1  MONEY"); say("=" * 120)
    a = t.groupby('acct').agg(first=('entry_et', 'min'), last=('exit_et', 'max'), trips=('usd', 'size'), usd=('usd', 'sum'), max_qty=('qty', 'max'))
    for acct, r in a.sort_values('first').iterrows():
        say(f"   {acct:16s} {r['first']:%Y-%m-%d} .. {r['last']:%Y-%m-%d}  {int(r.trips):4d} trips  ${r.usd:+9,.2f}  max {int(r.max_qty)} contracts")
    def st(x, lab):
        w = x[x > 0]; l = x[x <= 0]
        say(f"   {lab}: {len(x)} trades | win {len(w) / len(x):.1%} | avg win ${w.mean():.2f} | avg loss ${l.mean():.2f} | PF {w.sum() / -l.sum():.2f} | "
            f"${x.mean():+.2f}/trade (t {tt(x):+.2f}) | total ${x.sum():+,.2f}")
    d['mo'] = d.entry_et.dt.to_period('M')
    st(d.usd1, 'all decisions, 1 micro')
    for mo, g in d.groupby('mo'): st(g.usd1, f'{mo}')
    early = d.entry_et < pd.Timestamp('2026-10-26'); st(d.usd1[early], 'to 10-25 (manual exits)'); st(d.usd1[~early], 'from 10-26 (ATM 30-pt stop)')
    st(d.usd1[d.first_acct == MASTER], 'Master')
    say("   Master by day: " + "; ".join(f"{k} ${v:+.2f}" for k, v in mt.groupby(mt.exit_et.dt.date).usd.sum().items()))
    say("   Master trades:")
    for r in mt.itertuples():
        say(f"      {r.entry_et:%m-%d %H:%M:%S} {'LONG ' if r.dir > 0 else 'SHORT'} {r.entry:9.2f} -> {r.exit:9.2f} {r.exit_name:8s} {r.pts:+7.2f} pt ${r.usd:+8.2f} "
            f"held {r.hold_s / 60:6.1f} min")
    say(); say("=" * 120); say("A2-A5  SKILL (decisions with 1-minute data)"); say("=" * 120)
    e = enrich(d); e['day'] = e.entry_et.dt.date
    say(f"decisions graded: {len(e):,} of {len(d):,} (minute data must cover 9 days before and 60 min after)")
    for hz in HZ:
        x = e[f'f{hz}'].dropna(); say(f"   move in his direction +{hz:2d} min: {x.mean():+6.2f} pt (t {tt(x):+.2f}, n {len(x)})")
    x = e.f30.values; dirs = e.dir.values; days = e.day.values; base = np.nanmean(x)
    raw = x * dirs; sims = np.empty(2000)
    ud, inv = np.unique(days, return_inverse=True)
    for i in range(2000):
        dd = dirs.copy()
        for k in range(len(ud)):
            idx = np.where(inv == k)[0]; dd[idx] = RNG.permutation(dirs[idx])
        sims[i] = np.nanmean(raw * dd)
    say(f"A2 direction skill: +30 min {base:+.2f} pt vs shuffled directions mean {sims.mean():+.2f} -> one-sided p {(sims >= base).mean():.3f}")
    r30 = e.rand30.values; ok = ~np.isnan(r30) & ~np.isnan(x)
    diff = x[ok] - r30[ok]
    say(f"A3 timing skill: his +30 min {x[ok].mean():+.2f} pt vs same direction 20-60 min earlier/later {r30[ok].mean():+.2f} pt -> difference {diff.mean():+.2f} "
        f"(t {tt(diff):+.2f}, one-sided p {1 - NormalDist().cdf(tt(diff)):.3f})")
    say(f"A4 hourly EMA context (his 60-min chart): above both {int((e.ctx == 'above').sum())}, below both {int((e.ctx == 'below').sum())}, "
        f"between {int((e.ctx == 'between').sum())}")
    for lab, msk in (('WITH (long above both / short below both)', e['wth']), ('AGAINST', e['agn']), ('BETWEEN', e.ctx == 'between')):
        g = e.usd1[msk]; say(f"   {lab:45s} {len(g):4d} trades ${g.mean():+7.2f}/trade (t {tt(g):+.2f}) total ${g.sum():+9,.2f} | win {(g > 0).mean():.1%}")
    w, nw = e.usd1[e['wth']].values, e.usd1[~e['wth']].values
    se = np.sqrt(w.var(ddof=1) / len(w) + nw.var(ddof=1) / len(nw)); tw = (w.mean() - nw.mean()) / se
    say(f"   TRIAL A4: WITH - NOT = ${w.mean() - nw.mean():+.2f}/trade, Welch t {tw:+.2f}, one-sided p {1 - NormalDist().cdf(tw):.4f} "
        f"(bar: t >= 2.69) -> {'PASS' if tw >= 2.69 else 'FAIL'}")
    nov = e[e.entry_et >= pd.Timestamp('2026-11-01')]
    for lab, msk in (('Nov WITH', nov['wth']), ('Nov not WITH', ~nov['wth'])):
        g = nov.usd1[msk]; say(f"   {lab:45s} {len(g):4d} trades ${g.mean():+7.2f}/trade (t {tt(g):+.2f})")
    say("   distance from the nearer EMA at entry, points (WITH trades): " + ", ".join(f"q{int(q * 100)} {v:.0f}" for q, v in e.dist[e['wth']].quantile([.1, .25, .5, .75, .9]).items()))
    near = e['wth'] & (e.dist <= 60)
    g = e.usd1[near]; say(f"   WITH and within 60 pt of the EMA (a bounce): {len(g)} trades ${g.mean():+.2f}/trade (t {tt(g):+.2f})")
    b = e.bracket_usd.dropna(); a5 = e.usd1[b.index]
    say(f"A5 exits: his actual ${a5.mean():+.2f}/trade vs fixed 30/150 bracket from the same fills ${b.mean():+.2f}/trade (n {len(b)}); "
        f"from 10-26: actual ${e.usd1[~(e.entry_et < pd.Timestamp('2026-10-26'))].mean():+.2f} vs bracket ${e.bracket_usd[~(e.entry_et < pd.Timestamp('2026-10-26'))].mean():+.2f}")
    mm = e[e.first_acct == MASTER]
    say("   Master entries vs the hourly EMAs:")
    for r in mm.itertuples():
        say(f"      {r.entry_et:%m-%d %H:%M} {'LONG ' if r.dir > 0 else 'SHORT'} {r.entry:9.2f} | EMA50 {r.e50:9.2f} EMA200 {r.e200:9.2f} | price {r.ctx:7s} "
            f"| {'WITH' if r.wth else ('AGAINST' if r.agn else 'between')} | ${r.usd1:+8.2f}")
    e.to_csv(os.path.join(HERE, 'regime_work', 'manual_decisions.csv'), index=False)


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
