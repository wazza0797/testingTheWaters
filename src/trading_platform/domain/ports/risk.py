from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from trading_platform.domain.models.bar import Bar
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

    Milestone 0 defined the seam only. Milestone 4's `RiskHandler` subscribes
    to `SignalGenerated` and calls this to unblock backtesting — `Signal`
    carries no quantity, so *something* has to turn it into a sized `Order`
    before execution can run, even before real risk rules exist.
    `risk/engine.py::PassThroughRiskEngine` is that initial implementation:
    it approves every signal it can size (long-only, fixed fraction of
    equity — see `risk/sizing.py`) and rejects only what it structurally
    can't act on (already in a position on a `BUY`, nothing to close on a
    `SELL`/`CLOSE`). True risk rules (max position, drawdown halt, etc.) are
    unscheduled future work — they'd land as `risk/rules/*` composed into a
    rule-chain engine without changing this Protocol.

    `bar` is the signal's triggering bar (see `SignalGenerated.bar`) — the
    reference price a sizer needs, since `Signal` itself is deliberately
    price-free.
    """

    def evaluate(self, signal: Signal, bar: Bar) -> RiskDecision: ...


class IPendingOrderTracker(Protocol):
    """Whether a symbol currently has an order submitted but not yet fully
    resolved (filled or rejected) — i.e. already approved and still working
    somewhere between Risk and a terminal outcome.

    `PassThroughRiskEngine` needs this because `IPortfolioView.position_for`
    only reflects *filled* fills — it says nothing about an order that was
    already approved and is still sitting in latency, or only partially
    filled. Without this check, a second signal for the same symbol could
    be approved while an earlier order for it is still outstanding,
    breaking the long-only "no averaging/pyramiding, no double-close"
    policy `PassThroughRiskEngine` otherwise enforces (see its docstring).
    `backtesting.broker_sim.SimBroker` is the first implementation, backed
    by its internal `OrderQueue`.
    """

    def has_pending_order(self, symbol: str) -> bool: ...
