"""Per-bar features. Every window ends at the bar being computed."""

from __future__ import annotations

from jp_us_screener.indicators import (
    bollinger,
    directional_movement,
    ema,
    ppo_slope_points,
    roc,
    rolling_min_including,
    rolling_prev,
    rsi_wilder,
    sma,
    ulcer,
    wilder_atr,
)
from jp_us_screener.models import Bar, Feature
from jp_us_screener.sctr import score_from_parts


def compute_features(bars: list[Bar]) -> list[Feature]:
    if not bars:
        return []
    high = [bar.high for bar in bars]
    low = [bar.low for bar in bars]
    close = [bar.close for bar in bars]
    volume = [bar.volume for bar in bars]
    plus_di, minus_di, adx, atr14 = directional_movement(high, low, close, 14)
    atr10 = wilder_atr(high, low, close, 10)
    roc3 = roc(close, 3)
    roc20 = roc(close, 20)
    roc90 = roc(close, 90)
    roc125 = roc(close, 125)
    sma3_v = sma(volume, 3)
    sma20_v = sma(volume, 20)
    sma50_v = sma(volume, 50)
    sma200 = sma(close, 200)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)
    ema200 = ema(close, 200)
    ema12 = ema(close, 12)
    ema26 = ema(close, 26)
    rsi = rsi_wilder(close, 14)
    ulcer10 = ulcer(close, 10)
    bb_mid, bb_upper, bb_lower, bb_width = bollinger(close, 20, 2.0)

    ppo: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        if ema12[i] is None or ema26[i] in (None, 0):
            continue
        ppo[i] = (ema12[i] - ema26[i]) / ema26[i] * 100.0  # type: ignore[operator]
    signal = _ema_optional(ppo, 9)
    hist: list[float | None] = [None] * len(bars)
    slope: list[float | None] = [None] * len(bars)
    points: list[float | None] = [None] * len(bars)
    for i in range(len(bars)):
        if ppo[i] is None or signal[i] is None:
            continue
        hist[i] = ppo[i] - signal[i]
    for i in range(len(bars)):
        if hist[i] is None or i < 3 or hist[i - 3] is None:
            continue
        slope[i] = (hist[i] - hist[i - 3]) / 3.0  # type: ignore[operator]
        points[i] = ppo_slope_points(slope[i])  # type: ignore[arg-type]

    rows: list[Feature] = []
    for i, bar in enumerate(bars):
        yday_min = None
        low_is_min = None
        prior = None
        if i >= 19:
            window = low[i - 19 : i + 1]
            low_is_min = low[i] == min(window)
        if i >= 20:
            yday_window = low[i - 20 : i]
            yday_min = min(yday_window)
            prior = any(low[i - k] == yday_min for k in range(3, 11))
        feature = Feature(
            index=i,
            date=bar.date,
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            plus_di14=plus_di[i],
            minus_di14=minus_di[i],
            adx14=adx[i],
            atr10=atr10[i],
            atr14=atr14[i],
            roc3=roc3[i],
            roc20=roc20[i],
            roc90=roc90[i],
            roc125=roc125[i],
            sma3_volume=sma3_v[i],
            sma20_volume=sma20_v[i],
            sma50_volume=sma50_v[i],
            sma200_close=sma200[i],
            sma200_close_lag20=sma200[i - 20] if i >= 20 else None,
            ema20=ema20[i],
            ema50=ema50[i],
            ema200=ema200[i],
            rsi14=rsi[i],
            ppo=ppo[i],
            ppo_signal=signal[i],
            ppo_hist=hist[i],
            ppo_hist_slope3=slope[i],
            ppo_slope_points=points[i],
            ulcer10=ulcer10[i],
            bb_upper=bb_upper[i],
            bb_lower=bb_lower[i],
            bb_mid=bb_mid[i],
            bb_width=bb_width[i],
            bb_width_min63=rolling_min_including(bb_width, i, 63),
            kc_upper=(ema20[i] + 1.5 * atr10[i]) if ema20[i] is not None and atr10[i] is not None else None,
            kc_lower=(ema20[i] - 1.5 * atr10[i]) if ema20[i] is not None and atr10[i] is not None else None,
            prev25_max_plus_di=rolling_prev(plus_di, i, 25, "max"),
            prev25_min_plus_di=rolling_prev(plus_di, i, 25, "min"),
            low_is_min20=low_is_min,
            turtle_prior_low=prior,
            yday_min20_low=yday_min,
        )
        total, parts = score_from_parts(feature)
        feature.technical_score = total
        feature.score_parts = parts
        rows.append(feature)
    return rows


def _ema_optional(values: list[float | None], period: int) -> list[float | None]:
    from jp_us_screener.indicators import ema_from_optional

    return ema_from_optional(values, period)
