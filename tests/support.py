from __future__ import annotations

from datetime import date, timedelta

from jp_us_screener.config import EngineConfig
from jp_us_screener.models import Bar, Security


def weekdays(count: int, start: date = date(2023, 1, 2)) -> list[str]:
    found: list[str] = []
    cursor = start
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return found


def security(**overrides) -> Security:
    payload = dict(
        market="US",
        symbol="SYN-T",
        name="合成",
        security_type="stock",
        listing_segment="primary",
        exchange="XNAS",
        currency="USD",
        cohort="large",
        market_cap=20_000_000_000,
        market_cap_currency="USD",
        market_cap_as_of="2024-01-02",
        group="",
    )
    payload.update(overrides)
    return Security(**payload)


def bars_from_closes(
    closes: list[float],
    *,
    market: str = "US",
    symbol: str = "SYN-T",
    volume: float = 1_000_000,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    start: date = date(2023, 1, 2),
) -> list[Bar]:
    dates = weekdays(len(closes), start)
    rows = []
    for index, close in enumerate(closes):
        high = highs[index] if highs is not None else close * 1.01
        low = lows[index] if lows is not None else close * 0.99
        open_ = min(high, max(low, close * 0.995))
        rows.append(
            Bar(market, symbol, dates[index], open_, high, low, close, volume, "split_and_dividend")
        )
    return rows


def uptrend(count: int = 220, start: float = 40.0, step: float = 1.003) -> list[float]:
    price = start
    closes = []
    for _ in range(count):
        price *= step
        closes.append(price)
    return closes


def small_config(**overrides) -> EngineConfig:
    payload = dict(min_cohort_size=overrides.pop("min_cohort_size", 2))
    return EngineConfig(**payload)
