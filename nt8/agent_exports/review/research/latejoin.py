import os, sys, time
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import livestack as LS
from naplus3 import engine3

OUT = os.path.join(HERE, 'LATEJOIN_RESULTS.txt')
USD = 55751.0
LIVE_TICKS = 2.35
WARMUP = 15
LINES = []
T0 = time.time()


def say(s=''):
    print(s, flush=True); LINES.append(s)


def joins(mk, q, p_ref):
    po = {}; engine3(mk, q, pos_out=po); c = (0.25 * LIVE_TICKS + 0.25) / p_ref; rows = []
    for k, x in enumerate(mk.days):
        if k < WARMUP: continue
        arr = po.get(pd.Timestamp(x.date))
        if arr is None: continue
        pp = arr[x.slot].astype(int); O, C, M, nb = x.O, x.C, x.M, x.nb
        flat_i = nb - 1 if M[-1] >= 960 else nb - 2
        if flat_i < 2: continue
        seg_start = None
        for i in range(flat_i):
            p = pp[i]
            if p == 0: seg_start = None; continue
            if i == 0 or pp[i - 1] != p: seg_start = i
            if i + 1 >= flat_i: continue
            entry = O[seg_start + 1]; join = O[i + 1]
            ex = None
            for j in range(i + 1, flat_i):
                if pp[j] != p: ex = O[j + 1]; break
            if ex is None: ex = C[flat_i]
            if i == seg_start: continue
            rows.append((x.date, M[i], p * (ex / join - 1) - 2 * c, p * (join / entry - 1)))
    return pd.DataFrame(rows, columns=['date', 'minute', 'rem', 'open'])


def summ(d):
    g = d.groupby('date').rem.mean(); n = len(g)
    t = g.mean() / (g.std(ddof=1) / np.sqrt(n)) if n > 2 and g.std(ddof=1) > 0 else float('nan')
    S = d.groupby('date').rem.sum(); K = d.groupby('date').rem.size(); w = S.sum() / K.sum()
    se = np.sqrt(((S - w * K) ** 2).sum() * n / (n - 1)) / K.sum() if n > 2 else float('nan')
    return g.mean() * USD, t, n, len(d), (d.rem > 0).mean(), w * USD, w / se if se > 0 else float('nan')


def main():
    say(f"latejoin.py  {pd.Timestamp.now():%Y-%m-%d %H:%M}  (LATEJOIN_PLAN.txt)")
    p_ref = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
    say(f"price reference {p_ref:,.0f}; cost {LIVE_TICKS} ticks + $0.50 a side; $ per micro at today's price")
    verdict = {}
    for nm, keep in (('NQ', lambda d: d <= N.JOIN.date()), ('MNQ', lambda d: d > N.JOIN.date())):
        mk = N.market(nm); df = LS.full_bars(mk); q = LS.per_day(mk, df, LS.ema_state(df.close.values))
        d = joins(mk, q, p_ref); d = d[d.date.map(lambda z: keep(pd.Timestamp(z).date()))]
        assert len(d) > 5000, len(d)
        m, t, ndays, n, win, wm, wt = summ(d)
        say(); say(f"{nm}: {n:,} join points on {ndays:,} days with a position in progress")
        say(f"   {'':13s} PRE-REGISTERED day mean (t over days) | info: every join point equal (day-clustered t) | joins that made money")
        say(f"   {'ALL':13s} ${m:+7.2f} (t {t:5.2f}) | ${wm:+7.2f} (t {wt:5.2f}) | {win * 100:.0f}%")
        rows = {}
        for lab, sel in (('before 12:00', d.minute < 720), ('12:00-14:00', (d.minute >= 720) & (d.minute < 840)), ('after 14:00', d.minute >= 840),
                         ('open <= 0', d.open <= 0), ('open 0..+0.5%', (d.open > 0) & (d.open < 0.005)), ('open >= +0.5%', d.open >= 0.005)):
            s = d[sel]
            if len(s) < 30: say(f"   {lab:13s} too few ({len(s)})"); continue
            mm, tt, nd, nn, ww, wmm, wtt = summ(s); rows[lab] = mm
            say(f"   {lab:13s} ${mm:+7.2f} (t {tt:5.2f}) | ${wmm:+7.2f} (t {wtt:5.2f}) | {ww * 100:.0f}%  [{nn:,} joins, {nd:,} days]")
        verdict[nm] = (m > 0 and t >= 2.0, rows.get('open >= +0.5%', -1) > 0)
    keep_imm = all(a and b for a, b in verdict.values())
    say(); say("DECISION: " + ("KEEP 'Immediately submit, synchronize account'" if keep_imm else
                             "use 'Wait until flat, synchronize account' (joining a position in progress does not clear the bar)"))
    say(f"run {time.time() - T0:.0f} s")


if __name__ == '__main__':
    main()
    with open(OUT, 'w', encoding='utf-8') as f: f.write("\n".join(LINES) + "\n")
    print(f"wrote {OUT}")
