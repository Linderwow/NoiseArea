r"""WEEKEND extra 2: the A2a random-skip control with the skip probability CALIBRATED to A2a's own number of skipped entries (the first
control in weekend_extra.py used A2a's per-entry share, which skips ~37% more entries because a skipped entry is retried at later checks).
    python weekend_extra2.py -> weekend_extra2_output.txt
"""
import os, sys, time
sys.dont_write_bytecode = True
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'): os.environ.setdefault(_v, '1')
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import nav_lab as N, history_extend as HX, navar as NV, metalabel as ML, chval as CV
import weekend as W

out = []
def pr(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); out.append(s)


def main():
    W.below_normal(); t0 = time.time(); mks = {nm: N.market(nm) for nm, _ in ML.PARTS}
    base = W.run_pair({}, mks); rb = W.joined(base)
    a2a = W.run_pair(dict(conf_mode='skip'), mks, with_conf=True); ra = W.joined(a2a); g_a2a = NV.mar(ra) - NV.mar(rb)
    lg = {}; W.run_pair({}, mks, with_conf=True, log=lg); ent = [e for nm in lg for e in lg[nm]['entries']]
    n_skip = sum(1 for e in ent if e[3] is False)

    def one(q, sd):
        rng = np.random.default_rng(5000 + sd); cnt = [0]
        def conf_r(k, i, new):
            if rng.random() < q: cnt[0] += 1; return False
            return None
        res = {}
        for nm, _ in ML.PARTS:
            p, n, ac, ea = W.engine(mks[nm], conf=conf_r, conf_mode='skip'); res[nm] = (mks[nm], p, n, ac, ea)
        return W.joined(res), cnt[0]

    q = n_skip / len(ent)
    for it in range(5):                                           # calibrate q to A2a's number of skipped entries
        cs = [one(q, 900 + j)[1] for j in range(4)]; q *= n_skip / max(np.mean(cs), 1)
    gains = []; sharpes = []; skipped = []
    for sd in range(50):
        rr, c = one(q, sd); gains.append(NV.mar(rr) - NV.mar(rb)); sharpes.append(NV.sharpe(rr)); skipped.append(c)
    gains = np.array(gains)
    pr(f"A2a skips {n_skip:,} of {len(ent):,} entries; calibrated random-skip probability {q:.3f} -> {np.mean(skipped):,.0f} skipped per seed (50 seeds)")
    pr(f"random skipping, matched count: MAR change median {np.median(gains):+.3f}, 95th pct {np.quantile(gains, 0.95):+.3f}, max {gains.max():+.3f}; "
       f"Sharpe median {np.median(sharpes):.2f} (live {NV.sharpe(rb):.2f})")
    pr(f"A2a: MAR change {g_a2a:+.3f}, Sharpe {NV.sharpe(ra):.2f}, ${ra.mean() * 252 * ML.USD:,.0f}/yr vs ${rb.mean() * 252 * ML.USD:,.0f}/yr -> beats "
       f"{np.mean(gains < g_a2a) * 100:.0f}% of matched random-skip seeds: {'more than less exposure' if g_a2a > np.quantile(gains, 0.95) else 'NOT distinguishable from skipping entries at random'}")
    pr(f"{time.time() - t0:,.0f} s")
    open(os.path.join(HERE, 'weekend_extra2_output.txt'), 'w', encoding='utf-8').write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
