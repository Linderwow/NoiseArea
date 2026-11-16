from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class Line:
    kind: str
    start_bar: int
    start_price: float
    end_bar: int
    end_price: float

    def value(self, bar: int) -> float:
        slope = (self.end_price - self.start_price) / (self.end_bar - self.start_bar)
        return slope * bar - (slope * self.start_bar - self.start_price)


def swings(high: list[float], low: list[float], strength: int) -> tuple[list[float], list[float]]:
    n = len(high)
    sh = [0.0] * n
    sl = [0.0] * n
    const = 2 * strength + 1
    for t in range(n):
        if t + 1 < const:
            continue
        k = t - strength
        c = high[k]
        is_high = all(high[i] < c for i in range(t - const + 1, k)) and all(high[i] <= c for i in range(k + 1, t + 1))
        sh[k] = c if is_high else 0.0
        c = low[k]
        is_low = all(low[i] > c for i in range(t - const + 1, k)) and all(low[i] >= c for i in range(k + 1, t + 1))
        sl[k] = c if is_low else 0.0
    return sh, sl


def swing_bar(swing_series: list[float], t: int, strength: int, look_back: int) -> int:
    for idx in range(t - strength, t - strength - look_back - 1, -1):
        if idx < 0:
            return -1
        if swing_series[idx] == 0.0:
            continue
        return t - idx
    return -1


def trend_lines(bars, strength: int = 5):
    high = [b[1] for b in bars]
    low = [b[2] for b in bars]
    n = len(bars)
    sh, sl = swings(high, low, strength)
    values = [math.nan] * n
    kinds: list[str | None] = [None] * n
    active: list[Line | None] = [None] * n
    last_high_bar, last_low_bar = -1, -1
    last_high_price, last_low_price = -math.inf, math.inf
    high_trend = low_trend = None
    high_active: bool | None = None
    look_back = strength + 1
    for t in range(n):
        shb = swing_bar(sh, t, strength, look_back)
        if shb != -1:
            price = high[t - shb]
            if price < last_high_price and last_high_bar > -1:
                high_trend = Line("high", last_high_bar, last_high_price, t - shb, price)
                high_active = True
            last_high_bar, last_high_price = t - shb, price
        slb = swing_bar(sl, t, strength, look_back)
        if slb != -1:
            price = low[t - slb]
            if price > last_low_price and last_low_bar > -1:
                low_trend = Line("low", last_low_bar, last_low_price, t - slb, price)
                high_active = False
            last_low_bar, last_low_price = t - slb, price
        if high_active is not None:
            line = high_trend if high_active else low_trend
            values[t] = line.value(t)
            kinds[t] = line.kind
            active[t] = line
    return values, kinds, active


def crosses(bars, values):
    out = []
    for t in range(1, len(bars)):
        v0, v1 = values[t], values[t - 1]
        if math.isnan(v0) or math.isnan(v1):
            continue
        c0, c1 = bars[t][3], bars[t - 1][3]
        if c1 <= v1 and c0 > v0:
            out.append((t, "above"))
        elif c1 >= v1 and c0 < v0:
            out.append((t, "below"))
    return out
