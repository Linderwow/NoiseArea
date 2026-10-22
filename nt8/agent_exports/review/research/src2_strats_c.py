r"""SOURCES2 batch C (stage 3 - the adaptive search round, SOURCES2_PLAN.txt 'BATCH C'): more overnight-return and FOMC-calendar rules
from the second scout round, steered by stage 1's family scores. Registered into src2_test.STRATS on import.
"""
import os, sys
sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
import src2_test as T
import src2_lib as L
from src2_test import strat, intraday, daily_hold, overnight, at
from src2_strats_a import J, sma, cache, holidays, third_friday, fomc_k, series_on, vix, w04, same_seg


def n_sess(): return len(T.P.daily)


def night_into(mask_next):
    """Long the night INTO each session k with mask_next[k] True (close of k-1 -> open of k)."""
    m = np.asarray(mask_next, bool); sig = np.zeros(n_sess()); sig[:-1] = m[1:]; return overnight(sig)


# ------------------------------------------------------------------------------------------------ overnight family
@strat('C01', 'overnight', 'Bondarenko-Muravyev 2023 JFQA: the 4 hours around the European open (01:00-05:00 ET)', '2022-01-01', a=60, b=300)
def c01(a=60, b=300): return w04(a=a, b=b, after_down=False)


@strat('C02', 'overnight', 'Turn-of-month nights (McConnell-Xu 2008; Lakonishok-Smidt 1988): nights into days -1..+3', '2008-01-01')
def c02():
    d = T.P.dates; n = len(d); m = np.zeros(n, bool)
    for k in range(1, n):
        if d[k].month != d[k - 1].month:
            m[k - 1] = True
            for j in range(k, min(k + 3, n)): m[j] = True
    return night_into(m)


