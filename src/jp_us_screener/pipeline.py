"""Normalize, rank, scan, and diff. Writes nothing; the CLI writes after success."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from jp_us_screener.assumptions import ASSUMPTIONS
from jp_us_screener.config import EngineConfig
from jp_us_screener.features import compute_features
from jp_us_screener.io import ImportError_
from jp_us_screener.models import Bar, Feature, RankInfo, Security
from jp_us_screener.scans import evaluate_tree, jp_scan, outcome_of, scan_catalog
from jp_us_screener.sctr import RANK_METHOD, percentile_ranks
from jp_us_screener.universe import resolve_cohort, screen_security
from jp_us_screener.validate import validate_bars
from jp_us_screener.version import (
    DEMO_DISCLAIMER,
    ENGINE_VERSION,
    GROUPING_WARNING,
    JP_ADAPT_MESSAGE,
    RANK_LABEL,
    SCHEMA_VERSION,
    SEMANTICS_VERSION,
)

INDICATOR_FIELDS = [
    "plus_di14",
    "minus_di14",
    "adx14",
    "roc3",
    "roc20",
    "roc90",
    "sma3_volume",
    "sma20_volume",
    "sma50_volume",
    "sma200_close",
    "sma200_close_lag20",
    "ulcer10",
    "bb_width",
    "bb_width_min63",
    "kc_upper",
    "kc_lower",
    "bb_upper",
    "bb_lower",
    "prev25_max_plus_di",
    "prev25_min_plus_di",
    "yday_min20_low",
    "technical_score",
    "rsi14",
    "ppo_hist_slope3",
    "ppo_slope_points",
]

JUDGED = {
    "eligible",
    "stale",
    "excluded_etf",
    "excluded_unknown_type",
    "excluded_not_stock",
    "excluded_market",
    "excluded_listing",
    "eligibility_unresolved",
}


class PipelineError(Exception):
    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.details = details or {}


@dataclass
class Prepared:
    security: Security
    bucket: str
    reason: str | None = None
    errors: list[str] = field(default_factory=list)
    features: list[Feature] = field(default_factory=list)
    feature: Feature | None = None
    ranks: RankInfo | None = None
    market_as_of: str | None = None


def compute_snapshot(
    bars: list[Bar],
    securities: list[Security],
    config: EngineConfig,
    *,
    mode: str = "real",
    generated_at: str | None = None,
    previous: dict | None = None,
) -> dict[str, Any]:
    if mode not in {"real", "demo"}:
        raise PipelineError(f"mode 必須是 real 或 demo：{mode}")
    if not bars or not securities:
        raise PipelineError("沒有行情或沒有宇宙檔，拒絕寫出空白命中。")

    prepared = _prepare(bars, securities, config)
    if not any(item.bucket in JUDGED for item in prepared):
        raise PipelineError(
            "沒有任何可判定的證券，拒絕以空白命中覆寫舊快照。",
            {"diagnostics": _universe_diagnostics(prepared)},
        )

    _assign_ranks(prepared, config)
    snapshot = _assemble(prepared, config, mode, generated_at, previous)
    snapshot["snapshot_id"] = _snapshot_id(snapshot)
    return snapshot


def _prepare(bars: list[Bar], securities: list[Security], config: EngineConfig) -> list[Prepared]:
    by_key: dict[tuple[str, str], list[Bar]] = defaultdict(list)
    for bar in bars:
        by_key[(bar.market, bar.symbol)].append(bar)
    meta = {(item.market, item.symbol): item for item in securities}
    keys = set(by_key) | set(meta)
    prepared: list[Prepared] = []
    for key in sorted(keys):
        security = meta.get(key)
        series = by_key.get(key, [])
        if security is None:
            placeholder = Security(
                market=key[0],
                symbol=key[1],
                name="",
                security_type="unknown",
                listing_segment="",
                exchange="",
                currency="",
                cohort="",
                market_cap=None,
                market_cap_currency="",
                market_cap_as_of="",
                group="",
            )
            prepared.append(Prepared(placeholder, "data_error", "宇宙檔沒有這筆代號，不會假設為股票"))
            continue
        if not series:
            prepared.append(Prepared(security, "data_error", "沒有行情列"))
            continue
        errors = validate_bars(series, config)
        if errors:
            prepared.append(Prepared(security, "data_error", errors[0], errors=errors))
            continue
        bucket, reason = screen_security(security, config)
        item = Prepared(security, bucket, reason)
        if bucket == "eligible":
            item.features = compute_features(series)
            item.feature = item.features[-1] if item.features else None
        prepared.append(item)

    as_of: dict[str, str] = {}
    for item in prepared:
        if item.bucket == "eligible" and item.feature is not None:
            current = as_of.get(item.security.market)
            if current is None or item.feature.date > current:
                as_of[item.security.market] = item.feature.date
    for item in prepared:
        if item.bucket != "eligible" or item.feature is None:
            continue
        market_as_of = as_of.get(item.security.market)
        if market_as_of and item.feature.date < market_as_of:
            item.bucket = "stale"
            item.reason = f"最後交易柱 {item.feature.date} 早於該市場 {market_as_of}，沒有向前填價"
            item.feature = None
    for item in prepared:
        item.market_as_of = as_of.get(item.security.market)
    return prepared


def _assign_ranks(prepared: list[Prepared], config: EngineConfig) -> None:
    grouped: dict[tuple[str, str], list[Prepared]] = defaultdict(list)
    for item in prepared:
        if item.bucket != "eligible" or item.feature is None:
            continue
        cohort, source, reason = resolve_cohort(item.security, config)
        if cohort is None:
            item.ranks = RankInfo(None, source, None, None, None, reason or "cohort_unavailable")
            continue
        if item.feature.technical_score is None:
            item.ranks = RankInfo(cohort, source, None, None, None, "indicator_score_unavailable")
            continue
        item.ranks = RankInfo(cohort, source, None, None, None, None)
        grouped[(item.security.market, cohort)].append(item)

    for (market, cohort), members in grouped.items():
        scores = {
            item.security.qualified_symbol: item.feature.technical_score
            for item in members
            if item.feature is not None and item.feature.technical_score is not None
        }
        ranks = percentile_ranks(scores, config.min_cohort_size)
        for item in members:
            assert item.ranks is not None
            item.ranks.cohort_size = len(scores)
            if ranks is None:
                item.ranks.reason = "insufficient_cohort"
                continue
            value = ranks[item.security.qualified_symbol]
            item.ranks.own = value
            if cohort == "large":
                item.ranks.large = value
            elif cohort == "mid":
                item.ranks.mid = value


def _assemble(
    prepared: list[Prepared],
    config: EngineConfig,
    mode: str,
    generated_at: str | None,
    previous: dict | None,
) -> dict[str, Any]:
    notice = DEMO_DISCLAIMER if mode == "demo" else None
    markets: dict[str, dict[str, Any]] = {}
    for market in ("US", "JP"):
        dates = [item.market_as_of for item in prepared if item.security.market == market]
        as_of = next((value for value in dates if value), None)
        eligible = sum(1 for item in prepared if item.security.market == market and item.bucket == "eligible")
        stale = sum(1 for item in prepared if item.security.market == market and item.bucket == "stale")
        markets[market] = {
            "as_of": as_of,
            "eligible_stocks": eligible,
            "stale": stale,
            "calendar": "回看使用交易柱。沒有假期向前填價，也沒有內建交易所假期表。",
        }

    scans_out: dict[str, Any] = {}
    for scan in scan_catalog():
        markets_out = {}
        for market in ("US", "JP"):
            rule = jp_scan(scan) if market == "JP" and mode == "real" else scan
            markets_out[market] = _evaluate_market(rule, market, prepared, notice)
        scans_out[scan["id"]] = {
            "id": scan["id"],
            "source_id": scan["source_id"],
            "name_en": scan["name_en"],
            "name_zh": scan["name_zh"],
            "raw_source": scan["raw_source"],
            "grouping": scan["grouping"],
            "grouping_warning": scan["grouping_warning"],
            "jp_policy": "run" if mode == "real" else scan["jp_policy"],
            "sort": scan["sort"],
            "markets": markets_out,
        }

    if previous is not None:
        _apply_diff(scans_out, previous)
    else:
        for scan in scans_out.values():
            for market_payload in scan["markets"].values():
                for row in market_payload["matches"]:
                    row["diff_status"] = "no_baseline"

    diag_counts = _count_buckets(prepared)
    status = "complete_with_diagnostics" if any(diag_counts.values()) or _scan_diag(scans_out) else "complete"
    return {
        "schema_version": SCHEMA_VERSION,
        "engine_version": ENGINE_VERSION,
        "semantics_version": SEMANTICS_VERSION if mode == "demo" else SEMANTICS_VERSION + ".jp-yen",
        "grouping_warning": GROUPING_WARNING,
        "generated_at": generated_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": mode,
        "demo_disclaimer": notice,
        "status": status,
        "rank_label": RANK_LABEL,
        "config": config.to_dict(),
        "assumptions": [
            {**note, "title": "日股門檻", "body": "Squeeze On 及 Weak ADX 的市值須大於 1,000 億日圓，Turtle Soup Plus One 的收市價須大於 500 日圓。"}
            if mode == "real" and note["id"] == "jp-us-only-scans" else note for note in ASSUMPTIONS
        ],
        "rank_method": RANK_METHOD,
        "markets": markets,
        "cohort_ranks": _cohort_ranks(prepared),
        "universe_diagnostics": _universe_diagnostics(prepared),
        "diagnostic_counts": diag_counts,
        "scans": scans_out,
        "diff": {
            "baseline_id": None if previous is None else previous.get("snapshot_id"),
            "note": "無上一成功快照，不計算新增、保留、剔除。" if previous is None else "相對於上一成功快照。",
        },
    }


def _evaluate_market(scan: dict, market: str, prepared: list[Prepared], notice: str | None) -> dict[str, Any]:
    if scan["jp_policy"] == "needs_parameters" and market == "JP":
        return {
            "evaluation": "blocked_needs_jp_parameters",
            "message": scan.get("jp_message", JP_ADAPT_MESSAGE),
            "matches": [],
            "dropped": [],
            "counts": {"matches": 0, "no_match": 0, "unmet_dependency": 0, "insufficient_warmup": 0},
            "diagnostics": {"unmet_dependency": [], "insufficient_warmup": []},
        }
    matches = []
    unmet = []
    warmup = []
    no_match = 0
    for item in prepared:
        if item.security.market != market or item.bucket != "eligible" or item.feature is None:
            continue
        ranks = item.ranks or RankInfo(None, None, None, None, None, "cohort_unavailable")
        leaves: list = []
        root = evaluate_tree(scan["tree"], item.feature, item.security, ranks, leaves)
        outcome = outcome_of(root)
        row = _row(item, ranks, leaves, notice)
        if outcome == "match":
            matches.append(row)
        elif outcome == "no_match":
            no_match += 1
        elif outcome == "insufficient_warmup":
            row["reason"] = root.detail
            warmup.append(row)
        else:
            row["reason"] = root.detail
            unmet.append(row)
    matches = _sort_rows(matches, scan["sort"])
    evaluation = "complete"
    if not matches and not unmet and not warmup:
        evaluation = "complete_zero_matches"
    elif unmet or warmup:
        evaluation = "complete_with_diagnostics"
    return {
        "evaluation": evaluation,
        "message": "已完成計算，本快照沒有命中。" if evaluation == "complete_zero_matches" else None,
        "matches": matches,
        "dropped": [],
        "counts": {
            "matches": len(matches),
            "no_match": no_match,
            "unmet_dependency": len(unmet),
            "insufficient_warmup": len(warmup),
        },
        "diagnostics": {"unmet_dependency": unmet, "insufficient_warmup": warmup},
    }


def _row(item: Prepared, ranks: RankInfo, leaves: list, notice: str | None) -> dict[str, Any]:
    feature = item.feature
    assert feature is not None
    security = item.security
    spark = [point.close for point in item.features[-80:]]
    indicators = {name: getattr(feature, name) for name in INDICATOR_FIELDS}
    return {
        "qualified_symbol": security.qualified_symbol,
        "market": security.market,
        "symbol": security.symbol,
        "name": security.name,
        "cohort": ranks.cohort,
        "cohort_source": ranks.cohort_source,
        "technical_rank": ranks.own,
        "technical_rank_large": ranks.large,
        "technical_rank_mid": ranks.mid,
        "rank_label": RANK_LABEL,
        "rank_reason": ranks.reason,
        "last_date": feature.date,
        "diff_status": "retained",
        "data_notice": notice or "",
        "close": feature.close,
        "volume": feature.volume,
        "market_cap": security.market_cap,
        "market_cap_currency": security.market_cap_currency or None,
        "indicators": indicators,
        "sparkline": spark,
        "clauses": [
            {"id": clause.clause_id, "label": clause.label, "status": clause.status, "code": clause.code, "detail": clause.detail}
            for clause in leaves
        ],
    }


def _sort_rows(rows: list[dict], spec: dict | None) -> list[dict]:
    if not spec:
        return sorted(rows, key=lambda row: row["qualified_symbol"])
    field = spec["field"]
    descending = spec["direction"] == "desc"

    def key(row: dict):
        value = row["indicators"].get(field)
        if descending:
            return (value is None, 0 if value is None else -value, row["qualified_symbol"])
        return (value is None, 0 if value is None else value, row["qualified_symbol"])

    return sorted(rows, key=key)


def _apply_diff(scans_out: dict, previous: dict) -> None:
    prev_scans = previous.get("scans") or {}
    for scan_id, scan in scans_out.items():
        prev_scan = prev_scans.get(scan_id) or {}
        prev_markets = prev_scan.get("markets") or {}
        for market, payload in scan["markets"].items():
            prev_payload = prev_markets.get(market) or {}
            prev_rows = {row["qualified_symbol"]: row for row in prev_payload.get("matches") or []}
            current_ids = {row["qualified_symbol"] for row in payload["matches"]}
            for row in payload["matches"]:
                row["diff_status"] = "retained" if row["qualified_symbol"] in prev_rows else "new"
            dropped = []
            for symbol, row in sorted(prev_rows.items()):
                if symbol not in current_ids:
                    copy = dict(row)
                    copy["diff_status"] = "dropped"
                    dropped.append(copy)
            payload["dropped"] = dropped
            payload["counts"]["new"] = sum(1 for row in payload["matches"] if row["diff_status"] == "new")
            payload["counts"]["retained"] = sum(1 for row in payload["matches"] if row["diff_status"] == "retained")
            payload["counts"]["dropped"] = len(dropped)


def _cohort_ranks(prepared: list[Prepared]) -> dict[str, Any]:
    table: dict[str, Any] = {"US": {}, "JP": {}, "unranked": []}
    for item in prepared:
        if item.bucket != "eligible" or item.ranks is None:
            continue
        if item.ranks.own is None or item.ranks.cohort is None:
            table["unranked"].append(
                {
                    "qualified_symbol": item.security.qualified_symbol,
                    "market": item.security.market,
                    "cohort": item.ranks.cohort,
                    "reason": item.ranks.reason,
                    "rank_label": RANK_LABEL,
                }
            )
            continue
        bucket = table.setdefault(item.security.market, {}).setdefault(item.ranks.cohort, [])
        bucket.append(
            {
                "qualified_symbol": item.security.qualified_symbol,
                "technical_rank": item.ranks.own,
                "technical_score": None if item.feature is None else item.feature.technical_score,
                "rank_label": RANK_LABEL,
            }
        )
    for market, cohorts in table.items():
        if market == "unranked":
            continue
        for rows in cohorts.values():
            rows.sort(key=lambda row: (-row["technical_rank"], row["qualified_symbol"]))
    return table


def _universe_diagnostics(prepared: list[Prepared]) -> dict[str, list[dict[str, str]]]:
    buckets = [
        "excluded_etf",
        "excluded_unknown_type",
        "excluded_not_stock",
        "excluded_market",
        "excluded_listing",
        "eligibility_unresolved",
        "data_error",
        "stale",
    ]
    out = {name: [] for name in buckets}
    for item in prepared:
        if item.bucket not in out:
            continue
        out[item.bucket].append(
            {
                "qualified_symbol": item.security.qualified_symbol,
                "name": item.security.name,
                "reason": item.reason or "",
            }
        )
    return out


def _count_buckets(prepared: list[Prepared]) -> dict[str, int]:
    diag = _universe_diagnostics(prepared)
    return {key: len(value) for key, value in diag.items()}


def _scan_diag(scans_out: dict) -> bool:
    for scan in scans_out.values():
        for market in scan["markets"].values():
            if market["diagnostics"]["unmet_dependency"] or market["diagnostics"]["insufficient_warmup"]:
                return True
    return False


def _snapshot_id(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key != "snapshot_id"}
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def flat_match_rows(snapshot: dict) -> list[dict[str, Any]]:
    rows = []
    notice = snapshot.get("demo_disclaimer") or ""
    if snapshot.get("mode") == "demo":
        rows.append(
            {
                "data_notice": notice,
                "scan_id": "",
                "scan_name_zh": "",
                "market": "",
                "qualified_symbol": "",
                "name": "",
                "cohort": "",
                "diff_status": "",
                "last_date": "",
                "technical_rank": "",
                "close": "",
                "volume": "",
                "market_cap": "",
                "market_cap_currency": "",
                "roc3": "",
                "roc20": "",
                "roc90": "",
                "ulcer10": "",
                "plus_di14": "",
                "adx14": "",
            }
        )
    for scan_id, scan in snapshot["scans"].items():
        for market, payload in scan["markets"].items():
            for kind in ("matches", "dropped"):
                for row in payload.get(kind) or []:
                    indicators = row.get("indicators") or {}
                    rows.append(
                        {
                            "data_notice": row.get("data_notice") or notice,
                            "scan_id": scan_id,
                            "scan_name_zh": scan["name_zh"],
                            "market": market,
                            "qualified_symbol": row["qualified_symbol"],
                            "name": row.get("name", ""),
                            "cohort": row.get("cohort") or "",
                            "diff_status": row.get("diff_status", ""),
                            "last_date": row.get("last_date", ""),
                            "technical_rank": row.get("technical_rank", ""),
                            "close": row.get("close", ""),
                            "volume": row.get("volume", ""),
                            "market_cap": row.get("market_cap", ""),
                            "market_cap_currency": row.get("market_cap_currency") or "",
                            "roc3": indicators.get("roc3", ""),
                            "roc20": indicators.get("roc20", ""),
                            "roc90": indicators.get("roc90", ""),
                            "ulcer10": indicators.get("ulcer10", ""),
                            "plus_di14": indicators.get("plus_di14", ""),
                            "adx14": indicators.get("adx14", ""),
                        }
                    )
    return rows


def load_previous(path: str) -> dict:
    import json
    from pathlib import Path

    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportError_(f"無法讀取上一成功快照：{exc}") from exc
