"""Security gates. ETFs leave before ranking and before any scan."""

from __future__ import annotations

from jp_us_screener.config import EngineConfig
from jp_us_screener.models import Security


ALLOWED_TYPES = {"stock", "etf"}
COHORTS = {"large", "mid", "small"}


def screen_security(security: Security, config: EngineConfig) -> tuple[str, str | None]:
    """Return (status, reason).

    status is `eligible` or an exclusion bucket.
    """

    stype = (security.security_type or "").strip().lower()
    group = (security.group or "").strip().lower()
    segment = (security.listing_segment or "").strip().lower()
    if security.market not in {"US", "JP"}:
        return "excluded_market", "只處理 US 與 JP"
    if not stype or stype == "unknown":
        return "excluded_unknown_type", "security_type 缺漏或 unknown，不會假設為股票"
    if stype == "etf" or group == "etf":
        if stype == "stock" and group == "etf":
            return "data_error", "security_type 為 stock 但 group 為 ETF，類型互相矛盾"
        return "excluded_etf", "ETF 在排名與掃描之前剔除"
    if stype != "stock":
        return "excluded_not_stock", f"security_type={security.security_type}，不是股票"
    if segment == "unknown":
        return "eligibility_unresolved", "listing_segment=unknown，無法判斷是否 ADR/OTC"
    if segment == "adr":
        return _policy(config.adr_policy, "ADR")
    if segment == "otc":
        return _policy(config.otc_policy, "OTC")
    if segment not in {"", "primary"}:
        return "eligibility_unresolved", f"未識別的 listing_segment={security.listing_segment}"
    return "eligible", None


def _policy(policy: str, label: str) -> tuple[str, str | None]:
    if policy == "include":
        return "eligible", None
    if policy == "exclude":
        return "excluded_listing", f"{label} 已按設定排除"
    return "eligibility_unresolved", f"{label} 資格未決議：設定為 unresolved，不會靜默納入或排除後假裝已決定"


def resolve_cohort(security: Security, config: EngineConfig) -> tuple[str | None, str | None, str | None]:
    """Return (cohort, source, reason).

    Market cap does not assign a cohort. A name is ranked only when the
    universe file names ``large``, ``mid``, or ``small``. The published index
    lists put every member in one pool, ``large``, so the existing
    ``SCTR.large`` clauses rank the whole index. That pool is not a
    market-cap band.
    """

    del config
    cohort = (security.cohort or "").strip().lower()
    if cohort in COHORTS:
        return cohort, "metadata", None
    if cohort:
        return None, None, "invalid_cohort"
    return None, None, "cohort_not_in_list"
