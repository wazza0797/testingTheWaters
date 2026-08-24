from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.backtesting.engine import BacktestEngine
from trading_platform.backtesting.ledger import BacktestLedger
from trading_platform.domain.events.execution import FillReceived
from trading_platform.domain.events.market import BarClosed
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import Order, OrderSide, OrderType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order(order_id: str = "o1", quantity: Decimal = Decimal("0.1")) -> Order:
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


def _fill(
    order_id: str = "o1",
    side: OrderSide = OrderSide.BUY,
    filled_qty: Decimal = Decimal("0.1"),
    fill_price: Decimal = Decimal("50000"),
    fee: Decimal = Decimal("5"),
    timestamp: datetime = UTC_TS,
) -> Fill:
    return Fill(
        order_id=order_id,
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        filled_qty=filled_qty,
        remaining_qty=Decimal("0"),
        fill_price=fill_price,
        fee=fee,
        fee_type=FeeType.TAKER,
        is_complete=True,
        timestamp=timestamp,
    )


class StubBroker:
    """Test double for `SimBroker` — returns a pre-scripted list of
    `(order, fill)` pairs for each `process_bar` call, keyed by call index.
    """

    def __init__(self, fills_by_call: list[list[tuple[Order, Fill]]] | None = None) -> None:
        self._fills_by_call = fills_by_call or []
        self.processed_bars: list = []
        self._call_index = 0

    def process_bar(self, bar):
        self.processed_bars.append(bar)
        if self._call_index < len(self._fills_by_call):
            result = self._fills_by_call[self._call_index]
        else:
            result = []
        self._call_index += 1
        return result


class TestBacktestEngine:
    def test_runs_every_bar_through_the_trading_loop(self, fake_event_bus, make_bar) -> None:
        broker = StubBroker()
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")
        bars = [make_bar(), make_bar(), make_bar()]

        result = engine.run(bars, timeframe="1h")

        assert result.bars_processed == 3
        bar_closed_events = [e for e in fake_event_bus.published if isinstance(e, BarClosed)]
        assert len(bar_closed_events) == 3

    def test_calls_process_bar_once_per_bar_before_publishing_bar_closed(
        self, fake_event_bus, make_bar
    ) -> None:
        broker = StubBroker()
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")
        bars = [make_bar(), make_bar()]

        engine.run(bars, timeframe="1h")

        assert broker.processed_bars == bars

    def test_fills_from_process_bar_are_applied_to_the_ledger(
        self, fake_event_bus, make_bar
    ) -> None:
        order = _order()
        fill = _fill()
        broker = StubBroker(fills_by_call=[[(order, fill)]])
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        engine.run([make_bar()], timeframe="1h")

        assert ledger.position_for("BTC/USDT") is not None
        assert ledger.position_for("BTC/USDT").quantity == Decimal("0.1")

    def test_fills_from_process_bar_are_published_as_fill_received(
        self, fake_event_bus, make_bar
    ) -> None:
        order = _order()
        fill = _fill()
        broker = StubBroker(fills_by_call=[[(order, fill)]])
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        engine.run([make_bar()], timeframe="1h")

        fill_events = [e for e in fake_event_bus.published if isinstance(e, FillReceived)]
        assert len(fill_events) == 1
        assert fill_events[0].fill is fill
        assert fill_events[0].order is order
        assert fill_events[0].correlation_id == order.correlation_id

    def test_fill_received_is_published_before_that_bars_bar_closed(
        self, fake_event_bus, make_bar
    ) -> None:
        order = _order()
        fill = _fill()
        broker = StubBroker(fills_by_call=[[(order, fill)]])
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        engine.run([make_bar()], timeframe="1h")

        types_in_order = [type(e).__name__ for e in fake_event_bus.published]
        assert types_in_order == ["FillReceived", "BarClosed"]

    def test_equity_curve_has_one_point_per_bar(self, fake_event_bus, make_bar) -> None:
        broker = StubBroker()
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")
        bars = [
            make_bar(open_="50000", high="50000", low="50000", close="50000", timestamp=UTC_TS),
            make_bar(open_="51000", high="51000", low="51000", close="51000", timestamp=UTC_TS),
        ]

        result = engine.run(bars, timeframe="1h")

        assert len(result.equity_curve) == 2

    def test_equity_curve_reflects_open_position_marked_to_bar_close(
        self, fake_event_bus, make_bar
    ) -> None:
        order = _order(quantity=Decimal("1"))
        fill = _fill(filled_qty=Decimal("1"), fill_price=Decimal("50000"), fee=Decimal("0"))
        broker = StubBroker(fills_by_call=[[(order, fill)]])
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        result = engine.run(
            [make_bar(open_="50000", high="50000", low="50000", close="50000")], timeframe="1h"
        )

        # Equity = cash remaining after the buy + the open position marked to
        # this bar's close (this fixture doesn't enforce cash sufficiency, so
        # cash can go negative -- what matters here is equity = cash + position value).
        assert result.equity_curve[0].equity == ledger.cash + Decimal("1") * Decimal("50000")

    def test_result_reports_starting_and_ending_cash(self, fake_event_bus, make_bar) -> None:
        broker = StubBroker()
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        result = engine.run([make_bar()], timeframe="1h")

        assert result.starting_cash == Decimal("10000")
        assert result.ending_cash == Decimal("10000")

    def test_result_reports_total_fees_paid_from_the_ledger(self, fake_event_bus, make_bar) -> None:
        order = _order()
        fill = _fill(fee=Decimal("7.5"))
        broker = StubBroker(fills_by_call=[[(order, fill)]])
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        result = engine.run([make_bar()], timeframe="1h")

        assert result.total_fees_paid == Decimal("7.5")

    def test_result_reports_the_fills_that_occurred(self, fake_event_bus, make_bar) -> None:
        order = _order()
        fill = _fill()
        broker = StubBroker(fills_by_call=[[(order, fill)]])
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        result = engine.run([make_bar()], timeframe="1h")

        assert result.fills == (fill,)

    def test_result_reports_symbol_and_timeframe(self, fake_event_bus, make_bar) -> None:
        broker = StubBroker()
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        result = engine.run([make_bar()], timeframe="4h")

        assert result.symbol == "BTC/USDT"
        assert result.timeframe == "4h"

    def test_no_bars_returns_an_empty_result(self, fake_event_bus) -> None:
        broker = StubBroker()
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        engine = BacktestEngine(fake_event_bus, broker, ledger, symbol="BTC/USDT")

        result = engine.run([], timeframe="1h")

        assert result.bars_processed == 0
        assert result.equity_curve == ()
        assert result.fills == ()
        assert result.ending_cash == Decimal("10000")
