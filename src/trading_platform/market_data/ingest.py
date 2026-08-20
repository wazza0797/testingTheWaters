from __future__ import annotations

import logging
from datetime import datetime

from trading_platform.domain.errors import MarketDataError
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.domain.ports.market_data import IMarketDataRepository

logger = logging.getLogger(__name__)

_FETCH_LIMIT = 1000
_MAX_PAGES = 10_000  # safety cap against a pathological/misbehaving adapter


class DataIngestService:
    """Downloads historical OHLCV bars and persists them incrementally.

    Idempotent: re-running for the same symbol/timeframe only fetches bars
    newer than `IMarketDataRepository.latest_timestamp` (the repository also
    de-dupes by timestamp on write regardless, as a second safety net).

    Publishes a `BarClosed(mode="ingest")` event per new bar so the existing
    `MetricsHandler` records `trading_bars_processed_total` for downloads —
    no ingest-specific metrics code needed.
    """

    def __init__(
        self,
        exchange: IExchangeAdapter,
        repository: IMarketDataRepository,
        event_bus: IEventBus | None = None,
    ) -> None:
        self._exchange = exchange
        self._repository = repository
        self._event_bus = event_bus

    def sync(self, symbol: str, timeframe: str, since: datetime) -> int:
        """Fetch and persist bars from `max(since, latest stored)` up to now.

        Returns the number of new bars persisted.
        """
        latest_stored = self._repository.latest_timestamp(symbol, timeframe)
        cursor = max(since, latest_stored) if latest_stored is not None else since

        total_new = 0
        for _ in range(_MAX_PAGES):
            bars = self._exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=_FETCH_LIMIT)
            new_bars = [
                bar for bar in bars if latest_stored is None or bar.timestamp > latest_stored
            ]
            if not new_bars:
                break

            self._repository.save_bars(symbol, timeframe, new_bars)
            for bar in new_bars:
                self._publish_bar_closed(bar)

            total_new += len(new_bars)
            latest_stored = new_bars[-1].timestamp
            cursor = new_bars[-1].timestamp

            if len(bars) < _FETCH_LIMIT:
                break  # short page: no more history available from the exchange
        else:
            raise MarketDataError(
                f"Ingest for {symbol}@{timeframe} did not terminate after {_MAX_PAGES} pages "
                "— aborting to avoid an unbounded loop."
            )

        logger.info(
            "data_ingest_complete",
            extra={"symbol": symbol, "timeframe": timeframe, "new_bars": total_new},
        )
        return total_new

    def _publish_bar_closed(self, bar: Bar) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(BarClosed(bar=bar, mode="ingest"))
