r"""Databento price quotes (metadata.get_cost only - free, nothing is downloaded or bought) for an order-flow test of NoiseArea's band
breakouts: tick data around each entry decision, on MNQ 2019-2026 (the live market) and NQ 2010-2019 (the deep market, discovery).
    python tickdata_quote.py sample     cost per window type from a sample of real entries, scaled to all entries -> tickdata_quote.txt
    python tickdata_quote.py exact W S  exact cost of window W (W6 / W15 / W35) in schema S for every MNQ entry (and NQ entry day)
"""
import os, sys, ctypes, time
sys.dont_write_bytecode = True
try: ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
except Exception: pass
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import databento as db

WIN = {'W6': (-5, 1), 'W15': (-15, 2), 'W35': (-30, 5)}       # minutes around the decision (the 5-minute bar close that triggers the entry)
SCHEMAS = ('trades', 'tbbo')
client = db.Historical()                                      # key from DATABENTO_API_KEY


def cost(sym, schema, s, e, tries=4):
    for k in range(tries):
        try:
            return client.metadata.get_cost(dataset='GLBX.MDP3', symbols=[sym], stype_in='continuous', schema=schema,
                                            start=s.isoformat(), end=e.isoformat())
        except Exception as ex:
            if k == tries - 1: return float('nan')
            time.sleep(2 + 3 * k)


def run_jobs(jobs, workers=16):
    """jobs: list of (key, sym, schema, start, end) -> list of (key, usd), in parallel, with progress lines."""
    from concurrent.futures import ThreadPoolExecutor
    out = []; t0 = time.time()
    with ThreadPoolExecutor(workers) as ex:
        for n, r in enumerate(ex.map(lambda j: (j[0], cost(*j[1:])), jobs), 1):
            out.append(r)
            if n % 25 == 0 or n == len(jobs): print(f"  {n}/{len(jobs)} quotes, {time.time() - t0:.0f}s", flush=True)
    return out


def utc(ts): return pd.Timestamp(ts).tz_localize('America/New_York').tz_convert('UTC')


def mnq_entries():
    t = pd.read_csv(os.path.join(HERE, 's1_reference_trades_ntbars_v3.csv'), parse_dates=['entry_time'])
    return t.entry_time - pd.Timedelta(minutes=5)                  # decision = close of the bar before the fill bar


def nq_entry_days():
    import regime_test as rt
    dts, R, NT, *_ = rt.joined('MNQ')
    s = pd.Series(NT[:, rt.A_DEF], index=dts); s = s[(s.index <= rt.JOIN) & (s > 0)]
    return s                                                       # trades per NQ day (1 entry per trade)


def sample():
    rng = np.random.default_rng(7); out = []
    dec = mnq_entries(); tod = (dec.dt.hour * 60 + dec.dt.minute).values
    by_year = dec.groupby(dec.dt.year)
    nq = nq_entry_days(); nq_year = nq.groupby(nq.index.year).sum()
    jobs = []
    for yr, g in by_year:                                          # MNQ: 4 real entries a year
        for d in rng.choice(g.values, min(4, len(g)), replace=False):
            d = pd.Timestamp(d)
            for w, (a, b) in WIN.items():
                for sc in SCHEMAS:
                    jobs.append((('MNQ', yr, w, sc), 'MNQ.v.0', sc, utc(d + pd.Timedelta(minutes=a)), utc(d + pd.Timedelta(minutes=b))))
            day = d.normalize()
            for sc in SCHEMAS:
                jobs.append((('MNQ', yr, 'RTH', sc), 'MNQ.v.0', sc, utc(day + pd.Timedelta(hours=9.5)), utc(day + pd.Timedelta(hours=16))))
    for yr in nq_year.index:                                       # NQ: 3 real entry days a year, entry time drawn from MNQ's time-of-day mix
        days = nq[nq.index.year == yr].index
        for day in rng.choice(days.values, min(3, len(days)), replace=False):
            day = pd.Timestamp(day); d = day + pd.Timedelta(minutes=int(rng.choice(tod)))
            for w, (a, b) in WIN.items():
                for sc in SCHEMAS:
                    jobs.append((('NQ', yr, w, sc), 'NQ.v.0', sc, utc(d + pd.Timedelta(minutes=a)), utc(d + pd.Timedelta(minutes=b))))
            for sc in SCHEMAS:
                jobs.append((('NQ', yr, 'RTH', sc), 'NQ.v.0', sc, utc(day + pd.Timedelta(hours=9.5)), utc(day + pd.Timedelta(hours=16))))
    rows = [(*key, usd) for key, usd in run_jobs(jobs)]
    df = pd.DataFrame(rows, columns=['mkt', 'year', 'win', 'schema', 'usd'])
    df.to_csv(os.path.join(HERE, 'tickdata_quote_sample.csv'), index=False)
    n_mnq = dec.groupby(dec.dt.year).size(); n_mnq_days = dec.dt.normalize().groupby(dec.dt.year).nunique()
    n_nq = nq_year; n_nq_days = nq.groupby(nq.index.year).size()
    L = [f"Databento GLBX.MDP3 cost quotes, {pd.Timestamp.now():%Y-%m-%d %H:%M} (get_cost; nothing downloaded or bought)",
         f"entries: MNQ {len(dec)} ({dec.min():%Y-%m-%d} .. {dec.max():%Y-%m-%d}), NQ {int(n_nq.sum())} on {len(nq)} days (2010-2019)",
         "estimated total = sum over years of (mean sampled cost per window that year x entries that year); RTH = whole 09:30-16:00 of each entry day"]
    for mkt, n_e, n_d in (('MNQ', n_mnq, n_mnq_days), ('NQ', n_nq, n_nq_days)):
        for sc in SCHEMAS:
            parts = []
            for w in list(WIN) + ['RTH']:
                m = df[(df.mkt == mkt) & (df.win == w) & (df.schema == sc)].groupby('year').usd.mean()
                n = n_d if w == 'RTH' else n_e
                tot = float(sum(m.get(y, m.mean()) * n[y] for y in n.index))
                parts.append(f"{w} ${tot:,.0f}")
            L.append(f"   {mkt:3s} {sc:6s}: " + " | ".join(parts))
    for mkt in ('MNQ', 'NQ'):
        m = df[(df.mkt == mkt) & (df.win == 'W6') & (df.schema == 'trades')].groupby('year').usd.mean()
        L.append(f"   {mkt} trades W6 mean $ per entry by year: " + " ".join(f"{y} {v:.3f}" for y, v in m.items()))
    txt = "\n".join(L); print(txt); open(os.path.join(HERE, 'tickdata_quote.txt'), 'w').write(txt + "\n")


