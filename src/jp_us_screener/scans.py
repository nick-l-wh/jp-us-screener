"""Versioned scan predicates.

The three ungrouped scans keep their raw text and are evaluated with an explicit
provisional tree: (large OR mid) AND the remaining clauses. That is not a claim
of StockCharts parser parity.
"""

from __future__ import annotations

from typing import Any

from jp_us_screener.indicators import cmp_op
from jp_us_screener.models import ClauseResult, Feature, RankInfo, Security
from jp_us_screener.version import GROUPING_WARNING, JP_ADAPT_MESSAGE, MILLION

Node = dict[str, Any]


def _cmp(label: str, left: str, op: str, right: Any, clause_id: str) -> Node:
    return {"id": clause_id, "label": label, "op": "cmp", "left": left, "cmp": op, "right": right}


def _rank(label: str, cohort: str, op: str, value: float, clause_id: str) -> Node:
    return {"id": clause_id, "label": label, "op": "rank", "cohort": cohort, "cmp": op, "value": value}


def _flag(label: str, flag: str, clause_id: str) -> Node:
    return {"id": clause_id, "label": label, "op": "flag", "flag": flag}


def _cap(label: str, millions: float, clause_id: str) -> Node:
    return {
        "id": clause_id,
        "label": label,
        "op": "cap_usd",
        "cmp": ">",
        "millions": millions,
        "absolute_usd": millions * MILLION,
    }


def _and(*children: Node) -> Node:
    return {"op": "and", "children": list(children)}


def _or(*children: Node) -> Node:
    return {"op": "or", "children": list(children)}


RAW_ADX = """[SCTR.large > 0]
or [SCTR.mid > 0]
and [roc(90) > 20]
and [Plus DI(14) > yesterday's max(25, Plus DI(14))]
and [type is stock]
and [Volume > 100000]
and [SCTR > 70]"""

RAW_PULLBACK = """[SCTR.large > 70]
or [SCTR.mid > 70]
and [roc(20) > 20]
and [Plus DI(14) > 30]
and [type is stock]
rank by [Daily ULCER(10)]"""

RAW_SQUEEZE = """[type = stock]
and [country is US]
and [sma(20,volume) > 300000]
and [close > sma(200,close)]
and [sma(200,close) > 20 days ago sma(200,close)]
and [Upper Kelt Chan(20,1.5,10) > Upper BB(20,2)]
and [Lower Kelt Chan(20,1.5,10) < Lower BB(20,2)]
and [BB Width(20,2) = min(63, BB Width(20,2))]
and [Plus DI(14) < 20]
and [Minus DI(14) < 20]
and [market cap > 1,000]"""

RAW_TURTLE = """[type = stock]
and [country is us]
and [SMA(20, volume) > 500000]
and [group is not ETF]
and [close > 5]

//  20
and [low = min(20, low)]

//  20  3  OR
and [
    [3 days ago low = 1 day ago min(20, low)] or
    [4 days ago low = 1 day ago min(20, low)] or
    [5 days ago low = 1 day ago min(20, low)] or
    [6 days ago low = 1 day ago min(20, low)] or
    [7 days ago low = 1 day ago min(20, low)] or
    [8 days ago low = 1 day ago min(20, low)] or
    [9 days ago low = 1 day ago min(20, low)] or
    [10 days ago low = 1 day ago min(20, low)]
]

//  20
and [close <= 1 day ago min(20, low) * 1.005]"""

RAW_WEAK = """[type is stock]
and [Volume > 100000]
and
[
 [SCTR.large > 0]
 or [SCTR.mid > 0]
]
and [SCTR < 20]
and [ADX Line(14) < 20]
and [Plus DI(14) < yesterday's min(25, Plus DI(14))]
and [market cap > 10,000]"""

RAW_YOUNG = """[SCTR.large > 50]
or [SCTR.mid > 50]
and [roc(3) > 9]
and [SMA(3,volume)>SMA(50,volume)*1.5]
rank by ROC(3)"""


