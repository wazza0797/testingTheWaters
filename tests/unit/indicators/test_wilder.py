from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.wilder import wilder_smoothing


class TestWilderSmoothing:
    def test_known_values_with_period_3(self) -> None:
        # seed = mean(1,2,3) = 2; then (2*2+4)/3 = 8/3; then (8/3*2+5)/3 = 31/9
        values = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])

        result = wilder_smoothing(values, period=3)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)
        assert result.iloc[3] == pytest.approx(8.0 / 3.0)
        assert result.iloc[4] == pytest.approx(31.0 / 9.0)

    def test_rejects_period_less_than_one(self) -> None:
        with pytest.raises(ValueError, match="period"):
            wilder_smoothing(pd.Series([1.0, 2.0]), period=0)

    def test_short_series_is_all_nan(self) -> None:
        result = wilder_smoothing(pd.Series([1.0, 2.0]), period=5)
        assert result.isna().all()

    def test_preserves_index(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0], index=[10, 20, 30])
        result = wilder_smoothing(values, period=2)
        assert list(result.index) == [10, 20, 30]

    def test_does_not_mutate_input(self) -> None:
        values = pd.Series([1.0, 2.0, 3.0, 4.0])
        original = values.copy()
        wilder_smoothing(values, period=2)
        pd.testing.assert_series_equal(values, original)
