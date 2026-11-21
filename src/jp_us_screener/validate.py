"""Bar checks. A bad symbol is diagnosed; it does not delete anyone else's snapshot."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from jp_us_screener.config import EngineConfig
from jp_us_screener.models import Bar


def validate_bars(bars: list[Bar], config: EngineConfig) -> list[str]:
    errors: list[str] = []
    if not bars:
        return ["沒有行情列"]
    seen: set[str] = set()
    previous: date | None = None
    adjustments = {bar.adjustment for bar in bars}
    if len(adjustments) != 1:
        errors.append("同一代號的 adjustment 不一致")
    required = config.adjustment_required
    if adjustments != {required}:
        errors.append(f"調整政策必須是 {required}，實際是 {sorted(adjustments)}")
    for bar in bars:
        try:
            current = date.fromisoformat(bar.date)
        except ValueError:
            errors.append(f"{bar.date} 不是 YYYY-MM-DD")
            continue
        if current.weekday() >= 5:
            errors.append(f"{bar.date} 是星期六或日；引擎不接受週末柱，也不會自行補假期")
        if bar.date in seen:
            errors.append(f"重複日期 {bar.date}")
        seen.add(bar.date)
        if previous is not None and current <= previous:
            errors.append(f"{bar.date} 不是嚴格遞增")
        previous = current
        errors.extend(_ohlc_errors(bar))
        if bar.volume < 0 or not _finite(bar.volume):
            errors.append(f"{bar.date} 成交量無效")
    return errors


def _ohlc_errors(bar: Bar) -> list[str]:
    errors = []
    prices = {"open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close}
    for name, value in prices.items():
        if not _finite(value) or value <= 0:
            errors.append(f"{bar.date} {name} 必須是正數")
    if errors:
        return errors
    high = Decimal(str(bar.high))
    low = Decimal(str(bar.low))
    # Values already passed through float, so compare the floats that will be used.
    if bar.high < bar.low:
        errors.append(f"{bar.date} high < low")
    if bar.high < max(bar.open, bar.close):
        errors.append(f"{bar.date} high 低於 open 或 close")
    if bar.low > min(bar.open, bar.close):
        errors.append(f"{bar.date} low 高於 open 或 close")
    del high, low
    return errors


def _finite(value: float) -> bool:
    return value == value and value not in {float("inf"), float("-inf")}
