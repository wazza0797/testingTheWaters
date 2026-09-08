from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.volume_breakout import compute_volume_breakout


class TestComputeVolumeBreakout:
    def test_flags_a_volume_spike(self) -> None:
        volume = pd.Series([10.0, 10.0, 10.0, 50.0])
        result = compute_volume_breakout(volume, period=3, k=2.0)

        assert result.iloc[2] == 0.0  # 10 vs 2*mean(10,10,10)=20 -> not a breakout
        assert result.iloc[3] == 1.0  # 50 vs 2*mean(10,10,10)=20 -> breakout

    def test_nan_during_warmup(self) -> None:
        volume = pd.Series([10.0, 10.0])
        result = compute_volume_breakout(volume, period=3)
        assert result.isna().all()

    def test_zero_volume_never_crashes(self) -> None:
        volume = pd.Series([0.0, 0.0, 0.0, 0.0])
        result = compute_volume_breakout(volume, period=3, k=2.0)
        assert result.iloc[2] == 0.0
        assert result.iloc[3] == 0.0

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="period"):
            compute_volume_breakout(s, period=0)

    def test_rejects_non_positive_k(self) -> None:
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="k"):
            compute_volume_breakout(s, k=0)

    def test_does_not_mutate_input(self) -> None:
        volume = pd.Series([10.0, 20.0, 30.0])
        original = volume.copy()
        compute_volume_breakout(volume, period=2)
        pd.testing.assert_series_equal(volume, original)
