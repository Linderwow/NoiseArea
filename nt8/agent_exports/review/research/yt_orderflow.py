from __future__ import annotations

import argparse
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tape_seconds as tape
import yt_rule_fit as fit

NT_TICKS = Path(r"D:\nt_ticks\MNQ")
CACHE = HERE / "regime_work" / "orderflow"
ET = "America/New_York"
LARGE = 5
EPS = timedelta(microseconds=1)
FEATURES = ["vol", "n_trades", "avg_size", "max_size", "large_share", "delta", "delta_frac", "at_side_share",
            "vol_last15_share", "n_last15_share", "delta_last15", "delta_last15_frac", "move_last10",
            "delta_pre5", "delta_pre", "delta_pre_frac", "vol_ratio_tick", "delta_z", "cumdelta_pre_slope",
            "open_gap", "move_next5", "delta_next5", "spread_close", "imb_close", "vol_ratio20"]
SIGNED = {"delta", "delta_frac", "delta_last15", "delta_last15_frac", "move_last10", "delta_pre5", "delta_pre",
          "delta_pre_frac", "delta_z", "cumdelta_pre_slope", "open_gap", "move_next5", "delta_next5", "imb_close", "at_side_share"}


def build_sample(rows: list[dict], N: int) -> list[dict]:
    signals, flats = fit.signal_and_flat(rows)
    out = []
    for day in sorted(set(signals) | set(flats)):
        bars = fit.day_bars(day)
        if bars is None:
            continue
        C = bars["close"].to_numpy(dtype=float)
        V = bars["volume"].to_numpy(dtype=float) if "volume" in bars else np.full(len(C), np.nan)
        Vavg = np.array([V[max(0, t - 20):t].mean() if t > 0 else np.nan for t in range(len(V))])
        labels = [t.strftime("%H:%M") for t in bars.index]
        idx = {lab: i for i, lab in enumerate(labels)}

        def brk(i, side):
            return (side == 1 and C[i] > C[i - N:i].max()) or (side == -1 and C[i] < C[i - N:i].min())

        for lab, side in signals.get(day, {}).items():
            i = idx.get(lab)
            if i is None or i < N + 1:
                continue
            fired, prev = brk(i, side), brk(i - 1, side)
            j = i if fired else (i - 1 if prev else i)
            out.append({"day": day, "bar": labels[j], "side": side, "taken": 1, "fired": int(fired or prev),
                        "vol_ratio20": V[j] / Vavg[j] if Vavg[j] and not np.isnan(Vavg[j]) else np.nan})
        for lab, armed in flats.get(day, {}).items():
            i = idx.get(lab)
            if i is None or i < N or not brk(i, armed):
                continue
            out.append({"day": day, "bar": lab, "side": armed, "taken": 0, "fired": 1,
                        "vol_ratio20": V[i] / Vavg[i] if Vavg[i] and not np.isnan(Vavg[i]) else np.nan})
    return out


def bar_time(day: str, lab: str) -> datetime:
    return datetime.strptime(f"{day} {lab}", "%Y-%m-%d %H:%M")


def nt_session_of(t: datetime) -> str:
    d = t.date() + (timedelta(days=1) if t.hour >= 18 else timedelta(0))
    return d.strftime("%Y-%m-%d")


def nt_trades(session: str) -> pd.DataFrame | None:
    files = list(NT_TICKS.glob(f"*/{session}.parquet"))
    if not files:
        return None
    d = pd.read_parquet(files[-1], columns=["time", "price", "volume", "side", "bid", "ask"])
    if not len(d):
        return None
    t = pd.DatetimeIndex(d["time"]).tz_convert(ET).tz_localize(None)
    df = pd.DataFrame({"price": d["price"].to_numpy(float), "size": d["volume"].to_numpy(float),
                       "agg": np.where(d["side"].astype(str).to_numpy() == "A", 1.0, -1.0),
                       "bid": d["bid"].to_numpy(float), "ask": d["ask"].to_numpy(float)}, index=t)
    return df.sort_index()


