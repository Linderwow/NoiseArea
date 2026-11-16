import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L2, ml_lab as ML, ml_run as MR, ind_lab as IL, growth_e as GE
import biglever as BL, bulenox_routes as BR

CROSS = ['x_r15', 'x_r60', 'x_ropen', 'rel15', 'rel_open']
IL.FEATS = [f for f in IL.FEATS if f not in CROSS]
OUT = os.path.join(HERE, 'E5N_REPORT.txt')
U = L2.N_USD
LINES = []


def say(s=''):
    print(s, flush=True); LINES.append(s)


def trades(T, pred, thr, c1):
    rows = []; days = []
    for k, g in T.assign(pred=pred).groupby('k', sort=True):
        o930 = g.open930.iat[0]; pos = 0; last = None; cur = None; day_r = 0.0; worst = 0.0; sides = 0; realized = 0.0
        for r in g.itertuples():
            if not np.isfinite(r.pred): continue
            new = 1 if r.pred > thr else (-1 if r.pred < -thr else 0)
            if pos != 0:
                open_r = pos * (r.entry - cur['px']) / o930
                worst = min(worst, realized + open_r - sides * c1)
            if new != pos:
                if pos != 0:
                    ret = pos * (r.entry - cur['px']) / o930; realized += ret
                    rows.append(dict(date=g.date.iat[0], dir=pos, t_in=cur['t'], t_out=int(r.dec), px_in=cur['px'], px_out=r.entry, o930=o930, ret=ret - 2 * c1))
                sides += abs(new - pos)
                if new != 0: cur = dict(px=r.entry, t=int(r.dec))
                pos = new
        if pos != 0:
            ret = pos * (g.exitp.iat[-1] - cur['px']) / o930; realized += ret; sides += 1
            rows.append(dict(date=g.date.iat[0], dir=pos, t_in=cur['t'], t_out=int(g.dec.iat[-1]) + 5, px_in=cur['px'], px_out=g.exitp.iat[-1], o930=o930, ret=ret - 2 * c1))
        if sides: days.append(dict(date=g.date.iat[0], net=realized - sides * c1, worst=min(worst, realized - sides * c1), n=sides / 2))
    return pd.DataFrame(rows), pd.DataFrame(days)


def stats(d, lab):
    x = d.net * U; eq = x.cumsum(); dd = eq.cummax() - eq; i = int(dd.values.argmax())
    peak = int(eq.values[:i + 1].argmax()) if i > 0 else 0
    under = (dd > 0).astype(int); longest = int(under.groupby((under == 0).cumsum()).sum().max())
    neg = x[x < 0]; sortino = x.mean() / np.sqrt((np.minimum(x, 0) ** 2).mean()) * np.sqrt(252)
    say(f"{lab}: {len(x)} sessions ({int((x != 0).sum())} traded) | ${x.mean() * 252:,.0f}/yr | Sharpe {x.mean() / x.std() * np.sqrt(252):.2f} | Sortino {sortino:.2f} | "
        f"daily sd ${x.std():,.0f} | positive days {np.mean(x > 0):.0%} | best ${x.max():,.0f} worst ${x.min():,.0f} | max DD ${dd.max():,.0f} "
        f"({d.date.iat[peak]:%Y-%m-%d} -> {d.date.iat[i]:%Y-%m-%d}) | longest time under water {longest} sessions")


