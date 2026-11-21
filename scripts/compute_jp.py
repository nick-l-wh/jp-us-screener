"""Compute one dated JP results page from the cached JPX list and Yahoo daily bars."""
import datetime as dt
import itertools
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path

import openpyxl

root = Path(__file__).resolve().parents[1] / "data"
sys.path.insert(0, str(root.parent / "src"))
from jp_us_screener.config import EngineConfig
from jp_us_screener.features import compute_features
from jp_us_screener.models import Bar, Security, RankInfo
from jp_us_screener.pipeline import Prepared, _assign_ranks, _evaluate_market
from jp_us_screener.scans import evaluate_tree, jp_scan, scan_catalog
from jp_us_screener.validate import validate_bars

day = "2026-11-20"
jst = dt.timezone(dt.timedelta(hours=9))
config = EngineConfig()
sizes = {"TOPIX Core30": "large", "TOPIX Large70": "large", "TOPIX Mid400": "mid",
         "TOPIX Small 1": "small", "TOPIX Small 2": "small"}
issues = Counter()
items = []
rows = openpyxl.load_workbook(root / "data_e.xlsx", read_only=True).active.values
capsfile = root / "jp-caps-20261120.json"
caps = json.loads(capsfile.read_text(encoding="utf8")) if capsfile.exists() else {}
for r in itertools.islice(rows, 1, None):
    if "(Domestic)" not in r[3]:
        continue
    code = str(r[1])
    cache = root / "jp-20261120" / (code + ".json")
    if not cache.exists():
        issues["missing file"] += 1
        continue
    record = json.loads(cache.read_bytes())
    q = record["indicators"]["quote"][0]
    adjusted = record["indicators"]["adjclose"][0]["adjclose"]
    bars = []
    for i, timestamp in enumerate(record.get("timestamp", [])):
        date = dt.datetime.fromtimestamp(timestamp, jst).date().isoformat()
        if date > day:
            continue
        prices = [q[k][i] for k in ("open", "high", "low", "close")]
        volume = q["volume"][i]
        if all(price is None for price in prices):
            continue
        if None in prices or volume is None or not adjusted[i] or not prices[-1]:
            bars = []
            break
        factor = adjusted[i] / prices[-1]
        bars.append(Bar("JP", code, date, *(price * factor for price in prices),
                        volume, config.adjustment_required))
    if not bars or bars[-1].date != day:
        issues["missing complete target/history"] += 1
        continue
    if validate_bars(bars, config):
        issues["invalid OHLC history"] += 1
        continue
    cap = caps.get(code)
    security = Security("JP", code, r[2], "stock", "primary", "XTKS", "JPY", sizes.get(r[-1], ""),
                        cap, "JPY" if cap is not None else "", day if cap is not None else "", "")
    feature = compute_features(bars)[-1]
    items.append(Prepared(security, "eligible", None, feature=feature, market_as_of=day))

_assign_ranks(items, config)
pending = set()
scans = {}
for original in scan_catalog():
    scan = jp_scan(original)
    if scan["id"] in ("squeeze", "weak"):
        for item in items:
            leaves = []
            evaluate_tree(scan["tree"], item.feature, item.security, item.ranks, leaves)
            if all(leaf.status == "pass" for leaf in leaves if leaf.clause_id not in ("sq-cap", "wk-cap")):
                if item.security.market_cap is None:
                    pending.add(item.security.symbol)
                    code = item.security.symbol
                    try:
                        url = f"https://finance.yahoo.co.jp/quote/{code}.T"
                        req = urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=18) as resp:
                            html = resp.read().decode("utf8")
                        caprow = re.search(r">時価総額</span>.*?</dd>", html, re.S)
                        plain = re.sub(r"<[^>]*>", "", caprow[0]) if caprow else ""
                        amount = re.search(r"([\d,]+)百万円\(11/20\)", plain)
                        if amount and ('2026-11-20' in html or '2026/11/20' in html):
                            cap = int(amount[1].replace(",", "")) * 1_000_000
                            item.security.market_cap = caps[code] = cap
                            item.security.market_cap_currency = "JPY"
                            item.security.market_cap_as_of = day
                            pending.discard(code)
                    except (ValueError, OSError):
                        pass
    result = _evaluate_market(scan, "JP", items, None)
    scans[scan["id"]] = {"blocked": False, "rows": [
        {"symbol": row["symbol"], "name": row["name"], "reference_sctr": row["technical_rank"],
         "local_volume": str(int(row["volume"]))} for row in result["matches"]]}
    print(scan["id"], len(scans[scan["id"]]["rows"]), flush=True)

print("Data coverage", len(items), dict(issues), "missing cap", sorted(pending), flush=True)
capsfile.write_text(json.dumps(caps), encoding="utf8")
(root / "jp-cap-candidates.json").write_text(json.dumps(sorted(pending)), encoding="utf8")
output = {"key": "JP/" + day + "/bundled", "date": day, "market": "JP", "demo": False, "scans": scans,
          "coverage": {"eligible": len(items), "excluded": dict(issues), "unverified_cap": sorted(pending)}}
(root / "jp-day.json").write_text(json.dumps(output, ensure_ascii=False), encoding="utf8")
