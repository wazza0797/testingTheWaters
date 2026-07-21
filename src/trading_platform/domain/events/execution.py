from __future__ import annotations

from dataclasses import dataclass

from trading_platform.domain.events.base import Event
from trading_platform.domain.models.fill import Fill
from trading_platform.domain.models.order import Order


@dataclass(frozen=True, slots=True, kw_only=True)
class FillReceived(Event):
    """Published by ExecutionHandler/SimBroker/PaperBroker on a (possibly partial) fill."""

    fill: Fill
    order: Order


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRejected(Event):
    """Published when an approved Order fails exchange validation (min size, notional, precision)."""

    order: Order
    reason: str
