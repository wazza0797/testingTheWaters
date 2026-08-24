from __future__ import annotations

from dataclasses import dataclass

from trading_platform.domain.events.base import Event
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.order import Order
from trading_platform.domain.models.signal import Signal


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderApproved(Event):
    """Published by RiskHandler once a Signal passes risk checks and is sized into an Order.

    Carries `bar` (the same bar that triggered the originating `Signal`, from
    `SignalGenerated`) so `ExecutionHandler`'s `OrderValidator` (Milestone 4)
    has a reference price for market orders' `min_notional` check — an `Order`
    itself has no price when it's a market order.
    """

    order: Order
    signal: Signal
    bar: Bar


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskRejected(Event):
    """Published by RiskHandler when a Signal fails a risk rule. No Order is created."""

    signal: Signal
    reason: str
