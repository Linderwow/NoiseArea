#!/usr/bin/env python3
"""
Independent re-implementation of the intraday momentum strategy in

    Zarattini, Aziz & Barbon (2024), "Beat the Market: An Effective Intraday
    Momentum Strategy for S&P500 ETF (SPY)", SSRN 4824172.

Implemented strictly from the paper's Section 3 ("Strategy Description"),
base case: volatility multiplier VM = 1, fixed 1 unit, no leverage, no
costs. Run on 5-minute MNQ bars (timestamps = bar close, US Eastern).

Rules as stated in the paper (quoted in the report that accompanies this
file):

  sigma_{t,HH:MM} = (1/14) * sum_{i=1..14} | Close_{t-i,HH:MM} / Open_{t-i,9:30} - 1 |
  UpperBound_{t,HH:MM} = max(Open_{t,9:30}, Close_{t-1,16:00}) * (1 + sigma_{t,HH:MM})
  LowerBound_{t,HH:MM} = min(Open_{t,9:30}, Close_{t-1,16:00}) * (1 - sigma_{t,HH:MM})

  Checks only at semi-hourly marks HH:00 / HH:30.
  Flat  : price > UpperBound -> long ; price < LowerBound -> short ; else stay flat.
  Long  : stop = max(UpperBound, VWAP); price < stop -> close.
  Short : stop = min(LowerBound, VWAP); price > stop -> close.
  Crossover to the opposite boundary -> close and open the opposite direction.
  All positions closed at the market close.

  VWAP (per the authors' reference [5]): sum(HLC_t * Vol_t) / sum(Vol_t),
  HLC = (H+L+C)/3, regular trading hours only, cumulative from the open.

Execution convention requested by the caller: a decision made at the close of
bar t is filled at the open of bar t+1; end-of-day exits are at the close of
the day's final RTH bar.
"""
import sys
import datetime as dt
import numpy as np
import pandas as pd

HERE = "/tmp/claude-0/-home-claude/85e6a840-90f1-54e7-ba9f-3bace6d5d1f3/scratchpad/reimpl"
BARS = f"{HERE}/mnq_5min_bars.csv"
OUT = f"{HERE}/trades_reimpl.csv"

RTH_FIRST = dt.time(9, 35)      # first RTH bar (covers 09:30-09:35)
RTH_LAST = dt.time(16, 0)       # last RTH bar (covers 15:55-16:00)
FIRST_CHECK = dt.time(10, 0)    # paper: "takes its first position at 10:00"
LOOKBACK = 14                   # paper: "average move over the last 14 days"
MIN_SIGMA_OBS = 7               # ambiguity resolution: min. obs. per time slot
VM = 1.0                        # paper base case: volatility multiplier = 1


def load_days(path):
    df = pd.read_csv(path, parse_dates=["time"])
    df = df.sort_values("time").reset_index(drop=True)
    tod = df["time"].dt.time
    rth = df[(tod >= RTH_FIRST) & (tod <= RTH_LAST)].copy()   # overnight ignored entirely
    rth["date"] = rth["time"].dt.date
    rth["tod"] = rth["time"].dt.time
    days = []
    for d, g in rth.groupby("date", sort=True):
        g = g.reset_index(drop=True)
        # day filter requested by the caller
        if g.loc[0, "tod"] != RTH_FIRST or len(g) < 30:
            continue
        days.append(g)
    return days


def day_moves(g):
    """|Close_{HH:MM} / Open_{9:30} - 1| for every RTH bar of the day, keyed by time-of-day."""
    o = g.loc[0, "open"]
    return pd.Series(np.abs(g["close"].values / o - 1.0), index=g["tod"].values)


def cum_vwap(g):
    hlc = (g["high"] + g["low"] + g["close"]) / 3.0
    pv = (hlc * g["volume"]).cumsum()
    v = g["volume"].cumsum()
    vwap = pv / v
    # fallback if cumulative volume is zero (never happens on MNQ RTH, kept for safety)
    fallback = hlc.expanding().mean()
    return vwap.where(v > 0, fallback).values


