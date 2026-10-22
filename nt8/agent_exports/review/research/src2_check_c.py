r"""Independent re-computation of the batch-C overnight passes (C15 overnight long above SMA200, C05 volatility-managed overnight long)
from the 1-minute caches with plain loops, compared day by day with src2_test; plus a descriptive stress test of C15.
    python src2_check_c.py
"""
import os, sys, statistics
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_lib as L, src2_check as K


def main():
    D = K.panel(); seg = list(D.seg); j = seg.index(1); sh = D.aC[j - 1] - D.aO[j]
    cl = [x + (sh if s else 0) for x, s in zip(D.aC, seg)]; N = len(cl); out = []

    def night(sig):
        r = [0.0] * N
        for k in range(N - 1):
            if sig[k] and seg[k] == seg[k + 1]: r[k + 1] += sig[k] * (D.aO[k + 1] - D.aC[k]) / D.O[k + 1] - 2 * abs(sig[k]) * L.side_cost(1)
        return pd.Series(r, index=pd.DatetimeIndex(D.date))

    sma = [sum(cl[k - 199:k + 1]) / 200 if k >= 199 else float('nan') for k in range(N)]
    c15 = night([1.0 if cl[k] > sma[k] else 0.0 for k in range(N)])
    ret = [float('nan')] + [(cl[k] - cl[k - 1]) / D.O[k] for k in range(1, N)]
    rv = [statistics.stdev(ret[k - 19:k + 1]) if k >= 20 else float('nan') for k in range(N)]
    med, hist = [], []
    for k in range(N):
        if not np.isnan(rv[k]): hist.append(rv[k])
        med.append(float(np.median(hist)) if len(hist) >= 250 else float('nan'))
    c05 = night([min(2.0, med[k] / rv[k]) if not (np.isnan(med[k]) or np.isnan(rv[k]) or rv[k] == 0) else 0.0 for k in range(N)])

    import src2_test as T
    T.load_batches(); T.P = L.Panel()
    for sid, mine in (('C15', c15), ('C05', c05)):
        g, n = T.run(sid); ref = g - n * L.side_cost(1); c = ref.index.intersection(mine.index); d = np.abs(ref.reindex(c).values - mine.reindex(c).values)
        out.append(f"  {sid}: common days {len(c)}, max |daily difference| {d.max():.2e}, days differing {(d > 1e-9).sum()}, t {L.tstat(ref.values):+.2f} vs {L.tstat(mine.values):+.2f}")
    g, n = T.run('C15'); gw, nw = T.run('W06')
    for nm, (gg, nn) in (('C15 overnight above SMA200', (g, n)), ('W06 overnight every night', (gw, nw))):
        for tk in (0, 1, 2, 3):
            net = gg - nn * ((0.5 * tk + 0.5) / L.N_USD)
            out.append(f"  {nm}: {tk} tick(s)/side ${net.sum() * L.N_USD / (len(net) / 252):+,.0f}/yr t {L.tstat(net.values):+.2f}")
    net = g - n * L.side_cost(1); usd = net * L.N_USD
    out.append("  C15 by year: " + " ".join(f"{y}:{v:+,.0f}" for y, v in usd.groupby(usd.index.year).sum().items()))
    p23 = net[net.index >= '2023-01-01']
    out.append(f"  C15: held {100 * (n > 0).mean():.0f}% of nights; 2023-2026 ${p23.sum() * L.N_USD / (len(p23) / 252):+,.0f}/yr t {L.tstat(p23.values):+.2f}; "
               f"worst drawdown ${(usd.cumsum().cummax() - usd.cumsum()).max():,.0f} per micro")
    na = T.noisearea(); cm = na.index.intersection(net.index)
    out.append(f"  C15 daily correlation with NoiseArea {np.corrcoef(na.reindex(cm), net.reindex(cm))[0, 1]:+.2f}")
    off = (gw - nw * L.side_cost(1)) - net; o = off[off != 0]
    out.append(f"  nights the SMA200 filter skips: {len(o)}, ${off.sum() * L.N_USD / (len(off) / 252):+,.0f}/yr, mean {o.mean() * 1e4:+.1f} bp, t {L.tstat(o.values):+.2f}")
    txt = "=== batch-C overnight passes re-computed independently + C15 stress test ===\n" + "\n".join(out); print(txt)
    open(os.path.join(HERE, 'src2_check_c_output.txt'), 'w').write(txt + "\n")


if __name__ == '__main__':
    main()