def recorder_windows(utc_date: str, windows: list[tuple[datetime, datetime]]) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    path = tape.TAPE / f"{utc_date}_tape.csv"
    if not path.exists() or not windows:
        return None, None
    import pyarrow as pa
    import pyarrow.csv as pacsv
    starts = np.array(sorted(np.datetime64(a, "us") for a, _ in windows))
    ends = np.array([e for _, e in sorted((np.datetime64(a, "us"), np.datetime64(b, "us")) for a, b in windows)])
    reader = pacsv.open_csv(str(path), read_options=pacsv.ReadOptions(block_size=64 << 20),
                            parse_options=pacsv.ParseOptions(invalid_row_handler=lambda row: "skip"),
                            convert_options=pacsv.ConvertOptions(include_columns=["kind", "feed_utc", "price", "size", "bid", "ask"],
                                                                 column_types={"feed_utc": pa.timestamp("us"), "kind": pa.string()}))
    kept = []
    for batch in reader:
        t = batch.column("feed_utc").to_numpy(zero_copy_only=False)
        k = np.searchsorted(starts, t, side="right") - 1
        inwin = (k >= 0) & (t <= ends[np.clip(k, 0, len(ends) - 1)])
        if inwin.any():
            kept.append(batch.to_pandas()[inwin])
    if not kept:
        return None, None
    d = pd.concat(kept)
    d.index = pd.DatetimeIndex(d["feed_utc"]).tz_localize("UTC").tz_convert(ET).tz_localize(None)
    d = d.sort_index()
    tr = d[d["kind"] == "T"]
    agg = np.where(tr["price"] >= tr["ask"], 1.0, np.where(tr["price"] <= tr["bid"], -1.0, np.nan))
    tick = np.sign(tr["price"].diff().to_numpy())
    tick[tick == 0] = np.nan
    agg = pd.Series(np.where(np.isnan(agg), tick, agg), index=tr.index).ffill().fillna(1.0)
    trades = pd.DataFrame({"price": tr["price"].to_numpy(float), "size": tr["size"].to_numpy(float), "agg": agg.to_numpy(),
                           "bid": tr["bid"].to_numpy(float), "ask": tr["ask"].to_numpy(float)}, index=tr.index)
    q = d[d["kind"] != "T"]
    quotes = pd.DataFrame({"bid": np.where(q["kind"] == "B", q["price"], np.nan), "bidsz": np.where(q["kind"] == "B", q["size"], np.nan),
                           "ask": np.where(q["kind"] == "A", q["price"], np.nan), "asksz": np.where(q["kind"] == "A", q["size"], np.nan)},
                          index=q.index).ffill()
    return trades, quotes


def features(tr: pd.DataFrame, qt: pd.DataFrame | None, T: datetime, s: int, minutes: int) -> dict | None:
    t0 = T - timedelta(seconds=60)
    bar = tr.loc[t0:T - EPS]
    if len(bar) < 5:
        return None
    size, agg, price = bar["size"].to_numpy(), bar["agg"].to_numpy(), bar["price"].to_numpy()
    vol = size.sum()
    n = len(bar)
    delta = (size * agg).sum() * s
    f = {"vol": vol, "n_trades": n, "avg_size": vol / n, "max_size": size.max(), "large_share": size[size >= LARGE].sum() / vol,
         "delta": delta, "delta_frac": delta / vol, "at_side_share": size[agg == s].sum() / vol}
    last = bar.loc[T - timedelta(seconds=15):]
    f["vol_last15_share"] = last["size"].sum() / vol
    f["n_last15_share"] = len(last) / n
    f["delta_last15"] = (last["size"] * last["agg"]).sum() * s
    f["delta_last15_frac"] = f["delta_last15"] / last["size"].sum() if last["size"].sum() else np.nan
    before10 = tr.loc[:T - timedelta(seconds=10) - EPS]
    f["move_last10"] = (price[-1] - before10["price"].iloc[-1]) * s if len(before10) else np.nan
    pre = tr.loc[T - timedelta(minutes=minutes + 1): t0 - EPS]
    if len(pre) >= 5:
        g = pre.groupby(pre.index.floor("min"))
        pv = g["size"].sum()
        pdlt = (pre["size"] * pre["agg"]).groupby(pre.index.floor("min")).sum() * s
        f["delta_pre"] = pdlt.sum()
        f["delta_pre_frac"] = pdlt.sum() / pv.sum()
        f["vol_ratio_tick"] = vol / pv.mean()
        f["delta_z"] = (delta - pdlt.mean()) / pdlt.std() if len(pdlt) > 2 and pdlt.std() > 0 else np.nan
        cum = pdlt.cumsum().to_numpy()
        f["cumdelta_pre_slope"] = np.polyfit(np.arange(len(cum)), cum, 1)[0] if len(cum) > 2 else np.nan
        pre5 = tr.loc[T - timedelta(minutes=6): t0 - EPS]
        f["delta_pre5"] = (pre5["size"] * pre5["agg"]).sum() * s
    else:
        for k in ("delta_pre", "delta_pre_frac", "vol_ratio_tick", "delta_z", "cumdelta_pre_slope", "delta_pre5"):
            f[k] = np.nan
    nxt = tr.loc[T: T + timedelta(seconds=5) - EPS]
    if len(nxt):
        f["open_gap"] = (nxt["price"].iloc[0] - price[-1]) * s
        f["move_next5"] = (nxt["price"].iloc[-1] - price[-1]) * s
        f["delta_next5"] = (nxt["size"] * nxt["agg"]).sum() * s
    else:
        f["open_gap"] = f["move_next5"] = f["delta_next5"] = np.nan
    if qt is not None and len(qt):
        q = qt.loc[:T - EPS]
        if len(q):
            last_q = q.iloc[-1]
            f["spread_close"] = (last_q["ask"] - last_q["bid"]) / 0.25
            tot = last_q["bidsz"] + last_q["asksz"]
            f["imb_close"] = (last_q["bidsz"] - last_q["asksz"]) / tot * s if tot and not np.isnan(tot) else np.nan
        else:
            f["spread_close"] = f["imb_close"] = np.nan
    else:
        f["spread_close"] = (bar["ask"].iloc[-1] - bar["bid"].iloc[-1]) / 0.25
        f["imb_close"] = np.nan
    return f


