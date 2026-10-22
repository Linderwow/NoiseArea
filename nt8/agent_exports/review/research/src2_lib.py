r"""Shared data and scoring for the SOURCES2 program (SOURCES2_PLAN.txt): 1-minute NQ 2010-2019 / MNQ 2019-2026 / ES / MES caches built
from the local Databento files (no API calls), a per-session view, and the common scorecard.
    python src2_lib.py build     build regime_work\{nq,mnqx,es,mes}_1m.npz from the .dbn.zst files (once)
"""
import os, sys, ctypes
sys.dont_write_bytecode = True
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd

DB = r"C:\Users\linde\OneDrive\Desktop\Desktop\databento_data"
SRC = {'nq': ['NQ_ohlcv-1m_2010-2019.dbn.zst'], 'es': ['ES_ohlcv-1m_2010-2019.dbn.zst'],
       'mnqx': ['mnq_ohlcv1m_2018-01-01_2022-12-31.dbn.zst', 'mnq_ohlcv1m_2023-01-01_2025-12-31.dbn.zst', 'MNQ_ohlcv-1m_2026-2026.dbn.zst'],
       'mes': ['MES_ohlcv-1m_2019-2026.dbn.zst']}
CACHE = lambda name: os.path.join(HERE, 'regime_work', f'{name}_1m.npz')
P_REF = float(pd.read_csv(os.path.join(HERE, 'scaling_inputs', 'scaling_daily.csv'), index_col=0).attrs_p_ref.iloc[0])
N_USD = 2 * P_REF                                     # MNQ notional at today's price ($2 a point)
JOIN = pd.Timestamp('2019-05-05')
DISC, VAL, HOLD = ('2010-07-01', '2019-05-05'), ('2019-05-06', '2022-12-31'), ('2023-01-01', '2026-10-16')


def side_cost(ticks=1):
    """Cost of one order side as a return at today's price: `ticks` of slippage ($0.50 each) + $0.50 commission."""
    return (0.5 * ticks + 0.5) / N_USD


def build(name):
    import databento as dbn
    dfs = []
    for f in SRC[name]:
        df = dbn.DBNStore.from_file(os.path.join(DB, f)).to_df()
        dfs.append(df[['instrument_id', 'open', 'high', 'low', 'close', 'volume']])
    df = pd.concat(dfs); df = df[~df.index.duplicated(keep='last')].sort_index()
    if name == 'mnqx': df = df[df.index >= pd.Timestamp('2019-05-06', tz='UTC')]
    t = df.index.tz_convert('America/New_York').tz_localize(None)
    iid = df.instrument_id.values.astype(np.int64); o, h, l, c = (df[k].values.astype(float) for k in ('open', 'high', 'low', 'close'))
    chg = np.where(iid[1:] != iid[:-1])[0] + 1; cum = np.zeros(len(c))
    for k in chg: cum[:k] += o[k] - c[k - 1]          # back-adjusted copy for indicators: earlier bars shifted onto the later contract
    np.savez_compressed(CACHE(name), t=t.values.astype('datetime64[m]'), iid=iid, o=o, h=h, l=l, c=c, v=df.volume.values.astype(float), shift=cum)
    print(f"{name}: {len(c):,} bars {t[0]} .. {t[-1]}, {len(chg)} contract changes")


class Mins:
    """1-minute arrays (raw prices; a* = back-adjusted) and per-RTH-session index ranges; bars stamped at their start, New York time."""
    def __init__(self, name):
        z = np.load(CACHE(name)); self.__dict__.update({k: z[k] for k in z.files}); self.name = name
        tt = pd.DatetimeIndex(self.t); self.m = (tt.hour * 60 + tt.minute).values; self.date = tt.normalize().values.astype('datetime64[D]')
        self.ac = self.c + self.shift; self.ah = self.h + self.shift; self.al = self.l + self.shift; self.ao = self.o + self.shift
        rth = (self.m >= 570) & (self.m < 960); idx = np.where(rth)[0]; dts = self.date[idx]
        starts = np.r_[0, np.where(dts[1:] != dts[:-1])[0] + 1]; ends = np.r_[starts[1:] - 1, len(idx) - 1]
        self.days = {}
        for s, e in zip(starts, ends):
            i0, i1 = idx[s], idx[e]
            if self.m[i0] == 570 and i1 - i0 >= 200 and self.iid[i0] == self.iid[i1]: self.days[pd.Timestamp(dts[s])] = (i0, i1)
        self.dates = sorted(self.days)


