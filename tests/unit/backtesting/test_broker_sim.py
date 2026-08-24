from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.backtesting.broker_sim import SimBroker
from trading_platform.backtesting.fill_simulator import FillSimulator
from trading_platform.backtesting.models.fee_model import FeeModel
from trading_platform.backtesting.models.latency_model import LatencyModel
from trading_platform.backtesting.models.partial_fill_model import PartialFillModel
from trading_platform.backtesting.models.spread_model import SpreadModel
from trading_platform.backtesting.order_queue import OrderQueue
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order(
    order_id: str = "o1", symbol: str = "BTC/USDT", quantity: Decimal = Decimal("0.1")
) -> Order:
    return Order(
        order_id=order_id,
        correlation_id="c1",
        symbol=symbol,
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


def _broker(
    latency_bars: int = 1,
    volume_participation_rate: float = 1.0,
    rules: InstrumentRules | None = None,
) -> SimBroker:
    fill_simulator = FillSimulator(
        spread_model=SpreadModel(0),
        fee_model=FeeModel(assume_maker_on_limit=True),
        partial_fill_model=PartialFillModel(volume_participation_rate),
        use_next_bar_open=True,
    )
    return SimBroker(
        fill_simulator=fill_simulator,
        order_queue=OrderQueue(latency_model=LatencyModel(latency_bars)),
        instrument_rules={"BTC/USDT": rules} if rules else {},
    )


class TestSubmitOrder:
    def test_submit_order_never_returns_synchronous_fills(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = _broker(rules=btc_usdt_instrument_rules)

        fills = broker.submit_order(_order())

        assert fills == []


class TestProcessBar:
    def test_default_latency_of_one_bar_fills_on_the_next_bar_after_submission(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # Submission always happens strictly *after* the signal's triggering
        # bar has already closed, so this test's first process_bar call
        # already represents "bar N+1" relative to that triggering bar --
        # latency_bars=1 fills right here, at that bar's open.
        broker = _broker(latency_bars=1, rules=btc_usdt_instrument_rules)
        broker.submit_order(_order())

        fills = broker.process_bar(
            make_bar(open_="51000", high="51100", low="50900", close="51050", volume="10")
        )

        assert len(fills) == 1
        order, fill = fills[0]
        assert order.order_id == "o1"
        assert fill.fill_price == Decimal("51000")

    def test_higher_latency_defers_the_fill_by_that_many_additional_bars(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = _broker(latency_bars=2, rules=btc_usdt_instrument_rules)
        broker.submit_order(_order())

        first_bar_fills = broker.process_bar(
            make_bar(open_="50000", high="50100", low="49900", close="50050", volume="10")
        )
        assert first_bar_fills == []

        second_bar_fills = broker.process_bar(
            make_bar(open_="51000", high="51100", low="50900", close="51050", volume="10")
        )
        assert len(second_bar_fills) == 1
        assert second_bar_fills[0][1].fill_price == Decimal("51000")

    def test_order_for_a_different_symbol_is_ignored_by_this_bar(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        broker = _broker(
            latency_bars=0,
            rules=btc_usdt_instrument_rules,
        )
        broker.submit_order(_order(symbol="ETH/USDT"))

        fills = broker.process_bar(make_bar(symbol="BTC/USDT"))

        assert fills == []

    def test_missing_instrument_rules_skips_the_order_defensively(self, make_bar) -> None:
        broker = _broker(latency_bars=0, rules=None)
        broker.submit_order(_order())

        fills = broker.process_bar(make_bar())
        fills = broker.process_bar(make_bar())

        assert fills == []

    def test_partial_fill_across_bars_eventually_completes(
        self, btc_usdt_instrument_rules: InstrumentRules, make_bar
    ) -> None:
        broker = _broker(
            latency_bars=0, volume_participation_rate=0.5, rules=btc_usdt_instrument_rules
        )
        broker.submit_order(_order(quantity=Decimal("1")))

        first = broker.process_bar(
            make_bar(open_="50000", high="50100", low="49900", close="50050", volume="1")
        )
        assert len(first) == 1
        assert first[0][1].filled_qty == Decimal("0.5")
        assert first[0][1].is_complete is False

        second = broker.process_bar(
            make_bar(open_="50000", high="50100", low="49900", close="50050", volume="1")
        )
        assert len(second) == 1
        assert second[0][1].filled_qty == Decimal("0.5")
        assert second[0][1].is_complete is True

        # Fully filled -- no more fill attempts even if process_bar is called again.
        third = broker.process_bar(
            make_bar(open_="50000", high="50100", low="49900", close="50050", volume="1")
        )
        assert third == []

    def test_multiple_orders_are_each_processed_independently(
        self, btc_usdt_instrument_rules: InstrumentRules, make_bar
    ) -> None:
        broker = _broker(latency_bars=0, rules=btc_usdt_instrument_rules)
        broker.submit_order(_order(order_id="a", quantity=Decimal("0.1")))
        broker.submit_order(_order(order_id="b", quantity=Decimal("0.2")))

        fills = broker.process_bar(
            make_bar(open_="50000", high="50100", low="49900", close="50050", volume="10")
        )

        assert {order.order_id for order, _fill in fills} == {"a", "b"}