def compute_day(day: str, items: list[dict], minutes: int) -> tuple[dict[tuple[str, int], dict], dict]:
    out: dict[tuple[str, int], dict] = {}
    src = {"nt": 0, "recorder": 0, "none": 0}
    sessions: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        sessions[nt_session_of(bar_time(day, it["bar"]))].append(it)
    pending = []
    for session, its in sessions.items():
        tr = nt_trades(session) if day <= "2026-10-29" else None
        for it in its:
            T = bar_time(day, it["bar"])
            f = features(tr, None, T, it["side"], minutes) if tr is not None else None
            if f is None:
                pending.append(it)
            else:
                f["source"] = "nt"
                out[(it["bar"], it["side"])] = f
                src["nt"] += 1
        del tr
    if pending:
        windows: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
        for it in pending:
            T = bar_time(day, it["bar"])
            a = pd.Timestamp(T - timedelta(minutes=minutes + 1)).tz_localize(ET).tz_convert("UTC").tz_localize(None).to_pydatetime()
            b = pd.Timestamp(T + timedelta(minutes=1)).tz_localize(ET).tz_convert("UTC").tz_localize(None).to_pydatetime()
            for ud in {a.strftime("%Y-%m-%d"), b.strftime("%Y-%m-%d")}:
                windows[ud].append((a, b))
        trs, qts = [], []
        for ud, w in windows.items():
            tr, qt = recorder_windows(ud, w)
            if tr is not None:
                trs.append(tr)
            if qt is not None:
                qts.append(qt)
        tr = pd.concat(trs).sort_index() if trs else None
        qt = pd.concat(qts).sort_index() if qts else None
        if tr is not None:
            tr = tr[~tr.index.duplicated(keep="last")] if tr.index.has_duplicates and False else tr
        for it in pending:
            T = bar_time(day, it["bar"])
            f = features(tr, qt, T, it["side"], minutes) if tr is not None else None
            if f is None:
                src["none"] += 1
                continue
            f["source"] = "recorder"
            out[(it["bar"], it["side"])] = f
            src["recorder"] += 1
    return out, src


def load_features(sample: list[dict], minutes: int, recompute: bool) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list[dict]] = defaultdict(list)
    for it in sample:
        by_day[it["day"]].append(it)
    frames = []
    cov = {"nt": 0, "recorder": 0, "none": 0, "days_nt": 0, "days_rec": 0, "days_none": 0}
    for day in sorted(by_day):
        cache = CACHE / f"{day}_m{minutes}.parquet"
        have = pd.read_parquet(cache) if cache.exists() and not recompute else pd.DataFrame()
        keyed = {(r["bar"], int(r["side"])): r for r in have.to_dict("records")} if len(have) else {}
        missing = [it for it in by_day[day] if (it["bar"], it["side"]) not in keyed]
        if missing:
            new, src = compute_day(day, missing, minutes)
            for (bar, side), f in new.items():
                keyed[(bar, side)] = {"bar": bar, "side": side, **f}
            for (bar, side) in {(it["bar"], it["side"]) for it in missing} - set(new):
                keyed[(bar, side)] = {"bar": bar, "side": side, "source": "none"}
            pd.DataFrame(list(keyed.values())).to_parquet(cache)
        rows = []
        for it in by_day[day]:
            f = keyed.get((it["bar"], it["side"]), {"source": "none"})
            rows.append({**it, **{k: v for k, v in f.items() if k not in ("bar", "side")}})
        df = pd.DataFrame(rows)
        frames.append(df)
        srcs = df["source"].value_counts().to_dict() if "source" in df else {}
        for k in ("nt", "recorder", "none"):
            cov[k] += srcs.get(k, 0)
        cov["days_nt" if srcs.get("nt") else ("days_rec" if srcs.get("recorder") else "days_none")] += 1
    df = pd.concat(frames, ignore_index=True)
    for k in FEATURES:
        if k not in df:
            df[k] = np.nan
    return df, cov


