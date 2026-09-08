from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.bollinger import compute_bollinger


class TestComputeBollinger:
    def test_known_values_with_period_3(self) -> None:
        closes = pd.Series([10.0, 10.0, 10.0, 20.0, 10.0])
        result = compute_bollinger(closes, period=3, num_std=2.0)

        # window [10,10,10]: mean=10, std(ddof=0)=0
        assert result["mid"].iloc[2] == pytest.approx(10.0)
        assert result["upper"].iloc[2] == pytest.approx(10.0)
        assert result["lower"].iloc[2] == pytest.approx(10.0)
        assert result["width"].iloc[2] == pytest.approx(0.0)

        # window [10,10,20]: mean=40/3, population std = sqrt(mean((x-mean)^2))
        mean = 40.0 / 3.0
        variance = ((10 - mean) ** 2 + (10 - mean) ** 2 + (20 - mean) ** 2) / 3.0
        std = math.sqrt(variance)
        assert result["mid"].iloc[3] == pytest.approx(mean)
        assert result["upper"].iloc[3] == pytest.approx(mean + 2 * std)
        assert result["lower"].iloc[3] == pytest.approx(mean - 2 * std)

    def test_upper_always_at_or_above_lower(self) -> None:
        closes = pd.Series([100.0 + i * (-1) ** i for i in range(30)])
        result = compute_bollinger(closes, period=5)
        valid = result.dropna()
        assert (valid["upper"] >= valid["lower"]).all()

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_bollinger(s, period=0)

    def test_rejects_non_positive_num_std(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="num_std"):
            compute_bollinger(s, num_std=0)

    def test_short_series_is_all_nan(self) -> None:
        s = pd.Series([1.0, 2.0])
        result = compute_bollinger(s, period=5)
        assert result["mid"].isna().all()

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([10.0, 12.0, 9.0, 15.0])
        original = closes.copy()
        compute_bollinger(closes, period=2)
        pd.testing.assert_series_equal(closes, original)
