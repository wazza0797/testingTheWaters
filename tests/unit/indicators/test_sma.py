from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.sma import compute_sma


class TestComputeSma:
    def test_known_values(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = compute_sma(closes, period=3)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx(2.0)  # mean(1, 2, 3)
        assert result.iloc[3] == pytest.approx(3.0)  # mean(2, 3, 4)
        assert result.iloc[4] == pytest.approx(4.0)  # mean(3, 4, 5)

    def test_period_of_one_returns_input_unchanged(self) -> None:
        closes = pd.Series([10.0, 20.0, 30.0])
        result = compute_sma(closes, period=1)
        pd.testing.assert_series_equal(result, closes.astype("float64"), check_names=False)

    def test_insufficient_data_is_all_nan(self) -> None:
        closes = pd.Series([1.0, 2.0])
        result = compute_sma(closes, period=5)
        assert result.isna().all()
        assert len(result) == len(closes)

    def test_result_has_same_length_and_index_as_input(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0], index=[10, 20, 30, 40])
        result = compute_sma(closes, period=2)
        assert list(result.index) == [10, 20, 30, 40]
        assert len(result) == len(closes)

    def test_rejects_non_positive_period(self) -> None:
        with pytest.raises(ValueError):
            compute_sma(pd.Series([1.0, 2.0]), period=0)

    def test_deterministic(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        first = compute_sma(closes, period=3)
        second = compute_sma(closes, period=3)
        pd.testing.assert_series_equal(first, second)

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0])
        original = closes.copy()
        compute_sma(closes, period=2)
        pd.testing.assert_series_equal(closes, original)
