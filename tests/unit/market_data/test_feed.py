from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from trading_platform.domain.models.bar import Bar
from trading_platform.market_data.feed import PollingMarketDataFeed
from trading_platform.utils.time import utc_now


class FakeExchange:
    def __init__(self, bars: list[Bar]) -> None:
        self._bars = bars

    def fetch_ohlcv(self, symbol: str, timeframe: str, since=None, limit=None):
        return list(self._bars)


class TestPollingMarketDataFeed:
    def test_skips_still_forming_candle(self) -> None:
        # Last bar opened this hour → still forming for 1h; previous is closed
        closed = Bar(
            symbol="BTC/USDT",
            timeframe="1h",
            timestamp=utc_now().replace(minute=0, second=0, microsecond=0) - timedelta(hours=1),
            open=Decimal("100"),
            high=Decimal("100"),
            low=Decimal("100"),
            close=Decimal("100"),
            volume=Decimal("1"),
        )
        forming = Bar(
            symbol="BTC/USDT",
            timeframe="1h",
            timestamp=utc_now().replace(minute=0, second=0, microsecond=0),
            open=Decimal("110"),
            high=Decimal("110"),
            low=Decimal("110"),
            close=Decimal("110"),
            volume=Decimal("1"),
        )
        feed = PollingMarketDataFeed(FakeExchange([closed, forming]))  # type: ignore[arg-type]
        bar = feed.poll_latest_closed_bar("BTC/USDT", "1h")
        assert bar is not None
        assert bar.close == Decimal("100")