def scan_catalog() -> list[dict[str, Any]]:
    adx_gate = _or(
        _rank("大型族群技術排名 > 0", "large", ">", 0, "adx-large"),
        _rank("中型族群技術排名 > 0", "mid", ">", 0, "adx-mid"),
    )
    adx_gate["id"] = "adx-gate"
    adx_gate["label"] = "（暫定）大型或中型技術排名 > 0"
    pull_gate = _or(
        _rank("大型族群技術排名 > 70", "large", ">", 70, "pb-large"),
        _rank("中型族群技術排名 > 70", "mid", ">", 70, "pb-mid"),
    )
    pull_gate["id"] = "pb-gate"
    pull_gate["label"] = "（暫定）大型或中型技術排名 > 70"
    young_gate = _or(
        _rank("大型族群技術排名 > 50", "large", ">", 50, "yt-large"),
        _rank("中型族群技術排名 > 50", "mid", ">", 50, "yt-mid"),
    )
    young_gate["id"] = "yt-gate"
    young_gate["label"] = "（暫定）大型或中型技術排名 > 50"
    weak_gate = _or(
        _rank("大型族群技術排名 > 0", "large", ">", 0, "wk-large"),
        _rank("中型族群技術排名 > 0", "mid", ">", 0, "wk-mid"),
    )
    weak_gate["id"] = "wk-gate"
    weak_gate["label"] = "（原文括號）大型或中型技術排名 > 0"

    return [
        {
            "id": "adx",
            "source_id": "4468405",
            "name_en": "ADX",
            "name_zh": "ADX",
            "raw_source": RAW_ADX,
            "grouping": "provisional_cohort_and_rest",
            "grouping_warning": GROUPING_WARNING,
            "jp_policy": "run",
            "sort": None,
            "tree": _and(
                adx_gate,
                _cmp("90 日 ROC > 20", "roc90", ">", 20, "adx-roc90"),
                _cmp(
                    "今日 +DI(14) > 不含今日的前 25 根 +DI(14) 最高值",
                    "plus_di14",
                    ">",
                    {"ref": "prev25_max_plus_di"},
                    "adx-di",
                ),
                _flag("類型為股票", "is_stock", "adx-type"),
                _cmp("成交量（股數）> 100000", "volume", ">", 100000, "adx-vol"),
                _rank("所屬族群技術排名 > 70", "own", ">", 70, "adx-own"),
            ),
        },
        {
            "id": "pullback",
            "source_id": "4477104",
            "name_en": "Pullback",
            "name_zh": "回調",
            "raw_source": RAW_PULLBACK,
            "grouping": "provisional_cohort_and_rest",
            "grouping_warning": GROUPING_WARNING,
            "jp_policy": "run",
            "sort": {
                "field": "ulcer10",
                "direction": "desc",
                "direction_verified": False,
                "note": "Daily ULCER(10) 是排序不是過濾。預設較大值在前；方向未經來源核對。",
            },
            "tree": _and(
                pull_gate,
                _cmp("20 日 ROC > 20", "roc20", ">", 20, "pb-roc20"),
                _cmp("+DI(14) > 30", "plus_di14", ">", 30, "pb-di"),
                _flag("類型為股票", "is_stock", "pb-type"),
            ),
        },
        {
            "id": "squeeze",
            "source_id": "4536938",
            "name_en": "Squeeze On",
            "name_zh": "擠壓開啟",
            "raw_source": RAW_SQUEEZE,
            "grouping": "explicit_and",
            "grouping_warning": None,
            "jp_policy": "needs_parameters",
            "jp_message": JP_ADAPT_MESSAGE,
            "sort": None,
            "tree": _and(
                _flag("類型為股票", "is_stock", "sq-type"),
                _flag("國家為美國", "country_us", "sq-country"),
                _cmp("20 日平均成交量（股數）> 300000", "sma20_volume", ">", 300000, "sq-vol"),
                _cmp("收市 > SMA(200)", "close", ">", {"ref": "sma200_close"}, "sq-sma"),
                _cmp(
                    "SMA(200) > 20 根之前的 SMA(200)",
                    "sma200_close",
                    ">",
                    {"ref": "sma200_close_lag20"},
                    "sq-sma-lag",
                ),
                _cmp("Keltner 上軌 > 布林上軌", "kc_upper", ">", {"ref": "bb_upper"}, "sq-kcu"),
                _cmp("Keltner 下軌 < 布林下軌", "kc_lower", "<", {"ref": "bb_lower"}, "sq-kcl"),
                _cmp(
                    "布林寬度等於含今日的 63 根最小值",
                    "bb_width",
                    "=",
                    {"ref": "bb_width_min63"},
                    "sq-width",
                ),
                _cmp("+DI(14) < 20", "plus_di14", "<", 20, "sq-pdi"),
                _cmp("-DI(14) < 20", "minus_di14", "<", 20, "sq-mdi"),
                _cap("市值 > 1,000（百萬美元）即 > 1e9 USD", 1000, "sq-cap"),
            ),
        },
        {
            "id": "turtle",
            "source_id": "4554816",
            "name_en": "Turtle Soup Plus One - buy",
            "name_zh": "海龜湯加一（買入）",
            "raw_source": RAW_TURTLE,
            "grouping": "explicit_and",
            "grouping_warning": None,
            "jp_policy": "needs_parameters",
            "jp_message": JP_ADAPT_MESSAGE,
            "sort": None,
            "tree": _and(
                _flag("類型為股票", "is_stock", "tu-type"),
                _flag("國家為美國", "country_us", "tu-country"),
                _cmp("20 日平均成交量（股數）> 500000", "sma20_volume", ">", 500000, "tu-vol"),
                _flag("group 不是 ETF", "group_not_etf", "tu-etf"),
                _cmp("收市 > 5（美元；不換算成日圓）", "close", ">", 5, "tu-px"),
                _flag("今日低位等於含今日的 20 根最低值", "low_is_min20", "tu-low"),
                _flag("3 至 10 根前任一低位等於昨日的 20 根最低值", "turtle_prior_low", "tu-prior"),
                _cmp(
                    "收市 <= 昨日 20 根最低值 × 1.005",
                    "close",
                    "<=",
                    {"mul": ["yday_min20_low", 1.005]},
                    "tu-close",
                ),
            ),
        },
        {
            "id": "weak",
            "source_id": "4534703",
            "name_en": "Weak ADX",
            "name_zh": "弱 ADX",
            "raw_source": RAW_WEAK,
            "grouping": "explicit_brackets",
            "grouping_warning": None,
            "jp_policy": "run",
            "sort": None,
            "tree": _and(
                _flag("類型為股票", "is_stock", "wk-type"),
                _cmp("成交量（股數）> 100000", "volume", ">", 100000, "wk-vol"),
                weak_gate,
                _rank("所屬族群技術排名 < 20", "own", "<", 20, "wk-own"),
                _cmp("ADX(14) < 20", "adx14", "<", 20, "wk-adx"),
                _cmp(
                    "今日 +DI(14) < 不含今日的前 25 根 +DI(14) 最低值",
                    "plus_di14",
                    "<",
                    {"ref": "prev25_min_plus_di"},
                    "wk-di",
                ),
                _cap("市值 > 10,000（百萬美元）即 > 1e10 USD", 10000, "wk-cap"),
            ),
        },
        {
            "id": "young",
            "source_id": "4525267",
            "name_en": "Young Trend",
            "name_zh": "初生趨勢",
            "raw_source": RAW_YOUNG,
            "grouping": "provisional_cohort_and_rest",
            "grouping_warning": GROUPING_WARNING,
            "jp_policy": "run",
            "sort": {
                "field": "roc3",
                "direction": "desc",
                "direction_verified": False,
                "note": "ROC(3) 是排序不是過濾。預設較大值在前；方向未經來源核對。",
            },
            "tree": _and(
                young_gate,
                _cmp("3 日 ROC > 9", "roc3", ">", 9, "yt-roc"),
                _cmp(
                    "SMA(3,成交量) > SMA(50,成交量) × 1.5",
                    "sma3_volume",
                    ">",
                    {"mul": ["sma50_volume", 1.5]},
                    "yt-vol",
                ),
            ),
        },
    ]


