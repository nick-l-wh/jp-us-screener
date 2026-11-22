"""Fetch the latest completed US and JP sessions, screen them, and publish.

Entrypoint, from the repository root:

    python -m jp_us_screener update

automation-1795319781957 starts that command on Monday–Saturday around 08:00
Hong Kong (UTC+8, no US daylight-saving shift), inside 07:45–08:15. This module
does not register a timer. It writes web/public/data only after a real snapshot
validates. An unchanged session, a holiday carried forward, or any failure
leaves the previous public files in place. Full price history stays under the
gitignored data/ directory.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jp_us_screener.config import EngineConfig, load_config
from jp_us_screener.io import load_ohlcv, load_universe, write_csv
from jp_us_screener.marketdata import (
    accept_cap,
    caps_problem,
    choose_session,
    coverage_problem,
    parse_jpx,
    parse_us_listings,
    transform_chart,
)
from jp_us_screener.models import Bar
from jp_us_screener.pipeline import compute_snapshot
from jp_us_screener.publish import (
    UpdateError,
    build_publication,
    config_problem,
    dated_snapshot_name,
    publish_files,
    published_sessions,
    read_published,
    regression_problem,
    validate_public_snapshot,
)
from jp_us_screener.sessions import (
    ROUTINE_ID,
    expected_sessions,
    session_cut,
    within_start_window,
)
from jp_us_screener.yahoo import YahooClient

JPX_URL = "https://www.jpx.co.jp/english/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_e.xlsx"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
LOOKBACK_DAYS = 800


@dataclass
class LoadedSymbol:
    market: str
    symbol: str
    yahoo: str
    name: str
    exchange: str
    currency: str
    payload: dict | None = None
    quote: dict | None = None
    error: str | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class LoadedMarkets:
    symbols: dict[str, list[LoadedSymbol]]
    notes: dict[str, str]


@dataclass
class PricedSymbol:
    market: str
    symbol: str
    name: str
    exchange: str
    currency: str
    bars: list[Bar]
    market_cap: float | None
    market_cap_currency: str
    market_cap_as_of: str
    cap_status: str


@dataclass
class Assembled:
    rows: dict[str, list[PricedSymbol]]
    choices: dict
    problems: list[str]
    notes: dict[str, str]


def assemble(loaded: LoadedMarkets, expected: dict[str, date], config: EngineConfig) -> Assembled:
    rows: dict[str, list[PricedSymbol]] = {}
    choices = {}
    problems: list[str] = []
    for market in ("US", "JP"):
        symbols = loaded.symbols.get(market) or []
        zone = "Asia/Tokyo" if market == "JP" else "America/New_York"
        lookback = expected[market] - timedelta(days=LOOKBACK_DAYS)
        transformed: list[tuple[LoadedSymbol, list[Bar]]] = []
        errors = 0
        for item in symbols:
            if item.error or item.payload is None:
                errors += 1
                continue
            bars, info = transform_chart(
                item.yahoo,
                item.payload,
                market,
                through=expected[market],
                lookback_start=lookback,
                config=config,
            )
            if bars is None:
                errors += 1
                item.error = str(info.get("error") or "行情無效")
                continue
            transformed.append((item, bars))
        choice = choose_session([date.fromisoformat(bars[-1].date) for _item, bars in transformed], expected[market])
        choices[market] = choice
        if choice.state == "incomplete" or choice.session is None:
            problems.append(f"{market} {choice.detail}")
            rows[market] = []
            continue
        priced: list[PricedSymbol] = []
        etf = 0
        for item, bars in transformed:
            if date.fromisoformat(bars[-1].date) != choice.session:
                continue
            close = bars[-1].close
            cap, status = accept_cap(
                market=market,
                currency=item.currency,
                session=choice.session,
                close=close,
                quote=item.quote,
                zone=zone,
            )
            if status == "provider_etf":
                etf += 1
                continue
            priced.append(
                PricedSymbol(
                    market=market,
                    symbol=item.symbol,
                    name=item.name,
                    exchange=item.exchange,
                    currency=item.currency,
                    bars=bars,
                    market_cap=cap,
                    market_cap_currency=item.currency if cap is not None else "",
                    market_cap_as_of=choice.session.isoformat() if cap is not None else "",
                    cap_status=status,
                )
            )
        universe = len(symbols) - etf
        problem = coverage_problem(universe, len(priced), errors)
        if problem:
            problems.append(f"{market} {problem}")
        with_cap = sum(1 for row in priced if row.market_cap is not None)
        cap_gap = caps_problem(market, len(priced), with_cap)
        if cap_gap:
            problems.append(cap_gap)
        rows[market] = priced
    return Assembled(rows=rows, choices=choices, problems=problems, notes=dict(loaded.notes))


def run_update(
    *,
    public_dir: Path,
    config_path: Path,
    cache_dir: Path,
    now: datetime,
    load: Callable[[dict[str, date], Path], LoadedMarkets] | None = None,
) -> int:
    try:
        return _run_update(public_dir=public_dir, config_path=config_path, cache_dir=cache_dir, now=now, load=load)
    except UpdateError as exc:
        print(f"failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — a crashed update must not replace the public snapshot
        print(f"failed: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


def _run_update(
    *,
    public_dir: Path,
    config_path: Path,
    cache_dir: Path,
    now: datetime,
    load: Callable[[dict[str, date], Path], LoadedMarkets] | None,
) -> int:
    cut = session_cut(now)
    expected = expected_sessions(cut)
    window = "視窗內" if within_start_window(now) else "視窗外"
    print(
        f"每日更新 {ROUTINE_ID} 香港切點 {cut.isoformat()} {window} "
        f"預期美國 {expected['US'].isoformat()} 日本 {expected['JP'].isoformat()}",
        flush=True,
    )
    latest_path = public_dir / "latest.json"
    previous = read_published(latest_path)
    published = published_sessions(previous) if previous else None
    if published and all(expected[market] <= date.fromisoformat(published[market]) for market in ("US", "JP")):
        print(f"unchanged US {published['US']} JP {published['JP']}")
        return 0

    config = load_config(config_path)
    problem = config_problem(config)
    if problem:
        print(f"failed: {problem}", file=sys.stderr)
        return 1
    loader = load or fetch_markets
    loaded = loader(expected, cache_dir)
    assembled = assemble(loaded, expected, config)
    for market, choice in assembled.choices.items():
        print(f"{market} {choice.state} {choice.detail}", flush=True)
    if assembled.problems:
        print("failed: " + "；".join(assembled.problems), file=sys.stderr)
        return 1
    sessions = {market: assembled.choices[market].session for market in ("US", "JP")}
    if any(session is None for session in sessions.values()):
        print("failed: 沒有交易日", file=sys.stderr)
        return 1
    if published:
        for market, session in sessions.items():
            previous_day = date.fromisoformat(published[market])
            if session < previous_day:
                print(f"failed: {market} {session.isoformat()} 早於已公布 {published[market]}，保留原快照", file=sys.stderr)
                return 1
        if all(sessions[market] == date.fromisoformat(published[market]) for market in ("US", "JP")):
            print(f"unchanged US {published['US']} JP {published['JP']}")
            return 0

    assert sessions["US"] is not None and sessions["JP"] is not None
    work = cache_dir / "inputs"
    _write_inputs(work, assembled.rows)
    bars = load_ohlcv(work / "ohlcv.csv")
    securities = load_universe(work / "universe.csv", work / "marketcap.csv")
    if any(security.cohort for security in securities):
        print("failed: 宇宙檔寫入了 cohort，市值門檻沒有生效", file=sys.stderr)
        return 1
    baseline = previous if previous and previous.get("mode") == "real" else None
    snapshot = compute_snapshot(
        bars,
        securities,
        config,
        mode="real",
        generated_at=cut.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
        previous=baseline,
    )
    blocked = regression_problem(baseline, snapshot)
    if blocked:
        print(f"failed: {blocked}", file=sys.stderr)
        return 1
    errors = validate_public_snapshot(snapshot, sessions, cut.date())
    if errors:
        print("failed: " + "；".join(errors), file=sys.stderr)
        return 1
    publication = build_publication(snapshot, assembled.notes)
    if publication["snapshot_id"] != snapshot["snapshot_id"]:
        print("failed: 公布說明與快照不一致", file=sys.stderr)
        return 1
    for market in ("US", "JP"):
        for status in publication["scans"][market].values():
            if status not in {"valid", "not_valid", "incomplete"}:
                print("failed: 公布狀態無效", file=sys.stderr)
                return 1
    publish_files(public_dir, snapshot, publication)
    print(
        f"published {snapshot['snapshot_id']} US {sessions['US'].isoformat()} JP {sessions['JP'].isoformat()} "
        f"{dated_snapshot_name(sessions['US'].isoformat(), sessions['JP'].isoformat())}"
    )
    return 0


def _write_inputs(work: Path, rows: dict[str, list[PricedSymbol]]) -> None:
    ohlcv = []
    universe = []
    caps = []
    for market in ("US", "JP"):
        for item in rows[market]:
            for bar in item.bars:
                if bar.date > item.bars[-1].date:
                    raise UpdateError(f"拒絕寫入未來行情 {bar.symbol} {bar.date}")
                ohlcv.append(
                    {
                        "market": bar.market,
                        "symbol": bar.symbol,
                        "date": bar.date,
                        "open": _num(bar.open),
                        "high": _num(bar.high),
                        "low": _num(bar.low),
                        "close": _num(bar.close),
                        "volume": _num(bar.volume),
                        "adjustment": bar.adjustment,
                    }
                )
            cap = "" if item.market_cap is None else _num(item.market_cap)
            universe.append(
                {
                    "market": item.market,
                    "symbol": item.symbol,
                    "name": item.name,
                    "security_type": "stock",
                    "listing_segment": "primary",
                    "exchange": item.exchange,
                    "currency": item.currency,
                    "cohort": "",
                    "market_cap": cap,
                    "market_cap_currency": item.market_cap_currency,
                    "market_cap_as_of": item.market_cap_as_of,
                    "group": "",
                }
            )
            if cap:
                caps.append(
                    {
                        "market": item.market,
                        "symbol": item.symbol,
                        "market_cap": cap,
                        "market_cap_currency": item.market_cap_currency,
                        "market_cap_as_of": item.market_cap_as_of,
                    }
                )
    write_csv(
        work / "ohlcv.csv",
        ["market", "symbol", "date", "open", "high", "low", "close", "volume", "adjustment"],
        ohlcv,
    )
    write_csv(
        work / "universe.csv",
        [
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
        ],
        universe,
    )
    write_csv(
        work / "marketcap.csv",
        ["market", "symbol", "market_cap", "market_cap_currency", "market_cap_as_of"],
        caps,
    )


def _num(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return format(value, ".10g")


def fetch_markets(expected: dict[str, date], cache_dir: Path) -> LoadedMarkets:
    client = YahooClient()
    nasdaq = client.request_public(NASDAQ_LISTED_URL).decode("utf-8", "replace")
    other = client.request_public(OTHER_LISTED_URL).decode("utf-8", "replace")
    us_rows, us_info = parse_us_listings(nasdaq, other)
    jpx = client.request_public(JPX_URL)
    jp_rows, jp_info = parse_jpx(jpx)
    if not us_rows or not jp_rows:
        raise UpdateError("美股或日股名單是空的，保留原快照")
    symbols = {
        "US": [_loaded(row) for row in us_rows],
        "JP": [_loaded(row) for row in jp_rows],
    }
    for market, items in symbols.items():
        _fetch_charts(client, items, expected[market], cache_dir / "charts")
        _fetch_quotes(client, items)
    notes = {
        "US": (
            f"美股名單來自 Nasdaq Trader nasdaqlisted.txt 與 otherlisted.txt"
            f"（{us_info['nasdaq_footer']} / {us_info['other_footer']}）。"
            f"收錄名稱列明普通股的 {us_info['rows']} 檔，已排除 ETF、權證、優先股和存託憑證。"
            "不是完整美股市場。ADR 與 OTC 未決議，沒有猜進名單。"
        ),
        "JP": (
            "日股名單來自 JPX 英文 data_e.xlsx 的 TOPIX Core30、Large70、Mid400 國內股，"
            f"共 {jp_info['rows']} 檔，有效日 {jp_info['effective_dates']}。"
            "指數名稱只用來定名單，不用作族群；族群按市值門檻。不是完整日股市場。"
        ),
    }
    print(f"名單 美國 {len(symbols['US'])} 日本 {len(symbols['JP'])}", flush=True)
    return LoadedMarkets(symbols=symbols, notes=notes)


def _loaded(row: dict) -> LoadedSymbol:
    return LoadedSymbol(
        market=row["market"],
        symbol=row["symbol"],
        yahoo=row["yahoo"],
        name=row["name"],
        exchange=row["exchange"],
        currency=row["currency"],
        extra={key: value for key, value in row.items() if key == "index_series"},
    )


def _fetch_charts(client: YahooClient, items: list[LoadedSymbol], through: date, cache: Path) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    start = through - timedelta(days=LOOKBACK_DAYS)
    end = through + timedelta(days=1)
    pending = []
    for item in items:
        path = cache / f"{item.yahoo}-{through.isoformat()}.json"
        if path.exists() and path.stat().st_size > 0:
            try:
                item.payload = json.loads(path.read_text(encoding="utf-8"))
                continue
            except json.JSONDecodeError:
                path.unlink(missing_ok=True)
        pending.append((item, path))
    print(f"{items[0].market if items else ''} 下載行情 {len(pending)}，沿用快取 {len(items) - len(pending)}", flush=True)

    def one(job: tuple[LoadedSymbol, Path]) -> None:
        item, path = job
        try:
            payload = client.chart(item.yahoo, start, end)
        except Exception as exc:  # noqa: BLE001 — one symbol must not abort the others before the coverage check
            item.error = str(exc)
            return
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(temporary, path)
        item.payload = payload

    done = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        for _job in pool.map(one, pending):
            done += 1
            if done % 250 == 0 or done == len(pending):
                print(f"行情進度 {done}/{len(pending)}", flush=True)


def _fetch_quotes(client: YahooClient, items: list[LoadedSymbol]) -> None:
    wanted = [item for item in items if item.payload is not None and not item.error]
    found: dict[str, dict] = {}
    for offset in range(0, len(wanted), 40):
        batch = wanted[offset : offset + 40]
        try:
            for row in client.quotes([item.yahoo for item in batch]):
                symbol = row.get("symbol")
                if symbol:
                    found[symbol] = row
        except Exception as exc:  # noqa: BLE001 — missing quotes leave caps empty; the cap floor can reject the run
            print(f"報價批次失敗 {batch[0].yahoo}：{exc}", flush=True)
    for item in wanted:
        item.quote = found.get(item.yahoo)


def _run_check(now: datetime) -> int:
    cut = session_cut(now)
    expected = expected_sessions(cut)
    print(
        f"檢查 {ROUTINE_ID} 香港切點 {cut.isoformat()} 預期美國 {expected['US'].isoformat()} 日本 {expected['JP'].isoformat()}",
        flush=True,
    )
    client = YahooClient()
    config = EngineConfig()
    try:
        nasdaq = client.request_public(NASDAQ_LISTED_URL).decode("utf-8", "replace")
        other = client.request_public(OTHER_LISTED_URL).decode("utf-8", "replace")
        us_rows, _info = parse_us_listings(nasdaq, other)
        jp_rows, _jp_info = parse_jpx(client.request_public(JPX_URL))
    except Exception as exc:  # noqa: BLE001
        print(f"failed: 名單下載失敗：{exc}", file=sys.stderr)
        return 1
    if not us_rows or not jp_rows:
        print("failed: 名單是空的", file=sys.stderr)
        return 1
    print(f"名單 美國 {len(us_rows)} 日本 {len(jp_rows)}", flush=True)
    for market, yahoo in (("US", "AAPL"), ("JP", "7203.T")):
        through = expected[market]
        try:
            payload = client.chart(yahoo, through - timedelta(days=LOOKBACK_DAYS), through + timedelta(days=1))
            bars, info = transform_chart(
                yahoo,
                payload,
                market,
                through=through,
                lookback_start=through - timedelta(days=LOOKBACK_DAYS),
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"failed: {yahoo} {exc}", file=sys.stderr)
            return 1
        if bars is None or date.fromisoformat(bars[-1].date) > through:
            print(f"failed: {yahoo} {info}", file=sys.stderr)
            return 1
        print(f"check {yahoo} last {bars[-1].date} bars {len(bars)}", flush=True)
    try:
        quotes = client.quotes(["AAPL", "7203.T"])
    except Exception as exc:  # noqa: BLE001
        print(f"failed: 報價失敗：{exc}", file=sys.stderr)
        return 1
    print(f"check quotes {len(quotes)}", flush=True)
    print("check-ok")
    return 0


def main_update(args) -> int:
    now = datetime.now(timezone.utc)
    if args.check:
        return _run_check(now)
    return run_update(
        public_dir=Path(args.public_dir),
        config_path=Path(args.config),
        cache_dir=Path(args.cache_dir),
        now=now,
    )
