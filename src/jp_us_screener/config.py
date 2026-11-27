"""Engine assumptions that must stay visible. Thresholds are opt-in config, not official bands."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CohortThresholds:
    """Optional user mapping from market cap to cohort.

    `large` is strictly greater than `large_min`.
    `mid` is strictly greater than `mid_min` and less than or equal to `mid_max`.
    When `large_min == mid_max`, a cap exactly on that line stays in mid.
    A cap equal to `mid_min`, or below it, is outside large and mid.
    """

    enabled: bool = False
    currency: str | None = None
    large_min: float | None = None
    mid_min: float | None = None
    mid_max: float | None = None
    small_min: float | None = None
    note: str = ""


@dataclass
class EngineConfig:
    min_cohort_size: int = 5
    # unresolved | include | exclude
    adr_policy: str = "unresolved"
    otc_policy: str = "unresolved"
    adjustment_required: str = "split_and_dividend"
    volume_basis: str = "unadjusted_shares"
    cohort_thresholds: dict[str, CohortThresholds] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.cohort_thresholds:
            self.cohort_thresholds = default_thresholds()
        if self.adr_policy not in {"unresolved", "include", "exclude"}:
            raise ValueError(f"adr_policy 無效：{self.adr_policy}")
        if self.otc_policy not in {"unresolved", "include", "exclude"}:
            raise ValueError(f"otc_policy 無效：{self.otc_policy}")
        if self.min_cohort_size < 2:
            raise ValueError("min_cohort_size 至少為 2，否則百分位沒有定義")
        for market, rule in self.cohort_thresholds.items():
            if rule.enabled:
                if not rule.currency or rule.large_min is None or rule.mid_min is None or rule.mid_max is None:
                    raise ValueError(f"{market} 已啟用市值分層，但貨幣或門檻不完整")
                if rule.mid_min > rule.mid_max:
                    raise ValueError(f"{market} 中型門檻上下顛倒")
                if rule.large_min < rule.mid_max:
                    raise ValueError(f"{market} 大型下限與中型區間重疊")

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        return raw


def default_thresholds() -> dict[str, CohortThresholds]:
    return {
        "US": CohortThresholds(
            enabled=False,
            currency="USD",
            large_min=None,
            mid_min=None,
            mid_max=None,
            small_min=None,
            note="2026-11-26 起停用。美股宇宙是 Russell 1000，不再按市值分成大型與中型。",
        ),
        "JP": CohortThresholds(
            enabled=False,
            currency="JPY",
            large_min=None,
            mid_min=None,
            mid_max=None,
            small_min=None,
            note="2026-11-26 起停用。日股宇宙是 TOPIX 500（Core30、Large70、Mid400），不再按市值分成大型與中型。",
        ),
    }


def load_config(path: str | Path | None) -> EngineConfig:
    if path is None:
        return EngineConfig()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    thresholds = default_thresholds()
    for market, payload in (data.get("cohort_thresholds") or {}).items():
        base = thresholds.get(market, CohortThresholds())
        for key, value in payload.items():
            if hasattr(base, key):
                setattr(base, key, value)
        thresholds[market] = base
    return EngineConfig(
        min_cohort_size=int(data.get("min_cohort_size", 5)),
        adr_policy=data.get("adr_policy", "unresolved"),
        otc_policy=data.get("otc_policy", "unresolved"),
        adjustment_required=data.get("adjustment_required", "split_and_dividend"),
        volume_basis=data.get("volume_basis", "unadjusted_shares"),
        cohort_thresholds=thresholds,
    )
