from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.rsi import compute_rsi

# 15 closes forming exactly 14 changes; published worked example (EUR/USD,
# 14-period Wilder RSI) verified independently against the source's own
# arithmetic: sum(gains)=0.0185, sum(losses)=0.0070, avg_gain=0.00132142857,
# avg_loss=0.0005, RS=2.642857, RSI=100-100/3.642857=72.549019607843.
_EURUSD_CLOSES = [
    1.0800,
    1.0825,
    1.0810,
    1.0840,
    1.0835,
    1.0860,
    1.0880,
    1.0865,
    1.0890,
    1.0870,
    1.0895,
    1.0910,
    1.0900,
    1.0920,
    1.0915,
]
_EXPECTED_FIRST_RSI = 72.549019607843137


class TestComputeRsi:
    def test_known_value_matches_published_worked_example(self) -> None:
        closes = pd.Series(_EURUSD_CLOSES)
        result = compute_rsi(closes, period=14)

        assert result.iloc[:14].isna().all()
        assert result.iloc[14] == pytest.approx(_EXPECTED_FIRST_RSI, abs=1e-9)

    def test_known_values_with_non_default_period(self) -> None:
        # Hand-derived independently of the implementation (period=2, so the
        # published period=14 example alone can't catch a period-handling bug):
        #   changes = [+1, -1, +2, -1, +2]
        #   avg_gain[2] = mean(1, 0) = 0.5; avg_loss[2] = mean(0, 1) = 0.5 -> RSI=50
        #   avg_gain[3] = (0.5*1 + 2)/2 = 1.25; avg_loss[3] = (0.5*1 + 0)/2 = 0.25 -> RSI=83.333...
        #   avg_gain[4] = (1.25*1 + 0)/2 = 0.625; avg_loss[4] = (0.25*1 + 1)/2 = 0.625 -> RSI=50
        #   avg_gain[5] = (0.625*1 + 2)/2 = 1.3125; avg_loss[5] = (0.625*1 + 0)/2 = 0.3125 -> RSI=80.769...
        closes = pd.Series([1.0, 2.0, 1.0, 3.0, 2.0, 4.0])
        expected = [math.nan, math.nan, 50.0, 250 / 3, 50.0, 80.76923076923077]

        result = compute_rsi(closes, period=2)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        for i in range(2, len(expected)):
            assert result.iloc[i] == pytest.approx(expected[i]), f"mismatch at index {i}"

    def test_all_gains_is_rsi_100(self) -> None:
        closes = pd.Series([float(i) for i in range(1, 20)])  # strictly increasing
        result = compute_rsi(closes, period=14)
        assert result.iloc[14] == pytest.approx(100.0)

    def test_all_losses_is_rsi_0(self) -> None:
        closes = pd.Series([float(i) for i in range(20, 1, -1)])  # strictly decreasing
        result = compute_rsi(closes, period=14)
        assert result.iloc[14] == pytest.approx(0.0)

    def test_no_movement_is_neutral_50(self) -> None:
        closes = pd.Series([10.0] * 20)
        result = compute_rsi(closes, period=14)
        assert result.iloc[14] == pytest.approx(50.0)

    def test_bounded_between_zero_and_hundred(self) -> None:
        closes = pd.Series(
            [1.0, 3.0, 1.5, 5.0, 2.0, 6.0, 1.0, 7.0, 2.0, 8.0, 1.0, 9.0, 2.0, 10.0, 1.0, 11.0]
        )
        result = compute_rsi(closes, period=14)
        valid = result.dropna()
        assert not valid.empty
        assert (valid >= 0.0).all()
        assert (valid <= 100.0).all()

    def test_insufficient_data_is_all_nan(self) -> None:
        closes = pd.Series([float(i) for i in range(10)])  # only 9 changes, need 14
        result = compute_rsi(closes, period=14)
        assert result.isna().all()
        assert len(result) == len(closes)

    def test_result_has_same_length_and_index_as_input(self) -> None:
        closes = pd.Series(_EURUSD_CLOSES, index=range(100, 115))
        result = compute_rsi(closes, period=14)
        assert list(result.index) == list(range(100, 115))
        assert len(result) == len(closes)

    def test_rejects_non_positive_period(self) -> None:
        with pytest.raises(ValueError):
            compute_rsi(pd.Series([1.0, 2.0]), period=0)

    def test_deterministic(self) -> None:
        closes = pd.Series(_EURUSD_CLOSES)
        first = compute_rsi(closes, period=14)
        second = compute_rsi(closes, period=14)
        pd.testing.assert_series_equal(first, second)

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series(_EURUSD_CLOSES)
        original = closes.copy()
        compute_rsi(closes, period=14)
        pd.testing.assert_series_equal(closes, original)

    def test_default_period_is_14(self) -> None:
        closes = pd.Series(_EURUSD_CLOSES)
        default_result = compute_rsi(closes)
        explicit_result = compute_rsi(closes, period=14)
        pd.testing.assert_series_equal(default_result, explicit_result)

    def test_nan_only_before_first_valid_value(self) -> None:
        closes = pd.Series(_EURUSD_CLOSES)
        result = compute_rsi(closes, period=14)
        assert math.isnan(result.iloc[13])
        assert not math.isnan(result.iloc[14])
