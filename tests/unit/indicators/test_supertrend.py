from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.supertrend import compute_supertrend


def _uptrend_bars(n: int = 40) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = pd.Series([100.0 + i * 2 for i in range(n)])
    low = pd.Series([98.0 + i * 2 for i in range(n)])
    close = pd.Series([99.0 + i * 2 for i in range(n)])
    return high, low, close


def _downtrend_bars(n: int = 40) -> tuple[pd.Series, pd.Series, pd.Series]:
    high = pd.Series([300.0 - i * 2 for i in range(n)])
    low = pd.Series([298.0 - i * 2 for i in range(n)])
    close = pd.Series([299.0 - i * 2 for i in range(n)])
    return high, low, close


class TestComputeSupertrend:
    def test_uptrend_ends_in_direction_up(self) -> None:
        high, low, close = _uptrend_bars()
        result = compute_supertrend(high, low, close, period=10, multiplier=3.0)
        assert result["direction"].iloc[-1] == 1.0

    def test_downtrend_ends_in_direction_down(self) -> None:
        high, low, close = _downtrend_bars()
        result = compute_supertrend(high, low, close, period=10, multiplier=3.0)
        assert result["direction"].iloc[-1] == -1.0

    def test_uptrend_value_is_below_close(self) -> None:
        high, low, close = _uptrend_bars()
        result = compute_supertrend(high, low, close, period=10, multiplier=3.0)
        last = result.iloc[-1]
        assert last["value"] < close.iloc[-1]

    def test_direction_is_nan_during_warmup(self) -> None:
        high, low, close = _uptrend_bars()
        result = compute_supertrend(high, low, close, period=10, multiplier=3.0)
        assert result["direction"].iloc[:9].isna().all()

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_supertrend(s, s, s, period=0)

    def test_rejects_non_positive_multiplier(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="multiplier"):
            compute_supertrend(s, s, s, multiplier=0)

    def test_does_not_mutate_input(self) -> None:
        high, low, close = _uptrend_bars()
        h, l_, c = high.copy(), low.copy(), close.copy()
        compute_supertrend(high, low, close, period=10)
        pd.testing.assert_series_equal(high, h)
        pd.testing.assert_series_equal(low, l_)
        pd.testing.assert_series_equal(close, c)

    def test_deterministic(self) -> None:
        high, low, close = _uptrend_bars()
        a = compute_supertrend(high, low, close, period=10)
        b = compute_supertrend(high, low, close, period=10)
        pd.testing.assert_frame_equal(a, b)
