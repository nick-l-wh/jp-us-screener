"""Local CSV in, snapshot out. `update` fetches real sessions and does not schedule."""

from __future__ import annotations

import argparse
import sys

from jp_us_screener.assumptions import export_rules
from jp_us_screener.config import load_config
from jp_us_screener.io import (
    ImportError_,
    atomic_write,
    atomic_write_json,
    load_ohlcv,
    load_universe,
    template_marketcap,
    template_ohlcv,
    template_universe,
    write_csv,
)
from jp_us_screener.pipeline import PipelineError, compute_snapshot, flat_match_rows, load_previous
from jp_us_screener.provider import NotConfiguredProvider, ProviderNotConfigured

CSV_FIELDS = [
    "data_notice",
    "scan_id",
    "scan_name_zh",
    "market",
    "qualified_symbol",
    "name",
    "cohort",
    "diff_status",
    "last_date",
    "technical_rank",
    "close",
    "volume",
    "market_cap",
    "market_cap_currency",
    "roc3",
    "roc20",
    "roc90",
    "ulcer10",
    "plus_di14",
    "adx14",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jp-us-screener", description="日美收市股票篩選")
    sub = parser.add_subparsers(dest="cmd", required=True)

    compute = sub.add_parser("compute", help="由 OHLCV 與宇宙檔計算快照")
    compute.add_argument("--ohlcv", required=True)
    compute.add_argument("--universe", required=True)
    compute.add_argument("--marketcap")
    compute.add_argument("--config")
    compute.add_argument("--previous")
    compute.add_argument("--out", required=True)
    compute.add_argument("--csv")
    compute.add_argument("--mode", choices=["real", "demo"], default="real")
    compute.add_argument("--generated-at")

    templates = sub.add_parser("templates", help="寫出 CSV 範本")
    templates.add_argument("--dir", required=True)

    rules = sub.add_parser("rules", help="寫出規則與假設 JSON")
    rules.add_argument("--out", required=True)

    provider = sub.add_parser("provider", help="顯示供應商狀態")
    provider.add_argument("--name", default="none")

    update = sub.add_parser("update", help="抓取已完成的美股和日股交易日，通過檢查後才更新公開快照")
    update.add_argument("--public-dir", default="web/public/data")
    update.add_argument("--config", default="config/default.json")
    update.add_argument("--cache-dir", default="data/update-cache")
    update.add_argument("--check", action="store_true", help="檢查指數名單。加上 --prices-dir 時只讀本機 CSV，不呼叫 Yahoo")
    update.add_argument("--prices-dir", default="", help="本機日線 CSV 目錄（Date, Open, High, Low, Close, AdjClose, Volume）。設定後不呼叫 Yahoo")

    args = parser.parse_args(argv)
    try:
        if args.cmd == "compute":
            return _compute(args)
        if args.cmd == "templates":
            return _templates(args.dir)
        if args.cmd == "rules":
            atomic_write_json(args.out, export_rules())
            print(f"已寫入 {args.out}")
            return 0
        if args.cmd == "provider":
            NotConfiguredProvider().fetch_daily_bars("US", [], "2024-01-01", "2024-01-02")
            return 0
        if args.cmd == "update":
            from jp_us_screener.update import main_update

            return main_update(args)
    except (ImportError_, PipelineError, ProviderNotConfigured) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — a crashed compute must not look like zero matches
        print(f"計算失敗：{exc}", file=sys.stderr)
        return 1
    return 2


def _compute(args: argparse.Namespace) -> int:
    bars = load_ohlcv(args.ohlcv)
    securities = load_universe(args.universe, args.marketcap)
    config = load_config(args.config)
    previous = load_previous(args.previous) if args.previous else None
    snapshot = compute_snapshot(
        bars,
        securities,
        config,
        mode=args.mode,
        generated_at=args.generated_at,
        previous=previous,
    )
    atomic_write_json(args.out, snapshot)
    if args.csv:
        write_csv(args.csv, CSV_FIELDS, flat_match_rows(snapshot))
    print(
        f"完成 {snapshot['snapshot_id']} 狀態 {snapshot['status']} "
        f"美國日期 {snapshot['markets']['US']['as_of']} 日本日期 {snapshot['markets']['JP']['as_of']}"
    )
    return 0


def _templates(directory: str) -> int:
    from pathlib import Path

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "ohlcv.csv", template_ohlcv())
    atomic_write(root / "universe.csv", template_universe())
    atomic_write(root / "marketcap.csv", template_marketcap())
    print(f"已寫入範本到 {root}")
    return 0
