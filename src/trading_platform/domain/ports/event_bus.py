from __future__ import annotations

from typing import Protocol

from trading_platform.domain.events.base import Event


class IEventHandler(Protocol):
    """Implemented by every `*Handler` (StrategyHandler, RiskHandler, MetricsHandler, ...).

    A handler instance may be subscribed to multiple event types; the bus always
    calls `handle` with whichever event triggered the invocation.
    """

    def handle(self, event: Event) -> None: ...


class IEventBus(Protocol):
    """The primary integration mechanism between modules.

    Handlers must never import or call each other directly — all cross-module
    communication flows through `publish`/`subscribe`. Subscriptions are wired
    exclusively in the composition root (`container.py`).
    """

    def subscribe(self, event_type: type[Event], handler: IEventHandler) -> None: ...

    def unsubscribe(self, event_type: type[Event], handler: IEventHandler) -> None: ...

    def publish(self, event: Event) -> None: ...