def auc_of(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    from scipy.stats import mannwhitneyu
    a, b = a[~np.isnan(a)], b[~np.isnan(b)]
    if len(a) < 5 or len(b) < 5:
        return np.nan, np.nan
    u = mannwhitneyu(a, b, alternative="two-sided")
    return u.statistic / (len(a) * len(b)), u.pvalue


def cv_models(X: np.ndarray, y: np.ndarray, names: list[str]) -> list[str]:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import RepeatedStratifiedKFold, cross_validate
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.tree import DecisionTreeClassifier
    from sklearn.ensemble import RandomForestClassifier
    base = max(y.mean(), 1 - y.mean())
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=5, random_state=0)
    lines = [f"base rate (always say the majority class): {base:.1%}   n={len(y)} (taken {int(y.sum())}, let go {int(len(y) - y.sum())})"]
    models = [("logistic (all features, standardized)", make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                                                     LogisticRegression(C=0.5, max_iter=2000))),
              ("tree depth 2", make_pipeline(SimpleImputer(strategy="median"), DecisionTreeClassifier(max_depth=2, min_samples_leaf=10, random_state=0))),
              ("random forest depth 3, 200 trees", make_pipeline(SimpleImputer(strategy="median"),
                                                                 RandomForestClassifier(n_estimators=200, max_depth=3, min_samples_leaf=5, random_state=0, n_jobs=1)))]
    for label, m in models:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = cross_validate(m, X, y, cv=cv, scoring=("accuracy", "roc_auc"), n_jobs=1)
        lines.append(f"{label:<40} CV accuracy {r['test_accuracy'].mean():.1%} (+/- {r['test_accuracy'].std():.1%})  vs base {base:.1%};  "
                     f"CV AUC {r['test_roc_auc'].mean():.3f}")
    m = models[0][1].fit(X, y)
    coef = m[-1].coef_[0]
    top = sorted(zip(np.abs(coef), coef, names), reverse=True)[:5]
    lines.append("largest logistic weights (standardized): " + ", ".join(f"{n} {c:+.2f}" for _, c, n in top))
    vol_fam = ["vol_ratio20", "vol_ratio_tick", "vol", "n_trades", "max_size", "avg_size", "large_share"]
    delta_fam = ["delta_frac", "at_side_share", "delta_z", "delta_last15_frac", "delta_pre_frac", "cumdelta_pre_slope", "imb_close", "move_last10"]
    groups = [("bar volume only (vol_ratio20, on the chart)", ["vol_ratio20"]), ("volume family (ratios, trades, lot sizes)", vol_fam),
              ("delta family only (no volume levels)", delta_fam), ("volume family + delta family", vol_fam + delta_fam)]
    lines.append("ablation, logistic CV:")
    for label, cols in groups:
        ix = [names.index(c) for c in cols]
        mm = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.5, max_iter=2000))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            r = cross_validate(mm, X[:, ix], y, cv=cv, scoring=("accuracy", "roc_auc"), n_jobs=1)
        lines.append(f"  {label:<50} AUC {r['test_roc_auc'].mean():.3f}  accuracy {r['test_accuracy'].mean():.1%}")
    return lines


