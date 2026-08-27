from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.registry import build_default_registry


class TestComputeAtr:
    def test_known_values_with_period_2(self) -> None:
        # Hand-derived: closes/highs/lows for period=2
        # bars: (h,l,c) = (12,10,11), (13,11,12), (14,10,11), (15,12,14)
        # TR[1] = max(13-11, |13-11|, |11-11|) = 2
        # TR[2] = max(14-10, |14-12|, |10-12|) = 4
        # TR[3] = max(15-12, |15-11|, |12-11|) = 4
        # ATR[2] = mean(TR[1], TR[2]) = 3.0
        # ATR[3] = (3.0*1 + 4)/2 = 3.5
        high = pd.Series([12.0, 13.0, 14.0, 15.0])
        low = pd.Series([10.0, 11.0, 10.0, 12.0])
        close = pd.Series([11.0, 12.0, 11.0, 14.0])

        result = compute_atr(high, low, close, period=2)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(3.0)
        assert result.iloc[3] == pytest.approx(3.5)

    def test_rejects_period_less_than_one(self) -> None:
        series = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_atr(series, series, series, period=0)

    def test_rejects_length_mismatch(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            compute_atr(pd.Series([1.0, 2.0]), pd.Series([1.0]), pd.Series([1.0, 2.0]))

    def test_short_series_is_all_nan(self) -> None:
        high = pd.Series([12.0, 13.0])
        low = pd.Series([10.0, 11.0])
        close = pd.Series([11.0, 12.0])

        result = compute_atr(high, low, close, period=14)

        assert result.isna().all()


class TestAtrRegistry:
    def test_atr_is_registered(self) -> None:
        registry = build_default_registry()
        assert "atr" in registry.available()

    def test_compute_requires_high_and_low(self) -> None:
        registry = build_default_registry()
        closes = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="high="):
            registry.compute("atr", closes, period=2)

    def test_compute_with_ohlc_kwargs(self) -> None:
        registry = build_default_registry()
        high = pd.Series([12.0, 13.0, 14.0, 15.0])
        low = pd.Series([10.0, 11.0, 10.0, 12.0])
        close = pd.Series([11.0, 12.0, 11.0, 14.0])

        result = registry.compute("atr", close, high=high, low=low, period=2)

        assert result.iloc[2] == pytest.approx(3.0)
