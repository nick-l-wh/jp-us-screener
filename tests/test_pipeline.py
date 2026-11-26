import json
from pathlib import Path

from jp_us_screener.cli import main
from jp_us_screener.config import EngineConfig
from jp_us_screener.io import load_universe, template_universe, write_csv
from jp_us_screener.models import Bar
from jp_us_screener.pipeline import PipelineError, compute_snapshot, flat_match_rows
from jp_us_screener.version import DEMO_DISCLAIMER
from tests.support import bars_from_closes, security, small_config, uptrend, weekdays


def _write_inputs(directory: Path, bars: list[Bar], securities: list) -> tuple[Path, Path]:
    ohlcv = directory / "ohlcv.csv"
    universe = directory / "universe.csv"
    fields = ["market", "symbol", "date", "open", "high", "low", "close", "volume", "adjustment"]
    write_csv(
        ohlcv,
        fields,
        [
            {
                "market": bar.market,
                "symbol": bar.symbol,
                "date": bar.date,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "adjustment": bar.adjustment,
            }
            for bar in bars
        ],
    )
    write_csv(
        universe,
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
        [
            {
                "market": item.market,
                "symbol": item.symbol,
                "name": item.name,
                "security_type": item.security_type,
                "listing_segment": item.listing_segment,
                "exchange": item.exchange,
                "currency": item.currency,
                "cohort": item.cohort,
                "market_cap": "" if item.market_cap is None else item.market_cap,
                "market_cap_currency": item.market_cap_currency,
                "market_cap_as_of": item.market_cap_as_of,
                "group": item.group,
            }
            for item in securities
        ],
    )
    return ohlcv, universe


def test_etf_is_excluded_before_it_can_change_ranks():
    strong = bars_from_closes(uptrend(220, step=1.01), symbol="SYN-ETF", volume=2_000_000)
    mild = bars_from_closes(uptrend(220, step=1.001), symbol="SYN-A")
    weak = bars_from_closes([50 - i * 0.05 for i in range(220)], symbol="SYN-B")
    securities = [
        security(symbol="SYN-ETF", security_type="etf", name="合成基金"),
        security(symbol="SYN-A", name="合成甲"),
        security(symbol="SYN-B", name="合成乙"),
    ]
    snapshot = compute_snapshot(strong + mild + weak, securities, small_config(), generated_at="2024-06-03T00:00:00+00:00")
    excluded = {row["qualified_symbol"] for row in snapshot["universe_diagnostics"]["excluded_etf"]}
    assert excluded == {"US:SYN-ETF"}
    for scan in snapshot["scans"].values():
        symbols = {row["qualified_symbol"] for row in scan["markets"]["US"]["matches"]}
        assert "US:SYN-ETF" not in symbols
    ranks = {row["qualified_symbol"]: row["technical_rank"] for row in snapshot["cohort_ranks"]["US"]["large"]}
    assert "US:SYN-ETF" not in ranks
    assert ranks["US:SYN-A"] == 99.9
    assert ranks["US:SYN-B"] == 0.0

    as_stock = [
        security(symbol="SYN-ETF", security_type="stock", name="合成基金"),
        security(symbol="SYN-A", name="合成甲"),
        security(symbol="SYN-B", name="合成乙"),
    ]
    contaminated = compute_snapshot(strong + mild + weak, as_stock, small_config(), generated_at="2024-06-03T00:00:00+00:00")
    contaminated_ranks = {
        row["qualified_symbol"]: row["technical_rank"] for row in contaminated["cohort_ranks"]["US"]["large"]
    }
    assert contaminated_ranks["US:SYN-ETF"] == 99.9
    assert contaminated_ranks["US:SYN-A"] != 99.9


def test_large_and_mid_ranks_are_not_one_global_list():
    series = []
    people = []
    series += bars_from_closes(uptrend(220, step=1.004), symbol="SYN-L1")
    series += bars_from_closes(uptrend(220, step=1.0005), symbol="SYN-L2")
    series += bars_from_closes(uptrend(220, step=1.02), symbol="SYN-M1")
    series += bars_from_closes([80 - i * 0.02 for i in range(220)], symbol="SYN-M2")
    people += [
        security(symbol="SYN-L1", cohort="large"),
        security(symbol="SYN-L2", cohort="large"),
        security(symbol="SYN-M1", cohort="mid"),
        security(symbol="SYN-M2", cohort="mid"),
    ]
    snapshot = compute_snapshot(series, people, small_config(), generated_at="2024-06-03T00:00:00+00:00")
    large = {row["qualified_symbol"]: row for row in snapshot["cohort_ranks"]["US"]["large"]}
    mid = {row["qualified_symbol"]: row for row in snapshot["cohort_ranks"]["US"]["mid"]}
    assert large["US:SYN-L1"]["technical_rank"] == 99.9
    assert "US:SYN-L1" not in mid
    assert mid["US:SYN-M1"]["technical_rank"] == 99.9
    assert "US:SYN-M1" not in large
    assert large["US:SYN-L1"]["rank_label"] == "技術排名（依SCTR公開方法，非官方）"


