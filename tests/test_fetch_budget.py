from __future__ import annotations

from datetime import date, timedelta

from jp_us_screener.fetchbudget import (
    chart_request_window,
    corporate_action_invalidates,
    fill_missing_records,
    merge_chart_payloads,
    seed_records_from_inputs,
    seed_records_from_snapshot,
    select_quote_refresh,
)
from jp_us_screener.update import LoadedSymbol


def test_chart_window_reuses_a_covered_cache_and_requests_only_the_gap():
    through = date(2026, 11, 24)
    lookback = through - timedelta(days=800)
    assert chart_request_window(cached_first=date(2020, 1, 2), cached_last=through, lookback_start=lookback, through=through) is None
    assert chart_request_window(cached_first=lookback + timedelta(days=1), cached_last=through, lookback_start=lookback, through=through) is None
    start, end = chart_request_window(
        cached_first=date(2020, 1, 2),
        cached_last=date(2026, 11, 20),
        lookback_start=lookback,
        through=through,
    )
    assert start == date(2026, 11, 13)
    assert end == date(2026, 11, 25)
    full = chart_request_window(cached_first=None, cached_last=None, lookback_start=lookback, through=through)
    assert full == (lookback, date(2026, 11, 25))


def test_new_split_invalidates_but_overlap_merges():
    old = _chart([100], [10])
    newer = _chart([200], [11], splits={"1": {"date": 200, "numerator": 2, "denominator": 1}})
    assert corporate_action_invalidates(old, newer) is True
    assert corporate_action_invalidates(old, _chart([200], [11])) is False
    merged = merge_chart_payloads(old, _chart([100, 200], [10.5, 11]))
    assert merged["timestamp"] == [100, 200]
    assert merged["indicators"]["quote"][0]["close"] == [10.5, 11]


def test_quote_refresh_is_bounded_and_still_includes_missing_and_moved_prices():
    items = [LoadedSymbol("US", f"S{index:02d}", f"S{index:02d}", "n", "XNAS", "USD", payload={"ok": True}) for index in range(10)]
    records = {
        f"US:{item.symbol}": {
            "currency": "USD",
            "shares_outstanding": 100,
            "close_at_observation": 10,
            "source": "yahoo_quote",
            "shares_as_of": "2026-11-20",
        }
        for item in items
    }
    del records["US:S00"]
    records["US:S03"]["close_at_observation"] = 10
    closes = {("US", item.symbol): 10 for item in items}
    closes[("US", "S03")] = 20
    selected = select_quote_refresh(items, records, closes, session_by_market={"US": date(2026, 11, 24), "JP": date(2026, 11, 24)}, limit=3)
    symbols = [item.symbol for item in selected]
    assert "S00" in symbols
    assert "S03" in symbols
    assert len(symbols) <= 5


def test_snapshot_seed_keeps_provenance_and_skips_blanks_and_conflicts(tmp_path):
    snapshot = {
        "mode": "real",
        "snapshot_id": "abc123",
        "scans": {
            "adx": {
                "markets": {
                    "US": {
                        "matches": [
                            {"market": "US", "symbol": "AAPL", "close": 100, "market_cap": 1000, "market_cap_currency": "USD", "last_date": "2026-11-20"},
                            {"market": "US", "symbol": "ZERO", "close": 10, "market_cap": 0, "market_cap_currency": "USD", "last_date": "2026-11-20"},
                            {"market": "US", "symbol": "AAPL", "close": 50, "market_cap": 1000, "market_cap_currency": "USD", "last_date": "2026-11-20"},
                        ]
                    }
                }
            }
        },
    }
    seeded = seed_records_from_snapshot(snapshot)
    assert "US:ZERO" not in seeded
    assert "US:AAPL" not in seeded

    clean = {
        "mode": "real",
        "snapshot_id": "abc123",
        "scans": {
            "adx": {
                "markets": {
                    "JP": {
                        "matches": [
                            {"market": "JP", "symbol": "7203", "close": 2500, "market_cap": 5_000_000, "market_cap_currency": "JPY", "last_date": "2026-11-20"}
                        ]
                    }
                }
            }
        },
    }
    jp = seed_records_from_snapshot(clean)["JP:7203"]
    assert jp["shares_outstanding"] == 2000
    assert jp["source"] == "published_snapshot_implied"
    assert "abc123" in jp["provenance"]
    assert "不是 Yahoo sharesOutstanding" in jp["provenance"]

    ohlcv = tmp_path / "ohlcv.csv"
    caps = tmp_path / "marketcap.csv"
    ohlcv.write_text("market,symbol,date,close\nUS,MSFT,2026-11-20,200\n", encoding="utf-8")
    caps.write_text("market,symbol,market_cap,market_cap_currency,market_cap_as_of\nUS,MSFT,4000,USD,2026-11-20\nUS,NONE,10,USD,2026-11-20\n", encoding="utf-8")
    implied = seed_records_from_inputs(ohlcv, caps)
    assert set(implied) == {"US:MSFT"}
    assert implied["US:MSFT"]["shares_outstanding"] == 20
    assert implied["US:MSFT"]["source"] == "cached_inputs_implied"
    records = {"US:MSFT": {"currency": "USD", "shares_outstanding": 9, "source": "yahoo_quote"}}
    assert fill_missing_records(records, implied) == 0
    assert records["US:MSFT"]["source"] == "yahoo_quote"


def _chart(stamps: list[int], closes: list[float], splits: dict | None = None) -> dict:
    return {
        "meta": {"symbol": "AAPL", "currency": "USD"},
        "timestamp": stamps,
        "indicators": {
            "quote": [{"open": closes, "high": closes, "low": closes, "close": closes, "volume": [100] * len(closes)}],
            "adjclose": [{"adjclose": closes}],
        },
        "events": {"splits": splits or {}},
    }
