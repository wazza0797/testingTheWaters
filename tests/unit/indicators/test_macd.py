from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.macd import compute_macd


class TestComputeMacd:
    def test_hist_equals_macd_minus_signal(self) -> None:
        closes = pd.Series([100.0 + i for i in range(60)])
        result = compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)
        valid = result.dropna()
        assert len(valid) > 0
        pd.testing.assert_series_equal(
            valid["hist"], (valid["macd"] - valid["signal"]), check_names=False
        )

    def test_macd_warms_up_before_signal(self) -> None:
        closes = pd.Series([100.0 + i for i in range(60)])
        result = compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)

        first_macd_idx = result["macd"].first_valid_index()
        first_signal_idx = result["signal"].first_valid_index()
        assert first_macd_idx is not None
        assert first_signal_idx is not None
        assert first_signal_idx > first_macd_idx

    def test_rising_trend_gives_positive_macd(self) -> None:
        closes = pd.Series([100.0 + i * 2 for i in range(60)])
        result = compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)
        assert result["macd"].iloc[-1] > 0

    def test_rejects_fast_not_less_than_slow(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="strictly less than"):
            compute_macd(closes, fast_period=10, slow_period=10)

    def test_rejects_period_less_than_one(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match=">= 1"):
            compute_macd(closes, fast_period=0)

    def test_short_series_is_all_nan(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0])
        result = compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)
        assert result["macd"].isna().all()
        assert result["signal"].isna().all()
        assert result["hist"].isna().all()

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([100.0 + i for i in range(30)])
        original = closes.copy()
        compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)
        pd.testing.assert_series_equal(closes, original)

    def test_deterministic(self) -> None:
        closes = pd.Series([100.0 + math.sin(i) * 5 for i in range(60)])
        a = compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)
        b = compute_macd(closes, fast_period=5, slow_period=10, signal_period=4)
        pd.testing.assert_frame_equal(a, b)
