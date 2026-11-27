import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "public" / "data"


def symbols(snapshot: dict, scan_id: str, market: str) -> list[str]:
    return [row["symbol"] for row in snapshot["scans"][scan_id]["markets"][market]["matches"]]


def test_published_nov20_snapshot_matches_the_computed_result():
    dated = json.loads((DATA / "snapshot-2026-11-20.json").read_text(encoding="utf-8"))
    assert dated["snapshot_id"] == "333c3e1d581cb716"
    assert dated["semantics_version"] == "2026-11-22.provisional.2.jp-yen"
    assert dated["mode"] == "real"
    assert dated["demo_disclaimer"] is None
    assert dated["markets"]["US"]["as_of"] == "2026-11-20"
    assert dated["markets"]["JP"]["as_of"] == "2026-11-20"
    us = dated["config"]["cohort_thresholds"]["US"]
    jp = dated["config"]["cohort_thresholds"]["JP"]
    assert us["enabled"] is True and us["currency"] == "USD"
    assert (us["large_min"], us["mid_min"], us["mid_max"], us["small_min"]) == (100_000_000_000, 25_000_000_000, 100_000_000_000, None)
    assert jp["enabled"] is True and jp["currency"] == "JPY"
    assert (jp["large_min"], jp["mid_min"], jp["mid_max"], jp["small_min"]) == (1_000_000_000_000, 250_000_000_000, 1_000_000_000_000, None)
    assert len(dated["cohort_ranks"]["US"]["large"]) == 112
    assert len(dated["cohort_ranks"]["US"]["mid"]) == 258
    assert len(dated["cohort_ranks"]["JP"]["large"]) == 192
    assert len(dated["cohort_ranks"]["JP"]["mid"]) == 275
    missing = [row for row in dated["cohort_ranks"]["unranked"] if row["reason"] == "missing_market_cap"]
    assert len(missing) == 942
    assert all(row["qualified_symbol"].startswith("US:") for row in missing)
    assert symbols(dated, "pullback", "JP") == ["4912"]
    assert symbols(dated, "weak", "JP") == ["4385"]
    assert symbols(dated, "adx", "JP") == []
    assert symbols(dated, "squeeze", "JP") == []
    assert symbols(dated, "young", "JP") == []
    assert sorted(symbols(dated, "pullback", "US")) == ["CMG", "HDB", "IBN", "INSM"]
    assert sorted(symbols(dated, "squeeze", "US")) == ["ALRM", "DV"]
    assert sorted(symbols(dated, "weak", "US")) == ["ADBE", "CDNS", "GS", "NXPI", "ORCL", "SNPS"]
    assert sorted(symbols(dated, "young", "US")) == ["INSM", "PANW"]
    assert symbols(dated, "adx", "US") == []
    assert dated["scans"]["turtle"]["markets"]["JP"]["counts"]["matches"] == 68
    assert dated["scans"]["turtle"]["markets"]["US"]["counts"]["matches"] == 113
    for scan in dated["scans"].values():
        for market in scan["markets"].values():
            for row in market["matches"]:
                assert row["last_date"] == "2026-11-20"


def test_published_latest_is_the_local_price_snapshot():
    latest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    dated = json.loads((DATA / "snapshot-us-2026-11-25-jp-2026-11-26.json").read_text(encoding="utf-8"))
    notice = json.loads((DATA / "publication.json").read_text(encoding="utf-8"))
    prior = json.loads((DATA / "snapshot-2026-11-20.json").read_text(encoding="utf-8"))
    assert latest == dated
    assert latest["snapshot_id"] == "b10aaf00a207ce76"
    assert latest["snapshot_id"] != prior["snapshot_id"]
    assert latest["generated_at"] == "2026-11-27T00:00:00+00:00"
    assert latest["semantics_version"] == "2026-11-26.index.1.jp-yen"
    assert latest["mode"] == "real"
    assert latest["demo_disclaimer"] is None
    assert latest["markets"]["US"]["as_of"] == "2026-11-25"
    assert latest["markets"]["JP"]["as_of"] == "2026-11-26"
    assert latest["markets"]["US"]["eligible_stocks"] == 1017
    assert latest["markets"]["JP"]["eligible_stocks"] == 496
    assert latest["config"]["cohort_thresholds"]["US"]["enabled"] is False
    assert latest["config"]["cohort_thresholds"]["JP"]["enabled"] is False
    assert symbols(latest, "adx", "US") == ["HPQ", "TSCO", "VRSN"]
    assert symbols(latest, "pullback", "US") == ["INSM", "BMRN", "BBIO", "RKT", "BILL", "VVV", "ACM", "GPK", "COLM", "TSCO", "VFC", "EPAM", "TNL", "VNT", "REGN", "CMG", "CLX", "FCN", "NXST", "GEN", "ADT"]
    assert symbols(latest, "squeeze", "US") == ["WEX"]
    assert symbols(latest, "turtle", "US") == []
    assert symbols(latest, "weak", "US") == ["ADI", "CDNS", "ECHO", "GTES", "IRM", "ON", "SNPS"]
    assert symbols(latest, "young", "US") == ["AMTM", "INSM", "ZS", "BMRN", "HPQ", "AWI", "GAP", "DLTR"]
    assert symbols(latest, "adx", "JP") == ["2331"]
    assert symbols(latest, "pullback", "JP") == ["9766", "4912", "9684", "9706"]
    assert symbols(latest, "squeeze", "JP") == ["2371"]
    assert symbols(latest, "turtle", "JP") == ["5844", "8015"]
    assert symbols(latest, "weak", "JP") == ["4516"]
    assert symbols(latest, "young", "JP") == []
    assert latest["scans"]["turtle"]["markets"]["US"]["counts"]["no_match"] == 1017
    assert latest["scans"]["young"]["markets"]["JP"]["counts"]["no_match"] == 496
    held_out = {"SNDK", "CEG", "HOLX", "BMNR", "641A", "642A", "646A", "8303", "8377"}
    for scan in latest["scans"].values():
        for market, block in scan["markets"].items():
            assert block["counts"]["matches"] + block["counts"]["no_match"] + block["counts"].get("insufficient_warmup", 0) + block["counts"].get("unmet_dependency", 0) == latest["markets"][market]["eligible_stocks"]
            for row in block["matches"]:
                assert row["last_date"] == latest["markets"][market]["as_of"]
                assert row["symbol"] not in held_out
                assert not str(row["symbol"]).startswith("SYN")
                assert row["market_cap"] in (None, "")
                assert not row.get("data_notice")
    assert notice["snapshot_id"] == latest["snapshot_id"]
    assert notice["sessions"] == {"US": "2026-11-25", "JP": "2026-11-26"}
    for market in ("US", "JP"):
        for scan_id in ("adx", "pullback", "squeeze", "turtle", "weak", "young"):
            assert notice["scans"][market][scan_id] == "valid"
