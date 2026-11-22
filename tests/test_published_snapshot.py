import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "public" / "data"


def symbols(snapshot: dict, scan_id: str, market: str) -> list[str]:
    return [row["symbol"] for row in snapshot["scans"][scan_id]["markets"][market]["matches"]]


def test_published_nov20_snapshot_matches_the_computed_result():
    latest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    dated = json.loads((DATA / "snapshot-2026-11-20.json").read_text(encoding="utf-8"))
    notice = json.loads((DATA / "publication.json").read_text(encoding="utf-8"))
    assert latest == dated
    assert latest["snapshot_id"] == "333c3e1d581cb716"
    assert latest["semantics_version"] == "2026-11-22.provisional.2.jp-yen"
    assert latest["mode"] == "real"
    assert latest["demo_disclaimer"] is None
    assert latest["markets"]["US"]["as_of"] == "2026-11-20"
    assert latest["markets"]["JP"]["as_of"] == "2026-11-20"
    us = latest["config"]["cohort_thresholds"]["US"]
    jp = latest["config"]["cohort_thresholds"]["JP"]
    assert us["enabled"] is True and us["currency"] == "USD"
    assert (us["large_min"], us["mid_min"], us["mid_max"], us["small_min"]) == (100_000_000_000, 25_000_000_000, 100_000_000_000, None)
    assert jp["enabled"] is True and jp["currency"] == "JPY"
    assert (jp["large_min"], jp["mid_min"], jp["mid_max"], jp["small_min"]) == (1_000_000_000_000, 250_000_000_000, 1_000_000_000_000, None)
    assert len(latest["cohort_ranks"]["US"]["large"]) == 112
    assert len(latest["cohort_ranks"]["US"]["mid"]) == 258
    assert len(latest["cohort_ranks"]["JP"]["large"]) == 192
    assert len(latest["cohort_ranks"]["JP"]["mid"]) == 275
    missing = [row for row in latest["cohort_ranks"]["unranked"] if row["reason"] == "missing_market_cap"]
    assert len(missing) == 942
    assert all(row["qualified_symbol"].startswith("US:") for row in missing)
    assert symbols(latest, "pullback", "JP") == ["4912"]
    assert symbols(latest, "weak", "JP") == ["4385"]
    assert symbols(latest, "adx", "JP") == []
    assert symbols(latest, "squeeze", "JP") == []
    assert symbols(latest, "young", "JP") == []
    assert sorted(symbols(latest, "pullback", "US")) == ["CMG", "HDB", "IBN", "INSM"]
    assert sorted(symbols(latest, "squeeze", "US")) == ["ALRM", "DV"]
    assert sorted(symbols(latest, "weak", "US")) == ["ADBE", "CDNS", "GS", "NXPI", "ORCL", "SNPS"]
    assert sorted(symbols(latest, "young", "US")) == ["INSM", "PANW"]
    assert symbols(latest, "adx", "US") == []
    assert latest["scans"]["turtle"]["markets"]["JP"]["counts"]["matches"] == 68
    assert latest["scans"]["turtle"]["markets"]["US"]["counts"]["matches"] == 113
    assert notice["snapshot_id"] == latest["snapshot_id"]
    assert notice["as_of"] == "2026-11-20"
    for market in ("US", "JP"):
        for scan_id in ("adx", "pullback", "squeeze", "turtle", "weak", "young"):
            assert notice["scans"][market][scan_id] == "valid"
    for scan in latest["scans"].values():
        for market in scan["markets"].values():
            for row in market["matches"]:
                assert row["last_date"] == "2026-11-20"
