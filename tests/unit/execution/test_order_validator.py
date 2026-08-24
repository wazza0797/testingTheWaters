from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import Order, OrderSide, OrderType
from trading_platform.execution.order_validator import validate_order

UTC_TS = datetime(2024, 1, 1, tzinfo=UTC)


def _order(
    quantity: Decimal = Decimal("0.001"),
    price: Decimal | None = None,
    order_type: OrderType = OrderType.MARKET,
) -> Order:
    return Order(
        order_id="o1",
        correlation_id="c1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=order_type,
        quantity=quantity,
        price=price,
        strategy_name="test-strategy",
        created_at=UTC_TS,
    )


class TestValidateOrder:
    def test_returns_none_when_order_meets_all_rules(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        order = _order(quantity=Decimal("1"))

        result = validate_order(
            order, btc_usdt_instrument_rules, market_reference_price=Decimal("50000")
        )

        assert result is None

    def test_rejects_non_positive_quantity(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        order = _order(quantity=Decimal("0"))

        result = validate_order(
            order, btc_usdt_instrument_rules, market_reference_price=Decimal("50000")
        )

        assert result is not None
        assert "positive" in result

    def test_rejects_quantity_below_min_qty(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        order = _order(quantity=Decimal("0.000001"))

        result = validate_order(
            order, btc_usdt_instrument_rules, market_reference_price=Decimal("50000")
        )

        assert result is not None
        assert "min_qty" in result

    def test_rejects_market_order_notional_below_min_notional(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        # min_notional=10 on the fixture; 0.0001 * 50000 = 5 < 10
        order = _order(quantity=Decimal("0.0001"))

        result = validate_order(
            order, btc_usdt_instrument_rules, market_reference_price=Decimal("50000")
        )

        assert result is not None
        assert "min_notional" in result

    def test_market_order_notional_uses_the_supplied_reference_price(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        order = _order(quantity=Decimal("0.001"))

        # 0.001 * 50000 = 50 >= 10 -> passes
        assert (
            validate_order(
                order, btc_usdt_instrument_rules, market_reference_price=Decimal("50000")
            )
            is None
        )
        # 0.001 * 1000 = 1 < 10 -> fails on a lower reference price
        result = validate_order(
            order, btc_usdt_instrument_rules, market_reference_price=Decimal("1000")
        )
        assert result is not None

    def test_limit_order_notional_uses_the_orders_own_price_not_the_reference(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        order = _order(
            quantity=Decimal("0.001"), price=Decimal("50000"), order_type=OrderType.LIMIT
        )

        # Reference price is irrelevant/wrong here (e.g. stale) — the limit
        # order's own price is what determines its notional.
        result = validate_order(
            order, btc_usdt_instrument_rules, market_reference_price=Decimal("1")
        )

        assert result is None
