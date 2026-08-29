from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.analytics.handler import AnalyticsHandler
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import RiskRejected
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.signal import Signal, SignalType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order() -> Order:
    return Order(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("1"),
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


def _fill(side: OrderSide = OrderSide.BUY, price: str = "100") -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        filled_qty=Decimal("1"),
        remaining_qty=Decimal("0"),
        fill_price=Decimal(price),
        fee=Decimal("0"),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=UTC_TS,
    )


def _signal() -> Signal:
    return Signal(
        symbol="BTC/USDT",
        signal_type=SignalType.BUY,
        strategy_name="test",
        timestamp=UTC_TS,
    )


class TestAnalyticsHandler:
    def test_fill_received_updates_round_trip_count(self) -> None:
        handler = AnalyticsHandler()
        handler.handle(FillReceived(fill=_fill(OrderSide.BUY), order=_order()))
        handler.handle(FillReceived(fill=_fill(OrderSide.SELL, "110"), order=_order()))

        assert len(handler.state.fills) == 2
        assert len(handler.state.round_trips) == 1

    def test_non_fill_events_ignored(self, make_bar) -> None:
        handler = AnalyticsHandler()
        handler.handle(BarClosed(bar=make_bar(), mode="paper"))
        assert handler.state.fills == []

    def test_rejections_increment_counter(self) -> None:
        handler = AnalyticsHandler()
        handler.handle(OrderRejected(order=_order(), reason="min_notional"))
        handler.handle(RiskRejected(signal=_signal(), reason="no_cash"))
        assert handler.state.signals_rejected_total == 2

    def test_handler_exceptions_do_not_propagate(self) -> None:
        handler = AnalyticsHandler()

        def boom(_fill: Fill) -> None:
            raise RuntimeError("boom")

        handler.state.record_fill = boom  # type: ignore[method-assign]
        handler.handle(FillReceived(fill=_fill(), order=_order()))  # must not raise
