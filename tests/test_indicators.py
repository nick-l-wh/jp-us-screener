import math

from jp_us_screener.features import compute_features
from jp_us_screener.indicators import (
    almost_equal,
    bollinger,
    cmp_op,
    directional_movement,
    ema,
    greater,
    ppo_slope_points,
    roc,
    rolling_prev,
    rsi_wilder,
    sma,
    ulcer,
)
from jp_us_screener.models import Bar
from tests.support import bars_from_closes, weekdays


def test_wilder_di_and_adx_seed_period_3():
    high = [10, 12, 11, 13, 12, 14, 13, 15]
    low = [8, 9, 9, 10, 10, 11, 12, 13]
    close = [9, 11, 10, 12, 11, 13, 12, 14]
    plus, minus, adx, atr = directional_movement(high, low, close, 3)
    assert plus[0] is None and adx[2] is None
    assert math.isclose(plus[3], 50.0)
    assert minus[3] == 0
    assert math.isclose(atr[3], 8 / 3)
    assert adx[4] is None
    assert math.isclose(adx[5], 100.0)


def test_rsi_flat_is_100_and_roc_is_percent():
    flat = [10.0] * 20
    rsi = rsi_wilder(flat, 14)
    assert rsi[13] is None
    assert rsi[14] == 100.0
    values = roc([10, 10, 12, 15], 2)
    assert values[2] == 20
    assert values[3] == 50


def test_ema_uses_sma_seed():
    values = [1, 2, 3, 4, 5]
    out = ema(values, 3)
    assert out[1] is None
    assert out[2] == 2
    k = 2 / 4
    assert math.isclose(out[3], 4 * k + 2 * (1 - k))


def test_sma_and_population_bollinger_width():
    values = [1, 2, 3, 4, 5]
    assert sma(values, 3)[2] == 2
    mid, upper, lower, width = bollinger([1, 2, 3, 4, 6], 5, 2)
    center = 16 / 5
    var = sum((value - center) ** 2 for value in [1, 2, 3, 4, 6]) / 5
    assert math.isclose(mid[4], center)
    assert math.isclose(upper[4] - lower[4], 4 * math.sqrt(var))
    assert math.isclose(width[4], (upper[4] - lower[4]) / center * 100)


def test_ulcer_matches_squared_drawdown():
    closes = [10, 9, 8, 10]
    value = ulcer(closes, 4)[-1]
    peak = 10
    drawdowns = [((price - peak) / peak * 100) ** 2 for price in closes]
    assert math.isclose(value, math.sqrt(sum(drawdowns) / 4))


def test_ppo_slope_points_are_clamped_not_a_second_formula():
    assert ppo_slope_points(2) == 5
    assert ppo_slope_points(-2) == 0
    assert ppo_slope_points(0) == 2.5
    assert ppo_slope_points(1) == 5
    assert ppo_slope_points(-1) == 0


def test_previous_25_excludes_today_and_is_not_the_number_25():
    series = [float(i) for i in range(30)]
    # Today is 29. Previous 25 bars are 4..28, max 28. The number 25 is irrelevant.
    assert rolling_prev(series, 29, 25, "max") == 28
    assert rolling_prev(series, 29, 25, "min") == 4
    series[29] = 1000
    assert rolling_prev(series, 29, 25, "max") == 28


def test_equality_tolerance_is_float_dust_only():
    assert almost_equal(1.0, 1.0 + 1e-15)
    assert not almost_equal(1.0, 1.0 + 1e-6)
    assert not greater(10.0, 10.0)
    assert not cmp_op(">", 10.0, 10.0 + 1e-15)
    assert cmp_op(">", 10.0 + 1e-4, 10.0)
    assert cmp_op("<", 18.0, 25.0)


def test_no_lookahead_when_future_bar_is_appended():
    closes = [40 + i * 0.4 for i in range(80)]
    bars = bars_from_closes(closes, symbol="SYN-LOOK")
    before = compute_features(bars)
    future = Bar(
        "US",
        "SYN-LOOK",
        weekdays(81)[-1],
        1,
        80,
        1,
        5,
        9_000_000,
        "split_and_dividend",
    )
    after = compute_features(bars + [future])
    for left, right in zip(before, after):
        assert left.plus_di14 == right.plus_di14
        assert left.prev25_max_plus_di == right.prev25_max_plus_di
        assert left.prev25_min_plus_di == right.prev25_min_plus_di
        assert left.bb_width == right.bb_width
        assert left.bb_width_min63 == right.bb_width_min63
        assert left.turtle_prior_low == right.turtle_prior_low
        assert left.low_is_min20 == right.low_is_min20
        assert left.adx14 == right.adx14


def test_changing_today_does_not_change_previous_di_window():
    closes = [40 + i * 0.5 for i in range(70)]
    bars = bars_from_closes(closes, symbol="SYN-TODAY")
    changed = list(bars)
    last = changed[-1]
    changed[-1] = Bar(
        last.market,
        last.symbol,
        last.date,
        last.open,
        last.high + 30,
        last.low,
        last.close + 20,
        last.volume,
        last.adjustment,
    )
    before = compute_features(bars)
    after = compute_features(changed)
    assert before[-1].prev25_max_plus_di == after[-1].prev25_max_plus_di
    assert before[-1].plus_di14 != after[-1].plus_di14


def test_gap_uses_trading_bars_not_filled_calendar_days():
    from jp_us_screener.models import Bar as B

    dates = ["2023-01-02", "2023-01-03", "2023-01-09"]
    bars = [
        B("US", "SYN-GAP", dates[0], 10, 11, 9, 10, 1000, "split_and_dividend"),
        B("US", "SYN-GAP", dates[1], 10, 11, 9, 12, 1000, "split_and_dividend"),
        B("US", "SYN-GAP", dates[2], 10, 11, 9, 15, 1000, "split_and_dividend"),
    ]
    features = compute_features(bars)
    assert [row.date for row in features] == dates
    assert len(features) == 3
    assert features[-1].roc3 is None
    one = (15 - 12) / 12 * 100
    assert math.isclose(roc([10, 12, 15], 1)[-1], one)
