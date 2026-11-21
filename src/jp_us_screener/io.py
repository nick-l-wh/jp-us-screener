"""CSV and JSON boundaries. Parsing failures raise; they do not emit an empty success."""

from __future__ import annotations

import csv
import io
import json
import math
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path

from jp_us_screener.models import Bar, Security

OHLCV_COLUMNS = [
    "market",
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adjustment",
]
UNIVERSE_COLUMNS = [
    "market",
    "symbol",
    "name",
    "security_type",
    "listing_segment",
    "exchange",
    "currency",
    "cohort",
    "market_cap",
    "market_cap_currency",
    "market_cap_as_of",
    "group",
]
CAP_COLUMNS = ["market", "symbol", "market_cap", "market_cap_currency", "market_cap_as_of"]


class ImportError_(Exception):
    """Structural import failure. The previous snapshot must stay untouched."""


def read_csv(path: str | Path) -> tuple[list[str], list[dict[str, str]]]:
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_csv(text, source=str(path))


def parse_csv(text: str, source: str = "<memory>") -> tuple[list[str], list[dict[str, str]]]:
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    try:
        reader = csv.DictReader(io.StringIO(text))
    except csv.Error as exc:
        raise ImportError_(f"{source} 不是有效 CSV：{exc}") from exc
    if reader.fieldnames is None:
        raise ImportError_(f"{source} 沒有表頭")
    fields = [name.strip() for name in reader.fieldnames]
    rows = []
    try:
        for line_no, row in enumerate(reader, start=2):
            if row is None:
                continue
            if None in row:
                raise ImportError_(f"{source} 第 {line_no} 行列數多於表頭")
            cleaned = {(key or "").strip(): (value if value is not None else "") for key, value in row.items()}
            if all(not str(value).strip() for value in cleaned.values()):
                continue
            cleaned["_line"] = str(line_no)
            rows.append(cleaned)
    except csv.Error as exc:
        raise ImportError_(f"{source} CSV 讀取失敗：{exc}") from exc
    return fields, rows


def require_columns(fields: list[str], required: list[str], source: str) -> None:
    missing = [name for name in required if name not in fields]
    if missing:
        raise ImportError_(f"{source} 缺少欄位：{', '.join(missing)}")


def parse_number(raw: str, source: str, field: str) -> float:
    text = raw.strip()
    if text == "":
        raise ImportError_(f"{source} 的 {field} 是空白")
    if "," in text:
        raise ImportError_(f"{source} 的 {field} 不可使用千分位逗號")
    try:
        dec = Decimal(text)
    except InvalidOperation as exc:
        raise ImportError_(f"{source} 的 {field} 不是數字：{raw}") from exc
    if not dec.is_finite():
        raise ImportError_(f"{source} 的 {field} 不是有限數字")
    value = float(dec)
    if not math.isfinite(value):
        raise ImportError_(f"{source} 的 {field} 超出浮點範圍")
    return value


def load_ohlcv(path: str | Path) -> list[Bar]:
    fields, rows = read_csv(path)
    require_columns(fields, OHLCV_COLUMNS, str(path))
    bars: list[Bar] = []
    for row in rows:
        loc = f"{path} 第 {row['_line']} 行"
        market = row["market"].strip().upper()
        symbol = row["symbol"].strip()
        if not symbol or ":" in symbol:
            raise ImportError_(f"{loc} 代號空白或含有冒號；合格代號由市場與代號組成")
        try:
            _parse_iso_date(row["date"].strip(), loc)
        except ImportError_:
            raise
        bars.append(
            Bar(
                market=market,
                symbol=symbol,
                date=row["date"].strip(),
                open=parse_number(row["open"], loc, "open"),
                high=parse_number(row["high"], loc, "high"),
                low=parse_number(row["low"], loc, "low"),
                close=parse_number(row["close"], loc, "close"),
                volume=parse_number(row["volume"], loc, "volume"),
                adjustment=row["adjustment"].strip(),
            )
        )
    return bars


