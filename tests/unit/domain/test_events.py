from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import ErrorOccurred, Heartbeat
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.signal import Signal, SignalType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


class TestEventBase:
    def test_correlation_id_defaults_and_is_unique(self, make_bar) -> None:
        event_one = BarClosed(bar=make_bar(), mode="backtest")
        event_two = BarClosed(bar=make_bar(), mode="backtest")
        assert event_one.correlation_id != event_two.correlation_id

    def test_timestamp_defaults_to_utc_now(self, make_bar) -> None:
        event = BarClosed(bar=make_bar(), mode="backtest")
        assert event.timestamp.tzinfo is not None

    def test_correlation_id_can_be_propagated_explicitly(self, make_bar) -> None:
        signal = Signal(
            symbol="BTC/USDT",
            signal_type=SignalType.BUY,
            strategy_name="sma_crossover",
            timestamp=UTC_TS,
        )
        signal_event = SignalGenerated(signal=signal, bar=make_bar(), correlation_id="fixed-id")
        order = Order(
            order_id="o1",
            correlation_id="fixed-id",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
            price=None,
            strategy_name="sma_crossover",
            created_at=UTC_TS,
        )
        order_event = OrderApproved(
            order=order, signal=signal, bar=make_bar(), correlation_id="fixed-id"
        )
        assert signal_event.correlation_id == order_event.correlation_id == "fixed-id"

    def test_events_are_frozen(self, make_bar) -> None:
        event = BarClosed(bar=make_bar(), mode="backtest")
        with pytest.raises(AttributeError):
            event.mode = "paper"  # type: ignore[misc]


class TestEventConstruction:
    """kw_only=True on Event subclasses must allow non-default fields to
    follow the base class's defaulted correlation_id/timestamp fields.
    """

    def test_bar_closed(self, make_bar) -> None:
        event = BarClosed(bar=make_bar(), mode="paper")
        assert event.mode == "paper"

    def test_risk_rejected(self) -> None:
        signal = Signal(
            symbol="BTC/USDT",
            signal_type=SignalType.SELL,
            strategy_name="sma_crossover",
            timestamp=UTC_TS,
        )
        event = RiskRejected(signal=signal, reason="max_position_exceeded")
        assert event.reason == "max_position_exceeded"

    def test_order_rejected(self) -> None:
        order = Order(
            order_id="o1",
            correlation_id="c1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
            price=None,
            strategy_name="sma_crossover",
            created_at=UTC_TS,
        )
        event = OrderRejected(order=order, reason="below_min_notional")
        assert event.reason == "below_min_notional"

    def test_fill_received(self) -> None:
        order = Order(
            order_id="o1",
            correlation_id="c1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.01"),
            price=None,
            strategy_name="sma_crossover",
            created_at=UTC_TS,
        )
        fill = Fill(
            order_id="o1",
            correlation_id="c1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            filled_qty=Decimal("0.01"),
            remaining_qty=Decimal("0"),
            fill_price=Decimal("100"),
            fee=Decimal("0.0001"),
            fee_type=FeeType.TAKER,
            is_complete=True,
            timestamp=UTC_TS,
        )
        event = FillReceived(fill=fill, order=order)
        assert event.fill.is_complete

    def test_heartbeat_and_error_occurred(self) -> None:
        heartbeat = Heartbeat(mode="paper", uptime_seconds=12.3)
        error = ErrorOccurred(source="strategy_handler", error_type="ValueError", message="boom")
        assert heartbeat.uptime_seconds == 12.3
        assert error.source == "strategy_handler"
