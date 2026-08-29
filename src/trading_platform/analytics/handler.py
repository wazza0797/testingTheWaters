from __future__ import annotations

import logging

from trading_platform.analytics.state import RunningPerformanceState
from trading_platform.domain.events.base import Event
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.risk import RiskRejected

logger = logging.getLogger(__name__)


class AnalyticsHandler:
    """Side-effect handler: updates running performance state on fills/rejects.

    Exceptions are caught internally so a metrics glitch never blocks the
    critical path (see `InMemoryEventBus` / coding standards).
    """

    name = "analytics"

    def __init__(self, state: RunningPerformanceState | None = None) -> None:
        self.state = state if state is not None else RunningPerformanceState()

    def handle(self, event: Event) -> None:
        try:
            if isinstance(event, FillReceived):
                self.state.record_fill(event.fill)
            elif isinstance(event, (OrderRejected, RiskRejected)):
                self.state.record_rejection()
            else:
                logger.debug(
                    "analytics_handler_ignored_event",
                    extra={"event_type": type(event).__name__},
                )
        except Exception:
            logger.exception(
                "analytics_handler_failed",
                extra={"event_type": type(event).__name__},
            )