def main():
    t0 = time.time(); c1 = MR.SIDE(2.35); thr = 4 * c1
    nq, mq = ML.Sessions('nq'), ML.Sessions('mnqx')
    Ta = IL.features(nq, None, 5, 5); Ta = Ta[Ta.date <= GE.JOIN]
    Tb = IL.features(mq, None, 5, 5); Tb = Tb[Tb.date > GE.JOIN].assign(k=lambda d: d.k + 10 ** 6)
    T = pd.concat([Ta, Tb], ignore_index=True); T = T[T.dec >= 15].reset_index(drop=True); p = GE.walk(T)
    tr, dy = trades(T, p, thr, c1); dy = dy[pd.to_datetime(dy.date).dt.year >= 2013].reset_index(drop=True); dy['date'] = pd.to_datetime(dy.date)
    tr['date'] = pd.to_datetime(tr.date); tr = tr[tr.date.dt.year >= 2013]
    ref, _ = ML.trade(T, p, thr, c1); ref = ref[ref.index.year >= 2013]
    chk = float(np.max(np.abs(ref.values - dy.set_index('date').net.reindex(ref.index).values)))
    say(f"E5N REPORT  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (E5n = 5-min indicator model without cross-index inputs; walk-forward, refit each January)")
    say(f"Convention: $ per MNQ micro at today's price (return x {U:,.0f}), live cost 2.35 ticks + $0.61 a side; out-of-sample 2013-2026")
    say(f"audit: trade-by-trade rebuild == the graded daily series, max |diff| {chk:.1e} -> {'PASS' if chk < 1e-12 else 'FAIL'}")
    say(); say("1. PERFORMANCE AND RISK")
    alls = pd.DatetimeIndex(sorted(set(pd.to_datetime(T.date)[pd.to_datetime(T.date).dt.year >= 2013])))
    dz = dy.set_index('date')[['net', 'worst', 'n']].reindex(alls).fillna(0.0).rename_axis('date').reset_index()
    say('   (every session counts, days without a trade as $0; an earlier version of this report annualised over traded days only and overstated $/yr and Sharpe)')
    stats(dz, '2013-2026 all'); stats(dz[dz.date <= GE.JOIN], 'NQ 2013-19   '); stats(dz[dz.date > GE.JOIN], 'MNQ 2019-26  '); stats(dz[dz.date >= '2023-01-01'], '2023-2026    ')
    yr = dy.groupby(dy.date.dt.year).net.sum() * U; mo = dy.groupby(dy.date.dt.to_period('M')).net.sum() * U
    say("   per year: " + "; ".join(f"{y} {v:+,.0f}" for y, v in yr.items()))
    say(f"   months positive {np.mean(mo > 0):.0%} ({int((mo > 0).sum())} of {len(mo)}); worst month ${mo.min():,.0f} ({mo.idxmin()}); best ${mo.max():,.0f} ({mo.idxmax()})")
    say(); say("2. TRADES")
    r = tr.ret * U; w, l = r[r > 0], r[r <= 0]
    say(f"   {len(tr):,} trades ({len(tr) / 13.8:,.0f}/yr, {dy.n.mean():.1f} per traded day, traded on {len(dy)} of {T[T.date.dt.year >= 2013].k.nunique()} sessions)")
    say(f"   win rate {len(w) / len(r):.1%} | avg win ${w.mean():,.2f} | avg loss ${l.mean():,.2f} | payoff {w.mean() / -l.mean():.2f} | profit factor {w.sum() / -l.sum():.2f} | "
        f"expectancy ${r.mean():,.2f}/trade | hold median {np.median(tr.t_out - tr.t_in):.0f} min (mean {np.mean(tr.t_out - tr.t_in):.0f})")
    for dr, lab in ((1, 'long'), (-1, 'short')):
        x = r[tr.dir == dr]; say(f"   {lab:5s}: {len(x):,} trades, win {np.mean(x > 0):.1%}, ${x.sum() / 13.8:,.0f}/yr, ${x.mean():,.2f}/trade")
    tb = pd.cut(570 + tr.t_in, [570, 630, 690, 750, 810, 870, 960], right=False, labels=['09:30-10:30', '10:30-11:30', '11:30-12:30', '12:30-13:30', '13:30-14:30', '14:30-16:00'])
    say("   by entry time: " + "; ".join(f"{b} {int((tb == b).sum())} trades ${r[tb == b].sum() / 13.8:+,.0f}/yr" for b in tb.cat.categories))
    say(); say("3. COST SENSITIVITY (same forecasts; $ per year, t)")
    for tk in (1.0, 2.35, 3.5, 5.0, 7.0):
        s, _ = ML.trade(T, p, thr, MR.SIDE(tk)); s = s[s.index.year >= 2013]
        say(f"   {tk:4.2f} ticks a side: ${s.mean() * U * 252:,.0f}/yr, t {MR.tt(s):+.2f}")
    say(); say("4. BULENOX 50K MODEL (biglever; MNQ 2019-26 days; intraday worst marked at every 5-min decision)")
    sess = pd.DatetimeIndex(sorted(set(pd.to_datetime(T.date)[pd.to_datetime(T.date) > GE.JOIN])))
    full = dy.set_index('date')[['net', 'worst', 'n']].reindex(sess).fillna(0.0)
    say(f"   {len(sess)} MNQ sessions 2019-26, traded on {int((full.n > 0).sum())}")
    usd = (full.net * U).values; usdm = (full.worst * U).values; nt = full.n.values; a = BR.SIZES['50K']
    say(f"   rules: trailing drawdown ${a['dd']:,}, daily loss limit ${a['dll']:,}, evaluation target ${a['target']:,}; fees: evaluation ${a['qual']}, activation ${a['act']}")
    for q in (1, 2, 3):
        e = BL.eval_stats(usd, usdm, nt, q, BL.SEED_REPORT, a['dd'], a['dll'], a['target'], a['qual'], resets=True)
        f = BL.funded_stats(BL.master_qm, usd, usdm, nt, a, q, BL.SEED_REPORT)
        say(f"   {q} micro{'s' if q > 1 else ' '}: evaluation pass {e['p'] * 100:5.1f}% per try (fees per pass ${e['fees']:,.0f}) | funded Master: 2-yr payouts ${f['pay']:,.0f}, "
            f"any payout {f['anyp'] * 100:.0f}%, open after 1 yr {f['open1'] * 100:.0f}% / 2 yrs {f['open2'] * 100:.0f}%")
    say(); say("5. PREDICTIONS (block bootstrap of MNQ 2019-26 days, 21-day blocks, 20,000 paths; 1 micro, no prop rules)")
    x = usd; mu = x.mean(); rng = np.random.default_rng(20261115)
    def boot(n, shift):
        nb = -(-n // 21); st = rng.integers(0, len(x) - 21, (20000, nb)); idx = (st[:, :, None] + np.arange(21)).reshape(20000, -1)[:, :n]
        y = x[idx] - shift; eq = y.cumsum(1); dd = (np.maximum.accumulate(eq, 1) - eq).max(1); return y.sum(1), dd
    fw = pd.read_csv(os.path.join(HERE, 'fwd', 'shadow_ledger_e5n.csv')) if os.path.exists(os.path.join(HERE, 'fwd', 'shadow_ledger_e5n.csv')) else None
    for lab, sh in (('as tested', 0.0), ('half the edge (typical haircut for a model picked after many tries)', mu / 2), ('no edge', mu)):
        say(f"   {lab}:")
        for n, nm in ((21, '1 month'), (63, '3 months'), (252, '1 year')):
            s, dd = boot(n, sh)
            say(f"      {nm:8s}: P(profit) {np.mean(s > 0):5.1%} | median ${np.median(s):+,.0f} | 5%..95% ${np.percentile(s, 5):+,.0f} .. ${np.percentile(s, 95):+,.0f} | "
                f"median max DD ${np.median(dd):,.0f}, 95% ${np.percentile(dd, 95):,.0f}")
        if fw is not None and len(fw):
            s, _ = boot(len(fw), sh); obs = fw.net_usd_1micro.sum()
            say(f"      forward test so far: {len(fw)} sessions ${obs:+,.0f}; chance of that or worse under this scenario {np.mean(s <= obs):.1%}")
    say(f"run {time.time() - t0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as fh: fh.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
