import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from jp_us_screener.config import EngineConfig
from jp_us_screener.indexlists import INDEX_RANK_COHORT, load_constituents, normalize_jp_code, normalize_us_ticker
from jp_us_screener.localprices import bars_from_asia_csv, index_price_files, match_price_file
from jp_us_screener.sessions import HKT
from jp_us_screener.update import load_local_markets, run_update
from jp_us_screener.yahoo import YahooClient

ROOT = Path(__file__).resolve().parents[1]
SESSION = date(2026, 11, 23)


def test_published_lists_are_russell_1000_and_topix_500_without_cap_filters():
    us, jp, info = load_constituents(ROOT / "universes")
    assert info["US"]["rows"] == 1021
    assert info["JP"]["rows"] == 501
    assert info["JP"]["series"] == {"TOPIX Core30": 30, "TOPIX Large70": 70, "TOPIX Mid400": 401}
    us_symbols = {row["symbol"] for row in us}
    jp_symbols = {row["symbol"] for row in jp}
    assert "AAPL" in us_symbols
    assert "BRK-B" in us_symbols
    assert "LEN-B" in us_symbols
    assert "HOLX" in us_symbols
    assert "USD" not in us_symbols
    assert "ESZ6" not in us_symbols
    assert "7203" in jp_symbols
    assert {"285A", "417A", "641A", "642A", "646A"} <= jp_symbols
    assert all("cohort" not in row for row in us + jp)
    assert all("market_cap" not in row for row in us + jp)
    holx = next(row for row in us if row["symbol"] == "HOLX")
    assert holx["source_ticker"] == "HOLX"
    brk = next(row for row in us if row["symbol"] == "BRK-B")
    assert brk["source_ticker"] == "BRK B"
    assert brk["exchange"] == "XNYS"
    assert normalize_us_ticker("BF/A") == "BF-A"
    assert normalize_jp_code("7203.T") == "7203"
    assert INDEX_RANK_COHORT == "large"


def test_live_snapshot_is_not_replaced_by_this_change():
    latest = json.loads((ROOT / "web" / "public" / "data" / "latest.json").read_text(encoding="utf-8"))
    dated = json.loads((ROOT / "web" / "public" / "data" / "snapshot-2026-11-20.json").read_text(encoding="utf-8"))
    assert latest["snapshot_id"] == dated["snapshot_id"] == "333c3e1d581cb716"
    assert latest["semantics_version"] == "2026-11-22.provisional.2.jp-yen"


def test_local_csv_maps_tickers_and_does_not_fill_holes(tmp_path: Path):
    us = tmp_path / "us"
    jp = tmp_path / "jp"
    us.mkdir()
    jp.mkdir()
    _write_prices(us / "BRK.B.csv", close=100, adj_close=50, volume=1000)
    _write_prices(jp / "7203.T.csv", close=2000, adj_close=2000, volume=3000)
    hole = _write_prices(us / "AAPL.csv", close=10, adj_close=10, volume=1000, rows=5)
    lines = hole.read_text(encoding="utf-8").splitlines()
    parts = lines[3].split(",")
    parts[4] = ""
    parts[5] = ""
    lines[3] = ",".join(parts)
    hole.write_text("\n".join(lines) + "\n", encoding="utf-8")

    found = index_price_files(tmp_path)
    brk, brk_error = match_price_file(found, "US", "BRK-B")
    code, code_error = match_price_file(found, "JP", "7203")
    assert brk_error is None and brk is not None and brk.name == "BRK.B.csv"
    assert code_error is None and code is not None and code.name == "7203.T.csv"
    bars, error = bars_from_asia_csv(
        brk, "US", "BRK-B", through=SESSION, lookback_start=date(2020, 1, 1), config=EngineConfig()
    )
    assert error is None and bars is not None
    assert bars[0].close == pytest.approx(50)
    assert bars[0].open == pytest.approx(50)
    assert bars[-1].close == pytest.approx(50 * (1.002 ** (len(bars) - 1)))
    assert bars[-1].volume == pytest.approx(1000)
    assert all(bar.close > 0 for bar in bars)

    rejected, reason = bars_from_asia_csv(
        hole, "US", "AAPL", through=SESSION, lookback_start=date(2020, 1, 1), config=EngineConfig()
    )
    assert rejected is None
    assert reason is not None and "不會補零" in reason
    blank = _write_prices(us / "MSFT.csv", close=10, adj_close=10, volume=1000, rows=5)
    blank_lines = blank.read_text(encoding="utf-8").splitlines()
    blank_parts = blank_lines[3].split(",")
    blank_lines[3] = blank_parts[0] + "," * (len(blank_parts) - 1)
    blank.write_text("\n".join(blank_lines) + "\n", encoding="utf-8")
    rejected_blank, blank_reason = bars_from_asia_csv(
        blank, "US", "MSFT", through=SESSION, lookback_start=date(2020, 1, 1), config=EngineConfig()
    )
    assert rejected_blank is None
    assert blank_reason is not None and "不會補零" in blank_reason

    zero = us / "ZERO.csv"
    _write_prices(zero, close=0, adj_close=0, volume=0, rows=3)
    rejected_zero, zero_reason = bars_from_asia_csv(
        zero, "US", "ZERO", through=SESSION, lookback_start=date(2020, 1, 1), config=EngineConfig()
    )
    assert rejected_zero is None
    assert zero_reason is not None and "不會補零" in zero_reason


