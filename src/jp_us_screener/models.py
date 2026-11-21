"""Typed rows. Predicates read these fields; they do not eval text."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Bar:
    market: str
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    adjustment: str


@dataclass
class Security:
    market: str
    symbol: str
    name: str
    security_type: str
    listing_segment: str
    exchange: str
    currency: str
    cohort: str
    market_cap: float | None
    market_cap_currency: str
    market_cap_as_of: str
    group: str

    @property
    def qualified_symbol(self) -> str:
        return f"{self.market}:{self.symbol}"


@dataclass
class Feature:
    index: int
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    plus_di14: float | None = None
    minus_di14: float | None = None
    adx14: float | None = None
    atr10: float | None = None
    atr14: float | None = None
    roc3: float | None = None
    roc20: float | None = None
    roc90: float | None = None
    roc125: float | None = None
    sma3_volume: float | None = None
    sma20_volume: float | None = None
    sma50_volume: float | None = None
    sma200_close: float | None = None
    sma200_close_lag20: float | None = None
    ema20: float | None = None
    ema50: float | None = None
    ema200: float | None = None
    rsi14: float | None = None
    ppo: float | None = None
    ppo_signal: float | None = None
    ppo_hist: float | None = None
    ppo_hist_slope3: float | None = None
    ppo_slope_points: float | None = None
    ulcer10: float | None = None
    bb_upper: float | None = None
    bb_lower: float | None = None
    bb_mid: float | None = None
    bb_width: float | None = None
    bb_width_min63: float | None = None
    kc_upper: float | None = None
    kc_lower: float | None = None
    prev25_max_plus_di: float | None = None
    prev25_min_plus_di: float | None = None
    low_is_min20: bool | None = None
    turtle_prior_low: bool | None = None
    yday_min20_low: float | None = None
    technical_score: float | None = None
    score_parts: dict[str, float] = field(default_factory=dict)


@dataclass
class RankInfo:
    cohort: str | None
    cohort_source: str | None
    own: float | None
    large: float | None
    mid: float | None
    reason: str | None
    cohort_size: int | None = None


@dataclass
class ClauseResult:
    clause_id: str
    label: str
    status: str  # pass | fail | unavailable
    code: str | None = None
    detail: str | None = None
