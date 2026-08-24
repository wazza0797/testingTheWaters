from __future__ import annotations

import logging
from collections.abc import Mapping

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.events.base import Event
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.risk import OrderApproved
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.domain.ports.execution import IBroker
from trading_platform.execution.order_validator import validate_order

logger = logging.getLogger(__name__)


class ExecutionHandler:
    """Adapts one `IBroker` to the event bus: subscribes to `OrderApproved`,
    validates the order against exchange rules, and either publishes
    `OrderRejected` or forwards it to the broker and publishes `FillReceived`
    for whatever fills come back synchronously.

    Sits at the end of the critical path (strategy -> risk -> execution).
    Order validation (`execution/order_validator.py`) happens *here*, not
    inside the risk engine — Risk's rejections (`RiskRejected`) are
    trading-policy-level (already in a position, nothing to close); this
    handler's rejections (`OrderRejected`) are exchange-rule-level (below
    `min_qty`/`min_notional` after rounding), matching
    `docs/architecture.md`'s event table.

    `SimBroker.submit_order` always returns `[]` (backtest fills happen via
    `SimBroker.process_bar`, called directly by the backtest engine — not
    through this handler) — but a future `PaperBroker`/`LiveBroker` may
    return fills synchronously from `submit_order` itself, so this always
    forwards whatever list comes back rather than assuming it's empty.
    """

    def __init__(
        self,
        broker: IBroker,
        instrument_rules: Mapping[str, InstrumentRules],
        event_bus: IEventBus,
    ) -> None:
        self._broker = broker
        self._instrument_rules = instrument_rules
        self._event_bus = event_bus

    def handle(self, event: Event) -> None:
        if not isinstance(event, OrderApproved):
            return

        order, bar = event.order, event.bar
        rules = self._instrument_rules.get(order.symbol)
        if rules is None:
            self._reject(order, f"no instrument rules for {order.symbol!r}", event.correlation_id)
            return

        reference_price = order.price if order.price is not None else bar.close
        rejection_reason = validate_order(order, rules, reference_price)
        if rejection_reason is not None:
            self._reject(order, rejection_reason, event.correlation_id)
            return

        try:
            fills = self._broker.submit_order(order)
        except ExchangeAdapterError as exc:
            self._reject(order, f"broker rejected order: {exc}", event.correlation_id)
            return

        for fill in fills:
            logger.debug(
                "fill_received",
                extra={
                    "symbol": fill.symbol,
                    "filled_qty": str(fill.filled_qty),
                    "fill_price": str(fill.fill_price),
                    "correlation_id": event.correlation_id,
                },
            )
            self._event_bus.publish(
                FillReceived(fill=fill, order=order, correlation_id=event.correlation_id)
            )

    def _reject(self, order: Order, reason: str, correlation_id: str) -> None:
        logger.debug(
            "order_rejected",
            extra={"symbol": order.symbol, "reason": reason, "correlation_id": correlation_id},
        )
        self._event_bus.publish(
            OrderRejected(order=order, reason=reason, correlation_id=correlation_id)
        )
