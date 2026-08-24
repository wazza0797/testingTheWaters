from __future__ import annotations

import logging
from dataclasses import replace

from trading_platform.domain.events.base import Event
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.ports.event_bus import IEventBus
from trading_platform.domain.ports.risk import IRiskEngine

logger = logging.getLogger(__name__)

_DEFAULT_REJECTION_REASON = "rejected by risk engine"


class RiskHandler:
    """Adapts one `IRiskEngine` to the event bus: subscribes to
    `SignalGenerated`, calls `evaluate`, and publishes `OrderApproved` or
    `RiskRejected` — reusing the triggering signal's `correlation_id` so a
    signal's entire strategy -> risk -> execution chain traces as one ID.

    Sits between `StrategyHandler` and `ExecutionHandler` on the critical
    path — an exception here (from the wrapped engine) propagates to the
    caller (the event bus / trading loop), same as `StrategyHandler`.

    Like `StrategyHandler` stamping `Signal.strategy_name`, this overwrites
    whatever placeholder `correlation_id` the engine's returned `Order`
    carries (see `risk/engine.py::_PENDING_CORRELATION_ID`) with the real one
    from the triggering event — the engine itself has no event to read it
    from.
    """

    def __init__(self, risk_engine: IRiskEngine, event_bus: IEventBus) -> None:
        self._risk_engine = risk_engine
        self._event_bus = event_bus

    def handle(self, event: Event) -> None:
        if not isinstance(event, SignalGenerated):
            return

        decision = self._risk_engine.evaluate(event.signal, event.bar)

        if decision.approved:
            assert decision.order is not None  # approved implies order is set — see RiskDecision
            order = replace(decision.order, correlation_id=event.correlation_id)
            logger.debug(
                "order_approved",
                extra={
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": str(order.quantity),
                    "strategy": order.strategy_name,
                    "correlation_id": event.correlation_id,
                },
            )
            self._event_bus.publish(
                OrderApproved(
                    order=order,
                    signal=event.signal,
                    bar=event.bar,
                    correlation_id=event.correlation_id,
                )
            )
        else:
            reason = decision.rejection_reason or _DEFAULT_REJECTION_REASON
            logger.debug(
                "signal_rejected",
                extra={
                    "symbol": event.signal.symbol,
                    "reason": reason,
                    "correlation_id": event.correlation_id,
                },
            )
            self._event_bus.publish(
                RiskRejected(
                    signal=event.signal, reason=reason, correlation_id=event.correlation_id
                )
            )
