"""Technical rank reconstructed from the public SCTR description.

This is not an official StockCharts score. Cohorts are ranked separately.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from jp_us_screener.models import Feature
from jp_us_screener.version import RANK_LABEL


def score_from_parts(feature: Feature) -> tuple[float | None, dict[str, float]]:
    """Long 60 / medium 30 / short 10, as published.

    PPO points already include the 5% weight (0..5). Other inputs are the raw
    percent or RSI reading multiplied by the published weight.
    """

    needed = {
        "ema200": feature.ema200,
        "ema50": feature.ema50,
        "roc125": feature.roc125,
        "roc20": feature.roc20,
        "rsi14": feature.rsi14,
        "ppo_slope_points": feature.ppo_slope_points,
    }
    if any(value is None for value in needed.values()) or feature.close == 0:
        return None, {}
    assert feature.ema200 is not None and feature.ema50 is not None
    if feature.ema200 == 0 or feature.ema50 == 0:
        return None, {}
    pct200 = (feature.close - feature.ema200) / feature.ema200 * 100.0
    pct50 = (feature.close - feature.ema50) / feature.ema50 * 100.0
    parts = {
        "pct_distance_ema200": pct200,
        "roc125": feature.roc125,  # type: ignore[dict-item]
        "pct_distance_ema50": pct50,
        "roc20": feature.roc20,  # type: ignore[dict-item]
        "ppo_slope_points": feature.ppo_slope_points,  # type: ignore[dict-item]
        "rsi14": feature.rsi14,  # type: ignore[dict-item]
        "weighted_ema200": pct200 * 0.30,
        "weighted_roc125": feature.roc125 * 0.30,  # type: ignore[operator]
        "weighted_ema50": pct50 * 0.15,
        "weighted_roc20": feature.roc20 * 0.15,  # type: ignore[operator]
        "weighted_ppo": feature.ppo_slope_points,  # type: ignore[dict-item]
        "weighted_rsi": feature.rsi14 * 0.05,  # type: ignore[operator]
    }
    total = (
        parts["weighted_ema200"]
        + parts["weighted_roc125"]
        + parts["weighted_ema50"]
        + parts["weighted_roc20"]
        + parts["weighted_ppo"]
        + parts["weighted_rsi"]
    )
    parts["indicator_score"] = total
    return total, parts


def percentile_ranks(scores: dict[str, float], min_size: int) -> dict[str, float] | None:
    """Map scores to 0.0..99.9 inside one cohort.

    Lowest score is 0.0 and highest is 99.9 when there are no ties.
    Ties share the average ordinal. One decimal, half up.
    Returns None when the cohort is smaller than `min_size` or smaller than 2.
    There is no global ranking across cohorts or markets.
    """

    n = len(scores)
    if n < max(min_size, 2):
        return None
    ordered = sorted(scores.items(), key=lambda item: (item[1], item[0]))
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and ordered[j + 1][1] == ordered[i][1]:
            j += 1
        avg = (i + j) / 2.0
        pct = Decimal(str(avg)) / Decimal(n - 1) * Decimal("99.9")
        rounded = pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if rounded < 0:
            rounded = Decimal("0.0")
        if rounded > Decimal("99.9"):
            rounded = Decimal("99.9")
        for k in range(i, j + 1):
            out[ordered[k][0]] = float(rounded)
        i = j + 1
    return out


RANK_METHOD = {
    "label": RANK_LABEL,
    "official_parity": False,
    "range": [0.0, 99.9],
    "scope": "每個市場的指數名單各自排名一次。公布的宇宙沒有按市值再分成大型與中型。",
    "weights": {
        "pct_distance_ema200": 0.30,
        "roc125": 0.30,
        "pct_distance_ema50": 0.15,
        "roc20": 0.15,
        "ppo_histogram_slope3": 0.05,
        "rsi14": 0.05,
    },
    "ppo": {
        "parameters": [12, 26, 9],
        "assumption": (
            "ChartSchool SCTR 權重表寫 PPO(12,26,9)。同頁正文寫直方圖是 PPO 減其 12 日 EMA。"
            "PPO 條目與掃描語法則用 9 日訊號線。本引擎採用 (12,26,9)，不聲稱與官方分數一致。"
        ),
        "slope": "(hist[t] - hist[t-3]) / 3；限制在 [-1, 1] 後映射到 0..5 分。",
    },
    "percentile": "最低分為 0.0，最高分為 99.9；同分取平均序位，四捨五入到 0.1（half up）。",
    "source": "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stockcharts-technical-rank-sctr",
}
