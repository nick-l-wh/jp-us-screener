"""The page's coverage figures have to match the published snapshot and the index lists."""

import json
from pathlib import Path

from jp_us_screener.assumptions import export_rules
from jp_us_screener.indexlists import export_public_universe
from jp_us_screener.indicators import ema
from jp_us_screener.sctr import score_from_parts
from jp_us_screener.models import Feature

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "public" / "data"


def test_published_rules_include_the_engine_trees():
    published = json.loads((ROOT / "web" / "public" / "rules.json").read_text(encoding="utf-8"))
    assert published == export_rules()
    by_id = {scan["id"]: scan for scan in published["scans"]}
    assert "市值" not in json.dumps(by_id["squeeze"]["tree"], ensure_ascii=False)
    assert "市值" not in json.dumps(by_id["weak"]["tree"], ensure_ascii=False)
    assert by_id["turtle"]["jp_tree"]["children"][4]["label"] == "收市 > 500 日圓"
    assert by_id["turtle"]["tree"]["children"][4]["label"] == "收市 > 5（美元；不換算成日圓）"
    assert by_id["squeeze"]["jp_tree"]["children"][1]["label"] == "日本股票"


def test_published_universe_matches_the_manifest():
    published = json.loads((DATA / "universe.json").read_text(encoding="utf-8"))
    assert published == export_public_universe(ROOT / "universes")
    assert published["US"]["rows"] == 1021
    assert published["JP"]["rows"] == 501
    assert published["JP"]["series"] == {"TOPIX Core30": 30, "TOPIX Large70": 70, "TOPIX Mid400": 401}


def test_snapshot_omissions_match_the_ingest_diagnostics():
    snapshot = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    universe = json.loads((DATA / "universe.json").read_text(encoding="utf-8"))
    ingest = json.loads((DATA / "ingest-diagnostics.json").read_text(encoding="utf-8"))
    assert snapshot["snapshot_id"] == "820ba67e10634ae7"
    assert ingest["snapshot_id"] == snapshot["snapshot_id"]
    eligible = _eligible(snapshot)
    for market in ("US", "JP"):
        listed = set(universe[market]["symbols"])
        missing = [symbol for symbol in universe[market]["symbols"] if symbol not in eligible[market]]
        extra = eligible[market] - listed
        assert not extra
        assert len(eligible[market]) == snapshot["markets"][market]["eligible_stocks"]
        assert set(missing) == set(ingest["no_bars"][market]) | set(ingest["jump"][market])
        assert set(ingest["no_bars"][market]).isdisjoint(ingest["jump"][market])
    assert set(ingest["no_bars"]["US"]) == {"SNDK", "CEG", "HOLX"}
    assert set(ingest["jump"]["US"]) == {"BMNR"}
    assert set(ingest["no_bars"]["JP"]) == {"641A", "642A", "646A"}
    assert set(ingest["jump"]["JP"]) == {"8303", "8377"}
    short = [row for row in snapshot["cohort_ranks"]["unranked"] if row["reason"] == "indicator_score_unavailable"]
    assert len(short) == 15
    assert {row["market"] for row in short} == {"US"}
    assert all(v is None for v in ema([1.0] * 199, 200))
    assert ema([1.0] * 200, 200)[-1] is not None
    feature = Feature(index=0, date="2026-11-25", open=1, high=1, low=1, close=1, volume=1)
    assert score_from_parts(feature)[0] is None


def _eligible(snapshot: dict) -> dict[str, set[str]]:
    found = {"US": set(), "JP": set()}
    ranks = snapshot["cohort_ranks"]
    for market in ("US", "JP"):
        for rows in ranks[market].values():
            for row in rows:
                found[market].add(row["qualified_symbol"].split(":", 1)[1])
    for row in ranks["unranked"]:
        market = "JP" if row["qualified_symbol"].startswith("JP:") else "US"
        found[market].add(row["qualified_symbol"].split(":", 1)[1])
    return found
