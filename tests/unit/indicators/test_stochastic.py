from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.stochastic import compute_stochastic


class TestComputeStochastic:
    def test_known_values_with_k_period_3(self) -> None:
        high = pd.Series([10.0, 12.0, 11.0, 14.0])
        low = pd.Series([8.0, 9.0, 8.0, 10.0])
        close = pd.Series([9.0, 11.0, 10.0, 13.0])

        result = compute_stochastic(high, low, close, k_period=3, d_period=2)

        # window [10,12,11] high -> 12; [8,9,8] low -> 8; close=10
        expected_k2 = 100.0 * (10.0 - 8.0) / (12.0 - 8.0)
        assert result["k"].iloc[2] == pytest.approx(expected_k2)

    def test_k_is_within_0_to_100(self) -> None:
        high = pd.Series([100.0 + i for i in range(30)])
        low = pd.Series([98.0 + i for i in range(30)])
        close = pd.Series([99.0 + i for i in range(30)])

        result = compute_stochastic(high, low, close, k_period=14, d_period=3)
        valid_k = result["k"].dropna()
        assert (valid_k >= 0).all()
        assert (valid_k <= 100).all()

    def test_guards_against_zero_range(self) -> None:
        high = pd.Series([10.0, 10.0, 10.0])
        low = pd.Series([10.0, 10.0, 10.0])
        close = pd.Series([10.0, 10.0, 10.0])

        result = compute_stochastic(high, low, close, k_period=2, d_period=1)
        assert math.isnan(result["k"].iloc[1])

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="k_period"):
            compute_stochastic(s, s, s, k_period=0)
        with pytest.raises(ValueError, match="d_period"):
            compute_stochastic(s, s, s, d_period=0)
