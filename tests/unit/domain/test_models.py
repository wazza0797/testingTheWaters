from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_platform.domain.errors import ValidationError
from trading_platform.domain.models.bar import Bar
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.domain.models.portfolio import Portfolio
from trading_platform.domain.models.position import Position, PositionSide
from trading_platform.domain.models.signal import Signal, SignalType

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


class TestBar:
    def test_valid_bar_constructs(self, make_bar) -> None:
        bar = make_bar()
        assert bar.symbol == "BTC/USDT"
        assert bar.close == Decimal("105")

    def test_high_below_low_raises(self) -> None:
        with pytest.raises(ValidationError, match="high"):
            Bar(
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=UTC_TS,
                open=Decimal("100"),
                high=Decimal("90"),
                low=Decimal("110"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )

    def test_open_outside_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="open"):
            Bar(
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=UTC_TS,
                open=Decimal("200"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("100"),
                volume=Decimal("1"),
            )

    def test_close_outside_range_raises(self) -> None:
        with pytest.raises(ValidationError, match="close"):
            Bar(
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=UTC_TS,
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("200"),
                volume=Decimal("1"),
            )

    def test_negative_volume_raises(self) -> None:
        with pytest.raises(ValidationError, match="volume"):
            Bar(
                symbol="BTC/USDT",
                timeframe="1h",
                timestamp=UTC_TS,
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("90"),
                close=Decimal("100"),
                volume=Decimal("-1"),
            )

    def test_bar_is_immutable(self, make_bar) -> None:
        bar = make_bar()
        with pytest.raises(AttributeError):
            bar.close = Decimal("999")  # type: ignore[misc]


class TestSignal:
    def test_defaults(self) -> None:
        signal = Signal(
            symbol="BTC/USDT",
            signal_type=SignalType.BUY,
            strategy_name="sma_crossover",
            timestamp=UTC_TS,
        )
        assert signal.strength == 1.0
        assert signal.metadata == {}


class TestOrder:
    def test_market_order_has_no_price(self) -> None:
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
        assert order.price is None
        assert order.side is OrderSide.BUY


class TestFill:
    def test_partial_fill_flags(self) -> None:
        fill = Fill(
            order_id="o1",
            correlation_id="c1",
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            filled_qty=Decimal("0.005"),
            remaining_qty=Decimal("0.005"),
            fill_price=Decimal("100"),
            fee=Decimal("0.0001"),
            fee_type=FeeType.TAKER,
            is_complete=False,
            timestamp=UTC_TS,
        )
        assert not fill.is_complete
        assert fill.remaining_qty == Decimal("0.005")


class TestInstrumentRules:
    def test_valid_rules(self, btc_usdt_instrument_rules: InstrumentRules) -> None:
        assert btc_usdt_instrument_rules.tick_size == Decimal("0.01")

    @pytest.mark.parametrize(
        "field_name,value",
        [
            ("tick_size", Decimal("0")),
            ("step_size", Decimal("-1")),
            ("min_qty", Decimal("-1")),
            ("min_notional", Decimal("-1")),
        ],
    )
    def test_invalid_field_raises(self, field_name: str, value: Decimal) -> None:
        kwargs: dict[str, object] = {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "tick_size": Decimal("0.01"),
            "step_size": Decimal("0.00001"),
            "min_qty": Decimal("0.00001"),
            "min_notional": Decimal("10"),
            "price_precision": 2,
            "qty_precision": 5,
            "maker_fee_rate": Decimal("0.001"),
            "taker_fee_rate": Decimal("0.001"),
        }
        kwargs[field_name] = value
        with pytest.raises(ValidationError):
            InstrumentRules(**kwargs)  # type: ignore[arg-type]

    def test_negative_fee_rate_raises(self) -> None:
        with pytest.raises(ValidationError, match="Fee rates"):
            InstrumentRules(
                exchange="binance",
                symbol="BTC/USDT",
                tick_size=Decimal("0.01"),
                step_size=Decimal("0.00001"),
                min_qty=Decimal("0.00001"),
                min_notional=Decimal("10"),
                price_precision=2,
                qty_precision=5,
                maker_fee_rate=Decimal("-0.001"),
                taker_fee_rate=Decimal("0.001"),
            )


class TestPosition:
    def test_long_side(self) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("0.5"), average_entry_price=Decimal("100")
        )
        assert position.side is PositionSide.LONG
        assert not position.is_flat

    def test_short_side(self) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("-0.5"), average_entry_price=Decimal("100")
        )
        assert position.side is PositionSide.SHORT

    def test_flat_side(self) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("0"), average_entry_price=Decimal("100")
        )
        assert position.side is PositionSide.FLAT
        assert position.is_flat

    def test_unrealized_pnl_long(self) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("2"), average_entry_price=Decimal("100")
        )
        assert position.unrealized_pnl(Decimal("110")) == Decimal("20")

    def test_unrealized_pnl_short(self) -> None:
        position = Position(
            symbol="BTC/USDT", quantity=Decimal("-2"), average_entry_price=Decimal("100")
        )
        assert position.unrealized_pnl(Decimal("90")) == Decimal("20")


class TestPortfolio:
    def test_equity_sums_cash_and_positions(self) -> None:
        portfolio = Portfolio(
            cash=Decimal("1000"),
            positions={
                "BTC/USDT": Position(
                    symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
                )
            },
        )
        equity = portfolio.equity({"BTC/USDT": Decimal("150")})
        assert equity == Decimal("1150")

    def test_equity_ignores_symbols_without_mark_price(self) -> None:
        portfolio = Portfolio(
            cash=Decimal("1000"),
            positions={
                "BTC/USDT": Position(
                    symbol="BTC/USDT", quantity=Decimal("1"), average_entry_price=Decimal("100")
                )
            },
        )
        assert portfolio.equity({}) == Decimal("1000")

    def test_position_for_missing_symbol_returns_none(self) -> None:
        portfolio = Portfolio(cash=Decimal("1000"))
        assert portfolio.position_for("ETH/USDT") is None
