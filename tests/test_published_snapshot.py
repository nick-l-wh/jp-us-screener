import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "web" / "public" / "data"


def test_published_nov20_snapshot_matches_the_computed_result():
    latest = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    dated = json.loads((DATA / "snapshot-2026-11-20.json").read_text(encoding="utf-8"))
    notice = json.loads((DATA / "publication.json").read_text(encoding="utf-8"))
    assert latest == dated
    assert latest["snapshot_id"] == "7f53fcc6ca63a75c"
    assert latest["mode"] == "real"
    assert latest["demo_disclaimer"] is None
    assert latest["markets"]["US"]["as_of"] == "2026-11-20"
    assert latest["markets"]["JP"]["as_of"] == "2026-11-20"
    assert [row["symbol"] for row in latest["scans"]["pullback"]["markets"]["JP"]["matches"]] == ["4912"]
    assert sorted(row["symbol"] for row in latest["scans"]["squeeze"]["markets"]["US"]["matches"]) == ["ALRM", "DV"]
    assert latest["scans"]["turtle"]["markets"]["JP"]["counts"]["matches"] == 68
    assert latest["scans"]["turtle"]["markets"]["US"]["counts"]["matches"] == 113
    assert notice["snapshot_id"] == latest["snapshot_id"]
    assert notice["as_of"] == "2026-11-20"
    for scan_id in ("adx", "pullback", "weak", "young"):
        assert notice["scans"]["US"][scan_id] == "not_valid"
    assert notice["scans"]["JP"]["adx"] == "incomplete"
    assert notice["scans"]["US"]["squeeze"] == "valid"
    assert notice["scans"]["JP"]["turtle"] == "valid"
    for scan in latest["scans"].values():
        for market in scan["markets"].values():
            for row in market["matches"]:
                assert row["last_date"] == "2026-11-20"
