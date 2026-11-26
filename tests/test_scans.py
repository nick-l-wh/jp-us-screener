from jp_us_screener.features import compute_features
from jp_us_screener.models import Feature, RankInfo
from jp_us_screener.scans import evaluate_tree, jp_scan, outcome_of, scans_by_id
from jp_us_screener.version import GROUPING_WARNING, JP_ADAPT_MESSAGE
from tests.support import bars_from_closes, security


def _feature(**overrides) -> Feature:
    payload = dict(index=40, date="2024-06-03", open=10, high=11, low=9, close=10, volume=200_000)
    payload.update(overrides)
    return Feature(**payload)


def _large(rank: float) -> RankInfo:
    return RankInfo("large", "metadata", rank, rank, None, None, 10)


def test_di_clause_uses_rolling_max_not_numeric_25():
    scan = scans_by_id()["adx"]
    feature = _feature(plus_di14=19, prev25_max_plus_di=18, roc90=25)
    root = evaluate_tree(scan["tree"], feature, security(), _large(80), [])
    assert outcome_of(root) == "match"
    # 19 is not above the number 25, but it is above the previous 25-bar high.
    assert feature.plus_di14 < 25


def test_jp_currency_and_strict_thresholds():
    base = scans_by_id()
    feature = _feature(close=500)
    stock = security(market="JP", currency="JPY", market_cap=None, market_cap_currency="", market_cap_as_of="")
    for scan_id in ("weak", "squeeze"):
        for tree in (base[scan_id]["tree"], jp_scan(base[scan_id])["tree"]):
            assert all(clause.get("op") not in {"cap_usd", "cap_jpy"} for clause in tree["children"])
    price = next(c for c in jp_scan(base["turtle"])["tree"]["children"] if c.get("id") == "tu-px")
    assert evaluate_tree(price, feature, stock, _large(10)).status == "fail"
    feature.close = 501
    assert evaluate_tree(price, feature, stock, _large(10)).status == "pass"


def test_provisional_grouping_does_not_let_large_rank_skip_later_clauses():
    scan = scans_by_id()["adx"]
    assert scan["grouping_warning"] == GROUPING_WARNING
    feature = _feature(plus_di14=40, prev25_max_plus_di=10, roc90=5)
    root = evaluate_tree(scan["tree"], feature, security(), _large(90), [])
    assert outcome_of(root) == "no_match"


def test_mid_rank_can_satisfy_the_provisional_or():
    scan = scans_by_id()["young"]
    ranks = RankInfo("mid", "metadata", 80, None, 80, None, 8)
    feature = _feature(roc3=12, sma3_volume=400_000, sma50_volume=100_000)
    leaves = []
    root = evaluate_tree(scan["tree"], feature, security(cohort="mid"), ranks, leaves)
    assert outcome_of(root) == "match"
    assert any(leaf.clause_id == "yt-large" and leaf.status == "fail" for leaf in leaves)
    assert any(leaf.clause_id == "yt-mid" and leaf.status == "pass" for leaf in leaves)


def test_missing_cohort_is_unmet_not_a_silent_miss_when_nothing_else_fails():
    scan = scans_by_id()["adx"]
    feature = _feature(plus_di14=40, prev25_max_plus_di=10, roc90=30)
    ranks = RankInfo(None, None, None, None, None, "missing_market_cap")
    root = evaluate_tree(scan["tree"], feature, security(cohort="", market_cap=None, market_cap_currency=""), ranks, [])
    assert outcome_of(root) == "unmet_dependency"


def test_weak_keeps_explicit_brackets_and_rejects_rank_zero():
    scan = scans_by_id()["weak"]
    assert scan["grouping"] == "explicit_brackets"
    assert scan["grouping_warning"] is None
    passing = _feature(adx14=10, plus_di14=5, prev25_min_plus_di=8, volume=200_000)
    stock = security(market_cap=20_000_000_000)
    assert outcome_of(evaluate_tree(scan["tree"], passing, stock, _large(10), [])) == "match"
    assert outcome_of(evaluate_tree(scan["tree"], passing, stock, _large(0.0), [])) == "no_match"
    assert outcome_of(evaluate_tree(scan["tree"], passing, stock, _large(25), [])) == "no_match"


