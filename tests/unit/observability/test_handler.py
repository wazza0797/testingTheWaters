from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.observability.handler import MetricsHandler

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order() -> Order:
    return Order(
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


class TestMetricsHandler:
    def test_bar_closed_increments_bars_counter(self, fake_metrics, make_bar) -> None:
        handler = MetricsHandler(fake_metrics)
        handler.handle(BarClosed(bar=make_bar(), mode="backtest"))

        assert (
            fake_metrics.counter_total(
                "trading_bars_processed_total", mode="backtest", symbol="BTC/USDT"
            )
            == 1
        )

    def test_signal_generated_increments_signals_counter(self, fake_metrics, make_bar) -> None:
        handler = MetricsHandler(fake_metrics)
        signal = Signal(
            symbol="BTC/USDT",
            signal_type=SignalType.BUY,
            strategy_name="sma_crossover",
            timestamp=UTC_TS,
        )
        handler.handle(SignalGenerated(signal=signal, bar=make_bar()))

        assert (
            fake_metrics.counter_total(
                "trading_signals_generated_total", strategy="sma_crossover", symbol="BTC/USDT"
            )
            == 1
        )

    def test_order_approved_increments_orders_submitted(self, fake_metrics, make_bar) -> None:
        handler = MetricsHandler(fake_metrics)
        order = _order()
        signal = Signal(
            symbol="BTC/USDT",
            signal_type=SignalType.BUY,
            strategy_name="sma_crossover",
            timestamp=UTC_TS,
        )
        handler.handle(OrderApproved(order=order, signal=signal, bar=make_bar()))

        assert (
            fake_metrics.counter_total(
                "trading_orders_submitted_total", symbol="BTC/USDT", side="buy"
            )
            == 1
        )

    def test_risk_rejected_increments_orders_rejected(self, fake_metrics) -> None:
        handler = MetricsHandler(fake_metrics)
        signal = Signal(
            symbol="BTC/USDT",
            signal_type=SignalType.SELL,
            strategy_name="sma_crossover",
            timestamp=UTC_TS,
        )
        handler.handle(RiskRejected(signal=signal, reason="max_position"))

        assert (
            fake_metrics.counter_total("trading_orders_rejected_total", reason="risk_rejected") == 1
        )

    def test_order_rejected_uses_event_reason_label(self, fake_metrics) -> None:
        handler = MetricsHandler(fake_metrics)
        handler.handle(OrderRejected(order=_order(), reason="below_min_notional"))

        assert (
            fake_metrics.counter_total("trading_orders_rejected_total", reason="below_min_notional")
            == 1
        )

    def test_fill_received_increments_fills_counter(self, fake_metrics) -> None:
        handler = MetricsHandler(fake_metrics)
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
        handler.handle(FillReceived(fill=fill, order=_order()))

        assert (
            fake_metrics.counter_total(
                "trading_fills_received_total", symbol="BTC/USDT", fee_type="taker"
            )
            == 1
        )

    def test_unhandled_event_type_is_ignored_without_error(self, fake_metrics) -> None:
        handler = MetricsHandler(fake_metrics)
        handler.handle(Heartbeat(mode="paper", uptime_seconds=1.0))

        assert fake_metrics.counters == []
