from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.backtesting.fill_simulator import FillSimulator
from trading_platform.backtesting.models.fee_model import FeeModel
from trading_platform.backtesting.models.partial_fill_model import PartialFillModel
from trading_platform.backtesting.models.spread_model import SpreadModel
from trading_platform.domain.models.fill import FeeType
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _market_order(side: OrderSide = OrderSide.BUY, quantity: Decimal = Decimal("0.1")) -> Order:
    return Order(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        price=None,
        strategy_name="test",
        created_at=UTC_TS,
    )


def _limit_order(side: OrderSide, price: Decimal, quantity: Decimal = Decimal("0.1")) -> Order:
    return Order(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=price,
        strategy_name="test",
        created_at=UTC_TS,
    )


def _simulator(
    spread_bps: float = 0, volume_participation_rate: float = 1.0, use_next_bar_open: bool = True
) -> FillSimulator:
    return FillSimulator(
        spread_model=SpreadModel(spread_bps),
        fee_model=FeeModel(assume_maker_on_limit=True),
        partial_fill_model=PartialFillModel(volume_participation_rate),
        use_next_bar_open=use_next_bar_open,
    )


class TestMarketOrderFills:
    def test_market_buy_fills_at_bar_open_with_no_spread(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(spread_bps=0)
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _market_order(), Decimal("0.1"), bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert fill.fill_price == Decimal("50000")
        assert fill.fee_type == FeeType.TAKER
        assert fill.is_complete is True

    def test_market_order_uses_bar_close_when_use_next_bar_open_is_false(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(spread_bps=0, use_next_bar_open=False)
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _market_order(), Decimal("0.1"), bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert fill.fill_price == Decimal("50050")

    def test_market_buy_fee_is_taker_rate(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(spread_bps=0)
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _market_order(), Decimal("0.1"), bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert (
            fill.fee == Decimal("0.1") * Decimal("50000") * btc_usdt_instrument_rules.taker_fee_rate
        )

    def test_market_order_partial_fill_caps_at_volume_participation_rate(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(spread_bps=0, volume_participation_rate=0.10)
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="1")

        fill = simulator.simulate_fill(
            _market_order(quantity=Decimal("5")), Decimal("5"), bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert fill.filled_qty == Decimal("0.1")  # 10% of 1 volume
        assert fill.remaining_qty == Decimal("4.9")
        assert fill.is_complete is False

    def test_returns_none_when_bar_has_zero_volume(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator()
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="0")

        fill = simulator.simulate_fill(
            _market_order(), Decimal("0.1"), bar, btc_usdt_instrument_rules
        )

        assert fill is None

    def test_volume_derived_partial_fill_is_rounded_down_to_step_size(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # Regression: PartialFillModel caps by raw bar volume with no notion
        # of lot-size granularity -- 10% of a volume=1.234567 bar is
        # 0.1234567, which is not a multiple of
        # btc_usdt_instrument_rules.step_size=0.00001, so it must be rounded
        # down to 0.12345 rather than passed through as-is.
        simulator = _simulator(spread_bps=0, volume_participation_rate=0.10)
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="1.234567")

        fill = simulator.simulate_fill(
            _market_order(quantity=Decimal("5")), Decimal("5"), bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert fill.filled_qty == Decimal("0.12345")

    def test_returns_none_when_the_rounded_partial_fill_quantity_is_zero(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # A volume-derived quantity smaller than one step_size (0.00001)
        # rounds down to zero -- must be treated the same as "doesn't fill
        # at all this bar", not surfaced as a zero-quantity Fill.
        simulator = _simulator(spread_bps=0, volume_participation_rate=1.0)
        bar = make_bar(open_="50000", high="50100", low="49900", close="50050", volume="0.000005")

        fill = simulator.simulate_fill(
            _market_order(quantity=Decimal("5")), Decimal("5"), bar, btc_usdt_instrument_rules
        )

        assert fill is None


class TestLimitOrderFills:
    def test_buy_limit_fills_at_limit_price_when_bar_low_reaches_it(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator()
        bar = make_bar(open_="50000", high="50100", low="49800", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _limit_order(OrderSide.BUY, Decimal("49900")),
            Decimal("0.1"),
            bar,
            btc_usdt_instrument_rules,
        )

        assert fill is not None
        assert fill.fill_price == Decimal("49900")

    def test_buy_limit_does_not_fill_when_bar_low_never_reaches_it(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator()
        bar = make_bar(open_="50000", high="50100", low="49950", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _limit_order(OrderSide.BUY, Decimal("49900")),
            Decimal("0.1"),
            bar,
            btc_usdt_instrument_rules,
        )

        assert fill is None

    def test_sell_limit_fills_at_limit_price_when_bar_high_reaches_it(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator()
        bar = make_bar(open_="50000", high="50200", low="49900", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _limit_order(OrderSide.SELL, Decimal("50150")),
            Decimal("0.1"),
            bar,
            btc_usdt_instrument_rules,
        )

        assert fill is not None
        assert fill.fill_price == Decimal("50150")

    def test_sell_limit_does_not_fill_when_bar_high_never_reaches_it(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator()
        bar = make_bar(open_="50000", high="50050", low="49900", close="50020", volume="10")

        fill = simulator.simulate_fill(
            _limit_order(OrderSide.SELL, Decimal("50150")),
            Decimal("0.1"),
            bar,
            btc_usdt_instrument_rules,
        )

        assert fill is None

    def test_non_crossing_resting_buy_limit_gets_maker_fee_when_assumed(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(use_next_bar_open=True)
        # open=50000 (reference), limit=49900 is below open -> resting, non-crossing
        bar = make_bar(open_="50000", high="50100", low="49800", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _limit_order(OrderSide.BUY, Decimal("49900")),
            Decimal("0.1"),
            bar,
            btc_usdt_instrument_rules,
        )

        assert fill is not None
        assert fill.fee_type == FeeType.MAKER

    def test_crossing_buy_limit_gets_taker_fee(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(use_next_bar_open=True)
        # open=50000 (reference), limit=50100 is above open -> aggressive/crossing
        bar = make_bar(open_="50000", high="50200", low="49900", close="50050", volume="10")

        fill = simulator.simulate_fill(
            _limit_order(OrderSide.BUY, Decimal("50100")),
            Decimal("0.1"),
            bar,
            btc_usdt_instrument_rules,
        )

        assert fill is not None
        assert fill.fee_type == FeeType.TAKER


class TestFillTimestampAndOrderLinkage:
    def test_fill_carries_the_bars_timestamp_and_the_orders_ids(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator()
        bar = make_bar(
            timestamp=datetime(2024, 5, 1, tzinfo=UTC),
            open_="50000",
            high="50100",
            low="49900",
            close="50050",
            volume="10",
        )
        order = _market_order()

        fill = simulator.simulate_fill(order, Decimal("0.1"), bar, btc_usdt_instrument_rules)

        assert fill is not None
        assert fill.timestamp == datetime(2024, 5, 1, tzinfo=UTC)
        assert fill.order_id == order.order_id
        assert fill.correlation_id == order.correlation_id
        assert fill.symbol == order.symbol


class TestVolatilityAwareSpread:
    def test_observe_bar_widens_fill_price_after_atr_warms_up(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = FillSimulator(
            spread_model=SpreadModel(spread_bps=0, volatility_k=2.0, atr_period=2),
            fee_model=FeeModel(assume_maker_on_limit=True),
            partial_fill_model=PartialFillModel(1.0),
            use_next_bar_open=True,
        )
        seed_bars = [
            make_bar(open_="100", high="110", low="90", close="100", volume="10"),
            make_bar(open_="100", high="120", low="80", close="100", volume="10"),
            make_bar(open_="100", high="115", low="85", close="100", volume="10"),
        ]
        for bar in seed_bars:
            simulator.observe_bar(bar)

        fill_bar = make_bar(open_="100", high="110", low="90", close="100", volume="10")
        simulator.observe_bar(fill_bar)
        fill = simulator.simulate_fill(
            _market_order(), Decimal("0.1"), fill_bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert fill.fill_price > Decimal("100")

    def test_k_zero_observe_bar_is_noop_for_prices(
        self, make_bar, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        simulator = _simulator(spread_bps=10)
        bar = make_bar(open_="50000", high="51000", low="49000", close="50000", volume="10")
        for _ in range(20):
            simulator.observe_bar(bar)

        fill = simulator.simulate_fill(
            _market_order(), Decimal("0.1"), bar, btc_usdt_instrument_rules
        )

        assert fill is not None
        assert fill.fill_price == Decimal("50025.0000")
