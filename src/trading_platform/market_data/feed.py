from __future__ import annotations

import logging

from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.exchange import IExchangeAdapter
from trading_platform.market_data.timeframe import timeframe_to_timedelta
from trading_platform.utils.time import utc_now

logger = logging.getLogger(__name__)


class PollingMarketDataFeed:
    """`IMarketDataFeed`: poll the exchange for the latest *fully closed* bar.

    Exchange OHLCV responses often include the still-forming candle as the
    last row — we skip any bar whose open + timeframe is still in the future
    relative to `utc_now()`, so paper trading never acts on incomplete data.
    """

    def __init__(self, exchange: IExchangeAdapter) -> None:
        self._exchange = exchange

    def poll_latest_bar(self, symbol: str, timeframe: str) -> Bar | None:
        return self.poll_latest_closed_bar(symbol, timeframe)

    def poll_latest_closed_bar(self, symbol: str, timeframe: str) -> Bar | None:
        bars = self._exchange.fetch_ohlcv(symbol, timeframe, limit=5)
        if not bars:
            return None

        interval = timeframe_to_timedelta(timeframe)
        now = utc_now()
        for bar in reversed(bars):
            if bar.timestamp + interval <= now:
                return bar

        logger.debug(
            "no_closed_bar_yet",
            extra={"symbol": symbol, "timeframe": timeframe},
        )
        return None
