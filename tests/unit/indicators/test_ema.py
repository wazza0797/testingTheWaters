from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.ema import compute_ema


class TestComputeEma:
    def test_known_values(self) -> None:
        # Hand-derived independently of the implementation:
        #   multiplier = 2 / (3 + 1) = 0.5
        #   seed (index 2) = mean(22, 23, 24) = 23
        #   ema[i] = (close[i] - ema[i-1]) * 0.5 + ema[i-1]
        closes = pd.Series([22.0, 23.0, 24.0, 23.0, 22.0, 21.0, 22.0, 23.0, 24.0, 25.0])
        expected = [
            math.nan,
            math.nan,
            23.0,
            23.0,
            22.5,
            21.75,
            21.875,
            22.4375,
            23.21875,
            24.109375,
        ]

        result = compute_ema(closes, period=3)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        for i in range(2, len(expected)):
            assert result.iloc[i] == pytest.approx(expected[i]), f"mismatch at index {i}"

    def test_seed_is_simple_average_of_first_period_closes(self) -> None:
        closes = pd.Series([10.0, 20.0, 30.0, 100.0])
        result = compute_ema(closes, period=3)
        assert result.iloc[2] == pytest.approx((10.0 + 20.0 + 30.0) / 3)

    def test_constant_series_stays_constant(self) -> None:
        closes = pd.Series([5.0] * 10)
        result = compute_ema(closes, period=4)
        assert result.iloc[3:].apply(lambda v: v == pytest.approx(5.0)).all()

    def test_insufficient_data_is_all_nan(self) -> None:
        closes = pd.Series([1.0, 2.0])
        result = compute_ema(closes, period=5)
        assert result.isna().all()
        assert len(result) == len(closes)

    def test_result_has_same_length_and_index_as_input(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0], index=[10, 20, 30, 40])
        result = compute_ema(closes, period=2)
        assert list(result.index) == [10, 20, 30, 40]
        assert len(result) == len(closes)

    def test_rejects_non_positive_period(self) -> None:
        with pytest.raises(ValueError):
            compute_ema(pd.Series([1.0, 2.0]), period=0)

    def test_deterministic(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
        first = compute_ema(closes, period=3)
        second = compute_ema(closes, period=3)
        pd.testing.assert_series_equal(first, second)

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([1.0, 2.0, 3.0, 4.0])
        original = closes.copy()
        compute_ema(closes, period=2)
        pd.testing.assert_series_equal(closes, original)

    def test_differs_from_pandas_ewm_adjust_true_default(self) -> None:
        """Guards against silently switching to `Series.ewm(...)`'s default
        (`adjust=True`), which is a different, non-recursive-textbook formula.
        """
        closes = pd.Series([22.0, 23.0, 24.0, 23.0, 22.0, 21.0, 22.0, 23.0, 24.0, 25.0])
        ours = compute_ema(closes, period=3)
        pandas_adjusted = closes.ewm(span=3, adjust=True).mean()
        assert ours.iloc[-1] != pytest.approx(pandas_adjusted.iloc[-1])
