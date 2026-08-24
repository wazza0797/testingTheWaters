from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_platform.backtesting.ledger import BacktestLedger
from trading_platform.domain.errors import PortfolioError
from trading_platform.domain.models.fill import FeeType, Fill
from trading_platform.domain.models.order import OrderSide

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _fill(
    side: OrderSide,
    filled_qty: Decimal,
    fill_price: Decimal,
    fee: Decimal = Decimal("0"),
    remaining_qty: Decimal = Decimal("0"),
    is_complete: bool = True,
    timestamp: datetime = UTC_TS,
    symbol: str = "BTC/USDT",
) -> Fill:
    return Fill(
        order_id="o1",
        correlation_id="c1",
        symbol=symbol,
        side=side,
        filled_qty=filled_qty,
        remaining_qty=remaining_qty,
        fill_price=fill_price,
        fee=fee,
        fee_type=FeeType.TAKER,
        is_complete=is_complete,
        timestamp=timestamp,
    )


class TestInitialState:
    def test_starts_with_the_given_cash_and_no_positions(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))

        assert ledger.cash == Decimal("10000")
        assert ledger.position_for("BTC/USDT") is None
        assert ledger.equity({"BTC/USDT": Decimal("50000")}) == Decimal("10000")
        assert ledger.fills == ()
        assert ledger.total_fees_paid == Decimal("0")


class TestBuyFills:
    def test_a_single_buy_fill_opens_a_position_and_deducts_cash_plus_fee(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))

        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000"), fee=Decimal("5")))

        assert ledger.cash == Decimal("10000") - (Decimal("0.1") * Decimal("50000") + Decimal("5"))
        position = ledger.position_for("BTC/USDT")
        assert position is not None
        assert position.quantity == Decimal("0.1")
        assert position.average_entry_price == Decimal("50000")

    def test_two_partial_buy_fills_accumulate_a_weighted_average_entry_price(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("100000"))

        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000")))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("60000")))

        position = ledger.position_for("BTC/USDT")
        assert position is not None
        assert position.quantity == Decimal("0.2")
        assert position.average_entry_price == Decimal("55000")

    def test_equity_reflects_mark_to_market_value_after_a_buy(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000")))

        # cash=5000, position 0.1 BTC marked at 60000 -> 5000 + 6000 = 11000
        equity = ledger.equity({"BTC/USDT": Decimal("60000")})

        assert equity == Decimal("11000")

    def test_fills_and_total_fees_paid_accumulate(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000"), fee=Decimal("2")))
        ledger.apply_fill(
            _fill(OrderSide.BUY, Decimal("0.05"), Decimal("51000"), fee=Decimal("1.5"))
        )

        assert len(ledger.fills) == 2
        assert ledger.total_fees_paid == Decimal("3.5")


class TestSellFills:
    def test_a_full_sell_closes_the_position_and_credits_cash_minus_fee(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000")))

        ledger.apply_fill(_fill(OrderSide.SELL, Decimal("0.1"), Decimal("60000"), fee=Decimal("6")))

        assert ledger.position_for("BTC/USDT") is None
        # cash after buy = 5000; after sell = 5000 + (0.1*60000 - 6) = 10994
        assert ledger.cash == Decimal("10994")

    def test_a_partial_sell_reduces_quantity_and_keeps_the_position_open(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.2"), Decimal("50000")))

        ledger.apply_fill(_fill(OrderSide.SELL, Decimal("0.1"), Decimal("60000")))

        position = ledger.position_for("BTC/USDT")
        assert position is not None
        assert position.quantity == Decimal("0.1")
        assert position.average_entry_price == Decimal("50000")  # unchanged by a sell

    def test_realized_pnl_accumulates_across_partial_sells_before_fully_closing(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.2"), Decimal("50000")))

        ledger.apply_fill(_fill(OrderSide.SELL, Decimal("0.1"), Decimal("60000")))
        position = ledger.position_for("BTC/USDT")
        assert position is not None
        assert position.realized_pnl == Decimal("1000")  # (60000-50000) * 0.1

        ledger.apply_fill(_fill(OrderSide.SELL, Decimal("0.1"), Decimal("70000")))
        assert ledger.position_for("BTC/USDT") is None  # fully closed, position removed

    def test_selling_more_than_held_raises_portfolio_error(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000")))

        with pytest.raises(PortfolioError, match="exceeds held"):
            ledger.apply_fill(_fill(OrderSide.SELL, Decimal("0.2"), Decimal("60000")))

    def test_selling_with_no_open_position_raises_portfolio_error(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))

        with pytest.raises(PortfolioError, match="no open position"):
            ledger.apply_fill(_fill(OrderSide.SELL, Decimal("0.1"), Decimal("60000")))


class TestMultiSymbolIsolation:
    def test_positions_for_different_symbols_do_not_interfere(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))

        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000"), symbol="BTC/USDT"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("1"), Decimal("3000"), symbol="ETH/USDT"))

        btc = ledger.position_for("BTC/USDT")
        eth = ledger.position_for("ETH/USDT")
        assert btc is not None and btc.quantity == Decimal("0.1")
        assert eth is not None and eth.quantity == Decimal("1")

    def test_equity_marks_every_held_symbol_using_supplied_prices(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000"), symbol="BTC/USDT"))
        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("1"), Decimal("3000"), symbol="ETH/USDT"))

        equity = ledger.equity({"BTC/USDT": Decimal("50000"), "ETH/USDT": Decimal("3000")})

        # cash = 10000 - 5000 - 3000 = 2000; + 0.1*50000 + 1*3000 = 2000+5000+3000=10000
        assert equity == Decimal("10000")


class TestTimestampTracking:
    def test_timestamp_is_none_before_any_fill(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))

        assert ledger.timestamp is None

    def test_timestamp_advances_with_each_fill(self) -> None:
        ledger = BacktestLedger(starting_cash=Decimal("10000"))
        later = UTC_TS + timedelta(hours=1)

        ledger.apply_fill(_fill(OrderSide.BUY, Decimal("0.1"), Decimal("50000"), timestamp=later))

        assert ledger.timestamp == later
