"""Provider boundary. No network client is implemented or selected."""

from __future__ import annotations

from typing import Protocol


class ProviderNotConfigured(RuntimeError):
    pass


class MarketDataProvider(Protocol):
    name: str

    def fetch_daily_bars(self, market: str, symbols: list[str], start: str, end: str) -> None:
        ...


class NotConfiguredProvider:
    name = "none"

    def fetch_daily_bars(self, market: str, symbols: list[str], start: str, end: str) -> None:
        raise ProviderNotConfigured(
            "尚未選擇行情供應商。本草稿只接受本地 CSV，不會連線、不會索取憑證。"
        )
