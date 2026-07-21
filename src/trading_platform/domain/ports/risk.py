from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_platform.domain.models.order import Order
from trading_platform.domain.models.signal import Signal


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Result of evaluating a Signal. Exactly one of `order`/`rejection_reason` is set."""

    order: Order | None
    rejection_reason: str | None

    @property
    def approved(self) -> bool:
        return self.order is not None


class IRiskEngine(Protocol):
    """Sits between strategy and execution — sizes/approves or rejects a Signal.

    Milestone 0 defines the seam only; RiskHandler (M6) subscribes to
    `SignalGenerated` and calls this. The initial implementation is
    pass-through (approves everything); rule chains (max position, drawdown
    halt, etc.) land as `risk/rules/*` without changing this Protocol.
    """

    def evaluate(self, signal: Signal) -> RiskDecision: ...
