from __future__ import annotations

import pandas as pd

from trading_platform.indicators.atr import compute_atr
from trading_platform.indicators.atr_pct import compute_atr_pct


class TestComputeAtrPct:
    def test_equals_atr_divided_by_close(self) -> None:
        high = pd.Series([12.0, 13.0, 14.0, 15.0, 16.0])
        low = pd.Series([10.0, 11.0, 10.0, 12.0, 13.0])
        close = pd.Series([11.0, 12.0, 11.0, 14.0, 15.0])

        atr = compute_atr(high, low, close, period=2)
        atr_pct = compute_atr_pct(high, low, close, period=2)

        expected = atr / close
        pd.testing.assert_series_equal(atr_pct, expected, check_names=False)

    def test_scale_invariant_across_price_levels(self) -> None:
        # Same relative volatility, 100x the price — atr_pct should match,
        # unlike raw ATR (the whole point of a cross-asset-comparable metric).
        high = pd.Series([12.0, 13.0, 14.0, 15.0, 16.0])
        low = pd.Series([10.0, 11.0, 10.0, 12.0, 13.0])
        close = pd.Series([11.0, 12.0, 11.0, 14.0, 15.0])

        scaled_high, scaled_low, scaled_close = high * 100, low * 100, close * 100

        atr_pct = compute_atr_pct(high, low, close, period=2)
        scaled_atr_pct = compute_atr_pct(scaled_high, scaled_low, scaled_close, period=2)

        pd.testing.assert_series_equal(atr_pct, scaled_atr_pct, check_names=False)

    def test_short_series_is_all_nan(self) -> None:
        s = pd.Series([1.0, 2.0])
        result = compute_atr_pct(s, s, s, period=14)
        assert result.isna().all()
