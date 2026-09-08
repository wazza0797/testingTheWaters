from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.ma_slope import compute_ma_slope


class TestComputeMaSlope:
    def test_rising_series_has_positive_slope(self) -> None:
        closes = pd.Series([100.0 + i for i in range(40)])
        result = compute_ma_slope(closes, period=10, slope_bars=5, ma_type="sma")
        assert result.iloc[-1] > 0

    def test_falling_series_has_negative_slope(self) -> None:
        closes = pd.Series([200.0 - i for i in range(40)])
        result = compute_ma_slope(closes, period=10, slope_bars=5, ma_type="sma")
        assert result.iloc[-1] < 0

    def test_flat_series_has_zero_slope(self) -> None:
        closes = pd.Series([100.0 for _ in range(40)])
        result = compute_ma_slope(closes, period=10, slope_bars=5, ma_type="sma")
        assert result.iloc[-1] == pytest.approx(0.0)

    def test_supports_ema(self) -> None:
        closes = pd.Series([100.0 + i for i in range(40)])
        result = compute_ma_slope(closes, period=10, slope_bars=5, ma_type="ema")
        assert result.iloc[-1] > 0

    def test_rejects_unknown_ma_type(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="ma_type"):
            compute_ma_slope(closes, ma_type="wma")

    def test_rejects_period_less_than_one(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_ma_slope(closes, period=0)

    def test_rejects_slope_bars_less_than_one(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="slope_bars"):
            compute_ma_slope(closes, slope_bars=0)

    def test_short_series_is_all_nan(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        result = compute_ma_slope(closes, period=10, slope_bars=5)
        assert result.isna().all()

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([100.0 + i for i in range(40)])
        original = closes.copy()
        compute_ma_slope(closes, period=10, slope_bars=5)
        pd.testing.assert_series_equal(closes, original)
