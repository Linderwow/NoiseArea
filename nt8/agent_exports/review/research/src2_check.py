r"""Independent re-implementation of the SOURCES2 stage-1 passes (W33 Williams %R(5), W45 VIX-conditioned FOMC eve) straight from the
1-minute caches with plain loops (own session building, own indicators), compared day by day with src2_test; plus a descriptive
comparison of W45 with the plain FOMC-eve long (no VIX filter).
    python src2_check.py
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L


def sessions(name, lo, hi):
    """Own RTH session table from the cache: date -> raw O/H/L/C, adjusted O/H/L/C, 09:30 open, index of the last bar."""
    z = np.load(L.CACHE(name)); t = pd.DatetimeIndex(z['t']); m = t.hour * 60 + t.minute
    df = pd.DataFrame({'d': t.normalize(), 'm': m, 'o': z['o'], 'h': z['h'], 'l': z['l'], 'c': z['c'], 'iid': z['iid'], 'sh': z['shift'],
                       'i': np.arange(len(t))})
    df = df[(df.m >= 570) & (df.m < 960) & (df.d >= lo) & (df.d <= hi)]
    rows = []
    for d, g in df.groupby('d', sort=True):
        if g.m.iloc[0] != 570 or len(g) < 201 or g.iid.iloc[0] != g.iid.iloc[-1]: continue
        sh = g.sh.iloc[0]
        rows.append(dict(date=d, O=g.o.iloc[0], H=g.h.max(), L=g.l.min(), C=g.c.iloc[-1], aO=g.o.iloc[0] + sh, aH=g.h.max() + sh, aL=g.l.min() + sh,
                         aC=g.c.iloc[-1] + sh, iid=g.iid.iloc[0]))
    return pd.DataFrame(rows)


def panel():
    a = sessions('nq', pd.Timestamp(L.DISC[0]), L.JOIN); a['seg'] = 0
    b = sessions('mnqx', L.JOIN + pd.Timedelta(days=1), pd.Timestamp('2030-01-01')); b['seg'] = 1
    return pd.concat([a, b], ignore_index=True)


def w33_loop(D, n=5, lvl=10.0):
    cl, hi, lo = list(D.aC), list(D.aH), list(D.aL); seg = list(D.seg)
    j = seg.index(1); shift = D.aC[j - 1] - D.aO[j]                           # join the MNQ part onto NQ's last close for indicators
    cl = [x + (shift if s else 0) for x, s in zip(cl, seg)]; hi = [x + (shift if s else 0) for x, s in zip(hi, seg)]; lo = [x + (shift if s else 0) for x, s in zip(lo, seg)]
    N = len(cl); pos = [0] * N; p = 0
    for k in range(N):
        if k >= n - 1:
            hh, ll = max(hi[k - n + 1:k + 1]), min(lo[k - n + 1:k + 1])
            wr = (hh - cl[k]) / (hh - ll) * -100 if hh > ll else float('nan')
        else: wr = float('nan')
        if p == 0 and wr < -100 + lvl: p = 1
        elif p == 1 and k > 0 and cl[k] > hi[k - 1]: p = 0
        pos[k] = p
    for k in range(N - 1):
        if seg[k] != seg[k + 1]: pos[k] = 0                                  # never across the join
    r = [0.0] * N; prev = 0
    for k in range(N - 1):
        if pos[k] != prev: r[k] -= abs(pos[k] - prev) * L.side_cost(1)
        if pos[k]: r[k + 1] += (D.aC[k + 1] - D.aC[k]) / D.O[k + 1]
        prev = pos[k]
    if prev: r[N - 1] -= L.side_cost(1)
    return pd.Series(r, index=pd.DatetimeIndex(D.date))


def fomc_eve_loop(D, vix_filter=True, look=252):
    fomc = {pd.Timestamp(s.strip()) for s in open(os.path.join(HERE, '..', '..', '..', 'bin', 'Custom', 'Strategies', 'fomc_dates.txt'))
            if s.strip() and not s.startswith('#')}
    v = pd.read_csv(os.path.join(HERE, 'vix_history_cboe.csv')); v.index = pd.to_datetime(v.DATE, format='%m/%d/%Y'); vc = v.CLOSE
    dates = list(pd.DatetimeIndex(D.date)); r = [0.0] * len(dates)
    for f in range(1, len(dates)):
        if dates[f] not in fomc or D.seg[f] != D.seg[f - 1]: continue
        if vix_filter:
            prior = vc[vc.index < dates[f - 1]]                                # VIX known before the eve session
            if len(prior) < look or not prior.iloc[-1] > prior.iloc[-look:].median(): continue
        r[f] += (D.aO[f] - D.aC[f - 1]) / D.O[f] - 2 * L.side_cost(1)
    return pd.Series(r, index=pd.DatetimeIndex(dates))


def main():
    import src2_test as T
    T.load_batches(); T.P = L.Panel(); D = panel()
    print(f"own session table: {len(D)} sessions vs src2 panel {len(T.P.daily)}")
    out = []
    for sid, mine in (('W33', w33_loop(D)), ('W45', fomc_eve_loop(D))):
        g, n = T.run(sid); ref = g - n * L.side_cost(1); c = ref.index.intersection(mine.index)
        d = np.abs(ref.reindex(c).values - mine.reindex(c).values)
        usd = lambda s: s.sum() * L.N_USD / (len(s) / 252)
        out.append(f"  {sid}: common days {len(c)}, max |daily difference| {d.max():.2e}, days differing {(d > 1e-12).sum()}, "
                   f"${usd(ref.reindex(c)):+,.0f}/yr vs ${usd(mine.reindex(c)):+,.0f}/yr, t {L.tstat(ref.reindex(c).values):+.2f} vs {L.tstat(mine.reindex(c).values):+.2f}")
    plain = fomc_eve_loop(D, vix_filter=False); w45 = fomc_eve_loop(D)
    for nm, s in (('FOMC eve, plain', plain), ('FOMC eve, VIX > median (W45)', w45)):
        tr = s[s != 0]; nq, mn = s[s.index <= L.JOIN], s[s.index > L.JOIN]
        out.append(f"  {nm:30s}: {len(tr)} nights, mean {tr.mean() * 1e4:+.1f} bp, ${s.sum() * L.N_USD / (len(s) / 252):+,.0f}/yr, t {L.tstat(s.values):+.2f}"
                   f" (NQ {L.tstat(nq.values):+.2f}, MNQ {L.tstat(mn.values):+.2f})")
    off = plain - w45; tr = off[off != 0]
    out.append(f"  nights the filter removes: {len(tr)}, mean {tr.mean() * 1e4:+.1f} bp, t {L.tstat(tr.values):+.2f} (over those nights)")
    txt = "=== SOURCES2 stage-1 passes re-computed independently ===\n" + "\n".join(out); print(txt)
    open(os.path.join(HERE, 'src2_check_output.txt'), 'w').write(txt + "\n")


if __name__ == '__main__':
    main()
