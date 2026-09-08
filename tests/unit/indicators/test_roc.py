from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.roc import compute_roc


class TestComputeRoc:
    def test_known_values(self) -> None:
        closes = pd.Series([100.0, 105.0, 110.0, 90.0])
        result = compute_roc(closes, period=2)

        assert math.isnan(result.iloc[0])
        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx((110.0 - 100.0) / 100.0 * 100.0)
        assert result.iloc[3] == pytest.approx((90.0 - 105.0) / 105.0 * 100.0)

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_roc(s, period=0)

    def test_guards_against_zero_reference_close(self) -> None:
        closes = pd.Series([0.0, 10.0])
        result = compute_roc(closes, period=1)
        assert math.isnan(result.iloc[1])

    def test_does_not_mutate_input(self) -> None:
        closes = pd.Series([100.0, 105.0, 110.0])
        original = closes.copy()
        compute_roc(closes, period=1)
        pd.testing.assert_series_equal(closes, original)
