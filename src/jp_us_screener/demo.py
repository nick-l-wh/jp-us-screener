"""Deterministic synthetic fixtures. These are not market prices."""

from __future__ import annotations

from datetime import date, timedelta

from jp_us_screener.config import EngineConfig
from jp_us_screener.models import Bar, Security
from jp_us_screener.pipeline import compute_snapshot

GENERATED_AT = "2026-11-21T00:00:00+00:00"
START = date(2023, 1, 2)


def weekday_dates(count: int) -> list[str]:
    found: list[str] = []
    cursor = START
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return found


def build_bars(
    market: str,
    symbol: str,
    closes: list[float],
    *,
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    volumes: list[float] | None = None,
    volume: float = 400_000,
    dates: list[str] | None = None,
) -> list[Bar]:
    days = dates or weekday_dates(len(closes))
    if len(days) != len(closes):
        raise ValueError(f"{symbol} 日期數量不符")
    rows: list[Bar] = []
    for index, close in enumerate(closes):
        high = highs[index] if highs is not None else close * 1.012
        low = lows[index] if lows is not None else close * 0.988
        vol = volumes[index] if volumes is not None else volume
        open_ = min(high, max(low, close * 0.996))
        if high < max(open_, close, low) or low > min(open_, close, high):
            raise ValueError(f"{symbol} {days[index]} OHLC 不一致")
        rows.append(Bar(market, symbol, days[index], open_, high, low, close, vol, "split_and_dividend"))
    return rows


def trend(count: int, start: float, step: float) -> list[float]:
    price = start
    out = []
    for _ in range(count):
        price *= step
        out.append(price)
    return out


def adx_closes(count: int = 260) -> tuple[list[float], list[float], list[float]]:
    closes = trend(count, 32, 1.0045)
    highs = [price * 1.02 for price in closes]
    lows = [price * 0.985 for price in closes]
    closes[-1] = closes[-2] * 1.09
    highs[-1] = closes[-1] * 1.08
    lows[-1] = closes[-1] * 0.97
    return closes, highs, lows


def young_closes(count: int = 260) -> tuple[list[float], list[float]]:
    closes = trend(count, 20, 1.001)
    closes[-3] = closes[-4] * 1.04
    closes[-2] = closes[-3] * 1.035
    closes[-1] = closes[-2] * 1.035
    volumes = [160_000] * count
    volumes[-3:] = [500_000, 520_000, 540_000]
    return closes, volumes


def pullback_closes(count: int = 260) -> tuple[list[float], list[float], list[float]]:
    closes = []
    price = 15.0
    for _ in range(count):
        price *= 1.012
        closes.append(price)
    # A shallow dip inside the last 10 bars keeps ROC(20) above 20 and Ulcer non-zero.
    closes[-5] = closes[-6] * 0.97
    for index in range(-4, 0):
        closes[index] = closes[index - 1] * 1.012
    highs = [max(price * 1.004, price) for price in closes]
    lows = [price * 0.992 for price in closes]
    return closes, highs, lows


def squeeze_ohlc(count: int = 260) -> tuple[list[float], list[float], list[float]]:
    rise = 160
    closes = trend(rise, 25, 1.003)
    plateau = closes[-1]
    closes.extend([plateau] * (count - rise))
    highs = [price * 1.01 for price in closes]
    lows = [price * 0.99 for price in closes]
    for index in range(rise, count):
        highs[index] = plateau * 1.012
        lows[index] = plateau * 0.988
        closes[index] = plateau
    return closes, highs, lows


def turtle_ohlc(count: int = 260, *, pass_setup: bool = True) -> tuple[list[float], list[float], list[float]]:
    closes = [22.0] * count
    highs = [22.8] * count
    lows = [21.2] * count
    if pass_setup:
        lows[-6] = 20.0
        lows[-1] = 18.5
        highs[-1] = 21.0
        closes[-1] = 20.05
    return closes, highs, lows


