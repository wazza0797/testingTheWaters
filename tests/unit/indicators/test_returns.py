from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.returns import compute_returns


class TestComputeReturns:
    def test_simple_known_values(self) -> None:
        closes = pd.Series([100.0, 110.0, 121.0])
        result = compute_returns(closes, period=1, kind="simple")

        assert math.isnan(result.iloc[0])
        assert result.iloc[1] == pytest.approx(0.10)
        assert result.iloc[2] == pytest.approx(0.10)

    def test_log_known_values(self) -> None:
        closes = pd.Series([100.0, 110.0])
        result = compute_returns(closes, period=1, kind="log")

        assert result.iloc[1] == pytest.approx(math.log(110.0 / 100.0))

    def test_multi_bar_period(self) -> None:
        closes = pd.Series([100.0, 105.0, 110.0, 120.0])
        result = compute_returns(closes, period=2, kind="simple")

        assert math.isnan(result.iloc[1])
        assert result.iloc[2] == pytest.approx((110.0 - 100.0) / 100.0)
        assert result.iloc[3] == pytest.approx((120.0 - 105.0) / 105.0)

    def test_rejects_unknown_kind(self) -> None:
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="kind"):
            compute_returns(s, kind="weird")

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="period"):
            compute_returns(s, period=0)

    def test_guards_against_zero_reference_close(self) -> None:
        closes = pd.Series([0.0, 10.0])
        result = compute_returns(closes, period=1, kind="simple")
        assert math.isnan(result.iloc[1])
