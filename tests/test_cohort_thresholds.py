from jp_us_screener.config import load_config
from jp_us_screener.universe import resolve_cohort
from tests.support import security

ROOT_CONFIG = "config/default.json"


def test_market_cap_cohorts_use_the_published_cutoffs():
    config = load_config(ROOT_CONFIG)
    us = config.cohort_thresholds["US"]
    jp = config.cohort_thresholds["JP"]
    assert us.enabled and us.currency == "USD"
    assert (us.large_min, us.mid_min, us.mid_max, us.small_min) == (100_000_000_000, 25_000_000_000, 100_000_000_000, None)
    assert jp.enabled and jp.currency == "JPY"
    assert (jp.large_min, jp.mid_min, jp.mid_max, jp.small_min) == (1_000_000_000_000, 250_000_000_000, 1_000_000_000_000, None)

    assert resolve_cohort(security(cohort="", market_cap=100_000_000_000.01), config)[0] == "large"
    assert resolve_cohort(security(cohort="", market_cap=100_000_000_000), config)[0] == "mid"
    assert resolve_cohort(security(cohort="", market_cap=25_000_000_000.01), config)[0] == "mid"
    floor = resolve_cohort(security(cohort="", market_cap=25_000_000_000), config)
    assert floor[0] is None and floor[2] == "cap_outside_configured_cohorts"
    below = resolve_cohort(security(cohort="", market_cap=24_999_999_999), config)
    assert below[0] is None and below[2] == "cap_outside_configured_cohorts"
    missing = resolve_cohort(security(cohort="", market_cap=None, market_cap_currency=""), config)
    assert missing[0] is None and missing[2] == "missing_market_cap"
    assert resolve_cohort(security(cohort="mid", market_cap=1), config)[:2] == ("mid", "metadata")

    yen_large = security(market="JP", currency="JPY", cohort="", market_cap=1_000_000_000_000.01, market_cap_currency="JPY")
    yen_line = security(market="JP", currency="JPY", cohort="", market_cap=1_000_000_000_000, market_cap_currency="JPY")
    yen_mid = security(market="JP", currency="JPY", cohort="", market_cap=250_000_000_000.01, market_cap_currency="JPY")
    yen_floor = security(market="JP", currency="JPY", cohort="", market_cap=250_000_000_000, market_cap_currency="JPY")
    assert resolve_cohort(yen_large, config)[0] == "large"
    assert resolve_cohort(yen_line, config)[0] == "mid"
    assert resolve_cohort(yen_mid, config)[0] == "mid"
    assert resolve_cohort(yen_floor, config)[2] == "cap_outside_configured_cohorts"
