from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.errors import ExchangeAdapterError
from trading_platform.domain.events.execution import FillReceived, OrderRejected
from trading_platform.domain.events.risk import OrderApproved
from trading_platform.domain.events.strategy import SignalGenerated
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.signal import Signal, SignalType
from trading_platform.execution.handler import ExecutionHandler

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


class StubBroker:
    def __init__(self) -> None:
        self.submitted: list[Order] = []
        self.next_fills: list[Fill] = []
        self.raise_error: Exception | None = None

    def submit_order(self, order: Order) -> list[Fill]:
        self.submitted.append(order)
        if self.raise_error is not None:
            raise self.raise_error
        return self.next_fills


def _order(quantity: Decimal = Decimal("1"), symbol: str = "BTC/USDT") -> Order:
    return Order(
        order_id="o1",
        correlation_id="c1",
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


def _signal() -> Signal:
    return Signal(
        symbol="BTC/USDT", signal_type=SignalType.BUY, strategy_name="test", timestamp=UTC_TS
    )


def _fill(order_id: str = "o1") -> Fill:
    return Fill(
        order_id=order_id,
        correlation_id="c1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        filled_qty=Decimal("1"),
        remaining_qty=Decimal("0"),
        fill_price=Decimal("50000"),
        fee=Decimal("5"),
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=UTC_TS,
    )


class TestExecutionHandler:
    def test_ignores_events_that_are_not_order_approved(self, fake_event_bus, make_bar) -> None:
        broker = StubBroker()
        handler = ExecutionHandler(broker, {}, fake_event_bus)

        handler.handle(SignalGenerated(signal=_signal(), bar=make_bar()))

        assert broker.submitted == []
        assert fake_event_bus.published == []

    def test_rejects_when_no_instrument_rules_registered_for_symbol(
        self, fake_event_bus, make_bar
    ) -> None:
        broker = StubBroker()
        handler = ExecutionHandler(broker, {}, fake_event_bus)

        handler.handle(OrderApproved(order=_order(), signal=_signal(), bar=make_bar()))

        published = fake_event_bus.published[0]
        assert isinstance(published, OrderRejected)
        assert "instrument rules" in published.reason
        assert broker.submitted == []

    def test_rejects_an_order_that_fails_validation_without_calling_the_broker(
        self, fake_event_bus, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = StubBroker()
        handler = ExecutionHandler(broker, {"BTC/USDT": btc_usdt_instrument_rules}, fake_event_bus)
        tiny_order = _order(quantity=Decimal("0.00001"))  # notional way below min_notional=10
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        handler.handle(OrderApproved(order=tiny_order, signal=_signal(), bar=bar))

        published = fake_event_bus.published[0]
        assert isinstance(published, OrderRejected)
        assert broker.submitted == []

    def test_valid_order_is_submitted_to_the_broker_and_fills_are_published(
        self, fake_event_bus, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = StubBroker()
        broker.next_fills = [_fill()]
        handler = ExecutionHandler(broker, {"BTC/USDT": btc_usdt_instrument_rules}, fake_event_bus)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        handler.handle(OrderApproved(order=_order(), signal=_signal(), bar=bar))

        assert len(broker.submitted) == 1
        published = fake_event_bus.published[0]
        assert isinstance(published, FillReceived)
        assert published.fill.order_id == "o1"

    def test_publishes_one_fill_received_per_returned_fill(
        self, fake_event_bus, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = StubBroker()
        broker.next_fills = [_fill(), _fill()]
        handler = ExecutionHandler(broker, {"BTC/USDT": btc_usdt_instrument_rules}, fake_event_bus)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        handler.handle(OrderApproved(order=_order(), signal=_signal(), bar=bar))

        fills_published = [e for e in fake_event_bus.published if isinstance(e, FillReceived)]
        assert len(fills_published) == 2

    def test_no_fills_returned_publishes_nothing(
        self, fake_event_bus, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = StubBroker()
        broker.next_fills = []
        handler = ExecutionHandler(broker, {"BTC/USDT": btc_usdt_instrument_rules}, fake_event_bus)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        handler.handle(OrderApproved(order=_order(), signal=_signal(), bar=bar))

        assert fake_event_bus.published == []

    def test_broker_exchange_error_is_converted_to_order_rejected(
        self, fake_event_bus, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = StubBroker()
        broker.raise_error = ExchangeAdapterError("simulated exchange rejection")
        handler = ExecutionHandler(broker, {"BTC/USDT": btc_usdt_instrument_rules}, fake_event_bus)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        handler.handle(OrderApproved(order=_order(), signal=_signal(), bar=bar))

        published = fake_event_bus.published[0]
        assert isinstance(published, OrderRejected)
        assert "simulated exchange rejection" in published.reason

    def test_correlation_id_from_the_triggering_event_is_reused(
        self, fake_event_bus, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = StubBroker()
        broker.next_fills = [_fill()]
        handler = ExecutionHandler(broker, {"BTC/USDT": btc_usdt_instrument_rules}, fake_event_bus)
        bar = make_bar(close="50000", open_="50000", high="50000", low="50000")

        handler.handle(
            OrderApproved(order=_order(), signal=_signal(), bar=bar, correlation_id="trace-1")
        )

        published = fake_event_bus.published[0]
        assert published.correlation_id == "trace-1"

    def test_rejection_correlation_id_from_the_triggering_event_is_reused(
        self, fake_event_bus, make_bar
    ) -> None:
        broker = StubBroker()
        handler = ExecutionHandler(broker, {}, fake_event_bus)

        handler.handle(
            OrderApproved(
                order=_order(), signal=_signal(), bar=make_bar(), correlation_id="trace-2"
            )
        )

        published = fake_event_bus.published[0]
        assert published.correlation_id == "trace-2"
