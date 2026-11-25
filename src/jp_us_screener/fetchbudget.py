"""Bound Yahoo chart and quote traffic.

A run may send at most YAHOO_HTTP_BUDGET Yahoo HTTP requests. Chart, quote,
and crumb attempts all count, including retries. Each Yahoo request waits
YAHOO_REQUEST_INTERVAL seconds after the previous one. On HTTP 429, Retry-After
is honored up to two minutes and replaces that pause when it is longer.

Charts already on disk are reused. A short cache is extended only for the
missing days. When more symbols are stale or missing than the budget allows,
the run downloads a rotating page and leaves the rest on the cached file.
Quotes prefer symbols with no saved shares, then closes that moved a lot,
then a rotating slice. A 429 stops the remaining downloads. Caps are shares
outstanding times that session's close. Missing shares stay missing.
"""

from __future__ import annotations

import csv
import json
import math
from datetime import date, timedelta
from pathlib import Path

from jp_us_screener.io import atomic_write_json
from jp_us_screener.marketdata import session_date

QUOTE_BATCH = 100
YAHOO_HTTP_BUDGET = 100
YAHOO_REQUEST_INTERVAL = 1.5
# Cookie host plus getcrumb. Both are Yahoo HTTP and both count against the budget.
CRUMB_HTTP = 2
# When charts still need a download, quotes cannot take the whole budget.
MAX_QUOTE_RESERVE = 40
ROTATING_QUOTE_LIMIT = 100
PRICE_MOVE_FRACTION = 0.25
CHART_OVERLAP_DAYS = 7
COMMITTED_SHARES = Path("shares/outstanding.json")

_SOURCE_RANK = {
    "yahoo_quote": 3,
    "cached_inputs_implied": 2,
    "published_snapshot_implied": 1,
}


def chart_request_window(
    *,
    cached_first: date | None,
    cached_last: date | None,
    lookback_start: date,
    through: date,
) -> tuple[date, date] | None:
    """Return the download window, or None when the cache already covers it.

    The end is exclusive. A cache that already reaches `through` and the
    lookback is not downloaded again. A shorter cache only asks for the gap,
    with a few days of overlap so the last cached session can be replaced.
    """

    end = through + timedelta(days=1)
    # The first returned bar is the next session after a weekend or holiday,
    # so a cache that starts within a week of the lookback already has the history.
    history_ok = cached_first is not None and cached_first <= lookback_start + timedelta(days=CHART_OVERLAP_DAYS)
    if history_ok and cached_last is not None and cached_last >= through:
        return None
    if history_ok and cached_last is not None and cached_last < through:
        start = cached_last - timedelta(days=CHART_OVERLAP_DAYS)
        if start < lookback_start:
            start = lookback_start
        return start, end
    return lookback_start, end


def _action_ids(payload: dict) -> set[tuple]:
    found: set[tuple] = set()
    events = payload.get("events") or {}
    if not isinstance(events, dict):
        return found
    for kind, values in events.items():
        if not isinstance(values, dict):
            continue
        for raw in values.values():
            if not isinstance(raw, dict):
                continue
            found.add((kind, raw.get("date"), raw.get("numerator"), raw.get("denominator"), raw.get("amount")))
    return found


def corporate_action_invalidates(old: dict, new: dict) -> bool:
    """True when `new` contains a split or dividend the cached chart never saw."""

    return bool(_action_ids(new) - _action_ids(old))


