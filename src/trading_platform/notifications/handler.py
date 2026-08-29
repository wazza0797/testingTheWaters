from __future__ import annotations

import logging

from trading_platform.domain.events.base import Event
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.risk import RiskRejected
from trading_platform.domain.events.system import ErrorOccurred, Heartbeat
from trading_platform.domain.ports.notification import INotifier

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Side-effect handler: format domain events and fan out via `INotifier`.

    Exceptions are caught internally so a notifier glitch never blocks the
    critical path (fills / risk / execution).
    """

    name = "notifications"

    def __init__(self, notifier: INotifier) -> None:
        self._notifier = notifier

    def handle(self, event: Event) -> None:
        try:
            formatted = format_event(event)
            if formatted is None:
                logger.debug(
                    "notification_handler_ignored_event",
                    extra={"event_type": type(event).__name__},
                )
                return
            message, level = formatted
            self._notifier.notify(message, level)
        except Exception:
            logger.exception(
                "notification_handler_failed",
                extra={"event_type": type(event).__name__},
            )


def format_event(event: Event) -> tuple[str, str] | None:
    """Return `(message, level)` for supported events, else `None`."""
    if isinstance(event, FillReceived):
        fill = event.fill
        order = event.order
        return (
            (
                f"FILL {fill.side.value} {fill.symbol} "
                f"qty={fill.filled_qty} @ {fill.fill_price} "
                f"fee={fill.fee} ({fill.fee_type.value}) "
                f"order={order.order_id} complete={fill.is_complete}"
            ),
            "info",
        )
    if isinstance(event, RiskRejected):
        # Trading-policy reject: no Order was created (e.g. cash / sizing rules).
        signal = event.signal
        return (
            (
                f"RISK REJECTED {signal.signal_type.value} {signal.symbol} "
                f"strength={signal.strength} reason={event.reason}"
            ),
            "warning",
        )
    if isinstance(event, OrderRejected):
        # Exchange-rule reject: Order existed but failed validation (min notional,
        # step size, etc.) — distinct from RiskRejected.
        order = event.order
        return (
            (
                f"ORDER REJECTED {order.side.value} {order.symbol} "
                f"qty={order.quantity} order={order.order_id} reason={event.reason}"
            ),
            "warning",
        )
    if isinstance(event, ErrorOccurred):
        return (
            f"ERROR source={event.source} type={event.error_type}: {event.message}",
            "error",
        )
    if isinstance(event, Heartbeat):
        # TODO(M7 follow-up): when Telegram is enabled, Heartbeat every poll is
        # noisy (~1 msg / poll_interval). Prefer console-only heartbeats or a
        # coarser Telegram cadence (e.g. every N minutes) — leave unmuted for now.
        return (
            f"HEARTBEAT mode={event.mode} uptime={event.uptime_seconds:.1f}s",
            "info",
        )
    return None
