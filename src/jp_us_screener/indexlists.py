"""Index membership lists for the published universes.

US is the Russell 1000, starting from the iShares IWB holdings file named in
``universes/manifest.json``. JP is TOPIX 500 (Core30 + Large70 + Mid400),
starting from the JPX review file named there. Market cap is not a filter.

To publish a newer list, replace the CSV and update ``file``, ``as_of``, and
(for TOPIX) ``size_column`` in the manifest. The parsers stay the same as long
as the file layouts stay the same.

``INDEX_RANK_COHORT`` is the single ranking pool written onto every index
member. The scan trees still read the ``large`` slot (``SCTR.large``). It is
not a market-cap band, and there is no separate mid-cap cohort.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

INDEX_RANK_COHORT = "large"
TOPIX_500_SERIES = frozenset({"TOPIX Core30", "TOPIX Large70", "TOPIX Mid400"})

_US_EXCHANGES = {
    "NASDAQ": "XNAS",
    "NYSE": "XNYS",
    "NYSE MKT LLC": "XASE",
    "CBOE BZX": "BATS",
}


class ListError(ValueError):
    pass


def universe_dir() -> Path:
    """Find ``universes/manifest.json`` next to the repo, then in the cwd."""

    candidates = [
        Path(__file__).resolve().parents[2] / "universes",
        Path.cwd() / "universes",
    ]
    for root in candidates:
        if (root / "manifest.json").is_file():
            return root
    raise ListError("找不到 universes/manifest.json。指數名單要放在倉庫的 universes/ 目錄。")


def load_manifest(root: Path | None = None) -> tuple[Path, dict]:
    directory = root or universe_dir()
    path = directory / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ListError(f"讀不到指數名單清單：{exc}") from exc
    if "US" not in payload or "JP" not in payload:
        raise ListError("manifest.json 必須有 US 和 JP")
    return directory, payload


def load_constituents(root: Path | None = None) -> tuple[list[dict], list[dict], dict]:
    """Return ``(us_rows, jp_rows, info)`` for the lists named in the manifest."""

    directory, manifest = load_manifest(root)
    us_spec = manifest["US"]
    jp_spec = manifest["JP"]
    us_rows, us_info = load_iwb_holdings(directory / us_spec["file"], as_of=str(us_spec["as_of"]))
    jp_rows, jp_info = load_topix500(
        directory / jp_spec["file"],
        as_of=str(jp_spec["as_of"]),
        size_column=str(jp_spec.get("size_column") or "size_new_20261030"),
    )
    us_info["index_name"] = us_spec.get("name") or "Russell 1000"
    jp_info["index_name"] = jp_spec.get("name") or "TOPIX 500"
    info = {"manifest": manifest, "US": us_info, "JP": jp_info}
    return us_rows, jp_rows, info


def load_iwb_holdings(path: Path, *, as_of: str) -> tuple[list[dict], dict]:
    """Equity rows from an iShares holdings CSV. Cash, futures, and collateral leave."""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ListError(f"讀不到 IWB 持股檔 {path}：{exc}") from exc
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if line.startswith("Ticker,")), None)
    if start is None:
        raise ListError(f"{path} 沒有 iShares 的 Ticker 標題列")
    selected: list[dict] = []
    seen: set[str] = set()
    skipped: dict[str, int] = {}
    for row in csv.DictReader(lines[start:]):
        ticker = (row.get("Ticker") or "").strip()
        kind = (row.get("Type") or "").strip().upper()
        if not ticker or kind != "EQUITY":
            label = kind or "blank"
            skipped[label] = skipped.get(label, 0) + 1
            continue
        symbol = normalize_us_ticker(ticker)
        if not symbol:
            raise ListError(f"IWB 代號是空的：{ticker}")
        if symbol in seen:
            raise ListError(f"IWB 代號重複：{symbol}（原始 {ticker}）")
        seen.add(symbol)
        exchange = (row.get("Exchange") or "").strip()
        selected.append(
            {
                "market": "US",
                "symbol": symbol,
                "yahoo": symbol,
                "name": (row.get("Name") or "").strip(),
                "exchange": _US_EXCHANGES.get(exchange.upper(), exchange or "UNKNOWN"),
                "currency": (row.get("Currency") or "USD").strip().upper() or "USD",
                "source_ticker": ticker,
                "index_name": "Russell 1000",
                "as_of": as_of,
            }
        )
    if not selected:
        raise ListError(f"{path} 沒有 EQUITY 持股")
    return selected, {"rows": len(selected), "as_of": as_of, "skipped": skipped, "path": str(path)}


def load_topix500(path: Path, *, as_of: str, size_column: str) -> tuple[list[dict], dict]:
    """TOPIX 500 constituents. Series membership is not a market-cap cohort."""

    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ListError(f"讀不到 TOPIX 500 檔 {path}：{exc}") from exc
    reader = csv.DictReader(text.splitlines())
    if reader.fieldnames is None or "code" not in reader.fieldnames or "name" not in reader.fieldnames:
        raise ListError(f"{path} 必須有 code 和 name 欄")
    if size_column not in reader.fieldnames:
        raise ListError(f"{path} 沒有系列欄 {size_column}")
    selected: list[dict] = []
    seen: set[str] = set()
    series_counts: dict[str, int] = {}
    for row in reader:
        code = normalize_jp_code(row.get("code") or "")
        series = (row.get(size_column) or "").strip()
        if not code:
            continue
        if series not in TOPIX_500_SERIES:
            raise ListError(f"{code} 的 {size_column}={series or '空白'}，不是 TOPIX 500 的系列")
        if code in seen:
            raise ListError(f"TOPIX 代號重複：{code}")
        seen.add(code)
        series_counts[series] = series_counts.get(series, 0) + 1
        selected.append(
            {
                "market": "JP",
                "symbol": code,
                "yahoo": f"{code}.T",
                "name": (row.get("name") or "").strip(),
                "exchange": "XTKS",
                "currency": "JPY",
                "source_ticker": code,
                "index_name": "TOPIX 500",
                "index_series": series,
                "as_of": as_of,
            }
        )
    if not selected:
        raise ListError(f"{path} 沒有 TOPIX 500 成分")
    return selected, {
        "rows": len(selected),
        "as_of": as_of,
        "series": series_counts,
        "size_column": size_column,
        "path": str(path),
    }


def normalize_us_ticker(raw: str) -> str:
    """``BRK B``, ``BRK.B``, and ``BRK/B`` all become ``BRK-B``."""

    text = raw.strip().upper().replace("/", "-").replace(".", "-").replace(" ", "-")
    while "--" in text:
        text = text.replace("--", "-")
    return text.strip("-")


def normalize_jp_code(raw: str) -> str:
    """``7203``, ``7203.T``, and ``7203.0`` become the 4-character code."""

    text = raw.strip().upper()
    if text.endswith(".T"):
        text = text[:-2]
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if text.isdigit():
        return text.zfill(4)
    return text
