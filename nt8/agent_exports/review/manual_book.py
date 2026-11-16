import os, glob
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
REC = os.path.join(os.path.dirname(HERE), 'records', 'nt_executions.csv')
LIVE = 'D:/nt_recorder/accounts'
PV = 2.0
FEE = 0.61
ET = 'America/New_York'


def fills(rec=REC, live=LIVE):
    parts = []
    if os.path.exists(rec):
        e = pd.read_csv(rec)
        parts.append(pd.DataFrame({'t': pd.to_datetime(e.time_utc, format='ISO8601'), 'acct': e.account_name, 'inst': e.instrument_name, 'id': e.ExecutionId,
                                   'name': e.Name, 'side': np.where(e.MarketPosition == 0, 1, -1), 'px': e.Price, 'q': e.Quantity}))
    for f in sorted(glob.glob(os.path.join(live, '*_executions.csv'))):
        if os.path.getsize(f) == 0: continue
        x = pd.read_csv(f)
        if x.empty: continue
        parts.append(pd.DataFrame({'t': pd.to_datetime(x.exec_utc, format='ISO8601'), 'acct': x.account, 'inst': x.instrument, 'id': x.exec_id,
                                   'name': x.order_name, 'side': np.where(x.position == 'Long', 1, -1), 'px': x.price, 'q': x.quantity}))
    if not parts: return pd.DataFrame(columns=['t', 'acct', 'inst', 'id', 'name', 'side', 'px', 'q', 'et'])
    f = pd.concat(parts).drop_duplicates('id').sort_values(['t', 'id']).reset_index(drop=True)
    f = f[f.acct.astype(str).str.startswith('BX') & f.inst.astype(str).str.startswith('MNQ')].copy()
    f['et'] = f.t.dt.tz_localize('UTC').dt.tz_convert(ET).dt.tz_localize(None)
    return f


def roundtrips(f):
    out = []
    for acct, g in f.groupby('acct', sort=False):
        lots = []
        for r in g.itertuples():
            q = int(r.q)
            while q > 0:
                if lots and lots[0][0] != r.side:
                    s, lq, lpx, lt, lname = lots[0]
                    m = min(q, lq)
                    out.append({'acct': acct, 'inst': r.inst, 'dir': s, 'qty': m, 'entry_et': lt, 'exit_et': r.et, 'entry': lpx, 'exit': r.px,
                                'entry_name': lname, 'exit_name': r.name,
                                'pts': (r.px - lpx) * s, 'usd': (r.px - lpx) * s * PV * m - 2 * FEE * m})
                    q -= m
                    if m == lq: lots.pop(0)
                    else: lots[0] = (s, lq - m, lpx, lt, lname)
                else:
                    lots.append((r.side, q, r.px, r.et, r.name)); q = 0
    cols = ['acct', 'inst', 'dir', 'qty', 'entry_et', 'exit_et', 'entry', 'exit', 'entry_name', 'exit_name', 'pts', 'usd']
    t = pd.DataFrame(out, columns=cols).sort_values('entry_et').reset_index(drop=True)
    t['hold_s'] = (t.exit_et - t.entry_et).dt.total_seconds()
    return t


def decisions(t):
    t = t.copy()
    t['key'] = t.entry_et.dt.floor('2s').astype(str) + t.dir.astype(str)
    g = t.groupby('key', sort=False)
    d = g.agg(entry_et=('entry_et', 'min'), exit_et=('exit_et', 'max'), dir=('dir', 'first'), entry=('entry', 'mean'),
              exit=('exit', 'mean'), qty=('qty', 'sum'), accts=('acct', 'nunique'), usd=('usd', 'sum'), first_acct=('acct', 'first'),
              exit_name=('exit_name', 'first'), inst=('inst', 'first'))
    d['pts'] = (d.exit - d.entry) * d.dir
    d['usd1'] = d.pts * PV - 2 * FEE
    d['hold_s'] = (d.exit_et - d.entry_et).dt.total_seconds()
    return d.sort_values('entry_et').reset_index(drop=True)


def day_stats(x, when):
    if len(x) == 0: return dict(n=0, days=0, mean=0.0, t=0.0, green=0.0, total=0.0, win=0.0)
    dd = x.groupby(when.dt.date).sum(); s = dd.std(ddof=1) if len(dd) > 1 else 0.0
    return dict(n=len(x), days=len(dd), mean=float(dd.mean()), t=float(dd.mean() / (s / np.sqrt(len(dd)))) if s > 0 else 0.0,
                green=float((dd > 0).mean()), total=float(x.sum()), win=float((x > 0).mean()))


def scorecard(day, rec=REC, live=LIVE, window=20):
    f = fills(rec, live)
    if f.empty: return ["manual Bulenox trading: no MNQ fills on Bulenox accounts in the records"]
    t = roundtrips(f); d = decisions(t)
    day = pd.Timestamp(day).date()
    lines = []
    td = t[t.exit_et.dt.date == day]
    if len(td):
        per = "; ".join(f"{a} {len(g)} trades ${g.usd.sum():+,.2f}" for a, g in td.groupby('acct'))
        lines.append(f"manual Bulenox trading {day}: {per}")
    else:
        lines.append(f"manual Bulenox trading {day}: no trades")
    days = sorted(d.entry_et.dt.date.unique()); last = [x for x in days if x <= day][-window:]
    w = d[d.entry_et.dt.date.isin(last)]
    s = day_stats(w.usd1, w.entry_et)
    lines.append(f"   last {len(last)} trading days at 1 micro: {s['n']} trades, win {s['win']:.0%}, ${s['mean']:+,.0f}/day (day-clustered t {s['t']:+.2f}), "
                 f"green days {s['green']:.0%}, total ${s['total']:+,.0f} - an edge needs t >= 2 over 60+ days")
    for acct, g in t.groupby('acct'):
        if not str(acct).startswith('BX-M'): continue
        g = g[g.exit_et.dt.date <= day]
        if len(g) == 0: continue
        s = day_stats(g.usd, g.exit_et)
        lines.append(f"   funded {acct} since {g.entry_et.min():%Y-%m-%d}: {s['n']} trades, win {s['win']:.0%}, ${s['total']:+,.2f} "
                     f"(fills + ${FEE:.2f}/side), ${s['mean']:+,.0f}/day over {s['days']} days (t {s['t']:+.2f})")
    return lines
