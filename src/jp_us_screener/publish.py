"""Validate a real snapshot and replace the public files only after that succeeds."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

from jp_us_screener.config import CohortThresholds, EngineConfig, default_thresholds
from jp_us_screener.io import atomic_write_json
from jp_us_screener.version import SEMANTICS_VERSION

SCAN_IDS = ("adx", "pullback", "squeeze", "turtle", "weak", "young")
PUBLICATION_STATUSES = frozenset({"valid", "not_valid", "incomplete"})


class UpdateError(RuntimeError):
    pass


def config_problem(config: EngineConfig) -> str | None:
    """The daily publish keeps the live scan rules and market-cap cohorts."""

    if config.adr_policy != "unresolved" or config.otc_policy != "unresolved":
        return "ADR 或 OTC 政策不是未決議，這次更新不會改掃描規則"
    if config.adjustment_required != "split_and_dividend":
        return "調整政策必須是 split_and_dividend"
    if config.volume_basis != "unadjusted_shares":
        return "成交量基準必須是 unadjusted_shares"
    if config.min_cohort_size != 5:
        return "min_cohort_size 必須維持 5"
    expected = default_thresholds()
    for market, rule in expected.items():
        got = config.cohort_thresholds.get(market)
        if got is None or not got.enabled:
            return f"{market} 市值分層未啟用"
        for field in ("currency", "large_min", "mid_min", "mid_max", "small_min"):
            if getattr(got, field) != getattr(rule, field):
                return f"{market} {field} 與已公布門檻不同"
    return None


def scan_publication_status(block: dict) -> str:
    """Map an engine block to the label the page shows.

    A completed zero has real `no_match` rows and stays `valid`.
    A block that never judged a security is not shown as zero hits.
    """

    if block.get("evaluation") == "blocked_needs_jp_parameters":
        return "not_valid"
    counts = block.get("counts") or {}
    matches = int(counts.get("matches") or 0)
    no_match = int(counts.get("no_match") or 0)
    unmet = int(counts.get("unmet_dependency") or 0)
    warmup = int(counts.get("insufficient_warmup") or 0)
    judged = matches + no_match
    if judged <= 0 and warmup > 0 and unmet <= 0:
        return "incomplete"
    if judged <= 0:
        return "not_valid"
    return "valid"


def _cohort_counts(snapshot: dict, market: str) -> tuple[int, int, int, int]:
    ranks = (snapshot.get("cohort_ranks") or {}).get(market) or {}
    large = len(ranks.get("large") or [])
    mid = len(ranks.get("mid") or [])
    unranked = [row for row in (snapshot.get("cohort_ranks") or {}).get("unranked") or [] if row.get("market") == market]
    large += sum(1 for row in unranked if row.get("cohort") == "large")
    mid += sum(1 for row in unranked if row.get("cohort") == "mid")
    outside = sum(1 for row in unranked if row.get("reason") == "cap_outside_configured_cohorts")
    missing = sum(1 for row in unranked if row.get("reason") == "missing_market_cap")
    return large, mid, outside, missing


def build_publication(snapshot: dict, notes: dict[str, str] | None = None) -> dict:
    us = snapshot["markets"]["US"]["as_of"]
    jp = snapshot["markets"]["JP"]["as_of"]
    scans = {
        market: {scan_id: scan_publication_status(snapshot["scans"][scan_id]["markets"][market]) for scan_id in SCAN_IDS}
        for market in ("US", "JP")
    }
    us_large, us_mid, us_out, us_miss = _cohort_counts(snapshot, "US")
    jp_large, jp_mid, jp_out, jp_miss = _cohort_counts(snapshot, "JP")
    sentences = [
        f"美國數據日期 {us}。日本數據日期 {jp}。",
        (
            f"大型與中型按市值劃分。美國大型大於 1,000 億美元（{us_large} 檔），"
            f"中型大於 250 億至 1,000 億美元（{us_mid} 檔）。"
            f"日本大型大於 1 兆日圓（{jp_large} 檔），中型大於 2,500 億至 1 兆日圓（{jp_mid} 檔）。"
        ),
        (
            f"低過中型下限：美股 {us_out} 檔、日股 {jp_out} 檔。"
            f"沒有市值：美股 {us_miss} 檔、日股 {jp_miss} 檔。沒有市值不會當成 0，也不會歸入族群。"
        ),
        "有 Yahoo 報價時，市值用流通股數乘以該交易日收市價；報價同時有股價和市值、兩者相差超過 1% 就不用這次報價。沒有新報價時沿用已保存的流通股數乘以該交易日收市價，股本觀察日另註明。沒有流通股數就留空，不會補 0。",
    ]
    for market, label in (("US", "美股"), ("JP", "日股")):
        hits = []
        zeros = []
        blocked = []
        for scan_id in SCAN_IDS:
            scan = snapshot["scans"][scan_id]
            block = scan["markets"][market]
            status = scans[market][scan_id]
            name = scan.get("name_zh") or scan_id
            matches = int(block["counts"]["matches"])
            if status == "valid" and matches == 0:
                zeros.append(name)
            elif status != "valid":
                blocked.append(name)
            elif matches <= 8:
                symbols = "、".join(row["symbol"] for row in block["matches"])
                hits.append(f"{name} {matches} 筆（{symbols}）")
            else:
                hits.append(f"{name} {matches} 筆")
        sentence = f"{label}：" + ("、".join(hits) if hits else "沒有列出命中")
        if zeros:
            sentence += "。算完的 0 筆：" + "、".join(zeros)
        if blocked:
            sentence += "。沒有有效結果、不會顯示成 0：" + "、".join(blocked)
        sentence += "。"
        sentences.append(sentence)
    us_eligible = snapshot["markets"]["US"].get("eligible_stocks")
    jp_eligible = snapshot["markets"]["JP"].get("eligible_stocks")
    sentences.append(f"納入計算：美股 {us_eligible} 檔、日股 {jp_eligible} 檔。名單不是全市場。")
    for text in (notes or {}).values():
        if text:
            sentences.append(text)
    sentences.append("沒有內建交易所假期表。預期交易日沒有足夠完整行情時沿用上一個密集交易日；與已公布日期相同則不覆寫。下載或計算失敗會保留上一個公開快照。")
    return {
        "snapshot_id": snapshot["snapshot_id"],
        "as_of": us if us == jp else max(us, jp),
        "sessions": {"US": us, "JP": jp},
        "title": f"已公布 {us}" if us == jp else f"已公布 美國 {us}、日本 {jp}",
        "body": "".join(sentences),
        "scans": scans,
    }


def validate_public_snapshot(snapshot: dict, sessions: dict[str, date], today: date) -> list[str]:
    errors: list[str] = []
    if snapshot.get("mode") != "real":
        errors.append("只公布 real，不公布示範")
    if snapshot.get("demo_disclaimer"):
        errors.append("示範聲明不是空的")
    if snapshot.get("semantics_version") != SEMANTICS_VERSION + ".jp-yen":
        errors.append("規則版本與目前引擎不一致")
    if snapshot.get("status") not in {"complete", "complete_with_diagnostics"}:
        errors.append(f"快照狀態不可公布：{snapshot.get('status')}")
    try:
        config = _config_from_snapshot(snapshot.get("config") or {})
    except (TypeError, ValueError) as exc:
        errors.append(f"快照裡的設定不能讀：{exc}")
    else:
        problem = config_problem(config)
        if problem:
            errors.append(problem)
    for market, session in sessions.items():
        block = (snapshot.get("markets") or {}).get(market) or {}
        as_of = block.get("as_of")
        if as_of != session.isoformat():
            errors.append(f"{market} 快照日期 {as_of} 不是 {session.isoformat()}")
        if session > today:
            errors.append(f"{market} 交易日 {session.isoformat()} 晚於香港切點 {today.isoformat()}")
        if int(block.get("eligible_stocks") or 0) <= 0:
            errors.append(f"{market} 沒有可計算的股票")
    scans = snapshot.get("scans") or {}
    for scan_id in SCAN_IDS:
        scan = scans.get(scan_id)
        if not scan:
            errors.append(f"缺少掃描 {scan_id}")
            continue
        for market in ("US", "JP"):
            block = (scan.get("markets") or {}).get(market) or {}
            counts = block.get("counts") or {}
            status = scan_publication_status(block)
            if status not in PUBLICATION_STATUSES:
                errors.append(f"{market} {scan_id} 狀態無效")
            matches = int(counts.get("matches") or 0)
            no_match = int(counts.get("no_match") or 0)
            if status == "valid" and matches == 0 and no_match <= 0:
                errors.append(f"{market} {scan_id} 被標成有效的 0 筆，但沒有算過")
            for row in block.get("matches") or []:
                if row.get("last_date") != (snapshot.get("markets") or {}).get(market, {}).get("as_of"):
                    errors.append(f"{market} {row.get('symbol')} 的最後交易日不是該市場日期")
                if str(row.get("symbol") or "").startswith("SYN"):
                    errors.append(f"示範代號 {row.get('symbol')} 不能公布")
                if row.get("data_notice"):
                    errors.append(f"{row.get('symbol')} 帶有示範聲明")
    return errors


def regression_problem(previous: dict | None, snapshot: dict) -> str | None:
    if not previous or previous.get("mode") != "real":
        return None
    for scan_id in SCAN_IDS:
        scan = (snapshot.get("scans") or {}).get(scan_id) or {}
        prev_scan = (previous.get("scans") or {}).get(scan_id) or {}
        for market in ("US", "JP"):
            new_block = (scan.get("markets") or {}).get(market) or {}
            prev_block = (prev_scan.get("markets") or {}).get(market) or {}
            if not prev_block:
                continue
            prev_matches = int((prev_block.get("counts") or {}).get("matches") or 0)
            if scan_publication_status(prev_block) == "valid" and prev_matches > 0:
                if scan_publication_status(new_block) != "valid":
                    return f"{market} {scan_id} 原本有 {prev_matches} 筆有效命中，新結果不是有效掃描，保留原快照"
    return None


def published_sessions(payload: dict) -> dict[str, str]:
    markets = payload.get("markets") or {}
    found = {}
    for market in ("US", "JP"):
        as_of = (markets.get(market) or {}).get("as_of")
        if not as_of:
            raise UpdateError("已公布快照缺少交易日，拒絕覆蓋")
        found[market] = as_of
    return found


def read_published(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError(f"已公布快照不是 JSON，拒絕覆蓋：{exc}") from exc
    return payload


def dated_snapshot_name(us: str, jp: str) -> str:
    if us == jp:
        return f"snapshot-{us}.json"
    return f"snapshot-us-{us}-jp-{jp}.json"


def publish_files(public_dir: Path, snapshot: dict, publication: dict) -> Path:
    public_dir.mkdir(parents=True, exist_ok=True)
    latest = public_dir / "latest.json"
    notice = public_dir / "publication.json"
    us = snapshot["markets"]["US"]["as_of"]
    jp = snapshot["markets"]["JP"]["as_of"]
    dated = public_dir / dated_snapshot_name(us, jp)
    previous = {path: path.read_bytes() if path.exists() else None for path in (latest, notice, dated)}
    try:
        atomic_write_json(latest, snapshot)
        atomic_write_json(notice, publication)
        atomic_write_json(dated, snapshot)
    except Exception:
        for path, content in previous.items():
            _restore(path, content)
        raise
    return dated


def _restore(path: Path, content: bytes | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
        return
    temporary = path.with_suffix(path.suffix + ".restore")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def _config_from_snapshot(raw: dict) -> EngineConfig:
    raw_thresholds = raw.get("cohort_thresholds") or {}
    if "US" not in raw_thresholds or "JP" not in raw_thresholds:
        raise ValueError("沒有美股或日股市值門檻")
    thresholds: dict[str, CohortThresholds] = {}
    for market, payload in raw_thresholds.items():
        thresholds[market] = CohortThresholds(
            enabled=bool(payload.get("enabled")),
            currency=payload.get("currency"),
            large_min=payload.get("large_min"),
            mid_min=payload.get("mid_min"),
            mid_max=payload.get("mid_max"),
            small_min=payload.get("small_min"),
            note=payload.get("note") or "",
        )
    return EngineConfig(
        min_cohort_size=int(raw.get("min_cohort_size", 0)),
        adr_policy=raw.get("adr_policy") or "",
        otc_policy=raw.get("otc_policy") or "",
        adjustment_required=raw.get("adjustment_required") or "",
        volume_basis=raw.get("volume_basis") or "",
        cohort_thresholds=thresholds,
    )
