import os, sys, glob, datetime as dt
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import phaseval as PV

MINUTE = r'D:\nt_ticks\minute\MNQ'
LEDGER = os.path.join(HERE, 'fwd', 'shadow_ledger_phase.csv'); START = pd.Timestamp('2026-11-11')


class Minutes:
    def __init__(self, path):
        df = pd.read_parquet(path); self.name = os.path.basename(path)[:-8]
        t = (pd.DatetimeIndex(df.time) - pd.Timedelta(minutes=1)).tz_convert('America/New_York').tz_localize(None)
        self.t = t.values.astype('datetime64[m]'); self.o, self.h, self.l, self.c = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
        self.v = df.volume.values.astype(float)
        self.ao, self.ah, self.al, self.ac = self.o, self.h, self.l, self.c; self.iid = np.zeros(len(t), np.int64)
        self.m = (t.hour * 60 + t.minute).values; self.date = t.normalize().values.astype('datetime64[D]')
        rth = (self.m >= 570) & (self.m < 960); idx = np.where(rth)[0]; dts = self.date[idx]; self.days = {}; self.rthvol = {}
        if len(idx):
            st = np.r_[0, np.where(dts[1:] != dts[:-1])[0] + 1]; en = np.r_[st[1:] - 1, len(idx) - 1]
            for s, e in zip(st, en):
                i0, i1 = idx[s], idx[e]
                if self.m[i0] == 570 and i1 - i0 >= 200 and self.m[i1] >= 959:
                    d = pd.Timestamp(dts[s]); self.days[d] = (i0, i1); self.rthvol[d] = self.v[i0:i1 + 1].sum()
        self.dates = sorted(self.days)


def front_trades(start):
    mks = [Minutes(p) for p in sorted(glob.glob(os.path.join(MINUTE, '*.parquet')))[-3:]]
    own = {}
    for mk in mks:
        for d in mk.dates:
            if d >= start and (d not in own or mk.rthvol[d] > own[d].rthvol[d]): own[d] = mk
    parts = []
    for mk in mks:
        ds = [d for d, m in own.items() if m is mk]
        if ds:
            tr = PV.trades_market(mk, ds); tr['contract'] = mk.name; parts.append(tr)
    tr = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=['date', 'dr', 'e', 'x', 'em', 'xm', 'o930', 'contract'])
    return tr, sorted(own)


def update(start=START, ledger=LEDGER):
    tr, done = front_trades(start)
    have = set(pd.read_csv(ledger, parse_dates=['date']).date) if os.path.exists(ledger) else set()
    new = [d for d in done if d not in have]
    if new:
        now = dt.datetime.now().strftime('%Y-%m-%d %H:%M'); rows = []
        for d in new:
            x = tr[tr.date == d]
            if len(x) == 0: rows.append(dict(date=d.date(), contract='', dr=0, entry_min='', entry=np.nan, exit_min='', exit=np.nan, pts=0.0, recorded=now))
            for r in x.itertuples():
                rows.append(dict(date=d.date(), contract=r.contract, dr=int(r.dr), entry_min=f"{r.em // 60:02d}:{r.em % 60:02d}", entry=r.e,
                                 exit_min=f"{r.xm // 60:02d}:{r.xm % 60:02d}", exit=r.x, pts=round(float(r.dr * (r.x - r.e)), 2), recorded=now))
        pd.DataFrame(rows).to_csv(ledger, mode='a', header=not os.path.exists(ledger), index=False)
    print(f"phase-oscillator paper ledger (E03, information): {len(new)} session(s) added")


if __name__ == '__main__':
    if sys.argv[1:2] == ['check']:
        import src2_lib as L
        tr, _ = front_trades(pd.Timestamp('2026-09-15'))
        db = L.Mins('mnqx'); ds = [d for d in db.dates if pd.Timestamp('2026-09-15') <= d <= pd.Timestamp('2026-10-15')]
        ref = PV.trades_market(db, ds)
        a = tr[tr.date <= pd.Timestamp('2026-10-15')][['date', 'dr', 'em', 'xm']].reset_index(drop=True)
        b = ref[['date', 'dr', 'em', 'xm']].reset_index(drop=True)
        print("NT minute store trades 2026-09-15..10-15:"); print(a.to_string())
        print("Databento trades same days:"); print(b.to_string())
        print("identical:", a.equals(b))
    else:
        update()