def load_universe(path: str | Path, marketcap_path: str | Path | None = None) -> list[Security]:
    fields, rows = read_csv(path)
    require_columns(fields, UNIVERSE_COLUMNS, str(path))
    caps: dict[tuple[str, str], dict[str, str]] = {}
    if marketcap_path:
        cap_fields, cap_rows = read_csv(marketcap_path)
        require_columns(cap_fields, CAP_COLUMNS, str(marketcap_path))
        for row in cap_rows:
            key = (row["market"].strip().upper(), row["symbol"].strip())
            if key in caps:
                raise ImportError_(f"{marketcap_path} 重複的 {key[0]}:{key[1]}")
            caps[key] = row
    securities: list[Security] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        loc = f"{path} 第 {row['_line']} 行"
        market = row["market"].strip().upper()
        symbol = row["symbol"].strip()
        if not symbol or ":" in symbol:
            raise ImportError_(f"{loc} 代號空白或含有冒號")
        key = (market, symbol)
        if key in seen:
            raise ImportError_(f"{loc} 與較早的列重複了 {market}:{symbol}")
        seen.add(key)
        cap_row = caps.get(key)
        cap_raw = row["market_cap"].strip()
        cap_ccy = row["market_cap_currency"].strip().upper()
        cap_as_of = row["market_cap_as_of"].strip()
        if cap_row is not None:
            extra_raw = cap_row["market_cap"].strip()
            extra_ccy = cap_row["market_cap_currency"].strip().upper()
            extra_as_of = cap_row["market_cap_as_of"].strip()
            if cap_raw and extra_raw and cap_raw != extra_raw:
                raise ImportError_(f"{market}:{symbol} 的宇宙市值與市值檔不一致")
            if cap_ccy and extra_ccy and cap_ccy != extra_ccy:
                raise ImportError_(f"{market}:{symbol} 的市值貨幣不一致")
            cap_raw = cap_raw or extra_raw
            cap_ccy = cap_ccy or extra_ccy
            cap_as_of = cap_as_of or extra_as_of
        market_cap = parse_number(cap_raw, loc, "market_cap") if cap_raw else None
        if market_cap is not None and not cap_ccy:
            raise ImportError_(f"{loc} 有市值但沒有 market_cap_currency")
        if cap_as_of:
            _parse_iso_date(cap_as_of, loc)
        securities.append(
            Security(
                market=market,
                symbol=symbol,
                name=row["name"],
                security_type=row["security_type"].strip(),
                listing_segment=row["listing_segment"].strip(),
                exchange=row["exchange"].strip(),
                currency=row["currency"].strip().upper(),
                cohort=row["cohort"].strip(),
                market_cap=market_cap,
                market_cap_currency=cap_ccy,
                market_cap_as_of=cap_as_of,
                group=row["group"].strip(),
            )
        )
    return securities


def _parse_iso_date(value: str, loc: str):
    from datetime import date

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ImportError_(f"{loc} 日期必須是 YYYY-MM-DD：{value}") from exc


def write_csv(path: str | Path, fieldnames: list[str], rows: list[dict]) -> None:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in fieldnames})
    atomic_write(path, buffer.getvalue())


def atomic_write(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, target)


def atomic_write_json(path: str | Path, payload: dict) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def template_ohlcv() -> str:
    return (
        "market,symbol,date,open,high,low,close,volume,adjustment\n"
        "US,SYN-EXAMPLE,2024-01-02,10,11,9,10.5,1000000,split_and_dividend\n"
    )


def template_universe() -> str:
    header = ",".join(UNIVERSE_COLUMNS)
    row = 'US,SYN-EXAMPLE,"合成,「範例」",stock,primary,XNAS,USD,large,2000000000,USD,2024-01-02,\n'
    return header + "\n" + row


def template_marketcap() -> str:
    return "market,symbol,market_cap,market_cap_currency,market_cap_as_of\nUS,SYN-EXAMPLE,2000000000,USD,2024-01-02\n"
