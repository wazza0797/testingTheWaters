from __future__ import annotations

import pandas as pd
import pytest

from trading_platform.indicators.volume_roc import compute_volume_roc


class TestComputeVolumeRoc:
    def test_matches_roc_applied_to_volume(self) -> None:
        volume = pd.Series([10.0, 12.0, 15.0, 9.0])
        result = compute_volume_roc(volume, period=2)

        assert result.iloc[2] == pytest.approx((15.0 - 10.0) / 10.0 * 100.0)
        assert result.iloc[3] == pytest.approx((9.0 - 12.0) / 12.0 * 100.0)

    def test_rejects_period_less_than_one(self) -> None:
        s = pd.Series([1.0, 2.0])
        with pytest.raises(ValueError, match="period"):
            compute_volume_roc(s, period=0)
