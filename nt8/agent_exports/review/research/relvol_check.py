"""Audit of relvol.py (RELVOL_PLAN.txt): an independent re-implementation of the filter on top of published.S1's code path (dict
history, load_rth sessions), roll windows re-derived from the raw instrument_id switches, statistics recomputed with pandas.
python relvol_check.py [databento folder]   -> compare with relvol_output.txt"""
import os, sys
import numpy as np, pandas as pd
import databento as dbn
from published import load_rth
import relvol

DBDIR = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\linde\OneDrive\Desktop\Desktop\databento_data'


def roll_dates_raw(files, days):
    """Trading date of the first bar of each new instrument_id (Globex day: after 17:00 ET = next day), then the 5 valid sessions before."""
    df = pd.concat([dbn.DBNStore.from_file(os.path.join(DBDIR, f)).to_df(map_symbols=False)[['instrument_id']] for f in files])
    df = df[~df.index.duplicated(keep='last')].sort_index()
    k = np.flatnonzero(df.instrument_id.values[1:] != df.instrument_id.values[:-1]) + 1
    et = df.index[k].tz_convert('America/New_York')
    tdays = [(t + pd.Timedelta(days=1)).date() if t.hour * 60 + t.minute > 17 * 60 else t.date() for t in et]
    valid = np.array(sorted(days)); out = set()
    for s in tdays:
        b = valid[valid < s]
        if len(b) and (valid >= s).any(): out |= set(b[-5:])
    return out


def s1_rv(days, pv, slip, comm, theta=None, scope='E', roll=frozenset(), look=14):
    dates = sorted(days); hist = []; vhist = []; prev_close = None; out = {}; nrev = 0; undefined_days = []
    for d in dates:
        g = days[d]; O = g.open.values; H = g.high.values; L = g.low.values; C = g.close.values; M = g.m.values.astype(int); V = g.volume.values.astype(float)
        o930 = O[0]; mv = {int(mm): abs(c / o930 - 1) for mm, c in zip(M, C)}
        v30 = {int(mm): float(V[(M > mm - 30) & (M <= mm)].sum()) for mm in M}
        if len(hist) >= look and prev_close is not None:
            tp = (H + L + C) / 3; vw = np.cumsum(tp * V) / np.maximum(np.cumsum(V), 1)
            pos = 0; entry = None; pnl = 0.0; pending = None; checks = set(range(600, 931, 30))
            flat_i = len(g) - 1 if int(M[-1]) >= 960 else len(g) - 2
            elig = [h for e, h in vhist if e][-look:]
            for i in range(len(g)):
                if pending is not None:
                    act, newpos = pending; px = O[i]
                    if pos != 0: pnl += (px - slip * pos - entry) * pos * pv - comm; pos = 0
                    if newpos != 0: entry = px + slip * newpos; pos = newpos; pnl -= comm
                    pending = None
                if i >= flat_i:
                    if pos != 0: pnl += (C[i] - slip * pos - entry) * pos * pv - comm; pos = 0
                    break
                mm = int(M[i])
                if mm not in checks: continue
                vals = [h[mm] for h in hist[-look:] if mm in h]
                if len(vals) < max(1, (look + 1) // 2): continue
                sig = float(np.mean(vals)); ub = max(o930, prev_close) * (1 + sig); lb = min(o930, prev_close) * (1 - sig); p = C[i]
                if pos == 0:
                    if p > ub: pending = ('enter', 1)
                    elif p < lb: pending = ('enter', -1)
                elif pos == 1:
                    if p < lb: pending = ('rev', -1)
                    elif p < max(ub, vw[i]): pending = ('exit', 0)
                else:
                    if p > ub: pending = ('rev', 1)
                    elif p > min(lb, vw[i]): pending = ('exit', 0)
                if pending is not None and pending[0] == 'rev': nrev += 1
                if theta is not None and d not in roll and pending is not None and (pending[0] == 'enter' or (pending[0] == 'rev' and scope == 'ER')):
                    vv = [h[mm] for h in elig if mm in h]
                    rv = v30[mm] / np.mean(vv) if (len(elig) >= look and len(vv) >= (look + 1) // 2) else np.nan
                    if np.isnan(rv): undefined_days.append(d)
                    if not (rv >= theta):
                        pending = None if pending[0] == 'enter' else ('exit', 0)
            out[d] = pnl
        hist.append(mv); vhist.append((d not in roll, v30)); prev_close = C[-1]
    s = pd.Series(out, dtype=float); s.index = pd.to_datetime(s.index)
    allv = pd.Series(0.0, index=pd.to_datetime(dates)); allv.loc[s.index] = s.values
    return allv, nrev, undefined_days


def st(s, a, b):
    s = s.iloc[30:]; s = s[(s.index >= a) & (s.index <= b)]
    return s.sum(), s.mean() / s.std(ddof=1) * np.sqrt(len(s))


if __name__ == '__main__':
    files = {'NT': ('nt_analyzer_bars.csv', 2.0, 0.5, None), 'DB': ('mnq_db_5m_2019-2026.csv', 2.0, 0.5, relvol.MNQ_FILES), 'NQ': ('nq_2010-2019_5m.csv', 20.0, 2.0, relvol.NQ_FILES)}
    for lab, (csv, pv, comm, raw) in files.items():
        days = load_rth(csv)
        roll = roll_dates_raw(raw, days) if raw else set()
        mk = relvol.Market(csv, pv, lab)
        rm = relvol.roll_mask(mk, relvol.switch_dates(raw)) if raw else np.zeros(mk.n, bool)
        roll_engine = set(np.array([x.date for x in mk.days])[rm])
        print(f"{lab}: roll-window sessions, raw instrument_id switches {len(roll)} vs relvol.py {len(roll_engine)}, identical {roll == roll_engine}")
        RV = relvol.rv_table(mk, ~rm)
        end = str(mk.dates[-1].date())
        pers = [('full', '2010-01-01', '2019-05-05')] if lab == 'NQ' else [('disc', '2019-01-01', '2022-12-31'), ('val', '2023-01-01', end)]
        base = None
        for th, sc in ((None, 'E'), (0.8, 'E'), (1.0, 'E'), (1.0, 'ER'), (1.25, 'E'), (1.5, 'E')):
            s, nrev, und = s1_rv(days, pv, 0.25, comm, theta=th, scope=sc, roll=roll)
            p, n, c = relvol.engine(mk, RV=RV, theta=th, scope=sc, nofilt=rm, comm=comm)
            e = pd.Series(np.nan_to_num(p), index=mk.dates)
            d = (s.reindex(e.index) - e).abs().max()
            line = " | ".join(f"{nm} ${st(s, a, b)[0]:,.0f} t {st(s, a, b)[1]:.2f}" for nm, a, b in pers)
            if th is None: base = s
            else:
                a, b = pers[-1][1], pers[-1][2]; m = (s.index >= a) & (s.index <= b); m[:30] = False
                bb = base[m]; top = bb.sort_values(ascending=False, kind='stable').index[:max(1, int(round(0.01 * m.sum())))]
                line += f" | top-1% kept {s[top].sum() / bb[top].sum() * 100:.1f}%"
            print(f"   theta {str(th):5s} {sc:2s}: independent {line} | max |daily diff| vs relvol.py ${d:.6f} | reversal signals {nrev}"
                  + (f" | undefined RV {len(und)} on {sorted(set(str(x) for x in und))[:3]}..., session index max {max(sorted(days).index(x) for x in und)}" if und else ""))
