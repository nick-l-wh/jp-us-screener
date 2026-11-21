"""Reference membership versus engine predicates.

This module does not fetch data and does not treat a close match as a scan match.
A zero reference list is not evidence that the engine also has zero hits, unless
the candidate set is the full market that the reference scan was run on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from jp_us_screener.models import ClauseResult, Feature, RankInfo, Security
from jp_us_screener.scans import evaluate_tree, scans_by_id
from jp_us_screener.version import JP_ADAPT_MESSAGE

RANK_MODE_REFERENCE = "reference_sctr"
RANK_MODE_UNAVAILABLE = "unavailable"
COVERAGE_FULL = "full_market"
COVERAGE_HIT_UNION = "selection_biased_hit_union"

UNIVERSE_TO_COHORT = {
    "lrg": "large",
    "large": "large",
    "mid": "mid",
    "small": "small",
    "sml": "small",
}

# Account export columns we never copy into a published comparison.
FORBIDDEN_PUBLIC_KEYS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "adj_open",
    "adj_high",
    "adj_low",
    "last",
    "daily_ulcer",
    "name",
    "exchange",
    "sector",
    "industry",
}


@dataclass
class ReferenceMember:
    symbol: str
    name: str = ""
    exchange: str = ""
    sector: str = ""
    industry: str = ""
    sctr: float | None = None
    universe: str = ""


@dataclass
class Candidate:
    security: Security
    feature: Feature | None = None
    input_block: dict[str, str] | None = None
    withhold: dict[str, tuple[str, str]] | None = None


def reference_ranks(member: ReferenceMember | None) -> RankInfo:
    """Use a supplied StockCharts SCTR only as a diagnostic number.

    The  hit-selection sample is not ranked into a local cohort percentile.
    """
    if member is None or member.sctr is None or not member.universe:
        return RankInfo(
            cohort=None,
            cohort_source=None,
            own=None,
            large=None,
            mid=None,
            reason="reference_sctr_missing",
        )
    cohort = UNIVERSE_TO_COHORT.get(member.universe.strip().lower())
    if cohort not in {"large", "mid", "small"}:
        return RankInfo(
            cohort=cohort,
            cohort_source="reference_sctr",
            own=None,
            large=None,
            mid=None,
            reason="reference_cohort_not_large_or_mid",
        )
    value = float(member.sctr)
    if cohort == "small":
        return RankInfo(
            cohort="small",
            cohort_source="reference_sctr",
            own=value,
            large=None,
            mid=None,
            reason=None,
        )
    return RankInfo(
        cohort=cohort,
        cohort_source="reference_sctr",
        own=value,
        large=value if cohort == "large" else None,
        mid=value if cohort == "mid" else None,
        reason=None,
    )


def unavailable_ranks() -> RankInfo:
    return RankInfo(
        cohort=None,
        cohort_source=None,
        own=None,
        large=None,
        mid=None,
        reason="local_cohort_rank_not_computed",
    )


def _security_for_session(security: Security, session_date: str) -> tuple[Security, str | None]:
    if security.market_cap is None:
        return security, None
    if security.market_cap_as_of != session_date:
        cleared = replace(
            security,
            market_cap=None,
            market_cap_currency="",
            market_cap_as_of="",
        )
        return cleared, "market_cap_as_of_not_session"
    return security, None


def _status_of(kind: str, parts: list[dict[str, Any]]) -> str:
    statuses = [part["status"] for part in parts]
    if kind == "and":
        if "fail" in statuses:
            return "fail"
        if "unavailable" in statuses:
            return "unavailable"
        return "pass"
    if "pass" in statuses:
        return "pass"
    if "unavailable" in statuses:
        return "unavailable"
    return "fail"


def explain_tree(
    node: dict[str, Any],
    feature: Feature,
    security: Security,
    ranks: RankInfo,
    cap_override: str | None,
) -> dict[str, Any]:
    op = node["op"]
    if op in {"and", "or"}:
        children = [
            explain_tree(child, feature, security, ranks, cap_override) for child in node["children"]
        ]
        status = _status_of(op, children)
        if status == "pass":
            decisive: list[dict[str, Any]] = []
        elif status == "fail":
            decisive = [child for child in children if child["status"] == "fail"]
        else:
            decisive = [child for child in children if child["status"] == "unavailable"]
        return {
            "id": node.get("id", op),
            "label": node.get("label", op),
            "op": op,
            "status": status,
            "children": children,
            "decisive": decisive,
        }
    leaves: list[ClauseResult] = []
    result = evaluate_tree(node, feature, security, ranks, leaves)
    result = leaves[0]
    if cap_override and node.get("op") == "cap_usd":
        result = ClauseResult(
            result.clause_id,
            result.label,
            "unavailable",
            cap_override,
            f"市值日期不是所選交易日，不用其他日期的市值",
        )
    return {
        "id": result.clause_id,
        "label": result.label,
        "op": op,
        "status": result.status,
        "code": result.code,
        "detail": result.detail,
        "children": [],
        "decisive": [],
    }


def _flatten_decisive(node: dict[str, Any]) -> list[dict[str, Any]]:
    if node["op"] not in {"and", "or"}:
        return [node] if node["status"] != "pass" else []
    found: list[dict[str, Any]] = []
    for child in node["decisive"]:
        found.extend(_flatten_decisive(child) if child["op"] in {"and", "or"} else [child])
    return found


def _all_leaves(node: dict[str, Any]) -> list[dict[str, Any]]:
    if node["op"] not in {"and", "or"}:
        return [node]
    found: list[dict[str, Any]] = []
    for child in node["children"]:
        found.extend(_all_leaves(child))
    return found


def tighter_and_tree(scan: dict[str, Any]) -> dict[str, Any] | None:
    """Alternate reading: AND binds tighter than the leading OR.

    Source text is `large OR mid AND rest`. The provisional tree is
    `(large OR mid) AND rest`. The other reading is `large OR (mid AND rest)`.
    """
    if not scan.get("grouping_warning"):
        return None
    tree = scan["tree"]
    if tree.get("op") != "and" or not tree.get("children"):
        return None
    gate = tree["children"][0]
    if gate.get("op") != "or" or len(gate.get("children", [])) != 2:
        return None
    large, mid = gate["children"]
    rest = tree["children"][1:]
    return {
        "op": "or",
        "id": f"{scan['id']}-alt-or",
        "label": "另一讀法：大型成立，或（中型且其餘條件）",
        "children": [
            large,
            {
                "op": "and",
                "id": f"{scan['id']}-alt-and",
                "label": "中型且其餘條件",
                "children": [mid, *rest],
            },
        ],
    }


def _leaf_public(leaf: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": leaf["id"],
        "label": leaf["label"],
        "status": leaf["status"],
        "code": leaf.get("code"),
        "detail": leaf.get("detail"),
    }


def _withhold(node: dict[str, Any], withhold: dict[str, tuple[str, str]] | None) -> dict[str, Any]:
    if node["op"] not in {"and", "or"}:
        spec = None if not withhold else withhold.get(node["id"])
        if spec is None:
            return node
        code, detail = spec
        return {**node, "status": "unavailable", "code": code, "detail": detail}
    children = [_withhold(child, withhold) for child in node["children"]]
    status = _status_of(node["op"], children)
    if status == "pass":
        decisive: list[dict[str, Any]] = []
    elif status == "fail":
        decisive = [child for child in children if child["status"] == "fail"]
    else:
        decisive = [child for child in children if child["status"] == "unavailable"]
    return {**node, "children": children, "status": status, "decisive": decisive}


def _drop_clauses(node: dict[str, Any], clause_ids: set[str]) -> dict[str, Any] | None:
    if node["op"] not in {"and", "or"}:
        return None if node["id"] in clause_ids else node
    children = [child for child in (_drop_clauses(item, clause_ids) for item in node["children"]) if child]
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    status = _status_of(node["op"], children)
    if status == "pass":
        decisive: list[dict[str, Any]] = []
    elif status == "fail":
        decisive = [child for child in children if child["status"] == "fail"]
    else:
        decisive = [child for child in children if child["status"] == "unavailable"]
    return {**node, "children": children, "status": status, "decisive": decisive}


def _outcome_from_explained(node: dict[str, Any]) -> str:
    if node["status"] == "pass":
        return "match"
    if node["status"] == "fail":
        return "no_match"
    codes = [leaf.get("code") for leaf in _all_leaves(node) if leaf["status"] == "unavailable"]
    if codes and all(code in {None, "insufficient_warmup"} for code in codes):
        return "insufficient_warmup"
    return "unmet_dependency"


def diagnose(
    scan: dict[str, Any],
    candidate: Candidate,
    ranks: RankInfo,
    session_date: str,
) -> dict[str, Any]:
    if scan.get("jp_policy") == "needs_parameters" and candidate.security.market == "JP":
        return {
            "outcome": "unmet_dependency",
            "decision": "indeterminate",
            "confirmed_failed": [],
            "unresolved": [
                {
                    "id": "jp-parameters",
                    "label": JP_ADAPT_MESSAGE,
                    "status": "unavailable",
                    "code": "blocked_needs_jp_parameters",
                    "detail": "不把美元門檻套到日圓",
                }
            ],
            "alternate_outcome": None,
            "grouping_causal": False,
            "technical_outcome": "unmet_dependency",
            "alt_status": None,
        }
    if candidate.input_block is not None or candidate.feature is None:
        block = candidate.input_block or {
            "id": "missing-feature",
            "label": "沒有可計算的指標",
            "code": "missing_feature",
            "detail": "這隻股票沒有可用的目標日序列",
        }
        return {
            "outcome": "unmet_dependency",
            "decision": "indeterminate",
            "confirmed_failed": [],
            "unresolved": [
                {
                    "id": block["id"],
                    "label": block["label"],
                    "status": "unavailable",
                    "code": block["code"],
                    "detail": block["detail"],
                }
            ],
            "alternate_outcome": None,
            "grouping_causal": False,
            "technical_outcome": "unmet_dependency",
            "alt_status": None,
        }
    security, cap_override = _security_for_session(candidate.security, session_date)
    primary = _withhold(
        explain_tree(scan["tree"], candidate.feature, security, ranks, cap_override),
        candidate.withhold,
    )
    outcome = _outcome_from_explained(primary)
    failed = [_leaf_public(leaf) for leaf in _flatten_decisive(primary) if leaf["status"] == "fail"]
    unresolved = [
        _leaf_public(leaf) for leaf in _all_leaves(primary) if leaf["status"] == "unavailable"
    ]
    if outcome == "match" and not failed and not unresolved:
        decision = "engine_pass"
    elif failed:
        decision = "confirmed_failed_condition"
    else:
        decision = "indeterminate"
    alternate_outcome = None
    grouping_causal = False
    technical_outcome = outcome
    alternate = tighter_and_tree(scan)
    alt_explained = None
    dropped_ids = set(candidate.withhold or {})
    if dropped_ids:
        technical = _drop_clauses(primary, dropped_ids)
        technical_outcome = outcome if technical is None else _outcome_from_explained(technical)
    if alternate is not None:
        alt_explained = _withhold(
            explain_tree(alternate, candidate.feature, security, ranks, cap_override),
            candidate.withhold,
        )
        if dropped_ids:
            technical_primary = _drop_clauses(primary, dropped_ids)
            technical_alt = _drop_clauses(alt_explained, dropped_ids)
            if technical_primary is not None and technical_alt is not None:
                grouping_causal = _outcome_from_explained(technical_primary) != _outcome_from_explained(technical_alt)
                alternate_outcome = _outcome_from_explained(technical_alt)
            else:
                alternate_outcome = _outcome_from_explained(alt_explained)
                grouping_causal = alternate_outcome != technical_outcome
        else:
            alternate_outcome = _outcome_from_explained(alt_explained)
            grouping_causal = alternate_outcome != outcome
        if grouping_causal and decision == "engine_pass":
            decision = "indeterminate"
    return {
        "outcome": outcome,
        "decision": decision,
        "confirmed_failed": failed,
        "unresolved": unresolved,
        "alternate_outcome": alternate_outcome,
        "grouping_causal": grouping_causal,
        "technical_outcome": technical_outcome,
        "alt_status": None if alt_explained is None else alt_explained["status"],
    }


CLASSIFICATION_CODES = {"security_type_not_in_export", "etf_group_not_in_export"}


def block_kind(confirmed_failed: list[dict[str, Any]], unresolved: list[dict[str, Any]], decision: str) -> str:
    if decision == "engine_pass":
        return "engine_pass"
    if confirmed_failed:
        return "confirmed_failed_condition"
    codes = {item.get("code") for item in unresolved}
    if codes and codes <= CLASSIFICATION_CODES:
        return "classification_unresolved"
    if decision == "indeterminate" and not unresolved:
        return "grouping_unresolved"
    return "data_unresolved"


def compare_scan(
    *,
    scan_id: str,
    session_date: str,
    candidates: list[Candidate],
    reference: list[ReferenceMember],
    rank_mode: str,
    candidate_coverage: str,
    rank_lookup: dict[str, ReferenceMember] | None = None,
) -> dict[str, Any]:
    if rank_mode not in {RANK_MODE_REFERENCE, RANK_MODE_UNAVAILABLE}:
        raise ValueError(f"未知排名模式：{rank_mode}")
    if candidate_coverage not in {COVERAGE_FULL, COVERAGE_HIT_UNION}:
        raise ValueError(f"未知候選覆蓋：{candidate_coverage}")
    scan = scans_by_id()[scan_id]
    by_symbol = {item.security.symbol: item for item in candidates}
    reference_by_symbol = {item.symbol: item for item in reference}
    symbols = list(dict.fromkeys([*reference_by_symbol.keys(), *by_symbol.keys()]))
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        member = reference_by_symbol.get(symbol)
        candidate = by_symbol.get(symbol)
        in_reference = member is not None
        if candidate is None:
            rows.append(
                {
                    "symbol": symbol,
                    "name": member.name if member else "",
                    "exchange": member.exchange if member else "",
                    "sector": member.sector if member else "",
                    "industry": member.industry if member else "",
                    "reference_sctr": member.sctr if member else None,
                    "reference_universe": member.universe if member else "",
                    "in_reference": in_reference,
                    "engine_outcome": None,
                    "decision": "indeterminate",
                    "membership": "indeterminate",
                    "confirmed_failed": [],
                    "unresolved": [
                        {
                            "id": "no-candidate-bar",
                            "label": "沒有這隻股票的計算列",
                            "status": "unavailable",
                            "code": "missing_candidate",
                            "detail": "參考表有這隻股票，候選行情沒有它",
                        }
                    ],
                    "alternate_outcome": None,
                "grouping_causal": False,
                "technical_outcome": None,
                "block_kind": "data_unresolved",
                }
            )
            continue
        rank_member = member if member is not None else (rank_lookup or {}).get(symbol)
        ranks = reference_ranks(rank_member) if rank_mode == RANK_MODE_REFERENCE else unavailable_ranks()
        diagnosed = diagnose(scan, candidate, ranks, session_date)
        engine_hit = diagnosed["decision"] == "engine_pass"
        if diagnosed["grouping_causal"] or diagnosed["decision"] == "indeterminate":
            membership = "indeterminate"
        elif in_reference and engine_hit:
            membership = "overlap"
        elif in_reference and not engine_hit:
            membership = "missing"
        elif engine_hit and candidate_coverage == COVERAGE_FULL:
            membership = "extra"
        elif engine_hit:
            membership = "within_sample_extra"
        else:
            membership = "outside_reference_not_hit"
        rows.append(
            {
                "symbol": symbol,
                "name": member.name if member else candidate.security.name,
                "exchange": member.exchange if member else candidate.security.exchange,
                "sector": member.sector if member else "",
                "industry": member.industry if member else "",
                "reference_sctr": member.sctr if member else None,
                "reference_universe": member.universe if member else "",
                "in_reference": in_reference,
                "engine_outcome": diagnosed["outcome"],
                "decision": diagnosed["decision"],
                "membership": membership,
                "confirmed_failed": diagnosed["confirmed_failed"],
                "unresolved": diagnosed["unresolved"],
                "alternate_outcome": diagnosed["alternate_outcome"],
                "grouping_causal": diagnosed["grouping_causal"],
                "technical_outcome": diagnosed["technical_outcome"],
                "block_kind": block_kind(
                    diagnosed["confirmed_failed"],
                    diagnosed["unresolved"],
                    diagnosed["decision"],
                ),
            }
        )
    counts = {
        "reference": len(reference_by_symbol),
        "engine_pass": sum(1 for row in rows if row["decision"] == "engine_pass"),
        "overlap": sum(1 for row in rows if row["membership"] == "overlap"),
        "missing": sum(1 for row in rows if row["membership"] == "missing"),
        "extra": sum(1 for row in rows if row["membership"] == "extra"),
        "within_sample_extra": sum(1 for row in rows if row["membership"] == "within_sample_extra"),
        "indeterminate": sum(1 for row in rows if row["membership"] == "indeterminate"),
    }
    full_market = candidate_coverage == COVERAGE_FULL
    zero_validated = full_market and counts["reference"] == 0 and counts["engine_pass"] == 0 and counts["indeterminate"] == 0
    blockers: list[str] = []
    if not full_market:
        blockers.append("候選集不是全市場，不能計算全市場的多出或漏掉。")
    if counts["reference"] == 0 and not zero_validated:
        blockers.append("參考結果是 0。候選集沒有獨立的全市場代號，不能把「沒有基準代號」當成引擎也是 0。")
    if rank_mode == RANK_MODE_REFERENCE:
        blockers.append("排名用的是結果表上的參考 SCTR，不是本地族群排名。")
    elif rank_mode == RANK_MODE_UNAVAILABLE:
        blockers.append("沒有計算本地族群排名。命中選擇樣本不能當成完整 SCTR。")
    if scan.get("grouping_warning"):
        blockers.append("原文沒有括號。已同時計算（大型或中型）且其餘，以及大型或（中型且其餘）。兩者不一致時標成可能原因。")
    if any(
        leaf.get("code") in {"missing_market_cap", "market_cap_as_of_not_session", "market_cap_currency_not_usd"}
        for row in rows
        for leaf in row["unresolved"]
    ):
        blockers.append("所選交易日的美元市值不齊，相關市值條件維持未定，不用其他日期的市值。")
    return {
        "scan_id": scan_id,
        "scan_name": scan["name_zh"],
        "scan_name_en": scan["name_en"],
        "session_date": session_date,
        "rank_mode": rank_mode,
        "rank_mode_label": (
            "診斷用參考 SCTR，不是本地族群排名"
            if rank_mode == RANK_MODE_REFERENCE
            else "未計算本地族群排名"
        ),
        "candidate_coverage": candidate_coverage,
        "full_market_extra_computed": full_market,
        "full_market_missing_computed": full_market,
        "zero_reference_validated": zero_validated,
        "grouping_warning": scan.get("grouping_warning"),
        "counts": counts,
        "blockers": blockers,
        "rows": rows,
    }


def public_document(report: dict[str, Any]) -> dict[str, Any]:
    """Drop account price and volume fields. Keep membership and clause evidence."""

    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: scrub(item) for key, item in value.items() if key not in FORBIDDEN_PUBLIC_KEYS}
        if isinstance(value, list):
            return [scrub(item) for item in value]
        return value

    return scrub(report)
