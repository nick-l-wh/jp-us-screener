"""Turn public listings and Yahoo daily bars into engine rows.

Missing prices and missing caps stay missing. Nothing is filled with zero.
"""

from __future__ import annotations

import io
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from jp_us_screener.config import EngineConfig
from jp_us_screener.models import Bar
from jp_us_screener.validate import validate_bars

JP_INDEX_SERIES = frozenset({"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"})
EXCLUDE_NAME_BITS = (
    "etf",
    "etn",
    "warrant",
    "preferred",
    "preference",
    "depositary",
    "depository",
    "debenture",
    " when issued",
    "when-issued",
    "tick pilot",
    "test group",
    "beneficial interest",
    " unit",
    " units",
    " right",
    " rights",
    " note",
    " notes",
    " bond",
    " bonds",
)
COMMON_BITS = ("common stock", "common shares", "ordinary shares", "ordinary share")
US_EXCHANGES = {"N": "XNYS", "A": "XASE", "P": "ARCX", "Z": "BATS", "V": "IEXG"}
DENSE_SHARE = 0.8
CAP_SHARE = 0.5


@dataclass(frozen=True)
class SessionChoice:
    state: str  # ready, carried, incomplete
    session: date | None
    detail: str


def session_date(timestamp: int, zone_name: str) -> date:
    return datetime.fromtimestamp(int(timestamp), ZoneInfo(zone_name)).date()


def subsequent_split_factor(splits: list[tuple[date, float]], bar_day: date) -> float:
    factor = 1.0
    for ex_date, ratio in splits:
        if ex_date > bar_day:
            factor *= ratio
    return factor


def repair_ohlc(open_: float, high: float, low: float, close: float) -> tuple[float, float, float, float] | None:
    if min(open_, high, low, close) <= 0:
        return None
    ceiling = max(open_, close)
    floor = min(open_, close)
    if high < ceiling:
        if ceiling - high > max(1e-4, abs(ceiling) * 1e-6):
            return None
        high = ceiling
    if low > floor:
        if low - floor > max(1e-4, abs(floor) * 1e-6):
            return None
        low = floor
    if high < low:
        return None
    return open_, high, low, close


def choose_session(last_dates: list[date], expected: date) -> SessionChoice:
    """Pick the dense completed session at or before `expected`.

    A thin newer tail does not become the session. A weekday with no dense
    bars is treated as closed, and the previous dense session is carried.
    """

    if not last_dates:
        return SessionChoice("incomplete", None, "沒有任何完整行情")
    future = [day for day in last_dates if day > expected]
    if future:
        return SessionChoice("incomplete", None, f"行情晚於已收市交易日 {expected.isoformat()}")
    counts = Counter(last_dates)
    primary = max(counts, key=lambda day: (counts[day], day.toordinal()))
    share = counts[primary] / len(last_dates)
    if share < DENSE_SHARE:
        return SessionChoice(
            "incomplete",
            primary,
            f"{primary.isoformat()} 只有 {counts[primary]}/{len(last_dates)} 檔，不足八成，不當成完成的交易日",
        )
    if primary < expected:
        return SessionChoice(
            "carried",
            primary,
            f"預期 {expected.isoformat()} 沒有足夠成市，沿用 {primary.isoformat()}（{counts[primary]}/{len(last_dates)}）",
        )
    return SessionChoice("ready", primary, f"{primary.isoformat()} {counts[primary]}/{len(last_dates)}")


def coverage_problem(universe: int, usable: int, download_errors: int) -> str | None:
    if universe <= 0:
        return "宇宙是空的"
    if usable <= 0:
        return "沒有可用行情"
    if download_errors / universe > 1 - DENSE_SHARE:
        return f"下載失敗 {download_errors}/{universe}，超過兩成"
    if usable / universe < DENSE_SHARE:
        return f"可用行情 {usable}/{universe}，不足八成"
    return None


def caps_problem(market: str, usable: int, with_cap: int) -> str | None:
    if usable <= 0:
        return None
    if with_cap / usable < CAP_SHARE:
        return f"{market} 有市值的代號 {with_cap}/{usable}，不足一半，拒絕用缺市值的結果覆蓋"
    return None