def exact(w, sc):
    a, b = WIN[w]; dec = mnq_entries()
    res = run_jobs([(i, 'MNQ.v.0', sc, utc(d + pd.Timedelta(minutes=a)), utc(d + pd.Timedelta(minutes=b))) for i, d in enumerate(dec)])
    per = [u for _, u in sorted(res)]; tot = float(np.nansum(per))
    pd.DataFrame({'decision_et': dec.values, 'usd': per}).to_csv(os.path.join(HERE, f'tickdata_quote_exact_MNQ_{w}_{sc}.csv'), index=False)
    line = f"EXACT MNQ {w} {sc}: {len(dec)} windows, ${tot:,.2f} ({pd.Timestamp.now():%Y-%m-%d %H:%M})"
    print(line); open(os.path.join(HERE, 'tickdata_quote.txt'), 'a').write(line + "\n")


def nq_decisions():
    """Exact NoiseArea entry-decision times on NQ 2010-2019 (from the research engine's state machine) - entries and reversals."""
    import regime_test as rt
    from research_engine import Market
    m = Market(rt.FILES['NQ'], 20.0, 'NQ'); st = m.state(); out = []
    for x, s in zip(m.days, st):
        if not s.valid: continue
        prev = 0
        for i in range(x.nb):
            p = int(s.mpos[i])
            if p != prev and p != 0 and pd.Timestamp(x.date) <= pd.Timestamp('2019-05-03'):
                out.append(pd.Timestamp(x.date) + pd.Timedelta(minutes=int(x.M[i])))
            prev = p
    return pd.Series(out)


def exact_nq(w='W15', sc='trades'):
    """Exact cost of NQ tick data around EVERY NoiseArea entry decision 2010-2026 (NQ-era decisions from the engine, MNQ-era decision
    times from the reference trade list) - the order-flow test's data, all from NQ (where the flow forms)."""
    a, b = WIN[w]; dec = pd.concat([nq_decisions(), mnq_entries()], ignore_index=True).sort_values()
    jobs = [(i, 'NQ.v.0', sc, utc(d + pd.Timedelta(minutes=a)), utc(d + pd.Timedelta(minutes=b))) for i, d in enumerate(dec)]
    res = dict(run_jobs(jobs)); per = pd.Series([res[i] for i in range(len(dec))], index=dec.values)
    per.to_csv(os.path.join(HERE, f'tickdata_quote_exact_NQ_{w}_{sc}.csv'), header=['usd'])
    by = per.groupby(per.index.year).agg(['size', 'sum'])
    line = (f"EXACT NQ {sc} {w} ({a:+d}..{b:+d} min) around all {len(dec)} NoiseArea entry decisions 2010-2026: ${per.sum():,.2f} "
            f"(2010-2019 ${per[per.index <= pd.Timestamp('2019-05-05')].sum():,.2f}; 2019-2026 ${per[per.index > pd.Timestamp('2019-05-05')].sum():,.2f}); "
            f"by year " + " ".join(f"{y}:{int(r['size'])}/${r['sum']:.2f}" for y, r in by.iterrows()) + f"  ({pd.Timestamp.now():%Y-%m-%d %H:%M})")
    print(line); open(os.path.join(HERE, 'tickdata_quote.txt'), 'a').write(line + "\n")


if __name__ == '__main__':
    if sys.argv[1:2] == ['sample']: sample()
    elif sys.argv[1:2] == ['exact']: exact(sys.argv[2], sys.argv[3])
    elif sys.argv[1:2] == ['exact_nq']: exact_nq(*sys.argv[2:4])
    else: print(__doc__)
