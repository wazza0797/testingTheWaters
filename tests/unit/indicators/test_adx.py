from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.adx import compute_adx


def _trending_bars(n: int = 60) -> tuple[pd.Series, pd.Series, pd.Series]:
    # Steady, strong uptrend: every bar makes a higher high and higher low.
    high = pd.Series([100.0 + i * 2 for i in range(n)])
    low = pd.Series([98.0 + i * 2 for i in range(n)])
    close = pd.Series([99.0 + i * 2 for i in range(n)])
    return high, low, close


def _choppy_bars(n: int = 60) -> tuple[pd.Series, pd.Series, pd.Series]:
    # Oscillates around a flat center — no sustained directional movement.
    high = pd.Series([101.0 if i % 2 == 0 else 100.0 for i in range(n)])
    low = pd.Series([99.0 if i % 2 == 0 else 98.0 for i in range(n)])
    close = pd.Series([100.0 if i % 2 == 0 else 99.0 for i in range(n)])
    return high, low, close


class TestComputeAdx:
    def test_strong_uptrend_has_higher_plus_di_than_minus_di(self) -> None:
        high, low, close = _trending_bars()
        result = compute_adx(high, low, close, period=14)
        last = result.iloc[-1]
        assert last["plus_di"] > last["minus_di"]
        assert last["adx"] > 20  # clearly trending, not a tight numeric claim

    def test_strong_uptrend_adx_is_bounded(self) -> None:
        high, low, close = _trending_bars()
        result = compute_adx(high, low, close, period=14)
        valid = result["adx"].dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_choppy_market_has_lower_adx_than_strong_trend(self) -> None:
        trend_high, trend_low, trend_close = _trending_bars()
        chop_high, chop_low, chop_close = _choppy_bars()

        trend_adx = compute_adx(trend_high, trend_low, trend_close, period=14)["adx"].iloc[-1]
        chop_adx = compute_adx(chop_high, chop_low, chop_close, period=14)["adx"].iloc[-1]

        assert chop_adx < trend_adx

    def test_di_values_are_non_negative(self) -> None:
        high, low, close = _trending_bars()
        result = compute_adx(high, low, close, period=14)
        valid = result.dropna()
        assert (valid["plus_di"] >= 0).all()
        assert (valid["minus_di"] >= 0).all()

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="period"):
            compute_adx(s, s, s, period=0)

    def test_rejects_length_mismatch(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            compute_adx(pd.Series([1.0, 2.0]), pd.Series([1.0]), pd.Series([1.0, 2.0]))

    def test_short_series_is_all_nan(self) -> None:
        high, low, close = _trending_bars(n=10)
        result = compute_adx(high, low, close, period=14)
        assert result["adx"].isna().all()

    def test_does_not_mutate_input(self) -> None:
        high, low, close = _trending_bars()
        high_orig, low_orig, close_orig = high.copy(), low.copy(), close.copy()
        compute_adx(high, low, close, period=14)
        pd.testing.assert_series_equal(high, high_orig)
        pd.testing.assert_series_equal(low, low_orig)
        pd.testing.assert_series_equal(close, close_orig)

    def test_deterministic(self) -> None:
        high, low, close = _trending_bars()
        a = compute_adx(high, low, close, period=14)
        b = compute_adx(high, low, close, period=14)
        pd.testing.assert_frame_equal(a, b)
