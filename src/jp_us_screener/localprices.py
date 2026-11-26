"""Read Asia Breadth-style daily bars from a local directory.

The files are not Yahoo chart downloads. A successful read does not call
Yahoo, does not fetch a crumb, and does not request a v7 quote. Missing
files and broken series stay missing. Nothing is filled with zero or with
demo bars.

Expected columns, in any order: Date, Open, High, Low, Close, AdjClose,
Volume. AdjOpen, AdjHigh, and AdjLow may also be present. Adjusted OHLC is
``Open/High/Low/Close * AdjClose/Close``, the same single factor the Yahoo
chart path uses. Volume is the number in the file. These files do not carry
split events, so volume is not reconstructed.

Filenames may differ from the universe symbol. ``7203.T.csv`` maps to
``7203``. ``BRK.B.csv``, ``BRK-B.csv``, and ``BRK B.csv`` map to ``BRK-B``.
A directory may be flat, or split into ``us/`` and ``jp/`` folders.
"""

from __future__ import annotations

import csv
import math
from datetime import date, datetime
from pathlib import Path

from jp_us_screener.config import EngineConfig
from jp_us_screener.indexlists import normalize_jp_code, normalize_us_ticker
from jp_us_screener.marketdata import repair_ohlc
from jp_us_screener.models import Bar
from jp_us_screener.validate import validate_bars

_HEADER = {
    "date": "date",
    "open": "open",
    "high": "high",
    "low": "low",
    "close": "close",
    "adjclose": "adjclose",
    "adjustedclose": "adjclose",
    "volume": "volume",
    "adjopen": "adjopen",
    "adjhigh": "adjhigh",
    "adjlow": "adjlow",
}
_REQUIRED = ("date", "open", "high", "low", "close", "adjclose", "volume")


def index_price_files(root: Path) -> dict[tuple[str, str], list[Path]]:
    """Map ``(market, symbol)`` to the CSV files that claim that symbol."""

    if not root.is_dir():
        raise FileNotFoundError(f"價格目錄不存在：{root}")
    buckets: dict[str | None, list[Path]] = {"US": [], "JP": [], None: []}
    market_dirs = {
        child.name.lower(): child
        for child in root.iterdir()
        if child.is_dir() and child.name.lower() in {"us", "jp"} and not child.name.startswith(".")
    }
    if market_dirs:
        for key, folder in market_dirs.items():
            market = "US" if key == "us" else "JP"
            buckets[market].extend(_csv_files(folder, recursive=True))
        buckets[None].extend(_csv_files(root, recursive=False))
    else:
        buckets[None].extend(_csv_files(root, recursive=True))
    found: dict[tuple[str, str], list[Path]] = {}
    for market, paths in buckets.items():
        markets = (market,) if market else ("US", "JP")
        for path in paths:
            for alias in _stem_aliases(path.stem):
                for item_market in markets:
                    symbol = normalize_us_ticker(alias) if item_market == "US" else normalize_jp_code(alias)
                    if not symbol:
                        continue
                    key = (item_market, symbol)
                    files = found.setdefault(key, [])
                    if path not in files:
                        files.append(path)
    return found


def match_price_file(index: dict[tuple[str, str], list[Path]], market: str, symbol: str) -> tuple[Path | None, str | None]:
    """Return ``(path, error)``. Two different files for one symbol is an error."""

    paths = index.get((market, symbol)) or []
    if not paths:
        return None, f"沒有 {market}:{symbol} 的價格檔"
    if len(paths) > 1:
        names = "、".join(str(path) for path in paths)
        return None, f"{market}:{symbol} 對上多個價格檔，不會挑選：{names}"
    return paths[0], None


