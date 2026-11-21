import json
from pathlib import Path

from jp_us_screener.demo import build_demo_snapshot
from jp_us_screener.version import DEMO_DISCLAIMER, JP_ADAPT_MESSAGE

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "web" / "public" / "demo" / "snapshot.json"


def test_demo_fixture_is_labeled_and_synthetic():
    snapshot = build_demo_snapshot()
    assert snapshot["mode"] == "demo"
    assert snapshot["demo_disclaimer"] == DEMO_DISCLAIMER
    assert snapshot["scans"]["squeeze"]["markets"]["JP"]["message"] == JP_ADAPT_MESSAGE
    assert snapshot["scans"]["turtle"]["markets"]["JP"]["evaluation"] == "blocked_needs_jp_parameters"
    assert snapshot["scans"]["adx"]["grouping_warning"] == "條件分組待核對"
    assert snapshot["scans"]["weak"]["grouping_warning"] is None
    assert snapshot["scans"]["weak"]["markets"]["US"]["matches"]
    assert snapshot["scans"]["pullback"]["markets"]["US"]["matches"]
    assert snapshot["scans"]["squeeze"]["markets"]["US"]["matches"]
    assert snapshot["scans"]["turtle"]["markets"]["US"]["matches"]
    assert snapshot["scans"]["young"]["markets"]["US"]["matches"]
    assert any(row["diff_status"] == "dropped" for row in snapshot["scans"]["turtle"]["markets"]["US"]["dropped"])
    names = []
    for scan in snapshot["scans"].values():
        for market in scan["markets"].values():
            for row in market["matches"] + market["dropped"]:
                names.append(row["qualified_symbol"])
                names.append(row.get("name") or "")
    for row in snapshot["cohort_ranks"].get("unranked", []):
        names.append(row["qualified_symbol"])
    blob = "\n".join(names)
    for banned in ("AAPL", "MSFT", "TSLA", "7203", "9984", "Toyota", "Sony"):
        assert banned not in blob
    matched = []
    for scan in snapshot["scans"].values():
        for market in scan["markets"].values():
            for row in market["matches"]:
                matched.append(row["qualified_symbol"])
                assert row["qualified_symbol"].startswith(("US:SYN-", "JP:SYN-"))
                assert row["data_notice"] == DEMO_DISCLAIMER
    assert snapshot["universe_diagnostics"]["excluded_etf"]
    assert "US:SYN-ETF" not in matched


def test_committed_demo_snapshot_matches_the_builder():
    snapshot = build_demo_snapshot()
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed["snapshot_id"] == snapshot["snapshot_id"]
    assert committed["demo_disclaimer"] == DEMO_DISCLAIMER
