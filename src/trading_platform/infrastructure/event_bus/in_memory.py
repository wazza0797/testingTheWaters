from __future__ import annotations

import logging
from collections import defaultdict

from trading_platform.domain.events.base import Event
from trading_platform.domain.ports.event_bus import IEventHandler

logger = logging.getLogger(__name__)


class InMemoryEventBus:
    """Synchronous, single-threaded, deterministic pub/sub. Implements `IEventBus`.

    Handlers for a given event type run in registration order. A handler
    exception propagates out of `publish` to the caller (TradingLoop), which
    decides whether to halt for critical handlers. Side-effect handlers
    (notifications, analytics) should catch their own exceptions internally so
    one failing notifier never blocks fill processing.
    """

    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[IEventHandler]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: IEventHandler) -> None:
        handlers = self._handlers[event_type]
        if handler not in handlers:
            handlers.append(handler)

    def unsubscribe(self, event_type: type[Event], handler: IEventHandler) -> None:
        handlers = self._handlers.get(event_type)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def publish(self, event: Event) -> None:
        handlers = self._handlers.get(type(event), [])
        logger.debug(
            "event_published",
            extra={
                "event_type": type(event).__name__,
                "correlation_id": event.correlation_id,
                "handler_count": len(handlers),
            },
        )
        for handler in list(handlers):
            handler.handle(event)