def test_missing_cap_short_history_unknown_and_adr():
    long_closes = uptrend(220, step=1.004)
    series = bars_from_closes(long_closes, symbol="SYN-OK")
    series += bars_from_closes(long_closes, symbol="SYN-NOCAP")
    short_dates = weekdays(220)[-30:]
    short_closes = uptrend(30)
    series += [
        Bar(
            "US",
            "SYN-SHORT",
            day,
            close * 0.99,
            close * 1.01,
            close * 0.98,
            close,
            250_000,
            "split_and_dividend",
        )
        for day, close in zip(short_dates, short_closes)
    ]
    series += bars_from_closes(long_closes, symbol="SYN-UNK")
    series += bars_from_closes(long_closes, symbol="SYN-ADR")
    people = [
        security(symbol="SYN-OK"),
        security(symbol="SYN-L2"),
        security(symbol="SYN-NOCAP", cohort="", market_cap=None, market_cap_currency="", name="缺市值"),
        security(symbol="SYN-SHORT", name="歷史短"),
        security(symbol="SYN-UNK", security_type="unknown", name="類型不明"),
        security(symbol="SYN-ADR", listing_segment="adr", name="存託未決議"),
    ]
    # A second large name so the cohort can rank when min size is 2.
    series += bars_from_closes([30 + (i % 5) * 0.1 for i in range(220)], symbol="SYN-L2")
    snapshot = compute_snapshot(series, people, small_config(), generated_at="2024-06-03T00:00:00+00:00")
    diag = snapshot["universe_diagnostics"]
    assert {row["qualified_symbol"] for row in diag["excluded_unknown_type"]} == {"US:SYN-UNK"}
    assert {row["qualified_symbol"] for row in diag["eligibility_unresolved"]} == {"US:SYN-ADR"}
    warmup = {row["qualified_symbol"] for row in snapshot["scans"]["adx"]["markets"]["US"]["diagnostics"]["insufficient_warmup"]}
    matches = {row["qualified_symbol"] for row in snapshot["scans"]["adx"]["markets"]["US"]["matches"]}
    reasons = {row["qualified_symbol"]: row["reason"] for row in snapshot["cohort_ranks"]["unranked"]}
    assert reasons["US:SYN-NOCAP"] == "cohort_not_in_list"
    assert "US:SYN-NOCAP" not in matches
    assert reasons["US:SYN-SHORT"] == "indicator_score_unavailable"
    assert "US:SYN-SHORT" in warmup
    assert "US:SYN-ADR" not in matches
    assert "US:SYN-UNK" not in matches


def test_jp_uses_yen_rules_and_us_keeps_the_original_price_filter(tmp_path: Path):
    closes = [20] * 40
    lows = [10.5] * 40
    lows[34] = 10
    lows[-1] = 9.5
    closes[-1] = 10.02
    highs = [21.0] * 40
    highs[-1] = 11.0
    us = bars_from_closes(closes, symbol="SYN-US", volume=800_000, highs=highs, lows=lows)
    jp = bars_from_closes(closes, market="JP", symbol="SYN-JP", volume=800_000, highs=highs, lows=lows)
    people = [
        security(symbol="SYN-US"),
        security(symbol="SYN-PAD", cohort="large"),
        security(market="JP", symbol="SYN-JP", currency="JPY", market_cap=10**15, market_cap_currency="JPY", exchange="XTKS"),
        security(market="JP", symbol="SYN-JP2", currency="JPY", market_cap=10**15, market_cap_currency="JPY", exchange="XTKS"),
    ]
    us += bars_from_closes(uptrend(40, step=1.001), symbol="SYN-PAD")
    jp += bars_from_closes(uptrend(40, step=1.001), market="JP", symbol="SYN-JP2")
    snapshot = compute_snapshot(us + jp, people, small_config(), generated_at="2024-06-03T00:00:00+00:00")
    assert snapshot["scans"]["squeeze"]["markets"]["JP"]["evaluation"].startswith("complete")
    jp_turtle = snapshot["scans"]["turtle"]["markets"]["JP"]
    assert jp_turtle["evaluation"].startswith("complete")
    assert jp_turtle["matches"] == []
    us_turtle = {row["qualified_symbol"] for row in snapshot["scans"]["turtle"]["markets"]["US"]["matches"]}
    assert "US:SYN-US" in us_turtle
    assert "JP:SYN-JP" not in us_turtle