def rule_lines(df: pd.DataFrame) -> list[str]:
    y = df["taken"].to_numpy()
    base = max(y.mean(), 1 - y.mean())
    rules = [("signal-bar delta has the breakout's sign", df["delta"] > 0),
             ("signal-bar delta share > +20 %", df["delta_frac"] > 0.2),
             ("last-15-s delta has the breakout's sign", df["delta_last15"] > 0),
             ("bar volume > 1.5 x prior-20 average (minute bars)", df["vol_ratio20"] > 1.5),
             ("bar volume > 1.5 x prior bars (ticks)", df["vol_ratio_tick"] > 1.5),
             ("large lots (>= 5) > 30 % of bar volume", df["large_share"] > 0.3),
             ("delta z-score vs prior bars > 1", df["delta_z"] > 1),
             ("prior-bars cumulative delta leans the breakout's way", df["delta_pre"] > 0),
             ("last-10-s move with the breakout (> 0)", df["move_last10"] > 0),
             ("next bar's first 5 s move with the breakout (> 0)  [after the fill]", df["move_next5"] > 0),
             ("next bar opens beyond the close (gap with the breakout)  [after the fill]", df["open_gap"] > 0)]
    lines = [f"{'rule':<72} {'fires':>5} {'prec':>6} {'recall':>6} {'acc':>6}  (base {base:.1%})"]
    for name, mask in rules:
        m = mask.fillna(False).to_numpy()
        if m.sum() == 0:
            continue
        prec = y[m].mean()
        rec = (y[m].sum()) / max(y.sum(), 1)
        acc = ((m == (y == 1)).mean())
        lines.append(f"{name:<72} {int(m.sum()):>5} {prec:>6.1%} {rec:>6.1%} {acc:>6.1%}")
    return lines


def report(df: pd.DataFrame, title: str) -> None:
    d = df[(df["source"] != "none") & (df["fired"] == 1)].copy()
    y = d["taken"].to_numpy().astype(int)
    print(f"\n==== {title}: {len(d)} breakout bars with ticks  (taken {int(y.sum())}, let go {int(len(y) - y.sum())}; "
          f"{(d['source'] == 'nt').sum()} from NT's store, {(d['source'] == 'recorder').sum()} from the recorder)")
    if y.sum() < 10 or (len(y) - y.sum()) < 10:
        print("too few on one side for statistics")
        return
    print(f"{'feature (signed = in the breakout direction)':<46} {'AUC':>5} {'p':>7}  {'taken med':>10} {'let-go med':>10}  direction")
    rows = []
    for k in FEATURES:
        a, b = d.loc[d["taken"] == 1, k].to_numpy(float), d.loc[d["taken"] == 0, k].to_numpy(float)
        auc, p = auc_of(a, b)
        if np.isnan(auc):
            continue
        rows.append((abs(auc - 0.5), k, auc, p, np.nanmedian(a), np.nanmedian(b)))
    rows.sort(reverse=True)
    for _, k, auc, p, ma, mb in rows:
        direction = "taken higher" if auc > 0.5 else "taken lower"
        flag = " *" if p < 0.01 else (" ." if p < 0.05 else "")
        print(f"{k + (' (s)' if k in SIGNED else ''):<46} {auc:>5.2f} {p:>7.3f}  {ma:>10.3g} {mb:>10.3g}  {direction}{flag}")
    print("  (* p < 0.01, . p < 0.05 - with 25 features, 1-2 dots are expected by chance)")
    X = d[FEATURES].to_numpy(float)
    print()
    for line in cv_models(X, y, FEATURES):
        print("  " + line)
    print()
    for line in rule_lines(d):
        print("  " + line)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="", help="only ledger days >= this ET date")
    ap.add_argument("--minutes", type=int, default=15, help="bars before the signal bar used for the 'pre' features")
    ap.add_argument("--n", type=int, default=8, help="trigger: close breaks the prior N closes")
    ap.add_argument("--recompute", action="store_true")
    args = ap.parse_args()
    rows = fit.load_trades()
    sample = build_sample(rows, args.n)
    if args.since:
        sample = [s for s in sample if s["day"] >= args.since]
    days = sorted({s["day"] for s in sample})
    print(f"sample (as yt_rule_diag, N={args.n}): {len(sample)} bars on {len(days)} days {days[0]}..{days[-1]}: "
          f"taken {sum(s['taken'] for s in sample)} (of which the trigger fired on {sum(s['taken'] * s['fired'] for s in sample)}), "
          f"let go {sum(1 - s['taken'] for s in sample)}")
    df, cov = load_features(sample, args.minutes, args.recompute)
    print(f"ticks found for {cov['nt'] + cov['recorder']} of {len(df)} bars: {cov['nt']} from NinjaTrader's store (sessions to 2026-10-29), "
          f"{cov['recorder']} from the recorder (2026-10-22+), {cov['none']} none; days: {cov['days_nt']} NT, {cov['days_rec']} recorder, {cov['days_none']} without ticks")
    report(df, "ALL DAYS WITH TICKS")
    recent = df[df["day"] >= "2026-10-01"]
    if len(recent) and len(recent) < len(df):
        report(recent, "OCTOBER-NOVEMBER ONLY (his current setup)")


if __name__ == "__main__":
    main()
