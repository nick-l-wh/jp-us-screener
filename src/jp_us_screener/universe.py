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

    Metadata cohort wins. Thresholds run only when that market's rule is enabled.
    """

    cohort = (security.cohort or "").strip().lower()
    if cohort in COHORTS:
        return cohort, "metadata", None
    if cohort and cohort not in COHORTS:
        return None, None, "invalid_cohort"
    rule = config.cohort_thresholds.get(security.market)
    if rule is None or not rule.enabled:
        if security.market_cap is None:
            return None, None, "missing_market_cap"
        return None, None, "cohort_not_configured"
    if security.market_cap is None:
        return None, None, "missing_market_cap"
    if not security.market_cap_currency:
        return None, None, "missing_market_cap_currency"
    if security.market_cap_currency != rule.currency:
        return None, None, "market_cap_currency_mismatch"
    cap = security.market_cap
    assert rule.large_min is not None and rule.mid_min is not None and rule.mid_max is not None
    if cap > rule.large_min:
        return "large", "config_threshold", None
    if rule.mid_min < cap <= rule.mid_max:
        return "mid", "config_threshold", None
    if rule.small_min is not None and rule.small_min <= cap <= rule.mid_min:
        return "small", "config_threshold", None
    return None, "config_threshold", "cap_outside_configured_cohorts"
