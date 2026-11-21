"""Indicator math.

Seeds (all documented and tested):

* Wilder smooth: first value is the arithmetic mean of the first `period`
  observations, placed on the bar of the last observation in that seed.
  Later values are ``(prev * (period - 1) + current) / period``.
* True range, +DM and -DM start on the second bar (they need the prior bar).
  The first DI/ATR(14) therefore lands on index ``period`` (the 15th bar when
  period is 14). The first ADX is the mean of the first `period` DX values,
  on index ``2 * period - 1`` (the 28th bar when period is 14).
* EMA seed is the SMA of the first `period` values. Multiplier is ``2 / (period + 1)``.
* RSI uses the same Wilder seed as DI. Average loss of 0 yields RSI 100,
  including a perfectly flat series.
* Bollinger standard deviation divides by N (population), not N-1.
* ROC is percent: ``(close / close_n_ago - 1) * 100``.
* Ulcer uses ChartSchool's signed percent drawdown from the window max close,
  then ``sqrt(mean(drawdown^2))``.
* PPO line is ``(EMA12 - EMA26) / EMA26 * 100``. Signal is the 9-period EMA of
  the PPO line. Histogram is line minus signal. The 3-day slope is
  ``(hist - hist_3_ago) / 3``.
"""

from __future__ import annotations

import math

from jp_us_screener.version import EQUAL_ABS_TOL, EQUAL_REL_TOL


def almost_equal(a: float, b: float) -> bool:
    """True only inside float64 dust. A 1e-6 relative gap does not pass."""

    return math.isclose(a, b, rel_tol=EQUAL_REL_TOL, abs_tol=EQUAL_ABS_TOL)


def greater(a: float, b: float) -> bool:
    if almost_equal(a, b):
        return False
    return a > b


def less(a: float, b: float) -> bool:
    if almost_equal(a, b):
        return False
    return a < b


def cmp_op(op: str, left: float, right: float) -> bool:
    if op == ">":
        return greater(left, right)
    if op == ">=":
        return greater(left, right) or almost_equal(left, right)
    if op == "<":
        return less(left, right)
    if op == "<=":
        return less(left, right) or almost_equal(left, right)
    if op == "=":
        return almost_equal(left, right)
    if op == "!=":
        return not almost_equal(left, right)
    raise ValueError(f"不支援的比較：{op}")


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    k = 2.0 / (period + 1.0)
    for i in range(period, len(values)):
        prev = out[i - 1]
        assert prev is not None
        out[i] = values[i] * k + prev * (1.0 - k)
    return out


def ema_from_optional(values: list[float | None], period: int) -> list[float | None]:
    """EMA over a series that is None during warmup, then contiguous."""

    out: list[float | None] = [None] * len(values)
    start = next((i for i, value in enumerate(values) if value is not None), None)
    if start is None:
        return out
    segment = []
    for value in values[start:]:
        if value is None:
            break
        segment.append(value)
    smoothed = ema(segment, period)
    for offset, value in enumerate(smoothed):
        out[start + offset] = value
    return out