@strat('C03', 'overnight', 'FOMC-cycle even-week nights (Cieslak et al. 2019), the FOMC-eve night excluded', '2019-01-01')
def c03():
    n = n_sess(); fk = fomc_k(); even = np.zeros(n, bool)
    for a_, f in enumerate(fk):
        nxt = fk[a_ + 1] if a_ + 1 < len(fk) else n + 1
        for k in range(f, min(nxt - 1, n)):                                   # day 0 onward (the night into day 0 is the FOMC eve)
            if ((k - f + 1) // 5) % 2 == 0: even[k] = True
    return night_into(even)


@strat('C04', 'overnight spread', 'Hendershott-Livdan-Rosch 2020 JFE: long NQ / short ES overnight, dollar-matched (ES 2010-19, MES 2019-26)',
       '2020-01-01')
def c04():
    def es_tab():
        out = {}
        for nm in ('es', 'mes'):
            mk = L.Mins(nm)
            for d, (i0, i1) in mk.days.items(): out[(nm, d)] = (mk.ao[i0], mk.ac[i1], mk.o[i0], mk.iid[i0])
        return out
    E = cache('es_tab', es_tab); D = T.P.daily; aO, aC, px, seg = D.aO.values, D.aC.values, D.px.values, D.seg.values; res = []
    for k in range(len(D) - 1):
        if seg[k] != seg[k + 1] or T.FILT: continue
        nm = 'es' if seg[k] == 0 else 'mes'; a, b = E.get((nm, D.date.iat[k])), E.get((nm, D.date.iat[k + 1]))
        if a is None or b is None or a[3] != b[3]: continue
        r = (aO[k + 1] - aC[k]) / px[k + 1] - (b[0] - a[1]) / b[2]
        res.append((k + 1, r, 4))
    if T.FILT: return None
    return L.series(T.P, res)


@strat('C05', 'overnight', 'Moreira-Muir volatility-managed overnight long: size = min(2, median RV20 / RV20)', '2017-01-01', n=20, cap=2.0)
def c05(n=20, cap=2.0):
    j = J(); r = pd.Series(np.r_[np.nan, np.diff(j['c'])] / j['px']); rv = r.rolling(n).std(); med = rv.expanding(min_periods=250).median()
    return overnight(np.nan_to_num(np.clip((med / rv).values, 0, cap)))


@strat('C06', 'overnight', 'Overnight long only when VIX is above its 252-day median (Bondarenko-Muravyev / Hu et al. rationale)', '2022-01-01', look=252)
def c06(look=252):
    v = vix().CLOSE; hi = series_on(pd.DataFrame({'x': (v > v.rolling(look).median()).astype(float)}), 'x')
    sig = np.zeros(n_sess()); sig[:-1] = hi[1:] == 1                               # VIX known before the next session (close of the eve)
    return overnight(sig)


@strat('C07', 'overnight', 'Overnight long only after a VIX stretch (VIX >= 5% above its 10-day SMA)', '2022-01-01', look=10, st=0.05)
def c07(look=10, st=0.05):
    v = vix().CLOSE; s = series_on(pd.DataFrame({'x': (v >= (1 + st) * v.rolling(look).mean()).astype(float)}), 'x')
    sig = np.zeros(n_sess()); sig[:-1] = s[1:] == 1; return overnight(sig)


@strat('C08', 'overnight', 'IBS overnight: long the night after a session closing in the bottom 20% of its range (Pagonidis)', '2014-01-01', thr=0.2)
def c08(thr=0.2):
    D = T.P.daily; ibs = (D.C - D.L) / (D.H - D.L).where(D.H > D.L); return overnight((ibs < thr).values.astype(float))


@strat('C09', 'overnight', 'Fade top-decile trend days overnight (Baltussen et al. reversal): short the night', '2021-01-01', q=0.9, look=252)
def c09(q=0.9, look=252):
    D = T.P.daily; r = np.full(len(D), np.nan)
    for k, (d, mk) in enumerate(T.P.sess):
        i0, i1 = mk.days[d]; j = at(mk, i0, i1, mk.m[i1] + 1 - 30)
        if j is not None and same_seg(k): r[k] = (mk.ac[j - 1] - D.aC.iat[k - 1]) / D.px.iat[k]
    thr = pd.Series(r).rolling(look, min_periods=120).quantile(q).shift(1).values
    return overnight(np.where(r >= thr, -1.0, 0.0))


@strat('C10', 'overnight', 'Option-expiration-week nights (Stivers-Sun 2013)', '2013-01-01')
def c10():
    d = T.P.dates; m = [third_friday(x.year, x.month) - pd.Timedelta(days=4) <= x <= third_friday(x.year, x.month) for x in d]
    return night_into(m)


@strat('C11', 'overnight', 'Overnight persistence: long only if the last 20 nights summed > 0 (Lou-Polk-Skouras style, index level)', '2019-01-01', look=20)
def c11(look=20):
    D = T.P.daily; on = np.r_[np.nan, (D.aO.values[1:] - D.aC.values[:-1]) / D.px.values[1:]]; on[np.r_[False, D.seg.values[1:] != D.seg.values[:-1]]] = np.nan
    s = pd.Series(on).rolling(look, min_periods=look).sum().values; return overnight(np.where(s > 0, 1.0, 0.0))


@strat('C12', 'overnight', 'Overnight long only if the 12-month return is positive', '2012-01-01', look=252)
def c12(look=252):
    j = J(); c = j['c']; return overnight(np.r_[np.zeros(look), (c[look:] > c[:-look]).astype(float)])


@strat('C13', 'overnight', 'Quarter-end mark-up reversal: short the night after the last session of a quarter (Carhart et al. 2002)', '2002-01-01')
def c13():
    d = T.P.dates; sig = np.zeros(len(d))
    for k in range(len(d) - 1):
        if d[k + 1].month != d[k].month and d[k].month in (3, 6, 9, 12): sig[k] = -1
    return overnight(sig)


@strat('C14', 'overnight', 'Weekday nights only: overnight long except over weekends (Rogalski 1984; Dyl-Maberly 1986)', '1986-01-01')
def c14():
    d = T.P.dates; sig = np.zeros(len(d))
    for k in range(len(d) - 1): sig[k] = 1.0 if (d[k + 1] - d[k]).days == 1 else 0.0
    return overnight(sig)


@strat('C15', 'overnight', 'Overnight long only above the 200-day SMA', '2007-01-01', n=200)
def c15(n=200):
    j = J(); return overnight(np.where(j['c'] > sma(j['c'], n), 1.0, 0.0))


@strat('C16', 'overnight', 'Pre-holiday nights: the night into the last session before a market holiday (Ariel 1990)', '1990-01-01')
def c16():
    hol = holidays(); m = []
    for dd in T.P.dates:
        nd = dd + pd.Timedelta(days=1)
        while nd.weekday() >= 5: nd += pd.Timedelta(days=1)
        m.append(nd in hol)
    return night_into(m)


@strat('C17', 'overnight', 'High-attention nights: long after top-decile relative volume or absolute return (Berkman et al. 2012, index version)',
       '2012-01-01', q=0.9, look=252)
def c17(q=0.9, look=252):
    D = T.P.daily; rv = (D.V / D.V.rolling(20).mean().shift(1)).values; ar = np.abs(np.r_[np.nan, np.diff(D.aC.values) / D.px.values[1:]])
    t1 = pd.Series(rv).rolling(look, min_periods=120).quantile(q).shift(1).values; t2 = pd.Series(ar).rolling(look, min_periods=120).quantile(q).shift(1).values
    return overnight(((rv >= t1) | (ar >= t2)).astype(float))


@strat('C18', 'overnight', 'Payday night: close on or before the 15th -> next open (Ma-Pratt)', '2018-10-01')
def c18():
    d = T.P.dates; sig = np.zeros(len(d))
    for k in range(len(d) - 1):
        if d[k].day <= 15 and (d[k + 1].day > 15 or d[k + 1].month != d[k].month): sig[k] = 1
    return overnight(sig)


# ------------------------------------------------------------------------------------------------ FOMC-calendar family
def _fomc_eve_series(cond):
    """FOMC-eve overnight long (close of the eve -> 09:30 on the FOMC day) only when cond(f) is True for the FOMC session index f."""
    sig = np.zeros(n_sess())
    for f in fomc_k():
        if f >= 1 and cond(f): sig[f - 1] = 1
    return overnight(sig)


@strat('C19', 'FOMC cycle', 'Neuhierl-Weber monetary momentum: sign of D-25..D-1 return, held D-1 close -> D+15 close', '2018-06-01', pre=25, post=15)
def c19(pre=25, post=15):
    j = J(); c = j['c']; n = n_sess(); pos = np.zeros(n); seg = T.P.daily.seg.values
    for f in fomc_k():
        if f - 1 - pre < 0 or seg[f - 1 - pre] != seg[f]: continue
        s = np.sign(c[f - 1] - c[f - 1 - pre])
        for k in range(f - 1, min(f + post, n - 1)): pos[k] = s                 # held from close f-1 to close f+post
    return daily_hold(pos)


def _press_conf(d):
    """FOMC meetings with a press conference: 2011 Apr/Jun/Nov, 2012 Jan/Apr/Jun/Sep/Dec, 2013-2018 quarterly, every meeting from 2019."""
    return d.year >= 2019 or (2013 <= d.year <= 2018 and d.month in (3, 6, 9, 12)) or (d.year == 2011 and d.month in (4, 6, 11)) or         (d.year == 2012 and d.month in (1, 4, 6, 9, 12))


@strat('C20', 'FOMC intraday', 'Gomez-Cram-Grotteria 2022 JFE: statement move 14:00-14:30 continues through the press conference (14:30-15:30)',
       '2022-01-01')
def c20():
    fs = {T.P.dates[f] for f in fomc_k()}
    def f(k, mk, i0, i1, D):
        d = D.date.iat[k]
        if d not in fs or d.year < 2013 or not _press_conf(d): return
        a, b, e = at(mk, i0, i1, 840), at(mk, i0, i1, 870), at(mk, i0, i1, 929)
        if None in (a, b, e): return
        s = np.sign(mk.c[b - 1] - mk.c[a - 1])
        if s: return [(s, mk.o[b], mk.c[e], 2, 870)]
    return intraday(f)


@strat('C21', 'pre-FOMC', 'Hu et al. 2022: FOMC-eve long only after a VIX build-up (VIX up over the prior 5 sessions, known before the eve)',
       '2019-04-01', look=5)
def c21(look=5):
    v = vix().CLOSE; up = series_on(pd.DataFrame({'x': (v > v.shift(look)).astype(float)}), 'x')
    return _fomc_eve_series(lambda f: up[f - 1] == 1)


@strat('C22', 'pre-FOMC', 'Martello-Ribeiro: FOMC-eve long only when the variance risk premium is above its 252-day median', '2018-11-01', look=252)
def c22(look=252):
    v = vix().CLOSE; iv = (v / 100) ** 2 * 21 / 252; ivs = series_on(pd.DataFrame({'x': iv}), 'x')
    j = J(); r = pd.Series(np.r_[np.nan, np.diff(j['c'])] / j['px']); rv = (r ** 2).rolling(21).sum().shift(1).values   # realized to the prior close
    vrp = ivs - rv; med = pd.Series(vrp).rolling(look, min_periods=120).median().values
    return _fomc_eve_series(lambda f: vrp[f - 1] > med[f - 1])


@strat('C23', 'pre-FOMC', 'Lucca-Moench conditioning: FOMC-eve long only if the last 8 FOMC-eve returns averaged > 0', '2015-01-01', look=8)
def c23(look=8):
    D = T.P.daily; fk = fomc_k(); past = {}
    for i, f in enumerate(fk):
        if f >= 1 and D.seg.iat[f] == D.seg.iat[f - 1]: past[f] = (D.aO.iat[f] - D.aC.iat[f - 1]) / D.px.iat[f]
    def cond(f):
        prev = [past[g] for g in fk if g < f and g in past][-look:]
        return len(prev) == look and np.mean(prev) > 0
    return _fomc_eve_series(cond)


@strat('C25', 'post-FOMC', 'Post-FOMC drift: sign of the 14:00-16:00 FOMC-day reaction held from the close to D+5 (Neuhierl-Weber; Golez et al.)',
       '2018-06-01', hold=5)
def c25(hold=5):
    n = n_sess(); pos = np.zeros(n); D = T.P.daily
    for f in fomc_k():
        d, mk = T.P.sess[f]; i0, i1 = mk.days[d]; a = at(mk, i0, i1, 840)
        if a is None or f + hold >= n or D.seg.iat[f + hold] != D.seg.iat[f]: continue
        s = np.sign(mk.c[i1] - mk.c[a - 1])
        for k in range(f, f + hold): pos[k] = s
    return daily_hold(pos)


@strat('C26', 'FOMC spread', 'Bodilsen et al. 2021: long NQ / short ES over press-conference FOMC days (close D-1 -> close D)', '2021-01-01')
def c26():
    if T.FILT: return None
    def es_tab():
        out = {}
        for nm in ('es', 'mes'):
            mk = L.Mins(nm)
            for d, (i0, i1) in mk.days.items(): out[(nm, d)] = (mk.ac[i1], mk.o[i0], mk.iid[i0])
        return out
    E = cache('es_tab2', es_tab); D = T.P.daily; res = []
    for f in fomc_k():
        d = T.P.dates[f]
        if f < 1 or not _press_conf(d) or D.seg.iat[f] != D.seg.iat[f - 1]: continue
        nm = 'es' if D.seg.iat[f] == 0 else 'mes'; a, b = E.get((nm, T.P.dates[f - 1])), E.get((nm, d))
        if a is None or b is None or a[2] != b[2]: continue
        res.append((f, (D.aC.iat[f] - D.aC.iat[f - 1]) / D.px.iat[f] - (b[0] - a[0]) / b[1], 4))
    return L.series(T.P, res)


@strat('C27', 'pre-FOMC', 'Han 2025 JME contrarian: FOMC day long if D-10..D-1 was down, short if up (close D-1 -> close D)', '2025-01-01', look=10)
def c27(look=10):
    j = J(); c = j['c']; pos = np.zeros(n_sess()); seg = T.P.daily.seg.values
    for f in fomc_k():
        if f - 1 - look >= 0 and seg[f - 1 - look] == seg[f]: pos[f - 1] = -np.sign(c[f - 1] - c[f - 1 - look])
    return daily_hold(pos)


@strat('C29', 'FOMC cycle', 'Cieslak et al. odd-week short: short over days D+4..8, D+14..18, D+24..28', '2019-01-01')
def c29():
    n = n_sess(); m = np.zeros(n, bool)
    for f in fomc_k():
        for a, b in ((4, 8), (14, 18), (24, 28)):
            for k in range(f + a, min(f + b + 1, n)): m[k] = True
    pos = np.zeros(n); pos[:-1] = -m[1:].astype(float); return daily_hold(pos)
