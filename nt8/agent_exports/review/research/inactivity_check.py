r"""Audit of INACTIVITY (inactivity.py) along the strategy-validation protocol, 2026-10-30. The claim under test is NOT an edge: it is
"the activity fallback (K=3, V=0) keeps the account trading and costs nothing measurable". Checks:
  1. sample count: with V = 1 (fallback == base rules) the fallback sessions must equal sum(max(0, run - K)) over the base dry runs
  2. synthetic paths: master_inact closes at exactly D dry sessions, not before; fallback switches on at dry >= K and off after a trade
  3. no lookahead: a session's fallback flag depends only on earlier sessions (shuffling later sessions never changes it)
  4. per era, drop the 5 best fallback sessions, and the control (V = 0 on every session / on sessions that are not fallback sessions)
  5. second instrument: MES 2019-2026 (Databento 5-min bars), same walk
    python inactivity_check.py   -> inactivity_check_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import nav_lab as N
import intraday_worst as IW
import inactivity as I

out = []; bad = 0
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))
def ok(cond, what):
    global bad
    pr(('PASS ' if cond else 'FAIL ') + what); bad += (not cond)
def tstat(a): return a.mean() / (a.std(ddof=1) / np.sqrt(len(a))) if len(a) > 2 else float('nan')


def main():
    K, V = 3, 0.0
    f1 = I.series_v(1.0); f0 = I.series_v(V)
    x1, w1, n1 = I.dollars(f1); x0, w0, n0 = I.dollars(f0); idx = f1.index

    # 1. sample count
    # (first written as sum(max(0, run - K)) = 174 and FAILED at 362: the session that ENDS a dry run of >= K is a fallback session
    # too - it is where the fallback takes its trade - so each run counts run - K + 1; 362 = 174 + the 188 runs >= 3. Formula fixed.)
    fb, took = I.walk(n1, n1, K)
    rr1 = I.runs(n1); trailing = int(n1[-1] == 0)                  # a dry run at the very end has no session after it
    expect = int(sum(r - K + 1 for r in rr1 if r >= K)) - (trailing and rr1[-1] >= K)
    ok(int(fb.sum()) == expect, f"sample count: V=1 fallback sessions {int(fb.sum())} == sum(run - {K} + 1) over runs >= {K} {expect}")
    fb0, took0 = I.walk(n1, n0, K)
    ok(1000 > fb0.sum() > 100, f"sample count: K={K} V={V} fallback sessions {int(fb0.sum())} in the expected band 100..1000 (16 years)")

    # 2. synthetic paths
    P = {'dd': 2500, 'dll': 1100, 'reserve': 2600, 'cap': 1500}
    nt = np.array([[1, 0, 0, 0, 0, 0, 0, 1]], float); X = np.zeros_like(nt); one = lambda c: np.ones(c.shape)
    r5 = I.master_inact(X, X, nt, X, X, nt, P, one, None, 5); r6 = I.master_inact(X, X, nt, X, X, nt, P, one, None, 6)
    ok(r5[1][0] == 5 and r5[3][0], f"synthetic: 5 dry sessions (t=1..5) close the account at t=5 with D=5 (closed at {r5[1][0]})")
    ok(r6[1][0] == 6 and r6[3][0], f"synthetic: the same path closes at t=6 with D=6 (closed at {r6[1][0]})")
    ntf = np.array([[1, 0, 0, 0, 1, 0, 0, 0]], float)      # the fallback paths trade on t=4
    r = I.master_inact(X, X, nt, X, X, ntf, P, one, 3, 5)
    ok(list(np.where(r[4][0])[0]) == [4] and not r[3][0], f"synthetic: K=3 switches the fallback on at t=4 only (dry 3), it trades, no closure "
       f"(fallback sessions {list(np.where(r[4][0])[0])})")
    ntf2 = np.array([[1, 0, 0, 0, 0, 1, 0, 0]], float)     # the fallback does not trade on t=4, does on t=5
    r = I.master_inact(X, X, nt, X, X, ntf2, P, one, 3, 5)
    ok(list(np.where(r[4][0])[0]) == [4, 5] and not r[3][0], f"synthetic: an untraded fallback session stays on the next session "
       f"(fallback sessions {list(np.where(r[4][0])[0])}, closed {bool(r[3][0])})")

    # 3. no lookahead
    rng = np.random.default_rng(1); cut = len(n1) // 2
    n_sh = n1.copy(); n_sh[cut:] = rng.permutation(n_sh[cut:]); f_sh = n0.copy(); f_sh[cut:] = rng.permutation(f_sh[cut:])
    fb_sh, _ = I.walk(n_sh, f_sh, K)
    ok(np.array_equal(fb_sh[:cut + 1], fb0[:cut + 1]), "no lookahead: shuffling the sessions after a cut never changes a fallback flag up to it")

    # 4. eras, drop-5, control
    a = x0[fb0]; d = idx[fb0]
    pr(f"\nfallback sessions K={K} V={V}: {len(a)}, mean ${a.mean():.2f} per micro (t {tstat(a):+.2f}), trade on {np.mean(n0[fb0] > 0) * 100:.1f}%")
    for lab, m in (('NQ 2010-07 .. 2019-05', d <= N.JOIN), ('MNQ 2019-05 .. 2026-10', d > N.JOIN)):
        pr(f"   {lab}: {m.sum()} sessions, mean ${a[m].mean():.2f} (t {tstat(a[m]):+.2f})")
        ok(tstat(a[m]) > -2.0, f"era {lab}: fallback sessions not significantly negative")
    yrs = pd.Series(a, index=d).groupby(d.year).agg(['size', 'mean'])
    pr("   per year (sessions, mean $): " + ', '.join(f"{y} {int(r['size'])} {r['mean']:+.0f}" for y, r in yrs.iterrows()))
    srt = np.sort(a); a5 = srt[:-5]
    pr(f"   drop the 5 best: mean ${a5.mean():.2f} (t {tstat(a5):+.2f})")
    ok(tstat(a5) > -2.0, "drop-5: without its 5 best sessions the fallback is still not significantly negative")
    ctl_all = x0 - x1; ctl_nf = (x0 - x1)[~fb0]
    pr(f"   control: V=0 minus the base on EVERY session ${ctl_all.mean():.2f}/session (t {tstat(ctl_all):+.2f}); on non-fallback sessions "
       f"${ctl_nf.mean():.2f} (t {tstat(ctl_nf):+.2f}); V=0 alone every session ${x0.mean():.2f} (t {tstat(x0):+.2f}) vs base ${x1.mean():.2f} (t {tstat(x1):+.2f})")
    rr = I.runs(took0)
    ok(rr.max() <= 4, f"history with the fallback: longest run without a trade {rr.max()} sessions (strict breach needs 5)")

    # 5. second instrument (MES, gross points / open like nav_lab.single, today's MES cost not modelled: sign check only)
    mk = N.market('MES'); p1, _, q1 = IW.day_close_and_worst(mk, vm=1.0); p0, _, q0 = IW.day_close_and_worst(mk, vm=V)
    o = np.array([x.O[0] for x in mk.days]); keep = np.arange(len(o)) >= 30
    g1 = np.nan_to_num(p1[keep]) / o[keep]; g0 = np.nan_to_num(p0[keep]) / o[keep]; q1 = q1[keep]; q0 = q0[keep]
    fbm, tookm = I.walk(q1, q0, K); am = g0[fbm] * 1e4; rm = I.runs(q1); rmf = I.runs(tookm)
    pr(f"\nMES {mk.dates[keep][0].date()} .. {mk.dates[keep][-1].date()}: base no-trade runs >= 5 {np.sum(rm >= 5)}; fallback sessions {fbm.sum()}, "
       f"trade on {np.mean(q0[fbm] > 0) * 100:.1f}%, mean {am.mean():+.2f} bps gross (t {tstat(am):+.2f}); runs >= 5 left {np.sum(rmf >= 5)}, longest {rmf.max()}")
    ok(tstat(am) > -2.0 and rmf.max() <= 4, "second instrument (MES): fallback not significantly negative and no 5-session dry run left")

    pr(f"\n{'ALL PASS' if bad == 0 else f'{bad} FAIL'}")
    with open(os.path.join(HERE, 'inactivity_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')
    return bad


if __name__ == '__main__':
    sys.exit(1 if main() else 0)