def test_local_ingest_publishes_without_yahoo_and_keeps_the_snapshot_when_prices_are_missing(tmp_path: Path, monkeypatch):
    def refuse_yahoo(*_args, **_kwargs):
        raise AssertionError("本機價格路徑不能呼叫 Yahoo")

    monkeypatch.setattr(YahooClient, "__init__", refuse_yahoo)
    prices = tmp_path / "prices"
    prices.mkdir()
    us_members = [_member("US", symbol) for symbol in ("AAPL", "MSFT", "BRK-B", "NVDA", "AMZN", "META")]
    jp_members = [_member("JP", symbol) for symbol in ("7203", "6758", "9984", "8306", "6501", "641A")]
    for row in us_members + jp_members:
        name = "BRK.B.csv" if row["symbol"] == "BRK-B" else f"{row['symbol']}.T.csv" if row["market"] == "JP" else f"{row['symbol']}.csv"
        _write_prices(prices / name, close=100 if row["market"] == "US" else 2000, adj_close=100 if row["market"] == "US" else 2000, volume=250_000)
    info = {
        "US": {"rows": len(us_members), "as_of": "2026-11-24"},
        "JP": {"rows": len(jp_members), "as_of": "2026-10-30", "series": {"TOPIX Core30": 1, "TOPIX Large70": 1, "TOPIX Mid400": 4}},
    }
    loaded = load_local_markets(expected={"US": SESSION, "JP": SESSION}, prices_dir=prices, members=(us_members, jp_members, info))
    assert all(item.quote is None and not item.dated_caps for item in loaded.symbols["US"] + loaded.symbols["JP"])
    assert all(item.error is None and item.bars for item in loaded.symbols["US"] + loaded.symbols["JP"])
    assert loaded.symbols["US"][2].symbol == "BRK-B"
    assert loaded.symbols["JP"][-1].symbol == "641A"

    public = tmp_path / "public"
    public.mkdir()
    (public / "latest.json").write_text('{"markets":{"US":{"as_of":"2026-11-20"},"JP":{"as_of":"2026-11-20"}}}\n', encoding="utf-8")

    def loader(expected, cache_dir):
        del cache_dir
        assert expected["US"] == SESSION
        return load_local_markets(expected, prices, members=(us_members, jp_members, info))

    code = run_update(
        public_dir=public,
        config_path=ROOT / "config" / "default.json",
        cache_dir=tmp_path / "cache",
        now=datetime(2026, 11, 24, 18, 30, tzinfo=HKT),
        load=loader,
    )
    assert code == 0
    snapshot = json.loads((public / "latest.json").read_text(encoding="utf-8"))
    assert snapshot["mode"] == "real"
    assert snapshot["demo_disclaimer"] is None
    assert snapshot["config"]["cohort_thresholds"]["US"]["enabled"] is False
    assert snapshot["markets"]["US"]["as_of"] == "2026-11-23"
    notice = json.loads((public / "publication.json").read_text(encoding="utf-8"))
    assert "Russell 1000" in notice["body"]
    assert "TOPIX 500" in notice["body"]
    assert "不足一半" not in notice["body"]
    universe = (tmp_path / "cache" / "inputs" / "universe.csv").read_text(encoding="utf-8")
    assert universe.count(",large,") == 12
    assert ",mid," not in universe
    assert ",0," not in universe

    thin = tmp_path / "thin"
    thin.mkdir()
    for row in us_members[:2] + jp_members[:2]:
        _write_prices(thin / f"{row['symbol']}.csv", close=100, adj_close=100, volume=250_000)
    before = (public / "latest.json").read_bytes()

    def thin_loader(expected, cache_dir):
        del cache_dir
        return load_local_markets(expected, thin, members=(us_members, jp_members, info))

    assert run_update(
        public_dir=public,
        config_path=ROOT / "config" / "default.json",
        cache_dir=tmp_path / "cache-thin",
        now=datetime(2026, 11, 25, 8, 0, tzinfo=HKT),
        load=thin_loader,
    ) == 1
    assert (public / "latest.json").read_bytes() == before


def _member(market: str, symbol: str) -> dict:
    return {
        "market": market,
        "symbol": symbol,
        "yahoo": f"{symbol}.T" if market == "JP" else symbol,
        "name": symbol,
        "exchange": "XTKS" if market == "JP" else "XNAS",
        "currency": "JPY" if market == "JP" else "USD",
    }


def _write_prices(path: Path, *, close: float, adj_close: float, volume: float, rows: int = 8) -> Path:
    days = _weekdays_ending(SESSION, rows)
    factor = adj_close / close if close else 1
    lines = ["Date,Open,High,Low,Close,AdjClose,Volume,AdjOpen,AdjHigh,AdjLow"]
    price = close
    for day in days:
        raw_open = price
        raw_high = price * 1.01
        raw_low = price * 0.99
        lines.append(
            f"{day.isoformat()},{raw_open},{raw_high},{raw_low},{price},{price * factor},{volume},"
            f"{raw_open * factor},{raw_high * factor},{raw_low * factor}"
        )
        price *= 1.002
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _weekdays_ending(end: date, count: int) -> list[date]:
    found: list[date] = []
    cursor = end
    while len(found) < count:
        if cursor.weekday() < 5:
            found.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(found))