def scans_by_id() -> dict[str, dict[str, Any]]:
    return {scan["id"]: scan for scan in scan_catalog()}


def jp_scan(scan: dict[str, Any]) -> dict[str, Any]:
    """Apply the owner's JPY rules to the JP scan, leaving the US source intact."""
    if scan["id"] not in {"squeeze", "weak", "turtle"}:
        return scan
    children = []
    for clause in scan["tree"]["children"]:
        if clause.get("flag") == "country_us":
            clause = _flag("日本股票", "country_jp", clause["id"])
        elif clause.get("op") == "cap_usd":
            clause = {"op": "cap_jpy", "id": clause["id"], "label": "市值 > 1,000 億日圓", "absolute_jpy": 100_000_000_000}
        elif clause.get("id") == "tu-px":
            clause = _cmp("收市 > 500 日圓", "close", ">", 500, "tu-px")
        children.append(clause)
    return {**scan, "tree": _and(*children), "jp_policy": "run"}


def evaluate_tree(
    node: Node,
    feature: Feature,
    security: Security,
    ranks: RankInfo,
    leaves: list[ClauseResult] | None = None,
) -> ClauseResult:
    op = node["op"]
    if op == "and":
        parts = [evaluate_tree(child, feature, security, ranks, leaves) for child in node["children"]]
        return _combine("and", parts, node.get("id", "and"), node.get("label", "同時成立"))
    if op == "or":
        parts = [evaluate_tree(child, feature, security, ranks, leaves) for child in node["children"]]
        return _combine("or", parts, node.get("id", "or"), node.get("label", "其一成立"))
    if op == "cmp":
        result = _eval_cmp(node, feature)
    elif op == "rank":
        result = _eval_rank(node, ranks)
    elif op == "flag":
        result = _eval_flag(node, feature, security)
    elif op in {"cap_usd", "cap_jpy"}:
        result = _eval_cap(node, security)
        if op == "cap_jpy" and security.market_cap_as_of != feature.date:
            result = ClauseResult(node["id"], node["label"], "unavailable", "missing_dated_market_cap", "欠當日市值")
    else:
        raise ValueError(f"未知謂詞：{op}")
    if leaves is not None:
        leaves.append(result)
    return result


