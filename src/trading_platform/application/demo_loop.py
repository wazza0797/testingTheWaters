from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime

from trading_platform.domain.events.execution import FillReceived
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.execution.demo_broker import DemoBroker
from trading_platform.market_data.feed import PollingMarketDataFeed

logger = logging.getLogger(__name__)


class DemoTradingLoop:
    """Poll venue order fills + closed bars for exchange demo/practice mode.

    Same event path as paper (`FillReceived` / `BarClosed` / `Heartbeat`), but
    fills come from `DemoBroker.poll_fills` (exchange), not `FillSimulator`.
    """

    def __init__(
        self,
        event_bus: IEventBus,
        feed: PollingMarketDataFeed,
        broker: DemoBroker,
        *,
        symbol: str,
        timeframe: str,
        poll_interval_sec: float = 5.0,
        last_bar_timestamp: datetime | None = None,
        should_stop: Callable[[], bool] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        on_heartbeat: Callable[[str], None] | None = None,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._event_bus = event_bus
        self._feed = feed
        self._broker = broker
        self._symbol = symbol
        self._timeframe = timeframe
        self._poll_interval_sec = poll_interval_sec
        self._last_bar_timestamp = last_bar_timestamp
        self._should_stop = should_stop or (lambda: False)
        self._sleep = sleep_fn
        self._on_heartbeat = on_heartbeat
        self._monotonic = monotonic_fn
        self._started_at: float | None = None

    @property
    def last_bar_timestamp(self) -> datetime | None:
        return self._last_bar_timestamp

    def run(self) -> int:
        processed = 0
        self._started_at = self._monotonic()
        logger.info(
            "demo_loop_started",
            extra={
                "symbol": self._symbol,
                "timeframe": self._timeframe,
                "poll_interval_sec": self._poll_interval_sec,
            },
        )
        while not self._should_stop():
            self._publish_fill_updates()
            bar = self._feed.poll_latest_closed_bar(self._symbol, self._timeframe)
            if bar is not None and self._is_new_bar(bar):
                self._process_bar(bar)
                processed += 1
            else:
                self._emit_heartbeat()
            if self._should_stop():
                break
            try:
                self._sleep(self._poll_interval_sec)
            except KeyboardInterrupt:
                break
        logger.info("demo_loop_stopped", extra={"bars_processed": processed})
        return processed

    def _publish_fill_updates(self) -> None:
        for order, fill in self._broker.poll_fills():
            self._event_bus.publish(
                FillReceived(fill=fill, order=order, correlation_id=order.correlation_id)
            )

    def _emit_heartbeat(self) -> None:
        started = self._started_at if self._started_at is not None else self._monotonic()
        uptime = self._monotonic() - started
        last = (
            self._last_bar_timestamp.isoformat()
            if self._last_bar_timestamp is not None
            else "none yet"
        )
        open_orders = self._broker.has_open_orders()
        message = (
            f"heartbeat: demo {self._symbol}@{self._timeframe} — "
            f"last_bar={last}, open_orders={open_orders}, "
            f"poll every {self._poll_interval_sec:g}s"
        )
        logger.info("demo_heartbeat", extra={"last_bar": last})
        self._event_bus.publish(Heartbeat(mode="demo", uptime_seconds=uptime))
        if self._on_heartbeat is not None:
            self._on_heartbeat(message)

    def _is_new_bar(self, bar: Bar) -> bool:
        if self._last_bar_timestamp is None:
            return True
        return bar.timestamp > self._last_bar_timestamp

    def _process_bar(self, bar: Bar) -> None:
        # Catch any fills that landed since the last poll before strategy sees the bar.
        self._publish_fill_updates()
        self._event_bus.publish(BarClosed(bar=bar, mode="demo"))
        self._last_bar_timestamp = bar.timestamp
        logger.info(
            "demo_bar_processed",
            extra={
                "symbol": bar.symbol,
                "timestamp": bar.timestamp.isoformat(),
                "close": str(bar.close),
            },
        )
