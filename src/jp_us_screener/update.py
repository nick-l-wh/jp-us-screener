"""Fetch the latest completed US and JP sessions, screen them, and publish.

Entrypoint, from the repository root:

    python -m jp_us_screener update

automation-1795319781957 starts that command on Monday–Saturday around 08:00
Hong Kong (UTC+8, no US daylight-saving shift), inside 07:45–08:15. This module
does not register a timer. It writes web/public/data only after a real snapshot
validates. An unchanged session, a holiday carried forward, or any failure
leaves the previous public files in place. Full price history stays under the
gitignored data/ directory. Each run sends at most 100 Yahoo HTTP requests
(charts, quotes, and crumb combined). Cached charts that already cover the
lookback are reused; a tight budget downloads a rotating page of stale or
missing symbols and leaves the rest on disk. Every Yahoo request waits 1.5
seconds. HTTP 429 still backs off and stops the remaining downloads. Saved
share counts are kept.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jp_us_screener.config import EngineConfig, load_config
from jp_us_screener.fetchbudget import (
    COMMITTED_SHARES,
    CRUMB_HTTP,
    QUOTE_BATCH,
    YAHOO_HTTP_BUDGET,
    cap_coverage_note,
    chart_download_limits,
    chart_request_window,
    corporate_action_invalidates,
    coverage_dates,
    fill_missing_records,
    last_adjusted_close,
    load_share_records,
    merge_chart_payloads,
    quote_request_reserve,
    remember_quote,
    save_share_records,
    seed_records_from_inputs,
    seed_records_from_snapshot,
    select_chart_refresh,
    select_quote_refresh,
    usable_shares,
)
from jp_us_screener.io import load_dated_caps, load_ohlcv, load_universe, write_csv
from jp_us_screener.marketdata import (
    caps_problem,
    choose_session,
    coverage_problem,
    parse_jpx,
    parse_us_listings,
    resolve_cap,
    session_date,
    shares_from_quote,
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
from jp_us_screener.yahoo import YahooBudgetExceeded, YahooClient

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
    shares_outstanding: float | None = None
    shares_as_of: str = ""
    shares_source: str = ""
    shares_currency: str = ""
    dated_caps: dict[str, float] = field(default_factory=dict)


@dataclass
class LoadedMarkets:
    symbols: dict[str, list[LoadedSymbol]]
    notes: dict[str, str]
    stats: dict = field(default_factory=dict)


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
    shares_as_of: str = ""


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
            cap, status = resolve_cap(
                market=market,
                currency=item.currency,
                session=choice.session,
                close=close,
                quote=item.quote,
                zone=zone,
                shares=item.shares_outstanding,
                shares_currency=item.shares_currency or item.currency,
            )
            if status == "provider_etf":
                etf += 1
                continue
            if choice.session.isoformat() in item.dated_caps:
                cap = item.dated_caps[choice.session.isoformat()]
                status = "dated_csv"
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
                    shares_as_of=item.shares_as_of if status == "cached_shares_times_session_close" else "",
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
    for line in loaded.stats.get("lines") or []:
        print(line, flush=True)
    assembled = assemble(loaded, expected, config)
    for market, choice in assembled.choices.items():
        print(f"{market} {choice.state} {choice.detail}", flush=True)
    assembled.notes["caps"] = cap_coverage_note(assembled.rows)
    print(assembled.notes["caps"], flush=True)
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
    client = YahooClient(cooldown_path=cache_dir / "yahoo-cooldown.json")
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
    cap_path = cache_dir / "marketcap.csv"
    dated_caps = load_dated_caps(cap_path) if cap_path.exists() else {}
    quote_items = []
    for market, items in symbols.items():
        for item in items:
            item.dated_caps = dated_caps.get((market, item.symbol), {})
            if expected[market].isoformat() not in item.dated_caps:
                quote_items.append(item)
    store_paths = [COMMITTED_SHARES, cache_dir / "shares-outstanding.json"]
    records = load_share_records(store_paths)
    seeded: dict[str, dict] = {}
    published_path = Path("web/public/data/latest.json")
    if published_path.exists():
        try:
            seeded.update(seed_records_from_snapshot(json.loads(published_path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError as exc:
            print(f"已公布快照不能用來回推股本：{exc}", flush=True)
    seeded.update(seed_records_from_inputs(cache_dir / "inputs" / "ohlcv.csv", cache_dir / "inputs" / "marketcap.csv"))
    added = fill_missing_records(records, seeded)
    if added:
        save_share_records(store_paths, records)
        print(f"沿用已保存或回推的流通股數 {len(records)}，本輪補入 {added}", flush=True)
    plans = {market: _prepare_charts(items, expected[market], cache_dir / "charts") for market, items in symbols.items()}
    job_counts = {market: len(jobs) for market, (jobs, _reused) in plans.items()}
    missing_shares = sum(not usable_shares(records.get(f"{item.market}:{item.symbol}"), item.currency)
                         for item in quote_items)
    reserve = quote_request_reserve(
        missing_shares=missing_shares,
        listed=len(quote_items),
        chart_jobs=sum(job_counts.values()),
        budget=YAHOO_HTTP_BUDGET,
    )
    limits = chart_download_limits(job_counts, max(0, YAHOO_HTTP_BUDGET - reserve))
    print(
        f"Yahoo 上限 {YAHOO_HTTP_BUDGET}，預留報價 {reserve}，"
        f"日線待補 美國 {job_counts.get('US', 0)} 日本 {job_counts.get('JP', 0)}，"
        f"本輪下載 美國 {limits['US']} 日本 {limits['JP']}",
        flush=True,
    )
    stop = threading.Event()
    stop_reason = ["行情下載已因 429 停止"]
    for market in ("US", "JP"):
        jobs, reused = plans[market]
        _download_chart_jobs(
            client,
            jobs,
            expected[market],
            reused=reused,
            limit=limits[market],
            stop=stop,
            stop_reason=stop_reason,
        )
    closes: dict[tuple[str, str], float] = {}
    for items in symbols.values():
        for item in items:
            if item.payload is None or item.error:
                continue
            close = last_adjusted_close(item.payload, market=item.market, through=expected[item.market])
            if close is not None:
                closes[(item.market, item.symbol)] = close
    quote_stats = _fetch_quotes(
        client,
        quote_items,
        records,
        closes,
        expected,
        store_paths,
        request_budget=client.yahoo_remaining,
        halt_reason=stop_reason[0] if stop.is_set() else None,
    )
    _attach_shares(symbols, records)
    lines = [
        (
            f"Yahoo HTTP chart {client.chart_http} quote {client.quote_http} crumb {client.crumb_http} "
            f"合計 {client.yahoo_http} 上限 {YAHOO_HTTP_BUDGET}"
        ),
        (
            f"報價批次 {quote_stats['batches']}，收到 {quote_stats['received']}，"
            f"刷新名單 {quote_stats['refresh']}，停止={quote_stats['stopped']}"
        ),
    ]
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
    return LoadedMarkets(symbols=symbols, notes=notes, stats={"lines": lines})


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


def _fetch_charts(
    client: YahooClient,
    items: list[LoadedSymbol],
    through: date,
    cache: Path,
    *,
    workers: int = 1,
    limit: int | None = None,
    stop: threading.Event | None = None,
    stop_reason: list[str] | None = None,
) -> None:
    del workers  # Yahoo calls are paced one at a time; a worker pool would overlap them.
    jobs, reused = _prepare_charts(items, through, cache)
    _download_chart_jobs(
        client,
        jobs,
        through,
        reused=reused,
        limit=limit,
        stop=stop,
        stop_reason=stop_reason,
    )


def _prepare_charts(
    items: list[LoadedSymbol],
    through: date,
    cache: Path,
) -> tuple[list[tuple[LoadedSymbol, Path, dict | None, date, date]], int]:
    """Reuse a chart that already covers the lookback. Return only the gaps and misses."""

    cache.mkdir(parents=True, exist_ok=True)
    lookback = through - timedelta(days=LOOKBACK_DAYS)
    pending = []
    reused = 0
    for item in items:
        path = cache / f"{item.yahoo}.json"
        cached = _read_chart_cache(path)
        if cached is None:
            pending.append((item, path, None, lookback, through + timedelta(days=1)))
            continue
        window = chart_request_window(
            cached_first=cached["first"],
            cached_last=cached["last"],
            lookback_start=lookback,
            through=through,
        )
        if window is None:
            item.payload = cached["payload"]
            reused += 1
            continue
        pending.append((item, path, cached, window[0], window[1]))
    return pending, reused


def _download_chart_jobs(
    client: YahooClient,
    jobs: list[tuple[LoadedSymbol, Path, dict | None, date, date]],
    through: date,
    *,
    reused: int,
    limit: int | None,
    stop: threading.Event | None,
    stop_reason: list[str] | None,
) -> None:
    """Download `limit` stale or missing charts. Leave every other symbol on its cache."""

    if stop is None:
        stop = threading.Event()
    if stop_reason is None:
        stop_reason = ["行情下載已因 429 停止"]
    market = jobs[0][0].market if jobs else ""
    lookback = through - timedelta(days=LOOKBACK_DAYS)
    chosen_ids: set[int] | None = None
    if limit is not None and len(jobs) > limit:
        chosen = select_chart_refresh([job[0] for job in jobs], session=through, limit=limit)
        chosen_ids = {id(item) for item in chosen}
        order = {id(item): index for index, item in enumerate(chosen)}
        ordered = [job for job in jobs if id(job[0]) in chosen_ids]
        ordered.sort(key=lambda job: order[id(job[0])])
    else:
        ordered = list(jobs)
    left = 0
    skipped = 0
    for job in jobs:
        if chosen_ids is not None and id(job[0]) in chosen_ids:
            continue
        if chosen_ids is None:
            continue
        item, _path, cached, _start, _end = job
        if cached is not None:
            item.payload = cached["payload"]
            item.error = None
            left += 1
        else:
            item.error = "本輪 Yahoo 請求上限，沒有已保存行情"
            skipped += 1
    print(
        f"{market} 下載行情 {len(ordered)}，沿用完整快取 {reused}，"
        f"留在較舊快取 {left}，沒有快取而本輪不下載 {skipped}",
        flush=True,
    )

    def halted_message() -> str:
        return stop_reason[0]

    def keep_cache(item: LoadedSymbol, cached: dict | None, message: str) -> None:
        if cached is not None:
            item.payload = cached["payload"]
            item.error = None
        else:
            item.error = message

    def one(job: tuple[LoadedSymbol, Path, dict | None, date, date]) -> None:
        item, path, cached, start, end = job
        if stop.is_set():
            keep_cache(item, cached, halted_message())
            return
        remaining = getattr(client, "yahoo_remaining", None)
        if remaining is not None and remaining <= 0:
            stop_reason[0] = "行情下載已因本輪 Yahoo 上限停止"
            stop.set()
            keep_cache(item, cached, halted_message())
            return
        try:
            downloaded = client.chart(item.yahoo, start, end)
        except YahooBudgetExceeded:
            stop_reason[0] = "行情下載已因本輪 Yahoo 上限停止"
            stop.set()
            keep_cache(item, cached, halted_message())
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                stop_reason[0] = "行情下載已因 429 停止"
                stop.set()
            if cached is not None:
                item.payload = cached["payload"]
                item.error = None
            else:
                item.error = "HTTP Error 429: Too Many Requests" if exc.code == 429 else str(exc)
            return
        except Exception as exc:  # noqa: BLE001 — one symbol must not abort the others before the coverage check
            item.error = str(exc)
            if cached is not None:
                item.payload = cached["payload"]
                item.error = None
            return
        full_start = lookback
        full_end = through + timedelta(days=1)
        if cached is not None and (start, end) != (full_start, full_end) and corporate_action_invalidates(cached["payload"], downloaded):
            if not stop.is_set():
                try:
                    downloaded = client.chart(item.yahoo, full_start, full_end)
                    cached = None
                except YahooBudgetExceeded:
                    stop_reason[0] = "行情下載已因本輪 Yahoo 上限停止"
                    stop.set()
                    item.payload = cached["payload"] if cached is not None else None
                    if item.payload is None:
                        item.error = halted_message()
                    return
                except urllib.error.HTTPError as exc:
                    if exc.code == 429:
                        stop_reason[0] = "行情下載已因 429 停止"
                        stop.set()
                    item.payload = cached["payload"] if cached is not None else None
                    if item.payload is None:
                        item.error = str(exc)
                    return
                except Exception:  # noqa: BLE001
                    item.payload = cached["payload"] if cached is not None else None
                    return
        if cached is not None:
            downloaded = merge_chart_payloads(cached["payload"], downloaded)
        _write_chart_cache(path, item, downloaded)
        item.payload = downloaded

    for index, job in enumerate(ordered, start=1):
        one(job)
        if index % 250 == 0 or index == len(ordered):
            print(f"行情進度 {index}/{len(ordered)}", flush=True)


def _read_chart_cache(path: Path) -> dict | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        first = date.fromisoformat(payload["first"])
        last = date.fromisoformat(payload["last"])
        chart = payload["payload"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        path.unlink(missing_ok=True)
        return None
    if not isinstance(chart, dict):
        return None
    return {"payload": chart, "first": first, "last": last}


def _write_chart_cache(path: Path, item: LoadedSymbol, payload: dict) -> None:
    first, last = coverage_dates(payload, item.market)
    if first is None or last is None:
        return
    document = {"version": 1, "first": first.isoformat(), "last": last.isoformat(), "payload": payload}
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    os.replace(temporary, path)


def _fetch_quotes(
    client: YahooClient,
    items: list[LoadedSymbol],
    records: dict[str, dict],
    closes: dict[tuple[str, str], float],
    expected: dict[str, date],
    store_paths: list[Path],
    *,
    request_budget: int | None = None,
    halt_reason: str | None = None,
) -> dict:
    stats = {"refresh": 0, "batches": 0, "stopped": False, "received": 0, "budget": False}
    if not items:
        return stats
    if halt_reason:
        stats["stopped"] = True
        print(f"{halt_reason}，略過 Yahoo 市值報價，使用有日期的市值檔及已保存股數", flush=True)
        return stats
    symbol_limit = None
    if request_budget is not None:
        if request_budget < CRUMB_HTTP + 1:
            stats["stopped"] = True
            stats["budget"] = True
            print("報價略過：剩餘 Yahoo 請求不夠取得 crumb 和報價，沿用已保存的流通股數", flush=True)
            return stats
        symbol_limit = (request_budget - CRUMB_HTTP) * QUOTE_BATCH
    refresh = select_quote_refresh(
        items,
        records,
        closes,
        session_by_market=expected,
        symbol_limit=symbol_limit,
    )
    stats["refresh"] = len(refresh)
    budget_label = "不限" if request_budget is None else str(request_budget)
    print(f"報價刷新 {len(refresh)} / {len(items)}，每批 {QUOTE_BATCH}，剩餘 Yahoo 請求 {budget_label}", flush=True)
    if not refresh:
        return stats
    try:
        client.ensure_crumb()
    except YahooBudgetExceeded as exc:
        stats["stopped"] = True
        stats["budget"] = True
        print(f"報價達到本輪 Yahoo 上限：{exc}", flush=True)
        return stats
    except urllib.error.HTTPError as exc:
        stats["stopped"] = True
        if exc.code == 429:
            print("報價遇到 HTTP 429，停止其餘報價請求，沿用已保存的流通股數", flush=True)
        else:
            print(f"報價 crumb 失敗：{exc}", flush=True)
        return stats
    except Exception as exc:  # noqa: BLE001 — quote failure must not discard cached shares or the price snapshot
        stats["stopped"] = True
        print(f"報價 crumb 失敗：{exc}", flush=True)
        return stats
    for offset in range(0, len(refresh), QUOTE_BATCH):
        remaining = getattr(client, "yahoo_remaining", None)
        if remaining is not None and remaining <= 0:
            stats["stopped"] = True
            stats["budget"] = True
            print("報價達到本輪 Yahoo 上限，停止其餘批次", flush=True)
            break
        batch = refresh[offset : offset + QUOTE_BATCH]
        try:
            rows = client.quotes([item.yahoo for item in batch])
        except YahooBudgetExceeded as exc:
            stats["stopped"] = True
            stats["budget"] = True
            print(f"報價達到本輪 Yahoo 上限：{exc}", flush=True)
            break
        except urllib.error.HTTPError as exc:
            stats["stopped"] = True
            print(f"報價批次失敗 {batch[0].yahoo}：{exc}", flush=True)
            if exc.code == 429:
                print("報價遇到 HTTP 429，停止其餘批次", flush=True)
            break
        except Exception as exc:  # noqa: BLE001
            stats["stopped"] = True
            print(f"報價批次失敗 {batch[0].yahoo}：{exc}", flush=True)
            break
        found: dict[str, dict] = {}
        for row in rows:
            symbol = row.get("symbol")
            if symbol:
                found[symbol] = row
        changed = False
        for item in batch:
            quote = found.get(item.yahoo)
            if not quote:
                continue
            item.quote = quote
            shares, _status = shares_from_quote(quote, item.currency)
            if shares is None:
                continue
            as_of = _quote_session(quote, item.market)
            remember_quote(records, item, quote, closes.get((item.market, item.symbol)), shares, as_of)
            stats["received"] += 1
            changed = True
        stats["batches"] += 1
        if changed:
            save_share_records(store_paths, records)
    return stats


def _quote_session(quote: dict, market: str) -> str:
    timestamp = quote.get("regularMarketTime")
    if not isinstance(timestamp, int):
        return ""
    zone = "Asia/Tokyo" if market == "JP" else "America/New_York"
    try:
        return session_date(timestamp, zone).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _attach_shares(symbols: dict[str, list[LoadedSymbol]], records: dict[str, dict]) -> None:
    for items in symbols.values():
        for item in items:
            record = records.get(f"{item.market}:{item.symbol}")
            if not record or record.get("currency") != item.currency:
                continue
            shares = record.get("shares_outstanding")
            if not isinstance(shares, (int, float)) or isinstance(shares, bool) or shares <= 0:
                continue
            item.shares_outstanding = float(shares)
            item.shares_as_of = str(record.get("shares_as_of") or "")
            item.shares_source = str(record.get("source") or "")
            item.shares_currency = str(record.get("currency") or "")


def _run_check(now: datetime, cache_dir: Path) -> int:
    cut = session_cut(now)
    expected = expected_sessions(cut)
    print(
        f"檢查 {ROUTINE_ID} 香港切點 {cut.isoformat()} 預期美國 {expected['US'].isoformat()} 日本 {expected['JP'].isoformat()}",
        flush=True,
    )
    client = YahooClient(cooldown_path=cache_dir / "yahoo-cooldown.json")
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
    cap_path = cache_dir / "marketcap.csv"
    dated_caps = load_dated_caps(cap_path) if cap_path.exists() else {}
    records = load_share_records([COMMITTED_SHARES, cache_dir / "shares-outstanding.json"])
    checks = []
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
        symbol = yahoo.removesuffix(".T")
        if bars[-1].date in dated_caps.get((market, symbol), {}):
            print(f"check {yahoo} 市值來源：當日 CSV", flush=True)
        else:
            checks.append((market, symbol, yahoo, bars[-1]))
    quotes = {}
    if checks:
        try:
            quotes = {row["symbol"]: row for row in client.quotes([entry[2] for entry in checks])}
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"check Yahoo 報價失敗：{exc}，檢查已保存股數", flush=True)
    for market, symbol, yahoo, bar in checks:
        record = records.get(f"{market}:{symbol}", {})
        currency = "USD" if market == "US" else "JPY"
        cap, status = resolve_cap(
            market=market, currency=currency, session=date.fromisoformat(bar.date), close=bar.close,
            quote=quotes.get(yahoo), zone="America/New_York" if market == "US" else "Asia/Tokyo",
            shares=record.get("shares_outstanding"), shares_currency=record.get("currency", ""),
        )
        if cap is None:
            print(f"failed: {yahoo} 欠缺可用市值：{status}", file=sys.stderr)
            return 1
        print(f"check {yahoo} 市值來源：{status}", flush=True)
    print("check-ok")
    return 0


def main_update(args) -> int:
    now = datetime.now(timezone.utc)
    if args.check:
        return _run_check(now, Path(args.cache_dir))
    return run_update(
        public_dir=Path(args.public_dir),
        config_path=Path(args.config),
        cache_dir=Path(args.cache_dir),
        now=now,
    )