def roc(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(period, len(values)):
        base = values[i - period]
        if base == 0:
            out[i] = None
        else:
            out[i] = (values[i] - base) / base * 100.0
    return out


def wilder_from(raw: list[float | None], period: int) -> list[float | None]:
    """Wilder smooth. raw[0] is unused; observations begin at index 1."""

    out: list[float | None] = [None] * len(raw)
    if len(raw) <= period:
        return out
    first = raw[1 : period + 1]
    if len(first) < period or any(value is None for value in first):
        return out
    out[period] = sum(first) / period  # type: ignore[arg-type]
    for i in range(period + 1, len(raw)):
        prev = out[i - 1]
        current = raw[i]
        if prev is None or current is None:
            out[i] = None
            continue
        out[i] = (prev * (period - 1) + current) / period
    return out


def wilder_atr(
    high: list[float], low: list[float], close: list[float], period: int
) -> list[float | None]:
    tr: list[float | None] = [None] * len(close)
    for i in range(1, len(close)):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    return wilder_from(tr, period)


def directional_movement(
    high: list[float], low: list[float], close: list[float], period: int
) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None]]:
    n = len(close)
    tr: list[float | None] = [None] * n
    plus_dm: list[float | None] = [None] * n
    minus_dm: list[float | None] = [None] * n
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus_dm[i] = up if up > down and up > 0 else 0.0
        minus_dm[i] = down if down > up and down > 0 else 0.0
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    sm_tr = wilder_from(tr, period)
    sm_plus = wilder_from(plus_dm, period)
    sm_minus = wilder_from(minus_dm, period)
    plus_di: list[float | None] = [None] * n
    minus_di: list[float | None] = [None] * n
    dx: list[float | None] = [None] * n
    for i in range(n):
        if sm_tr[i] is None or sm_plus[i] is None or sm_minus[i] is None or sm_tr[i] == 0:
            continue
        plus_di[i] = 100.0 * sm_plus[i] / sm_tr[i]
        minus_di[i] = 100.0 * sm_minus[i] / sm_tr[i]
        denom = plus_di[i] + minus_di[i]
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(plus_di[i] - minus_di[i]) / denom
    adx: list[float | None] = [None] * n
    seed_end = (period - 1) + period  # index of the last DX in the first ADX seed
    if seed_end < n:
        window = dx[period : seed_end + 1]
        if len(window) == period and all(value is not None for value in window):
            adx[seed_end] = sum(window) / period  # type: ignore[arg-type]
            for i in range(seed_end + 1, n):
                if dx[i] is None or adx[i - 1] is None:
                    break
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period  # type: ignore[operator]
    return plus_di, minus_di, adx, sm_tr


def rsi_wilder(close: list[float], period: int = 14) -> list[float | None]:
    gains: list[float | None] = [None] * len(close)
    losses: list[float | None] = [None] * len(close)
    for i in range(1, len(close)):
        change = close[i] - close[i - 1]
        gains[i] = max(change, 0.0)
        losses[i] = max(-change, 0.0)
    avg_gain = wilder_from(gains, period)
    avg_loss = wilder_from(losses, period)
    out: list[float | None] = [None] * len(close)
    for i in range(len(close)):
        if avg_gain[i] is None or avg_loss[i] is None:
            continue
        if avg_loss[i] == 0:
            out[i] = 100.0
            continue
        rs = avg_gain[i] / avg_loss[i]
        out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def ulcer(close: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(close)
    for i in range(period - 1, len(close)):
        window = close[i - period + 1 : i + 1]
        peak = max(window)
        if peak == 0:
            continue
        total = 0.0
        for value in window:
            drawdown = (value - peak) / peak * 100.0
            total += drawdown * drawdown
        out[i] = math.sqrt(total / period)
    return out


def bollinger(
    close: list[float], period: int = 20, mult: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None]]:
    mid = sma(close, period)
    n = len(close)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    width: list[float | None] = [None] * n
    for i in range(period - 1, n):
        center = mid[i]
        assert center is not None
        window = close[i - period + 1 : i + 1]
        var = sum((value - center) ** 2 for value in window) / period
        sd = math.sqrt(var)
        upper[i] = center + mult * sd
        lower[i] = center - mult * sd
        if center == 0:
            continue
        width[i] = (upper[i] - lower[i]) / center * 100.0  # type: ignore[operator]
    return mid, upper, lower, width


def rolling_prev(values: list[float | None], index: int, window: int, mode: str) -> float | None:
    """Max/min of the previous `window` bars, excluding `index`."""

    start = index - window
    if start < 0 or window <= 0:
        return None
    chunk = values[start:index]
    if len(chunk) != window or any(value is None for value in chunk):
        return None
    numbers = [value for value in chunk if value is not None]
    return max(numbers) if mode == "max" else min(numbers)


def rolling_min_including(values: list[float | None], index: int, window: int) -> float | None:
    start = index - window + 1
    if start < 0:
        return None
    chunk = values[start : index + 1]
    if len(chunk) != window or any(value is None for value in chunk):
        return None
    numbers = [value for value in chunk if value is not None]
    return min(numbers)


def ppo_slope_points(slope: float) -> float:
    """ChartSchool: clamp the 3-day histogram slope to [-1, 1], map to 0..5.

    slope > 1 contributes 5. slope < -1 contributes 0.
    Otherwise ``0.05 * ((slope + 1) * 50)``, which is the same mapping and is
    continuous at the endpoints.
    """

    if slope > 1:
        return 5.0
    if slope < -1:
        return 0.0
    return 0.05 * ((slope + 1.0) * 50.0)
