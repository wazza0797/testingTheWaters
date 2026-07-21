from __future__ import annotations

from dataclasses import dataclass

from trading_platform.domain.events.base import Event
from trading_platform.domain.models.order import Order
from trading_platform.domain.models.signal import Signal


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderApproved(Event):
    """Published by RiskHandler once a Signal passes risk checks and is sized into an Order."""

    order: Order
    signal: Signal


@dataclass(frozen=True, slots=True, kw_only=True)
class RiskRejected(Event):
    """Published by RiskHandler when a Signal fails a risk rule. No Order is created."""

    signal: Signal
    reason: str
