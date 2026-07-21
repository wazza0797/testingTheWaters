from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from trading_platform.domain.models.bar import Bar


class IMarketDataRepository(Protocol):
    """Canonical historical OHLCV storage. Backed by Parquet (M1); the interface
    stays stable if the backend later changes to SQLite/S3/TimescaleDB.
    """

    def save_bars(self, symbol: str, timeframe: str, bars: list[Bar]) -> None: ...

    def load_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Iterator[Bar]:
        """Load bars in `[start, end)`, ascending by timestamp."""
        ...

    def latest_timestamp(self, symbol: str, timeframe: str) -> datetime | None: ...


class IMarketDataFeed(Protocol):
    """Live/paper bar source — distinct from the repository (historical, at-rest)."""

    def poll_latest_bar(self, symbol: str, timeframe: str) -> Bar | None: ...
