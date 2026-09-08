from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.donchian import compute_donchian


class TestComputeDonchian:
    def test_known_values_with_period_3(self) -> None:
        high = pd.Series([10.0, 12.0, 9.0, 15.0, 11.0])
        low = pd.Series([8.0, 9.0, 7.0, 10.0, 9.0])
        close = high  # unused by donchian; any series is fine

        result = compute_donchian(high, low, close, period=3)

        assert math.isnan(result["upper"].iloc[0])
        assert math.isnan(result["upper"].iloc[1])
        # window [10,12,9] -> max 12; [8,9,7] -> min 7
        assert result["upper"].iloc[2] == pytest.approx(12.0)
        assert result["lower"].iloc[2] == pytest.approx(7.0)
        assert result["mid"].iloc[2] == pytest.approx(9.5)
        # window [12,9,15] -> max 15; [9,7,10] -> min 7
        assert result["upper"].iloc[3] == pytest.approx(15.0)
        assert result["lower"].iloc[3] == pytest.approx(7.0)
        # window [9,15,11] -> max 15; [7,10,9] -> min 7
        assert result["upper"].iloc[4] == pytest.approx(15.0)
        assert result["lower"].iloc[4] == pytest.approx(7.0)

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_donchian(s, s, s, period=0)

    def test_does_not_mutate_input(self) -> None:
        high = pd.Series([10.0, 12.0, 9.0])
        low = pd.Series([8.0, 9.0, 7.0])
        close = pd.Series([9.0, 11.0, 8.0])
        h, l_, c = high.copy(), low.copy(), close.copy()
        compute_donchian(high, low, close, period=2)
        pd.testing.assert_series_equal(high, h)
        pd.testing.assert_series_equal(low, l_)
        pd.testing.assert_series_equal(close, c)

    def test_deterministic(self) -> None:
        high = pd.Series([10.0, 12.0, 9.0, 15.0])
        low = pd.Series([8.0, 9.0, 7.0, 10.0])
        close = pd.Series([9.0, 11.0, 8.0, 12.0])
        a = compute_donchian(high, low, close, period=2)
        b = compute_donchian(high, low, close, period=2)
        pd.testing.assert_frame_equal(a, b)
