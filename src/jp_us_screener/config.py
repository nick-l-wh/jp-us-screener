"""Engine assumptions that must stay visible. Thresholds are opt-in config, not official bands."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class CohortThresholds:
    """Optional user mapping from market cap to cohort.

    Disabled by default. Enabling it does not make the cut official.
    `large` is strictly greater than `large_min`. `mid` is `mid_min` <= cap <= `mid_max`.
    When `large_min == mid_max`, the shared boundary stays in mid ("over" the large line).
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
            # Published StockCharts US universe bands, stored only as a disabled reference.
            large_min=10_000_000_000,
            mid_min=2_000_000_000,
            mid_max=10_000_000_000,
            small_min=250_000_000,
            note=(
                "未啟用。數字只是 ChartSchool 公開的美國 SCTR 宇宙說明（大型超過 100 億美元、"
                "中型 20 億至 100 億、小型 2.5 億至 20 億），不是本引擎的官方門檻，也不會自動套用。"
                "要使用必須在設定檔明確 enabled=true。"
            ),
        ),
        "JP": CohortThresholds(
            enabled=False,
            currency=None,
            large_min=None,
            mid_min=None,
            mid_max=None,
            small_min=None,
            note="沒有日股官方分層。請在宇宙檔提供 cohort，或自行寫入貨幣與門檻。不會把美元門檻改成日圓。",
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
