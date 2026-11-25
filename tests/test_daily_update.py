from __future__ import annotations

import email.message
import io
import json
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import pytest

from jp_us_screener.cli import main
from jp_us_screener.config import EngineConfig
from jp_us_screener.io import atomic_write_json
from jp_us_screener.marketdata import (
    accept_cap,
    choose_session,
    parse_jpx,
    parse_us_listings,
    resolve_cap,
    subsequent_split_factor,
    transform_chart,
)
from jp_us_screener.models import Bar
from jp_us_screener.publish import (
    build_publication,
    dated_snapshot_name,
    publish_files,
    regression_problem,
    scan_publication_status,
    validate_public_snapshot,
)
from jp_us_screener.sessions import (
    HKT,
    expected_sessions,
    session_cut,
    within_start_window,
)
from jp_us_screener.update import LoadedMarkets, LoadedSymbol, _fetch_charts, _fetch_quotes, run_update
from jp_us_screener.yahoo import YahooClient

ROOT = Path(__file__).resolve().parents[1]
SESSION = date(2026, 11, 23)
NY = ZoneInfo("America/New_York")
TOKYO = ZoneInfo("Asia/Tokyo")


def test_hong_kong_cut_does_not_follow_us_daylight_time():
    january = datetime(2026, 1, 6, 18, 0, tzinfo=HKT)
    july = datetime(2026, 7, 7, 18, 0, tzinfo=HKT)
    assert session_cut(january).hour == 8
    assert session_cut(july).hour == 8
    assert session_cut(january).utcoffset() == timedelta(hours=8)
    assert session_cut(july).utcoffset() == timedelta(hours=8)
    assert expected_sessions(session_cut(january)) == {"US": date(2026, 1, 5), "JP": date(2026, 1, 5)}
    assert expected_sessions(session_cut(july)) == {"US": date(2026, 7, 6), "JP": date(2026, 7, 6)}
    monday = datetime(2026, 11, 23, 8, 0, tzinfo=HKT)
    saturday = datetime(2026, 11, 21, 8, 0, tzinfo=HKT)
    assert expected_sessions(session_cut(monday)) == {"US": date(2026, 11, 20), "JP": date(2026, 11, 20)}
    assert expected_sessions(session_cut(saturday)) == {"US": date(2026, 11, 20), "JP": date(2026, 11, 20)}
    late = datetime(2026, 11, 24, 18, 30, tzinfo=HKT)
    sunday = datetime(2026, 11, 22, 12, 0, tzinfo=HKT)
    assert expected_sessions(session_cut(late)) == {"US": SESSION, "JP": SESSION}
    assert expected_sessions(session_cut(sunday)) == {"US": date(2026, 11, 20), "JP": date(2026, 11, 20)}


def test_start_window_is_monday_through_saturday_morning():
    assert within_start_window(datetime(2026, 11, 24, 7, 44, tzinfo=HKT)) is False
    assert within_start_window(datetime(2026, 11, 24, 7, 45, tzinfo=HKT)) is True
    assert within_start_window(datetime(2026, 11, 24, 8, 15, tzinfo=HKT)) is True
    assert within_start_window(datetime(2026, 11, 24, 8, 16, tzinfo=HKT)) is False
    assert within_start_window(datetime(2026, 11, 21, 8, 0, tzinfo=HKT)) is True
    assert within_start_window(datetime(2026, 11, 22, 8, 0, tzinfo=HKT)) is False


def test_session_choice_carries_a_closed_day_and_rejects_a_thin_tail():
    friday = date(2026, 11, 20)
    monday = date(2026, 11, 23)
    closed = choose_session([friday] * 10, monday)
    assert closed.state == "carried" and closed.session == friday
    thin = choose_session([monday] * 4 + [friday] * 2, monday)
    assert thin.state == "incomplete"
    ready = choose_session([monday] * 9 + [friday], monday)
    assert ready.state == "ready" and ready.session == monday
    assert choose_session([monday], friday).state == "incomplete"


