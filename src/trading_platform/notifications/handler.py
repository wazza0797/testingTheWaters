from __future__ import annotations

import logging
from concurrent.futures import Executor

from trading_platform.domain.events.base import Event
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.risk import RiskRejected
from trading_platform.domain.events.system import ErrorOccurred, Heartbeat
from trading_platform.domain.ports.notification import INotifier

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Side-effect handler: format domain events and fan out via `INotifier`.

    Remote HTTP (Discord/Telegram) must not block the event-bus thread — when
    an `executor` is provided, `notify` is submitted asynchronously so
    portfolio/fill handling can continue immediately. Exceptions inside the
    worker are logged and never re-raised onto the bus.
    """

    name = "notifications"

    def __init__(
        self,
        notifier: INotifier,
        *,
        executor: Executor | None = None,
    ) -> None:
        self._notifier = notifier
        self._executor = executor

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
            if self._executor is not None:
                self._executor.submit(self._safe_notify, message, level)
            else:
                self._safe_notify(message, level)
        except Exception:
            logger.exception(
                "notification_handler_failed",
                extra={"event_type": type(event).__name__},
            )

    def _safe_notify(self, message: str, level: str) -> None:
        try:
            self._notifier.notify(message, level)
        except Exception:
            logger.exception(
                "notification_handler_notify_failed",
                extra={"level": level},
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
        # TODO(M7 follow-up): when Telegram/Discord is enabled, Heartbeat every
        # poll is noisy (~1 msg / poll_interval). Prefer console-only heartbeats
        # or a coarser remote cadence — leave unmuted for now.
        return (
            f"HEARTBEAT mode={event.mode} uptime={event.uptime_seconds:.1f}s",
            "info",
        )
    return None
