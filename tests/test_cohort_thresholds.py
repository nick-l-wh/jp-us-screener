from jp_us_screener.config import CohortThresholds, EngineConfig, load_config
from jp_us_screener.publish import config_problem
from jp_us_screener.universe import resolve_cohort
from tests.support import security

ROOT_CONFIG = "config/default.json"


def test_market_cap_does_not_assign_a_cohort():
    config = load_config(ROOT_CONFIG)
    us = config.cohort_thresholds["US"]
    jp = config.cohort_thresholds["JP"]
    assert us.enabled is False and us.currency == "USD"
    assert jp.enabled is False and jp.currency == "JPY"
    assert config_problem(config) is None

    huge = security(cohort="", market_cap=100_000_000_000.01)
    assert resolve_cohort(huge, config) == (None, None, "cohort_not_in_list")
    missing = security(cohort="", market_cap=None, market_cap_currency="")
    assert resolve_cohort(missing, config)[2] == "cohort_not_in_list"
    assert resolve_cohort(security(cohort="large", market_cap=1), config)[:2] == ("large", "metadata")
    yen = security(market="JP", currency="JPY", cohort="", market_cap=1_000_000_000_000.01, market_cap_currency="JPY")
    assert resolve_cohort(yen, config)[0] is None

    enabled = EngineConfig(
        cohort_thresholds={
            "US": CohortThresholds(enabled=True, currency="USD", large_min=1, mid_min=1, mid_max=1),
            "JP": CohortThresholds(enabled=False, currency="JPY"),
        }
    )
    assert resolve_cohort(huge, enabled) == (None, None, "cohort_not_in_list")
    assert config_problem(enabled)