def bars_from_asia_csv(
    path: Path,
    market: str,
    symbol: str,
    *,
    through: date,
    lookback_start: date,
    config: EngineConfig,
) -> tuple[list[Bar] | None, str | None]:
    """Convert one CSV. Returns ``(bars, error)``. Never invents a missing bar."""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"{path.name} 讀不到：{exc}"
    try:
        rows = list(csv.DictReader(text.splitlines()))
    except csv.Error as exc:
        return None, f"{path.name} 不是 CSV：{exc}"
    if not rows and not text.strip():
        return None, f"{path.name} 是空的"
    header_map = _map_header(rows[0].keys() if rows else _header_from_text(text))
    missing = [name for name in _REQUIRED if name not in header_map]
    if missing:
        return None, f"{path.name} 缺少欄位 {', '.join(missing)}"
    window: list[tuple[date, list[float | None], bool, bool]] = []
    for line_number, row in enumerate(rows, start=2):
        try:
            day = _parse_day(row.get(header_map["date"]) or "")
        except ValueError as exc:
            return None, f"{path.name} 第 {line_number} 列 {exc}"
        if day < lookback_start or day > through:
            continue
        try:
            prices = [_parse_number(row.get(header_map[name])) for name in ("open", "high", "low", "close", "adjclose")]
            volume = _parse_number(row.get(header_map["volume"]))
        except ValueError as exc:
            return None, f"{path.name} {day.isoformat()} {exc}"
        empty = all(price is None for price in prices) and volume is None
        partial = (not empty) and (any(price is None for price in prices) or volume is None)
        window.append((day, [*prices, volume], empty, partial))
    last_complete = max((index for index, row in enumerate(window) if not row[2] and not row[3]), default=-1)
    if last_complete < 0:
        return None, f"{symbol} 在 {lookback_start.isoformat()} 至 {through.isoformat()} 沒有完整柱"
    parsed: list[tuple[date, float, float, float, float, float]] = []
    for index, (day, values, empty, partial) in enumerate(window):
        if index > last_complete:
            continue
        if empty or partial:
            return None, f"{day.isoformat()} 有缺價、缺量或沒有調整收市，不會補零"
        open_, high, low, close, adj, volume = values
        assert open_ is not None and high is not None and low is not None
        assert close is not None and adj is not None and volume is not None
        if close <= 0 or adj <= 0:
            return None, f"{day.isoformat()} 收市或調整收市不是正數，不會補零"
        factor = adj / close
        if not math.isfinite(factor) or factor <= 0:
            return None, f"{day.isoformat()} 調整因子無效"
        scaled = repair_ohlc(open_ * factor, high * factor, low * factor, close * factor)
        if scaled is None:
            return None, f"{day.isoformat()} 調整後 OHLC 不成立"
        if volume < 0 or not math.isfinite(volume):
            return None, f"{day.isoformat()} 成交量無效"
        parsed.append((day, *scaled, volume))
    parsed.sort()
    if any(day.weekday() >= 5 for day, *_rest in parsed):
        weekend = next(day for day, *_rest in parsed if day.weekday() >= 5)
        return None, f"{weekend.isoformat()} 是週末柱"
    dates = [day for day, *_rest in parsed]
    if len(dates) != len(set(dates)):
        return None, f"{symbol} 同一交易日重複"
    for previous, current in zip(parsed, parsed[1:]):
        if current[4] == 0:
            return None, f"{current[0].isoformat()} 調整後收市不是正數"
        jump = previous[4] / current[4]
        if jump > 3.5 or jump < 1 / 3.5:
            return None, f"{previous[0].isoformat()} 到 {current[0].isoformat()} 調整後收市跳動過大，整列剔除"
    bars = [
        Bar(market, symbol, day.isoformat(), open_, high, low, close, volume, "split_and_dividend")
        for day, open_, high, low, close, volume in parsed
    ]
    errors = validate_bars(bars, config)
    if errors:
        return None, errors[0]
    return bars, None


def _csv_files(folder: Path, *, recursive: bool) -> list[Path]:
    paths = folder.rglob("*") if recursive else folder.glob("*")
    return [
        path
        for path in paths
        if path.is_file() and path.suffix.lower() == ".csv" and not path.name.startswith(".")
    ]


def _stem_aliases(stem: str) -> set[str]:
    raw = stem.strip()
    found = {raw, raw.upper()}
    upper = raw.upper()
    if upper.endswith(".T"):
        found.add(raw[:-2])
        found.add(upper[:-2])
    found.add(upper.replace("/", "-").replace(".", "-").replace(" ", "-"))
    return {item for item in found if item}


def _header_from_text(text: str) -> list[str]:
    line = next((item for item in text.splitlines() if item.strip()), "")
    return next(csv.reader([line]), [])


def _map_header(names: object) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for name in names or []:
        key = "".join(ch for ch in str(name).strip().lower() if ch.isalnum())
        canonical = _HEADER.get(key)
        if canonical and canonical not in mapped:
            mapped[canonical] = str(name)
    return mapped


def _parse_day(text: str) -> date:
    raw = text.strip()
    if not raw:
        raise ValueError("日期是空的")
    head = raw.split("T", 1)[0].split(" ", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(head, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"日期無法解析：{text}")


def _parse_number(value: object) -> float | None:
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if raw in {"", "-", "null", "None"}:
        return None
    try:
        number = float(raw)
    except ValueError as exc:
        raise ValueError(f"數字無法解析：{value}") from exc
    if not math.isfinite(number):
        return None
    return number
