from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.backtesting.models.latency_model import LatencyModel
from trading_platform.backtesting.order_queue import OrderQueue
from trading_platform.domain.models.order import Order, OrderSide, OrderType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order(order_id: str = "o1", quantity: Decimal = Decimal("1")) -> Order:
    return Order(
        order_id=order_id,
        correlation_id="c1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


class TestLatencyGating:
    def test_order_with_one_bar_latency_is_not_ready_until_the_next_advance(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=1))
        queue.enqueue(_order())

        ready = queue.advance()

        assert [q.order.order_id for q in ready] == ["o1"]

    def test_order_with_two_bar_latency_needs_two_advances(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=2))
        queue.enqueue(_order())

        first = queue.advance()
        second = queue.advance()

        assert first == []
        assert [q.order.order_id for q in second] == ["o1"]

    def test_zero_latency_still_requires_one_advance_call(self) -> None:
        # No look-ahead: even latency_bars=0 can't fill before the next bar.
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=0))
        queue.enqueue(_order())

        ready = queue.advance()

        assert [q.order.order_id for q in ready] == ["o1"]

    def test_pending_and_active_counts_reflect_latency_state(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=2))
        queue.enqueue(_order())

        assert queue.pending_count == 1
        assert queue.active_count == 0

        queue.advance()
        assert queue.pending_count == 1
        assert queue.active_count == 0

        queue.advance()
        assert queue.pending_count == 0
        assert queue.active_count == 1


class TestPartialFillTracking:
    def test_ready_order_carries_full_remaining_qty_initially(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=1))
        queue.enqueue(_order(quantity=Decimal("10")))

        ready = queue.advance()

        assert ready[0].remaining_qty == Decimal("10")

    def test_partial_fill_reduces_remaining_qty_and_keeps_order_in_queue(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=1))
        queue.enqueue(_order(quantity=Decimal("10")))
        queue.advance()

        queue.record_fill("o1", Decimal("4"))

        ready = queue.advance()
        assert [q.remaining_qty for q in ready] == [Decimal("6")]

    def test_full_fill_removes_the_order_from_the_queue(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=1))
        queue.enqueue(_order(quantity=Decimal("10")))
        queue.advance()

        queue.record_fill("o1", Decimal("10"))

        assert queue.advance() == []
        assert queue.pending_count == 0
        assert queue.active_count == 0

    def test_activated_order_stays_active_on_subsequent_advances_without_further_latency(
        self,
    ) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=1))
        queue.enqueue(_order(quantity=Decimal("10")))
        queue.advance()
        queue.record_fill("o1", Decimal("3"))

        # Should be immediately ready again next bar, no extra latency delay.
        ready = queue.advance()

        assert [q.remaining_qty for q in ready] == [Decimal("7")]


class TestMultipleOrders:
    def test_orders_enqueued_at_different_times_activate_independently(self) -> None:
        queue = OrderQueue(latency_model=LatencyModel(latency_bars=1))
        queue.enqueue(_order(order_id="first"))

        first_ready = queue.advance()
        queue.enqueue(_order(order_id="second"))
        second_ready = queue.advance()

        assert [q.order.order_id for q in first_ready] == ["first"]
        assert {q.order.order_id for q in second_ready} == {"first", "second"}