def _combine(kind: str, parts: list[ClauseResult], clause_id: str, label: str) -> ClauseResult:
    codes = [part.code for part in parts if part.status == "unavailable" and part.code]
    detail = " | ".join(f"{part.label}:{part.status}" for part in parts)
    if kind == "and":
        if any(part.status == "fail" for part in parts):
            status = "fail"
        elif any(part.status == "unavailable" for part in parts):
            status = "unavailable"
        else:
            status = "pass"
    else:
        if any(part.status == "pass" for part in parts):
            status = "pass"
        elif any(part.status == "unavailable" for part in parts):
            status = "unavailable"
        else:
            status = "fail"
    code = None
    if status == "unavailable":
        if any(code and code != "insufficient_warmup" for code in codes):
            code = next(code for code in codes if code and code != "insufficient_warmup")
        else:
            code = "insufficient_warmup"
    return ClauseResult(clause_id, label, status, code, detail)


def _eval_cmp(node: Node, feature: Feature) -> ClauseResult:
    left = getattr(feature, node["left"])
    right, right_code = _resolve_right(node["right"], feature)
    label = node["label"]
    if left is None or right is None:
        return ClauseResult(node["id"], label, "unavailable", right_code or "insufficient_warmup", "數值尚未就緒")
    ok = cmp_op(node["cmp"], float(left), float(right))
    return ClauseResult(node["id"], label, "pass" if ok else "fail", None, f"{left} {node['cmp']} {right}")


def _resolve_right(spec: Any, feature: Feature) -> tuple[float | None, str | None]:
    if isinstance(spec, (int, float)):
        return float(spec), None
    if isinstance(spec, dict) and "ref" in spec:
        value = getattr(feature, spec["ref"])
        return (None if value is None else float(value)), "insufficient_warmup" if value is None else None
    if isinstance(spec, dict) and "mul" in spec:
        field, factor = spec["mul"]
        value = getattr(feature, field)
        if value is None:
            return None, "insufficient_warmup"
        return float(value) * float(factor), None
    raise ValueError(f"無法解析比較右值：{spec}")


