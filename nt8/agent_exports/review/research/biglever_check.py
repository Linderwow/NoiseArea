r"""Hand-computed paths for the BIGLEVER simulators (BIGLEVER_PLAN.txt), so the payout bookkeeping is checked against arithmetic.
    python biglever_check.py  -> biglever_check_output.txt
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np
import biglever as B

out = []
def pr(*a): print(*a, flush=True); out.append(' '.join(str(x) for x in a))


def path(*days, n=60):
    x = np.zeros(n); x[:len(days)] = days; return x[None, :], np.ones((1, n))


def payouts(fn, X, NT, p, W=None, **kw):
    pay, ou = fn(X, X if W is None else W, NT, p, **kw); idx = np.nonzero(pay[0])[0]
    return [(int(i), round(float(pay[0, i]), 2)) for i in idx], int(ou[0])


def case(name, got, want):
    pr(f"{'PASS' if got == want else 'FAIL'}  {name}: got {got}, expected {want}")


def main():
    n = 60; up = np.full(n, 100.0)
    # FT 25K, +$100 a day: goal $1,500 on day 14 -> $1,000 (you get $900); wait days 15-17, new cycle from day 17's close;
    # +$1,000 of new profit on day 27 -> next; then day 40, and day 53 at the 4th-payout cap of $1,250 (balance 2,400 -> 1,150 > 100)
    case("FT 25K +$100/day payout days", payouts(B.fast_track, up[None, :], np.ones((1, n)), B.FT['25K'])[0],
         [(14, 900.0), (27, 900.0), (40, 900.0), (53, 1125.0)])
    # FT consistency: day 0 +$600 then +$100: the 20% rule needs cycle profit >= $3,000 -> 600 + 100 x 24 on day 24
    x = np.r_[600.0, np.full(n - 1, 100.0)]
    case("FT 25K 20% consistency delays the first payout", payouts(B.fast_track, x[None, :], np.ones((1, n)), B.FT['25K'])[0][:1], [(24, 900.0)])
    # FT breach: -$1,001 on day 0 closes it; +$500 then -$1,001: threshold -500 after day 0, -501 on day 1 closes it
    X, NT = path(-1001.0); case("FT 25K breach day 0", payouts(B.fast_track, X, NT, B.FT['25K'])[1], 0)
    X, NT = path(500.0, -1001.0); case("FT 25K trailing breach day 1", payouts(B.fast_track, X, NT, B.FT['25K'])[1], 1)
    # FT lock: +$1,100 on day 0 locks the threshold at +$100; then -$999 (balance 101) survives, -$2 more closes it
    X, NT = path(1100.0, -999.0, -2.0); case("FT 25K lock at +$100", payouts(B.fast_track, X, NT, B.FT['25K'])[1], 2)
    # MO 25K +$100/day: day 14 balance $1,500 (minimum) but the $1,000 reserve leaves $500 -> $450 to you; new cycle from day 17
    # (balance $1,300); +$1,000 new profit on day 27 -> min($1,000 cap, $1,000 cycle profit, $2,300 - $1,000) = $1,000
    case("MO 25K +$100/day payouts", payouts(B.momentum_master, up[None, :], np.ones((1, n)), B.MO['25K'])[0][:2], [(14, 450.0), (27, 900.0)])
    # MO qualifying days: +$99 a day never makes a qualifying day -> no payout ever
    case("MO 25K $99 days never qualify", payouts(B.momentum_master, np.full((1, n), 99.0), np.ones((1, n)), B.MO['25K'])[0], [])
    # MO daily loss limit $600: a -$900 day counts as -$600 (balance -600, no breach of -1,000)
    X, NT = path(-900.0, -399.0); case("MO 25K DLL caps the day at -$600", payouts(B.momentum_master, X, NT, B.MO['25K'])[1], n)
    # Evaluation, 25K qualification: -500 x 4 breaches on day 3 (DLL caps each day at -500; -2,000 < -1,500); with resets the
    # balance restarts and +$100 a day reaches $1,500 on day 18 (15 days after the reset)
    x = np.r_[-500.0, -500.0, -500.0, -500.0, np.full(17, 100.0)][None, :]
    ok0, r0 = B.evaluation(x, x, 1500, 500, 1500); ok1, r1 = B.evaluation(x, x, 1500, 500, 1500, resets=True)
    case("evaluation without reset", (bool(ok0[0]), float(r0[0])), (False, 0.0))
    case("evaluation with reset", (bool(ok1[0]), float(r1[0])), (True, 1.0))
    # no reset when fewer than 5 sessions remain: breach on day 17 (3 left) is final
    x = np.r_[np.zeros(14), -500.0, -500.0, -500.0, -500.0, np.full(3, 900.0)][None, :]
    ok2, r2 = B.evaluation(x, x, 1500, 500, 1500, resets=True); case("no reset with 3 sessions left", (bool(ok2[0]), float(r2[0])), (False, 0.0))
    # INTRADAY (amendment 1). FT 25K: a day that closes +$500 but touches -$1,001 closes the account that day
    X, NT = path(500.0); W, _ = path(-1001.0)
    case("FT 25K intraday breach on a winning day", payouts(B.fast_track, X, NT, B.FT['25K'], W=W)[1], 0)
    # MO 25K: close +300 but worst -650 hits the $600 DLL -> the day books -600; then -399 (balance -999) survives; -2 more closes
    # it on day 2. On closes alone the same path ends at -101 and stays open.
    X, NT = path(300.0, -399.0, -2.0); W, _ = path(-650.0, -399.0, -2.0)
    case("MO 25K intraday DLL books -$600", payouts(B.momentum_master, X, NT, B.MO['25K'], W=W)[1], 2)
    case("MO 25K same path on closes", payouts(B.momentum_master, X, NT, B.MO['25K'])[1], n)
    # Evaluation 25K: +$100 closes every day pass on closes (day 14); with a -$600 worst point each day the $500 DLL books -500
    # a day and the account breaches on day 3 (-1,500 + -500 < -1,500)
    x = np.full((1, 21), 100.0); w = np.full((1, 21), -600.0)
    case("evaluation +$100 closes", bool(B.evaluation(x, x, 1500, 500, 1500)[0][0]), True)
    case("evaluation same closes, -$600 worst points", bool(B.evaluation(x, w, 1500, 500, 1500)[0][0]), False)
    with open(os.path.join(HERE, 'biglever_check_output.txt'), 'w', encoding='utf-8') as f: f.write('\n'.join(out) + '\n')


if __name__ == '__main__':
    main()