def accept_cap(
    *,
    market: str,
    currency: str,
    session: date,
    close: float,
    quote: dict | None,
    zone: str,
) -> tuple[float | None, str]:
    """Return a real cap, or (None, reason). Never invents zero."""

    if not quote:
        return None, "quote_missing"
    if quote.get("quoteType") == "ETF":
        return None, "provider_etf"
    if quote.get("currency") != currency:
        return None, "currency_mismatch"
    shares = quote.get("sharesOutstanding")
    reported = quote.get("marketCap")
    price = quote.get("regularMarketPrice")
    if market == "JP":
        # Shares outstanding are the provider's current count. The cap uses this
        # session's close, including when the live quote has already moved on.
        if not isinstance(shares, (int, float)) or isinstance(shares, bool) or shares <= 0 or close <= 0:
            return None, "shares_missing"
        cap = float(shares) * float(close)
        if not math.isfinite(cap) or cap <= 0:
            return None, "cap_invalid"
        return cap, "shares_times_session_close"
    timestamp = quote.get("regularMarketTime")
    if not isinstance(timestamp, int):
        return None, "quote_time_missing"
    if session_date(timestamp, zone) != session:
        return None, "quote_session_mismatch"
    if not isinstance(reported, (int, float)) or isinstance(reported, bool) or reported <= 0:
        return None, "cap_missing"
    if (
        isinstance(price, (int, float))
        and not isinstance(price, bool)
        and isinstance(shares, (int, float))
        and not isinstance(shares, bool)
        and price > 0
        and shares > 0
    ):
        implied = float(price) * float(shares)
        if implied > 0 and abs(implied - float(reported)) / implied > 0.01:
            return None, "cap_not_price_times_shares"
    return float(reported), "yahoo_market_cap"