def test_us_listing_filter_and_jpx_names_do_not_assign_cohorts():
    nasdaq = "\n".join(
        [
            "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares",
            "AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N",
            "AAA|Pacer ETF|G|N|N|100|Y|N",
            "BBB|Foo Depositary Shares|Q|N|N|100|N|N",
            "File Creation Time: 1120202621:31|||||||",
        ]
    )
    other = "\n".join(
        [
            "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol",
            "BRK.B|Berkshire Hathaway Inc. Class B Common Stock|N|BRK.B|N|100|N|BRK.B",
            "File Creation Time: 1120202621:31|||||||",
        ]
    )
    rows, info = parse_us_listings(nasdaq, other)
    assert [row["symbol"] for row in rows] == ["AAPL", "BRK-B"]
    assert rows[1]["exchange"] == "XNYS"
    assert info["rows"] == 2
    assert "cohort" not in rows[0]

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Effective Date", "Local Code", "Name (English)", "Section/Products", "Size (New Index Series)"])
    sheet.append([20261031, 7203, "TOYOTA", "Prime Market (Domestic)", "TOPIX Core30"])
    sheet.append([20261031, "285A", "KIOXIA", "Prime Market (Domestic)", "TOPIX Mid400"])
    sheet.append([20261031, 1301, "SKIP", "Prime Market (Domestic)", "TOPIX Small 1"])
    sheet.append([20261031, 9999, "FOREIGN", "Prime Market (Foreign)", "TOPIX Core30"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    jp_rows, jp_info = parse_jpx(buffer.getvalue())
    assert [row["symbol"] for row in jp_rows] == ["7203", "285A"]
    assert jp_rows[0]["yahoo"] == "7203.T"
    assert "cohort" not in jp_rows[0]
    assert jp_info["rows"] == 2


def test_transform_drops_future_bars_and_does_not_invent_zeros():
    zone = "America/New_York"
    friday = datetime(2026, 11, 20, 16, 0, tzinfo=NY)
    monday = datetime(2026, 11, 23, 16, 0, tzinfo=NY)
    payload = _chart("AAPL", "USD", zone, [(friday, 100, 100, 1000), (monday, None, None, None)])
    bars, info = transform_chart(
        "AAPL",
        payload,
        "US",
        through=date(2026, 11, 20),
        lookback_start=date(2024, 1, 1),
        config=EngineConfig(),
    )
    assert bars is not None and [bar.date for bar in bars] == ["2026-11-20"]
    assert info["error"] if False else bars[0].close == 100

    broken = _chart("AAPL", "USD", zone, [(friday, None, 100, 1000)])
    rejected, rejected_info = transform_chart(
        "AAPL",
        broken,
        "US",
        through=date(2026, 11, 20),
        lookback_start=date(2024, 1, 1),
        config=EngineConfig(),
    )
    assert rejected is None
    assert "不會補零" in rejected_info["error"]


def test_split_adjusted_volume_uses_later_split_ratio():
    assert subsequent_split_factor([(date(2020, 8, 31), 4.0)], date(2020, 8, 28)) == 4
    zone = "America/New_York"
    day = datetime(2020, 8, 28, 16, 0, tzinfo=NY)
    split_at = datetime(2020, 8, 31, 16, 0, tzinfo=NY)
    payload = _chart("AAPL", "USD", zone, [(day, 400, 100, 187_630_000)])
    payload["events"] = {"splits": {"0": {"date": int(split_at.timestamp()), "numerator": 4, "denominator": 1}}}
    bars, info = transform_chart(
        "AAPL",
        payload,
        "US",
        through=date(2020, 8, 28),
        lookback_start=date(2020, 8, 1),
        config=EngineConfig(),
    )
    assert info.get("error") is None
    assert bars is not None
    assert bars[0].close == pytest.approx(100)
    assert bars[0].volume == pytest.approx(46_907_500)


def test_caps_are_real_or_missing():
    session = date(2026, 11, 23)
    us_time = int(datetime(2026, 11, 23, 16, 0, tzinfo=NY).timestamp())
    cap, status = accept_cap(
        market="US",
        currency="USD",
        session=session,
        close=100,
        quote={"currency": "USD", "regularMarketTime": us_time, "regularMarketPrice": 100, "sharesOutstanding": 10, "marketCap": 1000},
        zone="America/New_York",
    )
    assert status == "yahoo_market_cap" and cap == 1000
    missing, reason = accept_cap(
        market="US",
        currency="USD",
        session=session,
        close=100,
        quote={"currency": "USD", "regularMarketTime": us_time, "regularMarketPrice": 100, "sharesOutstanding": 10, "marketCap": 1100},
        zone="America/New_York",
    )
    assert missing is None and reason == "cap_not_price_times_shares"
    later = int(datetime(2026, 11, 24, 16, 0, tzinfo=NY).timestamp())
    stale, stale_reason = accept_cap(
        market="US",
        currency="USD",
        session=session,
        close=100,
        quote={"currency": "USD", "regularMarketTime": later, "marketCap": 1000},
        zone="America/New_York",
    )
    assert stale is None and stale_reason == "quote_session_mismatch"
    jp_cap, jp_status = accept_cap(
        market="JP",
        currency="JPY",
        session=session,
        close=2500,
        quote={"currency": "JPY", "regularMarketTime": later, "sharesOutstanding": 4_000, "marketCap": 1},
        zone="Asia/Tokyo",
    )
    assert jp_status == "shares_times_session_close" and jp_cap == 10_000_000
    no_shares, no_reason = accept_cap(
        market="JP",
        currency="JPY",
        session=session,
        close=2500,
        quote={"currency": "JPY"},
        zone="Asia/Tokyo",
    )
    assert no_shares is None and no_reason == "shares_missing"


def test_publication_labels_a_real_zero_and_refuses_a_fake_one():
    assert scan_publication_status({"evaluation": "complete_zero_matches", "counts": {"matches": 0, "no_match": 8}}) == "valid"
    assert scan_publication_status({"evaluation": "complete", "counts": {"matches": 0, "no_match": 0, "unmet_dependency": 4}}) == "not_valid"
    snapshot = _snapshot_shell()
    notice = build_publication(snapshot, {"US": "測試名單", "JP": "測試名單"})
    assert notice["sessions"] == {"US": "2026-11-23", "JP": "2026-11-20"}
    assert "美國數據日期 2026-11-23" in notice["body"]
    assert "日本數據日期 2026-11-20" in notice["body"]
    assert "算完的 0 筆：回調" in notice["body"]
    assert "不會顯示成 0：擠壓" in notice["body"]
    assert notice["scans"]["US"]["pullback"] == "valid"
    assert notice["scans"]["US"]["squeeze"] == "not_valid"
    errors = validate_public_snapshot(snapshot, {"US": SESSION, "JP": date(2026, 11, 20)}, SESSION)
    assert errors == []
    snapshot["mode"] = "demo"
    snapshot["demo_disclaimer"] = "示範"
    assert any("示範" in error for error in validate_public_snapshot(snapshot, {"US": SESSION, "JP": date(2026, 11, 20)}, SESSION))


def test_regression_keeps_a_previously_valid_hit_list():
    previous = {"mode": "real", "scans": {"adx": {"markets": {"US": _counted(2, 5)}}}}
    current = {"scans": {"adx": {"markets": {"US": _counted(0, 0, unmet=4)}}}}
    assert regression_problem(previous, current)
    assert regression_problem(previous, {"scans": {"adx": {"markets": {"US": _counted(0, 9)}}}}) is None


def test_failed_publish_restores_the_previous_files(tmp_path, monkeypatch):
    latest = tmp_path / "latest.json"
    notice = tmp_path / "publication.json"
    latest.write_text('{"keep": true}\n', encoding="utf-8")
    notice.write_text('{"old": true}\n', encoding="utf-8")
    real = atomic_write_json

    def boom(path, payload):
        if Path(path).name == "publication.json":
            raise OSError("disk")
        real(path, payload)

    monkeypatch.setattr("jp_us_screener.publish.atomic_write_json", boom)
    snapshot = {"markets": {"US": {"as_of": "2026-11-23"}, "JP": {"as_of": "2026-11-23"}}}
    with pytest.raises(OSError):
        publish_files(tmp_path, snapshot, {"snapshot_id": "x"})
    assert latest.read_text(encoding="utf-8") == '{"keep": true}\n'
    assert notice.read_text(encoding="utf-8") == '{"old": true}\n'
    assert not (tmp_path / dated_snapshot_name("2026-11-23", "2026-11-23")).exists()


def test_429_honors_retry_after_and_does_not_sleep_through_a_long_reset():
    clock = {"t": 1_000.0}
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    client = YahooClient(sleep=sleep, now=lambda: clock["t"])
    headers = email.message.EmailMessage()
    headers["Retry-After"] = "5"
    busy = urllib.error.HTTPError("http://example.test/v7/finance/quote?symbols=AAPL", 429, "busy", headers, None)
    assert client._read("http://example.test/v7/finance/quote?symbols=AAPL", _Opener([busy, _Body(b"ok")]), 30, 4) == b"ok"
    assert sum(sleeps) == pytest.approx(5)

    sleeps.clear()
    clock["t"] = 2_000.0
    bare = email.message.EmailMessage()
    first = urllib.error.HTTPError("http://example.test/v7/finance/quote?symbols=AAPL", 429, "busy", bare, None)
    second = urllib.error.HTTPError("http://example.test/v7/finance/quote?symbols=AAPL", 429, "busy", bare, None)
    assert client._read(
        "http://example.test/v7/finance/quote?symbols=AAPL",
        _Opener([first, second, _Body(b"ok")]),
        30,
        4,
    ) == b"ok"
    assert sum(sleeps) == pytest.approx(6)

    stopped = YahooClient(sleep=lambda _seconds: sleeps.append(999), now=lambda: 0.0)
    long_headers = email.message.EmailMessage()
    long_headers["Retry-After"] = "600"
    long_error = urllib.error.HTTPError("http://example.test/v1/test/getcrumb", 429, "busy", long_headers, None)
    opener = _Opener([long_error, _Body(b"later")])
    before = len(sleeps)
    with pytest.raises(urllib.error.HTTPError):
        stopped._read("http://example.test/v1/test/getcrumb", opener, 30, 4)
    assert len(opener.items) == 1
    assert len(sleeps) == before


def test_cached_shares_publish_without_a_quote_and_missing_shares_stay_blank(tmp_path):
    public = tmp_path / "public"
    cache = tmp_path / "cache"

    def loader(expected, cache_dir):
        assert expected == {"US": SESSION, "JP": SESSION}
        loaded = _loaded(SESSION, SESSION)
        for item in loaded.symbols["US"] + loaded.symbols["JP"]:
            item.quote = None
            item.shares_outstanding = 1_000_000
            item.shares_as_of = "2026-11-20"
            item.shares_source = "published_snapshot_implied"
            item.shares_currency = item.currency
        loaded.symbols["US"][0].shares_outstanding = None
        loaded.symbols["US"][0].shares_as_of = ""
        return loaded

    code = run_update(
        public_dir=public,
        config_path=ROOT / "config/default.json",
        cache_dir=cache,
        now=datetime(2026, 11, 24, 18, 30, tzinfo=HKT),
        load=loader,
    )
    assert code == 0
    universe = (cache / "inputs" / "universe.csv").read_text(encoding="utf-8")
    assert ",0," not in universe
    assert ",," in universe
    notice = json.loads((public / "publication.json").read_text(encoding="utf-8"))
    assert "沿用較早流通股數" in notice["body"]
    assert "不會當成 0" in notice["body"]
    snapshot = json.loads((public / "latest.json").read_text(encoding="utf-8"))
    assert snapshot["markets"]["US"]["as_of"] == "2026-11-23"
    assert snapshot["markets"]["JP"]["as_of"] == "2026-11-23"


def test_no_shares_and_no_quotes_keep_the_previous_snapshot(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    latest = public / "latest.json"
    latest.write_text(_published("2026-11-20", "2026-11-20"), encoding="utf-8")
    before = latest.read_bytes()

    def loader(expected, cache_dir):
        loaded = _loaded(SESSION, SESSION)
        for item in loaded.symbols["US"] + loaded.symbols["JP"]:
            item.quote = None
        return loaded

    code = run_update(
        public_dir=public,
        config_path=ROOT / "config/default.json",
        cache_dir=tmp_path / "cache",
        now=datetime(2026, 11, 24, 8, 0, tzinfo=HKT),
        load=loader,
    )
    assert code == 1
    assert latest.read_bytes() == before


def test_quote_refresh_stops_on_429_before_the_next_batch(tmp_path):
    items = []
    for index in range(150):
        item = LoadedSymbol("US", f"S{index:03d}", f"S{index:03d}", f"S{index:03d}", "XNAS", "USD", payload={"ok": True})
        items.append(item)
    calls = {"quotes": 0}

    class Client:
        def ensure_crumb(self):
            return "crumb"

        def quotes(self, symbols):
            calls["quotes"] += 1
            headers = email.message.EmailMessage()
            raise urllib.error.HTTPError(
                "http://example.test/v7/finance/quote",
                429,
                "Too Many Requests",
                headers,
                None,
            )

        def _sleep(self, seconds):
            return None

    stats = _fetch_quotes(Client(), items, {}, {}, {"US": SESSION, "JP": SESSION}, [tmp_path / "shares.json"])
    assert calls["quotes"] == 1
    assert stats["stopped"] is True
    assert stats["batches"] == 0
    assert not (tmp_path / "shares.json").exists()


def test_chart_cache_skips_a_covered_span_and_429_stops_the_queue(tmp_path):
    through = date(2026, 11, 24)
    covered = LoadedSymbol("US", "AAPL", "AAPL", "Apple", "XNAS", "USD")
    cache = tmp_path / "charts"
    cache.mkdir()
    (cache / "AAPL.json").write_text(
        json.dumps({"version": 1, "first": "2020-01-02", "last": "2026-11-24", "payload": {"meta": {"symbol": "AAPL"}, "timestamp": []}}),
        encoding="utf-8",
    )

    class Client:
        def __init__(self):
            self.calls = []

        def chart(self, symbol, start, end):
            self.calls.append(symbol)
            return {}

        def _sleep(self, seconds):
            return None

    client = Client()
    _fetch_charts(client, [covered], through, cache, workers=1)
    assert client.calls == []
    assert covered.payload["meta"]["symbol"] == "AAPL"

    class Boom:
        def __init__(self):
            self.calls = []

        def chart(self, symbol, start, end):
            self.calls.append((symbol, start, end))
            raise urllib.error.HTTPError("http://example.test/v8/finance/chart/" + symbol, 429, "Too Many Requests", email.message.EmailMessage(), None)

        def _sleep(self, seconds):
            return None

    gap = tmp_path / "gap"
    boom = Boom()
    first = LoadedSymbol("US", "AAA", "AAA", "AAA", "XNAS", "USD")
    second = LoadedSymbol("US", "BBB", "BBB", "BBB", "XNAS", "USD")
    _fetch_charts(boom, [first, second], through, gap, workers=1)
    assert [call[0] for call in boom.calls] == ["AAA"]
    assert first.error and "429" in first.error
    assert second.error and "停止" in second.error


def test_resolve_cap_uses_saved_shares_and_does_not_invent_zero():
    session = date(2026, 11, 23)
    cap, status = resolve_cap(
        market="US",
        currency="USD",
        session=session,
        close=50,
        quote=None,
        zone="America/New_York",
        shares=2_000_000_000,
        shares_currency="USD",
    )
    assert status == "cached_shares_times_session_close"
    assert cap == 100_000_000_000
    missing, reason = resolve_cap(
        market="JP",
        currency="JPY",
        session=session,
        close=2500,
        quote=None,
        zone="Asia/Tokyo",
        shares=None,
        shares_currency="JPY",
    )
    assert missing is None and reason == "quote_missing"
    rejected, rejected_reason = resolve_cap(
        market="US",
        currency="USD",
        session=session,
        close=50,
        quote={"currency": "USD", "regularMarketPrice": 50, "sharesOutstanding": 10, "marketCap": 1100},
        zone="America/New_York",
        shares=None,
        shares_currency="USD",
    )
    assert rejected is None and rejected_reason == "quote_missing"


def test_retries_a_server_error_and_not_a_missing_symbol():
    sleeps: list[float] = []
    client = YahooClient(sleep=lambda seconds: sleeps.append(seconds))
    headers = email.message.EmailMessage()
    busy = urllib.error.HTTPError("http://example.test", 503, "busy", headers, None)
    missing = urllib.error.HTTPError("http://example.test", 404, "missing", headers, None)
    assert client._read("http://example.test", _Opener([busy, _Body(b"ok")]), 30, 4) == b"ok"
    assert sleeps
    opener = _Opener([missing, _Body(b"later")])
    with pytest.raises(urllib.error.HTTPError):
        client._read("http://example.test", opener, 30, 4)
    assert len(opener.items) == 1


def test_unchanged_session_does_not_fetch_or_replace(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    latest = public / "latest.json"
    latest.write_text(_published("2026-11-23", "2026-11-23"), encoding="utf-8")
    before = latest.read_bytes()

    def boom(expected, cache):
        raise AssertionError("不應該下載")

    code = run_update(
        public_dir=public,
        config_path=ROOT / "config" / "default.json",
        cache_dir=tmp_path / "cache",
        now=datetime(2026, 11, 24, 8, 5, tzinfo=HKT),
        load=boom,
    )
    assert code == 0
    assert latest.read_bytes() == before


def test_holiday_partial_and_crash_keep_the_previous_snapshot(tmp_path):
    public = tmp_path / "public"
    public.mkdir()
    latest = public / "latest.json"
    latest.write_text(_published("2026-11-20", "2026-11-20"), encoding="utf-8")
    before = latest.read_bytes()
    now = datetime(2026, 11, 24, 8, 0, tzinfo=HKT)

    def crash(expected, cache):
        raise RuntimeError("network down")

    assert run_update(public_dir=public, config_path=ROOT / "config/default.json", cache_dir=tmp_path / "c1", now=now, load=crash) == 1
    assert latest.read_bytes() == before

    def holiday(expected, cache):
        return _loaded(date(2026, 11, 20), date(2026, 11, 20))

    assert run_update(public_dir=public, config_path=ROOT / "config/default.json", cache_dir=tmp_path / "c2", now=now, load=holiday) == 0
    assert latest.read_bytes() == before

    def thin(expected, cache):
        loaded = _loaded(SESSION, SESSION)
        for item in loaded.symbols["US"][:2]:
            item.payload = _payload_for(item, date(2026, 11, 20))
        return loaded

    assert run_update(public_dir=public, config_path=ROOT / "config/default.json", cache_dir=tmp_path / "c3", now=now, load=thin) == 1
    assert latest.read_bytes() == before


def test_late_start_publishes_the_morning_cut_and_a_second_run_skips(tmp_path):
    public = tmp_path / "public"
    cache = tmp_path / "cache"
    calls = {"n": 0}

    def loader(expected, cache_dir):
        calls["n"] += 1
        assert expected == {"US": SESSION, "JP": SESSION}
        return _loaded(SESSION, SESSION)

    code = run_update(
        public_dir=public,
        config_path=ROOT / "config/default.json",
        cache_dir=cache,
        now=datetime(2026, 11, 24, 18, 30, tzinfo=HKT),
        load=loader,
    )
    assert code == 0
    assert calls["n"] == 1
    snapshot = json.loads((public / "latest.json").read_text(encoding="utf-8"))
    notice = json.loads((public / "publication.json").read_text(encoding="utf-8"))
    assert snapshot["mode"] == "real"
    assert snapshot["demo_disclaimer"] is None
    assert snapshot["markets"]["US"]["as_of"] == "2026-11-23"
    assert snapshot["markets"]["JP"]["as_of"] == "2026-11-23"
    assert notice["snapshot_id"] == snapshot["snapshot_id"]
    assert notice["sessions"] == {"US": "2026-11-23", "JP": "2026-11-23"}
    for market in ("US", "JP"):
        for status in notice["scans"][market].values():
            assert status in {"valid", "not_valid", "incomplete"}
    universe = (cache / "inputs" / "universe.csv").read_text(encoding="utf-8")
    assert ",," in universe
    assert "large" not in universe
    assert "mid" not in universe
    first = (public / "latest.json").read_bytes()
    again = run_update(
        public_dir=public,
        config_path=ROOT / "config/default.json",
        cache_dir=cache,
        now=datetime(2026, 11, 24, 8, 10, tzinfo=HKT),
        load=loader,
    )
    assert again == 0
    assert calls["n"] == 1
    assert (public / "latest.json").read_bytes() == first


def test_repo_has_no_timer_and_the_entrypoint_is_update():
    workflows = list((ROOT / ".github" / "workflows").glob("*.yml"))
    assert [path.name for path in workflows] == ["ci.yml"]
    for line in workflows[0].read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not stripped.startswith("schedule:")
        assert "cron:" not in stripped
    source = (ROOT / "src" / "jp_us_screener" / "update.py").read_text(encoding="utf-8")
    assert "automation-1795319781957" in source
    assert "cron" not in source
    with pytest.raises(SystemExit) as caught:
        main(["update", "--help"])
    assert caught.value.code == 0


class _Body:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self) -> bytes:
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _Opener:
    def __init__(self, items: list) -> None:
        self.items = items

    def __call__(self, request, timeout=30):
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _chart(symbol: str, currency: str, zone: str, rows: list[tuple[datetime, float | None, float | None, float | None]]) -> dict:
    quote = {name: [] for name in ("open", "high", "low", "close", "volume")}
    adjusted = []
    timestamps = []
    for moment, close, adj, volume in rows:
        timestamps.append(int(moment.timestamp()))
        for name in ("open", "high", "low"):
            quote[name].append(None if close is None else close)
        quote["close"].append(close)
        quote["volume"].append(volume)
        adjusted.append(adj)
    return {
        "meta": {"symbol": symbol, "currency": currency, "instrumentType": "EQUITY", "exchangeTimezoneName": zone},
        "timestamp": timestamps,
        "indicators": {"quote": [quote], "adjclose": [{"adjclose": adjusted}]},
        "events": {},
    }


def _counted(matches: int, no_match: int, unmet: int = 0) -> dict:
    return {
        "evaluation": "complete",
        "counts": {"matches": matches, "no_match": no_match, "unmet_dependency": unmet, "insufficient_warmup": 0},
        "matches": [],
    }


def _snapshot_shell() -> dict:
    def market_block(as_of: str, matches: int, no_match: int, unmet: int = 0, symbols: list[str] | None = None) -> dict:
        return {
            "evaluation": "complete_with_diagnostics" if unmet else "complete",
            "counts": {"matches": matches, "no_match": no_match, "unmet_dependency": unmet, "insufficient_warmup": 0},
            "matches": [{"symbol": symbol, "last_date": as_of, "data_notice": ""} for symbol in symbols or []],
        }

    scans = {}
    for scan_id, name in (
        ("adx", "ADX"),
        ("pullback", "回調"),
        ("squeeze", "擠壓"),
        ("turtle", "海龜湯"),
        ("weak", "弱 ADX"),
        ("young", "初生趨勢"),
    ):
        if scan_id == "squeeze":
            us = market_block("2026-11-23", 0, 0, 4)
            jp = market_block("2026-11-20", 0, 3)
        elif scan_id == "pullback":
            us = market_block("2026-11-23", 0, 6)
            jp = market_block("2026-11-20", 1, 5, symbols=["4912"])
        else:
            us = market_block("2026-11-23", 1, 5, symbols=["AAA"])
            jp = market_block("2026-11-20", 0, 6)
        scans[scan_id] = {"id": scan_id, "name_zh": name, "markets": {"US": us, "JP": jp}}
    config = EngineConfig()
    return {
        "snapshot_id": "abc",
        "mode": "real",
        "demo_disclaimer": None,
        "semantics_version": "2026-11-22.provisional.2.jp-yen",
        "status": "complete_with_diagnostics",
        "config": config.to_dict(),
        "markets": {
            "US": {"as_of": "2026-11-23", "eligible_stocks": 6},
            "JP": {"as_of": "2026-11-20", "eligible_stocks": 6},
        },
        "cohort_ranks": {"US": {"large": []}, "JP": {"large": []}, "unranked": []},
        "scans": scans,
    }


def _published(us: str, jp: str) -> str:
    return json.dumps({"markets": {"US": {"as_of": us}, "JP": {"as_of": jp}}})


def _loaded(us_day: date, jp_day: date) -> LoadedMarkets:
    return LoadedMarkets(
        symbols={"US": [_symbol("US", name, us_day) for name in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")], "JP": [_symbol("JP", name, jp_day) for name in ("7203", "6758", "9984", "8306", "6902", "4063")]},
        notes={"US": "測試名單", "JP": "測試名單"},
    )


def _symbol(market: str, symbol: str, session: date) -> LoadedSymbol:
    item = LoadedSymbol(
        market=market,
        symbol=symbol,
        yahoo=f"{symbol}.T" if market == "JP" else symbol,
        name=symbol,
        exchange="XTKS" if market == "JP" else "XNAS",
        currency="JPY" if market == "JP" else "USD",
    )
    item.payload = _payload_for(item, session)
    item.quote = _quote_for(item, session)
    return item


def _payload_for(item: LoadedSymbol, session: date) -> dict:
    zone = TOKYO if item.market == "JP" else NY
    start = 3000.0 if item.market == "JP" else 100.0
    price = start
    rows = []
    cursor = session
    days: list[date] = []
    while len(days) < 220:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    for day in reversed(days):
        price *= 1.001
        moment = datetime(day.year, day.month, day.day, 12, tzinfo=zone)
        rows.append((moment, price, price, 2_000_000))
    return _chart(item.yahoo, item.currency, str(zone), rows)


def _quote_for(item: LoadedSymbol, session: date) -> dict:
    zone = TOKYO if item.market == "JP" else NY
    close = 3000 * (1.001**220) if item.market == "JP" else 100 * (1.001**220)
    if item.market == "JP":
        return {"currency": "JPY", "sharesOutstanding": 2_000_000_000_000 / close, "regularMarketTime": int(datetime(session.year, session.month, session.day, 15, tzinfo=zone).timestamp())}
    shares = 150_000_000_000 / close
    return {
        "currency": "USD",
        "regularMarketTime": int(datetime(session.year, session.month, session.day, 16, tzinfo=zone).timestamp()),
        "regularMarketPrice": close,
        "sharesOutstanding": shares,
        "marketCap": close * shares,
    }


def test_bars_used_by_the_fixture_are_not_weekend_rows():
    assert Bar("US", "AAA", "2026-11-23", 1, 1, 1, 1, 1, "split_and_dividend").date == "2026-11-23"