def test_zero_matches_are_written_but_failed_import_is_not(tmp_path: Path):
    bars = bars_from_closes([10, 10, 10, 10, 10], symbol="SYN-QUIET", volume=10)
    people = [security(symbol="SYN-QUIET", market_cap=None, market_cap_currency="", cohort=""), security(symbol="SYN-OTHER", cohort="large")]
    bars += bars_from_closes([10, 10, 10, 10, 10], symbol="SYN-OTHER", volume=10)
    out = tmp_path / "snap.json"
    out.write_text('{"keep": true}\n', encoding="utf-8")
    ohlcv, universe = _write_inputs(tmp_path, bars, people)
    assert main(["compute", "--ohlcv", str(ohlcv), "--universe", str(universe), "--out", str(out), "--generated-at", "2024-06-03T00:00:00+00:00"]) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["scans"]["adx"]["markets"]["US"]["matches"] == []
    assert written["scans"]["adx"]["markets"]["US"]["evaluation"] in {"complete_zero_matches", "complete_with_diagnostics"}
    assert written["status"] in {"complete", "complete_with_diagnostics"}

    out.write_text('{"keep": true}\n', encoding="utf-8")
    bad = tmp_path / "bad.csv"
    bad.write_text("not,a,valid,ohlcv\n1,2,3,4\n", encoding="utf-8")
    code = main(["compute", "--ohlcv", str(bad), "--universe", str(universe), "--out", str(out)])
    assert code == 1
    assert out.read_text(encoding="utf-8") == '{"keep": true}\n'