def _eval_rank(node: Node, ranks: RankInfo) -> ClauseResult:
    label = node["label"]
    cohort = node["cohort"]
    if cohort == "own":
        if ranks.cohort is None:
            return ClauseResult(node["id"], label, "unavailable", _rank_code(ranks), ranks.reason)
        if ranks.own is None:
            return ClauseResult(node["id"], label, "unavailable", _rank_code(ranks), ranks.reason)
        ok = cmp_op(node["cmp"], ranks.own, float(node["value"]))
        return ClauseResult(node["id"], label, "pass" if ok else "fail", None, str(ranks.own))
    if ranks.cohort is None:
        return ClauseResult(node["id"], label, "unavailable", _rank_code(ranks), ranks.reason or "沒有族群")
    if ranks.cohort != cohort:
        return ClauseResult(node["id"], label, "fail", "not_in_cohort", f"所屬族群是 {ranks.cohort}")
    value = ranks.large if cohort == "large" else ranks.mid
    if value is None:
        return ClauseResult(node["id"], label, "unavailable", _rank_code(ranks), ranks.reason)
    ok = cmp_op(node["cmp"], value, float(node["value"]))
    return ClauseResult(node["id"], label, "pass" if ok else "fail", None, str(value))


def _rank_code(ranks: RankInfo) -> str:
    if ranks.reason in {"insufficient_warmup", "indicator_score_unavailable"}:
        return "insufficient_warmup"
    if ranks.reason == "insufficient_cohort":
        return "insufficient_cohort"
    return "unmet_dependency"


def _eval_flag(node: Node, feature: Feature, security: Security) -> ClauseResult:
    flag = node["flag"]
    label = node["label"]
    if flag == "is_stock":
        ok = security.security_type.strip().lower() == "stock"
        return ClauseResult(node["id"], label, "pass" if ok else "fail")
    if flag in {"country_us", "country_jp"}:
        ok = security.market == flag[-2:].upper()
        return ClauseResult(node["id"], label, "pass" if ok else "fail", None, security.market)
    if flag == "group_not_etf":
        group = (security.group or "").strip().lower()
        ok = group != "etf" and security.security_type.strip().lower() != "etf"
        return ClauseResult(node["id"], label, "pass" if ok else "fail")
    if flag in {"low_is_min20", "turtle_prior_low"}:
        value = getattr(feature, flag)
        if value is None:
            return ClauseResult(node["id"], label, "unavailable", "insufficient_warmup", "窗口不足")
        return ClauseResult(node["id"], label, "pass" if value else "fail")
    raise ValueError(f"未知旗標：{flag}")


def _eval_cap(node: Node, security: Security) -> ClauseResult:
    label = node["label"]
    if security.market_cap is None:
        return ClauseResult(node["id"], label, "unavailable", "missing_market_cap", "沒有市值")
    if not security.market_cap_currency:
        return ClauseResult(node["id"], label, "unavailable", "missing_market_cap_currency", "市值沒有貨幣")
    currency = "JPY" if node["op"] == "cap_jpy" else "USD"
    if security.market_cap_currency != currency:
        return ClauseResult(
            node["id"],
            label,
            "unavailable",
            "market_cap_currency_not_usd" if currency == "USD" else "market_cap_currency_mismatch",
            (f"條件單位是美元，檔案貨幣是 {security.market_cap_currency}，不會換算"
             if currency == "USD" else f"條件單位是日圓，資料為 {security.market_cap_currency}"),
        )
    threshold = float(node["absolute_" + currency.lower()])
    ok = cmp_op(">", security.market_cap, threshold)
    return ClauseResult(
        node["id"],
        label,
        "pass" if ok else "fail",
        None,
        f"{security.market_cap} > {threshold}",
    )


def clause_labels(node: Node) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []

    def walk(current: Node) -> None:
        if current["op"] in {"and", "or"}:
            if current.get("label") and current.get("id"):
                found.append({"id": current["id"], "label": current["label"], "op": current["op"]})
            for child in current["children"]:
                walk(child)
            return
        found.append({"id": current["id"], "label": current["label"], "op": current["op"]})

    walk(node)
    return found


def outcome_of(root: ClauseResult) -> str:
    if root.status == "pass":
        return "match"
    if root.status == "fail":
        return "no_match"
    if root.code == "insufficient_warmup":
        return "insufficient_warmup"
    return "unmet_dependency"


DEPENDENCY_CODES = {
    "missing_market_cap",
    "missing_market_cap_currency",
    "market_cap_currency_not_usd",
    "unmet_dependency",
    "insufficient_cohort",
    "cohort_not_configured",
    "invalid_cohort",
    "market_cap_currency_mismatch",
    "cap_outside_configured_cohorts",
}