def weak_ohlc(count: int = 260) -> tuple[list[float], list[float], list[float]]:
    """Decline, long chop so ADX fades, then a small +DI break under the prior 25-bar low.

    The path is synthetic and was checked against the typed clauses: ADX(14) < 20 and
    today's +DI(14) is strictly below the previous 25 bars, excluding today.
    """

    if count < 90:
        raise ValueError("weak series needs room for chop and the final break")
    closes: list[float] = []
    price = 120.0
    for _ in range(80):
        price *= 0.996
        closes.append(price)
    chop = count - 80 - 5 - 1
    for index in range(chop):
        price *= 1.0025 if index % 2 == 0 else 0.9975
        closes.append(price)
    for _ in range(5):
        price *= 1.001
        closes.append(price)
    last = price * 0.985
    closes.append(last)
    highs = [value * 1.008 for value in closes]
    lows = [value * 0.992 for value in closes]
    highs[-1] = last * 1.001
    lows[-1] = last * 0.985
    return closes, highs, lows


def bottom_ohlc(count: int = 260) -> list[float]:
    return trend(count, 90, 0.994)


def filler_closes(count: int, seed: int) -> list[float]:
    price = 30 + (seed % 17)
    closes = []
    state = seed
    for _ in range(count):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        shock = ((state % 1000) - 500) / 500 * 0.003
        price *= 1 + shock + 0.0006
        closes.append(max(price, 1.0))
    return closes


def security(
    market: str,
    symbol: str,
    name: str,
    *,
    security_type: str = "stock",
    cohort: str = "large",
    cap: float | None = 12_000_000_000,
    cap_ccy: str = "USD",
    listing: str = "primary",
    currency: str = "USD",
    exchange: str = "XNAS",
    group: str = "",
) -> Security:
    return Security(
        market=market,
        symbol=symbol,
        name=name,
        security_type=security_type,
        listing_segment=listing,
        exchange=exchange,
        currency=currency,
        cohort=cohort,
        market_cap=cap,
        market_cap_currency=cap_ccy if cap is not None else "",
        market_cap_as_of="2026-11-20" if cap is not None else "",
        group=group,
    )


def _pack(market: str, symbol: str, built, volume: float = 400_000, volumes: list[float] | None = None) -> list[Bar]:
    if len(built) == 3:
        closes, highs, lows = built
        return build_bars(market, symbol, closes, highs=highs, lows=lows, volume=volume, volumes=volumes)
    closes, vols = built
    return build_bars(market, symbol, closes, volume=volume, volumes=vols)