def test_exception_during_compute_does_not_replace_snapshot(tmp_path: Path, monkeypatch):
    bars = bars_from_closes(uptrend(30), symbol="SYN-A")
    people = [security(symbol="SYN-A")]
    ohlcv, universe = _write_inputs(tmp_path, bars, people)
    out = tmp_path / "snap.json"
    out.write_text("GOOD\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise RuntimeError("指標中斷")

    monkeypatch.setattr("jp_us_screener.cli.compute_snapshot", boom)
    assert main(["compute", "--ohlcv", str(ohlcv), "--universe", str(universe), "--out", str(out)]) == 1
    assert out.read_text(encoding="utf-8") == "GOOD\n"


def test_csv_escaping_roundtrip_and_demo_notice(tmp_path: Path):
    name = '合成,「甲」\n第二行'
    universe = tmp_path / "universe.csv"
    universe.write_text(template_universe().replace('合成,「範例」', name.replace("\n", "\n")), encoding="utf-8")
    # Build the quoted field explicitly so the newline is inside quotes.
    header = "market,symbol,name,security_type,listing_segment,exchange,currency,cohort,market_cap,market_cap_currency,market_cap_as_of,group\n"
    row = 'US,SYN-EXAMPLE,"%s",stock,primary,XNAS,USD,large,2000000000,USD,2024-01-02,\n' % name.replace('"', '""')
    universe.write_text(header + row, encoding="utf-8")
    loaded = load_universe(universe)
    assert loaded[0].name == name

    bars = bars_from_closes([10, 11, 12, 13, 14], symbol="SYN-EXAMPLE", volume=10)
    people = [security(symbol="SYN-EXAMPLE", name=name), security(symbol="SYN-B", name="乙")]
    bars += bars_from_closes([10, 10, 10, 10, 10], symbol="SYN-B", volume=10)
    snapshot = compute_snapshot(bars, people, small_config(), mode="demo", generated_at="2024-06-03T00:00:00+00:00")
    assert snapshot["demo_disclaimer"] == DEMO_DISCLAIMER
    csv_path = tmp_path / "out.csv"
    write_csv(csv_path, list(flat_match_rows(snapshot)[0].keys()) if False else [
        "data_notice", "scan_id", "scan_name_zh", "market", "qualified_symbol", "name", "cohort",
        "diff_status", "last_date", "technical_rank", "close", "volume", "market_cap",
        "market_cap_currency", "roc3", "roc20", "roc90", "ulcer10", "plus_di14", "adx14",
    ], flat_match_rows(snapshot))
    text = csv_path.read_text(encoding="utf-8")
    assert DEMO_DISCLAIMER in text
    escaped = tmp_path / "escaped.csv"
    write_csv(escaped, ["name", "note"], [{"name": name, "note": '他說 "好", 然後離開'}])
    import csv
    import io

    reloaded = list(csv.DictReader(io.StringIO(escaped.read_text(encoding="utf-8"))))
    assert reloaded[0]["name"] == name
    assert "好" in reloaded[0]["note"]
    real = compute_snapshot(bars, people, small_config(), mode="real", generated_at="2024-06-03T00:00:00+00:00")
    assert real["demo_disclaimer"] is None
    assert all(row["data_notice"] == "" for row in flat_match_rows(real))


def test_invalid_ohlc_duplicate_and_weekend_are_data_errors():
    good = security(symbol="SYN-OK")
    bad = security(symbol="SYN-BAD")
    weekend = security(symbol="SYN-WKD")
    dates = weekdays(4)
    ok = bars_from_closes([10, 11, 12, 13], symbol="SYN-OK")
    dup = bars_from_closes([10, 11, 12, 13], symbol="SYN-BAD")
    dup.append(Bar("US", "SYN-BAD", dup[-1].date, 10, 11, 9, 10, 100, "split_and_dividend"))
    sat = [Bar("US", "SYN-WKD", "2023-01-07", 10, 11, 9, 10, 100, "split_and_dividend")]
    snapshot = compute_snapshot(ok + dup + sat, [good, bad, weekend], small_config(), generated_at="2024-06-03T00:00:00+00:00")
    reasons = {row["qualified_symbol"]: row["reason"] for row in snapshot["universe_diagnostics"]["data_error"]}
    assert "重複" in reasons["US:SYN-BAD"]
    assert "星期" in reasons["US:SYN-WKD"]
    broken = bars_from_closes([10, 11], symbol="SYN-HL")
    broken[0] = Bar("US", "SYN-HL", broken[0].date, 10, 8, 9, 9.5, 100, "split_and_dividend")
    try:
        snapshot = compute_snapshot(broken, [security(symbol="SYN-HL"), security(symbol="SYN-KEEP")], small_config())
    except PipelineError:
        snapshot = None
    # A single broken symbol is diagnosed when another symbol is still judgeable.
    other = bars_from_closes([10, 11], symbol="SYN-KEEP")
    snapshot = compute_snapshot(broken + other, [security(symbol="SYN-HL"), security(symbol="SYN-KEEP")], small_config(), generated_at="2024-06-03T00:00:00+00:00")
    assert any(row["qualified_symbol"] == "US:SYN-HL" for row in snapshot["universe_diagnostics"]["data_error"])


def test_insufficient_cohort_does_not_invent_a_rank():
    series = bars_from_closes(uptrend(220), symbol="SYN-ONLY")
    snapshot = compute_snapshot(
        series,
        [security(symbol="SYN-ONLY")],
        EngineConfig(min_cohort_size=5),
        generated_at="2024-06-03T00:00:00+00:00",
    )
    reasons = {row["qualified_symbol"]: row["reason"] for row in snapshot["cohort_ranks"]["unranked"]}
    assert reasons["US:SYN-ONLY"] == "insufficient_cohort"
    assert snapshot["scans"]["adx"]["markets"]["US"]["matches"] == []


def test_diff_new_retained_dropped():
    bars = bars_from_closes(uptrend(40, step=1.01), symbol="SYN-A", volume=2_000_000)
    people = [security(symbol="SYN-A"), security(symbol="SYN-B")]
    bars += bars_from_closes(uptrend(40, step=1.0002), symbol="SYN-B", volume=2_000_000)
    first = compute_snapshot(bars, people, small_config(), generated_at="2024-06-01T00:00:00+00:00")
    # Force a synthetic baseline: keep only SYN-B as a previous young-trend hit if present,
    # otherwise plant one retained and one dropped symbol.
    previous = json.loads(json.dumps(first))
    previous["scans"]["young"]["markets"]["US"]["matches"] = [
        {
            "qualified_symbol": "US:SYN-B",
            "name": "合成乙",
            "cohort": "large",
            "technical_rank": 0.0,
            "last_date": bars[-1].date,
            "close": 1,
            "volume": 1,
            "indicators": {},
            "diff_status": "retained",
            "data_notice": "",
        },
        {
            "qualified_symbol": "US:SYN-OLD",
            "name": "合成舊",
            "cohort": "large",
            "technical_rank": 1,
            "last_date": "2024-05-01",
            "close": 1,
            "volume": 1,
            "indicators": {},
            "diff_status": "retained",
            "data_notice": "",
        },
    ]
    second = compute_snapshot(bars, people, small_config(), previous=previous, generated_at="2024-06-03T00:00:00+00:00")
    young = second["scans"]["young"]["markets"]["US"]
    status = {row["qualified_symbol"]: row["diff_status"] for row in young["matches"]}
    dropped = {row["qualified_symbol"] for row in young["dropped"]}
    if "US:SYN-B" in status:
        assert status["US:SYN-B"] == "retained"
    assert "US:SYN-OLD" in dropped
    assert second["diff"]["baseline_id"] == first["snapshot_id"]


def test_provider_stub_does_not_fetch():
    assert main(["provider"]) == 1
