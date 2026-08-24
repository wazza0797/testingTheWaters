from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trading_platform.backtesting.models.latency_model import LatencyModel
from trading_platform.domain.models.order import Order


@dataclass
class QueuedOrder:
    """One order still being worked by `SimBroker`: `order.quantity` is the
    original full size (immutable); `remaining_qty` is how much is still
    unfilled. `bars_remaining` counts down latency before the order is
    eligible for its first fill attempt; once it reaches zero the order
    stays in the queue (with `bars_remaining == 0`) until `remaining_qty`
    hits zero, being re-offered a fill attempt on every subsequent bar with
    no further latency.
    """

    order: Order
    remaining_qty: Decimal
    bars_remaining: int


@dataclass
class OrderQueue:
    """Tracks every order `SimBroker` is still working, across bars.

    One `OrderQueue` per `SimBroker` (effectively per backtest run — a fresh
    `SimBroker`/`OrderQueue` is constructed for each `trading-platform
    backtest` invocation). `enqueue` is called once per newly-submitted
    order; `advance` is called once per bar, before that bar's data is used
    for any fills (mirrors "processes pending order queue before each
    BarClosed" from the design doc).
    """

    latency_model: LatencyModel
    _orders: list[QueuedOrder] = field(default_factory=list)

    def enqueue(self, order: Order) -> None:
        self._orders.append(
            QueuedOrder(
                order=order,
                remaining_qty=order.quantity,
                bars_remaining=self.latency_model.latency_bars,
            )
        )

    def advance(self) -> list[QueuedOrder]:
        """Decrement latency on every still-pending order, then return every
        order now eligible for a fill attempt this bar (freshly activated,
        or already active from a previous bar with `remaining_qty` left).
        """
        ready: list[QueuedOrder] = []
        for queued in self._orders:
            if queued.bars_remaining > 0:
                queued.bars_remaining -= 1
            if queued.bars_remaining <= 0:
                ready.append(queued)
        return ready

    def record_fill(self, order_id: str, filled_qty: Decimal) -> None:
        """Reduce the tracked `remaining_qty` for `order_id` and drop it from
        the queue once fully filled.
        """
        for queued in self._orders:
            if queued.order.order_id == order_id:
                queued.remaining_qty -= filled_qty
                break
        self._orders = [q for q in self._orders if q.remaining_qty > 0]

    def has_pending_order(self, symbol: str) -> bool:
        """Whether any order for `symbol` is still being worked — waiting out
        latency or only partially filled. Backs `IPendingOrderTracker` (via
        `SimBroker.has_pending_order`) so `PassThroughRiskEngine` can avoid
        approving a second order for a symbol while an earlier one hasn't
        resolved yet.
        """
        return any(queued.order.symbol == symbol for queued in self._orders)

    @property
    def pending_count(self) -> int:
        """Orders still waiting out their latency (not yet eligible to fill)."""
        return sum(1 for q in self._orders if q.bars_remaining > 0)

    @property
    def active_count(self) -> int:
        """Orders past latency with quantity still unfilled."""
        return sum(1 for q in self._orders if q.bars_remaining <= 0)
