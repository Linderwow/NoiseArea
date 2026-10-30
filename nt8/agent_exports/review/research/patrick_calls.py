r"""Score anyone's day-type calls (Patrick's model, a discretionary read, anything) against what the day did and against NoiseArea.
    python patrick_calls.py <calls.csv>          -> report on screen + patrick_calls_output.txt
    python patrick_calls.py template             -> writes patrick_calls_template.csv (the format)
calls.csv columns: date (YYYY-MM-DD, ET session), time (HH:MM ET when the call was made: 09:30 = before the open, else 10:00 or
10:30 - a call made later is scored at the next slot after it, never earlier), call = 'trend' / 'range' or a number 0..1 = P(trend).
The calls must have been written down BEFORE the time stated; a call that looks at the rest of the day scores like the oracle
(REGIME_RESULTS: Sharpe 3-5, tiny drawdowns) and proves nothing. Only sessions in the strategy's bar history are scored
(NinjaTrader MNQ bars 2019-05 .. today, NQ 2010-2019 before that).
What is reported: AUC of the call for 'the rest of the day trended' (eff) and for 'NoiseArea's day paid' (meta); accuracy; and the
stand-down gate (NoiseArea skips / exits on 'range' calls): $ per micro, max drawdown, vs NoiseArea on the same sessions; the
DAYCALL bar (AUC 0.60 meta / 0.65 eff to beat NoiseArea's Sharpe; 0.70 / 0.80+ for a big drawdown cut).
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N, history_extend as HX
import weekend as W, regime3 as R3

SLOTS = (570, 600, 630); USD = 58300.0


def day_labels():
    rows = []
    for nm, keep in (('NQ', lambda d: d <= N.JOIN), ('MNQX', lambda d: d > N.JOIN)):
        mk = N.market(nm); sig = mk.sigma(14); p, nt, _, _ = W.engine(mk)
        for k, x in enumerate(mk.days):
            if k < 14 or np.isnan(mk.pc[k]) or not keep(mk.dates[k]): continue
            sg = sig[k, x.slot]; slot_of = {int(s): i for i, s in enumerate(x.slot)}
            _, _, sd = R3.day_pass(x, mk.pc[k], sg) if not np.isnan(sg).any() else (None, None, {})
            r = dict(date=mk.dates[k], pnl=p[k], nt=nt[k], open=x.O[0])
            for T in SLOTS:
                i = -1 if T == 570 else slot_of.get((T - 575) // 5, None)
                if i is None: continue
                c0 = x.O[0] if T == 570 else x.C[i]; hh = x.H[i + 1:]; ll = x.L[i + 1:]; rng = hh.max() - ll.min() if len(hh) else 0.0
                r[f'eff{T}'] = abs(x.C[-1] - c0) / rng if rng > 0 else np.nan
                r[f'sd{T}'] = 0.0 if T == 570 else sd.get(T, (p[k],))[0]
            rows.append(r)
    return pd.DataFrame(rows).set_index('date')


def auc(score, y):
    m = np.isfinite(score) & np.isfinite(y); s, t = score[m], y[m]; pos, neg = (t == 1).sum(), (t == 0).sum()
    if pos == 0 or neg == 0: return np.nan
    r = pd.Series(s).rank().values; return (r[t == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)


def main(path):
    calls = pd.read_csv(path); calls['date'] = pd.to_datetime(calls.date)
    def to_p(v):
        s = str(v).strip().lower()
        if s in ('trend', 'trending', 't', '1'): return 1.0
        if s in ('range', 'consolidation', 'r', '0', 'chop'): return 0.0
        return float(s)
    calls['p'] = calls.call.map(to_p)
    def slot(t):
        h, m = str(t).split(':'); mins = int(h) * 60 + int(m)
        for T in SLOTS:
            if mins <= T: return T
        return None
    calls['slot'] = calls.time.map(slot)
    bad = calls.slot.isna().sum()
    if bad: print(f"{bad} calls made after 10:30 are not scored (no later slot in this scorer)")
    calls = calls.dropna(subset=['slot']).drop_duplicates('date', keep='first').set_index('date')
    L = day_labels(); D = calls.join(L, how='inner'); lines = []
    def pr(s): print(s); lines.append(s)
    pr(f"calls {len(calls)} ({calls.index.min().date()} .. {calls.index.max().date()}), scored {len(D)} on sessions in the bar history")
    eff = np.array([D.loc[d, f'eff{int(D.loc[d, "slot"])}'] for d in D.index]); med = np.nanmedian(eff)
    y_eff = np.where(np.isfinite(eff), (eff >= med).astype(float), np.nan)
    y_meta = np.where(D.nt > 0, (D.pnl > 0).astype(float), np.nan)
    a_e, a_m = auc(D.p.values, y_eff), auc(D.p.values, y_meta)
    acc = np.nanmean((D.p.values >= 0.5) == (y_eff == 1))
    pr(f"AUC for 'the rest of the day trended' (eff, median {med:.3f}): {a_e:.3f}; for 'NoiseArea's day paid' (meta, {int(np.isfinite(y_meta).sum())} traded days): {a_m:.3f}; "
       f"accuracy vs eff {acc * 100:.0f}% (0.5 = coin flip; DAYCALL bar: 0.65 eff / 0.60 meta to beat NoiseArea)")
    sd = np.array([D.loc[d, f'sd{int(D.loc[d, "slot"])}'] for d in D.index]); gated = np.where(D.p.values < 0.5, sd, D.pnl.values)
    px = D.open.values; rb = D.pnl.values / px * USD; rg = gated / px * USD
    def mdd(v): e = np.cumsum(v); return float((np.maximum.accumulate(np.r_[0.0, e])[1:] - e).max())
    pr(f"stand-down gate on 'range' calls ({np.mean(D.p.values < 0.5) * 100:.0f}% of days): ${rg.sum():,.0f} vs NoiseArea ${rb.sum():,.0f} on the same {len(D)} sessions; "
       f"max drawdown ${mdd(rg):,.0f} vs ${mdd(rb):,.0f} (per micro, gross of the gate's own exits at 1 tick)")
    if len(D) < 120: pr(f"NOTE: {len(D)} sessions is too few for a verdict (SE of an AUC ~ {1 / np.sqrt(len(D)) * 0.6:.2f}); keep collecting.")
    open(os.path.join(HERE, 'patrick_calls_output.txt'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')


if __name__ == '__main__':
    if sys.argv[1:2] == ['template']:
        pd.DataFrame({'date': ['2026-11-02', '2026-11-03'], 'time': ['09:30', '10:30'], 'call': ['trend', 'range']}).to_csv(
            os.path.join(HERE, 'patrick_calls_template.csv'), index=False); print('wrote patrick_calls_template.csv')
    elif len(sys.argv) > 1: main(sys.argv[1])
    else: print(__doc__)