def test_squeeze_and_weak_do_not_use_market_cap():
    scan = scans_by_id()["squeeze"]
    assert "market cap" not in scan["raw_source"].lower()
    base = dict(
        sma20_volume=400_000,
        close=50,
        sma200_close=40,
        sma200_close_lag20=30,
        kc_upper=60,
        bb_upper=55,
        kc_lower=20,
        bb_lower=25,
        bb_width=4,
        bb_width_min63=4,
        plus_di14=10,
        minus_di14=10,
    )
    feature = _feature(**base)
    ranks = _large(50)
    missing = security(market_cap=None, market_cap_currency="", market_cap_as_of="")
    tiny = security(market_cap=1)
    yen = security(market_cap=1, market_cap_currency="JPY")
    for stock in (missing, tiny, yen):
        assert outcome_of(evaluate_tree(scan["tree"], feature, stock, ranks, [])) == "match"
    weak = scans_by_id()["weak"]
    assert "market cap" not in weak["raw_source"].lower()
    weak_feature = _feature(adx14=10, plus_di14=4, prev25_min_plus_di=6)
    assert outcome_of(evaluate_tree(weak["tree"], weak_feature, missing, _large(10), [])) == "match"
    assert outcome_of(evaluate_tree(weak["tree"], weak_feature, tiny, _large(10), [])) == "match"


def test_turtle_windows_and_close_buffer():
    count = 30
    lows = [10.5] * count
    closes = [11.0] * count
    lows[24] = 10.0
    lows[-1] = 9.5
    closes[-1] = 10.02
    highs = [max(11.2, close, low) for close, low in zip(closes, lows)]
    bars = bars_from_closes(closes, symbol="SYN-TU", volume=600_000, highs=highs, lows=lows)
    feature = compute_features(bars)[-1]
    assert feature.low_is_min20 is True
    assert feature.turtle_prior_low is True
    assert feature.yday_min20_low == 10.0
    scan = scans_by_id()["turtle"]
    assert outcome_of(evaluate_tree(scan["tree"], feature, security(symbol="SYN-TU"), _large(10), [])) == "match"

    lag2 = lows[:]
    lag2[24] = 10.5
    lag2[-3] = 10.0  # lag 2, outside the 3..10 set
    bars = bars_from_closes(closes, symbol="SYN-TU", volume=600_000, highs=highs, lows=lag2)
    assert compute_features(bars)[-1].turtle_prior_low is False

    lag11 = [10.5] * count
    lag11[-12] = 10.0
    lag11[-1] = 9.5
    bars = bars_from_closes(closes, symbol="SYN-TU", volume=600_000, highs=highs, lows=lag11)
    missed = compute_features(bars)[-1]
    assert missed.yday_min20_low == 10.0
    assert missed.turtle_prior_low is False


def test_sort_fields_are_not_filters():
    pullback = scans_by_id()["pullback"]
    young = scans_by_id()["young"]
    assert pullback["sort"]["field"] == "ulcer10"
    assert pullback["sort"]["direction_verified"] is False
    assert young["sort"]["field"] == "roc3"
    feature = _feature(roc20=25, plus_di14=40, ulcer10=None, roc3=12, sma3_volume=300_000, sma50_volume=100_000)
    assert outcome_of(evaluate_tree(pullback["tree"], feature, security(), _large(80), [])) == "match"
    assert outcome_of(evaluate_tree(young["tree"], feature, security(), _large(80), [])) == "match"


def test_jp_message_constant_is_the_adaptation_banner():
    assert scans_by_id()["squeeze"]["jp_message"] == JP_ADAPT_MESSAGE
    assert scans_by_id()["turtle"]["jp_policy"] == "needs_parameters"
    assert "5" in scans_by_id()["turtle"]["raw_source"]
