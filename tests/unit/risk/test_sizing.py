from __future__ import annotations

from decimal import Decimal

import pytest

from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.risk.sizing import EquityFractionSizer


class TestEquityFractionSizerConstruction:
    @pytest.mark.parametrize("fraction", [0.0, -0.1, 1.1, 2.0])
    def test_rejects_fraction_outside_zero_to_one(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="fraction"):
            EquityFractionSizer(fraction)

    def test_accepts_full_range_boundary(self) -> None:
        EquityFractionSizer(1.0)
        EquityFractionSizer(0.0001)


class TestEquityFractionSizerSize:
    def test_full_fraction_uses_all_equity(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        sizer = EquityFractionSizer(1.0)

        quantity = sizer.size(Decimal("10000"), Decimal("50000"), btc_usdt_instrument_rules)

        assert quantity == Decimal("0.2")

    def test_partial_fraction_uses_only_that_fraction_of_equity(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        sizer = EquityFractionSizer(0.5)

        quantity = sizer.size(Decimal("10000"), Decimal("50000"), btc_usdt_instrument_rules)

        assert quantity == Decimal("0.1")

    def test_rounds_down_to_step_size(self, btc_usdt_instrument_rules: InstrumentRules) -> None:
        sizer = EquityFractionSizer(1.0)

        # 10000 / 3333 = 3.0003... BTC -> rounds down to step_size 0.00001
        quantity = sizer.size(Decimal("10000"), Decimal("3333"), btc_usdt_instrument_rules)

        assert quantity == Decimal("3.00030")

    def test_never_exceeds_fraction_of_equity_value(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        sizer = EquityFractionSizer(1.0)
        equity = Decimal("10000")
        price = Decimal("3333")

        quantity = sizer.size(equity, price, btc_usdt_instrument_rules)

        assert quantity * price <= equity

    def test_zero_or_negative_equity_sizes_to_zero(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        sizer = EquityFractionSizer(1.0)

        assert sizer.size(Decimal("0"), Decimal("50000"), btc_usdt_instrument_rules) == Decimal("0")
        assert sizer.size(Decimal("-100"), Decimal("50000"), btc_usdt_instrument_rules) == Decimal(
            "0"
        )

    def test_zero_or_negative_price_sizes_to_zero(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        sizer = EquityFractionSizer(1.0)

        assert sizer.size(Decimal("10000"), Decimal("0"), btc_usdt_instrument_rules) == Decimal("0")

    def test_tiny_equity_can_round_down_to_zero(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        sizer = EquityFractionSizer(1.0)

        # 0.0001 / 50000 rounds down below step_size (0.00001) -> zero
        quantity = sizer.size(Decimal("0.0001"), Decimal("50000"), btc_usdt_instrument_rules)

        assert quantity == Decimal("0")
