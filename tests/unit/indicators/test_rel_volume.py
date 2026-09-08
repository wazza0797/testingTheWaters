from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.rel_volume import compute_rel_volume


class TestComputeRelVolume:
    def test_known_values(self) -> None:
        volume = pd.Series([10.0, 10.0, 10.0, 30.0])
        result = compute_rel_volume(volume, period=3)

        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(1.0)  # 10 / mean(10,10,10)
        assert result.iloc[3] == pytest.approx(30.0 / (10.0 + 10.0 + 30.0) * 3.0)

    def test_guards_against_zero_average_volume(self) -> None:
        volume = pd.Series([0.0, 0.0, 0.0])
        result = compute_rel_volume(volume, period=3)
        assert math.isnan(result.iloc[2])

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="period"):
            compute_rel_volume(s, period=0)

    def test_does_not_mutate_input(self) -> None:
        volume = pd.Series([10.0, 20.0, 30.0])
        original = volume.copy()
        compute_rel_volume(volume, period=2)
        pd.testing.assert_series_equal(volume, original)
