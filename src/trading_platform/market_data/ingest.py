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

        # Page size is the smaller of our own preferred page size and the
        # adapter's hard per-request cap (e.g. IG's nominal 500 vs Binance's
        # 1000).
        adapter_cap = getattr(self._exchange, "max_ohlcv_limit", _FETCH_LIMIT)
        page_limit = min(_FETCH_LIMIT, adapter_cap)

        total_new = 0
        for _ in range(_MAX_PAGES):
            bars = self._exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=page_limit)
            new_bars = [
                bar for bar in bars if latest_stored is None or bar.timestamp > latest_stored
            ]
            if not new_bars:
                # The only reliable "caught up" signal: a page with zero new
                # bars. We deliberately do NOT also stop on a "short" page
                # (`len(bars) < page_limit`) — some venues' historical price
                # APIs silently cap each response well below their documented
                # per-request limit regardless of what's requested (e.g. IG's
                # demo `/prices` endpoint has been observed returning exactly
                # ~20 points per call no matter the `max`/date range asked
                # for), so a "short" page there means "more small pages
                # follow", not "no more history" — treating it as the latter
                # silently truncated multi-page IG downloads to a single
                # page. One extra page returning zero new bars (the real
                # "caught up to now" case) is a cheap, always-correct price
                # to pay for not silently truncating history on venues like
                # that.
                break

            self._repository.save_bars(symbol, timeframe, new_bars)
            for bar in new_bars:
                self._publish_bar_closed(bar)

            total_new += len(new_bars)
            latest_stored = new_bars[-1].timestamp
            cursor = new_bars[-1].timestamp
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