def transform_chart(
    requested: str,
    payload: dict,
    market: str,
    *,
    through: date,
    lookback_start: date,
    config: EngineConfig,
) -> tuple[list[Bar] | None, dict]:
    meta = payload.get("meta") or {}
    info = {
        "requested": requested,
        "symbol": meta.get("symbol"),
        "currency": meta.get("currency"),
        "bars": 0,
        "dust_repairs": 0,
    }
    symbol = meta.get("symbol")
    if symbol != requested:
        return None, {**info, "error": f"代號不符，要求 {requested}，返回 {symbol}"}
    currency = "JPY" if market == "JP" else "USD"
    if meta.get("currency") != currency:
        return None, {**info, "error": f"貨幣是 {meta.get('currency')}，不是 {currency}"}
    if meta.get("instrumentType") not in {None, "EQUITY"}:
        return None, {**info, "error": f"instrumentType={meta.get('instrumentType')}，不是股票"}
    zone = meta.get("exchangeTimezoneName") or ("Asia/Tokyo" if market == "JP" else "America/New_York")
    timestamps = payload.get("timestamp") or []
    quote = ((payload.get("indicators") or {}).get("quote") or [{}])[0]
    adjusted = (((payload.get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose")
    if adjusted is None:
        return None, {**info, "error": "沒有 adjclose，不能標成除權息調整"}
    events = payload.get("events") or {}
    splits: list[tuple[date, float]] = []
    for raw in (events.get("splits") or {}).values():
        numerator = float(raw.get("numerator") or 0)
        denominator = float(raw.get("denominator") or 0)
        if numerator <= 0 or denominator <= 0:
            return None, {**info, "error": "拆股比例無效"}
        splits.append((session_date(int(raw["date"]), zone), numerator / denominator))
    splits.sort()

    parsed: list[tuple[date, float, float, float, float, float]] = []
    for index, timestamp in enumerate(timestamps):
        day = session_date(int(timestamp), zone)
        if day > through or day < lookback_start:
            continue
        prices = [quote.get(name, [None])[index] for name in ("open", "high", "low", "close")]
        volume = (quote.get("volume") or [None])[index]
        adj = adjusted[index] if index < len(adjusted) else None
        if all(price is None for price in prices) and volume is None and adj is None:
            continue
        if any(price is None for price in prices) or volume is None or adj is None:
            return None, {**info, "error": f"{day.isoformat()} 有缺價、缺量或沒有調整收市，不會補零"}
        close = float(prices[3])
        if close == 0 or float(adj) <= 0:
            return None, {**info, "error": f"{day.isoformat()} 收市或調整收市不是正數"}
        factor = float(adj) / close
        if not math.isfinite(factor) or factor <= 0:
            return None, {**info, "error": f"{day.isoformat()} 調整因子無效"}
        scaled = repair_ohlc(*(float(price) * factor for price in prices))
        if scaled is None:
            return None, {**info, "error": f"{day.isoformat()} 調整後 OHLC 不成立"}
        if scaled != tuple(float(price) * factor for price in prices):
            info["dust_repairs"] += 1
        share_factor = subsequent_split_factor(splits, day)
        if share_factor == 0:
            return None, {**info, "error": f"{day.isoformat()} 拆股因子為 0"}
        raw_volume = float(volume) / share_factor
        if raw_volume < 0 or not math.isfinite(raw_volume):
            return None, {**info, "error": f"{day.isoformat()} 還原股數成交量無效"}
        parsed.append((day, *scaled, raw_volume))

    if not parsed:
        return None, {**info, "error": "截到已收市交易日後沒有任何完整柱"}
    parsed.sort()
    if any(day.weekday() >= 5 for day, *_rest in parsed):
        weekend = next(day for day, *_rest in parsed if day.weekday() >= 5)
        return None, {**info, "error": f"{weekend.isoformat()} 是週末柱"}
    dates = [day for day, *_rest in parsed]
    if len(dates) != len(set(dates)):
        return None, {**info, "error": "同一交易日重複"}
    for previous, current in zip(parsed, parsed[1:]):
        jump = previous[4] / current[4]
        if jump > 3.5 or jump < 1 / 3.5:
            return None, {**info, "error": f"{previous[0].isoformat()} 到 {current[0].isoformat()} 調整後收市跳動過大，整列剔除"}
    quote_symbol = requested[:-2] if market == "JP" and requested.endswith(".T") else requested
    bars = [
        Bar(market, quote_symbol, day.isoformat(), open_, high, low, close, volume, "split_and_dividend")
        for day, open_, high, low, close, volume in parsed
    ]
    errors = validate_bars(bars, config)
    if errors:
        return None, {**info, "error": errors[0]}
    info["bars"] = len(bars)
    info["last"] = bars[-1].date
    return bars, info


def _name_excluded(name: str) -> str | None:
    lowered = f" {name.lower()} "
    for bit in EXCLUDE_NAME_BITS:
        if bit in lowered:
            return bit.strip()
    return None


def _common_equity(name: str) -> bool:
    lowered = name.lower()
    return any(bit in lowered for bit in COMMON_BITS)


def _suffix_excluded(symbol: str) -> bool:
    upper = symbol.upper()
    return upper.endswith((".U", ".W", ".R", ".WS")) or ".WS" in upper


def parse_us_listings(nasdaq_text: str, other_text: str) -> tuple[list[dict], dict]:
    excluded: Counter[str] = Counter()
    selected: list[dict] = []
    seen: set[str] = set()

    def consider(symbol: str, name: str, exchange: str, etf: str, test: str, origin: str) -> None:
        if test == "Y":
            excluded["test_issue"] += 1
            return
        if etf == "Y":
            excluded["etf_flag"] += 1
            return
        if origin == "otherlisted" and exchange not in US_EXCHANGES:
            excluded["exchange_" + exchange] += 1
            return
        if _suffix_excluded(symbol):
            excluded["nasdaq_suffix"] += 1
            return
        reason = _name_excluded(name)
        if reason:
            excluded[reason] += 1
            return
        if not _common_equity(name):
            excluded["not_common_equity_name"] += 1
            return
        yahoo = symbol.replace(".", "-")
        if yahoo in seen:
            excluded["duplicate_symbol"] += 1
            return
        seen.add(yahoo)
        selected.append(
            {
                "market": "US",
                "symbol": yahoo,
                "yahoo": yahoo,
                "name": name.strip(),
                "exchange": "XNAS" if origin == "nasdaqlisted" else US_EXCHANGES[exchange],
                "currency": "USD",
            }
        )

    nasdaq_lines = nasdaq_text.splitlines()
    other_lines = other_text.splitlines()
    for line in nasdaq_lines[1:]:
        if not line or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 8:
            continue
        consider(parts[0], parts[1], "Q", parts[6], parts[3], "nasdaqlisted")
    for line in other_lines[1:]:
        if not line or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        consider(parts[0], parts[1], parts[2], parts[4], parts[6], "otherlisted")
    return selected, {
        "rows": len(selected),
        "excluded": dict(excluded),
        "nasdaq_footer": nasdaq_lines[-1] if nasdaq_lines else "",
        "other_footer": other_lines[-1] if other_lines else "",
    }


def parse_jpx(data: bytes) -> tuple[list[dict], dict]:
    import openpyxl

    workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    rows = workbook.active.iter_rows(values_only=True)
    header = [str(cell) if cell is not None else "" for cell in next(rows)]
    expected = ["Effective Date", "Local Code", "Name (English)", "Section/Products", "Size (New Index Series)"]
    missing = [name for name in expected if name not in header]
    if missing:
        raise ValueError(f"JPX 檔缺少欄位 {missing}")
    index = {name: header.index(name) for name in expected}
    selected: list[dict] = []
    seen: set[str] = set()
    effective: Counter[str] = Counter()
    for row in rows:
        size = row[index["Size (New Index Series)"]]
        if size not in JP_INDEX_SERIES:
            continue
        section = str(row[index["Section/Products"]] or "")
        if "Domestic" not in section:
            continue
        effective[str(row[index["Effective Date"]])] += 1
        code = str(row[index["Local Code"]]).strip()
        if code.endswith(".0"):
            code = code[:-2]
        if code.isdigit():
            code = code.zfill(4)
        name = str(row[index["Name (English)"]] or "").strip().strip("'").strip()
        if code in seen:
            raise ValueError(f"JPX 代號重複：{code}")
        seen.add(code)
        selected.append(
            {
                "market": "JP",
                "symbol": code,
                "yahoo": f"{code}.T",
                "name": name,
                "exchange": "XTKS",
                "currency": "JPY",
                "index_series": size,
            }
        )
    return selected, {"rows": len(selected), "effective_dates": dict(effective)}
