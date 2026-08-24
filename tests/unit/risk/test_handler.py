from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.events.risk import OrderApproved, RiskRejected
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.events.system import Heartbeat
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.domain.ports.risk import RiskDecision
from trading_platform.risk.handler import RiskHandler

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


class StubRiskEngine:
    """Test double for `IRiskEngine` — returns whatever decision is queued,
    and records every `(signal, bar)` pair it was called with.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[Signal, object]] = []
        self.next_decision = RiskDecision(order=None, rejection_reason="not configured")

    def evaluate(self, signal, bar):
        self.calls.append((signal, bar))
        return self.next_decision


def _signal() -> Signal:
    return Signal(
        symbol="BTC/USDT", signal_type=SignalType.BUY, strategy_name="test", timestamp=UTC_TS
    )


def _order(correlation_id: str = "pending") -> Order:
    return Order(
        order_id="o1",
        correlation_id=correlation_id,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.2"),
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


class TestRiskHandler:
    def test_ignores_events_that_are_not_signal_generated(self, fake_event_bus) -> None:
        engine = StubRiskEngine()
        handler = RiskHandler(engine, fake_event_bus)

        handler.handle(Heartbeat(mode="backtest", uptime_seconds=1.0))

        assert engine.calls == []
        assert fake_event_bus.published == []

    def test_passes_signal_and_bar_from_the_event_to_the_engine(
        self, make_bar, fake_event_bus
    ) -> None:
        engine = StubRiskEngine()
        handler = RiskHandler(engine, fake_event_bus)
        signal = _signal()
        bar = make_bar()

        handler.handle(SignalGenerated(signal=signal, bar=bar))

        assert engine.calls == [(signal, bar)]

    def test_approved_decision_publishes_order_approved_with_event_correlation_id(
        self, make_bar, fake_event_bus
    ) -> None:
        engine = StubRiskEngine()
        engine.next_decision = RiskDecision(order=_order(), rejection_reason=None)
        handler = RiskHandler(engine, fake_event_bus)
        signal = _signal()
        bar = make_bar()

        handler.handle(SignalGenerated(signal=signal, bar=bar, correlation_id="trace-1"))

        published = fake_event_bus.published[0]
        assert isinstance(published, OrderApproved)
        assert published.correlation_id == "trace-1"
        assert published.order.correlation_id == "trace-1"
        assert published.signal == signal
        assert published.bar == bar

    def test_approved_order_correlation_id_overwrites_the_engines_placeholder(
        self, make_bar, fake_event_bus
    ) -> None:
        engine = StubRiskEngine()
        engine.next_decision = RiskDecision(
            order=_order(correlation_id="whatever-the-engine-set"), rejection_reason=None
        )
        handler = RiskHandler(engine, fake_event_bus)

        handler.handle(SignalGenerated(signal=_signal(), bar=make_bar(), correlation_id="trace-2"))

        published = fake_event_bus.published[0]
        assert isinstance(published, OrderApproved)
        assert published.order.correlation_id == "trace-2"

    def test_rejected_decision_publishes_risk_rejected_with_reason(
        self, make_bar, fake_event_bus
    ) -> None:
        engine = StubRiskEngine()
        engine.next_decision = RiskDecision(order=None, rejection_reason="already in a position")
        handler = RiskHandler(engine, fake_event_bus)
        signal = _signal()

        handler.handle(SignalGenerated(signal=signal, bar=make_bar(), correlation_id="trace-3"))

        published = fake_event_bus.published[0]
        assert isinstance(published, RiskRejected)
        assert published.signal == signal
        assert published.reason == "already in a position"
        assert published.correlation_id == "trace-3"

    def test_rejected_decision_with_no_reason_falls_back_to_a_default_message(
        self, make_bar, fake_event_bus
    ) -> None:
        engine = StubRiskEngine()
        engine.next_decision = RiskDecision(order=None, rejection_reason=None)
        handler = RiskHandler(engine, fake_event_bus)

        handler.handle(SignalGenerated(signal=_signal(), bar=make_bar()))

        published = fake_event_bus.published[0]
        assert isinstance(published, RiskRejected)
        assert published.reason
