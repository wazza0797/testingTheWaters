from __future__ import annotations

import math

import pandas as pd
import pytest

from trading_platform.indicators.vol_percentile import compute_vol_percentile


class TestComputeVolPercentile:
    def test_needs_full_lookback_after_vol_warmup(self) -> None:
        closes = pd.Series([100.0 + (i % 3) for i in range(50)])
        result = compute_vol_percentile(closes, vol_period=5, lookback=20)
        # realized_vol warms up at index 5; +20 more for a full lookback window.
        assert result.iloc[:24].isna().all()
        assert not math.isnan(result.iloc[24])

    def test_values_are_within_0_to_100(self) -> None:
        closes = pd.Series([100.0 + (i % 5) * ((-1) ** i) for i in range(200)])
        result = compute_vol_percentile(closes, vol_period=10, lookback=50)
        valid = result.dropna()
        assert len(valid) > 0
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_a_new_volatility_spike_ranks_high(self) -> None:
        # Calm for a long stretch, then a sudden burst of large moves.
        calm = [100.0 + (i % 2) * 0.01 for i in range(150)]
        spike = [100.0 + (i % 2) * 20 for i in range(20)]
        closes = pd.Series(calm + spike)

        result = compute_vol_percentile(closes, vol_period=10, lookback=100)

        # Not exactly 100: the alternating spike pattern ties several bars
        # at the same (highest) realized_vol reading, so "strictly below
        # current" undercounts slightly among ties — still unambiguously
        # near the top of the window's distribution.
        assert result.iloc[-1] >= 90.0

    def test_rejects_lookback_less_than_two(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="lookback"):
            compute_vol_percentile(s, lookback=1)

    def test_rejects_vol_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0, 3.0])
        with pytest.raises(ValueError, match="vol_period"):
            compute_vol_percentile(s, vol_period=0)
