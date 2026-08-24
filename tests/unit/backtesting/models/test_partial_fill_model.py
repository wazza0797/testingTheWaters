from __future__ import annotations

from decimal import Decimal

import pytest

from trading_platform.backtesting.models.partial_fill_model import PartialFillModel


class TestConstruction:
    @pytest.mark.parametrize("rate", [0.0, -0.1, 1.1, 2.0])
    def test_rejects_rate_outside_zero_to_one(self, rate: float) -> None:
        with pytest.raises(ValueError, match="volume_participation_rate"):
            PartialFillModel(rate)

    def test_accepts_boundary_values(self) -> None:
        PartialFillModel(1.0)
        PartialFillModel(0.0001)


class TestFillableQuantity:
    def test_full_fill_when_order_fits_within_participation_cap(self) -> None:
        model = PartialFillModel(volume_participation_rate=0.10)

        fillable = model.fillable_quantity(bar_volume=Decimal("1000"), remaining_qty=Decimal("50"))

        assert fillable == Decimal("50")

    def test_partial_fill_capped_at_participation_rate_of_volume(self) -> None:
        model = PartialFillModel(volume_participation_rate=0.10)

        fillable = model.fillable_quantity(bar_volume=Decimal("1000"), remaining_qty=Decimal("500"))

        assert fillable == Decimal("100")  # 10% of 1000

    def test_full_participation_rate_allows_filling_the_entire_bar_volume(self) -> None:
        model = PartialFillModel(volume_participation_rate=1.0)

        fillable = model.fillable_quantity(
            bar_volume=Decimal("1000"), remaining_qty=Decimal("2000")
        )

        assert fillable == Decimal("1000")

    def test_zero_bar_volume_fills_nothing(self) -> None:
        model = PartialFillModel(volume_participation_rate=0.10)

        assert model.fillable_quantity(
            bar_volume=Decimal("0"), remaining_qty=Decimal("10")
        ) == Decimal("0")

    def test_zero_remaining_qty_fills_nothing(self) -> None:
        model = PartialFillModel(volume_participation_rate=0.10)

        assert model.fillable_quantity(
            bar_volume=Decimal("1000"), remaining_qty=Decimal("0")
        ) == Decimal("0")

    def test_never_exceeds_remaining_qty_even_with_huge_volume(self) -> None:
        model = PartialFillModel(volume_participation_rate=1.0)

        fillable = model.fillable_quantity(
            bar_volume=Decimal("1000000"), remaining_qty=Decimal("5")
        )

        assert fillable == Decimal("5")