def assemble(*, turtle_passes: bool, young_spiked: bool, adx_liquid: bool, drop_passes: bool) -> tuple[list[Bar], list[Security]]:
    count = 260
    bars: list[Bar] = []
    people: list[Security] = []

    def add(market: str, symbol: str, name: str, series: list[Bar], **meta) -> None:
        bars.extend(series)
        currency = "JPY" if market == "JP" else "USD"
        exchange = "XTKS" if market == "JP" else "XNAS"
        people.append(security(market, symbol, name, currency=currency, exchange=exchange, **meta))

    adx_volume = 400_000 if adx_liquid else 1_000
    add("US", "SYN-L-ADX", "合成－大型趨勢", _pack("US", "SYN-L-ADX", adx_closes(count), volume=adx_volume))
    young = young_closes(count)
    if not young_spiked:
        closes = trend(count, 20, 1.001)
        volumes = [160_000] * count
        young = (closes, volumes)
    add("US", "SYN-L-YT", "合成－初生趨勢", _pack("US", "SYN-L-YT", young))
    add("US", "SYN-L-SQ", "合成－擠壓", _pack("US", "SYN-L-SQ", squeeze_ohlc(count), volume=450_000), cap=5_000_000_000)
    add("US", "SYN-L-TU", "合成－海龜", _pack("US", "SYN-L-TU", turtle_ohlc(count, pass_setup=turtle_passes), volume=800_000), cap=3_000_000_000)
    add(
        "US",
        "SYN-L-DROP",
        "合成－海龜對照",
        _pack("US", "SYN-L-DROP", turtle_ohlc(count, pass_setup=drop_passes), volume=800_000),
        cap=3_200_000_000,
    )
    add("US", "SYN-L-WK", "合成－弱趨勢", _pack("US", "SYN-L-WK", weak_ohlc(count), volume=250_000), cap=25_000_000_000)
    add("US", "SYN-L-BOT", "合成－最弱", build_bars("US", "SYN-L-BOT", bottom_ohlc(count), volume=220_000), cap=20_000_000_000)
    for index in range(1, 7):
        symbol = f"SYN-L-F{index:02d}"
        add("US", symbol, f"合成－大型填補{index}", build_bars("US", symbol, filler_closes(count, 100 + index), volume=180_000))

    add("US", "SYN-M-PB", "合成－中型回調", _pack("US", "SYN-M-PB", pullback_closes(count), volume=350_000), cohort="mid", cap=4_000_000_000)
    for index in range(1, 6):
        symbol = f"SYN-M-F{index:02d}"
        add(
            "US",
            symbol,
            f"合成－中型填補{index}",
            build_bars("US", symbol, filler_closes(count, 400 + index), volume=180_000),
            cohort="mid",
            cap=3_000_000_000,
        )

    add("JP", "SYN-L-ADX", "合成－日股趨勢", _pack("JP", "SYN-L-ADX", adx_closes(count), volume=400_000), cap_ccy="JPY", cap=2_000_000_000_000)
    add("JP", "SYN-L-YT", "合成－日股初生", _pack("JP", "SYN-L-YT", young_closes(count) if young_spiked else (trend(count, 20, 1.001), [160_000] * count)))
    add("JP", "SYN-L-WK", "合成－日股市值未換算", _pack("JP", "SYN-L-WK", weak_ohlc(count), volume=250_000), cap_ccy="JPY", cap=8_000_000_000_000)
    add("JP", "SYN-L-BOT", "合成－日股最弱", build_bars("JP", "SYN-L-BOT", bottom_ohlc(count), volume=220_000), cap_ccy="JPY", cap=7_000_000_000_000)
    for index in range(1, 5):
        symbol = f"SYN-L-F{index:02d}"
        add(
            "JP",
            symbol,
            f"合成－日股填補{index}",
            build_bars("JP", symbol, filler_closes(count, 700 + index), volume=180_000),
            cap_ccy="JPY",
            cap=1_500_000_000_000,
        )
    add("JP", "SYN-M-PB", "合成－日股回調", _pack("JP", "SYN-M-PB", pullback_closes(count), volume=350_000), cohort="mid", cap_ccy="JPY", cap=600_000_000_000)
    for index in range(1, 5):
        symbol = f"SYN-M-F{index:02d}"
        add(
            "JP",
            symbol,
            f"合成－日股中型{index}",
            build_bars("JP", symbol, filler_closes(count, 900 + index), volume=180_000),
            cohort="mid",
            cap_ccy="JPY",
            cap=500_000_000_000,
        )

    dates = weekday_dates(count)
    add(
        "US",
        "SYN-SHORT",
        "合成－歷史不足",
        build_bars("US", "SYN-SHORT", trend(40, 12, 1.002), dates=dates[-40:], volume=200_000),
    )
    add("US", "SYN-NOCAP", "合成－缺市值", build_bars("US", "SYN-NOCAP", trend(count, 18, 1.001), volume=200_000), cohort="", cap=None)
    add("US", "SYN-ETF", "合成－基金", build_bars("US", "SYN-ETF", trend(count, 40, 1.01), volume=2_000_000), security_type="etf", cohort="", cap=None, group="ETF")
    add("US", "SYN-UNK", "合成－類型不明", build_bars("US", "SYN-UNK", trend(count, 16, 1.001), volume=200_000), security_type="unknown", cohort="", cap=None)
    add("US", "SYN-ADR", "合成－存託未決議", build_bars("US", "SYN-ADR", trend(count, 16, 1.001), volume=200_000), listing="adr")
    return bars, people


def build_demo_snapshot() -> dict:
    config = EngineConfig(min_cohort_size=5)
    previous_bars, people = assemble(turtle_passes=True, young_spiked=False, adx_liquid=False, drop_passes=True)
    previous = compute_snapshot(previous_bars, people, config, mode="demo", generated_at="2026-11-20T00:00:00+00:00")
    bars, people = assemble(turtle_passes=True, young_spiked=True, adx_liquid=True, drop_passes=False)
    return compute_snapshot(
        bars,
        people,
        config,
        mode="demo",
        generated_at=GENERATED_AT,
        previous=previous,
    )


def summary(snapshot: dict) -> str:
    lines = []
    for scan_id, scan in snapshot["scans"].items():
        for market, payload in scan["markets"].items():
            symbols = [row["qualified_symbol"] for row in payload["matches"]]
            lines.append(
                f"{scan_id:10} {market} {payload['evaluation']:28} n={len(symbols):2} {', '.join(symbols)}"
            )
    return "\n".join(lines)


if __name__ == "__main__":
    print(summary(build_demo_snapshot()))
