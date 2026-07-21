from __future__ import annotations

import logging
import time

from trading_platform.domain.events.base import Event
from trading_platform.domain.ports.event_bus import IEventBus, IEventHandler
from trading_platform.domain.ports.metrics import IMetricsCollector

logger = logging.getLogger(__name__)

HANDLER_DURATION_METRIC = "trading_handler_duration_seconds"
EVENTS_PUBLISHED_METRIC = "trading_events_published_total"
HANDLER_ERRORS_METRIC = "trading_handler_errors_total"


def _handler_name(handler: IEventHandler) -> str:
    return getattr(handler, "name", type(handler).__name__)


class _TimedHandler:
    """Wraps a real handler to record duration/error metrics around `handle`."""

    def __init__(self, inner: IEventHandler, metrics: IMetricsCollector) -> None:
        self._inner = inner
        self._metrics = metrics
        self.name = _handler_name(inner)

    def handle(self, event: Event) -> None:
        event_type = type(event).__name__
        start = time.perf_counter()
        try:
            self._inner.handle(event)
        except Exception as exc:
            self._metrics.increment_counter(
                HANDLER_ERRORS_METRIC,
                labels={"handler": self.name, "error_type": type(exc).__name__},
            )
            raise
        finally:
            duration = time.perf_counter() - start
            self._metrics.observe_histogram(
                HANDLER_DURATION_METRIC,
                duration,
                labels={"handler": self.name, "event_type": event_type},
            )
            logger.debug(
                "handler_invoked",
                extra={
                    "handler": self.name,
                    "event_type": event_type,
                    "duration_ms": duration * 1000,
                    "correlation_id": event.correlation_id,
                },
            )


class TimedEventBus:
    """Decorates an `IEventBus` with automatic handler latency/error metrics.

    Handlers never instrument themselves — wrapping happens transparently at
    subscribe time, so adding a new handler automatically gets latency
    tracking with zero extra code (see `docs/architecture.md` observability
    section).

    Note: wrapper identity is tracked by `id(handler)`, which assumes
    long-lived handler singletons (as wired in `container.py`). This is not
    safe for short-lived/GC'd handler objects.
    """

    def __init__(self, inner: IEventBus, metrics: IMetricsCollector) -> None:
        self._inner = inner
        self._metrics = metrics
        self._wrappers: dict[tuple[type[Event], int], _TimedHandler] = {}

    def subscribe(self, event_type: type[Event], handler: IEventHandler) -> None:
        key = (event_type, id(handler))
        wrapper = self._wrappers.get(key)
        if wrapper is None:
            wrapper = _TimedHandler(handler, self._metrics)
            self._wrappers[key] = wrapper
        self._inner.subscribe(event_type, wrapper)

    def unsubscribe(self, event_type: type[Event], handler: IEventHandler) -> None:
        key = (event_type, id(handler))
        wrapper = self._wrappers.pop(key, None)
        if wrapper is not None:
            self._inner.unsubscribe(event_type, wrapper)

    def publish(self, event: Event) -> None:
        self._metrics.increment_counter(
            EVENTS_PUBLISHED_METRIC, labels={"event_type": type(event).__name__}
        )
        self._inner.publish(event)