def run(days, variant="literal"):
    """
    variant="literal": entry needs only a breach of the Noise Area boundary
                       (the paper's stated entry rule).
    variant="state"  : sensitivity check -- entry additionally requires the
                       price to be beyond the VWAP (i.e. long iff C > max(UB,VWAP)).
    """
    trades = []
    hist = []  # list of per-day move Series for valid days (volatility history)
    prev_close = None
    for g in days:
        n = len(g)
        times = g["time"].values
        opens = g["open"].values
        closes = g["close"].values
        tods = g["tod"].values
        vwap = cum_vwap(g)
        day_open = opens[0]
        day_close = closes[-1]

        if len(hist) >= LOOKBACK and prev_close is not None:
            # sigma per time-of-day from the previous 14 valid days
            H = pd.concat(hist[-LOOKBACK:], axis=1)          # rows: tod, cols: days
            cnt = H.notna().sum(axis=1)
            sigma = H.mean(axis=1, skipna=True)
            sigma[cnt < MIN_SIGMA_OBS] = np.nan
            ref_up = max(day_open, prev_close)
            ref_dn = min(day_open, prev_close)

            pos = 0
            entry_time = entry_price = None

            for i in range(n):
                is_final = (i == n - 1)
                if is_final:
                    if pos != 0:
                        trades.append((entry_time, times[i], pos, entry_price, closes[i]))
                        pos = 0
                    break
                t = tods[i]
                if t.minute not in (0, 30) or t < FIRST_CHECK:
                    continue
                s = sigma.get(t, np.nan)
                if np.isnan(s):
                    continue
                ub = ref_up * (1.0 + VM * s)
                lb = ref_dn * (1.0 - VM * s)
                v = vwap[i]
                c = closes[i]

                new_pos = pos
                # exit (dynamic trailing stop)
                if pos == 1 and c < max(ub, v):
                    new_pos = 0
                elif pos == -1 and c > min(lb, v):
                    new_pos = 0
                # entry (only when flat; no same-direction re-entry on the bar of a stop-out)
                if new_pos == 0:
                    if variant == "literal":
                        go_long = c > ub
                        go_short = c < lb
                    else:
                        go_long = c > max(ub, v)
                        go_short = c < min(lb, v)
                    if go_long and pos != 1:
                        new_pos = 1
                    elif go_short and pos != -1:
                        new_pos = -1

                if new_pos != pos:
                    fill_t, fill_p = times[i + 1], opens[i + 1]
                    if pos != 0:
                        trades.append((entry_time, fill_t, pos, entry_price, fill_p))
                    if new_pos != 0:
                        entry_time, entry_price = fill_t, fill_p
                    pos = new_pos

        hist.append(day_moves(g))
        prev_close = day_close

    out = pd.DataFrame(trades, columns=["entry_time", "exit_time", "dir", "entry_price", "exit_price"])
    out["direction"] = np.where(out["dir"] == 1, "long", "short")
    out["points"] = (out["exit_price"] - out["entry_price"]) * out["dir"]
    return out


def summarize(out, days, label):
    first_day, last_day = days[0].loc[0, "date"], days[-1].loc[0, "date"]
    years = (pd.Timestamp(last_day) - pd.Timestamp(first_day)).days / 365.25
    n = len(out)
    print(f"=== {label} ===")
    print(f"valid trading days in file : {len(days)} ({first_day} .. {last_day}, {years:.2f} years)")
    print(f"first day with signals     : {days[LOOKBACK].loc[0, 'date']}")
    print(f"number of trades           : {n}  (long {int((out['dir']==1).sum())}, short {int((out['dir']==-1).sum())})")
    print(f"gross points               : {out['points'].sum():.2f}")
    print(f"trades per year            : {n / years:.1f}   (trades per valid day: {n / len(days):.2f})")
    print(f"win rate                   : {(out['points'] > 0).mean():.3f}   avg pts/trade: {out['points'].mean():.2f}")


if __name__ == "__main__":
    days = load_days(BARS)
    out = run(days, "literal")
    summarize(out, days, "BASE CASE (paper's literal entry rule)")
    cols = ["entry_time", "exit_time", "direction", "entry_price", "exit_price"]
    out[cols].to_csv(OUT, index=False)
    print(f"wrote {OUT}")
    pd.set_option("display.width", 200)
    print("first 5 trades:")
    print(out[cols + ["points"]].head(5).to_string(index=False))
    print("last 5 trades:")
    print(out[cols + ["points"]].tail(5).to_string(index=False))

    if "--sensitivity" in sys.argv:
        alt = run(days, "state")
        summarize(alt, days, "SENSITIVITY (entry also requires price beyond VWAP) -- not written")