def scorecard(s, label=''):
    """s: daily net return series (index = session date, 0 on no-trade days). Returns dict of the common statistics."""
    s = s.sort_index(); n = len(s); yrs = n / 252
    t = lambda x: x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else 0.0
    out = {'label': label, 'usd_yr': s.sum() * N_USD / yrs if n else 0.0, 't': t(s), 'sharpe': t(s) / np.sqrt(yrs) if n else 0.0}
    for k, (a, b) in (('disc', DISC), ('val', VAL), ('hold', HOLD)):
        x = s[(s.index >= a) & (s.index <= b)]; out[f't_{k}'] = t(x); out[f'usd_{k}'] = x.sum() * N_USD / max(len(x) / 252, 1e-9)
    mnq = s[s.index > JOIN]; out['t_mnq'] = t(mnq)
    yr = s.groupby(s.index.year).sum(); out['yrs_pos'] = float((yr > 0).mean()) if len(yr) else 0.0
    eq = (s * N_USD).cumsum(); out['maxdd'] = float((eq.cummax() - eq).max()) if n else 0.0
    out['days_traded'] = int((s != 0).sum())
    return out


class Panel:
    """NQ sessions (discovery era) then MNQ sessions, each with its Mins object, plus a daily table (one row per RTH session):
    raw O/H/L/C, adjusted aO/aH/aL/aC (continuous within each market), px = raw 09:30 open, V = RTH volume, overnight high/low/open
    (16:00 prior .. 09:29, raw), seg 0 = NQ / 1 = MNQ. Multi-day returns never cross the NQ/MNQ join or a contract change."""
    def __init__(self, only=None):
        self.nq = Mins('nq'); self.mnq = Mins('mnqx') if only != 'disc' else None
        self.sess = [(d, self.nq) for d in self.nq.dates if pd.Timestamp(DISC[0]) <= d <= JOIN]
        if only != 'disc': self.sess += [(d, self.mnq) for d in self.mnq.dates if d > JOIN]
        rows = []; prev = {}
        for k, (d, mk) in enumerate(self.sess):
            i0, i1 = mk.days[d]; p = prev.get(mk.name)
            j0 = p + 1 if p is not None and i0 - p < 1500 else max(i0 - 1, 0)
            on = slice(j0, i0) if i0 > j0 else slice(i0, i0)
            hasON = i0 > j0 and mk.iid[j0] == mk.iid[i0]
            rows.append(dict(date=d, seg=0 if mk is self.nq else 1, i0=i0, i1=i1, iid=mk.iid[i0],
                             O=mk.o[i0], H=mk.h[i0:i1 + 1].max(), L=mk.l[i0:i1 + 1].min(), C=mk.c[i1],
                             aO=mk.ao[i0], aH=mk.ah[i0:i1 + 1].max(), aL=mk.al[i0:i1 + 1].min(), aC=mk.ac[i1], V=mk.v[i0:i1 + 1].sum(),
                             ONH=mk.h[on].max() if hasON else np.nan, ONL=mk.l[on].min() if hasON else np.nan,
                             ONO=mk.o[j0] if hasON else np.nan, last_m=mk.m[i1]))
            prev[mk.name] = i1
        self.daily = pd.DataFrame(rows)
        self.daily['px'] = self.daily.O
        self.mk_of = lambda k: self.nq if self.daily.seg.iat[k] == 0 else self.mnq
        self.dates = pd.DatetimeIndex(self.daily.date)


def series(panel, trades):
    """trades: iterable of (k, gross_return, sides) booked on session k -> (gross, sides) daily series over all sessions."""
    g = np.zeros(len(panel.daily)); s = np.zeros(len(panel.daily))
    for k, r, n in trades: g[k] += r; s[k] += n
    return pd.Series(g, index=panel.dates), pd.Series(s, index=panel.dates)


def tstat(x):
    x = np.asarray(x, float); sd = x.std(ddof=1); return x.mean() / (sd / np.sqrt(len(x))) if len(x) > 2 and sd > 0 else 0.0


def evaluate(name, gross, sides, na=None, pub=None, boot=True):
    """Common stage-1 scorecard at 1 tick, plus 2 ticks, post-publication slice and the ADDITION bootstrap against NoiseArea `na`."""
    net = gross - sides * side_cost(1); net2 = gross - sides * side_cost(2)
    out = scorecard(net, name); out['t_2tick'] = tstat(net2.values); out['usd_2tick'] = net2.sum() * N_USD / (len(net2) / 252)
    out['sides_day'] = float(sides[sides > 0].mean()) if (sides > 0).any() else 0.0
    if pub is not None:
        x = net[net.index >= pd.Timestamp(pub)]; out['t_post'] = tstat(x.values); out['usd_post'] = x.sum() * N_USD / max(len(x) / 252, 1e-9)
    if na is not None and boot:
        import regime_test as rt
        common = na.index.intersection(net.index); a, b = na.reindex(common).values, net.reindex(common).values
        out['corr_na'] = float(np.corrcoef(a, b)[0, 1]) if b.std() > 0 else 0.0
        out['sh_na'] = rt.sharpe(a); out['sh_combo'] = rt.sharpe(a + b); out['p_add'] = rt.boot_sharpe_diff(common, a + b, a)
    return out, net


if __name__ == '__main__':
    if sys.argv[1:2] == ['build']:
        for nm in (sys.argv[2:] or ['nq', 'mnqx', 'es', 'mes']): build(nm)
    else: print(__doc__)
