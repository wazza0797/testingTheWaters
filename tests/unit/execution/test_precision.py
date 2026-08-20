from __future__ import annotations

from decimal import ROUND_UP, Decimal

import pytest

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.execution.precision import (
    meets_min_notional,
    meets_min_qty,
    round_price,
    round_qty,
    round_to_step,
)


class TestRoundToStep:
    def test_rounds_down_by_default(self) -> None:
        assert round_to_step(Decimal("42123.456"), Decimal("0.01")) == Decimal("42123.45")

    def test_exact_multiple_is_unchanged(self) -> None:
        assert round_to_step(Decimal("100.00"), Decimal("0.01")) == Decimal("100.00")

    def test_rounds_up_when_requested(self) -> None:
        assert round_to_step(Decimal("42123.456"), Decimal("0.01"), rounding=ROUND_UP) == Decimal(
            "42123.46"
        )

    def test_non_positive_step_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            round_to_step(Decimal("100"), Decimal("0"))


class TestRoundPriceAndQty:
    def test_round_price_uses_tick_size(self, btc_usdt_instrument_rules: InstrumentRules) -> None:
        price = round_price(Decimal("42123.4567"), btc_usdt_instrument_rules)
        assert price == Decimal("42123.45")

    def test_round_qty_uses_step_size(self, btc_usdt_instrument_rules: InstrumentRules) -> None:
        qty = round_qty(Decimal("0.123456789"), btc_usdt_instrument_rules)
        assert qty == Decimal("0.12345")


class TestMeetsMinQty:
    def test_returns_true_when_at_or_above_minimum(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        assert meets_min_qty(Decimal("0.00001"), btc_usdt_instrument_rules) is True
        assert meets_min_qty(Decimal("1"), btc_usdt_instrument_rules) is True

    def test_returns_false_when_below_minimum(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        assert meets_min_qty(Decimal("0.000001"), btc_usdt_instrument_rules) is False


class TestMeetsMinNotional:
    def test_returns_true_when_notional_meets_minimum(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        assert meets_min_notional(Decimal("1"), Decimal("50000"), btc_usdt_instrument_rules) is True

    def test_returns_false_when_notional_below_minimum(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        assert (
            meets_min_notional(Decimal("0.0001"), Decimal("50000"), btc_usdt_instrument_rules)
            is False
        )