def _quote_rows(payload: dict) -> dict[int, dict]:
    indicators = payload.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0] or {}
    adjusted = ((indicators.get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    rows: dict[int, dict] = {}
    for index, stamp in enumerate(payload.get("timestamp") or []):
        def cell(name: str, index: int = index) -> object:
            values = quote.get(name) or []
            return values[index] if index < len(values) else None

        rows[int(stamp)] = {
            "open": cell("open"),
            "high": cell("high"),
            "low": cell("low"),
            "close": cell("close"),
            "volume": cell("volume"),
            "adjclose": adjusted[index] if index < len(adjusted) else None,
        }
    return rows


def merge_chart_payloads(old: dict, new: dict) -> dict:
    """Overlay `new` bars onto `old`. New timestamps replace the overlap."""

    rows = _quote_rows(old)
    rows.update(_quote_rows(new))
    stamps = sorted(rows)

    def column(name: str) -> list:
        return [rows[stamp][name] for stamp in stamps]

    events: dict = {}
    for source in (old, new):
        for kind, values in (source.get("events") or {}).items():
            if isinstance(values, dict):
                events.setdefault(kind, {}).update(values)
    meta = dict(old.get("meta") or {})
    meta.update(new.get("meta") or {})
    return {
        "meta": meta,
        "timestamp": stamps,
        "indicators": {
            "quote": [
                {
                    "open": column("open"),
                    "high": column("high"),
                    "low": column("low"),
                    "close": column("close"),
                    "volume": column("volume"),
                }
            ],
            "adjclose": [{"adjclose": column("adjclose")}],
        },
        "events": events,
    }


def coverage_dates(payload: dict, market: str) -> tuple[date | None, date | None]:
    meta = payload.get("meta") or {}
    zone = meta.get("exchangeTimezoneName") or ("Asia/Tokyo" if market == "JP" else "America/New_York")
    days: list[date] = []
    for stamp in payload.get("timestamp") or []:
        try:
            days.append(session_date(int(stamp), zone))
        except (TypeError, ValueError, OSError):
            continue
    if not days:
        return None, None
    return min(days), max(days)


def last_adjusted_close(payload: dict, *, market: str, through: date) -> float | None:
    """Last adjclose on or before `through`. This matches the engine's adjusted close."""

    meta = payload.get("meta") or {}
    zone = meta.get("exchangeTimezoneName") or ("Asia/Tokyo" if market == "JP" else "America/New_York")
    indicators = payload.get("indicators") or {}
    adjusted = ((indicators.get("adjclose") or [{}])[0] or {}).get("adjclose") or []
    last: float | None = None
    for index, stamp in enumerate(payload.get("timestamp") or []):
        try:
            day = session_date(int(stamp), zone)
        except (TypeError, ValueError, OSError):
            continue
        if day > through or index >= len(adjusted):
            continue
        value = adjusted[index]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0 or not math.isfinite(value):
            continue
        last = float(value)
    return last


def _positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def usable_shares(record: dict | None, currency: str) -> bool:
    if not record or record.get("currency") != currency:
        return False
    return _positive(record.get("shares_outstanding"))


def select_chart_refresh(items: list, *, session: date, limit: int) -> list:
    """One page of symbols that still need a chart download.

    `items` are the stale or missing names only. The page advances by `limit`
    symbols each session, so a tight budget walks the universe instead of
    repeating the same prefix. A limit of zero downloads nothing.
    """

    group = sorted(items, key=lambda item: (item.market, item.symbol))
    if limit <= 0 or not group:
        return []
    if limit >= len(group):
        return group
    start = (session.toordinal() * limit) % len(group)
    return [group[(start + offset) % len(group)] for offset in range(limit)]


def chart_download_limits(jobs_by_market: dict[str, int], limit: int) -> dict[str, int]:
    """Split a chart budget across markets. A market with no work gets nothing.

    The larger pending list gets any remainder. Unused quota from a short list
    is handed to the other market.
    """

    quotas = {"US": 0, "JP": 0}
    pending = {market: max(0, jobs_by_market.get(market, 0)) for market in ("US", "JP")}
    markets = [market for market in ("US", "JP") if pending[market] > 0]
    if limit <= 0 or not markets:
        return quotas
    ordered = sorted(markets, key=lambda market: pending[market], reverse=True)
    base, remainder = divmod(limit, len(ordered))
    for index, market in enumerate(ordered):
        quotas[market] = min(pending[market], base + (1 if index < remainder else 0))
    used = sum(quotas.values())
    if used < limit:
        for market in ordered:
            extra = min(pending[market] - quotas[market], limit - used)
            quotas[market] += extra
            used += extra
            if used >= limit:
                break
    return quotas


def quote_request_reserve(
    *,
    missing_shares: int,
    listed: int,
    chart_jobs: int,
    budget: int = YAHOO_HTTP_BUDGET,
) -> int:
    """Yahoo requests to leave for crumb and quotes before chart downloads start.

    Missing shares are what the cap check cannot fill from cache. One quote
    request carries QUOTE_BATCH symbols, and crumb setup costs CRUMB_HTTP.
    When charts are still pending, this reserve stops at MAX_QUOTE_RESERVE so
    a rotating chart page still runs. When every chart is already cached, quotes
    may use the whole budget.
    """

    if budget <= 0 or listed <= 0:
        return 0
    if missing_shares > 0:
        wanted = missing_shares
    else:
        wanted = min(listed, ROTATING_QUOTE_LIMIT * 2)
    if wanted <= 0:
        return 0
    batches = (wanted + QUOTE_BATCH - 1) // QUOTE_BATCH
    reserve = CRUMB_HTTP + batches
    if chart_jobs > 0:
        reserve = min(reserve, MAX_QUOTE_RESERVE)
        if reserve >= budget and budget > 1:
            reserve = budget - 1
    return min(reserve, max(0, budget))


def select_quote_refresh(
    items: list,
    records: dict[str, dict],
    closes: dict[tuple[str, str], float],
    *,
    session_by_market: dict[str, date],
    limit: int = ROTATING_QUOTE_LIMIT,
    move: float = PRICE_MOVE_FRACTION,
    symbol_limit: int | None = None,
) -> list:
    """Missing shares, then closes that moved a lot, then a rotating slice.

    The rotating slice is capped per market. Missing shares are not capped
    unless `symbol_limit` is set. The caller also stops on HTTP 429 and when
    the Yahoo request budget is spent. A prefix of this list is what a short
    budget refreshes, so names without shares come first.
    """

    chosen: list = []
    seen: set[tuple[str, str]] = set()

    def add(item) -> bool:
        if symbol_limit is not None and len(chosen) >= symbol_limit:
            return False
        key = (item.market, item.symbol)
        if key in seen:
            return True
        seen.add(key)
        chosen.append(item)
        return True

    groups: dict[str, list] = {}
    for market in ("US", "JP"):
        group = [item for item in items if item.market == market and item.payload and not item.error]
        group.sort(key=lambda item: item.symbol)
        groups[market] = group

    for market in ("US", "JP"):
        for item in groups[market]:
            record = records.get(f"{item.market}:{item.symbol}")
            if not usable_shares(record, item.currency):
                if not add(item):
                    return chosen
    for market in ("US", "JP"):
        for item in groups[market]:
            record = records.get(f"{item.market}:{item.symbol}")
            close = closes.get((item.market, item.symbol))
            observed = record.get("close_at_observation") if record else None
            if close and _positive(observed) and abs(float(close) / float(observed) - 1) > move:
                if not add(item):
                    return chosen
    for market in ("US", "JP"):
        group = groups[market]
        if not group:
            continue
        session = session_by_market[market]
        start = session.toordinal() % len(group)
        for offset in range(min(limit, len(group))):
            if not add(group[(start + offset) % len(group)]):
                return chosen
    return chosen


def _prefer_record(current: dict | None, incoming: dict) -> dict:
    if current is None:
        return incoming
    current_rank = _SOURCE_RANK.get(str(current.get("source")), 0)
    incoming_rank = _SOURCE_RANK.get(str(incoming.get("source")), 0)
    if incoming_rank != current_rank:
        return incoming if incoming_rank > current_rank else current
    if str(incoming.get("shares_as_of") or "") >= str(current.get("shares_as_of") or ""):
        return incoming
    return current


def load_share_records(paths: list[Path]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        records = payload.get("records") if isinstance(payload, dict) else None
        if not isinstance(records, dict):
            continue
        for key, record in records.items():
            if isinstance(record, dict) and usable_shares(record, str(record.get("currency") or "")):
                merged[str(key)] = _prefer_record(merged.get(str(key)), record)
    return merged


def save_share_records(paths: list[Path], records: dict[str, dict]) -> None:
    if not records:
        return
    document = {
        "version": 1,
        "note": "流通股數快取。市值用股數乘以該交易日收市價。缺股數不會寫成 0。yahoo_quote 是 Yahoo 報價的 sharesOutstanding；implied 是已公布市值除以當日調整收市價，不是新的股本公布。",
        "records": records,
    }
    for path in paths:
        atomic_write_json(path, document)


def _implied_record(
    *,
    market: str,
    symbol: str,
    yahoo: str,
    currency: str,
    shares: float,
    session: str,
    close: float,
    source: str,
    provenance: str,
) -> dict:
    return {
        "market": market,
        "symbol": symbol,
        "yahoo": yahoo,
        "currency": currency,
        "shares_outstanding": shares,
        "shares_as_of": session,
        "close_at_observation": close,
        "source": source,
        "provenance": provenance,
    }


def seed_records_from_snapshot(snapshot: dict) -> dict[str, dict]:
    """Imply shares as published market_cap / adjusted close. Skip conflicts and blanks."""

    if snapshot.get("mode") != "real":
        return {}
    snapshot_id = str(snapshot.get("snapshot_id") or "unknown")
    found: dict[str, dict | None] = {}

    def consider(row: dict) -> None:
        market = row.get("market")
        symbol = row.get("symbol")
        session = row.get("last_date")
        currency = row.get("market_cap_currency")
        cap = row.get("market_cap")
        close = row.get("close")
        if market not in {"US", "JP"} or not symbol or not session:
            return
        if (market == "US" and currency != "USD") or (market == "JP" and currency != "JPY"):
            return
        if not _positive(cap) or not _positive(close):
            return
        shares = float(cap) / float(close)
        if not _positive(shares):
            return
        key = f"{market}:{symbol}"
        yahoo = f"{symbol}.T" if market == "JP" else str(symbol).replace(".", "-")
        record = _implied_record(
            market=market,
            symbol=str(symbol),
            yahoo=yahoo,
            currency=str(currency),
            shares=shares,
            session=str(session),
            close=float(close),
            source="published_snapshot_implied",
            provenance=(
                f"snapshot {snapshot_id}：market_cap / 調整收市價，交易日 {session}。"
                "不是 Yahoo sharesOutstanding 的新公布。"
            ),
        )
        previous = found.get(key)
        if previous is None and key in found:
            return
        if previous is not None:
            prior = float(previous["shares_outstanding"])
            if abs(prior - shares) / shares > 0.01:
                found[key] = None
            return
        found[key] = record

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if "market_cap" in node and "close" in node and "symbol" in node:
                consider(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(snapshot.get("scans") or {})
    return {key: record for key, record in found.items() if record is not None}


def seed_records_from_inputs(ohlcv_path: Path, cap_path: Path) -> dict[str, dict]:
    """Imply shares from a prior run's market-cap file and that session's adjusted close."""

    if not ohlcv_path.exists() or not cap_path.exists():
        return {}
    closes: dict[tuple[str, str, str], float] = {}
    with ohlcv_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            market = (row.get("market") or "").strip()
            symbol = (row.get("symbol") or "").strip()
            session = (row.get("date") or "").strip()
            raw = (row.get("close") or "").strip()
            if not market or not symbol or not session or not raw:
                continue
            try:
                close = float(raw)
            except ValueError:
                continue
            if _positive(close):
                closes[(market, symbol, session)] = close
    records: dict[str, dict] = {}
    with cap_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            market = (row.get("market") or "").strip()
            symbol = (row.get("symbol") or "").strip()
            session = (row.get("market_cap_as_of") or "").strip()
            currency = (row.get("market_cap_currency") or "").strip().upper()
            raw = (row.get("market_cap") or "").strip()
            if market not in {"US", "JP"} or not symbol or not session or not raw:
                continue
            if (market == "US" and currency != "USD") or (market == "JP" and currency != "JPY"):
                continue
            close = closes.get((market, symbol, session))
            if close is None:
                continue
            try:
                cap = float(raw)
            except ValueError:
                continue
            if not _positive(cap):
                continue
            shares = cap / close
            if not _positive(shares):
                continue
            yahoo = f"{symbol}.T" if market == "JP" else symbol.replace(".", "-")
            records[f"{market}:{symbol}"] = _implied_record(
                market=market,
                symbol=symbol,
                yahoo=yahoo,
                currency=currency,
                shares=shares,
                session=session,
                close=close,
                source="cached_inputs_implied",
                provenance=(
                    f"{cap_path.name} 的市值除以 {ohlcv_path.name} 在 {session} 的調整收市價。"
                    "不是 Yahoo sharesOutstanding 的新公布。"
                ),
            )
    return records


def fill_missing_records(records: dict[str, dict], incoming: dict[str, dict]) -> int:
    added = 0
    for key, record in incoming.items():
        if key not in records and usable_shares(record, str(record.get("currency") or "")):
            records[key] = record
            added += 1
    return added


def remember_quote(records: dict[str, dict], item, quote: dict, close: float | None, shares: float, shares_as_of: str) -> None:
    """Store a Yahoo shares print. Does not store zero, and does not replace a newer print."""

    key = f"{item.market}:{item.symbol}"
    observed = close if _positive(close) else quote.get("regularMarketPrice")
    record = {
        "market": item.market,
        "symbol": item.symbol,
        "yahoo": item.yahoo,
        "currency": item.currency,
        "shares_outstanding": float(shares),
        "shares_as_of": shares_as_of,
        "close_at_observation": float(observed) if _positive(observed) else None,
        "source": "yahoo_quote",
        "provenance": (
            "Yahoo Finance v7 quote sharesOutstanding。"
            "報價同時有股價和市值時，兩者相差超過 1% 的不會寫入。"
            f"觀察日 {shares_as_of or '未標明'}。"
        ),
    }
    current = records.get(key)
    if current is not None and current.get("source") == "yahoo_quote":
        if str(current.get("shares_as_of") or "") > shares_as_of:
            return
    records[key] = record


def cap_coverage_note(rows: dict[str, list]) -> str:
    fresh = 0
    cached = 0
    missing = 0
    observed: list[str] = []
    for market in ("US", "JP"):
        for row in rows.get(market) or []:
            if row.market_cap is None:
                missing += 1
                continue
            if row.cap_status == "cached_shares_times_session_close":
                cached += 1
                if row.shares_as_of:
                    observed.append(row.shares_as_of)
            else:
                fresh += 1
    if cached and observed:
        stale = f"沿用較早流通股數 {cached} 檔，觀察日 {min(observed)} 至 {max(observed)}，不是本交易日新公布的股本。"
    elif cached:
        stale = f"沿用較早流通股數 {cached} 檔，觀察日沒有寫明。"
    else:
        stale = "沒有沿用較早流通股數。"
    return (
        "本輪 Yahoo 報價算出市值 "
        f"{fresh} 檔。其餘用已保存流通股數乘以該交易日收市價。"
        f"{stale}沒有流通股數 {missing} 檔，不歸入族群，也不會當成 0。"
    )
