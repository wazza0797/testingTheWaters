from __future__ import annotations

import logging

from trading_platform.domain.events.base import Event
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.ports.metrics import IMetricsCollector

logger = logging.getLogger(__name__)


class MetricsHandler:
    """Subscribes to every throughput-relevant domain event and increments the
    matching Prometheus counter (see metric catalog in `docs/architecture.md`).

    This is the *only* place that translates domain events into throughput
    counters — strategy/risk/execution code never touches metrics directly.
    Registered for each event type it cares about in `container.py`.
    """

    name = "metrics"

    def __init__(self, metrics: IMetricsCollector) -> None:
        self._metrics = metrics

    def handle(self, event: Event) -> None:
        if isinstance(event, BarClosed):
            self._metrics.increment_counter(
                "trading_bars_processed_total",
                labels={"mode": event.mode, "symbol": event.bar.symbol},
            )
        elif isinstance(event, SignalGenerated):
            self._metrics.increment_counter(
                "trading_signals_generated_total",
                labels={"strategy": event.signal.strategy_name, "symbol": event.signal.symbol},
            )
        elif isinstance(event, OrderApproved):
            self._metrics.increment_counter(
                "trading_orders_submitted_total",
                labels={"symbol": event.order.symbol, "side": event.order.side.value},
            )
        elif isinstance(event, RiskRejected):
            # `reason` labels must stay a small fixed set (see IMetricsCollector
            # cardinality note) — risk rules should categorize, not use free text.
            self._metrics.increment_counter(
                "trading_orders_rejected_total", labels={"reason": "risk_rejected"}
            )
        elif isinstance(event, OrderRejected):
            self._metrics.increment_counter(
                "trading_orders_rejected_total", labels={"reason": event.reason}
            )
        elif isinstance(event, FillReceived):
            self._metrics.increment_counter(
                "trading_fills_received_total",
                labels={"symbol": event.fill.symbol, "fee_type": event.fill.fee_type.value},
            )
        else:
            logger.debug(
                "metrics_handler_ignored_event", extra={"event_type": type(event).__name__}
            )
