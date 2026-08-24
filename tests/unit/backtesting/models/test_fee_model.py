from __future__ import annotations

from decimal import Decimal

from trading_platform.backtesting.models.fee_model import FeeModel
from trading_platform.domain.models.fill import FeeType
from trading_platform.domain.models.instrument_rules import InstrumentRules
from trading_platform.domain.models.order import OrderType


class TestFeeTypeFor:
    def test_market_order_is_always_taker_even_if_it_would_not_cross(self) -> None:
        model = FeeModel(assume_maker_on_limit=True)

        assert model.fee_type_for(OrderType.MARKET, crosses_on_submission=False) == FeeType.TAKER
        assert model.fee_type_for(OrderType.MARKET, crosses_on_submission=True) == FeeType.TAKER

    def test_crossing_limit_order_is_always_taker(self) -> None:
        model = FeeModel(assume_maker_on_limit=True)

        assert model.fee_type_for(OrderType.LIMIT, crosses_on_submission=True) == FeeType.TAKER

    def test_non_crossing_limit_is_maker_when_configured(self) -> None:
        model = FeeModel(assume_maker_on_limit=True)

        assert model.fee_type_for(OrderType.LIMIT, crosses_on_submission=False) == FeeType.MAKER

    def test_non_crossing_limit_is_taker_when_maker_assumption_disabled(self) -> None:
        model = FeeModel(assume_maker_on_limit=False)

        assert model.fee_type_for(OrderType.LIMIT, crosses_on_submission=False) == FeeType.TAKER


class TestCalculateFee:
    def test_maker_fee_uses_maker_rate(self, btc_usdt_instrument_rules: InstrumentRules) -> None:
        fee = FeeModel.calculate_fee(
            Decimal("0.1"), Decimal("50000"), FeeType.MAKER, btc_usdt_instrument_rules
        )

        assert fee == Decimal("0.1") * Decimal("50000") * btc_usdt_instrument_rules.maker_fee_rate

    def test_taker_fee_uses_taker_rate(self, btc_usdt_instrument_rules: InstrumentRules) -> None:
        fee = FeeModel.calculate_fee(
            Decimal("0.1"), Decimal("50000"), FeeType.TAKER, btc_usdt_instrument_rules
        )

        assert fee == Decimal("0.1") * Decimal("50000") * btc_usdt_instrument_rules.taker_fee_rate

    def test_fee_scales_with_filled_quantity(
        self, btc_usdt_instrument_rules: InstrumentRules
    ) -> None:
        small = FeeModel.calculate_fee(
            Decimal("0.1"), Decimal("50000"), FeeType.TAKER, btc_usdt_instrument_rules
        )
        large = FeeModel.calculate_fee(
            Decimal("0.2"), Decimal("50000"), FeeType.TAKER, btc_usdt_instrument_rules
        )

        assert large == small * 2
