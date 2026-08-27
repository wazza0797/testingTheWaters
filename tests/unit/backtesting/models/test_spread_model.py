from __future__ import annotations

from decimal import Decimal

import pytest

from trading_platform.backtesting.models.spread_model import SpreadModel
from trading_platform.domain.models.order import OrderSide


class TestConstruction:
    def test_rejects_negative_spread_bps(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            SpreadModel(spread_bps=-1)

    def test_accepts_zero_spread(self) -> None:
        SpreadModel(spread_bps=0)


class TestFillPrice:
    def test_zero_spread_fills_exactly_at_mid(self) -> None:
        model = SpreadModel(spread_bps=0)

        assert model.fill_price(OrderSide.BUY, Decimal("50000")) == Decimal("50000")
        assert model.fill_price(OrderSide.SELL, Decimal("50000")) == Decimal("50000")

    def test_buy_fills_above_mid(self) -> None:
        model = SpreadModel(spread_bps=10)  # 10 bps = 0.1%, half = 0.05%

        price = model.fill_price(OrderSide.BUY, Decimal("50000"))

        assert price == Decimal("50025.0000")  # 50000 * 1.0005

    def test_sell_fills_below_mid(self) -> None:
        model = SpreadModel(spread_bps=10)

        price = model.fill_price(OrderSide.SELL, Decimal("50000"))

        assert price == Decimal("49975.0000")  # 50000 * 0.9995

    def test_buy_and_sell_are_symmetric_around_mid(self) -> None:
        model = SpreadModel(spread_bps=20)
        mid = Decimal("1000")

        buy_price = model.fill_price(OrderSide.BUY, mid)
        sell_price = model.fill_price(OrderSide.SELL, mid)

        assert buy_price - mid == mid - sell_price

    def test_larger_spread_bps_widens_the_gap(self) -> None:
        narrow = SpreadModel(spread_bps=5)
        wide = SpreadModel(spread_bps=50)
        mid = Decimal("50000")

        narrow_price = narrow.fill_price(OrderSide.BUY, mid)
        wide_price = wide.fill_price(OrderSide.BUY, mid)

        assert wide_price - mid > narrow_price - mid


class TestVolatilityScaling:
    def test_k_zero_ignores_atr(self) -> None:
        model = SpreadModel(spread_bps=10, volatility_k=0.0)
        mid = Decimal("50000")

        without_atr = model.fill_price(OrderSide.BUY, mid)
        with_atr = model.fill_price(OrderSide.BUY, mid, atr=Decimal("1000"))

        assert without_atr == with_atr

    def test_high_atr_widens_spread_vs_low_atr(self) -> None:
        model = SpreadModel(spread_bps=5, volatility_k=2.0)
        mid = Decimal("50000")

        low_vol = model.fill_price(OrderSide.BUY, mid, atr=Decimal("100"))
        high_vol = model.fill_price(OrderSide.BUY, mid, atr=Decimal("1000"))

        assert high_vol > low_vol

    def test_rejects_negative_volatility_k(self) -> None:
        with pytest.raises(ValueError, match="volatility_k"):
            SpreadModel(spread_bps=5, volatility_k=-1.0)
